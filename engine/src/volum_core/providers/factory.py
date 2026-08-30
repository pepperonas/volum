"""Getting a provider instance for a model id.

Deliberately not re-exported from ``providers/__init__``: the provider package
and the model package reference each other, and pulling the factory into the
package initialiser closes that loop. Import it by module.

One place. Specification section 68 makes this the test of whether the
abstraction is real: adding the second provider should not require edits
scattered through the application. It required this table and nothing else —
the CLI, the pipeline and the job system never learned that TRELLIS.2 exists.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .trellis2 import Trellis2Provider
from .triposr import TripoSRProvider
from .types import ImageTo3DProvider

if TYPE_CHECKING:
    from ..models.manager import ModelManager

#: model id -> constructor. The only place a provider class is named.
_PROVIDERS: dict[str, Callable[[ModelManager, str], ImageTo3DProvider]] = {
    "triposr": lambda manager, device: TripoSRProvider(manager, device=device),
    "trellis2": lambda manager, device: Trellis2Provider(manager, device=device),
}


def available_provider_ids() -> tuple[str, ...]:
    return tuple(_PROVIDERS)


def create_provider(
    model_id: str, manager: ModelManager, *, device: str
) -> ImageTo3DProvider | None:
    """Build the provider for ``model_id``, or ``None`` if there is no implementation.

    ``None`` rather than an exception: a model can legitimately be in the
    registry — documented, licence-checked, visible in the catalogue — before it
    has an implementation, and the caller says so better than a traceback.
    """
    factory = _PROVIDERS.get(model_id)
    return factory(manager, device) if factory is not None else None
