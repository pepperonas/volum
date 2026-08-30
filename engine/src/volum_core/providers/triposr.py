"""The TripoSR provider.

Thin by design. All it does is describe itself, check its environment, and hand
a request to a worker running in TripoSR's own virtual environment. There is no
model code here — VOLUM integrates models, it does not fork them.
"""

from __future__ import annotations

from pathlib import Path

from ..models.manager import InstallState, ModelManager
from ..models.registry import TRIPOSR
from .types import (
    EnvironmentReport,
    GenerationRequest,
    GenerationResult,
    ImageTo3DProvider,
    ProgressCallback,
    ProviderMetadata,
    ResourceEstimate,
)
from .worker_provider import ProviderExecutionError, WorkerProcess, run_worker

WORKER_SCRIPT = Path(__file__).resolve().parent / "workers" / "triposr_worker.py"

#: Generous: a CPU run on a slow machine is legitimately slow. The limit exists
#: so a wedged worker cannot hold a job open for ever, not to police speed.
_TIMEOUT_S = 60 * 30


class TripoSRProvider(ImageTo3DProvider):
    def __init__(self, manager: ModelManager, *, device: str = "cpu") -> None:
        self._manager = manager
        self._device = device
        self._worker: WorkerProcess | None = None

    @property
    def metadata(self) -> ProviderMetadata:
        return TRIPOSR

    def validate_environment(self) -> EnvironmentReport:
        model_id = TRIPOSR.id
        state = self._manager.state(model_id)
        if state is not InstallState.INSTALLED:
            return EnvironmentReport(
                ok=False,
                problems=[f"TripoSR is not installed (state: {state.value})."],
                detail={"fix": "volum models install triposr"},
            )
        problems = self._manager.verify(model_id)
        if not WORKER_SCRIPT.exists():
            problems.append(f"The worker script is missing at {WORKER_SCRIPT}.")
        return EnvironmentReport(
            ok=not problems,
            problems=problems,
            detail={
                "python": str(self._manager.python_executable(model_id)),
                "source": str(self._manager.source_dir(model_id)),
                "weights": str(self._manager.weights_dir(model_id)),
                "device": self._device,
            },
        )

    def estimate_resources(self, request: GenerationRequest) -> ResourceEstimate:
        requirements = TRIPOSR.requirements
        return ResourceEstimate(
            peak_memory_bytes=requirements.estimated_peak_memory_bytes,
            disk_bytes=requirements.disk_bytes,
            # Left unset on purpose: VOLUM has not measured this model on this
            # machine, and an upstream figure from other hardware would be a
            # guess dressed as a number.
            estimated_seconds=None,
        )

    def generate(
        self, request: GenerationRequest, on_progress: ProgressCallback
    ) -> GenerationResult:
        environment = self.validate_environment()
        if not environment.ok:
            raise ProviderExecutionError(
                "TripoSR is not ready to run.",
                technical="; ".join(environment.problems),
                suggestions=["Install or repair it with: volum models install triposr"],
            )
        if not request.images:
            raise ProviderExecutionError("No input image was provided.")

        model_id = TRIPOSR.id
        result, worker = run_worker(
            python_executable=self._manager.python_executable(model_id),
            worker_script=WORKER_SCRIPT,
            request=request,
            source_dir=self._manager.source_dir(model_id),
            weights_dir=self._manager.weights_dir(model_id),
            hf_cache_dir=None,
            device=self._device,
            on_progress=on_progress,
            timeout_seconds=_TIMEOUT_S,
        )
        self._worker = worker
        return result

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def cleanup(self) -> None:
        """Nothing to release here.

        The model lived in a subprocess that has already exited, which is the
        whole point of the boundary: memory returns to the operating system
        without VOLUM having to ask a framework nicely.
        """
        self._worker = None
