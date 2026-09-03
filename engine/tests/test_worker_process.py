"""The worker subprocess boundary, exercised with a real child process.

No model here — the child is a small Python script speaking the worker
protocol. What is under test is the part that makes CANCELLED true rather than
cosmetic: the worker must actually die, even when it ignores SIGTERM.
"""

from __future__ import annotations

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
