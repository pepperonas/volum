"""VOLUM core — the single place pipeline logic lives.

The HTTP engine (``volum_engine``) and the CLI (``volum_cli``) are thin shells
over this package. Business logic in either of them is a bug (spec section 32).
"""

from .version import __version__

__all__ = ["__version__"]
