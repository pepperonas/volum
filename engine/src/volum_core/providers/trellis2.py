"""The TRELLIS.2 provider.

Same shape as the TripoSR one — describe, check, hand a request to a worker in
the model's own environment — which is the point: adding the second provider
should not require changes elsewhere (spec section 68). The differences that do
exist are declared, not coded around:

- the install carries the geometry models only, so ``pipeline.json`` is VOLUM's
  own and names what was actually downloaded;
- background removal points at BiRefNet, because the upstream config names a
  CC BY-NC model;
- backend selection happens through environment variables that must be set
  before torch is imported, so they travel with the install rather than the run.
"""

from __future__ import annotations

from pathlib import Path

from ..models.install_spec import get_install_spec
from ..models.manager import InstallState, ModelManager
from ..models.registry import TRELLIS2
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

WORKER_SCRIPT = Path(__file__).resolve().parent / "workers" / "trellis2_worker.py"

#: Generous. Loading alone takes minutes on Apple Silicon, and a 16 GB machine
#: pages to disk. The limit exists so a wedged worker cannot hold a job open for
#: ever, not to police speed.
_TIMEOUT_S = 60 * 60


class Trellis2Provider(ImageTo3DProvider):
    def __init__(self, manager: ModelManager, *, device: str = "mps") -> None:
        self._manager = manager
        self._device = device
        self._worker: WorkerProcess | None = None

    @property
    def metadata(self) -> ProviderMetadata:
        return TRELLIS2

    def validate_environment(self) -> EnvironmentReport:
        model_id = TRELLIS2.id
        state = self._manager.state(model_id)
        if state is not InstallState.INSTALLED:
            return EnvironmentReport(
                ok=False,
                problems=[f"TRELLIS.2 is not installed (state: {state.value})."],
                detail={"fix": "volum models install trellis2"},
            )

        problems = self._manager.verify(model_id)
        if not WORKER_SCRIPT.exists():
            problems.append(f"The worker script is missing at {WORKER_SCRIPT}.")

        # The pipeline config decides which models load. Without it the loader
        # falls back to the upstream one and asks for weights that were never
        # downloaded — which surfaces as a confusing missing-file error.
        if not (self._manager.weights_dir(model_id) / "pipeline.json").exists():
            problems.append("The pipeline configuration is missing.")

        manifest = self._manager.manifest(model_id)
        detail = {
            "python": str(self._manager.python_executable(model_id)),
            "weights": str(self._manager.weights_dir(model_id)),
            "device": self._device,
        }
        if manifest and manifest.skipped_components:
            detail["reduced"] = "; ".join(manifest.skipped_components)
        return EnvironmentReport(ok=not problems, problems=problems, detail=detail)

    def estimate_resources(self, request: GenerationRequest) -> ResourceEstimate:
        requirements = TRELLIS2.requirements
        return ResourceEstimate(
            peak_memory_bytes=requirements.estimated_peak_memory_bytes,
            disk_bytes=requirements.disk_bytes,
            # Unset until VOLUM has measured this model on this machine. The
            # published figures are ~17 s on an H100 and over five minutes on a
            # 24 GB Mac; carrying either across would be a guess with a number on it.
            estimated_seconds=None,
        )

    def generate(
        self, request: GenerationRequest, on_progress: ProgressCallback
    ) -> GenerationResult:
        environment = self.validate_environment()
        if not environment.ok:
            raise ProviderExecutionError(
                "TRELLIS.2 is not ready to run.",
                technical="; ".join(environment.problems),
                suggestions=["Install or repair it with: volum models install trellis2"],
            )
        if not request.images:
            raise ProviderExecutionError("No input image was provided.")

        model_id = TRELLIS2.id
        spec = get_install_spec(model_id)
        manifest = self._manager.manifest(model_id)
        worker_env = (manifest.worker_env if manifest else None) or (
            dict(spec.worker_env) if spec else {}
        )

        result, worker = run_worker(
            python_executable=self._manager.python_executable(model_id),
            worker_script=WORKER_SCRIPT,
            request=request.model_copy(
                update={"parameters": {**request.parameters, "worker_env": worker_env}}
            ),
            source_dir=self._manager.source_dir(model_id),
            weights_dir=self._manager.weights_dir(model_id),
            hf_cache_dir=self._manager.hf_cache_dir(model_id),
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
        """Nothing to release: the model lived in a subprocess that has exited."""
        self._worker = None
