from __future__ import annotations

from typing import Any

from .engine_v2.plant_profiles import PlantProfile, resolve_plant_profile


def parse_consumable_bool(value: Any) -> bool:
    try:
        text = str(value or "").strip().lower()
    except Exception:
        text = ""
    if text in ("", "0", "false", "no", "off", "n"):
        return False
    return text in ("1", "true", "yes", "y", "on")


def build_plant_profile(plant_params: Any, options: dict | None = None) -> PlantProfile:
    """Adapter from corr_mrp_bridge plant configuration to Engine V2 PlantProfile.

    This helper lives outside engine_v2 so that the engine stays free of
    Odoo ORM and integration details.

    The function accepts any object that exposes attributes commonly
    used for plant configuration (stages, code, name, printing_ups,
    diecut_ups) and delegates the normalisation to
    engine_v2.plant_profiles.resolve_plant_profile.
    """

    opts = options.copy() if options else {}

    # Allow overriding plant identity via options when useful.
    if "plant_code" not in opts and hasattr(plant_params, "code"):
        opts["plant_code"] = getattr(plant_params, "code", "") or ""
    if "plant_name" not in opts and hasattr(plant_params, "name"):
        opts["plant_name"] = getattr(plant_params, "name", "") or ""

    # Forward UPS hints if present on the config.
    if hasattr(plant_params, "printing_ups") and "printing_ups" not in opts:
        opts["printing_ups"] = getattr(plant_params, "printing_ups", None)
    if hasattr(plant_params, "diecut_ups") and "diecut_ups" not in opts:
        opts["diecut_ups"] = getattr(plant_params, "diecut_ups", None)

    # Forward machine sheet dimensions when available so that the
    # layout engine can rely on PlantProfile for sheet size.
    if hasattr(plant_params, "sheet_length_mm") and "sheet_length_mm" not in opts:
        opts["sheet_length_mm"] = getattr(plant_params, "sheet_length_mm", None)
    if hasattr(plant_params, "sheet_width_mm") and "sheet_width_mm" not in opts:
        opts["sheet_width_mm"] = getattr(plant_params, "sheet_width_mm", None)

    # Forward glue lap / flap configuration for geometry-sensitive styles.
    if hasattr(plant_params, "default_glue_lap_mm") and "default_glue_lap_mm" not in opts:
        opts["default_glue_lap_mm"] = getattr(plant_params, "default_glue_lap_mm", None)
    if hasattr(plant_params, "flap_basis") and "flap_basis" not in opts:
        opts["flap_basis"] = getattr(plant_params, "flap_basis", None)
    if hasattr(plant_params, "flap_fixed_mm") and "flap_fixed_mm" not in opts:
        opts["flap_fixed_mm"] = getattr(plant_params, "flap_fixed_mm", None)
    if hasattr(plant_params, "flap_percent") and "flap_percent" not in opts:
        opts["flap_percent"] = getattr(plant_params, "flap_percent", None)
    if hasattr(plant_params, "flap_percent_of") and "flap_percent_of" not in opts:
        opts["flap_percent_of"] = getattr(plant_params, "flap_percent_of", None)

    # Engine V2 consumables: master feature flag and simple factors.
    # All defaults keep behaviour unchanged when the flag is disabled
    # or configuration is missing.
    try:
        env = getattr(plant_params, "env", None)
    except Exception:
        env = None
    if env is not None:
        try:
            icp = env["ir.config_parameter"].sudo()
        except Exception:
            icp = None
        # icp is a model recordset; it is always "falsy" in a boolean
        # context even though it is usable. Guard only against None so
        # that get_param calls are executed correctly.
        if icp is not None:
            master_raw = icp.get_param("engine_v2_enable_consumables")
            master_flag = parse_consumable_bool(master_raw)
            if master_raw is not None and not master_flag:
                opts["enable_consumables"] = False
                opts["enable_adhesive"] = False
                opts["enable_ink"] = False
                opts["enable_wire"] = False
                opts["enable_corrugation_adhesive"] = False
                opts["enable_lamination_adhesive"] = False
                opts["enable_printing_ink"] = False
                opts["enable_lamination_film"] = False
                opts["enable_stitching_wire"] = False
                opts["enable_jointing_glue"] = False
                opts["enable_jointing_tape"] = False
            elif master_flag:
                opts.setdefault("enable_consumables", True)
                # Adhesive is enabled by default when the consumables
                # master flag is on so existing plants can opt into the
                # original Phase 1 behaviour without extra switches.
                opts.setdefault("enable_adhesive", True)
                opts.setdefault("enable_corrugation_adhesive", opts.get("enable_adhesive", True))
                opts.setdefault("enable_lamination_adhesive", opts.get("enable_adhesive", True))
                opts.setdefault("enable_lamination_film", True)
                opts.setdefault("enable_jointing_glue", opts.get("enable_adhesive", True))

                def _float_param(key):
                    try:
                        raw = icp.get_param(key)
                    except Exception:
                        raw = None
                    if raw in (None, ""):
                        return None
                    try:
                        return float(str(raw).replace(",", ".").strip())
                    except Exception:
                        return None

                corr = _float_param("corr_mrp_bridge.default_adhesive_factor_corrugation_kg_per_m2")
                board = _float_param("corr_mrp_bridge.default_adhesive_factor_board_kg_per_m2")
                fg = _float_param("corr_mrp_bridge.default_adhesive_factor_fg_kg_per_m2")

                if corr is not None and "adhesive_factor_corrugation_kg_per_m2" not in opts:
                    opts["adhesive_factor_corrugation_kg_per_m2"] = corr
                if board is not None and "adhesive_factor_board_kg_per_m2" not in opts:
                    opts["adhesive_factor_board_kg_per_m2"] = board
                if fg is not None and "adhesive_factor_fg_kg_per_m2" not in opts:
                    opts["adhesive_factor_fg_kg_per_m2"] = fg

    for flag_name in (
        "enable_consumables",
        "enable_corrugation_adhesive",
        "enable_lamination_adhesive",
        "enable_printing_ink",
        "enable_lamination_film",
        "enable_stitching_wire",
        "enable_jointing_glue",
        "enable_jointing_tape",
    ):
        if hasattr(plant_params, flag_name) and flag_name not in opts:
            opts[flag_name] = getattr(plant_params, flag_name, None)

    # Forward ink base gm2 from the plant configuration when available
    # so that Engine V2 can compute ink usage without additional ORM
    # lookups. This does not by itself enable ink; the consumables
    # master flag and any future ink-specific toggles control feature
    # activation.
    try:
        ink_gm2 = getattr(plant_params, "ink_gm2_at_100pct_default", None)
    except Exception:
        ink_gm2 = None
    if ink_gm2 not in (None, "") and "ink_gm2_at_100pct_default" not in opts:
        try:
            ink_val = float(str(ink_gm2).replace(",", ".").strip())
        except Exception:
            ink_val = None
        if ink_val is not None:
            opts["ink_gm2_at_100pct_default"] = ink_val

    # Forward wire defaults (stitches per cm / g per stitch) from the
    # plant configuration. These act as fallbacks when the locking
    # configuration does not provide explicit wire parameters.
    try:
        wire_stitches = getattr(plant_params, "wire_stitches_per_cm_default", None)
    except Exception:
        wire_stitches = None
    if wire_stitches not in (None, "") and "wire_stitches_per_cm_default" not in opts:
        try:
            val = float(str(wire_stitches).replace(",", ".").strip())
        except Exception:
            val = None
        if val is not None:
            opts["wire_stitches_per_cm_default"] = val
    try:
        wire_g = getattr(plant_params, "wire_g_per_stitch_default", None)
    except Exception:
        wire_g = None
    if wire_g not in (None, "") and "wire_g_per_stitch_default" not in opts:
        try:
            val = float(str(wire_g).replace(",", ".").strip())
        except Exception:
            val = None
        if val is not None:
            opts["wire_g_per_stitch_default"] = val
    return resolve_plant_profile(plant_params, options=opts)



