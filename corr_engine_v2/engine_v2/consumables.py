from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


CONSUMABLE_FLAG_FIELDS = {
    "corrugation_adhesive": "enable_corrugation_adhesive",
    "lamination_adhesive": "enable_lamination_adhesive",
    "printing_ink": "enable_printing_ink",
    "lamination_film": "enable_lamination_film",
    "stitching_wire": "enable_stitching_wire",
    "jointing_glue": "enable_jointing_glue",
    "jointing_tape": "enable_jointing_tape",
}

CONSUMABLE_PRODUCT_ROLES = {
    "rm_adhesive_corrugation": "corrugation_adhesive",
    "rm_adhesive_board": "lamination_adhesive",
    "rm_adhesive_fg": "jointing_glue",
    "rm_ink": "printing_ink",
    "rm_print": "printing_ink",
    "rm_printing_ink": "printing_ink",
    "rm_lamination_film": "lamination_film",
    "rm_wire": "stitching_wire",
    "rm_tape": "jointing_tape",
}

CONSUMABLE_ROLE_ALIASES = {
    "adhesive_corrugation": "corrugation_adhesive",
    "corrugation_adhesive": "corrugation_adhesive",
    "adhesive_board": "lamination_adhesive",
    "lamination_adhesive": "lamination_adhesive",
    "adhesive_fg": "jointing_glue",
    "jointing_glue": "jointing_glue",
    "glue": "jointing_glue",
    "ink": "printing_ink",
    "print": "printing_ink",
    "printing": "printing_ink",
    "printing_ink": "printing_ink",
    "lamination": "lamination_film",
    "lamination_film": "lamination_film",
    "film": "lamination_film",
    "wire": "stitching_wire",
    "stitching_wire": "stitching_wire",
    "tape": "jointing_tape",
    "jointing_tape": "jointing_tape",
}


def boolish(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    try:
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def canonical_consumable_role(
    role: Any = "",
    *,
    product_key: Any = "",
    metadata: Optional[Mapping[str, Any]] = None,
) -> str:
    metadata = metadata or {}
    candidates = [
        role,
        metadata.get("kind"),
        metadata.get("semantic_role"),
        product_key,
    ]
    for candidate in candidates:
        key = str(candidate or "").strip().lower()
        if not key:
            continue
        if key in CONSUMABLE_ROLE_ALIASES:
            return CONSUMABLE_ROLE_ALIASES[key]
        if key in CONSUMABLE_PRODUCT_ROLES:
            return CONSUMABLE_PRODUCT_ROLES[key]
        if key.startswith("adhesive_corr"):
            return "corrugation_adhesive"
        if key.startswith("adhesive_board") or key.startswith("adhesive_lam"):
            return "lamination_adhesive"
        if key.startswith("adhesive_fg") or "jointing_glue" in key:
            return "jointing_glue"
        if "lamination_film" in key:
            return "lamination_film"
        if "ink" in key or "print" in key:
            return "printing_ink"
        if "wire" in key:
            return "stitching_wire"
        if "tape" in key:
            return "jointing_tape"
    return ""


def is_consumable_role(role: Any = "", *, product_key: Any = "", metadata: Optional[Mapping[str, Any]] = None) -> bool:
    return bool(canonical_consumable_role(role, product_key=product_key, metadata=metadata))


def effective_consumable_state(
    *,
    plant_profile: Any = None,
    spec: Any = None,
    options: Optional[Mapping[str, Any]] = None,
) -> Dict[str, bool]:
    opts = dict(options or {})

    global_default = boolish(
        opts.get("enable_consumables", getattr(plant_profile, "enable_consumables", False)),
        default=False,
    )

    def _spec_bool(name: str, default: bool = False) -> bool:
        if spec is None:
            return bool(default)
        return boolish(getattr(spec, name, default), default=default)

    def _surface_bool(name: str, default: bool = False) -> bool:
        surface = getattr(spec, "surface_profile", None) if spec is not None else None
        if surface is not None and hasattr(surface, name):
            return boolish(getattr(surface, name, default), default=default)
        return _spec_bool(name, default)

    def _joining_type() -> str:
        raw = ""
        if spec is not None:
            raw = getattr(spec, "joining_type", "") or ""
        if not raw:
            raw = opts.get("joining_type", "") or ""
        try:
            text = str(raw or "").strip().lower()
        except Exception:
            text = ""
        text = text.replace("-", "_").replace(" ", "_").replace("+", "_")
        aliases = {
            "": "none",
            "none": "none",
            "glue": "gluing",
            "gluing": "gluing",
            "adhesive": "gluing",
            "jointing_glue": "gluing",
            "wire": "stitching",
            "stitch": "stitching",
            "stitching": "stitching",
            "both": "gluing_stitching",
            "glue_wire": "gluing_stitching",
            "glue_stitching": "gluing_stitching",
            "gluing_stitching": "gluing_stitching",
            "tape": "tape",
            "taping": "tape",
        }
        return aliases.get(text, "none")

    joining_type = _joining_type()
    printing_required = (
        _surface_bool("printing_required", False)
        or _spec_bool("surface_printing_required", False)
    )
    lamination_required = (
        _surface_bool("lamination_required", False)
        or _spec_bool("surface_lamination_required", False)
    )
    adhesive_default = boolish(
        getattr(plant_profile, "enable_adhesive", True),
        default=True,
    )
    def _role_flag(field_name: str, legacy_default: bool = False) -> bool:
        if field_name in opts:
            return boolish(opts.get(field_name), default=False)
        raw = getattr(plant_profile, field_name, None)
        if raw is None:
            return bool(legacy_default)
        return boolish(raw, default=False) or bool(legacy_default)

    state = {
        "enable_consumables": global_default,
        "enable_corrugation_adhesive": bool(adhesive_default),
        "enable_lamination_adhesive": bool(adhesive_default),
        "enable_printing_ink": bool(printing_required),
        "enable_lamination_film": bool(lamination_required),
        "enable_stitching_wire": joining_type in {"stitching", "gluing_stitching"},
        "enable_jointing_glue": joining_type in {"gluing", "gluing_stitching"},
        "enable_jointing_tape": joining_type == "tape",
        "joining_type": joining_type,
        "printing_required": bool(printing_required),
        "lamination_required": bool(lamination_required),
    }

    # Backward compatibility: older specs that explicitly disabled plant
    # defaults before joining_type existed may still carry manual flags.
    # Use them only when no process-level joining/print/lamination signal
    # is present.
    use_plant_defaults = True
    if spec is not None:
        use_plant_defaults = boolish(getattr(spec, "use_plant_consumable_defaults", True), default=True)
    if spec is not None and not use_plant_defaults:
        if joining_type == "none":
            for field_name in (
                "enable_stitching_wire",
                "enable_jointing_glue",
                "enable_jointing_tape",
            ):
                if hasattr(spec, field_name):
                    state[field_name] = boolish(getattr(spec, field_name), default=False)
        if not printing_required and hasattr(spec, "enable_printing_ink"):
            state["enable_printing_ink"] = boolish(getattr(spec, "enable_printing_ink"), default=False)
        if not lamination_required:
            for field_name in ("enable_lamination_adhesive", "enable_lamination_film"):
                if hasattr(spec, field_name):
                    state[field_name] = boolish(getattr(spec, field_name), default=False)

    if not state["enable_consumables"]:
        for field_name in CONSUMABLE_FLAG_FIELDS.values():
            state[field_name] = False
    return state


def is_consumable_enabled(
    state: Optional[Mapping[str, Any]],
    role: Any = "",
    *,
    product_key: Any = "",
    metadata: Optional[Mapping[str, Any]] = None,
) -> bool:
    canonical = canonical_consumable_role(role, product_key=product_key, metadata=metadata)
    if not canonical:
        return True
    if not state or not boolish(state.get("enable_consumables"), default=False):
        return False
    field_name = CONSUMABLE_FLAG_FIELDS.get(canonical)
    return boolish(state.get(field_name), default=False) if field_name else False


def should_include_consumable(
    state: Optional[Mapping[str, Any]],
    role: Any = "",
    *,
    product_key: Any = "",
    metadata: Optional[Mapping[str, Any]] = None,
    material_type: Any = "",
    category: Any = "",
) -> bool:
    """Return whether a consumable row/attachment should be emitted.

    Unknown consumable roles are allowed only when the global switch is on.
    Known roles also require their individual role flag. This prevents generic
    seeded attachments such as COMP_GENERIC from leaking shadow_pending rows
    when consumables are globally disabled.
    """

    material_key = str(material_type or category or "").strip().lower()
    canonical = canonical_consumable_role(role, product_key=product_key, metadata=metadata)
    is_consumable = material_key == "consumable" or bool(canonical)
    if not is_consumable:
        return True
    if not state or not boolish(state.get("enable_consumables"), default=False):
        return False
    if not canonical:
        return True
    return is_consumable_enabled(state, role, product_key=product_key, metadata=metadata)


def consumable_state_signature(state: Optional[Mapping[str, Any]]) -> Dict[str, bool]:
    source = dict(state or {})
    return {
        "enable_consumables": boolish(source.get("enable_consumables"), default=False),
        **{
            field_name: boolish(source.get(field_name), default=False)
            for field_name in CONSUMABLE_FLAG_FIELDS.values()
        },
        "joining_type": str(source.get("joining_type") or "none"),
        "printing_required": boolish(source.get("printing_required"), default=False),
        "lamination_required": boolish(source.get("lamination_required"), default=False),
    }
