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


def _style_param(spec_input: Any, name: str, default: float = 0.0) -> float:
    """Read a style parameter from common generic containers or attrs."""

    for container_name in ("style_params", "style_parameters", "extra_params"):
        try:
            container = getattr(spec_input, container_name, None)
        except Exception:
            container = None
        if isinstance(container, dict) and name in container:
            try:
                return float(container.get(name) or default)
            except Exception:
                return default
    return _geom(spec_input, name, default)


@register_style("0202")
def compute_blank(spec_input, plant_profile) -> Optional[StyleResult]:
    """Compute blank size for a 0202 Overlap Slotted Container.

    0202 follows the 0201 body-panel baseline, but the top and bottom
    outer flaps partially overlap. Until separate top/bottom overlap
    inputs exist, the handler uses a symmetric overlap parameter:

        top_flap_depth    = W / 2 + overlap_mm
        bottom_flap_depth = W / 2 + overlap_mm

        blank_length_mm = 2 * (L + W) + seam_mm
        blank_width_mm  = H + W + (2 * overlap_mm)

    overlap_mm is required and must be positive so missing style setup
    fails clearly at the style boundary.
    """

    box_length_mm = _geom(spec_input, "box_length_mm", 0.0)
    box_width_mm = _geom(spec_input, "box_width_mm", 0.0)
    box_height_mm = _geom(spec_input, "box_height_mm", 0.0)

    if box_length_mm <= 0 or box_width_mm <= 0 or box_height_mm <= 0:
        return None

    overlap_mm = _style_param(spec_input, "overlap_mm", 0.0)
    if overlap_mm <= 0.0:
        raise MissingStyleParameterError(
            "FEFCO 0202 requires style parameter overlap_mm > 0."
        )

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

    top_flap_depth = (box_width_mm / 2.0) + overlap_mm
    bottom_flap_depth = (box_width_mm / 2.0) + overlap_mm
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
                "source": "0202_body_panels",
            },
            {
                "axis": "width",
                "positions_mm": [top_flap_depth, top_flap_depth + box_height_mm],
                "source": "0202_overlap_flap_scores",
            },
        ],
        fold_lines=[
            {
                "axis": "length",
                "count": 4,
                "source": "0202_body_panels",
            },
            {
                "axis": "width",
                "count": 2,
                "source": "0202_top_bottom_overlap_flaps",
            },
        ],
        allows_width_split=True,
        allows_length_split=False,
        allows_shared_upstream=True,
        orientation_rules={
            "allow_rotation": True,
            "preferred_grain_axis": "",
            "notes": "0202 uses generic rectangular layout with overlap flap depth.",
        },
        metadata={
            "style_code": "0202",
            "style_name": "Overlap Slotted Container",
            "contract_version": "1.0",
            "blank_formula": "2*(L+W)+seam by H+W+2*overlap",
            "overlap_mm": overlap_mm,
            "top_flap_depth_mm": top_flap_depth,
            "bottom_flap_depth_mm": bottom_flap_depth,
        },
    )




