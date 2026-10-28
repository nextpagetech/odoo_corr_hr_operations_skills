from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

import json
from types import SimpleNamespace

from .engine_v2 import EngineSpecInput
from .engine_v2.spec_input import (
    GeometrySpec,
    BoardLayerInput,
    BoardRecipeInput,
    SurfaceProfileInput,
    LayoutPolicyInput,
    PiecePlanInput,
    PieceSpecInput,
    RoutingProfileInput,
    RoutingStageInput,
    EngineOverrides,
)


def _float_from_attr(obj: Any, name: str) -> float:
    """Safely read a float attribute from an arbitrary object."""

    try:
        value = getattr(obj, name, 0.0)
    except Exception:
        value = 0.0
    try:
        if isinstance(value, str):
            value = value.replace(",", ".").strip()
        return float(value or 0.0)
    except Exception:
        return 0.0


def _str_from_attr(obj: Any, name: str) -> str:
    """Safely read a string attribute from an arbitrary object."""

    try:
        value = getattr(obj, name, "") or ""
    except Exception:
        value = ""
    try:
        return str(value)
    except Exception:
        return ""


def _piece_source_from_attr(obj: Any, name: str) -> str:
    value = (_str_from_attr(obj, name) or "inherited").strip().lower()
    if value == "override":
        return "custom"
    return value or "inherited"


def _normalize_joining_type(value: Any) -> str:
    try:
        text = str(value or "").strip().lower()
    except Exception:
        text = ""
    text = text.replace("-", "_").replace(" ", "_").replace("+", "_")
    aliases = {
        "": "none",
        "none": "none",
        "no": "none",
        "glue": "gluing",
        "gluing": "gluing",
        "adhesive": "gluing",
        "jointing_glue": "gluing",
        "stitch": "stitching",
        "stitching": "stitching",
        "wire": "stitching",
        "gluing_stitching": "gluing_stitching",
        "glue_stitching": "gluing_stitching",
        "glue_wire": "gluing_stitching",
        "both": "gluing_stitching",
        "tape": "tape",
        "taping": "tape",
    }
    return aliases.get(text, text if text in {"none", "gluing", "stitching", "gluing_stitching", "tape"} else "none")


def _locking_from_joining_type(joining_type: str, wire_type: Any = None):
    method_by_joining = {
        "gluing": "glue",
        "stitching": "wire",
        "gluing_stitching": "both",
        "tape": "tape",
    }
    method = method_by_joining.get(joining_type)
    if not method:
        return None
    code = joining_type
    wire_type_id = None
    wire_product_id = None
    wire_product_name = ""
    wire_stitches_per_cm = 0.0
    wire_g_per_stitch = 0.0
    if wire_type:
        wire_type_id = _record_id(wire_type)
        code = (_str_from_attr(wire_type, "code") or _str_from_attr(wire_type, "name") or joining_type).strip()
        wire_stitches_per_cm = _float_from_attr(wire_type, "stitches_per_cm")
        wire_g_per_stitch = _float_from_attr(wire_type, "g_per_stitch")
        product = getattr(wire_type, "product_id", None)
        wire_product_id = _record_id(product)
        wire_product_name = (
            _str_from_attr(product, "display_name")
            or _str_from_attr(product, "name")
        )
    return SimpleNamespace(
        code=code,
        method=method,
        wire_type_id=wire_type_id,
        wire_product_id=wire_product_id,
        wire_product_name=wire_product_name,
        wire_stitches_per_cm=wire_stitches_per_cm,
        wire_g_per_stitch=wire_g_per_stitch,
    )


def _is_v2_spec(spec: Any) -> bool:
    """Heuristically detect the new Engine V2 spec model.

    We avoid importing Odoo models and instead rely on duck-typed
    attributes that are unique to corr.engine.v2.spec.
    """

    try:
        model_name = getattr(spec, "_name", "") or ""
    except Exception:
        model_name = ""
    if model_name == "corr.engine.v2.spec":
        return True

    # Fallback: presence of V2-only fields.
    if hasattr(spec, "box_length_mm") or hasattr(spec, "board_recipe_id"):
        return True

    return False


def _iter_stack_lines(spec: Any) -> List[Any]:
    """Return stack lines ordered by (sequence, id) from a spec-like object.

    Works with both:
    - real Odoo recordsets (which provide ``sorted``), and
    - simple Python iterables used in unit tests.
    """

    lines_raw = getattr(spec, "stack_line_ids", None)
    if not lines_raw:
        return []

    # Odoo recordset path: use its sorted() helper.
    try:
        sorter = getattr(lines_raw, "sorted", None)
        if callable(sorter):
            return list(
                sorter(lambda line: (getattr(line, "sequence", 0), getattr(line, "id", 0)))
            )
    except Exception:
        pass

    # Fallback: treat as a generic iterable.
    try:
        return sorted(
            list(lines_raw),
            key=lambda line: (getattr(line, "sequence", 0), getattr(line, "id", 0)),
        )
    except Exception:
        try:
            return list(lines_raw)
        except Exception:
            return []


def _iter_multi_piece_lines(spec: Any) -> List[Any]:
    """Return persistent multi-piece lines ordered by (sequence, id)."""

    lines_raw = getattr(spec, "multi_piece_line_ids", None)
    if not lines_raw:
        return []

    try:
        sorter = getattr(lines_raw, "sorted", None)
        if callable(sorter):
            return list(
                sorter(lambda line: (getattr(line, "sequence", 0), getattr(line, "id", 0)))
            )
    except Exception:
        pass

    try:
        return sorted(
            list(lines_raw),
            key=lambda line: (getattr(line, "sequence", 0), getattr(line, "id", 0)),
        )
    except Exception:
        try:
            return list(lines_raw)
        except Exception:
            return []


def _build_piece_plan_from_v2_spec(spec: Any) -> PiecePlanInput | None:
    if not bool(getattr(spec, "multi_piece_enabled", False)):
        return None
    if (
        bool(getattr(spec, "multi_piece_split_stale", False))
        and (_str_from_attr(spec, "multi_piece_source") == "accepted_auto_split")
    ):
        return None

    pieces: List[PieceSpecInput] = []
    for index, line in enumerate(_iter_multi_piece_lines(spec), start=1):
        length = _float_from_attr(line, "piece_length_mm")
        width = _float_from_attr(line, "piece_width_mm")
        if length <= 0.0 or width <= 0.0:
            continue
        code = _engine_piece_key(_str_from_attr(line, "piece_code"), index)
        board_recipe = getattr(line, "board_recipe_id", None)
        flute_profile = getattr(line, "flute_profile_id", None)
        surface_profile = getattr(line, "surface_profile_id", None)
        top_sheet_product = getattr(line, "top_sheet_product_id", None)
        pieces.append(
            PieceSpecInput(
                piece_key=code,
                piece_name=_str_from_attr(line, "piece_name") or code,
                piece_index=index,
                piece_qty_per_fg=_float_from_attr(line, "piece_qty_per_fg") or 1.0,
                piece_width_mm=width,
                piece_length_mm=length,
                split_axis=_str_from_attr(spec, "multi_piece_split_axis") or "width",
                stack_source=_piece_source_from_attr(line, "stack_source"),
                board_recipe_key=(
                    str(getattr(board_recipe, "id", "") or "")
                    if board_recipe
                    else ""
                ),
                flute_profile_key=(
                    _str_from_attr(flute_profile, "code")
                    or str(getattr(flute_profile, "id", "") or "")
                    if flute_profile
                    else ""
                ),
                surface_source=_piece_source_from_attr(line, "surface_source"),
                surface_profile_key=(
                    str(getattr(surface_profile, "id", "") or "")
                    if surface_profile
                    else ""
                ),
                top_sheet_source=_piece_source_from_attr(line, "top_sheet_source"),
                top_sheet_mode=_str_from_attr(line, "top_sheet_mode"),
                top_sheet_product_key=(
                    str(getattr(top_sheet_product, "id", "") or "")
                    if top_sheet_product
                    else ""
                ),
                merge_stage_key=_str_from_attr(spec, "multi_piece_merge_stage_key") or "seaming",
            )
        )

    split_axis = _str_from_attr(spec, "multi_piece_split_axis") or "width"
    source_length = _float_from_attr(spec, "multi_piece_source_length_mm")
    source_width = _float_from_attr(spec, "multi_piece_source_width_mm")
    if (source_length <= 0.0 or source_width <= 0.0) and pieces:
        derived_length, derived_width = _derive_source_dimensions_from_pieces(
            pieces, split_axis=split_axis
        )
        if source_length <= 0.0:
            source_length = derived_length
        if source_width <= 0.0:
            source_width = derived_width

    return PiecePlanInput(
        enabled=True,
        source=_str_from_attr(spec, "multi_piece_source") or "explicit_multi_piece_design",
        status="accepted",
        is_design_intent=(
            (_str_from_attr(spec, "multi_piece_source") or "explicit_multi_piece_design")
            == "explicit_multi_piece_design"
        ),
        materialization_allowed=False,
        piece_count=len(pieces) or 2,
        split_axis=split_axis,
        split_mode=_str_from_attr(spec, "multi_piece_split_mode") or "equal",
        split_method=_str_from_attr(spec, "multi_piece_split_mode") or "equal",
        multi_piece_strategy=(
            _str_from_attr(spec, "multi_piece_strategy") or "independent_routed_piece"
        ),
        split_after_stage_key=_str_from_attr(spec, "multi_piece_split_after_stage_key"),
        source_width_mm=source_width,
        source_length_mm=source_length,
        merge_stage_key=_str_from_attr(spec, "multi_piece_merge_stage_key") or "seaming",
        pieces=pieces,
    )


def _engine_piece_key(code: str, index: int) -> str:
    normalized = (code or "").strip().lower()
    if normalized in ("a", "piece a", "piece_a"):
        return "piece_a"
    if normalized in ("b", "piece b", "piece_b"):
        return "piece_b"
    return normalized or "piece_%s" % index


def _derive_source_dimensions_from_pieces(
    pieces: List[PieceSpecInput], *, split_axis: str
) -> Tuple[float, float]:
    if not pieces:
        return 0.0, 0.0

    if (split_axis or "").strip().lower() == "length":
        return (
            sum(float(piece.piece_length_mm or 0.0) for piece in pieces),
            max(float(piece.piece_width_mm or 0.0) for piece in pieces),
        )
    return (
        max(float(piece.piece_length_mm or 0.0) for piece in pieces),
        sum(float(piece.piece_width_mm or 0.0) for piece in pieces),
    )


def _build_board_layers_from_stack(spec: Any) -> List[Dict[str, object]]:
    """Legacy board_layers builder for corr.spec.template-style stack lines.

    Each element is a dict understood by engine_v2.board_structure:
    ``{"type": "liner"|"medium", "gsm": float, "layer_no": int}``.

    For Engine V2 specs (corr.engine.v2.spec) we use a separate mapping
    that preserves richer role semantics; this helper is intentionally
    limited to legacy roles ("liner", "medium").
    """

    ordered_lines = _iter_stack_lines(spec)
    layers: List[Dict[str, object]] = []
    next_layer_no = 1

    for line in ordered_lines:
        role_raw = getattr(line, "role", "") or ""
        try:
            role = str(role_raw).strip().lower()
        except Exception:
            role = ""
        if role not in ("liner", "medium"):
            continue
        gsm = _float_from_attr(line, "gsm")
        if gsm <= 0.0:
            continue
        flute = getattr(line, "flute_id", None)
        flute_code = _str_from_attr(flute, "code") if flute else ""
        flute_tuf = _float_from_attr(flute, "tuf") if flute else 0.0
        layers.append(
            {
                "type": role,
                "gsm": gsm,
                "layer_no": next_layer_no,
                "flute_code": flute_code,
                "flute_tuf": flute_tuf,
            }
        )
        next_layer_no += 1

    return layers


def _aggregate_gsm_from_layers(
    layers: Iterable[Dict[str, object]],
    spec: Any,
) -> Tuple[float, float]:
    """Aggregate liner and medium GSM from board_layers.

    For compatibility with existing metrics logic, this function returns:
    - liner_gsm: sum of GSM for all liner layers
    - medium_gsm: sum of GSM for all medium layers

    When no valid layers are present, falls back to legacy attributes on spec
    (liner_gsm / medium_gsm) if they exist.
    """

    liner_gsm = 0.0
    medium_gsm = 0.0

    for layer in layers:
        type_raw = layer.get("type")
        try:
            layer_type = str(type_raw).strip().lower()
        except Exception:
            layer_type = ""
        try:
            gsm_val = float(layer.get("gsm") or 0.0)
        except Exception:
            gsm_val = 0.0
        if gsm_val <= 0.0:
            continue
        if layer_type == "liner":
            liner_gsm += gsm_val
        elif layer_type == "medium":
            medium_gsm += gsm_val

    if liner_gsm <= 0.0 and medium_gsm <= 0.0:
        liner_gsm = _float_from_attr(spec, "liner_gsm")
        medium_gsm = _float_from_attr(spec, "medium_gsm")

    return liner_gsm, medium_gsm


def _get_extra_params(spec: Any) -> Dict[str, object]:
    """Return extra_params_json as a dict, parsing JSON strings when needed."""

    raw = getattr(spec, "extra_params_json", None) or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw.strip() or "{}")
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def _style_param_value_from_line(line: Any) -> Tuple[str, object]:
    key = _str_from_attr(line, "key")
    if not key:
        param = getattr(line, "param_id", None)
        key = _str_from_attr(param, "key") if param else ""
    if not key:
        return "", None

    param = getattr(line, "param_id", None)
    param_type = _str_from_attr(param, "param_type") if param else _str_from_attr(line, "param_type")
    param_type = param_type or "text"
    if param_type == "float":
        return key, _float_from_attr(line, "value_float")
    if param_type == "integer":
        return key, int(_float_from_attr(line, "value_int"))
    if param_type == "boolean":
        try:
            return key, bool(getattr(line, "value_bool", False))
        except Exception:
            return key, False
    if param_type == "selection":
        return key, _str_from_attr(line, "value_selection")
    return key, _str_from_attr(line, "value_char")


def _collect_style_params(spec: Any) -> Dict[str, object]:
    collector = getattr(spec, "collect_style_params", None)
    if callable(collector):
        params = collector()
        return dict(params or {})

    try:
        lines = getattr(spec, "style_param_value_ids", []) or []
    except Exception:
        lines = []

    params: Dict[str, object] = {}
    for line in list(lines or []):
        try:
            active = getattr(line, "active", True)
        except Exception:
            active = True
        if not active:
            continue
        key, value = _style_param_value_from_line(line)
        if key:
            params[key] = value
    try:
        fg_per_sheet = getattr(spec, "fg_per_sheet", None)
    except Exception:
        fg_per_sheet = None
    if fg_per_sheet not in (None, ""):
        try:
            params["fg_per_sheet"] = int(fg_per_sheet or 1)
        except Exception:
            params["fg_per_sheet"] = fg_per_sheet
    return params
    return raw


def _compute_top_sheet_gsm(spec: Any) -> float:
    """Compute top_sheet_gsm from spec.extra_params_json when present.

    The current engine slice only needs a single GSM value representing
    the printed top sheet (whether sourced from a roll or external sheet).
    """

    params = _get_extra_params(spec)
    if not params:
        return 0.0

    raw_gsm = params.get("top_sheet_gsm")
    if raw_gsm in (None, ""):
        return 0.0

    try:
        value = raw_gsm
        if isinstance(value, str):
            value = value.replace(",", ".").strip()
        gsm = float(value or 0.0)
    except Exception:
        return 0.0

    return max(0.0, gsm)


def _record_id(record: Any) -> int | None:
    try:
        value = int(getattr(record, "id", 0) or 0)
    except Exception:
        value = 0
    return value or None


def _attr_with_fallback(primary: Any, primary_name: str, fallback: Any, fallback_name: str, default: Any = None) -> Any:
    try:
        if hasattr(primary, primary_name):
            return getattr(primary, primary_name)
    except Exception:
        pass
    try:
        if fallback is not None and hasattr(fallback, fallback_name):
            return getattr(fallback, fallback_name)
    except Exception:
        pass
    return default


def _nonempty_attr_with_fallback(primary: Any, primary_name: str, fallback: Any, fallback_name: str, default: Any = None) -> Any:
    value = _attr_with_fallback(primary, primary_name, None, "", None)
    if value not in (None, ""):
        return value
    return _attr_with_fallback(None, "", fallback, fallback_name, default)


def _build_engine_spec_input_from_legacy(
    spec: Any, sfg_mode: str | None = None
) -> EngineSpecInput:
    """Adapter from corr.spec.template-style records to EngineSpecInput.

    This preserves the existing behaviour used by the current debug
    wizard and BoM flows.
    """

    style_code = _str_from_attr(spec, "style")
    spec_id = int(_float_from_attr(spec, "id"))
    spec_name = _str_from_attr(spec, "name")

    # Blank dimensions: prefer explicit blank_* fields when present,
    # otherwise fall back to spec length/width in mm.
    blank_length_mm = _float_from_attr(spec, "blank_length_mm")
    blank_width_mm = _float_from_attr(spec, "blank_width_mm")
    length_mm = _float_from_attr(spec, "length_mm")
    width_mm = _float_from_attr(spec, "width_mm")
    height_mm = _float_from_attr(spec, "height_mm")

    if blank_length_mm <= 0.0:
        blank_length_mm = length_mm
    if blank_width_mm <= 0.0:
        blank_width_mm = width_mm

    layers = _build_board_layers_from_stack(spec)
    liner_gsm, medium_gsm = _aggregate_gsm_from_layers(layers, spec)
    top_sheet_gsm = _compute_top_sheet_gsm(spec)

    if sfg_mode is not None:
        sfg_mode_value = str(sfg_mode)
    else:
        sfg_mode_value = _str_from_attr(spec, "sfg_mode") or "staged"

    spec_input = EngineSpecInput(
        blank_length_mm=blank_length_mm,
        blank_width_mm=blank_width_mm,
        liner_gsm=liner_gsm,
        medium_gsm=medium_gsm,
        style_code=style_code,
        sfg_mode=sfg_mode_value,
        spec_id=spec_id,
        spec_name=spec_name,
    )

    # Attach additional attributes used by certain style / RM rules.
    spec_input.board_layers = layers
    spec_input.top_sheet_gsm = top_sheet_gsm
    spec_input.box_length_mm = length_mm
    spec_input.box_width_mm = width_mm
    spec_input.box_height_mm = height_mm
    style_params = _collect_style_params(spec)
    if style_params:
        spec_input.style_params = style_params

    # Printing UPS hint: sourced from corr.spec.template.extra_params_json
    # (key "printing_ups") when present and positive. This is treated as a
    # spec-level hint for Engine V2 UPS resolution and derived sheet sizing.
    try:
        params = _get_extra_params(spec)
        raw_pu = params.get("printing_ups")
        if raw_pu not in (None, ""):
            pu_val = int(float(raw_pu or 0))
            if pu_val > 0:
                spec_input.printing_ups = pu_val
    except Exception:
        # Hint is optional; failures must not break the adapter.
        pass

    # Surface / top-sheet / lamination mapping for legacy specs.
    # The spec is the single source of truth; any optional Surface
    # Profile is only used to pre-fill these fields on the spec.
    try:
        xp = _get_extra_params(spec)
    except Exception:
        xp = {}

    # Normalise top-layer mode similar to the legacy engine: values in
    # extra_params_json or the UI helper (top_layer_mode_ui). This is
    # used to decide when unit-sheet semantics should apply, and to
    # distinguish roll-mode top-sheet behaviour.
    mode_val = xp.get("top_layer_mode") or xp.get("top_layer") or "Roll"
    try:
        mode_norm = str(mode_val).strip().lower()
    except Exception:
        mode_norm = "roll"
    use_unit_top = mode_norm in ("unit", "units", "sheet", "printed", "printed_sheet")
    if not use_unit_top:
        try:
            ui = getattr(spec, "top_layer_mode_ui", None)
            if ui and str(ui).strip().lower() == "unit":
                use_unit_top = True
        except Exception:
            use_unit_top = use_unit_top
    if not use_unit_top:
        try:
            if getattr(spec, "top_sheet_product_id", False):
                use_unit_top = True
        except Exception:
            use_unit_top = use_unit_top

    surface_input = None

    if use_unit_top:
        # For Unit-top specs, build a SurfaceProfileInput from the spec
        # fields so that Engine V2 can apply unit_sheet semantics while
        # remaining independent of any Surface Profile model.
        try:
            ts_prod = getattr(spec, "top_sheet_product_id", None)
        except Exception:
            ts_prod = None
        try:
            top_sheet_pid = int(getattr(ts_prod, "id", 0) or 0)
        except Exception:
            top_sheet_pid = None
        if not top_sheet_pid:
            try:
                pid_json = xp.get("top_sheet_product_id")
                if pid_json not in (None, ""):
                    top_sheet_pid = int(pid_json)
            except Exception:
                top_sheet_pid = None

        try:
            lam = getattr(spec, "lamination_type_id", None)
        except Exception:
            lam = None
        try:
            lam_id = int(getattr(lam, "id", 0) or 0)
        except Exception:
            lam_id = None

        try:
            printing_required = bool(getattr(spec, "printing_required", False))
        except Exception:
            printing_required = False

        surface_input = SurfaceProfileInput(
            printing_required=printing_required,
            print_process="",
            print_colors=int(_float_from_attr(spec, "print_colors")),
            print_coverage_percent=_float_from_attr(spec, "coverage_pct"),
            print_side="",
            top_sheet_mode="unit_sheet",
            top_sheet_product_id=top_sheet_pid,
            lamination_required=bool(lam_id),
            lamination_type_id=lam_id,
            lamination_side="",
        )
        spec_input.surface_profile = surface_input
    else:
        # For roll-mode specs with printing enabled, build a minimal
        # SurfaceProfileInput from spec fields so that Engine V2 can
        # apply roll semantics based on board stack layers instead of
        # relying solely on a scalar top_sheet_gsm value. This keeps
        # legacy specs independent of any Surface Profile template while
        # enabling consistent top-sheet handling in rm_rules.
        try:
            printing_required = bool(getattr(spec, "printing_required", False))
        except Exception:
            printing_required = False

        if printing_required:
            try:
                lam = getattr(spec, "lamination_type_id", None)
            except Exception:
                lam = None
            try:
                lam_id = int(getattr(lam, "id", 0) or 0)
            except Exception:
                lam_id = None

            surface_input = SurfaceProfileInput(
                printing_required=printing_required,
                print_process="",
                print_colors=int(_float_from_attr(spec, "print_colors")),
                print_coverage_percent=_float_from_attr(spec, "coverage_pct"),
                print_side="",
                top_sheet_mode="roll",
                top_sheet_product_id=None,
                lamination_required=bool(lam_id),
                lamination_type_id=lam_id,
                lamination_side="",
            )
            spec_input.surface_profile = surface_input

    # Optional glue flap (used by style 0201) when provided on the spec.
    try:
        glue_flap_mm = _float_from_attr(spec, "glue_flap_mm")
        if glue_flap_mm > 0.0:
            spec_input.glue_flap_mm = glue_flap_mm
    except Exception:
        pass

    # Optional sheet size from spec. Layout will fall back to these when
    # the plant profile does not define sheet dimensions.
    try:
        sheet_length_mm = _float_from_attr(spec, "sheet_length_mm")
        sheet_width_mm = _float_from_attr(spec, "sheet_width_mm")
        if sheet_length_mm > 0.0:
            spec_input.sheet_length_mm = sheet_length_mm
        if sheet_width_mm > 0.0:
            spec_input.sheet_width_mm = sheet_width_mm
    except Exception:
        pass

    return spec_input


def _iter_routing_stages(routing_profile: Any) -> List[Any]:
    """Return routing stages ordered by (sequence, id) for a routing profile."""

    try:
        stages_raw = getattr(routing_profile, "stage_ids", None)
    except Exception:
        stages_raw = None
    if not stages_raw:
        return []

    try:
        sorter = getattr(stages_raw, "sorted", None)
        if callable(sorter):
            return list(
                sorter(lambda st: (getattr(st, "sequence", 0), getattr(st, "id", 0)))
            )
    except Exception:
        pass

    try:
        return sorted(
            list(stages_raw),
            key=lambda st: (getattr(st, "sequence", 0), getattr(st, "id", 0)),
        )
    except Exception:
        try:
            return list(stages_raw)
        except Exception:
            return []


def _build_engine_spec_input_from_v2_spec(
    spec: Any, sfg_mode: str | None = None
) -> EngineSpecInput:
    """Adapter from corr.engine.v2.spec-style records to EngineSpecInput.

    Responsibilities in this phase:
    - map geometry using V2 field names (style_id, box_*),
    - build board layers from corr.engine.v2.spec.layer lines,
    - capture surface/layout/routing profiles as typed inputs,
    - carry spec-level overrides (manual sheet, UPS),
    - maintain compatibility with the existing engine by still
      populating legacy scalar fields (blank_length_mm, liner_gsm, ...).
    """

    # Identity
    spec_id = int(_float_from_attr(spec, "id"))
    spec_name = _str_from_attr(spec, "name")

    # Style / geometry
    style_rec = getattr(spec, "style_id", None)
    if style_rec:
        style_code = _str_from_attr(style_rec, "code") or _str_from_attr(style_rec, "name")
        try:
            style_id = int(getattr(style_rec, "id", 0) or 0)
        except Exception:
            style_id = None
    else:
        style_code = ""
        style_id = None

    box_length_mm = _float_from_attr(spec, "box_length_mm")
    box_width_mm = _float_from_attr(spec, "box_width_mm")
    box_height_mm = _float_from_attr(spec, "box_height_mm")
    glue_flap_override_mm = _float_from_attr(spec, "glue_flap_override_mm")

    plant_rec = getattr(spec, "plant_id", None)
    if plant_rec:
        plant_code = _str_from_attr(plant_rec, "code")
        try:
            plant_id = int(getattr(plant_rec, "id", 0) or 0)
        except Exception:
            plant_id = None
    else:
        plant_code = ""
        plant_id = None

    geometry = GeometrySpec(
        style_id=style_id,
        box_length_mm=box_length_mm,
        box_width_mm=box_width_mm,
        box_height_mm=box_height_mm,
        glue_flap_override_mm=glue_flap_override_mm if glue_flap_override_mm > 0.0 else 0.0,
        plant_code=plant_code,
        plant_id=plant_id,
    )

    # Board layers from spec.stack_line_ids are the source of truth.
    ordered_lines = _iter_stack_lines(spec)
    simple_layers: List[Dict[str, object]] = []
    board_layers_v2: List[BoardLayerInput] = []
    next_layer_no = 1

    for line in ordered_lines:
        role_raw = getattr(line, "role", "") or ""
        try:
            role = str(role_raw).strip().lower()
        except Exception:
            role = ""

        gsm = _float_from_attr(line, "gsm")
        if gsm <= 0.0:
            continue

        # Map rich roles onto the simple liner/medium types used by the
        # current engine metrics/board structure slice.
        if role == "medium":
            layer_type = "medium"
        else:
            # outer_liner, inner_liner, center_liner, other -> liner bucket
            layer_type = "liner"

        bf_val = _float_from_attr(line, "bf")
        color = _str_from_attr(line, "color")

        flute = getattr(line, "flute_type_id", None)
        flute_code = _str_from_attr(flute, "code") if flute else ""
        flute_tuf = _float_from_attr(flute, "tuf") if flute else 0.0

        simple_layers.append(
            {
                "type": layer_type,
                "gsm": gsm,
                "layer_no": next_layer_no,
                "flute_code": flute_code,
                "flute_tuf": flute_tuf,
            }
        )

        material = getattr(line, "material_product_id", None)
        # Prefer default_code, fall back to name as a generic identifier.
        material_code = ""
        material_product_id = getattr(material, "id", None) if material else None
        material_product_name = ""
        material_product_uom_id = None
        material_product_uom_name = ""
        if material:
            material_code = _str_from_attr(material, "default_code") or _str_from_attr(
                material, "name"
            )
            material_product_name = _str_from_attr(material, "display_name") or _str_from_attr(material, "name")
            material_uom = getattr(material, "uom_id", None)
            material_product_uom_id = getattr(material_uom, "id", None) if material_uom else None
            material_product_uom_name = _str_from_attr(material_uom, "display_name") or _str_from_attr(material_uom, "name")
        material_template = getattr(line, "material_template_id", None)
        material_template_id = getattr(material_template, "id", None) if material_template else None
        material_template_name = _str_from_attr(material_template, "name") if material_template else ""

        source_type = _str_from_attr(line, "source_type")

        board_layers_v2.append(
            BoardLayerInput(
                sequence=int(getattr(line, "sequence", next_layer_no) or next_layer_no),
                role=role,
                gsm=gsm,
                bf=bf_val,
                color=color,
                flute_type_code=flute_code,
                flute_tuf=flute_tuf,
                material_code=material_code,
                material_product_id=material_product_id,
                material_product_name=material_product_name,
                material_product_uom_id=material_product_uom_id,
                material_product_uom_name=material_product_uom_name,
                material_template_id=material_template_id,
                material_template_name=material_template_name,
                gsm_value_id=getattr(getattr(line, "gsm_value_id", None), "id", None),
                bf_value_id=getattr(getattr(line, "bf_value_id", None), "id", None),
                color_value_id=getattr(getattr(line, "color_value_id", None), "id", None),
                source_type=source_type,
            )
        )
        next_layer_no += 1

    liner_gsm, medium_gsm = _aggregate_gsm_from_layers(simple_layers, spec)

    # Top-sheet GSM is sourced from extra_params_json when present so
    # that V2 specs can participate in legacy/top-sheet GSM rules. For
    # unit-sheet mode, product-derived GSM (when available) is attached
    # to the SurfaceProfileInput and treated as authoritative by RM
    # rules, with this scalar used only as a fallback.
    top_sheet_gsm = _compute_top_sheet_gsm(spec)

    # SFG mode: default to "staged" for Engine V2.
    if sfg_mode is not None:
        sfg_mode_value = str(sfg_mode)
    else:
        sfg_mode_value = _str_from_attr(spec, "sfg_mode") or "staged"

    # For V2 specs, blank dimensions may be computed by style rules from
    # box dimensions, so start at zero and let the style handler populate
    # them when needed.
    spec_input = EngineSpecInput(
        blank_length_mm=0.0,
        blank_width_mm=0.0,
        liner_gsm=liner_gsm,
        medium_gsm=medium_gsm,
        style_code=style_code,
        sfg_mode=sfg_mode_value,
        spec_id=spec_id,
        spec_name=spec_name,
    )

    # Attach geometry and board layers in both rich and legacy forms.
    spec_input.geometry = geometry
    spec_input.board_layers_v2 = board_layers_v2
    spec_input.board_layers = simple_layers
    joining_type = _normalize_joining_type(getattr(spec, "joining_type", "none"))
    spec_input.joining_type = joining_type
    spec_input.locking_type = _locking_from_joining_type(
        joining_type,
        getattr(spec, "stitching_wire_type_id", None),
    )

    # Also expose box dimensions and glue flap as direct attributes for
    # compatibility with existing style_0201 tests and helpers.
    spec_input.box_length_mm = box_length_mm
    spec_input.box_width_mm = box_width_mm
    spec_input.box_height_mm = box_height_mm
    style_params = _collect_style_params(spec)
    if style_params:
        spec_input.style_params = style_params
    if geometry.glue_flap_override_mm:
        # Legacy compatibility: engine styles currently expect a
        # glue_flap_mm attribute directly on the spec input. Keep this
        # alias while GeometrySpec uses the frozen V2 name.
        spec_input.glue_flap_mm = geometry.glue_flap_override_mm

    # Surface profile. For Engine V2 specs the profile is a template only;
    # preview must consume the spec-owned copied fields.
    surface = getattr(spec, "surface_profile_id", None)
    has_spec_surface_values = any(
        [
            getattr(spec, "surface_printing_required", False),
            _str_from_attr(spec, "surface_print_process"),
            _float_from_attr(spec, "surface_print_colors"),
            _float_from_attr(spec, "surface_print_coverage_percent"),
            _str_from_attr(spec, "surface_top_sheet_mode"),
            getattr(spec, "surface_top_sheet_product_id", None),
            getattr(spec, "surface_lamination_required", False),
            getattr(spec, "surface_lamination_type_id", None),
            _str_from_attr(spec, "surface_lamination_side"),
        ]
    )
    surface_input = None
    if surface or has_spec_surface_values:
        try:
            top_sheet_product = _attr_with_fallback(
                spec, "surface_top_sheet_product_id", surface, "top_sheet_product_id"
            )
        except Exception:
            top_sheet_product = None
        top_sheet_product_id = _record_id(top_sheet_product)

        try:
            lam_type = _attr_with_fallback(
                spec, "surface_lamination_type_id", surface, "lamination_type_id"
            )
        except Exception:
            lam_type = None
        lamination_type_id = _record_id(lam_type)

        surface_input = SurfaceProfileInput(
            printing_required=bool(
                _attr_with_fallback(spec, "surface_printing_required", surface, "printing_required", False)
            ),
            print_process=str(
                _attr_with_fallback(spec, "surface_print_process", surface, "print_process", "") or ""
            ),
            print_colors=int(
                _attr_with_fallback(spec, "surface_print_colors", surface, "print_colors", 0) or 0
            ),
            print_coverage_percent=_float_from_attr(
                type("SurfaceCoverage", (), {"value": _attr_with_fallback(
                    spec, "surface_print_coverage_percent", surface, "print_coverage_percent", 0.0
                )})(),
                "value",
            ),
            print_side=str(_attr_with_fallback(spec, "surface_print_side", surface, "print_side", "") or ""),
            top_sheet_mode=str(
                _nonempty_attr_with_fallback(spec, "surface_top_sheet_mode", surface, "top_sheet_mode", "none") or "none"
            ),
            top_sheet_product_id=top_sheet_product_id,
            lamination_required=bool(
                _attr_with_fallback(spec, "surface_lamination_required", surface, "lamination_required", False)
            ),
            lamination_type_id=lamination_type_id,
            lamination_side=str(
                _attr_with_fallback(spec, "surface_lamination_side", surface, "lamination_side", "") or ""
            ),
        )
        # Attach scalar GSM fallback on the spec input; unit-sheet
        # product-derived GSM (when present) is carried on the surface
        # profile and preferred by RM rules.
    spec_input.surface_profile = surface_input

    # Layout policy
    layout_policy_rec = getattr(spec, "layout_policy_id", None)
    layout_policy_input = None
    if layout_policy_rec:
        layout_policy_input = LayoutPolicyInput(
            allow_rotation=bool(
                getattr(layout_policy_rec, "allow_rotation", True)
            ),
            preferred_ups=int(
                _float_from_attr(layout_policy_rec, "preferred_ups")
            ),
            max_ups=int(_float_from_attr(layout_policy_rec, "max_ups")),
            layout_mode=_str_from_attr(layout_policy_rec, "layout_mode")
            or "optimize",
            sheet_source=_str_from_attr(layout_policy_rec, "sheet_source")
            or "plant_default",
            trim_margin_mm=_float_from_attr(layout_policy_rec, "trim_margin_mm"),
            gripper_margin_mm=_float_from_attr(
                layout_policy_rec, "gripper_margin_mm"
            ),
        )
    spec_input.layout_policy = layout_policy_input

    # Routing profile
    routing_rec = getattr(spec, "routing_profile_id", None)
    routing_profile_input = None
    if routing_rec:
        stage_inputs: List[RoutingStageInput] = []
        for st in _iter_routing_stages(routing_rec):
            tmpl = getattr(st, "stage_template_id", None)
            # Prefer stage_template_id.code as the authoritative stage code;
            # fall back to the legacy stage_code field when no template is linked.
            if tmpl:
                code_val = getattr(tmpl, "code", "") or ""
                exec_type_src = getattr(tmpl, "execution_type", "") or ""
                stage_type_src = getattr(tmpl, "stage_type", "") or ""
                output_kind_src = getattr(tmpl, "output_kind", "") or ""
                can_be_terminal = bool(getattr(tmpl, "can_be_terminal", False))
                is_fg_stage = bool(getattr(tmpl, "is_fg_stage", False))
            else:
                code_val = _str_from_attr(st, "stage_code")
                exec_type_src = _str_from_attr(st, "execution_type")
                stage_type_src = _str_from_attr(st, "stage_type")
                output_kind_src = _str_from_attr(st, "output_kind")
                can_be_terminal = bool(getattr(st, "can_be_terminal", False))
                is_fg_stage = bool(getattr(st, "is_fg_stage", False))
            try:
                code_str = str(code_val or "").strip()
            except Exception:
                code_str = ""
            # Prefer explicit execution_type when available; otherwise
            # map stage semantics onto material/operation semantics.
            exec_type_norm = (exec_type_src or "").strip().lower()
            stype_norm = (stage_type_src or "").strip().lower()
            if exec_type_norm in ("material", "operation"):
                stype_resolved = exec_type_norm
            elif stype_norm in ("corrugation", "conversion", "fg"):
                stype_resolved = "material"
            elif stype_norm in ("printing", "lamination", "diecut", "auxiliary"):
                stype_resolved = "operation"
            else:
                stype_resolved = _str_from_attr(st, "stage_type") or "operation"
            stage_inputs.append(
                RoutingStageInput(
                    sequence=int(getattr(st, "sequence", 0) or 0),
                    stage_code=code_str,
                    stage_type=stype_resolved,
                    stage_semantic=stype_norm,
                    output_kind=_str_from_attr(tmpl, "output_kind") if tmpl else output_kind_src,
                    can_be_terminal=can_be_terminal,
                    is_fg_stage=is_fg_stage,
                    waste_percent=_float_from_attr(st, "waste_percent"),
                )
            )
        routing_profile_input = RoutingProfileInput(
            name=_str_from_attr(routing_rec, "name"),
            stages=stage_inputs,
        )
    spec_input.routing_profile = routing_profile_input

    layout_policy = getattr(spec, "layout_policy_id", False)
    use_manual_sheet = bool(
        layout_policy and (_str_from_attr(layout_policy, "sheet_source") == "manual")
    )

    # Overrides (sheet + UPS). Manual sheet dimensions are only an
    # override when the selected layout policy explicitly asks for them.
    manual_sheet_length_mm = (
        _float_from_attr(spec, "manual_sheet_length_mm") if use_manual_sheet else 0.0
    )
    manual_sheet_width_mm = (
        _float_from_attr(spec, "manual_sheet_width_mm") if use_manual_sheet else 0.0
    )
    overrides = EngineOverrides(
        manual_sheet_length_mm=manual_sheet_length_mm,
        manual_sheet_width_mm=manual_sheet_width_mm,
        forced_ups=int(_float_from_attr(spec, "forced_ups"))
        if _float_from_attr(spec, "forced_ups")
        else None,
        ups_enforcement=_str_from_attr(spec, "ups_enforcement"),
    )
    spec_input.overrides = overrides

    # Expose manual sheet dimensions as the legacy sheet_* attributes
    # so that the current layout slice can already benefit from them.
    try:
        if overrides.manual_sheet_length_mm > 0.0:
            spec_input.sheet_length_mm = overrides.manual_sheet_length_mm
        if overrides.manual_sheet_width_mm > 0.0:
            spec_input.sheet_width_mm = overrides.manual_sheet_width_mm
    except Exception:
        # These are optional convenience attributes; failure to set them
        # must not break the adapter.
        pass

    # Top-sheet GSM fallback for V2 specs; RM rules will prefer any
    # product-derived GSM attached to the surface profile for
    # unit-sheet mode and fall back to this scalar when needed.
    spec_input.top_sheet_gsm = top_sheet_gsm

    # Persistent Phase D multi-piece definition. This is preview-only:
    # materialization stays disabled until the later SFG/BoM phase.
    spec_input.piece_plan = _build_piece_plan_from_v2_spec(spec)

    return spec_input


def build_engine_spec_input(spec: Any, sfg_mode: str | None = None) -> EngineSpecInput:
    """Entry point for building EngineSpecInput from a spec-like object.

    - Legacy specs (corr.spec.template-style) continue to use the
      original mapping.
    - Engine V2 specs (corr.engine.v2.spec) are mapped using the new
      typed domain structures while still populating legacy scalar
      fields for compatibility.
    """

    if _is_v2_spec(spec):
        return _build_engine_spec_input_from_v2_spec(spec, sfg_mode=sfg_mode)
    return _build_engine_spec_input_from_legacy(spec, sfg_mode=sfg_mode)



