"""Runs a provider in its own interpreter, in its own environment.

This is the boundary that makes GPU work survivable (``docs/architecture.md``
section 2). Everything below it — the model, its Torch build, its GPU context —
lives in a subprocess that can die without taking the engine with it.

It also makes cancellation real. A running Metal or CUDA kernel cannot be
interrupted from Python; terminating the process can.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from .types import (
    GenerationRequest,
    GenerationResult,
    ProgressCallback,
)
from .worker_protocol import ErrorEvent, ProgressEvent, ResultEvent, WorkerRequest, parse_event

#: Time to wait for a terminated worker to exit before killing it.
_TERMINATE_GRACE_S = 5.0


class ProviderExecutionError(RuntimeError):
    """A generation failed. Carries a user-facing message and technical detail."""

    def __init__(self, message: str, technical: str = "", suggestions: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.technical = technical
        self.suggestions = suggestions or []


class WorkerProcess:
    """One worker run."""

    def __init__(
        self,
        python_executable: Path,
        worker_script: Path,
        request: WorkerRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        self._python = python_executable
        self._script = worker_script
        self._request = request
        self._timeout = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._cancelled = False

    def cancel(self) -> None:
        """Terminate the worker.

        Safe to call before the process starts and after it has finished; the
        flag is what makes an early cancel take effect rather than being lost.
        """
        with self._lock:
            self._cancelled = True
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    def run(self, on_progress: ProgressCallback) -> ResultEvent:
        with self._lock:
            if self._cancelled:
                raise ProviderExecutionError("Cancelled before the model started.")
            self._process = subprocess.Popen(  # noqa: S603 - fixed argv, never a shell
                [str(self._python), str(self._script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            process = self._process

        assert process.stdin is not None  # noqa: S101 - guaranteed by PIPE above
        process.stdin.write(self._request.model_dump_json())
        process.stdin.close()

        result: ResultEvent | None = None
        error: ErrorEvent | None = None
        # Kept so a crash without a protocol line can still be explained.
        noise: list[str] = []
        started = time.monotonic()

        assert process.stdout is not None  # noqa: S101
        for line in process.stdout:
            if self._timeout is not None and time.monotonic() - started > self._timeout:
                self.cancel()
                raise ProviderExecutionError(
                    "Generation took longer than the configured limit and was stopped.",
                    technical=f"Exceeded {self._timeout}s.",
                )
            event = parse_event(line)
            if event is None:
                # Model libraries print to stdout uninvited. Keep it for
                # diagnostics; never let it end the job.
                noise.append(line.rstrip())
                continue
            if isinstance(event, ProgressEvent):
                on_progress(event.stage, event.fraction, event.message)
            elif isinstance(event, ResultEvent):
                result = event
            elif isinstance(event, ErrorEvent):
                error = event

        stderr = (process.stderr.read() if process.stderr else "") or ""
        returncode = process.wait()

        with self._lock:
            cancelled = self._cancelled

        if cancelled:
            raise ProviderExecutionError("Generation was cancelled.")

        if error is not None:
            raise ProviderExecutionError(error.message, error.technical, error.suggestions)

        if result is None:
            # No result and no protocol error means the worker died — usually an
            # out-of-memory kill by the OS, which produces no Python traceback.
            detail = "\n".join([*noise[-20:], stderr[-4000:]]).strip()
            raise ProviderExecutionError(
                "The generation process stopped without producing a model.",
                technical=f"Worker exited with code {returncode}.\n{detail}",
                suggestions=[
                    "Close other applications and try again — this often means the "
                    "system ran out of memory.",
                    "Try a lower resolution.",
                ],
            )

        if returncode != 0:
            raise ProviderExecutionError(
                "The generation process reported a failure after producing output.",
                technical=f"Worker exited with code {returncode}.\n{stderr[-2000:]}",
            )
        return result


def run_worker(  # noqa: PLR0913 - all keyword-only; a parameter object would
    # only move the same fields somewhere less readable
    *,
    python_executable: Path,
    worker_script: Path,
    request: GenerationRequest,
    source_dir: Path,
    weights_dir: Path,
    device: str,
    on_progress: ProgressCallback,
    timeout_seconds: float | None = None,
) -> tuple[GenerationResult, WorkerProcess]:
    """Run one generation. Returns the result and the handle used to cancel it."""
    worker_request = WorkerRequest(
        images=[str(path) for path in request.images],
        output_dir=str(request.output_dir),
        source_dir=str(source_dir),
        weights_dir=str(weights_dir),
        device=device,
        seed=request.seed,
        parameters=dict(request.parameters),
    )
    worker = WorkerProcess(
        python_executable, worker_script, worker_request, timeout_seconds=timeout_seconds
    )
    event = worker.run(on_progress)

    mesh_path = Path(event.mesh_path)
    if not mesh_path.exists():
        raise ProviderExecutionError(
            "The model reported success but produced no file.",
            technical=f"Expected an asset at {mesh_path}.",
        )

    return (
        GenerationResult(
            mesh_path=mesh_path,
            artifacts={name: Path(value) for name, value in event.artifacts.items()},
            seed=event.seed,
            runtime=event.device,
            duration_seconds=event.duration_seconds,
            parameters={
                "vertices": event.vertices,
                "faces": event.faces,
                **json.loads(worker_request.model_dump_json())["parameters"],
            },
        ),
        worker,
    )
