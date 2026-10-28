from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional


class UnsupportedStyleError(ValueError):
    """Raised when a spec asks for a style with no registered handler."""


class MissingStyleParameterError(ValueError):
    """Raised when a registered style lacks a required style parameter."""


@dataclass(frozen=True)
class StyleResult(Mapping[str, Any]):
    """Generic geometry/capability contract returned by style handlers.

    The engine core consumes only generic fields from this structure. New
    style modules should encode style-specific geometry here instead of
    adding branches to StageGraph, RM resolution, materialization, or BoM
    writer code.
    """

    blank_length_mm: float
    blank_width_mm: float
    score_lines: List[Dict[str, Any]] = field(default_factory=list)
    fold_lines: List[Dict[str, Any]] = field(default_factory=list)
    flap_mm: float = 0.0
    seam_mm: float = 0.0
    allows_width_split: bool = True
    allows_length_split: bool = False
    allows_shared_upstream: bool = True
    orientation_rules: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "blank_length_mm": float(self.blank_length_mm or 0.0),
            "blank_width_mm": float(self.blank_width_mm or 0.0),
            "score_lines": list(self.score_lines or []),
            "fold_lines": list(self.fold_lines or []),
            "flap_mm": float(self.flap_mm or 0.0),
            "seam_mm": float(self.seam_mm or 0.0),
            "allows_width_split": bool(self.allows_width_split),
            "allows_length_split": bool(self.allows_length_split),
            "allows_shared_upstream": bool(self.allows_shared_upstream),
            "orientation_rules": dict(self.orientation_rules or {}),
            "metadata": dict(self.metadata or {}),
        }

    def capabilities(self) -> Dict[str, Any]:
        return {
            "allows_width_split": bool(self.allows_width_split),
            "allows_length_split": bool(self.allows_length_split),
            "allows_shared_upstream": bool(self.allows_shared_upstream),
            "orientation_rules": dict(self.orientation_rules or {}),
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)


def normalize_style_result(result: Optional[Any]) -> Optional[StyleResult]:
    """Convert supported style handler results to StyleResult.

    Existing handlers/tests used dictionaries before the explicit contract
    existed. Keeping normalization here lets the engine accept both during
    migration while new style modules return StyleResult directly.
    """

    if result is None:
        return None
    if isinstance(result, StyleResult):
        return result
    if isinstance(result, Mapping):
        data = dict(result)
        return StyleResult(
            blank_length_mm=float(data.get("blank_length_mm") or 0.0),
            blank_width_mm=float(data.get("blank_width_mm") or 0.0),
            score_lines=list(data.get("score_lines") or []),
            fold_lines=list(data.get("fold_lines") or []),
            flap_mm=float(data.get("flap_mm") or 0.0),
            seam_mm=float(data.get("seam_mm") or 0.0),
            allows_width_split=bool(data.get("allows_width_split", True)),
            allows_length_split=bool(data.get("allows_length_split", False)),
            allows_shared_upstream=bool(data.get("allows_shared_upstream", True)),
            orientation_rules=dict(data.get("orientation_rules") or {}),
            metadata=dict(data.get("metadata") or {}),
        )
    raise TypeError(f"Style handler returned unsupported result type: {type(result)!r}")


def require_registered_style(style_code: str, registry: Mapping[str, Any]) -> Any:
    code = (style_code or "").strip().upper()
    if not code:
        return None
    handler = registry.get(code)
    if not handler:
        raise UnsupportedStyleError(
            f"Unsupported style {code!r}. Register a handler in STYLE_REGISTRY "
            f"under engine_v2/styles before using this style for geometry."
        )
    return handler



