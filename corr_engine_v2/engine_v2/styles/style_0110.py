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


@register_style("0110")
def compute_blank(spec_input, plant_profile) -> Optional[StyleResult]:
    """Compute finished sheet geometry for FEFCO 0110.

    0110 is a corrugated sheet product, not a box:

        blank_length_mm = sheet_length_mm
        blank_width_mm  = sheet_width_mm
        area_m2_per_fg  = sheet_length_mm * sheet_width_mm / 1_000_000

    The first implementation reuses the main geometry fields:

        box_length_mm -> sheet_length_mm
        box_width_mm  -> sheet_width_mm

    It does not require box height, seam/glue flap, flaps, overlap, or
    any 0110-specific style parameters.
    """

    _ = plant_profile

    sheet_length_mm = _geom(spec_input, "box_length_mm", 0.0)
    sheet_width_mm = _geom(spec_input, "box_width_mm", 0.0)

    if sheet_length_mm <= 0.0:
        raise MissingStyleParameterError(
            "FEFCO 0110 requires Sheet Length > 0."
        )
    if sheet_width_mm <= 0.0:
        raise MissingStyleParameterError(
            "FEFCO 0110 requires Sheet Width > 0."
        )

    area_m2_per_fg = (sheet_length_mm * sheet_width_mm) / 1_000_000.0

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
            "notes": "0110 is a finished corrugated sheet product.",
        },
        metadata={
            "style_code": "0110",
            "style_name": "Corrugated Sheet",
            "family": "sheet",
            "contract_version": "1.0",
            "blank_formula": "sheet_length*sheet_width",
            "sheet_length_mm": sheet_length_mm,
            "sheet_width_mm": sheet_width_mm,
            "area_m2_per_fg": area_m2_per_fg,
            "uom_basis": "sheet",
        },
    )



