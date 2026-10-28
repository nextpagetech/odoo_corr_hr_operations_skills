from __future__ import annotations

"""
Style plugin package for Engine V2.

Individual style modules (e.g. style_0201.py) register themselves in the
STYLE_REGISTRY via the @register_style decorator. The engine core then
looks up handlers by style code without hard-coding any particular
style.

This package automatically discovers and imports all style_* modules so
that adding a new style only requires creating engine_v2/styles/style_XXXX.py.
"""

import importlib
import pkgutil

from .contract import MissingStyleParameterError, StyleResult, UnsupportedStyleError  # noqa: F401
from .registry import STYLE_REGISTRY, register_style  # noqa: F401


def _autoload_styles() -> None:
    """Import all style_* modules in this package to trigger registration."""

    pkg_name = __name__
    for module_info in pkgutil.iter_modules(__path__):  # type: ignore[name-defined]
        mod_name = module_info.name
        if not mod_name.startswith("style_"):
            continue
        importlib.import_module(f"{pkg_name}.{mod_name}")


_autoload_styles()

__all__ = [
    "STYLE_REGISTRY",
    "MissingStyleParameterError",
    "StyleResult",
    "UnsupportedStyleError",
    "register_style",
]



