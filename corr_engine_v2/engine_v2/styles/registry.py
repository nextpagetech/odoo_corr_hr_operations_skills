from __future__ import annotations

from typing import Any, Callable, Dict


# Simple in-memory registry mapping style codes to handler callables.
STYLE_REGISTRY: Dict[str, Callable[..., Any]] = {}


def register_style(style_code: str):
    """Decorator to register a style handler.

    Usage:

        @register_style("0201")
        def compute_blank(spec_input, plant_profile):
            ...

    The style code is stored in uppercase to make lookups
    case-insensitive.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        code = (style_code or "").strip().upper()
        if code:
            STYLE_REGISTRY[code] = fn
        return fn

    return decorator




