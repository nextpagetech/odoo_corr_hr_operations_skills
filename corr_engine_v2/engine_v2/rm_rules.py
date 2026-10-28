from __future__ import annotations

from typing import List, Set, Optional

from .spec_input import EngineSpecInput
from .plant_profiles import PlantProfile
from .board_structure import resolve_board_structure, BoardStructure
from .consumables import effective_consumable_state, is_consumable_enabled
from . import EngineMetrics, StageGraph, RmRequirement


def _select_material_stage(stage_keys: Set[str], preferred: str) -> Optional[str]:
    """Select a material stage key with simple precedence rules.

    Business rule (canonical chain):
    - Corrugation-like usage prefers '2ply', then 'board', then 'fg'.
    - Board-like usage prefers 'board', then 'fg', then '2ply'.
    - FG-like usage prefers 'fg', then 'board', then '2ply'.
    - When none of the canonical stages are present, fall back to the
      last material stage so RMs are never silently dropped.
    """

    canonical_order = {
        "2ply": ("2ply", "board", "fg"),
        "board": ("board", "fg", "2ply"),
        "fg": ("fg", "board", "2ply"),
    }
    candidates = canonical_order.get(preferred, (preferred,))
    for cand in candidates:
        if cand in stage_keys:
            return cand
    if stage_keys:
        return sorted(stage_keys)[-1]
    return None


def _find_stage_for_operation_in_graph(
    stage_graph: StageGraph, op_codes: List[str]
) -> Optional[str]:
    """Return the first material stage that owns any of the given operations.

    Operations are matched against Stage.operations using lowercase
    comparison. When no explicit owner is found, callers are expected
    to fall back to _select_material_stage().
    """

    if not stage_graph or not getattr(stage_graph, "stages", None):
        return None
    wanted = {str(c or "").strip().lower() for c in op_codes if c}
    if not wanted:
        return None
    for st in getattr(stage_graph, "stages", []) or []:
        try:
            ops = getattr(st, "operations", []) or []
        except Exception:
            ops = []
        ops_norm = {str(o or "").strip().lower() for o in ops}
        if ops_norm & wanted:
            return getattr(st, "key", None)
    return None


def _find_stage_by_tokens_in_graph(
    stage_graph: StageGraph,
    tokens: Set[str],
) -> Optional[str]:
    wanted = {str(token or "").strip().lower() for token in tokens if token}
    if not wanted or not stage_graph or not getattr(stage_graph, "stages", None):
        return None

    def _matches(stage: object, include_operations: bool = False) -> bool:
        values = [
            getattr(stage, "key", None),
            getattr(stage, "name", None),
            getattr(stage, "logical_role", None),
            getattr(stage, "output_kind", None),
        ]
        if include_operations:
            try:
                values.extend(list(getattr(stage, "operations", []) or []))
            except Exception:
                pass
        text = " ".join(str(value or "").strip().lower() for value in values)
        normalized = text.replace("-", "_").replace(" ", "_")
        return any(token in normalized for token in wanted)

    stages = list(getattr(stage_graph, "stages", []) or [])
    # Prefer the real route stage (key/name/logical role). Attached operation
    # aliases such as cutting.operations=["ts_print"] are a fallback only.
    for stage in stages:
        if _matches(stage, include_operations=False):
            return getattr(stage, "key", None)
    for stage in stages:
        if _matches(stage, include_operations=True):
            return getattr(stage, "key", None)
    return None


def _find_material_stage_by_tokens(
    stage_graph: StageGraph,
    tokens: Set[str],
) -> Optional[str]:
    wanted = {str(token or "").strip().lower() for token in tokens if token}
    if not wanted:
        return None
    for stage in _material_stages_in_order(stage_graph):
        values = [
            getattr(stage, "key", None),
            getattr(stage, "name", None),
            getattr(stage, "logical_role", None),
        ]
        text = " ".join(str(value or "").strip().lower() for value in values)
        normalized = text.replace("-", "_").replace(" ", "_")
        if any(token in normalized for token in wanted):
            return getattr(stage, "key", None)
    return None


def _normalize_stage_semantic(value: object) -> str:
    try:
        text = str(value or "").strip().lower()
    except Exception:
        text = ""
    if text == "finishing":
        return "fg"
    return text


def _material_stages_in_order(stage_graph: StageGraph) -> List[object]:
    if not stage_graph or not getattr(stage_graph, "stages", None):
        return []
    stages: List[object] = []
    for stage in getattr(stage_graph, "stages", []) or []:
        try:
            if str(getattr(stage, "stage_type", "") or "").strip().lower() != "material":
                continue
        except Exception:
            continue
        stages.append(stage)
    return stages


def _is_mono_carton_style(spec_input: EngineSpecInput, metrics: EngineMetrics) -> bool:
    try:
        style_code = str(getattr(spec_input, "style_code", "") or "").strip().upper()
    except Exception:
        style_code = ""
    try:
        style_payload = dict((getattr(metrics, "extra", {}) or {}).get("style") or {})
    except Exception:
        style_payload = {}
    try:
        style_metadata = dict(style_payload.get("metadata") or {})
    except Exception:
        style_metadata = {}
    style_family = str(style_metadata.get("style_family") or "").strip().lower()
    return style_code.startswith("MONO_") or style_family == "mono_carton"


def _style_param_float(spec_input: EngineSpecInput, name: str, default: float = 0.0) -> float:
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
    return default


def _style_param_text(spec_input: EngineSpecInput, name: str, default: str = "") -> str:
    for container_name in ("style_params", "style_parameters", "extra_params"):
        try:
            container = getattr(spec_input, container_name, None)
        except Exception:
            container = None
        if isinstance(container, dict) and name in container:
            try:
                value = container.get(name)
                if value in (None, ""):
                    return default
                return str(value)
            except Exception:
                return default
    return default


def _is_unit_like_uom(uom_name: object) -> bool:
    text = str(uom_name or "").strip().lower()
    if not text:
        return False
    normalized = text.replace(".", "").replace("_", " ").replace("-", " ")
    return normalized in {
        "unit",
        "units",
        "pc",
        "pcs",
        "piece",
        "pieces",
        "nos",
        "no",
        "number",
        "each",
        "ea",
    }


def _mono_carton_source_mode(spec_input: EngineSpecInput, uom_name: object) -> str:
    mode = _style_param_text(spec_input, "carton_board_source_mode", "auto").strip().lower()
    if mode in ("unit", "unit_sheet", "sheet_unit", "precut", "pre_cut"):
        return "unit_sheet"
    if mode in ("area", "area_weight", "gsm", "weight"):
        return "area_weight"
    return "unit_sheet" if _is_unit_like_uom(uom_name) else "area_weight"


def _mono_carton_ups(spec_input: EngineSpecInput) -> int:
    ups = _style_param_float(spec_input, "carton_board_ups", 1.0)
    try:
        ups_int = int(ups or 0)
    except Exception:
        ups_int = 0
    return max(1, ups_int)


def _fg_per_sheet_yield_count(metrics: EngineMetrics) -> int:
    try:
        extra = getattr(metrics, "extra", {}) or {}
        style = dict(extra.get("style") or {})
        metadata = dict(style.get("metadata") or {})
    except Exception:
        metadata = {}
    if str(metadata.get("yield_basis") or "").strip().lower() != "fg_per_sheet":
        return 0
    try:
        fg_per_sheet = int(float(metadata.get("fg_per_sheet") or metadata.get("diecut_ups") or 0))
    except Exception:
        fg_per_sheet = 0
    return fg_per_sheet if fg_per_sheet > 0 else 0


def _select_mono_carton_board_stage(
    stage_graph: StageGraph,
    stage_keys: Set[str],
    route_selected: bool,
) -> Optional[str]:
    stage_key = _find_material_stage_by_tokens(
        stage_graph,
        {"printing", "print", "sheet_print", "diecut", "die_cut", "cutting"},
    )
    if stage_key:
        return stage_key
    if route_selected:
        return (
            _select_route_material_stage(stage_graph, "printing")
            or _select_route_material_stage(stage_graph, "diecut")
            or _select_route_material_stage(stage_graph, "board")
        )
    return _select_material_stage(stage_keys, "board")


def _stage_semantic(stage: object) -> str:
    semantic = _normalize_stage_semantic(getattr(stage, "logical_role", "") or "")
    if semantic:
        return semantic
    try:
        key = str(getattr(stage, "key", "") or "").strip().lower()
    except Exception:
        key = ""
    fallback = {
        "2ply": "corrugation",
        "3ply": "corrugation",
        "singleface": "corrugation",
        "board": "conversion",
        "lamination": "lamination",
        "fg": "fg",
        "final": "fg",
        "seaming": "conversion",
    }
    return fallback.get(key, "")


def _is_surface_material_stage(stage: object) -> bool:
    """Return True for materialized surface/finishing stages.

    Routing profiles may include materialized surface stages such as top-sheet
    cutting. Those stages produce sheets for later board formation; they should
    not receive normal board liner/medium requirements.
    """

    values = [
        getattr(stage, "key", None),
        getattr(stage, "name", None),
        getattr(stage, "logical_role", None),
        getattr(stage, "output_kind", None),
    ]
    text = " ".join(str(value or "").strip().lower() for value in values)
    normalized = text.replace("-", "_").replace(" ", "_")
    surface_tokens = (
        "top_sheet",
        "ts_cut",
        "ts_print",
        "print",
        "cut",
        "die",
        "peel",
        "seam",
        "stitch",
        "fold",
        "paste",
        "pack",
    )
    return any(token in normalized for token in surface_tokens)


def _is_explicit_board_material_stage(stage: object) -> bool:
    values = [
        getattr(stage, "key", None),
        getattr(stage, "name", None),
    ]
    text = " ".join(str(value or "").strip().lower() for value in values)
    normalized = text.replace("-", "_").replace(" ", "_")
    return "board" in normalized


def _is_board_output_material_stage(stage: object) -> bool:
    if _stage_semantic(stage) == "corrugation":
        return False
    return str(getattr(stage, "output_kind", "") or "").strip().lower() == "board"


def _first_board_material_stage(stage_graph: StageGraph) -> Optional[str]:
    for stage in _material_stages_in_order(stage_graph):
        if _is_explicit_board_material_stage(stage):
            return getattr(stage, "key", None)
    for stage in _material_stages_in_order(stage_graph):
        if _is_board_output_material_stage(stage) and not _is_surface_material_stage(stage):
            return getattr(stage, "key", None)
    return None


def _material_stage_key_by_semantics(
    stage_graph: StageGraph,
    semantics: Set[str],
    *,
    include_terminal: bool = True,
    require_board_material: bool = False,
) -> Optional[str]:
    semantics_norm = {_normalize_stage_semantic(v) for v in semantics if v}
    for stage in _material_stages_in_order(stage_graph):
        if not include_terminal and bool(getattr(stage, "is_terminal", False)):
            continue
        if require_board_material and _is_surface_material_stage(stage):
            continue
        if _stage_semantic(stage) in semantics_norm:
            return getattr(stage, "key", None)
    return None


def _final_material_stage_key(stage_graph: StageGraph) -> Optional[str]:
    if stage_graph is None:
        return None
    try:
        key = stage_graph.final_stage_key()
    except Exception:
        key = None
    if key:
        return key
    stages = _material_stages_in_order(stage_graph)
    if stages:
        return getattr(stages[-1], "key", None)
    return None


def _first_material_stage_key(stage_graph: StageGraph) -> Optional[str]:
    stages = _material_stages_in_order(stage_graph)
    if stages:
        return getattr(stages[0], "key", None)
    return None


def _material_stage_by_key(stage_graph: StageGraph, stage_key: Optional[str]) -> Optional[object]:
    if not stage_key:
        return None
    for stage in _material_stages_in_order(stage_graph):
        if getattr(stage, "key", None) == stage_key:
            return stage
    return None


def _is_terminal_fg_material_stage(stage: object) -> bool:
    if not stage:
        return False
    if _stage_semantic(stage) == "fg":
        return True
    output_kind = str(getattr(stage, "output_kind", "") or "").strip().lower()
    if output_kind in {"box", "unit", "finished_goods", "fg"}:
        return True
    return bool(getattr(stage, "is_terminal", False)) and output_kind not in {"", "sheet", "board"}


def _is_joining_material_stage(stage: object) -> bool:
    if not stage:
        return False
    values = [
        getattr(stage, "key", None),
        getattr(stage, "name", None),
        getattr(stage, "logical_role", None),
        getattr(stage, "output_kind", None),
    ]
    text = " ".join(str(value or "").strip().lower() for value in values)
    normalized = text.replace("-", "_").replace(" ", "_")
    if any(token in normalized for token in ("seaming", "jointing", "stitching", "gluing")):
        return True
    return _is_terminal_fg_material_stage(stage) and _stage_semantic(stage) in {"conversion", "fg"}


def _joining_material_stage_key(stage_graph: StageGraph) -> Optional[str]:
    for stage in _material_stages_in_order(stage_graph):
        if _is_joining_material_stage(stage):
            return getattr(stage, "key", None)
    return None


def _next_material_stage_after_semantics(
    stage_graph: StageGraph,
    semantics: Set[str],
    *,
    require_board_material: bool = False,
) -> Optional[str]:
    stages = _material_stages_in_order(stage_graph)
    semantics_norm = {_normalize_stage_semantic(v) for v in semantics if v}
    for idx, stage in enumerate(stages):
        if _stage_semantic(stage) not in semantics_norm:
            continue
        for candidate in stages[idx + 1 :]:
            if require_board_material and _is_surface_material_stage(candidate):
                continue
            return getattr(candidate, "key", None)
    return None


def _routing_profile_selected(spec_input: EngineSpecInput) -> bool:
    try:
        routing_profile = getattr(spec_input, "routing_profile", None)
    except Exception:
        routing_profile = None
    if routing_profile is None:
        return False
    try:
        stages = getattr(routing_profile, "stages", None)
    except Exception:
        stages = None
    return bool(stages)


def _select_route_material_stage(stage_graph: StageGraph, purpose: str) -> Optional[str]:
    purpose_norm = _normalize_stage_semantic(purpose)
    final_stage_key = _final_material_stage_key(stage_graph)

    if purpose_norm == "corrugation":
        return _material_stage_key_by_semantics(stage_graph, {"corrugation"}) or _first_material_stage_key(stage_graph)

    if purpose_norm == "board":
        return (
            _first_board_material_stage(stage_graph)
            or _material_stage_key_by_semantics(
                stage_graph,
                {"lamination", "conversion"},
                include_terminal=False,
                require_board_material=True,
            )
            or _next_material_stage_after_semantics(
                stage_graph,
                {"corrugation"},
                require_board_material=True,
            )
            or _material_stage_key_by_semantics(
                stage_graph,
                {"lamination", "conversion"},
                require_board_material=True,
            )
            or final_stage_key
            or _first_material_stage_key(stage_graph)
        )

    if purpose_norm == "lamination":
        return (
            _material_stage_key_by_semantics(stage_graph, {"lamination"})
            or _select_route_material_stage(stage_graph, "board")
        )

    if purpose_norm == "final":
        return final_stage_key

    return None


def resolve_rm_inputs(
    spec_input: EngineSpecInput,
    plant_profile: PlantProfile,
    stage_graph: StageGraph,
    metrics: EngineMetrics,
) -> (List[RmRequirement], BoardStructure):
    """Resolve raw-material inputs per material stage.

    This helper inspects the StageGraph and routes RM requirements to
    the appropriate material stages according to simple rules:

    - Corrugation board layers (liner/medium) feed stage '2ply'.
    - Surface/top-sheet behaviour is driven by a combination of:
        - spec_input.top_sheet_gsm (legacy) and
        - spec_input.surface_profile (V2).
    - Lamination film is added when lamination_required is enabled
      on the surface profile and a suitable lamination/board stage
      exists in the StageGraph.

    Quantities use the existing m2_per_box + GSM logic where available.
    """

    # Material stage keys from the StageGraph (SFG chain) and the
    # plant's configured process stages. We treat plant stages as the
    # authoritative list for process-only steps such as ts_print that
    # do not appear as material nodes in the StageGraph.
    stage_keys: Set[str] = {s.key for s in stage_graph.stages}
    route_selected = _routing_profile_selected(spec_input)
    m2_per_box = float(getattr(metrics, "m2_per_box", 0.0) or 0.0)

    # Lightweight diagnostics: expose consumables flags and factors in
    # metrics.extra so that callers (preview/debug tools) can inspect
    # what the engine actually saw at runtime. This only adds a nested
    # key and does not alter existing behaviour.
    try:
        extra = getattr(metrics, "extra", {}) or {}
        dbg = dict(extra.get("consumables_debug", {}) or {})
        dbg.setdefault("enable_consumables", bool(getattr(plant_profile, "enable_consumables", False)))
        dbg.setdefault("enable_adhesive", bool(getattr(plant_profile, "enable_adhesive", False)))
        dbg.setdefault("enable_ink", bool(getattr(plant_profile, "enable_ink", False)))
        dbg.setdefault("enable_wire", bool(getattr(plant_profile, "enable_wire", False)))
        dbg.setdefault(
            "adhesive_factor_corrugation_kg_per_m2",
            getattr(plant_profile, "adhesive_factor_corrugation_kg_per_m2", None),
        )
        dbg.setdefault(
            "adhesive_factor_board_kg_per_m2",
            getattr(plant_profile, "adhesive_factor_board_kg_per_m2", None),
        )
        dbg.setdefault(
            "adhesive_factor_fg_kg_per_m2",
            getattr(plant_profile, "adhesive_factor_fg_kg_per_m2", None),
        )
        dbg.setdefault(
            "ink_gm2_at_100pct_default",
            getattr(plant_profile, "ink_gm2_at_100pct_default", None),
        )
        dbg.setdefault(
            "wire_stitches_per_cm_default",
            getattr(plant_profile, "wire_stitches_per_cm_default", None),
        )
        dbg.setdefault(
            "wire_g_per_stitch_default",
            getattr(plant_profile, "wire_g_per_stitch_default", None),
        )
        extra = dict(extra)
        extra.setdefault("consumables_debug", dbg)
        metrics.extra = extra
    except Exception:
        # Diagnostics must never affect RM resolution.
        pass

    rm_requirements: List[RmRequirement] = []

    # 1) Corrugation RM (liner/medium layers) feeding a corrugation-capable
    # material stage. When the canonical 2ply stage is absent (e.g., fully
    # automated single-stage routes), board layers are merged into the
    # nearest material stage using _select_material_stage().
    board_struct = resolve_board_structure(spec_input)

    # Top-sheet configuration (legacy scalar + optional V2 surface profile).
    def _gsm_top(name: str) -> float:
        try:
            return float(getattr(spec_input, name, 0.0) or 0.0)
        except Exception:
            return 0.0

    top_sheet_gsm_legacy = _gsm_top("top_sheet_gsm")
    surface = getattr(spec_input, "surface_profile", None)

    # V2 SurfaceProfileInput-aware behaviour (common flags).
    printing_required = bool(getattr(surface, "printing_required", False)) if surface else False
    top_sheet_mode = (getattr(surface, "top_sheet_mode", "") or "").strip() or "none"

    # Boxes per sheet and printing_ups are used for unit_sheet quantity.
    try:
        boxes_per_sheet = float(getattr(metrics, "extra", {}).get("boxes_per_sheet", 0.0) or 0.0)
    except Exception:
        boxes_per_sheet = 0.0
    try:
        printing_ups_val = int(getattr(metrics, "extra", {}).get("printing_ups", 0) or 0)
    except Exception:
        printing_ups_val = 0
    if printing_ups_val <= 0:
        try:
            printing_ups_val = int(getattr(metrics, "printing_ups", 0) or 0)
        except Exception:
            printing_ups_val = 0
    if printing_ups_val <= 0:
        printing_ups_val = 1

    # When a V2 surface profile explicitly selects roll top-sheet mode, the
    # top sheet replaces the first/top liner from the board structure. Ply
    # count represents total final board layers, so the top sheet must not be
    # added on top of all internal liners.
    replace_top_liner = False
    top_replaced_layer_no = None
    top_replaced_layer_gsm = 0.0
    top_replaced_layer_role = ""
    corrugation_stage_key = (
        _select_route_material_stage(stage_graph, "corrugation")
        if route_selected
        else _select_material_stage(stage_keys, "2ply")
    )
    board_material_stage_key = (
        _select_route_material_stage(stage_graph, "board")
        if route_selected
        else _select_material_stage(stage_keys, "board")
    )
    separate_board_stage = bool(
        corrugation_stage_key
        and board_material_stage_key
        and corrugation_stage_key != board_material_stage_key
    )
    mono_carton_style = _is_mono_carton_style(spec_input, metrics)
    if mono_carton_style:
        mono_board_layer = None
        try:
            ordered_mono_layers = sorted(
                list(getattr(board_struct, "layers", []) or []),
                key=lambda layer: int(getattr(layer, "layer_no", 0) or 0) or 9999,
            )
        except Exception:
            ordered_mono_layers = list(getattr(board_struct, "layers", []) or [])
        for layer in ordered_mono_layers:
            role_norm = str(getattr(layer, "role", "") or "").strip().lower()
            if role_norm == "carton_board":
                mono_board_layer = layer
                break
        if mono_board_layer is None and ordered_mono_layers:
            mono_board_layer = ordered_mono_layers[0]

        try:
            carton_board_gsm = float(getattr(mono_board_layer, "gsm", 0.0) or 0.0) if mono_board_layer else 0.0
        except Exception:
            carton_board_gsm = 0.0
        if carton_board_gsm <= 0.0:
            carton_board_gsm = _style_param_float(spec_input, "carton_board_gsm", 0.0)
        if carton_board_gsm <= 0.0:
            carton_board_gsm = _gsm_top("carton_board_gsm")
        if carton_board_gsm <= 0.0:
            try:
                carton_board_gsm = float(getattr(spec_input, "liner_gsm", 0.0) or 0.0)
            except Exception:
                carton_board_gsm = 0.0
        carton_board_stage_key = _select_mono_carton_board_stage(
            stage_graph,
            stage_keys,
            route_selected,
        )
        if carton_board_stage_key and carton_board_gsm > 0.0 and m2_per_box > 0.0:
            material_product_id = getattr(mono_board_layer, "material_product_id", 0) or 0 if mono_board_layer else 0
            material_product_name = getattr(mono_board_layer, "material_product_name", "") or "" if mono_board_layer else ""
            material_product_uom_id = getattr(mono_board_layer, "material_product_uom_id", 0) or 0 if mono_board_layer else 0
            material_product_uom_name = getattr(mono_board_layer, "material_product_uom_name", "") or "" if mono_board_layer else ""
            material_template_id = getattr(mono_board_layer, "material_template_id", 0) or 0 if mono_board_layer else 0
            material_template_name = getattr(mono_board_layer, "material_template_name", "") or "" if mono_board_layer else ""
            material_bf = getattr(mono_board_layer, "bf", 0.0) or 0.0 if mono_board_layer else 0.0
            material_color = getattr(mono_board_layer, "color", "") or "" if mono_board_layer else ""
            resolution_status = "exact" if material_product_id else ""
            carton_board_source_mode = _mono_carton_source_mode(spec_input, material_product_uom_name)
            carton_board_ups = _mono_carton_ups(spec_input)
            if carton_board_source_mode == "unit_sheet":
                qty_per_fg = 1.0 / float(carton_board_ups or 1)
                qty_uom = material_product_uom_name or "Units"
                consumption_type = "unit"
                quantity_basis = "carton_board_unit_sheet_ups"
                order_qty_rounding = "ceil_by_ups"
            else:
                qty_per_fg = (carton_board_gsm * m2_per_box) / 1000.0
                qty_uom = "kg"
                consumption_type = "area"
                quantity_basis = "carton_board_gsm_area_weight"
                order_qty_rounding = ""
            rm_requirements.append(
                RmRequirement(
                    product_key="rm_carton_board",
                    role="carton_board",
                    stage_key=carton_board_stage_key,
                    layer_no=1,
                    qty_per_fg=qty_per_fg,
                    qty_uom=qty_uom,
                    consumption_type=consumption_type,
                    metadata={
                        "gsm": carton_board_gsm,
                        "layer_no": 1,
                        "type": "carton_board",
                        "role": "carton_board",
                        "style_family": "mono_carton",
                        "kind": "carton_board",
                        "carton_board_source_mode": carton_board_source_mode,
                        "carton_board_ups": carton_board_ups,
                        "quantity_basis": quantity_basis,
                        "order_qty_rounding": order_qty_rounding,
                        "bf": material_bf,
                        "color": material_color,
                        "material_product_id": material_product_id,
                        "material_product_name": material_product_name,
                        "material_product_uom_id": material_product_uom_id,
                        "material_product_uom_name": material_product_uom_name,
                        "material_template_id": material_template_id,
                        "material_template_name": material_template_name,
                        "product_id": material_product_id or None,
                        "product_name": material_product_name,
                        "uom_id": material_product_uom_id or None,
                        "uom_name": material_product_uom_name,
                        "resolution_status": resolution_status,
                        "resolution_product_name": material_product_name,
                        "writer_product_key": "product.product:%s" % material_product_id if material_product_id else "",
                        "resolution": {
                            "status": resolution_status,
                            "product_id": material_product_id or None,
                            "product_name": material_product_name,
                            "uom_id": material_product_uom_id or None,
                            "uom_name": material_product_uom_name,
                            "material_template_id": material_template_id or None,
                            "material_template_name": material_template_name,
                        } if material_product_id else {},
                    },
                )
            )
        corrugation_stage_key = None
        board_material_stage_key = None
        separate_board_stage = False

    board_surface_liner_layer_no = None
    if separate_board_stage and getattr(board_struct, "layers", None):
        ordered_layers = sorted(
            list(board_struct.layers or []),
            key=lambda layer: int(getattr(layer, "layer_no", 0) or 0) or 9999,
        )
        first_layer = ordered_layers[0] if ordered_layers else None
        if first_layer and getattr(first_layer, "type", "") == "liner":
            board_surface_liner_layer_no = getattr(first_layer, "layer_no", None)

    if (
        surface is not None
        and top_sheet_mode == "roll"
        and corrugation_stage_key is not None
        and m2_per_box > 0.0
        and getattr(board_struct, "layers", None)
    ):
        for layer in board_struct.layers:
            if getattr(layer, "type", "") == "liner" and getattr(layer, "gsm", 0.0) > 0.0:
                replace_top_liner = True
                top_replaced_layer_no = getattr(layer, "layer_no", None)
                top_replaced_layer_gsm = float(getattr(layer, "gsm", 0.0) or 0.0)
                top_replaced_layer_role = getattr(layer, "role", "liner") or "liner"
                break

    # Board/material feasibility: when the canonical 2ply stage is part
    # of the flow and area metrics are meaningful, the resolved board
    # structure must be usable for RM computation.
    if corrugation_stage_key and m2_per_box > 0.0:
        if not board_struct.layers:
            raise ValueError(
                "Engine V2 board/material resolution failed: no valid board layers "
                "could be resolved from the spec."
            )

        # Validate each resolved layer and ensure that the overall wall
        # has at least one liner and one medium layer (2-ply minimum).
        liner_count = 0
        medium_count = 0
        invalid_reasons = []
        for idx, layer in enumerate(board_struct.layers, start=1):
            if layer.type not in ("liner", "medium"):
                invalid_reasons.append(
                    f"layer #{idx} has unsupported type {layer.type!r}"
                )
            if layer.gsm is None or layer.gsm <= 0.0:
                invalid_reasons.append(
                    f"layer #{idx} has non-positive GSM ({layer.gsm})"
                )
            if layer.type == "liner":
                liner_count += 1
            elif layer.type == "medium":
                medium_count += 1

        if invalid_reasons:
            raise ValueError(
                "Engine V2 board/material resolution failed: "
                + "; ".join(invalid_reasons)
            )

        if liner_count <= 0 or medium_count <= 0:
            raise ValueError(
                "Engine V2 board/material resolution failed: resolved board must "
                "contain at least one liner layer and one medium layer for corrugation."
            )

    unit_sheet_prepends_top_layer = bool(
        surface is not None
        and printing_required
        and top_sheet_mode == "unit_sheet"
    )
    board_rm_stage = corrugation_stage_key
    if board_rm_stage and m2_per_box > 0:
        for layer in board_struct.layers:
            # Roll mode promotes the top stack liner into top_roll. Unit-sheet
            # mode is different: the selected sheet is an external layer and
            # the internal stack remains intact, shifted one layer down.
            if (
                replace_top_liner
                and layer.type == "liner"
                and top_replaced_layer_no is not None
                and getattr(layer, "layer_no", None) == top_replaced_layer_no
            ):
                continue
            layer_no_effective = getattr(layer, "layer_no", None)
            if unit_sheet_prepends_top_layer and layer_no_effective is not None:
                layer_no_effective = int(layer_no_effective or 0) + 1
            flute_tuf = 1.0
            if layer.type == "medium":
                try:
                    flute_tuf = float(getattr(layer, "flute_tuf", 0.0) or 0.0)
                except Exception:
                    flute_tuf = 0.0
                if flute_tuf <= 0.0:
                    flute_tuf = 1.0
            qty_kg = (layer.gsm * m2_per_box * flute_tuf) / 1000.0 if layer.gsm > 0 else 0.0
            if qty_kg <= 0:
                continue
            if layer.type == "liner":
                product_key = "rm_liner"
            elif layer.type == "medium":
                product_key = "rm_medium"
            else:
                continue
            rm_stage_key = board_rm_stage
            if (
                separate_board_stage
                and board_surface_liner_layer_no is not None
                and layer.type == "liner"
                and getattr(layer, "layer_no", None) == board_surface_liner_layer_no
            ):
                rm_stage_key = board_material_stage_key
            rm_requirements.append(
                RmRequirement(
                    product_key=product_key,
                    role=layer.type,
                    stage_key=rm_stage_key,
                    layer_no=layer_no_effective,
                    qty_per_fg=qty_kg,
                    qty_uom="kg",
                    consumption_type="area",
                    metadata={
                        "gsm": layer.gsm,
                        "layer_no": layer_no_effective,
                        "source_layer_no": layer.layer_no,
                        "type": layer.type,
                        "role": getattr(layer, "role", layer.type),
                        "bf": getattr(layer, "bf", 0.0),
                        "color": getattr(layer, "color", "") or "",
                        "material_template_id": getattr(layer, "material_template_id", 0) or 0,
                        "material_template_name": getattr(layer, "material_template_name", "") or "",
                        "gsm_value_id": getattr(layer, "gsm_value_id", 0) or 0,
                        "bf_value_id": getattr(layer, "bf_value_id", 0) or 0,
                        "color_value_id": getattr(layer, "color_value_id", 0) or 0,
                        "unit_top_sheet_internal_layer_shift": bool(unit_sheet_prepends_top_layer),
                        "flute_code": getattr(layer, "flute_code", "") or "",
                        "tuf_applied": flute_tuf if layer.type == "medium" else 1.0,
                        "assigned_route_stage": rm_stage_key,
                        "surface_board_liner": bool(
                            separate_board_stage
                            and board_surface_liner_layer_no is not None
                            and layer.type == "liner"
                            and getattr(layer, "layer_no", None) == board_surface_liner_layer_no
                        ),
                    },
                )
            )

    # 2) Top-sheet and surface RM routing (roll vs external sheet).
    def _resolve_top_sheet_gsm_for_surface(surface_profile: object | None, mode: str) -> float:
        """Resolve top-sheet GSM for V2 surface profiles.

        Resolution order for unit_sheet mode:
        1) surface_profile.top_sheet_gsm (product-derived GSM),
        2) spec_input.top_sheet_gsm (legacy scalar fallback).

        For roll mode, stack-driven GSM (from the top liner layer) is
        preferred when available so that roll-mode top sheets are
        derived directly from the board stack. The legacy scalar
        spec_input.top_sheet_gsm is used only as a fallback.
        """

        if surface_profile is None:
            return 0.0

        mode_norm = (mode or "").strip().lower()
        gsm = 0.0

        if mode_norm == "unit_sheet":
            try:
                gsm = float(getattr(surface_profile, "top_sheet_gsm", 0.0) or 0.0)
            except Exception:
                gsm = 0.0

        if gsm <= 0.0:
            try:
                gsm = float(getattr(spec_input, "top_sheet_gsm", 0.0) or 0.0)
            except Exception:
                gsm = 0.0

        # For roll mode, prefer stack-derived GSM when we detected a
        # replaced top layer; otherwise fall back to the scalar value.
        if mode_norm == "roll" and replace_top_liner and top_replaced_layer_gsm > 0.0:
            gsm = top_replaced_layer_gsm

        return max(0.0, gsm)

    # When no SurfaceProfileInput is attached, do not infer a top sheet
    # from scalar top_sheet_gsm alone. Top-sheet RM is explicit-mode
    # behaviour only: roll/unit_sheet surface configuration must be present.
    if surface is None:
        pass

    # For V2 surface profiles, resolve GSM based on mode so that
    # unit_sheet can prefer product-derived GSM while roll retains the
    # legacy scalar behaviour as a fallback only.
    top_sheet_gsm_v2 = _resolve_top_sheet_gsm_for_surface(surface, top_sheet_mode)

    # Top-sheet material is independent from printing. A roll top sheet
    # still needs cutting/conversion even when print consumables are absent.
    top_sheet_required = surface is not None and top_sheet_mode in ("roll", "unit_sheet")
    if not top_sheet_required or m2_per_box <= 0:
        pass
    else:
        # Common metadata for both modes; GSM is always attached as
        # metadata so that weight-based diagnostics remain available.
        gsm_meta = top_sheet_gsm_v2
        metadata_common = {
            "gsm": gsm_meta,
            "print_process": getattr(surface, "print_process", ""),
            "print_colors": getattr(surface, "print_colors", 0),
            "print_coverage_percent": getattr(surface, "print_coverage_percent", 0.0),
            "top_sheet_mode": top_sheet_mode,
            "top_sheet_product_id": getattr(surface, "top_sheet_product_id", None),
            "top_sheet_roll_code": getattr(surface, "top_sheet_roll_code", ""),
        }

        if top_sheet_mode == "roll":
            # In-house printing from roll; prefer the material stage
            # that owns printing operations when present, otherwise
            # fall back to the nearest board-like material stage.
            gsm_roll = top_sheet_gsm_v2
            if gsm_roll <= 0.0:
                gsm_roll = top_sheet_gsm_legacy
            if gsm_roll > 0.0:
                top_kg = (gsm_roll * m2_per_box) / 1000.0
                stage_key = _find_material_stage_by_tokens(
                    stage_graph, {"ts_cut", "cutting", "top_sheet_cut"}
                )
                if not stage_key:
                    stage_key = _find_stage_for_operation_in_graph(
                        stage_graph, ["ts_cut", "cutting", "top_sheet_cut", "ts_print", "printing"]
                    )
                if not stage_key:
                    stage_key = (
                        _select_route_material_stage(stage_graph, "board")
                        if route_selected
                        else _select_material_stage(stage_keys, "board")
                    )
                if stage_key and top_kg > 0.0:
                    # Attach the original stack layer index when available so
                    # downstream paper resolution can treat this RM like the
                    # replaced liner layer for variant matching and preview.
                    layer_no_top = top_replaced_layer_no if replace_top_liner else None
                    meta_top = dict(metadata_common)
                    if layer_no_top is not None:
                        meta_top.setdefault("layer_no", layer_no_top)
                        meta_top.setdefault("type", "liner")
                        meta_top.setdefault("role", top_replaced_layer_role or "liner")
                    meta_top["kind"] = "top_roll"
                    rm_requirements.append(
                        RmRequirement(
                            product_key="rm_top_roll",
                            role="top_roll",
                            stage_key=stage_key,
                            layer_no=layer_no_top,
                            qty_per_fg=top_kg,
                            qty_uom="kg",
                            consumption_type="area",
                            metadata=meta_top,
                        )
                    )
        elif top_sheet_mode == "unit_sheet":
            # External printed top-sheet units feeding board conversion.
            # Quantity is sheet-based: for die-cut sheet styles one external
            # top sheet feeds the whole source sheet, so FG-per-sheet is the
            # authoritative yield. Fall back to layout boxes-per-sheet and then
            # printing UPS for older/non-yield styles.
            fg_per_sheet = _fg_per_sheet_yield_count(metrics)
            if fg_per_sheet > 0:
                sheets_per_fg = 1.0 / float(fg_per_sheet)
            elif boxes_per_sheet > 0.0:
                sheets_per_fg = 1.0 / float(boxes_per_sheet)
            else:
                sheets_per_fg = 1.0 / float(max(printing_ups_val, 1))

            stage_key = (
                _select_route_material_stage(stage_graph, "board")
                if route_selected
                else _select_material_stage(stage_keys, "board")
            )
            if stage_key and sheets_per_fg > 0.0:
                meta_unit = {**metadata_common, "kind": "top_sheet_external"}
                meta_unit.setdefault("layer_no", 1)
                meta_unit.setdefault("type", "top_sheet_external")
                meta_unit.setdefault("role", "top_sheet_external")
                meta_unit["top_sheet_replaces_liner"] = False
                if fg_per_sheet > 0:
                    meta_unit["yield_basis"] = "fg_per_sheet"
                    meta_unit["fg_per_sheet"] = fg_per_sheet
                if top_sheet_gsm_v2 > 0.0:
                    top_kg = (top_sheet_gsm_v2 * m2_per_box) / 1000.0
                    meta_unit["kg_per_fg"] = top_kg
                rm_requirements.append(
                    RmRequirement(
                        product_key="rm_top_sheet_external",
                        role="top_sheet_external",
                        stage_key=stage_key,
                        layer_no=1,
                        qty_per_fg=sheets_per_fg,
                        qty_uom="unit",
                        consumption_type="unit",
                        metadata=meta_unit,
                    )
                )

    consumable_state = effective_consumable_state(
        plant_profile=plant_profile,
        spec=spec_input,
    )
    enable_consumables = bool(consumable_state.get("enable_consumables"))

    # 3) Lamination film (simplified) when requested on the surface profile.
    lamination_required = bool(getattr(surface, "lamination_required", False))
    if (
        lamination_required
        and m2_per_box > 0
        and is_consumable_enabled(consumable_state, "lamination_film")
    ):
        # Prefer an explicit lamination stage when present, otherwise
        # fall back to board conversion.
        if route_selected:
            lam_stage = _select_route_material_stage(stage_graph, "lamination")
        elif "lamination" in stage_keys:
            lam_stage = "lamination"
        else:
            lam_stage = _select_material_stage(stage_keys, "board")

        if lam_stage is not None:
            # For this slice, we treat lamination usage as proportional
            # to board area. Detailed gm2 logic can be added later.
            lam_qty = m2_per_box
            lam_metadata = {
                "lamination_type_id": getattr(surface, "lamination_type_id", None),
                "lamination_side": getattr(surface, "lamination_side", ""),
                "kind": "lamination_film",
            }
            rm_requirements.append(
                RmRequirement(
                    product_key="rm_lamination_film",
                    role="lamination",
                    stage_key=lam_stage,
                    layer_no=None,
                    qty_per_fg=lam_qty,
                    qty_uom="m2",
                    consumption_type="area",
                    metadata=lam_metadata,
                )
            )

    # 4) Consumable raw materials (adhesive, ink, wire, etc.). Adhesive
    # and ink are implemented under explicit feature flags so existing
    # flows remain unchanged when the flags are disabled.
    if enable_consumables and m2_per_box > 0.0:
        rm_requirements.extend(
            _resolve_consumable_rms(
                spec_input,
                plant_profile,
                stage_graph,
                metrics,
                m2_per_box=m2_per_box,
                consumable_state=consumable_state,
            )
        )

    # Aggregate adhesive totals for diagnostics when consumables are
    # enabled. This does not alter metrics when disabled.
    try:
        extra = getattr(metrics, "extra", None)
    except Exception:
        extra = None
    if enable_consumables and extra is not None:
        try:
            total_adh = 0.0
            for rm in rm_requirements:
                role = (getattr(rm, "role", "") or "").strip().lower()
                if not role.startswith("adhesive"):
                    continue
                try:
                    q = float(getattr(rm, "qty_per_fg", 0.0) or 0.0)
                except Exception:
                    q = 0.0
                total_adh += q
            if total_adh < 0.0:
                total_adh = 0.0
            extra_dict = dict(extra or {})
            extra_dict.setdefault("adhesive_total_kg_per_box", float(total_adh))
            metrics.extra = extra_dict
        except Exception:
            # Adhesive totals are best-effort and must never block RM resolution.
            pass

    # Aggregate ink totals for diagnostics when consumables are enabled
    # and ink is active. This mirrors adhesive totals but is kept
    # independent so that either consumable can be enabled in isolation.
    try:
        extra = getattr(metrics, "extra", None)
    except Exception:
        extra = None
    if enable_consumables and extra is not None and is_consumable_enabled(consumable_state, "printing_ink"):
        try:
            total_ink = 0.0
            for rm in rm_requirements:
                role = (getattr(rm, "role", "") or "").strip().lower()
                if role != "ink":
                    continue
                try:
                    q = float(getattr(rm, "qty_per_fg", 0.0) or 0.0)
                except Exception:
                    q = 0.0
                total_ink += q
            if total_ink < 0.0:
                total_ink = 0.0
            extra_dict = dict(extra or {})
            extra_dict.setdefault("ink_total_kg_per_box", float(total_ink))
            metrics.extra = extra_dict
        except Exception:
            # Ink totals are best-effort diagnostics and must never
            # block RM resolution.
            pass

    return rm_requirements, board_struct


def _resolve_consumable_rms(
    spec_input: EngineSpecInput,
    plant_profile: PlantProfile,
    stage_graph: StageGraph,
    metrics: EngineMetrics,
    *,
    m2_per_box: float,
    consumable_state: Optional[dict] = None,
) -> List[RmRequirement]:
    """Resolve all enabled consumable RMs (adhesive, ink, etc.).

    This helper orchestrates per-consumable resolvers while keeping
    each consumable implementation separate. It is intentionally thin
    so that future consumables (wire, tape, film) can be added without
    altering the resolve_rm_inputs contract.
    """

    rms: List[RmRequirement] = []
    consumable_state = dict(
        consumable_state
        or effective_consumable_state(plant_profile=plant_profile, spec=spec_input)
    )
    if _is_mono_carton_style(spec_input, metrics):
        # Mono carton consumes a single carton board substrate. Corrugation
        # and board-pasting adhesives only apply to corrugated board flows.
        consumable_state["adhesive_corrugation"] = False
        consumable_state["adhesive_board"] = False
        consumable_state["enable_corrugation_adhesive"] = False
        consumable_state["enable_lamination_adhesive"] = False

    if is_consumable_enabled(consumable_state, "adhesive_corrugation") or is_consumable_enabled(consumable_state, "adhesive_board") or is_consumable_enabled(consumable_state, "adhesive_fg"):
        rms.extend(
            _resolve_adhesive_rms(
                spec_input,
                plant_profile,
                stage_graph,
                metrics,
                m2_per_box=m2_per_box,
                consumable_state=consumable_state,
            )
        )

    if is_consumable_enabled(consumable_state, "printing_ink"):
        rms.extend(
            _resolve_ink_rms(
                spec_input,
                plant_profile,
                stage_graph,
                metrics,
                m2_per_box=m2_per_box,
            )
        )

    if is_consumable_enabled(consumable_state, "stitching_wire"):
        rms.extend(
            _resolve_wire_rms(
                spec_input,
                plant_profile,
                stage_graph,
                metrics,
                m2_per_box=m2_per_box,
            )
        )

    return rms


def _resolve_adhesive_rms(
    spec_input: EngineSpecInput,
    plant_profile: PlantProfile,
    stage_graph: StageGraph,
    metrics: EngineMetrics,
    *,
    m2_per_box: float,
    consumable_state: Optional[dict] = None,
) -> List[RmRequirement]:
    """Resolve adhesive consumables for corrugation, board and FG stages.

    Phase 1 uses simple per-m2 factors carried on PlantProfile. All
    quantities are optional and default to zero when configuration is
    missing so behaviour remains unchanged unless explicitly enabled.
    """

    rms: List[RmRequirement] = []
    consumable_state = dict(
        consumable_state
        or effective_consumable_state(plant_profile=plant_profile, spec=spec_input)
    )

    try:
        stage_keys: Set[str] = {s.key for s in stage_graph.stages}
    except Exception:
        stage_keys = set()
    route_selected = _routing_profile_selected(spec_input)

    def _float(val) -> float:
        try:
            return float(val or 0.0)
        except Exception:
            return 0.0

    def _add_adh(
        product_key: str,
        role: str,
        preferred_stage: str,
        factor_kg_per_m2: float,
    ) -> None:
        if route_selected:
            route_purpose = {
                "2ply": "corrugation",
                "board": "board",
                "fg": "final",
            }.get(preferred_stage, preferred_stage)
            stage_key = _select_route_material_stage(stage_graph, route_purpose)
        else:
            stage_key = _select_material_stage(stage_keys, preferred_stage)
        if not stage_key:
            return
        if route_selected and preferred_stage == "fg":
            board_stage_key = _select_route_material_stage(stage_graph, "board")
            if stage_key == board_stage_key and not _is_terminal_fg_material_stage(
                _material_stage_by_key(stage_graph, stage_key)
            ):
                return
        factor = _float(factor_kg_per_m2)
        if factor <= 0.0 or m2_per_box <= 0.0:
            return
        qty_kg = factor * float(m2_per_box)
        if qty_kg <= 0.0:
            return
        rms.append(
            RmRequirement(
                product_key=product_key,
                role=role,
                stage_key=stage_key,
                layer_no=None,
                qty_per_fg=qty_kg,
                qty_uom="kg",
                consumption_type="area",
                metadata={
                    "kind": role,
                    "factor_kg_per_m2": factor,
                },
            )
        )

    # Corrugation adhesive at the corrugation-like material stage, with
    # legacy 2ply fallback when no routing profile is selected.
    try:
        corr_factor = getattr(
            plant_profile, "adhesive_factor_corrugation_kg_per_m2", None
        )
    except Exception:
        corr_factor = None
    if corr_factor not in (None, ""):
        if is_consumable_enabled(consumable_state, "adhesive_corrugation"):
            _add_adh(
                product_key="rm_adhesive_corrugation",
                role="adhesive_corrugation",
                preferred_stage="2ply",
                factor_kg_per_m2=corr_factor,
            )

    # Board lamination adhesive at the board/lamination-like material
    # stage, with legacy board fallback when no routing profile is selected.
    try:
        board_factor = getattr(plant_profile, "adhesive_factor_board_kg_per_m2", None)
    except Exception:
        board_factor = None
    if board_factor not in (None, ""):
        if is_consumable_enabled(consumable_state, "adhesive_board"):
            _add_adh(
                product_key="rm_adhesive_board",
                role="adhesive_board",
                preferred_stage="board",
                factor_kg_per_m2=board_factor,
            )

    # Finished-good seaming adhesive at the final assembly stage. Generation is gated
    # by locking method when available:
    #   - glue or both -> generate FG adhesive,
    #   - wire / tape -> skip FG adhesive,
    #   - no locking record -> preserve legacy behaviour (generate).
    try:
        locking = getattr(spec_input, "locking_type", None)
    except Exception:
        locking = None
    locking_method = ""
    if locking is not None:
        try:
            locking_method = (getattr(locking, "method", "") or "").strip().lower()
        except Exception:
            locking_method = ""

    if locking is None:
        allow_fg_adh = True
    else:
        allow_fg_adh = locking_method in ("glue", "both")

    try:
        fg_factor = getattr(plant_profile, "adhesive_factor_fg_kg_per_m2", None)
    except Exception:
        fg_factor = None
    if allow_fg_adh and fg_factor not in (None, ""):
        if is_consumable_enabled(consumable_state, "adhesive_fg"):
            _add_adh(
                product_key="rm_adhesive_fg",
                role="adhesive_fg",
                preferred_stage="fg",
                factor_kg_per_m2=fg_factor,
            )

    return rms


def _resolve_ink_color_factor(
    print_colors: int,
    surface_profile: object | None,
    plant_profile: PlantProfile,
) -> float:
    """Compute a colour factor for ink usage.

    Temporary/default behaviour:
    - use max(1, print_colors) when a count is provided,
    - fall back to 1 when colours are not specified.

    This helper is intentionally isolated so that future
    process-/business-specific rules (e.g., per-print-process
    coefficients or per-ink-type factors) can replace the current
    logic without touching the rest of the ink RM computation or
    preview/BOM contracts. Tests document this default but should be
    updated alongside any future changes in business rules.
    """

    try:
        n = int(print_colors or 0)
    except Exception:
        n = 0
    if n <= 0:
        n = 1
    return float(n)


def _resolve_ink_rms(
    spec_input: EngineSpecInput,
    plant_profile: PlantProfile,
    stage_graph: StageGraph,
    metrics: EngineMetrics,
    *,
    m2_per_box: float,
) -> List[RmRequirement]:
    """Resolve ink consumables for printing stages.

    Ink is modelled as a single area-based RM per finished good, used
    at the printing stage. The quantity is derived from:
      - m2_per_box from EngineMetrics,
      - plant_profile.ink_gm2_at_100pct_default,
      - surface_profile.print_coverage_percent,
      - a configurable colour factor based on print_colors.

    Stage-aware behaviour attaches ink to the material stage that owns
    printing operations when possible; when no explicit printing owner
    exists, ink falls back to the nearest board-like material stage.
    """

    rms: List[RmRequirement] = []

    # Ink depends on the presence of a surface profile with printing
    # enabled; when absent we skip ink entirely.
    try:
        surface = getattr(spec_input, "surface_profile", None)
    except Exception:
        surface = None
    if surface is None:
        return rms

    try:
        printing_required = bool(getattr(surface, "printing_required", False))
    except Exception:
        printing_required = False
    if not printing_required or m2_per_box <= 0.0:
        return rms

    try:
        base_gm2 = getattr(plant_profile, "ink_gm2_at_100pct_default", None)
    except Exception:
        base_gm2 = None
    try:
        if base_gm2 is not None:
            base_gm2 = float(base_gm2)
    except Exception:
        base_gm2 = None
    if not base_gm2 or base_gm2 <= 0.0:
        # No usable baseline -> no ink RM; keeps behaviour unchanged
        # for plants that have not configured ink yet.
        return rms

    try:
        coverage_pct = float(getattr(surface, "print_coverage_percent", 0.0) or 0.0)
    except Exception:
        coverage_pct = 0.0
    coverage_factor = max(0.0, min(coverage_pct, 100.0)) / 100.0

    try:
        colors = int(getattr(surface, "print_colors", 0) or 0)
    except Exception:
        colors = 0

    color_factor = _resolve_ink_color_factor(colors, surface, plant_profile)

    # Effective gm2 taking coverage and colours into account.
    gm2_effective = float(base_gm2) * coverage_factor * float(color_factor)
    if gm2_effective <= 0.0:
        return rms

    ink_kg_per_box = (gm2_effective * float(m2_per_box)) / 1000.0
    if ink_kg_per_box <= 0.0:
        return rms

    # Attach ink to the logical printing stage. In route-driven Engine V2
    # flows Top Sheet Printing can be its own operation output node, so the
    # quantity-bearing legacy RM must use that stage key. Otherwise the
    # product-aware attachment rule lands on ts_print while the positive
    # quantity row falls back to cutting/board and stays as rm_ink.
    try:
        stage_keys: Set[str] = {s.key for s in stage_graph.stages}
    except Exception:
        stage_keys = set()
    route_selected = _routing_profile_selected(spec_input)

    stage_key = _find_stage_by_tokens_in_graph(
        stage_graph,
        {"ts_print", "top_sheet_print", "top_sheet_printing", "printing"},
    )
    if not stage_key:
        stage_key = _find_stage_for_operation_in_graph(stage_graph, ["ts_print", "printing"])
    if not stage_key:
        if route_selected:
            stage_key = _select_route_material_stage(stage_graph, "board")
        else:
            stage_key = _select_material_stage(stage_keys, "board")

    try:
        print_process = getattr(surface, "print_process", "")
    except Exception:
        print_process = ""
    try:
        print_side = getattr(surface, "print_side", "")
    except Exception:
        print_side = ""

    rms.append(
        RmRequirement(
            product_key="rm_ink",
            role="ink",
            stage_key=stage_key,
            layer_no=None,
            qty_per_fg=ink_kg_per_box,
            qty_uom="kg",
            consumption_type="area",
            metadata={
                "kind": "ink",
                "print_process": print_process,
                "print_colors": colors,
                "print_coverage_percent": coverage_pct,
                "print_side": print_side,
                "ink_gm2_at_100pct": float(base_gm2),
                "color_factor": float(color_factor),
            },
        )
    )

    return rms


def _resolve_wire_rms(
    spec_input: EngineSpecInput,
    plant_profile: PlantProfile,
    stage_graph: StageGraph,
    metrics: EngineMetrics,
    *,
    m2_per_box: float,
) -> List[RmRequirement]:
    """Resolve wire consumables for stitched locking.

    Wire is modelled as a single RM per finished good, expressed as a
    mass (kg) per box with consumption_type='length' to reflect that
    it is derived from a length-based process (stitches along the seam).

    Preconditions:
    - locking method must be 'wire',
    - seam length must be available via metrics.extra['geometry']['seam_mm'],
    - wire parameters must be available either on the locking type
      or via plant-level defaults (stitches_per_cm and g_per_stitch).
    """

    rms: List[RmRequirement] = []

    # 1) Locking must explicitly be wire-based.
    try:
        locking = getattr(spec_input, "locking_type", None)
    except Exception:
        locking = None
    locking_method = ""
    wire_grade = None
    wire_type_id = None
    wire_product_id = None
    wire_product_name = ""
    if locking is not None:
        try:
            locking_method = (getattr(locking, "method", "") or "").strip().lower()
        except Exception:
            locking_method = ""
        # Canonical wire subtype source: locking code. This is kept
        # as-is here; adapter layers are responsible for any further
        # normalisation before mapping.
        try:
            code_val = getattr(locking, "code", None)
        except Exception:
            code_val = None
        if code_val not in (None, ""):
            try:
                wire_grade = str(code_val).strip()
            except Exception:
                wire_grade = str(code_val)
        try:
            wire_type_id = int(getattr(locking, "wire_type_id", None) or 0) or None
        except Exception:
            wire_type_id = None
        try:
            wire_product_id = int(getattr(locking, "wire_product_id", None) or 0) or None
        except Exception:
            wire_product_id = None
        try:
            wire_product_name = str(getattr(locking, "wire_product_name", "") or "")
        except Exception:
            wire_product_name = ""
    if not locking_method:
        try:
            joining_type = str(getattr(spec_input, "joining_type", "") or "").strip().lower()
        except Exception:
            joining_type = ""
        if joining_type == "stitching":
            locking_method = "wire"
        elif joining_type == "gluing_stitching":
            locking_method = "both"
        elif joining_type == "tape":
            locking_method = "tape"
        elif joining_type == "gluing":
            locking_method = "glue"
    if locking_method not in ("wire", "both"):
        return rms

    # 2) Seam length from geometry metrics (single source of truth).
    try:
        extra = getattr(metrics, "extra", {}) or {}
    except Exception:
        extra = {}
    try:
        geometry = extra.get("geometry") or {}
    except Exception:
        geometry = {}
    try:
        seam_mm = float(geometry.get("seam_mm") or 0.0)
    except Exception:
        seam_mm = 0.0
    if seam_mm <= 0.0:
        # For styles without a seam or when geometry is unavailable we
        # do not attempt to recompute it here; no seam -> no wire.
        return rms

    # 3) Resolve wire parameters with explicit precedence:
    #    locking-level overrides plant defaults; if neither is usable,
    #    no wire RM is emitted.
    stitches_per_cm = 0.0
    g_per_stitch = 0.0

    # Locking-level overrides.
    if locking is not None:
        try:
            stitches_per_cm = float(
                getattr(locking, "wire_stitches_per_cm", None) or 0.0
            )
        except Exception:
            stitches_per_cm = 0.0
        try:
            g_per_stitch = float(
                getattr(locking, "wire_g_per_stitch", None) or 0.0
            )
        except Exception:
            g_per_stitch = 0.0

    # Plant-level defaults when locking does not provide usable values.
    if stitches_per_cm <= 0.0:
        try:
            stitches_per_cm = float(
                getattr(plant_profile, "wire_stitches_per_cm_default", None) or 0.0
            )
        except Exception:
            stitches_per_cm = 0.0
    if g_per_stitch <= 0.0:
        try:
            g_per_stitch = float(
                getattr(plant_profile, "wire_g_per_stitch_default", None) or 0.0
            )
        except Exception:
            g_per_stitch = 0.0

    if stitches_per_cm <= 0.0 or g_per_stitch <= 0.0:
        # Without valid wire parameters, wire consumption cannot be
        # computed reliably; skip wire instead of guessing.
        return rms

    # 4) Compute wire consumption per finished good.
    seam_cm = seam_mm / 10.0
    stitches_per_box = seam_cm * stitches_per_cm
    wire_g_per_box = stitches_per_box * g_per_stitch
    wire_kg_per_box = wire_g_per_box / 1000.0
    if wire_kg_per_box <= 0.0:
        return rms

    route_selected = _routing_profile_selected(spec_input)
    if route_selected:
        final_stage_key = _joining_material_stage_key(stage_graph) or _final_material_stage_key(stage_graph)
    else:
        try:
            stage_keys = {s.key for s in getattr(stage_graph, "stages", []) or []}
        except Exception:
            stage_keys = set()
        final_stage_key = (
            _joining_material_stage_key(stage_graph)
            or _select_material_stage(stage_keys, "fg")
            or "fg"
        )
    if not final_stage_key:
        return rms

    rms.append(
        RmRequirement(
            product_key="rm_wire",
            role="wire",
            stage_key=final_stage_key,
            layer_no=None,
            qty_per_fg=wire_kg_per_box,
            qty_uom="kg",
            consumption_type="length",
            metadata={
                "kind": "wire",
                "locking_method": locking_method,
                "wire_grade": wire_grade,
                "wire_type_id": wire_type_id,
                "wire_product_id": wire_product_id,
                "wire_product_name": wire_product_name,
                "product_id": wire_product_id,
                "product_name": wire_product_name,
                "writer_product_key": "product.product:%s" % wire_product_id if wire_product_id else "",
                "resolution_status": "resolved" if wire_product_id else "",
                "resolution": {"product_id": wire_product_id, "status": "resolved"} if wire_product_id else {},
                "seam_length_mm": float(seam_mm),
                "stitches_per_cm": float(stitches_per_cm),
                "g_per_stitch": float(g_per_stitch),
                "wire_g_per_box": float(wire_g_per_box),
            },
        )
    )

    return rms



