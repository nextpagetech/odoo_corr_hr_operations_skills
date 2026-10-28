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


def _style_param(spec_input: Any, name: str, default: float = 0.0) -> float:
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


def _style_text_param(spec_input: Any, name: str, default: str = "") -> str:
    for container_name in ("style_params", "style_parameters", "extra_params"):
        try:
            container = getattr(spec_input, container_name, None)
        except Exception:
            container = None
        if isinstance(container, dict) and name in container:
            return str(container.get(name) or default).strip()
    try:
        return str(getattr(spec_input, name, default) or default).strip()
    except Exception:
        return default


@register_style("MONO_STE")
def compute_blank(spec_input, plant_profile) -> Optional[StyleResult]:
    """Compute blank size for a mono-carton Straight Tuck End style.

    The first mono-carton handler uses generic style parameters and keeps
    routing/RM/BoM behavior outside the style layer.

        blank_length_mm = glue_flap + L + W + L + W + trim
        blank_width_mm  = top_tuck + H + bottom_tuck + trim

    Defaults:
      - glue_flap_mm = 20
      - top_tuck_flap_mm = W
      - bottom_tuck_flap_mm = W
      - trim_allowance_mm = 0
      - bleed_mm = 3
    """

    carton_length_mm = _geom(spec_input, "box_length_mm", 0.0)
    carton_width_mm = _geom(spec_input, "box_width_mm", 0.0)
    carton_height_mm = _geom(spec_input, "box_height_mm", 0.0)

    if carton_length_mm <= 0 or carton_width_mm <= 0 or carton_height_mm <= 0:
        return None

    glue_flap_mm = _style_param(spec_input, "glue_flap_mm", 20.0)
    if glue_flap_mm <= 0.0:
        glue_flap_mm = 20.0

    top_tuck_flap_mm = _style_param(spec_input, "top_tuck_flap_mm", carton_width_mm)
    if top_tuck_flap_mm <= 0.0:
        top_tuck_flap_mm = carton_width_mm

    bottom_tuck_flap_mm = _style_param(spec_input, "bottom_tuck_flap_mm", carton_width_mm)
    if bottom_tuck_flap_mm <= 0.0:
        bottom_tuck_flap_mm = carton_width_mm

    trim_allowance_mm = _style_param(spec_input, "trim_allowance_mm", 0.0)
    if trim_allowance_mm < 0.0:
        trim_allowance_mm = 0.0

    bleed_mm = _style_param(spec_input, "bleed_mm", 3.0)
    if bleed_mm < 0.0:
        bleed_mm = 0.0

    carton_board_gsm = _style_param(spec_input, "carton_board_gsm", 300.0)
    if carton_board_gsm <= 0.0:
        carton_board_gsm = 300.0

    grain_direction = _style_text_param(spec_input, "grain_direction", "height") or "height"

    blank_length_mm = glue_flap_mm + (2.0 * carton_length_mm) + (2.0 * carton_width_mm) + trim_allowance_mm
    blank_width_mm = top_tuck_flap_mm + carton_height_mm + bottom_tuck_flap_mm + trim_allowance_mm

    return StyleResult(
        blank_length_mm=blank_length_mm,
        blank_width_mm=blank_width_mm,
        seam_mm=glue_flap_mm,
        flap_mm=max(top_tuck_flap_mm, bottom_tuck_flap_mm),
        score_lines=[
            {
                "axis": "length",
                "positions_mm": [
                    glue_flap_mm,
                    glue_flap_mm + carton_length_mm,
                    glue_flap_mm + carton_length_mm + carton_width_mm,
                    glue_flap_mm + (2.0 * carton_length_mm) + carton_width_mm,
                ],
                "source": "mono_ste_body_panels",
            },
            {
                "axis": "width",
                "positions_mm": [top_tuck_flap_mm, top_tuck_flap_mm + carton_height_mm],
                "source": "mono_ste_tuck_flap_scores",
            },
        ],
        fold_lines=[
            {
                "axis": "length",
                "count": 4,
                "source": "mono_ste_body_panels",
            },
            {
                "axis": "width",
                "count": 2,
                "source": "mono_ste_top_bottom_tuck_flaps",
            },
        ],
        allows_width_split=True,
        allows_length_split=False,
        allows_shared_upstream=True,
        orientation_rules={
            "allow_rotation": True,
            "preferred_grain_axis": grain_direction,
            "notes": "Mono STE uses a rectangular folding-carton blank; grain and print orientation should be honored by layout configuration.",
        },
        metadata={
            "style_code": "MONO_STE",
            "style_name": "Mono Carton - Straight Tuck End",
            "style_family": "mono_carton",
            "contract_version": "1.0",
            "blank_formula": "glue_flap+2L+2W+trim by top_tuck+H+bottom_tuck+trim",
            "glue_flap_mm": glue_flap_mm,
            "top_tuck_flap_mm": top_tuck_flap_mm,
            "bottom_tuck_flap_mm": bottom_tuck_flap_mm,
            "trim_allowance_mm": trim_allowance_mm,
            "bleed_mm": bleed_mm,
            "carton_board_gsm": carton_board_gsm,
            "grain_direction": grain_direction,
            "primary_material_role": "carton_board",
        },
    )
