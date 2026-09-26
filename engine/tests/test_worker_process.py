"""The worker subprocess boundary, exercised with a real child process.

No model here — the child is a small Python script speaking the worker
protocol. What is under test is the part that makes CANCELLED true rather than
cosmetic: the worker must actually die, even when it ignores SIGTERM.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from volum_core.providers import worker_provider
from volum_core.providers.worker_protocol import WorkerRequest
from volum_core.providers.worker_provider import ProviderExecutionError, WorkerProcess


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "worker.py"
    path.write_text(body, encoding="utf-8")
    return path


def _request(tmp_path: Path) -> WorkerRequest:
    return WorkerRequest(
        images=["in.png"],
        output_dir=str(tmp_path),
        source_dir=str(tmp_path),
        weights_dir=str(tmp_path),
        hf_cache_dir=None,
        device="cpu",
        seed=None,
        parameters={},
    )


STUBBORN = """
import signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)   # a worker wedged in a kernel looks like this
sys.stdin.read()
print('{"type": "progress", "stage": "reconstructing", "fraction": null, '
      '"message": "busy"}', flush=True)
time.sleep(60)
"""


def test_a_worker_that_ignores_sigterm_is_killed_after_the_grace_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_provider, "_TERMINATE_GRACE_S", 0.5)
    worker = WorkerProcess(Path(sys.executable), _script(tmp_path, STUBBORN), _request(tmp_path))
    seen: list[str] = []

    def on_progress(stage: str, fraction: float | None, message: str) -> None:
        seen.append(message)
        worker.cancel()  # cancel as soon as the child is provably running

    started = time.monotonic()
    with pytest.raises(ProviderExecutionError, match="cancelled"):
        worker.run(on_progress)
    elapsed = time.monotonic() - started

    assert seen == ["busy"]
    assert elapsed < 10, f"the child outlived cancellation by {elapsed:.1f}s"
    assert worker._process is not None and worker._process.poll() is not None


def test_cancel_before_start_never_launches_the_child(tmp_path: Path) -> None:
    worker = WorkerProcess(Path(sys.executable), _script(tmp_path, STUBBORN), _request(tmp_path))
    worker.cancel()
    with pytest.raises(ProviderExecutionError, match="before the model started"):
        worker.run(lambda *_: None)
    assert worker._process is None


ECHO_ENV = """
import json, os, sys
sys.stdin.read()
print(json.dumps({"type": "error", "message": "reporting the environment",
                  "technical": json.dumps({k: v for k, v in os.environ.items()
                                           if k.startswith("PYTHON")}),
                  "suggestions": []}), flush=True)
"""


def test_a_worker_may_write_bytecode_even_when_the_engine_may_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bundled engine runs with PYTHONDONTWRITEBYTECODE because its own
    bundle is code-signed and sealed. A provider's environment lives in the
    data directory, is not sealed, and pays for every missing `.pyc` on every
    job — torch alone is thousands of modules. The setting must not be
    inherited into the worker.
    """
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    worker = WorkerProcess(Path(sys.executable), _script(tmp_path, ECHO_ENV), _request(tmp_path))

    with pytest.raises(ProviderExecutionError) as excinfo:
        worker.run(lambda *_: None)

    reported = json.loads(excinfo.value.technical)
    assert "PYTHONDONTWRITEBYTECODE" not in reported


def test_the_rest_of_the_environment_reaches_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only that one variable is dropped; a worker still needs everything else.
    monkeypatch.setenv("PYTHONHASHSEED", "12345")
    worker = WorkerProcess(Path(sys.executable), _script(tmp_path, ECHO_ENV), _request(tmp_path))

    with pytest.raises(ProviderExecutionError) as excinfo:
        worker.run(lambda *_: None)

    assert json.loads(excinfo.value.technical)["PYTHONHASHSEED"] == "12345"
