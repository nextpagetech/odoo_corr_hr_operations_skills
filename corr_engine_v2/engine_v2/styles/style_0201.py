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
    """Read a float from GeometrySpec when available, with fallback.

    This keeps GeometrySpec as the primary source of truth for V2 specs
    while preserving compatibility with legacy attributes on the
    EngineSpecInput instance.
    """

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


@register_style("0201")
def compute_blank(spec_input, plant_profile) -> Optional[StyleResult]:
    """Compute blank size for a 0201-style box.

    This mirrors the legacy engine geometry used for 0201/0203:

        seam_mm      ~= glue lap / joint allowance
        flap_add_mm  ~= top/bottom flap allowance

        blank_length_mm = 2 * (L + W) + seam_mm
        blank_width_mm  = W + H + flap_add_mm

    where:
      - L = box_length_mm
      - W = box_width_mm
      - H = box_height_mm

    Seam precedence:
      1) GeometrySpec.glue_flap_override_mm when present,
      2) spec_input.glue_flap_mm,
      3) plant_profile.default_glue_lap_mm,
      4) hard-coded fallback of 20 mm.

    Flap addition precedence:
      1) when plant_profile.flap_basis == "percent", use
         max(plant_profile.flap_fixed_mm,
             basis_dim * flap_percent / 100)
         where basis_dim is W or H depending on flap_percent_of,
      2) otherwise, plant_profile.flap_fixed_mm,
      3) hard-coded fallback of 20 mm when neither plant nor spec
         provide a flap config.

    If any of L/W/H are missing or zero, returns None so that the
    caller can fall back to any existing blank dimensions.
    """

    # Prefer GeometrySpec fields when available.
    box_length_mm = _geom(spec_input, "box_length_mm", 0.0)
    box_width_mm = _geom(spec_input, "box_width_mm", 0.0)
    box_height_mm = _geom(spec_input, "box_height_mm", 0.0)

    if box_length_mm <= 0 or box_width_mm <= 0 or box_height_mm <= 0:
        return None

    # Seam / glue lap: prefer spec/geometry overrides, then plant,
    # then a conservative default.
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

    # Flap addition: mirror the legacy flap rules when plant info is
    # available, otherwise fall back to a simple fixed allowance.
    flap_add = 0.0
    try:
        flap_basis = (getattr(plant_profile, "flap_basis", "") or "fixed").strip()
    except Exception:
        flap_basis = "fixed"
    try:
        flap_percent_of = (getattr(plant_profile, "flap_percent_of", "") or "width").strip()
    except Exception:
        flap_percent_of = "width"
    try:
        flap_fixed_mm = float(getattr(plant_profile, "flap_fixed_mm", 0.0) or 0.0)
    except Exception:
        flap_fixed_mm = 0.0
    try:
        flap_percent = float(getattr(plant_profile, "flap_percent", 0.0) or 0.0)
    except Exception:
        flap_percent = 0.0

    if flap_basis == "percent":
        basis_dim = box_width_mm if flap_percent_of == "width" else box_height_mm
        flap_add = max(flap_fixed_mm, basis_dim * (flap_percent / 100.0))
    else:
        flap_add = flap_fixed_mm

    if flap_add <= 0.0:
        # Legacy plant presets typically use ~20mm; fall back to this
        # when no flap configuration is provided.
        flap_add = 20.0

    blank_length_mm = 2.0 * (box_length_mm + box_width_mm) + seam_mm
    blank_width_mm = box_width_mm + box_height_mm + flap_add

    # Expose core geometry values for downstream consumers:
    # - blank_length_mm / blank_width_mm drive metrics.m2_per_box.
    # - seam_mm is consumed by consumables such as wire, which treat
    #   it as the effective stitched/glued seam length per box.
    # For styles without a seam, or when geometry cannot be resolved,
    # seam_mm is expected to be zero or absent.
    return StyleResult(
        blank_length_mm=blank_length_mm,
        blank_width_mm=blank_width_mm,
        seam_mm=seam_mm,
        flap_mm=flap_add,
        score_lines=[
            {
                "axis": "length",
                "positions_mm": [
                    box_length_mm,
                    box_length_mm + box_width_mm,
                    (2.0 * box_length_mm) + box_width_mm,
                    2.0 * (box_length_mm + box_width_mm),
                ],
                "source": "0201_body_panels",
            },
            {
                "axis": "width",
                "positions_mm": [flap_add / 2.0, (flap_add / 2.0) + box_height_mm],
                "source": "0201_flap_scores",
            },
        ],
        fold_lines=[
            {
                "axis": "length",
                "count": 4,
                "source": "0201_body_panels",
            },
            {
                "axis": "width",
                "count": 2,
                "source": "0201_top_bottom_flaps",
            },
        ],
        allows_width_split=True,
        allows_length_split=False,
        allows_shared_upstream=True,
        orientation_rules={
            "allow_rotation": True,
            "preferred_grain_axis": "",
            "notes": "0201 uses the generic rectangular blank layout rules.",
        },
        metadata={
            "style_code": "0201",
            "style_name": "Regular Slotted Container",
            "contract_version": "1.0",
            "blank_formula": "2*(L+W)+seam by W+H+flap",
        },
    )



