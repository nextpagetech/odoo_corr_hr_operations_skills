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


@register_style("0100")
def compute_blank(spec_input, plant_profile) -> Optional[StyleResult]:
    """Compute roll geometry for FEFCO 0100.

    0100 is a roll product style. It defines output form and area only;
    board construction remains stack-driven by the existing Engine V2
    stack model.

    The handler consumes normalized geometry:

        box_length_mm -> roll length in mm
        box_width_mm  -> roll width in mm

    UI-specific meter-to-millimeter conversion happens before this handler.
    """

    _ = plant_profile

    roll_length_mm = _geom(spec_input, "box_length_mm", 0.0)
    roll_width_mm = _geom(spec_input, "box_width_mm", 0.0)

    if roll_length_mm <= 0.0:
        raise MissingStyleParameterError(
            "FEFCO 0100 requires Roll Length > 0."
        )
    if roll_width_mm <= 0.0:
        raise MissingStyleParameterError(
            "FEFCO 0100 requires Roll Width > 0."
        )

    roll_length_m = roll_length_mm / 1000.0
    area_m2_per_fg = (roll_length_m * roll_width_mm) / 1000.0

    return StyleResult(
        blank_length_mm=roll_length_mm,
        blank_width_mm=roll_width_mm,
        seam_mm=0.0,
        flap_mm=0.0,
        score_lines=[],
        fold_lines=[],
        allows_width_split=False,
        allows_length_split=False,
        allows_shared_upstream=True,
        orientation_rules={
            "allow_rotation": False,
            "preferred_grain_axis": "length",
            "notes": "0100 is a finished corrugated roll product.",
        },
        metadata={
            "style_code": "0100",
            "style_name": "Roll Product",
            "family": "roll",
            "contract_version": "1.0",
            "blank_formula": "roll_length*roll_width",
            "roll_length_m": roll_length_m,
            "roll_width_mm": roll_width_mm,
            "area_m2_per_fg": area_m2_per_fg,
            "uom_basis": "roll",
            "requires_box_height": False,
            "uses_seam": False,
            "uses_flaps": False,
            "allows_any_stack": True,
        },
    )



