from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PlantProfile:
    """Normalized plant profile for the engine.

    This structure is intentionally minimal and independent of Odoo
    models; adapters in corr_mrp_bridge are responsible for building it
    from configuration or records.
    """

    stages: List[str] = field(default_factory=lambda: ["2ply", "board", "fg"])
    code: str = ""
    name: str = ""
    printing_ups: int = 1
    diecut_ups: int = 1
    # Layout behaviour: whether this plant allows blank rotation during
    # sheet layout optimisation. Defaults to True for broad compatibility.
    allow_blank_rotation: bool = True
    # Machine sheet dimensions (mm). When present, these drive the layout
    # engine for blank placement.
    sheet_length_mm: Optional[float] = None
    sheet_width_mm: Optional[float] = None
    # Engine V2 split + sheet behaviour (forwarded from corr.plant.param).
    max_machine_width_mm: Optional[float] = None
    split_mode: str = "none"  # none / equal_only / uneven_allowed
    min_piece_width_mm: Optional[float] = None
    max_piece_width_mm: Optional[float] = None
    default_sheet_behavior: str = "plant_default"
    # Glue lap and flap configuration (forwarded from corr.plant.param).
    default_glue_lap_mm: Optional[float] = None
    flap_basis: str = "fixed"
    flap_fixed_mm: Optional[float] = None
    flap_percent: Optional[float] = None
    flap_percent_of: str = "width"
    # Engine V2 consumables feature flags and factors. These are
    # opt-in and default to disabled so that existing behaviour remains
    # unchanged when no configuration is provided.
    enable_consumables: bool = False
    enable_adhesive: bool = False
    enable_ink: bool = False
    enable_wire: bool = False
    enable_corrugation_adhesive: bool = False
    enable_lamination_adhesive: bool = False
    enable_printing_ink: bool = False
    enable_lamination_film: bool = False
    enable_stitching_wire: bool = False
    enable_jointing_glue: bool = False
    enable_jointing_tape: bool = False
    adhesive_factor_corrugation_kg_per_m2: Optional[float] = None
    adhesive_factor_board_kg_per_m2: Optional[float] = None
    adhesive_factor_fg_kg_per_m2: Optional[float] = None
    ink_gm2_at_100pct_default: Optional[float] = None
    wire_stitches_per_cm_default: Optional[float] = None
    wire_g_per_stitch_default: Optional[float] = None


# Backwards-compatible alias to make the intent explicit in higher layers.
PlantProfileInput = PlantProfile


def resolve_plant_profile(raw_profile: Any, options: Dict | None = None) -> PlantProfile:
    """Convert an arbitrary object into a PlantProfile.

    - If raw_profile is already a PlantProfile, return it.
    - Otherwise, read commonly-named attributes (stages, code, name,
      printing_ups, diecut_ups, sheet_length_mm, sheet_width_mm,
      allow_blank_rotation) and fall back to sensible defaults.
    """

    if isinstance(raw_profile, PlantProfile):
        return raw_profile

    opts = options or {}

    # Stages
    try:
        raw_stages = getattr(raw_profile, "stages", None)
    except Exception:
        raw_stages = None
    if raw_stages:
        try:
            stages = list(raw_stages)
        except TypeError:
            stages = [str(raw_stages)]
    else:
        stages = ["2ply", "board", "fg"]

    # Identity
    code = str(getattr(raw_profile, "code", "") or opts.get("plant_code", "") or "")
    name = str(getattr(raw_profile, "name", "") or opts.get("plant_name", "") or "")

    def _int_attr(obj: Any, attr: str, opt_key: str, default: int) -> int:
        try:
            raw = getattr(obj, attr, None)
        except Exception:
            raw = None
        if raw in (None, "", 0, 0.0):
            raw = opts.get(opt_key, default)
        try:
            val = int(raw)
        except Exception:
            val = default
        return max(1, val)

    def _float_attr(obj: Any, attr: str, opt_key: str, default: float) -> float:
        try:
            raw = getattr(obj, attr, None)
        except Exception:
            raw = None
        if raw in (None, ""):
            raw = opts.get(opt_key, default)
        try:
            if isinstance(raw, str):
                raw = raw.replace(",", ".").strip()
            val = float(raw)
        except Exception:
            val = default
        return float(val or default)

    printing_ups = _int_attr(raw_profile, "printing_ups", "printing_ups", 1)
    diecut_ups = _int_attr(raw_profile, "diecut_ups", "diecut_ups", 1)

    sheet_length_mm = _float_attr(raw_profile, "sheet_length_mm", "sheet_length_mm", 0.0)
    sheet_width_mm = _float_attr(raw_profile, "sheet_width_mm", "sheet_width_mm", 0.0)

    # Layout rotation flag: read from the raw profile when present,
    # otherwise fall back to options or the default (True).
    try:
        allow_rot_raw = getattr(raw_profile, "allow_blank_rotation", None)
    except Exception:
        allow_rot_raw = None
    if allow_rot_raw is None:
        allow_rot_raw = opts.get("allow_blank_rotation", True)
    allow_blank_rotation = bool(allow_rot_raw)

    # Engine V2 split + sheet behaviour: read directly from the raw profile
    # when present and fall back to sensible defaults otherwise.
    def _opt_float(name: str, default: Optional[float] = None) -> Optional[float]:
        try:
            raw = getattr(raw_profile, name, None)
        except Exception:
            raw = None
        if raw in (None, ""):
            return default
        try:
            if isinstance(raw, str):
                raw = raw.replace(",", ".").strip()
            val = float(raw)
        except Exception:
            return default
        return float(val)

    try:
        max_machine_width_mm = _opt_float("max_machine_width_mm")
    except Exception:
        max_machine_width_mm = None

    try:
        split_mode = str(getattr(raw_profile, "split_mode", "") or "").strip() or "none"
    except Exception:
        split_mode = "none"

    try:
        min_piece_width_mm = _opt_float("min_piece_width_mm")
    except Exception:
        min_piece_width_mm = None

    try:
        max_piece_width_mm = _opt_float("max_piece_width_mm")
    except Exception:
        max_piece_width_mm = None

    try:
        default_sheet_behavior = (
            str(getattr(raw_profile, "default_sheet_behavior", "") or "").strip()
            or str(opts.get("default_sheet_behavior", "") or "").strip()
            or "plant_default"
        )
    except Exception:
        default_sheet_behavior = "plant_default"

    # Glue lap / flap configuration.
    default_glue_lap_mm = _float_attr(
        raw_profile, "default_glue_lap_mm", "default_glue_lap_mm", 0.0
    )

    try:
        flap_basis = (
            str(getattr(raw_profile, "flap_basis", "") or opts.get("flap_basis", "") or "")
            .strip()
            or "fixed"
        )
    except Exception:
        flap_basis = "fixed"

    flap_fixed_mm = _float_attr(raw_profile, "flap_fixed_mm", "flap_fixed_mm", 0.0)
    flap_percent = _float_attr(raw_profile, "flap_percent", "flap_percent", 0.0)

    try:
        flap_percent_of = (
            str(
                getattr(raw_profile, "flap_percent_of", "")
                or opts.get("flap_percent_of", "")
                or ""
            )
            .strip()
            or "width"
        )
    except Exception:
        flap_percent_of = "width"

    enable_consumables = bool(opts.get("enable_consumables", False))
    enable_adhesive = bool(opts.get("enable_adhesive", False))
    enable_ink = bool(opts.get("enable_ink", False))
    enable_wire = bool(opts.get("enable_wire", False))
    enable_corrugation_adhesive = bool(opts.get("enable_corrugation_adhesive", enable_adhesive))
    enable_lamination_adhesive = bool(opts.get("enable_lamination_adhesive", enable_adhesive))
    enable_printing_ink = bool(opts.get("enable_printing_ink", enable_ink))
    enable_lamination_film = bool(opts.get("enable_lamination_film", enable_consumables))
    enable_stitching_wire = bool(opts.get("enable_stitching_wire", enable_wire))
    enable_jointing_glue = bool(opts.get("enable_jointing_glue", enable_adhesive))
    enable_jointing_tape = bool(opts.get("enable_jointing_tape", False))
    if not enable_consumables:
        enable_corrugation_adhesive = False
        enable_lamination_adhesive = False
        enable_printing_ink = False
        enable_lamination_film = False
        enable_stitching_wire = False
        enable_jointing_glue = False
        enable_jointing_tape = False

    try:
        adh_corr = opts.get("adhesive_factor_corrugation_kg_per_m2", None)
        if adh_corr is not None:
            adh_corr = float(adh_corr)
    except Exception:
        adh_corr = None
    try:
        adh_board = opts.get("adhesive_factor_board_kg_per_m2", None)
        if adh_board is not None:
            adh_board = float(adh_board)
    except Exception:
        adh_board = None
    try:
        adh_fg = opts.get("adhesive_factor_fg_kg_per_m2", None)
        if adh_fg is not None:
            adh_fg = float(adh_fg)
    except Exception:
        adh_fg = None

    try:
        ink_base = opts.get("ink_gm2_at_100pct_default", None)
        if ink_base is not None:
            ink_base = float(ink_base)
    except Exception:
        ink_base = None

    try:
        wire_stitches = opts.get("wire_stitches_per_cm_default", None)
        if wire_stitches is not None:
            wire_stitches = float(wire_stitches)
    except Exception:
        wire_stitches = None
    try:
        wire_g = opts.get("wire_g_per_stitch_default", None)
        if wire_g is not None:
            wire_g = float(wire_g)
    except Exception:
        wire_g = None

    return PlantProfile(
        stages=[str(s) for s in stages],
        code=code,
        name=name,
        printing_ups=printing_ups,
        diecut_ups=diecut_ups,
        allow_blank_rotation=allow_blank_rotation,
        sheet_length_mm=sheet_length_mm,
        sheet_width_mm=sheet_width_mm,
        max_machine_width_mm=max_machine_width_mm,
        split_mode=split_mode,
        min_piece_width_mm=min_piece_width_mm,
        max_piece_width_mm=max_piece_width_mm,
        default_sheet_behavior=default_sheet_behavior,
        default_glue_lap_mm=default_glue_lap_mm,
        flap_basis=flap_basis,
        flap_fixed_mm=flap_fixed_mm,
        flap_percent=flap_percent,
        flap_percent_of=flap_percent_of,
        enable_consumables=enable_consumables,
        enable_adhesive=enable_adhesive,
        enable_ink=enable_ink,
        enable_wire=enable_wire,
        enable_corrugation_adhesive=enable_corrugation_adhesive,
        enable_lamination_adhesive=enable_lamination_adhesive,
        enable_printing_ink=enable_printing_ink,
        enable_lamination_film=enable_lamination_film,
        enable_stitching_wire=enable_stitching_wire,
        enable_jointing_glue=enable_jointing_glue,
        enable_jointing_tape=enable_jointing_tape,
        adhesive_factor_corrugation_kg_per_m2=adh_corr,
        adhesive_factor_board_kg_per_m2=adh_board,
        adhesive_factor_fg_kg_per_m2=adh_fg,
        ink_gm2_at_100pct_default=ink_base,
        wire_stitches_per_cm_default=wire_stitches,
        wire_g_per_stitch_default=wire_g,
    )



