from __future__ import annotations

from typing import Any, Optional

from .contract import MissingStyleParameterError, StyleResult
from .registry import register_style


def _f(obj: Any, name: str, default: float = 0.0) -> float:
    try:
        return float(getattr(obj, name, default) or default)
    except Exception:
        return default


def _geom(spec_input: Any, name: str, default: float = 0.0) -> float:
    try:
        geometry = getattr(spec_input, "geometry", None)
    except Exception:
        geometry = None
    if geometry is not None:
        try:
            return float(getattr(geometry, name, default) or default)
        except Exception:
            pass
    return _f(spec_input, name, default)


def _style_param(spec_input: Any, key: str, default: Any = None) -> Any:
    for container_name in ("style_params", "style_parameters", "extra_params"):
        try:
            container = getattr(spec_input, container_name, None)
        except Exception:
            container = None
        if isinstance(container, dict) and key in container:
            return container.get(key)
    return default


def _fg_per_sheet(spec_input: Any) -> int:
    raw = _style_param(spec_input, "fg_per_sheet", None)
    if raw in (None, ""):
        raw = _style_param(spec_input, "diecut_ups", None)
    if raw in (None, ""):
        raise MissingStyleParameterError(
            "Custom Diecut Sheet requires FG per Sheet > 0."
        )
    try:
        value_f = float(raw)
    except Exception as exc:
        raise MissingStyleParameterError(
            "Custom Diecut Sheet requires FG per Sheet to be an integer."
        ) from exc
    value_i = int(value_f)
    if value_f != float(value_i) or value_i < 1:
        raise MissingStyleParameterError(
            "Custom Diecut Sheet requires FG per Sheet to be an integer >= 1."
        )
    return value_i


@register_style("DIECUT_SHEET")
def compute_blank(spec_input, plant_profile) -> Optional[StyleResult]:
    """Compute manual sheet geometry and per-FG yield for diecut sheets."""

    _ = plant_profile

    sheet_length_mm = _geom(spec_input, "box_length_mm", 0.0)
    sheet_width_mm = _geom(spec_input, "box_width_mm", 0.0)
    fg_per_sheet = _fg_per_sheet(spec_input)

    if sheet_length_mm <= 0.0:
        raise MissingStyleParameterError(
            "Custom Diecut Sheet requires Sheet Length > 0."
        )
    if sheet_width_mm <= 0.0:
        raise MissingStyleParameterError(
            "Custom Diecut Sheet requires Sheet Width > 0."
        )

    area_m2_per_sheet = (sheet_length_mm * sheet_width_mm) / 1_000_000.0
    area_m2_per_fg = area_m2_per_sheet / float(fg_per_sheet or 1)

    return StyleResult(
        blank_length_mm=sheet_length_mm,
        blank_width_mm=sheet_width_mm,
        seam_mm=0.0,
        flap_mm=0.0,
        score_lines=[],
        fold_lines=[],
        allows_width_split=False,
        allows_length_split=False,
        allows_shared_upstream=True,
        orientation_rules={
            "allow_rotation": True,
            "preferred_grain_axis": "",
            "notes": "DIECUT_SHEET uses manual sheet geometry and FG-per-sheet yield.",
        },
        metadata={
            "style_code": "DIECUT_SHEET",
            "style_name": "Custom Diecut Sheet",
            "family": "diecut_sheet",
            "contract_version": "1.0",
            "blank_formula": "sheet_length*sheet_width",
            "sheet_length_mm": sheet_length_mm,
            "sheet_width_mm": sheet_width_mm,
            "area_m2_per_sheet": area_m2_per_sheet,
            "fg_per_sheet": fg_per_sheet,
            "diecut_ups": fg_per_sheet,
            "area_m2_per_fg": area_m2_per_fg,
            "uom_basis": "fg",
            "yield_basis": "fg_per_sheet",
            "yield_stage_policy": "route_selected",
            "yield_stage_precedence": [
                "diecut",
                "peeling",
                "conversion",
                "terminal",
            ],
            "peeling_aliases": [
                "peeling",
                "stripping",
                "waste stripping",
                "peel-off",
                "peeloff",
            ],
            "requires_box_height": False,
            "uses_seam": False,
            "uses_flaps": False,
            "uses_overlap": False,
        },
    )



