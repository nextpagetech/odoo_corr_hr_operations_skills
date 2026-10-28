from __future__ import annotations

from typing import Any, Optional

from .contract import StyleResult
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


@register_style("0203")
def compute_blank(spec_input, plant_profile) -> Optional[StyleResult]:
    """Compute blank size for a 0203 Full Overlap Slotted Container.

    0203 follows the 0201/0202 body-panel wrap, but uses full-overlap
    top and bottom flaps:

        top_flap_depth    = W
        bottom_flap_depth = W

        blank_length_mm = 2 * (L + W) + seam_mm
        blank_width_mm  = H + (2 * W)

    The first supported Engine V2 interpretation has no style-specific
    parameters. Any production clearance/shortfall rule should be added
    later as a style parameter only after confirmation.
    """

    box_length_mm = _geom(spec_input, "box_length_mm", 0.0)
    box_width_mm = _geom(spec_input, "box_width_mm", 0.0)
    box_height_mm = _geom(spec_input, "box_height_mm", 0.0)

    if box_length_mm <= 0 or box_width_mm <= 0 or box_height_mm <= 0:
        return None

    seam_mm = _geom(spec_input, "glue_flap_override_mm", 0.0)
    if seam_mm <= 0.0:
        seam_mm = _f(spec_input, "glue_flap_mm", 0.0)
    if seam_mm <= 0.0:
        try:
            seam_mm = float(getattr(plant_profile, "default_glue_lap_mm", 0.0) or 0.0)
        except Exception:
            seam_mm = 0.0
    if seam_mm <= 0.0:
        seam_mm = 20.0

    top_flap_depth = box_width_mm
    bottom_flap_depth = box_width_mm
    blank_length_mm = 2.0 * (box_length_mm + box_width_mm) + seam_mm
    blank_width_mm = top_flap_depth + box_height_mm + bottom_flap_depth

    return StyleResult(
        blank_length_mm=blank_length_mm,
        blank_width_mm=blank_width_mm,
        seam_mm=seam_mm,
        flap_mm=max(top_flap_depth, bottom_flap_depth),
        score_lines=[
            {
                "axis": "length",
                "positions_mm": [
                    box_length_mm,
                    box_length_mm + box_width_mm,
                    (2.0 * box_length_mm) + box_width_mm,
                    2.0 * (box_length_mm + box_width_mm),
                ],
                "source": "0203_body_panels",
            },
            {
                "axis": "width",
                "positions_mm": [top_flap_depth, top_flap_depth + box_height_mm],
                "source": "0203_full_overlap_flap_scores",
            },
        ],
        fold_lines=[
            {
                "axis": "length",
                "count": 4,
                "source": "0203_body_panels",
            },
            {
                "axis": "width",
                "count": 2,
                "source": "0203_top_bottom_full_overlap_flaps",
            },
        ],
        allows_width_split=True,
        allows_length_split=False,
        allows_shared_upstream=True,
        orientation_rules={
            "allow_rotation": True,
            "preferred_grain_axis": "",
            "notes": "0203 uses generic rectangular layout with full-overlap flap depth.",
        },
        metadata={
            "style_code": "0203",
            "style_name": "Full Overlap Slotted Container",
            "contract_version": "1.0",
            "blank_formula": "2*(L+W)+seam by H+2*W",
            "top_flap_depth_mm": top_flap_depth,
            "bottom_flap_depth_mm": bottom_flap_depth,
        },
    )



