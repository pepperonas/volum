"""Compatibility module providing ``torchmcubes`` on top of PyMCubes.

**Why this exists.** TripoSR requires ``torchmcubes``, a C++/CUDA extension that
must be compiled from a git checkout. It has no wheels, does not build usefully
on Apple Silicon, and its CUDA path is irrelevant on Metal. PyMCubes ships a
prebuilt arm64 wheel and computes the same isosurface.

**What it is not.** This does not patch TripoSR. The upstream checkout stays
exactly what its pinned commit says it is; this module is copied into the
provider's own environment and satisfies the import. If upstream ever publishes
usable wheels, deleting the shim from the install spec is the whole change.

The extraction runs on CPU. That is not a meaningful cost: it happens once per
generation on a grid of a few hundred cubed, while the diffusion and decoding
that surround it dominate by orders of magnitude.
"""

from __future__ import annotations

from typing import Any

import mcubes
import numpy as np
import torch


def marching_cubes(volume: Any, threshold: float) -> tuple[torch.Tensor, torch.Tensor]:
    """Extract an isosurface at ``threshold``.

    Mirrors ``torchmcubes.marching_cubes``: takes a 3-D scalar field, returns
    ``(vertices, faces)`` as torch tensors on the input's device.

    The sign convention matches upstream, where the surface encloses the region
    **above** the threshold. PyMCubes encloses the region below it, so the field
    is negated — getting this backwards produces an inside-out mesh that still
    looks plausible in a viewer, which is exactly the kind of bug that survives
    to a release.
    """
    if isinstance(volume, torch.Tensor):
        device = volume.device
        field = volume.detach().to("cpu", dtype=torch.float32).numpy()
    else:
        device = torch.device("cpu")
        field = np.asarray(volume, dtype=np.float32)

    vertices, faces = mcubes.marching_cubes(-field, -float(threshold))

    return (
        torch.from_numpy(np.ascontiguousarray(vertices, dtype=np.float32)).to(device),
        torch.from_numpy(np.ascontiguousarray(faces, dtype=np.int64)).to(device),
    )


def grid_interp(*args: object, **kwargs: object) -> None:
    """Present so an ``import`` of the upstream API does not fail late.

    Raises rather than returning something wrong: a silently incorrect
    interpolation would corrupt geometry in a way that is very hard to trace.
    """
    raise NotImplementedError(
        "grid_interp is not provided by the VOLUM PyMCubes compatibility module. "
        "If a provider needs it, the shim must be extended rather than guessed at."
    )


__all__ = ["grid_interp", "marching_cubes"]
