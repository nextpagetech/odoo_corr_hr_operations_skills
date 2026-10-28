from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

from odoo.tools.safe_eval import safe_eval

from .engine_v2.consumables import effective_consumable_state, should_include_consumable

SLITTING_OPTION_SAFETY_CAP = 50
SLITTING_OPTION_DEFAULT_MAX_UPS = 3


@dataclass
class RoutingStageRun:
    """Runtime representation of a single logical stage for Phase 8 shadow mode."""

    stage_template_id: int
    stage_contract_code: str
    stage_code: str
    stage_name: str
    stage_type: str
    capability_code: Optional[str]
    sequence: int
    is_optional: bool
    input_quantity_node_key: Optional[str]
    output_quantity_node_key: Optional[str]
    plant_binding_id: Optional[int] = None
    workcenter_id: Optional[int] = None
    routing_workcenter_id: Optional[int] = None


@dataclass
class RoutingInstance:
    """Runtime routing resolved for a spec + plant in Phase 8 shadow mode."""

    routing_template_id: Optional[int]
    routing_template_name: Optional[str]
    routing_template_code: Optional[str]
    seed_key: Optional[str]
    plant_id: Optional[int]
    stages: List[RoutingStageRun] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    selection_source: str = "none"
    selection_details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MaterialRequirementShadow:
    """Shadow material requirement derived from attachment rules (no quantities)."""

    stage_template_id: int
    stage_contract_code: str
    stage_code: str
    stage_type: str
    capability_code: Optional[str]
    stage_seed_key: Optional[str]
    semantic_role: Optional[str]
    material_profile_id: int
    material_type: str
    role: str
    product_tmpl_id: int
    default_bucket: Optional[str]
    attach_quantity_basis: str
    is_mandatory: bool
    rule_id: int
    legacy_role_aliases: List[str] = field(default_factory=list)
    input_quantity_node_key: Optional[str] = None
    output_quantity_node_key: Optional[str] = None


@dataclass
class StageCostLineShadow:
    """Stage-level cost aggregation placeholder for Phase 8 shadow mode."""

    stage_code: str
    stage_type: str
    stage_seed_key: Optional[str]
    bucket: Optional[str]
    material_cost: float = 0.0
    operation_cost: float = 0.0
    total_cost: float = 0.0
    notes: Optional[str] = None


@dataclass
class StageInputRoleSnapshot:
    """Snapshot of an allowed input role for a stage (contract only)."""

    role_code: str
    category: str
    requirement: str
    source: str
    priority: int


@dataclass
class StageOutput:
    """Shadow descriptor of a stage's output quantity (Phase 4).

    This lives only in the Phase 8 shadow runtime and does not affect
    Engine V2 core behaviour or BoM/costing flows.
    """

    kind: str
    quantity: float
    uom: str
    fg_equivalent_qty: float
    dimensions: Dict[str, float] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


@dataclass
class StageContext:
    """Runtime context for a routing stage in shadow mode.

    This captures the stage contract snapshot, parent-material linkage,
    contract-driven input roles and attachments, plus Phase 4 quantity
    planner outputs. It is exposed only via the Engine V2 debug JSON
    and does not affect live Engine V2 behaviour.
    """

    stage_template_id: int
    stage_contract_code: str
    stage_code: str
    stage_name: str
    stage_type: str
    sequence: int
    index: int
    is_optional: bool

    execution_type: str
    capability_code: Optional[str]
    capability_name: Optional[str]
    input_quantity_node_key: Optional[str]
    output_quantity_node_key: Optional[str]

    consumes_previous_output: bool
    previous_output_required: bool
    output_kind: Optional[str]
    output_stock_behavior: Optional[str]
    can_be_terminal: bool
    bom_mapping_policy: Optional[str]
    quantity_profile_code: Optional[str]
    quantity_profile_id: Optional[int]

    parent_material_index: Optional[int] = None
    parent_material_stage_code: Optional[str] = None

    input_roles: List[StageInputRoleSnapshot] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rm_attachments: List["StageAttachment"] = field(default_factory=list)
    consumable_attachments: List["StageAttachment"] = field(default_factory=list)
    intermediate_attachments: List["StageAttachment"] = field(default_factory=list)

    # Phase 4 quantity planner fields (shadow-only).
    fg_demand_qty: float = 0.0
    target_output_qty: float = 0.0
    target_output_uom: Optional[str] = None
    input_qty_from_prev: float = 0.0
    input_qty_from_rm: Dict[str, float] = field(default_factory=dict)
    computed_metrics: Dict[str, Any] = field(default_factory=dict)
    output_descriptor: Optional[StageOutput] = None


@dataclass
class StageAttachment:
    """Shadow attachment of a material/consumable/intermediate to a stage.

    This structure is populated in Phase 3 using contract-driven attachment
    rules and is visible only via the Phase 8 shadow runtime. It does not
    influence Engine V2 core behaviour.
    """

    stage_template_id: Optional[int]
    stage_contract_code: Optional[str]
    capability_code: Optional[str]
    role_code: Optional[str]
    semantic_role: Optional[str]
    category: Optional[str]
    source: str  # e.g. "material_profile_rule"
    material_profile_id: Optional[int]
    product_tmpl_id: Optional[int]
    rule_id: Optional[int]
    material_type: Optional[str]
    default_bucket: Optional[str]
    input_quantity_node_key: Optional[str] = None
    output_quantity_node_key: Optional[str] = None
    legacy_role_aliases: List[str] = field(default_factory=list)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _safe_getattr(record: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(record, name, default)
    except Exception:
        return default


def _is_usable_record(record: Any) -> bool:
    if not record:
        return False
    try:
        if getattr(record, "_name", "") == "_unknown":
            return False
    except Exception:
        return False
    return _safe_int(_safe_getattr(record, "id", 0), 0) > 0


def _normalize_flute_codes(value: Any) -> List[str]:
    """Return normalized flute codes from CSV/list-like values."""
    if value in (None, False, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = str(value or "").replace(";", ",").split(",")
    codes: List[str] = []
    for item in raw_items:
        code = str(item or "").strip().upper()
        if code and code not in codes:
            codes.append(code)
    return codes


def _required_corrugation_flutes_from_spec(spec: Any) -> List[str]:
    """Resolve required flute codes from current stack data.

    Current Engine V2 stack data is not fully stage-granular for every multi-
    flute case yet, so the safe rule is to require corrugation candidates to
    support every medium-layer flute found on the spec. This can be narrowed to
    stage/layer-specific requirements when that metadata becomes available.
    """
    required: List[str] = []

    try:
        for line in getattr(spec, "stack_line_ids", []) or []:
            role = str(getattr(line, "role", "") or "").strip().lower()
            if role != "medium":
                continue
            flute = getattr(line, "flute_type_id", None) or getattr(line, "flute_id", None)
            code = getattr(flute, "code", None) if flute else None
            for normalized in _normalize_flute_codes(code):
                if normalized not in required:
                    required.append(normalized)
    except Exception:
        pass

    if required:
        return required

    stack = []
    try:
        stack = list(getattr(spec, "stack_json", []) or [])
    except Exception:
        stack = []
    for layer in stack:
        if not isinstance(layer, dict):
            continue
        role = str(layer.get("role") or "").strip().lower()
        if role != "medium":
            continue
        for key in ("flute_code", "flute"):
            for normalized in _normalize_flute_codes(layer.get(key)):
                if normalized not in required:
                    required.append(normalized)
    return required


def _stage_requires_corrugation_flute(stage: RoutingStageRun, context: Optional["StageContext"] = None) -> bool:
    output_kind = str(getattr(context, "output_kind", "") or "").strip().lower()
    op_name = _stage_operation_name_for_feasibility(
        stage.stage_code,
        stage.stage_type,
        stage.capability_code,
        output_kind,
    )
    return op_name == "Corrugation"


def _stage_operation_name_for_feasibility(
    stage_code: Optional[str],
    stage_type: Optional[str],
    capability_code: Optional[str],
    output_kind: Optional[str],
) -> str:
    code = str(stage_code or "").strip().lower()
    stage_type_key = str(stage_type or "").strip().lower()
    capability_key = str(capability_code or "").strip().lower()
    output_kind_key = str(output_kind or "").strip().lower()
    classifier = capability_key or stage_type_key

    if classifier == "corrugation" or code in ("2ply", "3ply", "singleface"):
        return "Corrugation"
    if classifier == "cutting" or code in ("cutting", "ts_cut"):
        return "Cutting"
    if classifier == "printing" or code in ("printing", "ts_print"):
        return "Printing"
    if classifier == "lamination" or code in ("board", "lamination"):
        return "Lamination"
    if classifier == "diecut" or code in ("diecut", "slot_die", "slot/die"):
        return "Slot/Die"
    if classifier == "conversion" or output_kind_key in ("box", "fg") or code in ("seaming", "fg", "final"):
        return "Seaming"
    if code in ("peeloff", "peel-off", "peeling"):
        return "Peeling"
    return ""


def _fallback_stage_type_from_code(stage_code: Optional[str], stage_type: Optional[str] = None) -> str:
    code = str(stage_code or "").strip().lower()
    stage_type_key = str(stage_type or "").strip().lower()
    if stage_type_key in ("corrugation", "printing", "lamination", "diecut", "conversion", "fg", "auxiliary"):
        return stage_type_key
    if code in ("2ply", "3ply", "singleface", "board"):
        return "corrugation"
    if code in ("printing", "ts_print"):
        return "printing"
    if code in ("lamination",):
        return "lamination"
    if code in ("diecut", "slot_die", "slot/die", "peeloff", "peel-off", "peeling"):
        return "diecut"
    if code in ("seaming", "final"):
        return "conversion"
    if code in ("fg",):
        return "fg"
    if stage_type_key == "material":
        return "corrugation"
    return "auxiliary"


def _fallback_stage_io_from_code(stage_code: Optional[str], stage_type: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    code = str(stage_code or "").strip().lower()
    normalized_type = _fallback_stage_type_from_code(stage_code, stage_type)
    if code in ("2ply", "3ply", "singleface"):
        return "raw_material", "board"
    if normalized_type == "corrugation":
        return "board", "board"
    if normalized_type in ("printing", "lamination"):
        return "sheet", "sheet"
    if normalized_type == "diecut":
        return "sheet", "blanks"
    if normalized_type == "conversion":
        return "blanks", "finished_goods"
    if normalized_type == "fg":
        return "finished_goods", "finished_goods"
    return None, None


def _stage_dimension_payload(
    stage: RoutingStageRun,
    context: Optional[StageContext],
    engine_result: Any,
    spec: Any = None,
    v2_spec: Any = None,
) -> Dict[str, Any]:
    metrics = getattr(engine_result, "metrics", None)
    extra = getattr(metrics, "extra", {}) or {}
    layout = dict(extra.get("layout") or {})
    geometry = dict(extra.get("geometry") or {})

    sheet_length_mm = _safe_float(layout.get("sheet_length_mm"), 0.0)
    sheet_width_mm = _safe_float(layout.get("sheet_width_mm"), 0.0)
    sheet_source = str(layout.get("sheet_source") or "").strip() or "unknown"

    computed_blank_length_mm = _safe_float(geometry.get("blank_length_mm"), 0.0)
    computed_blank_width_mm = _safe_float(geometry.get("blank_width_mm"), 0.0)
    layout_blank_length_mm = 0.0
    layout_blank_width_mm = 0.0
    box_length_mm = _safe_float(geometry.get("box_length_mm"), 0.0)
    box_width_mm = _safe_float(geometry.get("box_width_mm"), 0.0)
    box_height_mm = _safe_float(geometry.get("box_height_mm"), 0.0)
    for source in (v2_spec, spec):
        if source is None:
            continue
        layout_blank_length_mm = layout_blank_length_mm or _safe_float(
            getattr(source, "blank_length_mm", 0.0), 0.0
        )
        layout_blank_width_mm = layout_blank_width_mm or _safe_float(
            getattr(source, "blank_width_mm", 0.0), 0.0
        )
        box_length_mm = box_length_mm or _safe_float(getattr(source, "box_length_mm", 0.0), 0.0)
        box_width_mm = box_width_mm or _safe_float(getattr(source, "box_width_mm", 0.0), 0.0)
        box_height_mm = box_height_mm or _safe_float(getattr(source, "box_height_mm", 0.0), 0.0)
        box_length_mm = box_length_mm or _safe_float(getattr(source, "length_mm", 0.0), 0.0)
        box_width_mm = box_width_mm or _safe_float(getattr(source, "width_mm", 0.0), 0.0)
        box_height_mm = box_height_mm or _safe_float(getattr(source, "height_mm", 0.0), 0.0)

    blank_length_mm = computed_blank_length_mm or layout_blank_length_mm
    blank_width_mm = computed_blank_width_mm or layout_blank_width_mm

    allow_rotation = True
    rotation_source = "default_true"
    for source in (v2_spec, spec):
        if source is None:
            continue
        layout_policy = getattr(source, "layout_policy_id", None)
        if layout_policy is None:
            continue
        try:
            allow_rotation = bool(getattr(layout_policy, "allow_rotation", True))
            rotation_source = "layout_policy"
            break
        except Exception:
            continue

    width_mm = sheet_width_mm or blank_width_mm or layout_blank_width_mm
    length_mm = sheet_length_mm or blank_length_mm or layout_blank_length_mm
    basis = "sheet"
    basis_reason = "resolved sheet dimensions are available from layout"
    if sheet_width_mm <= 0.0 or sheet_length_mm <= 0.0:
        basis = "sheet_fallback_blank"
        if blank_width_mm > 0.0 and blank_length_mm > 0.0:
            basis_reason = "layout did not resolve positive sheet dimensions; feasibility fell back to computed blank geometry"
        elif layout_blank_width_mm > 0.0 and layout_blank_length_mm > 0.0:
            basis_reason = "layout did not resolve positive sheet dimensions; feasibility fell back to effective layout blank dimensions"
        else:
            basis_reason = "layout did not resolve positive sheet dimensions and no blank geometry was available"

    checked_orientations: List[Dict[str, Any]] = []
    if width_mm > 0.0 or length_mm > 0.0:
        checked_orientations.append(
            {
                "orientation": "direct",
                "width_mm": width_mm,
                "length_mm": length_mm,
            }
        )
        if allow_rotation and (width_mm > 0.0 or length_mm > 0.0):
            rotated_width_mm = length_mm
            rotated_length_mm = width_mm
            if rotated_width_mm != width_mm or rotated_length_mm != length_mm:
                checked_orientations.append(
                    {
                        "orientation": "rotated",
                        "width_mm": rotated_width_mm,
                        "length_mm": rotated_length_mm,
                    }
                )

    return {
        "width_mm": width_mm,
        "length_mm": length_mm,
        "basis": basis,
        "basis_reason": basis_reason,
        "sheet_source": sheet_source,
        "sheet_width_mm": sheet_width_mm,
        "sheet_length_mm": sheet_length_mm,
        "box_length_mm": box_length_mm,
        "box_width_mm": box_width_mm,
        "box_height_mm": box_height_mm,
        "blank_width_mm": blank_width_mm,
        "blank_length_mm": blank_length_mm,
        "layout_blank_width_mm": layout_blank_width_mm,
        "layout_blank_length_mm": layout_blank_length_mm,
        "rotation_allowed": allow_rotation,
        "rotation_source": rotation_source,
        "checked_orientations": checked_orientations,
    }


def _is_diecut_sheet_layout_context(spec: Any = None, v2_spec: Any = None, engine_result: Any = None) -> bool:
    def _norm(value: Any) -> str:
        return str(value or "").strip().lower()

    candidates: List[str] = []
    for source in (v2_spec, spec):
        if source is None:
            continue
        candidates.append(_norm(getattr(source, "style_code", "")))
        candidates.append(_norm(getattr(source, "style", "")))
        candidates.append(_norm(getattr(source, "family", "")))
        style = getattr(source, "style_id", None)
        if style is not None:
            candidates.append(_norm(getattr(style, "code", "")))
            candidates.append(_norm(getattr(style, "family", "")))

    metrics = getattr(engine_result, "metrics", None)
    extra = getattr(metrics, "extra", {}) or {}
    for container in (
        extra,
        extra.get("metadata") if isinstance(extra, dict) else {},
        extra.get("style_metadata") if isinstance(extra, dict) else {},
        getattr(engine_result, "metadata", {}) or {},
    ):
        if isinstance(container, dict):
            candidates.append(_norm(container.get("style_code")))
            candidates.append(_norm(container.get("family")))
            candidates.append(_norm(container.get("style_family")))
    candidates.append(_norm(getattr(engine_result, "style_code", "")))

    return any(value in ("diecut_sheet", "diecut-sheet") or value.upper() == "DIECUT_SHEET" for value in candidates)


def _stage_current_ups(stage: RoutingStageRun, engine_result: Any) -> int:
    metrics = getattr(engine_result, "metrics", None)
    extra = getattr(metrics, "extra", {}) or {}
    op_name = _stage_operation_name_for_feasibility(
        stage.stage_code,
        stage.stage_type,
        stage.capability_code,
        "",
    )
    if op_name in ("Printing", "Lamination"):
        return max(
            _safe_int(extra.get("printing_ups"), 0),
            _safe_int(getattr(metrics, "printing_ups", 0), 0),
            1,
        )
    if op_name == "Slot/Die":
        return max(
            _safe_int(extra.get("diecut_ups"), 0),
            _safe_int(getattr(metrics, "diecut_ups", 0), 0),
            1,
        )
    return max(
        _safe_int(extra.get("effective_ups_machine"), 0),
        _safe_int(extra.get("effective_ups"), 0),
        _safe_int(getattr(metrics, "effective_ups", 0), 0),
        1,
    )


def _stage_ups_limit(stage: RoutingStageRun, plant_param: Any) -> Optional[int]:
    op_name = _stage_operation_name_for_feasibility(
        stage.stage_code,
        stage.stage_type,
        stage.capability_code,
        "",
    )
    if op_name in ("Printing", "Lamination"):
        limit = _safe_int(getattr(plant_param, "printing_ups", 0), 0)
        return limit or None
    if op_name == "Slot/Die":
        limit = _safe_int(getattr(plant_param, "diecut_ups", 0), 0)
        return limit or None
    return None


def _stage_width_limit_mm(stage: RoutingStageRun, plant_param: Any) -> Optional[float]:
    candidates: List[float] = []
    for attr in ("max_machine_width_mm", "sheet_width_mm"):
        value = _safe_float(getattr(plant_param, attr, 0.0), 0.0)
        if value > 0.0:
            candidates.append(value)

    op_name = _stage_operation_name_for_feasibility(
        stage.stage_code,
        stage.stage_type,
        stage.capability_code,
        "",
    )
    specific_attr = None
    if op_name == "Corrugation":
        specific_attr = "max_corrugator_width_mm"
    elif op_name in ("Printing", "Lamination", "Cutting"):
        specific_attr = "max_printer_width_mm"
    elif op_name == "Slot/Die":
        specific_attr = "max_diecut_width_mm"
    if specific_attr:
        value = _safe_float(getattr(plant_param, specific_attr, 0.0), 0.0)
        if value > 0.0:
            candidates.append(value)
    return min(candidates) if candidates else None


def _stage_length_limit_mm(plant_param: Any) -> Optional[float]:
    value = _safe_float(getattr(plant_param, "sheet_length_mm", 0.0), 0.0)
    return value or None


def _evaluate_stage_candidate_feasibility(
    candidate: Dict[str, Any],
    *,
    checked_dimensions: List[Dict[str, Any]],
    plant_width_limit_mm: Optional[float],
    plant_length_limit_mm: Optional[float],
    required_flutes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    deckle_min_cm = _safe_float(candidate.get("deckle_min_cm"), 0.0)
    deckle_max_cm = _safe_float(candidate.get("deckle_max_cm"), 0.0)
    width_min_source = candidate.get("deckle_min_source") or None
    width_max_source = candidate.get("deckle_max_source") or None
    required_flute_codes = _normalize_flute_codes(required_flutes)
    supported_flute_codes = _normalize_flute_codes(candidate.get("supported_flutes"))
    missing_flutes = (
        [code for code in required_flute_codes if code not in supported_flute_codes]
        if required_flute_codes
        else []
    )

    resolved_width_limit_mm = None
    resolved_width_limit_source = None
    if deckle_max_cm > 0.0:
        resolved_width_limit_mm = deckle_max_cm * 10.0
        resolved_width_limit_source = width_max_source or "candidate"
    elif plant_width_limit_mm:
        resolved_width_limit_mm = plant_width_limit_mm
        resolved_width_limit_source = "plant_fallback"

    resolved_length_limit_mm = None
    resolved_length_limit_source = None
    candidate_length_limit_mm = _safe_float(candidate.get("max_length_mm"), 0.0)
    if candidate_length_limit_mm > 0.0:
        resolved_length_limit_mm = candidate_length_limit_mm
        resolved_length_limit_source = candidate.get("max_length_source") or "candidate"
    elif plant_length_limit_mm:
        resolved_length_limit_mm = plant_length_limit_mm
        resolved_length_limit_source = "plant_fallback"

    orientation_checks: List[Dict[str, Any]] = []
    feasible_orientation = None

    for item in checked_dimensions or []:
        width_mm = _safe_float(item.get("width_mm"), 0.0)
        length_mm = _safe_float(item.get("length_mm"), 0.0)
        orientation = item.get("orientation") or "direct"
        rejection_reasons: List[str] = []

        if width_mm <= 0.0:
            rejection_reasons.append("missing_sheet_width_mm")
        if length_mm <= 0.0:
            rejection_reasons.append("missing_sheet_length_mm")
        if missing_flutes:
            rejection_reasons.append(
                "required flute(s) %s not supported by workcenter flutes %s"
                % (", ".join(missing_flutes), ", ".join(supported_flute_codes or ["-"]))
            )

        width_cm = width_mm / 10.0 if width_mm > 0.0 else 0.0
        if deckle_min_cm > 0.0 and width_cm + 1e-6 < deckle_min_cm:
            rejection_reasons.append(
                "width %.1f cm below %s deckle_min_cm %.1f cm"
                % (width_cm, width_min_source or "candidate", deckle_min_cm)
            )
        if resolved_width_limit_mm and width_mm > resolved_width_limit_mm + 1e-6:
            if resolved_width_limit_source == "plant_fallback":
                rejection_reasons.append(
                    "width %.1f mm exceeds plant fallback width limit %.1f mm"
                    % (width_mm, resolved_width_limit_mm)
                )
            else:
                rejection_reasons.append(
                    "width %.1f mm exceeds %s width limit %.1f mm"
                    % (width_mm, resolved_width_limit_source or "candidate", resolved_width_limit_mm)
                )
        if resolved_length_limit_mm and length_mm > resolved_length_limit_mm + 1e-6:
            if resolved_length_limit_source == "plant_fallback":
                rejection_reasons.append(
                    "length %.1f mm exceeds plant fallback length limit %.1f mm"
                    % (length_mm, resolved_length_limit_mm)
                )
            else:
                rejection_reasons.append(
                    "length %.1f mm exceeds %s length limit %.1f mm"
                    % (length_mm, resolved_length_limit_source or "candidate", resolved_length_limit_mm)
                )

        orientation_checks.append(
            {
                "orientation": orientation,
                "width_mm": width_mm,
                "length_mm": length_mm,
                "status": "feasible" if not rejection_reasons else "rejected",
                "rejection_reasons": rejection_reasons,
            }
        )
        if feasible_orientation is None and not rejection_reasons:
            feasible_orientation = orientation

    rejection_reasons = []
    if feasible_orientation is None:
        rejection_reasons = sorted(
            {
                reason
                for item in orientation_checks
                for reason in list(item.get("rejection_reasons") or [])
            }
        )

    return {
        **dict(candidate or {}),
        "status": "feasible" if feasible_orientation else "rejected",
        "rejection_reasons": rejection_reasons,
        "used_orientation": feasible_orientation,
        "rotation_used": feasible_orientation == "rotated",
        "dimension_checks": orientation_checks,
        "resolved_constraints": {
            "width_min_cm": deckle_min_cm or None,
            "width_min_source": width_min_source,
            "width_limit_mm": resolved_width_limit_mm,
            "width_limit_source": resolved_width_limit_source,
            "length_limit_mm": resolved_length_limit_mm,
            "length_limit_source": resolved_length_limit_source,
            "required_flutes": required_flute_codes,
            "supported_flutes": supported_flute_codes,
            "flute_check_applied": bool(required_flute_codes),
        },
    }


def _collect_stage_option_workcenters(
    env,
    stage: RoutingStageRun,
    context: Optional[StageContext],
    *,
    required_flutes: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    raw_candidates = _collect_stage_candidate_workcenters(env, stage, context)
    workcenter_model = None
    try:
        workcenter_model = env["mrp.workcenter"].sudo()
    except Exception:
        workcenter_model = None

    resolved: List[Dict[str, Any]] = []
    seen_ids = set()
    for raw in raw_candidates or []:
        workcenter_id = _safe_int(raw.get("workcenter_id"), 0)
        if workcenter_id <= 0 or workcenter_id in seen_ids:
            continue
        seen_ids.add(workcenter_id)

        width_min_cm = 0.0
        width_max_cm = 0.0
        length_min_mm = 0.0
        length_max_mm = 0.0
        max_slitting_ups = 0
        constraint_source = "workcenter"
        supported_flutes: List[str] = []

        if workcenter_model is not None:
            try:
                workcenter = workcenter_model.browse(workcenter_id)
            except Exception:
                workcenter = None
            if workcenter:
                deckle_min_mm = 0.0
                deckle_max_mm = 0.0
                width_min_cm = _safe_float(
                    getattr(workcenter, "corr_deckle_min_cm", width_min_cm),
                    width_min_cm,
                )
                width_max_cm = _safe_float(
                    getattr(workcenter, "corr_deckle_max_cm", width_max_cm),
                    width_max_cm,
                )
                if hasattr(workcenter, "get_corr_deckle_min_mm"):
                    deckle_min_mm = _safe_float(workcenter.get_corr_deckle_min_mm(), 0.0)
                else:
                    deckle_min_mm = width_min_cm * 10.0 if width_min_cm > 0.0 else 0.0
                if hasattr(workcenter, "get_corr_deckle_max_mm"):
                    deckle_max_mm = _safe_float(workcenter.get_corr_deckle_max_mm(), 0.0)
                else:
                    deckle_max_mm = width_max_cm * 10.0 if width_max_cm > 0.0 else 0.0
                if hasattr(workcenter, "get_corr_length_min_mm"):
                    length_min_mm = _safe_float(workcenter.get_corr_length_min_mm(), length_min_mm)
                else:
                    length_min_mm = _safe_float(
                        getattr(workcenter, "corr_length_min_mm", length_min_mm),
                        length_min_mm,
                    )
                if hasattr(workcenter, "get_corr_length_max_mm"):
                    length_max_mm = _safe_float(workcenter.get_corr_length_max_mm(), length_max_mm)
                else:
                    length_max_mm = _safe_float(
                        getattr(workcenter, "corr_length_max_mm", length_max_mm),
                        length_max_mm,
                    )
                max_slitting_ups = _safe_int(
                    getattr(workcenter, "max_slitting_ups", max_slitting_ups),
                    max_slitting_ups,
                )
                supported_flutes = _normalize_flute_codes(
                    getattr(workcenter, "corr_flutes_supported", "")
                )

        resolved.append(
            {
                "source": raw.get("source") or "",
                "operation_name": raw.get("operation_name") or "",
                "workcenter_id": workcenter_id,
                "workcenter_name": raw.get("workcenter_name") or "",
                "width_min_cm": width_min_cm or None,
                "width_max_cm": width_max_cm or None,
                "length_min_mm": length_min_mm or None,
                "length_max_mm": length_max_mm or None,
                "max_slitting_ups": max_slitting_ups or None,
                "max_slitting_ups_source": "workcenter" if max_slitting_ups > 0 else None,
                "required_flutes": list(required_flutes or []),
                "supported_flutes": supported_flutes,
                "constraint_source": constraint_source,
                "constraints_applied": {
                    "width_min_cm": width_min_cm or None,
                    "width_max_cm": width_max_cm or None,
                    "width_min_mm": deckle_min_mm or None,
                    "width_max_mm": deckle_max_mm or None,
                    "length_min_mm": length_min_mm or None,
                    "length_max_mm": length_max_mm or None,
                    "max_slitting_ups": max_slitting_ups or None,
                    "required_flutes": list(required_flutes or []),
                    "supported_flutes": supported_flutes,
                },
                "constraint_status": {
                    "width_constraints_defined": bool(width_min_cm > 0.0 or width_max_cm > 0.0),
                    "length_constraints_defined": bool(length_min_mm > 0.0 or length_max_mm > 0.0),
                    "max_slitting_ups_defined": bool(max_slitting_ups > 0),
                    "flute_constraints_defined": bool(supported_flutes),
                },
            }
        )
    return resolved


def _resolve_stage_option_blank_dimensions(
    stage: RoutingStageRun,
    context: Optional[StageContext],
    engine_result: Any,
    *,
    spec: Any = None,
    v2_spec: Any = None,
) -> Dict[str, Any]:
    dims = _stage_dimension_payload(stage, context, engine_result, spec=spec, v2_spec=v2_spec)
    blank_length_mm = _safe_float(dims.get("blank_length_mm"), 0.0)
    blank_width_mm = _safe_float(dims.get("blank_width_mm"), 0.0)
    blank_basis = "computed_blank"
    blank_basis_reason = "style/computed blank geometry is available"
    sheet_length_mm = _safe_float(dims.get("sheet_length_mm"), 0.0)
    sheet_width_mm = _safe_float(dims.get("sheet_width_mm"), 0.0)
    if sheet_length_mm > 0.0 and sheet_width_mm > 0.0:
        blank_length_mm = sheet_length_mm
        blank_width_mm = sheet_width_mm
        blank_basis = "manufacturing_sheet"
        blank_basis_reason = "full blank feasibility checks the one-up manufacturing sheet"

    if blank_length_mm <= 0.0 or blank_width_mm <= 0.0:
        blank_length_mm = _safe_float(dims.get("layout_blank_length_mm"), 0.0)
        blank_width_mm = _safe_float(dims.get("layout_blank_width_mm"), 0.0)
        blank_basis = "layout_blank_fallback"
        blank_basis_reason = "computed blank geometry unavailable; fell back to effective layout blank dimensions"

    if blank_length_mm <= 0.0 or blank_width_mm <= 0.0:
        blank_basis = "unavailable"
        blank_basis_reason = "no positive blank geometry is available for option generation"

    return {
        "box_length_mm": _safe_float(dims.get("box_length_mm"), 0.0),
        "box_width_mm": _safe_float(dims.get("box_width_mm"), 0.0),
        "box_height_mm": _safe_float(dims.get("box_height_mm"), 0.0),
        "blank_length_mm": blank_length_mm,
        "blank_width_mm": blank_width_mm,
        "blank_basis": blank_basis,
        "blank_basis_reason": blank_basis_reason,
        "rotation_allowed": bool(dims.get("rotation_allowed", True)),
        "rotation_source": dims.get("rotation_source") or "default_true",
    }


def _legacy_stage_slitting_option_eligibility_fallback(
    stage_code: Optional[str],
    stage_type: Optional[str],
    capability_code: Optional[str] = None,
) -> bool:
    """Backward-compatibility fallback only.

    Engine V2 should move toward fully configuration-driven slitting-option
    eligibility via stage-template fields. This helper intentionally preserves
    the legacy stage-name/stage-semantics heuristics only for templates that
    have not yet been explicitly configured.
    """
    code = str(stage_code or "").strip().lower()
    stage_type_key = str(stage_type or "").strip().lower()
    capability_key = str(capability_code or "").strip().lower()
    if code in ("2ply", "board", "cutting", "slitter", "slitting", "ts_cut"):
        return True
    if stage_type_key == "corrugation":
        return True
    if capability_key in ("cutting", "slitter", "slitting"):
        return True
    return False


def _configured_stage_slitting_option_eligibility(env, stage: RoutingStageRun) -> Tuple[bool, bool]:
    """Return `(is_configured, value)` for stage-template eligibility."""
    stage_template_id = _safe_int(getattr(stage, "stage_template_id", 0), 0)
    if stage_template_id <= 0:
        return False, False

    stage_model = None
    try:
        stage_model = env["corr.engine.stage.template"].sudo()
    except Exception:
        stage_model = None
    if stage_model is None:
        return False, False

    try:
        template = stage_model.browse(stage_template_id)
    except Exception:
        template = None
    if not template:
        return False, False

    try:
        if bool(getattr(template, "consider_slitting_options_configured", False)):
            return True, bool(getattr(template, "consider_slitting_options", False))
    except Exception:
        return False, False
    return False, False


def _resolve_stage_slitting_option_eligibility(env, stage: RoutingStageRun) -> bool:
    """Resolve final slitting-option eligibility for runtime/UI use.

    Resolution order:
    1. Explicit stage-template configuration
    2. Backward-compatibility fallback helper
    """
    is_configured, configured_value = _configured_stage_slitting_option_eligibility(env, stage)
    if is_configured:
        return configured_value

    return _legacy_stage_slitting_option_eligibility_fallback(
        stage.stage_code,
        stage.stage_type,
        stage.capability_code,
    )


def _stage_drives_layout_selection(env, stage_template_id: Any) -> bool:
    """Return whether a stage template is explicitly marked as the layout driver."""
    template_id = _safe_int(stage_template_id, 0)
    if template_id <= 0:
        return False

    stage_model = None
    try:
        stage_model = env["corr.engine.stage.template"].sudo()
    except Exception:
        stage_model = None
    if stage_model is None:
        return False

    try:
        template = stage_model.browse(template_id)
    except Exception:
        template = None
    if not template:
        return False

    try:
        return bool(getattr(template, "drives_layout_selection", False))
    except Exception:
        return False


def _resolve_layout_driving_stage_entry(env, stage_entries: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Resolve the single user-facing stage for layout/slitting selection."""
    ordered_entries = sorted(
        [dict(item) for item in stage_entries or []],
        key=lambda item: (
            _safe_int(item.get("sequence"), 0),
            _safe_int(item.get("stage_template_id"), 0),
        ),
    )
    if not ordered_entries:
        return None, None

    explicit_entries = [
        item
        for item in ordered_entries
        if _stage_drives_layout_selection(env, item.get("stage_template_id"))
    ]
    if explicit_entries:
        return explicit_entries[0], "explicit_primary_layout_stage"
    return ordered_entries[0], "first_eligible_layout_stage_fallback"


def _resolve_stage_option_explicit_max_ups(
    env,
    *,
    candidates: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[int], Optional[str]]:
    workcenter_limits = sorted(
        {
            _safe_int(item.get("max_slitting_ups"), 0)
            for item in (candidates or [])
            if _safe_int(item.get("max_slitting_ups"), 0) > 0
        }
    )
    if workcenter_limits:
        return max(workcenter_limits), "workcenter.max_slitting_ups"

    icp = None
    try:
        icp = env["ir.config_parameter"].sudo()
    except Exception:
        icp = None
    if icp is not None:
        try:
            limit = _safe_int(icp.get_param("corr_engine_v2.max_slitting_ups", "0"), 0)
        except Exception:
            limit = 0
        if limit > 0:
            return limit, "corr_engine_v2.max_slitting_ups"
    return SLITTING_OPTION_DEFAULT_MAX_UPS, "engine_default.max_slitting_ups"


def _candidate_supports_required_flutes(candidate: Dict[str, Any], required_flutes: Optional[List[str]]) -> bool:
    required = _normalize_flute_codes(required_flutes)
    if not required:
        return True
    supported = _normalize_flute_codes((candidate or {}).get("supported_flutes"))
    if not supported:
        return False
    return all(code in supported for code in required)


def _evaluate_slitting_option_candidate(
    candidate: Dict[str, Any],
    *,
    up_count: int,
    candidate_length_mm: float,
    candidate_width_mm: float,
    allow_rotation: bool,
) -> Dict[str, Any]:
    width_min_cm = _safe_float(candidate.get("width_min_cm"), 0.0)
    width_max_cm = _safe_float(candidate.get("width_max_cm"), 0.0)
    length_min_mm = _safe_float(candidate.get("length_min_mm"), 0.0)
    length_max_mm = _safe_float(candidate.get("length_max_mm"), 0.0)
    width_source = candidate.get("constraint_source") or "workcenter"
    length_source = candidate.get("constraint_source") or "workcenter"
    max_slitting_ups = _safe_int(candidate.get("max_slitting_ups"), 0)
    max_slitting_ups_source = candidate.get("max_slitting_ups_source") or "workcenter"
    required_flutes = _normalize_flute_codes(candidate.get("required_flutes"))
    supported_flutes = _normalize_flute_codes(candidate.get("supported_flutes"))
    missing_flutes = (
        [code for code in required_flutes if code not in supported_flutes]
        if required_flutes
        else []
    )

    orientation_checks: List[Dict[str, Any]] = []
    feasible_orientation = None
    checks = [
        {
            "orientation": "direct",
            "width_mm": candidate_width_mm,
            "length_mm": candidate_length_mm,
        }
    ]
    if allow_rotation and (candidate_length_mm != candidate_width_mm):
        checks.append(
            {
                "orientation": "rotated",
                "width_mm": candidate_length_mm,
                "length_mm": candidate_width_mm,
            }
        )

    for item in checks:
        width_mm = _safe_float(item.get("width_mm"), 0.0)
        length_mm = _safe_float(item.get("length_mm"), 0.0)
        rejection_reasons: List[str] = []

        if width_mm <= 0.0:
            rejection_reasons.append("missing_candidate_width_mm")
        if length_mm <= 0.0:
            rejection_reasons.append("missing_candidate_length_mm")
        if max_slitting_ups > 0 and up_count > max_slitting_ups:
            rejection_reasons.append(
                "UPS %s exceeds %s max_slitting_ups %s"
                % (up_count, max_slitting_ups_source, max_slitting_ups)
            )
        if missing_flutes:
            rejection_reasons.append(
                "required flute(s) %s not supported by workcenter flutes %s"
                % (", ".join(missing_flutes), ", ".join(supported_flutes or ["-"]))
            )

        width_cm = width_mm / 10.0 if width_mm > 0.0 else 0.0
        if width_min_cm > 0.0 and width_cm + 1e-6 < width_min_cm:
            rejection_reasons.append(
                "width %.1f cm below %s width minimum %.1f cm"
                % (width_cm, width_source, width_min_cm)
            )
        if width_max_cm > 0.0 and width_cm - 1e-6 > width_max_cm:
            rejection_reasons.append(
                "width %.1f mm exceeds %s width limit %.1f mm"
                % (width_mm, width_source, width_max_cm * 10.0)
            )
        if length_min_mm > 0.0 and length_mm + 1e-6 < length_min_mm:
            rejection_reasons.append(
                "length %.1f mm below %s length minimum %.1f mm"
                % (length_mm, length_source, length_min_mm)
            )
        if length_max_mm > 0.0 and length_mm - 1e-6 > length_max_mm:
            rejection_reasons.append(
                "length %.1f mm exceeds %s length limit %.1f mm"
                % (length_mm, length_source, length_max_mm)
            )

        orientation_checks.append(
            {
                "orientation": item.get("orientation") or "direct",
                "width_mm": width_mm,
                "length_mm": length_mm,
                "status": "feasible" if not rejection_reasons else "rejected",
                "rejection_reasons": rejection_reasons,
            }
        )
        if feasible_orientation is None and not rejection_reasons:
            feasible_orientation = item.get("orientation") or "direct"

    rejection_reasons = []
    if feasible_orientation is None:
        rejection_reasons = sorted(
            {
                reason
                for item in orientation_checks
                for reason in list(item.get("rejection_reasons") or [])
            }
        )

    return {
        **dict(candidate or {}),
        "status": "feasible" if feasible_orientation else "rejected",
        "rejection_reasons": rejection_reasons,
        "used_orientation": feasible_orientation,
        "rotation_used": feasible_orientation == "rotated",
        "dimension_checks": orientation_checks,
        "resolved_constraints": {
            "width_min_cm": width_min_cm or None,
            "width_max_cm": width_max_cm or None,
            "length_min_mm": length_min_mm or None,
            "length_max_mm": length_max_mm or None,
            "constraint_source": candidate.get("constraint_source") or "workcenter",
            "length_check_applied": bool(length_min_mm > 0.0 or length_max_mm > 0.0),
            "max_slitting_ups": max_slitting_ups or None,
            "max_slitting_ups_source": max_slitting_ups_source if max_slitting_ups > 0 else None,
            "required_flutes": required_flutes,
            "supported_flutes": supported_flutes,
            "flute_check_applied": bool(required_flutes),
        },
    }


def _slitting_option_preference_tuple(option: Dict[str, Any]) -> Tuple[Any, ...]:
    up_count = _safe_int(option.get("up_count"), 0)
    rotated = bool(option.get("rotated_used"))
    length_mm = _safe_float(option.get("candidate_length_mm"), 0.0)
    width_mm = _safe_float(option.get("candidate_width_mm"), 0.0)
    area = length_mm * width_mm if length_mm > 0.0 and width_mm > 0.0 else float("inf")
    matched_workcenter = dict(option.get("matched_workcenter") or {})
    wc_name = str(matched_workcenter.get("workcenter_name") or "")
    wc_id = _safe_int(matched_workcenter.get("workcenter_id"), 0)
    # Lower tuple sorts higher preference:
    # 1) more ups
    # 2) prefer direct over rotated
    # 3) prefer smaller final sheet area
    # 4) deterministic workcenter tie-break
    return (-up_count, 1 if rotated else 0, area, wc_name, wc_id)


def _resolve_requested_layout_up_count(
    *,
    spec: Any = None,
    v2_spec: Any = None,
) -> Optional[int]:
    for record in (v2_spec, spec):
        if not record:
            continue
        try:
            forced = _safe_int(getattr(record, "forced_ups", 0), 0)
        except Exception:
            forced = 0
        if forced > 0:
            return forced
    return None


def _build_slitting_option_display_row(
    option: Dict[str, Any],
    *,
    rank: Optional[int] = None,
) -> Dict[str, Any]:
    matched_workcenter = dict(option.get("matched_workcenter") or {})
    return {
        "rank": rank,
        "up_count": _safe_int(option.get("up_count"), 0),
        "final_sheet_length_mm": _safe_float(option.get("candidate_length_mm"), 0.0),
        "final_sheet_width_mm": _safe_float(option.get("candidate_width_mm"), 0.0),
        "matched_workcenter": matched_workcenter or None,
        "orientation_used": option.get("orientation_used"),
        "rotated_used": bool(option.get("rotated_used")),
        "feasible": bool(option.get("feasible")),
        "rejection_reasons": list(option.get("rejection_reasons") or []),
    }


def _build_slitting_option_presentation(
    options: List[Dict[str, Any]],
    *,
    requested_up_count: Optional[int] = None,
) -> Dict[str, Any]:
    feasible_rows = [dict(item) for item in options or [] if item.get("feasible")]
    rejected_rows = [dict(item) for item in options or [] if not item.get("feasible")]

    ranked_feasible = sorted(feasible_rows, key=_slitting_option_preference_tuple)
    preferred_option_source = "engine_ranked_default"
    requested_up_count = _safe_int(requested_up_count, 0) or None
    if requested_up_count:
        preferred_matches = [
            option
            for option in ranked_feasible
            if _safe_int(option.get("up_count"), 0) == requested_up_count
        ]
        if preferred_matches:
            remaining = [
                option
                for option in ranked_feasible
                if _safe_int(option.get("up_count"), 0) != requested_up_count
            ]
            ranked_feasible = preferred_matches + remaining
            preferred_option_source = "requested_up_count"
        else:
            preferred_option_source = "requested_up_count_unavailable_fallback"

    feasible_display = [
        {
            **_build_slitting_option_display_row(option, rank=index),
            "preference_model": "higher_up_then_direct_then_smaller_sheet",
        }
        for index, option in enumerate(ranked_feasible, start=1)
    ]
    rejected_display = [
        _build_slitting_option_display_row(option, rank=None)
        for option in rejected_rows
    ]

    preferred_option = feasible_display[0] if feasible_display else None
    return {
        "preference_model": "higher_up_then_direct_then_smaller_sheet",
        "requested_up_count": requested_up_count,
        "preferred_option_source": preferred_option_source,
        "preferred_option": preferred_option,
        "feasible_options": feasible_display,
        "rejected_options": rejected_display,
    }


def _build_slitting_options(
    env,
    stages: List[RoutingStageRun],
    stage_contexts: List[StageContext],
    *,
    spec: Any = None,
    v2_spec: Any = None,
    engine_result: Any = None,
    full_blank_only: bool = False,
) -> Dict[str, Any]:
    context_by_key = {
        ("template", ctx.stage_template_id): ctx
        for ctx in stage_contexts or []
        if getattr(ctx, "stage_template_id", None)
    }
    for ctx in stage_contexts or []:
        if getattr(ctx, "stage_contract_code", None):
            context_by_key.setdefault(("contract", ctx.stage_contract_code), ctx)
        if getattr(ctx, "stage_code", None):
            context_by_key.setdefault(("code", ctx.stage_code), ctx)

    stage_entries: List[Dict[str, Any]] = []
    feasible_option_count = 0
    rejected_option_count = 0

    for stage in stages or []:
        if not _resolve_stage_slitting_option_eligibility(env, stage):
            continue
        ctx = (
            context_by_key.get(("template", stage.stage_template_id))
            or context_by_key.get(("contract", stage.stage_contract_code))
            or context_by_key.get(("code", stage.stage_code))
        )
        dims = _resolve_stage_option_blank_dimensions(
            stage,
            ctx,
            engine_result,
            spec=spec,
            v2_spec=v2_spec,
        )
        is_diecut_sheet_layout = _is_diecut_sheet_layout_context(
            spec=spec,
            v2_spec=v2_spec,
            engine_result=engine_result,
        )
        blank_length_mm = _safe_float(dims.get("blank_length_mm"), 0.0)
        blank_width_mm = _safe_float(dims.get("blank_width_mm"), 0.0)
        is_full_blank_sheet_layout = bool(
            full_blank_only
            and
            blank_length_mm > 0.0
            and blank_width_mm > 0.0
            and (dims.get("blank_basis") or "") != "unavailable"
        )
        required_flutes = (
            _required_corrugation_flutes_from_spec(spec)
            if _stage_requires_corrugation_flute(stage, ctx)
            else []
        )
        candidates = _collect_stage_option_workcenters(
            env,
            stage,
            ctx,
            required_flutes=required_flutes,
        )
        max_ups_candidates = [
            candidate
            for candidate in candidates
            if _candidate_supports_required_flutes(candidate, required_flutes)
        ]
        explicit_max_ups, explicit_max_ups_source = _resolve_stage_option_explicit_max_ups(
            env,
            candidates=max_ups_candidates,
        )

        options: List[Dict[str, Any]] = []
        iteration_stop_reason = None
        iteration_stop_up_count = 0
        iteration_limit = explicit_max_ups or None
        if blank_length_mm > 0.0 and blank_width_mm > 0.0 and candidates:
            up_count = 1
            hard_upper = 1 if is_full_blank_sheet_layout else (explicit_max_ups or SLITTING_OPTION_SAFETY_CAP)
            hard_upper = min(hard_upper, SLITTING_OPTION_SAFETY_CAP)
            if is_full_blank_sheet_layout:
                iteration_limit = 1
            while up_count <= hard_upper:
                candidate_length_mm = blank_length_mm
                candidate_width_mm = blank_width_mm if is_full_blank_sheet_layout else blank_width_mm * float(up_count)
                candidate_results = [
                    _evaluate_slitting_option_candidate(
                        candidate,
                        up_count=up_count,
                        candidate_length_mm=candidate_length_mm,
                        candidate_width_mm=candidate_width_mm,
                        allow_rotation=bool(dims.get("rotation_allowed", True)),
                    )
                    for candidate in candidates
                ]
                feasible_candidates = [
                    item for item in candidate_results if item.get("status") == "feasible"
                ]
                matched = feasible_candidates[0] if feasible_candidates else None
                rejection_reasons = [] if matched else sorted(
                    {
                        reason
                        for item in candidate_results
                        for reason in list(item.get("rejection_reasons") or [])
                    }
                )
                options.append(
                    {
                        "up_count": up_count,
                        "candidate_length_mm": candidate_length_mm,
                        "candidate_width_mm": candidate_width_mm,
                        "orientation_used": matched.get("used_orientation") if matched else None,
                        "rotated_used": bool(matched.get("rotation_used")) if matched else False,
                        "matched_workcenter": (
                            {
                                "workcenter_id": matched.get("workcenter_id"),
                                "workcenter_name": matched.get("workcenter_name"),
                            }
                            if matched
                            else None
                        ),
                        "feasible": bool(matched),
                        "rejection_reasons": rejection_reasons,
                        "candidate_results": candidate_results,
                    }
                )
                iteration_stop_up_count = up_count
                if not feasible_candidates:
                    iteration_stop_reason = "all_candidate_workcenters_rejected"
                    break
                if is_full_blank_sheet_layout:
                    iteration_stop_reason = "full_blank_manufacturing_sheet_checked"
                    break
                if explicit_max_ups and up_count >= explicit_max_ups:
                    iteration_stop_reason = "explicit_max_ups_reached"
                    break
                up_count += 1

            if iteration_stop_reason is None:
                iteration_stop_reason = "iteration_safety_cap_reached"
                iteration_limit = SLITTING_OPTION_SAFETY_CAP

        stage_feasible = sum(1 for item in options if item.get("feasible"))
        stage_rejected = len(options) - stage_feasible
        feasible_option_count += stage_feasible
        rejected_option_count += stage_rejected

        if blank_length_mm <= 0.0 or blank_width_mm <= 0.0:
            stage_status = "rejected"
            stage_reasons = ["missing_blank_geometry"]
            iteration_stop_reason = iteration_stop_reason or "missing_blank_geometry"
        elif not candidates:
            stage_status = "rejected"
            stage_reasons = ["no_candidate_workcenters"]
            iteration_stop_reason = iteration_stop_reason or "no_candidate_workcenters"
        else:
            stage_status = "feasible" if stage_feasible > 0 else "rejected"
            stage_reasons = [] if stage_feasible > 0 else sorted(
                {
                    reason
                    for option in options
                    for reason in list(option.get("rejection_reasons") or [])
                }
            )
            if iteration_stop_reason is None:
                iteration_stop_reason = "no_iterations_ran"

        requested_up_count = _resolve_requested_layout_up_count(
            spec=spec,
            v2_spec=v2_spec,
        )
        presentation = _build_slitting_option_presentation(
            options,
            requested_up_count=requested_up_count,
        )
        stage_entries.append(
            {
                "stage_template_id": stage.stage_template_id,
                "stage_contract_code": stage.stage_contract_code,
                "stage_code": stage.stage_code,
                "stage_name": stage.stage_name,
                "stage_type": stage.stage_type,
                "capability_code": stage.capability_code,
                "sequence": stage.sequence,
                "drives_layout_selection": _stage_drives_layout_selection(env, stage.stage_template_id),
                "status": stage_status,
                "rejection_reasons": stage_reasons,
                "blank_dimensions": dims,
                "blank_basis_used": dims.get("blank_basis"),
                "blank_basis_reason": dims.get("blank_basis_reason"),
                "required_flutes": required_flutes,
                "explicit_max_ups": explicit_max_ups,
                "explicit_max_ups_source": explicit_max_ups_source,
                "requested_up_count": requested_up_count,
                "full_blank_manufacturing_sheet_one_up": bool(is_full_blank_sheet_layout),
                "diecut_sheet_full_blank_one_up": bool(is_diecut_sheet_layout),
                "iteration_limit": iteration_limit,
                "iteration_stop_reason": iteration_stop_reason,
                "iteration_stop_up_count": iteration_stop_up_count,
                "candidate_workcenters": candidates,
                "options": options,
                "presentation": presentation,
                "preferred_option": presentation.get("preferred_option"),
                "feasible_options": presentation.get("feasible_options") or [],
                "rejected_options": presentation.get("rejected_options") or [],
            }
        )

    layout_driving_stage, layout_driver_source = _resolve_layout_driving_stage_entry(env, stage_entries)
    layout_driving_identity = None
    if layout_driving_stage:
        layout_driving_identity = (
            layout_driving_stage.get("stage_contract_code")
            or layout_driving_stage.get("stage_code")
            or str(layout_driving_stage.get("stage_template_id") or "")
        )
    for entry in stage_entries:
        entry["is_layout_driving_stage"] = bool(
            layout_driving_identity
            and (
                entry.get("stage_contract_code")
                or entry.get("stage_code")
                or str(entry.get("stage_template_id") or "")
            ) == layout_driving_identity
        )

    return {
        "stages": stage_entries,
        "layout_driving_stage": (
            {
                "stage_template_id": layout_driving_stage.get("stage_template_id"),
                "stage_contract_code": layout_driving_stage.get("stage_contract_code"),
                "stage_code": layout_driving_stage.get("stage_code"),
                "stage_name": layout_driving_stage.get("stage_name"),
                "stage_type": layout_driving_stage.get("stage_type"),
                "sequence": layout_driving_stage.get("sequence"),
                "selection_source": layout_driver_source,
            }
            if layout_driving_stage
            else None
        ),
        "summary": {
            "stage_count": len(stage_entries),
            "option_count": feasible_option_count + rejected_option_count,
            "feasible_option_count": feasible_option_count,
            "rejected_option_count": rejected_option_count,
            "preference_model": "higher_up_then_direct_then_smaller_sheet",
        },
    }


def _collect_stage_candidate_workcenters(env, stage: RoutingStageRun, context: Optional[StageContext]) -> List[Dict[str, Any]]:
    Workcenter = env["mrp.workcenter"].sudo()
    OpMap = env["corr.operation.map"].sudo()

    output_kind = str(getattr(context, "output_kind", "") or "").strip().lower()
    op_name = _stage_operation_name_for_feasibility(
        stage.stage_code,
        stage.stage_type,
        stage.capability_code,
        output_kind,
    )

    candidates: List[Dict[str, Any]] = []
    seen_keys = set()

    def _append_candidate(workcenter, *, source: str, mapping=None):
        wc_id = getattr(workcenter, "id", None)
        if not wc_id:
            return
        map_id = getattr(mapping, "id", None) if mapping else None
        key = (source, wc_id, map_id)
        if key in seen_keys:
            return
        seen_keys.add(key)
        mapping_deckle_min_cm = _safe_float(getattr(mapping, "deckle_min_cm", None) if mapping else None, 0.0)
        mapping_deckle_max_cm = _safe_float(getattr(mapping, "deckle_max_cm", None) if mapping else None, 0.0)
        if hasattr(workcenter, "get_corr_deckle_min_mm"):
            workcenter_deckle_min_cm = _safe_float(workcenter.get_corr_deckle_min_mm(), 0.0) / 10.0
        else:
            workcenter_deckle_min_cm = _safe_float(getattr(workcenter, "corr_deckle_min_cm", 0.0), 0.0)
        if hasattr(workcenter, "get_corr_deckle_max_mm"):
            workcenter_deckle_max_cm = _safe_float(workcenter.get_corr_deckle_max_mm(), 0.0) / 10.0
        else:
            workcenter_deckle_max_cm = _safe_float(getattr(workcenter, "corr_deckle_max_cm", 0.0), 0.0)

        if mapping_deckle_min_cm > 0.0:
            deckle_min_cm = mapping_deckle_min_cm
            deckle_min_source = "operation_map"
        elif workcenter_deckle_min_cm > 0.0:
            deckle_min_cm = workcenter_deckle_min_cm
            deckle_min_source = "workcenter"
        else:
            deckle_min_cm = 0.0
            deckle_min_source = None

        if mapping_deckle_max_cm > 0.0:
            deckle_max_cm = mapping_deckle_max_cm
            deckle_max_source = "operation_map"
        elif workcenter_deckle_max_cm > 0.0:
            deckle_max_cm = workcenter_deckle_max_cm
            deckle_max_source = "workcenter"
        else:
            deckle_max_cm = 0.0
            deckle_max_source = None

        candidates.append(
            {
                "source": source,
                "operation_name": op_name,
                "mapping_id": map_id,
                "workcenter_id": wc_id,
                "workcenter_name": getattr(workcenter, "display_name", None) or getattr(workcenter, "name", "") or "",
                "supported_flutes": _normalize_flute_codes(getattr(workcenter, "corr_flutes_supported", "")),
                "deckle_min_cm": deckle_min_cm or None,
                "deckle_max_cm": deckle_max_cm or None,
                "deckle_min_source": deckle_min_source,
                "deckle_max_source": deckle_max_source,
                "routing_bound": source.startswith("plant_binding"),
            }
        )

    direct_ids = []
    if getattr(stage, "routing_workcenter_id", None):
        direct_ids.append(stage.routing_workcenter_id)
    if getattr(stage, "workcenter_id", None):
        direct_ids.append(stage.workcenter_id)
    if direct_ids:
        for wc in Workcenter.browse(list(dict.fromkeys(direct_ids))):
            _append_candidate(wc, source="plant_binding")

    if op_name:
        mappings = OpMap.search(
            [("op_name", "=", op_name), ("active", "=", True), ("workcenter_id", "!=", False)],
            order="sequence, id",
        )
        for mapping in mappings:
            wc = mapping.workcenter_id
            if wc:
                _append_candidate(wc, source="operation_map", mapping=mapping)

    return candidates


def _build_stage_feasibility(
    env,
    stages: List[RoutingStageRun],
    stage_contexts: List[StageContext],
    plant_param: Any,
    spec: Any = None,
    v2_spec: Any = None,
    engine_result: Any = None,
) -> Dict[str, Any]:
    context_by_key = {
        ("template", ctx.stage_template_id): ctx
        for ctx in stage_contexts or []
        if getattr(ctx, "stage_template_id", None)
    }
    for ctx in stage_contexts or []:
        if getattr(ctx, "stage_contract_code", None):
            context_by_key.setdefault(("contract", ctx.stage_contract_code), ctx)
        if getattr(ctx, "stage_code", None):
            context_by_key.setdefault(("code", ctx.stage_code), ctx)

    entries: List[Dict[str, Any]] = []
    feasible_count = 0

    for stage in stages or []:
        ctx = (
            context_by_key.get(("template", stage.stage_template_id))
            or context_by_key.get(("contract", stage.stage_contract_code))
            or context_by_key.get(("code", stage.stage_code))
        )
        dims = _stage_dimension_payload(stage, ctx, engine_result, spec=spec, v2_spec=v2_spec)
        current_ups = _stage_current_ups(stage, engine_result)
        plant_width_limit_mm = _stage_width_limit_mm(stage, plant_param)
        plant_length_limit_mm = _stage_length_limit_mm(plant_param)
        required_flutes = (
            _required_corrugation_flutes_from_spec(spec)
            if _stage_requires_corrugation_flute(stage, ctx)
            else []
        )

        candidate_rows = _collect_stage_candidate_workcenters(env, stage, ctx)
        evaluated_candidates = [
            _evaluate_stage_candidate_feasibility(
                candidate,
                checked_dimensions=list(dims.get("checked_orientations") or []),
                plant_width_limit_mm=plant_width_limit_mm,
                plant_length_limit_mm=plant_length_limit_mm,
                required_flutes=required_flutes,
            )
            for candidate in candidate_rows
        ]

        if not evaluated_candidates:
            status = "rejected"
            rejection_reasons = ["no_candidate_workcenters"]
        else:
            feasible_candidates = [item for item in evaluated_candidates if item.get("status") == "feasible"]
            status = "feasible" if feasible_candidates else "rejected"
            rejection_reasons = [] if feasible_candidates else sorted(
                {
                    reason
                    for item in evaluated_candidates
                    for reason in list(item.get("rejection_reasons") or [])
                }
            )

        if status == "feasible":
            feasible_count += 1

        entries.append(
            {
                "stage_template_id": stage.stage_template_id,
                "stage_contract_code": stage.stage_contract_code,
                "stage_code": stage.stage_code,
                "stage_name": stage.stage_name,
                "stage_type": stage.stage_type,
                "capability_code": stage.capability_code,
                "sequence": stage.sequence,
                "status": status,
                "rejection_reasons": rejection_reasons,
                "operation_name": _stage_operation_name_for_feasibility(
                    stage.stage_code,
                    stage.stage_type,
                    stage.capability_code,
                    getattr(ctx, "output_kind", None),
                ),
                "current_dimensions": dims,
                "current_ups": current_ups,
                "required_flutes": required_flutes,
                "ups_limit": None,
                "ups_rejection_applied": False,
                "plant_constraints": {
                    "width_limit_mm": plant_width_limit_mm,
                    "length_limit_mm": plant_length_limit_mm,
                    "applied_as_fallback_only": True,
                },
                "candidates": evaluated_candidates,
            }
        )

    return {
        "stages": entries,
        "summary": {
            "stage_count": len(entries),
            "feasible_stage_count": feasible_count,
            "rejected_stage_count": len(entries) - feasible_count,
        },
    }


def build_shadow_runtime(
    env,
    spec,
    plant_param,
    qgraph,
    order_qty=None,
    v2_spec=None,
    engine_result=None,
    consumable_state=None,
) -> Dict[str, Any]:
    """Build Phase 8 stage-first runtime structures in shadow mode.

    This helper is intentionally read-only and must not affect current Engine V2
    behaviour. It inspects Phase 8 configuration models and returns a pure
    dictionary ready to be embedded under raw_json for debugging.
    """

    # Best-effort: never let shadow builder break preview.
    try:
        routing = _build_routing_instance(env, spec, plant_param, qgraph, v2_spec=v2_spec)
        stages = routing.stages

        effective_state = dict(consumable_state or {})
        if not effective_state and v2_spec is not None:
            try:
                if not bool(getattr(v2_spec, "use_plant_consumable_defaults", True)):
                    effective_state = effective_consumable_state(spec=v2_spec)
            except Exception:
                effective_state = {}
        material_reqs = _build_material_requirements(
            env,
            plant_param,
            stages,
            consumable_state=effective_state,
        )
        stage_cost_lines = _build_stage_cost_lines(stages)
        stage_contexts = _build_stage_contexts(env, stages)
        _attach_materials_to_stage_contexts(
            env,
            stage_contexts,
            material_reqs,
            consumable_state=effective_state,
        )
        _attach_engine_rm_requirements_to_stage_contexts(
            env,
            stage_contexts,
            engine_result,
            consumable_state=effective_state,
        )
        stage_feasibility = _build_stage_feasibility(
            env,
            stages,
            stage_contexts,
            plant_param,
            spec=spec,
            v2_spec=v2_spec,
            engine_result=engine_result,
        )
        slitting_options = _build_slitting_options(
            env,
            stages,
            stage_contexts,
            spec=spec,
            v2_spec=v2_spec,
            engine_result=engine_result,
            full_blank_only=bool(getattr(env, "context", {}).get("layout_planning_full_blank_only", False)),
        )

        quantity_report = None
        if engine_result is not None:
            try:
                quantity_report = _run_quantity_planner(
                    env,
                    stage_contexts,
                    engine_result,
                    order_qty=order_qty,
                    qgraph=qgraph,
                )
            except Exception:
                # Quantity planner is best-effort diagnostics only.
                quantity_report = None

        routing_payload = _serialize_routing_instance(routing)
        route_source = {
            "selection_source": routing.selection_source,
            **(routing.selection_details or {}),
        }

        payload: Dict[str, Any] = {
            "routing": routing_payload,
            "route_source": route_source,
            "material_requirements": [_asdict_safe(m) for m in material_reqs],
            "stage_cost_lines": [_asdict_safe(s) for s in stage_cost_lines],
            "stage_contexts": [_asdict_safe(ctx) for ctx in stage_contexts],
            "stage_feasibility": stage_feasibility,
            "slitting_options": slitting_options,
            "consumable_state": effective_state,
        }
        if quantity_report:
            payload["quantity_report"] = quantity_report

        return payload
    except Exception as exc:  # pragma: no cover - defensive
        # Import here to avoid hard dependency at import time.
        import logging

        _logger = logging.getLogger(__name__)
        _logger.exception("Phase 8 shadow runtime builder failed: %s", exc)
        return {"error": str(exc)}


def _build_routing_instance(env, spec, plant_param, qgraph, v2_spec=None) -> RoutingInstance:
    """Resolve a routing instance for shadow mode.

    Priority order:
    1) Engine V2 Routing Profile attached to the Engine V2 Spec (when provided).
    2) Routing Profile directly on the primary spec (for future compatibility).
    3) Phase 8 Routing Template from the Engine V2 Spec.
    4) Phase 8 Routing Template from the Plant.
    5) Global/default Phase 8 Routing Template (compatibility).
    """

    # 1) Prefer Engine V2 Routing Profile from v2_spec when available.
    routing_profile = None
    if v2_spec is not None:
        try:
            routing_profile = getattr(v2_spec, "routing_profile_id", None)
        except Exception:
            routing_profile = None

    # 2) Fallback: routing_profile_id directly on the primary spec, if present.
    if routing_profile is None:
        try:
            routing_profile = getattr(spec, "routing_profile_id", None)
        except Exception:
            routing_profile = None

    if routing_profile and _safe_getattr(routing_profile, "active", True):
        return _build_routing_instance_from_profile(env, routing_profile, plant_param, qgraph)

    # 3+) Fallback to Phase 8 routing templates (existing behaviour).
    Routing = env["corr.engine.routing.template"].sudo()
    Binding = env["corr.engine.plant.stage.binding"].sudo()

    # Strategy: prefer a system-default routing template when available.
    warnings: List[str] = []
    selection_source: str = "none"
    selection_details: Dict[str, Any] = {}

    routing_tmpl = None

    # 1) Spec override via Engine V2 Spec (when explicitly provided in the wizard).
    if v2_spec and _safe_getattr(v2_spec, "phase8_routing_template_id", False):
        tmpl = v2_spec.phase8_routing_template_id
        if _is_usable_record(tmpl) and _safe_getattr(tmpl, "active", True):
            routing_tmpl = tmpl
            selection_source = "spec"
            selection_details = {
                "source": "spec",
                "spec_id": v2_spec.id,
                "spec_name": v2_spec.display_name,
                "template_id": tmpl.id,
                "template_name": tmpl.display_name,
            }
        else:
            warnings.append(
                "Engine V2 Spec %s references an inactive Phase 8 routing template; "
                "falling back to plant/global routing."
                % (v2_spec.display_name,)
            )

    # 2) Plant default routing template when no spec override was resolved.
    if not routing_tmpl and plant_param and _safe_getattr(
        plant_param, "phase8_routing_template_id", False
    ):
        tmpl = plant_param.phase8_routing_template_id
        if _is_usable_record(tmpl) and _safe_getattr(tmpl, "active", True):
            routing_tmpl = tmpl
            selection_source = "plant"
            selection_details = {
                "source": "plant",
                "plant_id": plant_param.id,
                "plant_code": getattr(plant_param, "code", ""),
                "template_id": tmpl.id,
                "template_name": tmpl.display_name,
            }
        else:
            warnings.append(
                "Plant %s has an inactive Phase 8 routing template; "
                "falling back to global compatibility template."
                % (getattr(plant_param, "code", "N/A"),)
            )

    # 3) Global compatibility default when no spec/plant template is available.
    if not routing_tmpl:
        routing_tmpl = Routing.search(
            [("is_system_default", "=", True)], order="id asc", limit=1
        )
        if routing_tmpl:
            selection_source = "global_default"
            selection_details = {
                "source": "global_default",
                "template_id": routing_tmpl.id,
                "template_name": routing_tmpl.display_name,
                "seed_key": routing_tmpl.seed_key,
            }
            warnings.append(
                "Using global compatibility routing template %s (seed_key=%s) in "
                "Phase 8 shadow mode."
                % (routing_tmpl.display_name, routing_tmpl.seed_key or "N/A")
            )
        else:
            # Final fallback: any active routing template, shadow-only.
            routing_tmpl = Routing.search([("active", "=", True)], order="id asc", limit=1)
            if routing_tmpl:
                selection_source = "global_default"
                selection_details = {
                    "source": "global_default",
                    "template_id": routing_tmpl.id,
                    "template_name": routing_tmpl.display_name,
                    "seed_key": routing_tmpl.seed_key,
                }
                warnings.append(
                    "No system-default routing template marked; using first active "
                    "routing template %s for Phase 8 shadow mode only."
                    % (routing_tmpl.display_name,)
                )

    if not routing_tmpl:
        warnings.append(
            "No Engine Routing Template found; Phase 8 shadow routing is disabled."
        )
        return RoutingInstance(
            routing_template_id=None,
            routing_template_name=None,
            routing_template_code=None,
            seed_key=None,
            plant_id=plant_param.id if plant_param else None,
            stages=[],
            warnings=warnings,
            selection_source=selection_source,
            selection_details=selection_details,
        )

    stages: List[RoutingStageRun] = []
    for line in routing_tmpl.stage_ids:
        stage_tmpl = _safe_getattr(line, "stage_template_id", None)
        if not _is_usable_record(stage_tmpl):
            continue

        binding = None
        if plant_param:
            binding = Binding.search(
                [
                    ("plant_id", "=", plant_param.id),
                    ("stage_template_id", "=", stage_tmpl.id),
                ],
                limit=1,
            )

        input_node, output_node = _resolve_stage_io(stage_tmpl, qgraph)

        stages.append(
            RoutingStageRun(
                stage_template_id=stage_tmpl.id,
                stage_contract_code=_stage_contract_code(stage_tmpl, _safe_getattr(stage_tmpl, "code", "") or ""),
                stage_code=_safe_getattr(stage_tmpl, "code", "") or "",
                stage_name=_safe_getattr(stage_tmpl, "name", "") or "",
                stage_type=_safe_getattr(stage_tmpl, "stage_type", "") or "",
                capability_code=_resolved_capability_code(stage_tmpl, _safe_getattr(stage_tmpl, "stage_type", "") or ""),
                sequence=_safe_getattr(line, "sequence", 0) or _safe_getattr(stage_tmpl, "sequence", 0) or 10,
                is_optional=bool(_safe_getattr(line, "is_optional", False) or _safe_getattr(stage_tmpl, "is_optional", False)),
                input_quantity_node_key=input_node,
                output_quantity_node_key=output_node,
                plant_binding_id=binding.id if binding else None,
                workcenter_id=binding.workcenter_id.id if binding and binding.workcenter_id else None,
                routing_workcenter_id=binding.routing_workcenter_id.id
                if binding and binding.routing_workcenter_id
                else None,
            )
        )

    stages.sort(key=lambda s: (s.sequence, s.stage_code))

    return RoutingInstance(
        routing_template_id=routing_tmpl.id,
        routing_template_name=routing_tmpl.name or "",
        routing_template_code=routing_tmpl.code or "",
        seed_key=routing_tmpl.seed_key or None,
        plant_id=plant_param.id if plant_param else None,
        stages=stages,
        warnings=warnings,
        selection_source=selection_source,
        selection_details=selection_details,
    )


def _build_routing_instance_from_profile(env, routing_profile, plant_param, qgraph) -> RoutingInstance:
    """Build RoutingInstance from an Engine V2 Routing Profile.

    This keeps shadow routing aligned with the same routing source used by
    EngineSpecInput and StageGraph when routing_profile_id is present.
    """

    StageLine = env["corr.engine.routing.profile.stage"].sudo()
    Binding = env["corr.engine.plant.stage.binding"].sudo()
    StageTemplate = env["corr.engine.stage.template"].sudo()

    warnings: List[str] = []

    try:
        lines = StageLine.search(
            [
                ("routing_profile_id", "=", routing_profile.id),
                ("stage_template_id", "!=", False),
            ],
            order="sequence,id",
        )
    except Exception:
        lines = routing_profile.stage_ids.sorted(key=lambda l: (l.sequence, l.id)) if routing_profile.stage_ids else []

    stages: List[RoutingStageRun] = []

    for line in lines:
        stage_tmpl = _safe_getattr(line, "stage_template_id", None)
        has_template = _is_usable_record(stage_tmpl)
        line_stage_code = str(_safe_getattr(line, "stage_code", "") or "").strip()
        line_stage_type = str(_safe_getattr(line, "stage_type", "") or "").strip()
        if not has_template and line_stage_code:
            try:
                matched_template = StageTemplate.search([("code", "=", line_stage_code)], limit=1)
            except Exception:
                matched_template = None
            if _is_usable_record(matched_template):
                stage_tmpl = matched_template
                has_template = True
        stage_code = (
            str(_safe_getattr(stage_tmpl, "code", "") or "").strip()
            if has_template
            else line_stage_code
        )
        if not stage_code:
            continue
        if has_template and not _safe_getattr(stage_tmpl, "active", True):
            continue
        stage_type = (
            str(_safe_getattr(stage_tmpl, "stage_type", "") or "").strip()
            if has_template
            else _fallback_stage_type_from_code(stage_code, line_stage_type)
        )
        stage_name = (
            str(_safe_getattr(stage_tmpl, "name", "") or "").strip()
            if has_template
            else stage_code
        )
        stage_template_id = _safe_int(_safe_getattr(stage_tmpl, "id", 0), 0) if has_template else 0

        # Optional plant binding, reusing the same model as Phase 8 templates.
        binding = None
        if plant_param and has_template:
            binding = Binding.search(
                [
                    ("plant_id", "=", plant_param.id),
                    ("stage_template_id", "=", stage_tmpl.id),
                ],
                limit=1,
            )

        if has_template:
            input_node, output_node = _resolve_stage_io(stage_tmpl, qgraph)
            capability_code = _resolved_capability_code(stage_tmpl, stage_type)
            sequence = _safe_getattr(line, "sequence", 0) or _safe_getattr(stage_tmpl, "sequence", 0) or 10
            is_optional = bool(_safe_getattr(line, "is_optional", False) or _safe_getattr(stage_tmpl, "is_optional", False))
            stage_contract_code = _stage_contract_code(stage_tmpl, stage_code)
        else:
            input_node, output_node = _fallback_stage_io_from_code(stage_code, line_stage_type)
            capability_code = _resolved_capability_code(None, stage_type)
            sequence = _safe_getattr(line, "sequence", 0) or 10
            is_optional = bool(_safe_getattr(line, "is_optional", False))
            stage_contract_code = _stage_contract_code(None, stage_code)
            warnings.append(
                "Routing profile stage '%s' has an unreadable stage template; "
                "using the stored stage code for shadow layout feasibility."
                % stage_code
            )

        stages.append(
            RoutingStageRun(
                stage_template_id=stage_template_id,
                stage_contract_code=stage_contract_code,
                stage_code=stage_code,
                stage_name=stage_name,
                stage_type=stage_type,
                capability_code=capability_code,
                sequence=sequence,
                is_optional=is_optional,
                input_quantity_node_key=input_node,
                output_quantity_node_key=output_node,
                plant_binding_id=binding.id if binding else None,
                workcenter_id=binding.workcenter_id.id if binding and binding.workcenter_id else None,
                routing_workcenter_id=binding.routing_workcenter_id.id
                if binding and binding.routing_workcenter_id
                else None,
            )
        )

    stages.sort(key=lambda s: (s.sequence, s.stage_code))

    return RoutingInstance(
        routing_template_id=None,
        routing_template_name=routing_profile.name or "",
        routing_template_code=None,
        seed_key=None,
        plant_id=plant_param.id if plant_param else None,
        stages=stages,
        warnings=warnings,
        selection_source="routing_profile",
        selection_details={
            "source": "routing_profile",
            "routing_profile_id": routing_profile.id,
            "routing_profile_name": routing_profile.display_name,
        },
    )


def _resolve_stage_io(stage_tmpl, qgraph) -> Tuple[Optional[str], Optional[str]]:
    """Resolve input/output quantity nodes for a stage in a safe, read-only way."""

    input_key = None
    output_key = None
    if not _is_usable_record(stage_tmpl):
        return input_key, output_key

    try:
        io = stage_tmpl.io_template_ids.filtered(lambda r: _safe_getattr(r, "active", False))[:1]
    except Exception:
        io = []

    if io:
        io = io[0]
        input_key = _safe_getattr(io, "input_quantity_node_key", None) or None
        output_key = _safe_getattr(io, "output_quantity_node_key", None) or None

    # Fallback: use default_quantity_basis when explicit IO mapping is missing.
    if not input_key or not output_key:
        basis = _safe_getattr(stage_tmpl, "default_quantity_basis", None) or None
        input_key = input_key or basis
        output_key = output_key or basis

    # Final safety: ensure keys exist in the StageGraph when possible,
    # but never fail if they don't (this is diagnostic-only).
    try:
        nodes = getattr(qgraph, "nodes", {}) or {}
        if input_key and input_key not in nodes:
            input_key = input_key
        if output_key and output_key not in nodes:
            output_key = output_key
    except Exception:
        pass

    return input_key, output_key


def _stage_contract_code(stage_tmpl, fallback_stage_code: Optional[str] = None) -> str:
    if _is_usable_record(stage_tmpl):
        seed_key = _safe_getattr(stage_tmpl, "seed_key", None)
        if seed_key:
            return str(seed_key)
        stage_template_id = _safe_int(_safe_getattr(stage_tmpl, "id", 0), 0)
        if stage_template_id:
            return "stage_template:%s" % stage_template_id
    if fallback_stage_code:
        return "stage_code:%s" % fallback_stage_code
    return ""


def _resolved_capability_code(stage_tmpl=None, stage_type: Optional[str] = None) -> Optional[str]:
    capability = _safe_getattr(stage_tmpl, "capability_id", None) if _is_usable_record(stage_tmpl) else None
    capability_code = _safe_getattr(capability, "code", None) if _is_usable_record(capability) else None
    if capability_code:
        return capability_code
    return (stage_type or _safe_getattr(stage_tmpl, "stage_type", None) or None) or None


def _normalize_material_role(role: Optional[str]) -> str:
    return str(role or "").strip().lower()


def _legacy_aliases_for_semantic_role(semantic_role: Optional[str]) -> List[str]:
    semantic = str(semantic_role or "").strip().lower()
    alias_map = {
        "liner": ["liner"],
        "medium": ["medium"],
        "top_sheet_external": ["top_sheet_external", "top_roll"],
        "paper": ["paper"],
        "adhesive_corrugation": ["adhesive_corrugation", "adhesive"],
        "adhesive_board": ["adhesive_board", "adhesive"],
        "adhesive_fg": ["adhesive_fg", "adhesive"],
        "ink": ["ink", "ink_total", "print", "printing", "printing_ink"],
        "varnish": ["varnish"],
        "lamination": ["lamination", "lamination_film"],
        "wire": ["wire", "stitching_wire"],
        "tape": ["tape", "jointing_tape"],
        "sheet": ["sheet"],
        "board": ["board"],
        "box": ["box"],
        "component": ["component"],
    }
    return list(alias_map.get(semantic, [semantic] if semantic else []))


def _semantic_role_for_material(
    *,
    role: Optional[str],
    material_type: Optional[str],
    stage_type: Optional[str],
    capability_code: Optional[str],
    output_kind: Optional[str],
    output_stock_behavior: Optional[str],
    input_quantity_node_key: Optional[str],
    output_quantity_node_key: Optional[str],
) -> Optional[str]:
    role_key = _normalize_material_role(role)
    mtype = str(material_type or "").strip().lower()
    stage_family = str(capability_code or stage_type or "").strip().lower()
    output_kind_key = str(output_kind or "").strip().lower()
    output_stock_key = str(output_stock_behavior or "").strip().lower()
    input_node = str(input_quantity_node_key or "").strip().lower()
    output_node = str(output_quantity_node_key or "").strip().lower()

    if mtype == "structural":
        if "liner" in role_key:
            return "liner"
        if "medium" in role_key or "flute" in role_key:
            return "medium"
        if "top" in role_key or "topsheet" in role_key or "top_sheet" in role_key:
            return "top_sheet_external"
        return "paper"

    if mtype == "consumable":
        if "ink" in role_key or "print" in role_key:
            return "ink"
        if "varnish" in role_key or "coat" in role_key:
            return "varnish"
        if "lamination" in role_key or "film" in role_key:
            return "lamination"
        if "wire" in role_key or "stitch" in role_key:
            return "wire"
        if "tape" in role_key:
            return "tape"
        if "adhesive" in role_key or "gum" in role_key or "glue" in role_key:
            if output_stock_key == "fg" or output_kind_key in ("box", "fg") or stage_family in ("conversion", "fg"):
                return "adhesive_fg"
            if stage_family == "corrugation":
                if input_node == "raw_material":
                    return "adhesive_corrugation"
                if input_node == "board" or output_node == "board":
                    return "adhesive_board"
                return "adhesive_corrugation"
            return "adhesive_board"

    if mtype == "byproduct":
        return "component"

    return None


def _role_candidates_for_semantic_role(
    semantic_role: Optional[str],
    *,
    material_type: Optional[str],
    role: Optional[str],
) -> List[str]:
    semantic = str(semantic_role or "").strip().lower()
    mtype = str(material_type or "").strip().lower()
    role_key = _normalize_material_role(role)

    candidate_map = {
        "liner": ["RM_PAPER_LINER", "RM_PAPER_GENERIC"],
        "medium": ["RM_PAPER_MEDIUM", "RM_PAPER_GENERIC"],
        "top_roll": ["RM_PAPER_TOP", "RM_PAPER_LINER", "RM_PAPER_GENERIC"],
        "top_sheet_external": ["RM_PAPER_TOP", "RM_PAPER_GENERIC"],
        "paper": ["RM_PAPER_GENERIC"],
        "adhesive_corrugation": ["CONSUMABLE_ADHESIVE_CORRUGATION", "CONSUMABLE_ADHESIVE_BOARD"],
        "adhesive_board": ["CONSUMABLE_ADHESIVE_BOARD", "CONSUMABLE_ADHESIVE_CORRUGATION"],
        "adhesive_fg": ["CONSUMABLE_ADHESIVE_FG"],
        "ink": ["CONSUMABLE_INK", "CONSUMABLE_PRINTING_INK", "CONSUMABLE_PRINT"],
        "varnish": ["CONSUMABLE_VARNISH"],
        "lamination": ["CONSUMABLE_LAMINATION_FILM", "CONSUMABLE_VARNISH"],
        "wire": ["CONSUMABLE_STITCHING_WIRE", "CONSUMABLE_WIRE"],
        "tape": ["CONSUMABLE_JOINTING_TAPE", "CONSUMABLE_TAPE"],
        "sheet": ["INT_SHEET"],
        "board": ["INT_BOARD", "INT_2PLY_BOARD"],
        "box": ["INT_BOX_SEAMED"],
        "component": ["COMP_GENERIC"],
    }
    candidates = list(candidate_map.get(semantic, []))
    if candidates:
        return candidates

    if mtype == "structural":
        return ["RM_PAPER_GENERIC"]
    if mtype == "consumable":
        if "ink" in role_key or "print" in role_key:
            return ["CONSUMABLE_INK", "CONSUMABLE_PRINTING_INK", "CONSUMABLE_PRINT"]
        if "wire" in role_key or "stitch" in role_key:
            return ["CONSUMABLE_STITCHING_WIRE", "CONSUMABLE_WIRE"]
        if "tape" in role_key:
            return ["CONSUMABLE_JOINTING_TAPE", "CONSUMABLE_TAPE"]
        if "film" in role_key or "lamination" in role_key:
            return ["CONSUMABLE_LAMINATION_FILM", "CONSUMABLE_VARNISH"]
        if "varnish" in role_key or "coat" in role_key:
            return ["CONSUMABLE_VARNISH"]
        if "adhesive" in role_key or "gum" in role_key or "glue" in role_key:
            return [
                "CONSUMABLE_ADHESIVE_BOARD",
                "CONSUMABLE_ADHESIVE_CORRUGATION",
                "CONSUMABLE_ADHESIVE_FG",
            ]
    return ["COMP_GENERIC"]


def _stage_matches_attachment_rule(stage: RoutingStageRun, rule) -> bool:
    if rule.stage_template_ids:
        return stage.stage_template_id in rule.stage_template_ids.ids
    rule_stage_type = str(getattr(rule, "stage_type", "") or "").strip().lower()
    if not rule_stage_type:
        return True
    candidates = {
        str(getattr(stage, "stage_type", "") or "").strip().lower(),
        str(getattr(stage, "capability_code", "") or "").strip().lower(),
        str(getattr(stage, "stage_code", "") or "").strip().lower(),
        str(getattr(stage, "stage_name", "") or "").strip().lower(),
    }
    if rule_stage_type in candidates:
        return True
    aliases = {
        "printing": ("print", "ts_print", "top_sheet_print", "top sheet print"),
        "lamination": ("lamination", "laminat", "film"),
        "corrugation": ("corrugation", "corrugat", "2ply", "2-ply"),
        "conversion": ("conversion", "seaming", "jointing", "stitch", "glue", "diecut", "die-cut"),
        "diecut": ("diecut", "die-cut", "slot", "die"),
        "fg": ("fg", "finished", "seaming", "jointing"),
    }
    tokens = aliases.get(rule_stage_type, ())
    return any(token in candidate for candidate in candidates for token in tokens)


def _material_type_for_engine_rm_role(role: str) -> str:
    role_key = str(role or "").strip().lower()
    if role_key in {"liner", "medium", "top_roll", "top_sheet_external", "paper"}:
        return "structural"
    if (
        role_key in {"lamination", "lamination_film", "ink", "varnish"}
        or role_key.startswith("adhesive")
        or "glue" in role_key
        or "gum" in role_key
    ):
        return "consumable"
    return ""


def _build_material_requirements(
    env,
    plant_param,
    stages: List[RoutingStageRun],
    *,
    consumable_state: Optional[Dict[str, Any]] = None,
) -> List[MaterialRequirementShadow]:
    """Derive shadow material requirements per stage from attachment rules."""

    AttachmentRule = env["corr.engine.material.attachment.rule"].sudo()
    MaterialProfile = env["corr.engine.material.profile"].sudo()
    StageTemplate = env["corr.engine.stage.template"].sudo()

    rules = AttachmentRule.search(
        [
            ("active", "=", True),
            ("material_profile_id", "!=", False),
            "|",
            ("plant_id", "=", False),
            ("plant_id", "=", plant_param.id if plant_param else 0),
        ]
    )

    if not rules:
        return []

    result: List[MaterialRequirementShadow] = []
    template_ids = {stage.stage_template_id for stage in stages if stage.stage_template_id}
    templates_by_id = {tmpl.id: tmpl for tmpl in StageTemplate.browse(list(template_ids))}

    # Cache profiles for quick lookup.
    profiles_by_id = {
        p.id: p
        for p in MaterialProfile.browse(
            list({r.material_profile_id.id for r in rules if r.material_profile_id})
        )
    }

    for stage in stages:
        for rule in rules:
            profile = profiles_by_id.get(rule.material_profile_id.id)
            if not profile:
                continue

            # Stage match: explicit stage_template_ids wins; otherwise the
            # broad stage_type filter may match the technical stage type,
            # capability, code, or name family.
            if not _stage_matches_attachment_rule(stage, rule):
                continue

            # Build a shadow requirement compatible with existing RM concepts.
            product_tmpl = profile.product_tmpl_id
            material_type = profile.material_type or ""
            if consumable_state and material_type == "consumable" and not should_include_consumable(
                consumable_state,
                profile.role or "",
                material_type=material_type,
                category="consumable",
            ):
                continue
            stage_tmpl = templates_by_id.get(stage.stage_template_id)
            semantic_role = _semantic_role_for_material(
                role=profile.role or "",
                material_type=material_type,
                stage_type=stage.stage_type,
                capability_code=stage.capability_code,
                output_kind=getattr(stage_tmpl, "output_kind", None),
                output_stock_behavior=getattr(stage_tmpl, "output_stock_behavior", None),
                input_quantity_node_key=stage.input_quantity_node_key,
                output_quantity_node_key=stage.output_quantity_node_key,
            )
            result.append(
                MaterialRequirementShadow(
                    stage_template_id=stage.stage_template_id,
                    stage_contract_code=stage.stage_contract_code,
                    stage_code=stage.stage_code,
                    stage_type=stage.stage_type,
                    capability_code=stage.capability_code,
                    stage_seed_key=None,  # filled when needed in later phases
                    semantic_role=semantic_role,
                    legacy_role_aliases=_legacy_aliases_for_semantic_role(semantic_role),
                    material_profile_id=profile.id,
                    material_type=material_type,
                    role=profile.role or "",
                    product_tmpl_id=product_tmpl.id if product_tmpl else 0,
                    default_bucket=profile.default_bucket or None,
                    attach_quantity_basis=rule.attach_quantity_basis or "per_m2",
                    is_mandatory=bool(rule.is_mandatory),
                    rule_id=rule.id,
                    input_quantity_node_key=stage.input_quantity_node_key,
                    output_quantity_node_key=stage.output_quantity_node_key,
                )
            )

    return result


def _infer_input_role_code(
    req: MaterialRequirementShadow,
    ctx: Optional[StageContext] = None,
) -> Optional[str]:
    """Best-effort mapping from a MaterialRequirementShadow to an InputRole code.

    This function is intentionally conservative and heuristic: it attempts to
    classify structural vs consumable materials into the shared input role
    taxonomy. It must never raise and should fall back to generic roles when
    in doubt.
    """

    candidates = _role_candidates_for_semantic_role(
        req.semantic_role,
        material_type=req.material_type,
        role=req.role,
    )
    if ctx and ctx.input_roles:
        allowed_codes = {r.role_code for r in ctx.input_roles if r.role_code}
        for candidate in candidates:
            if candidate in allowed_codes:
                return candidate
    return candidates[0] if candidates else None


def _build_stage_cost_lines(stages: List[RoutingStageRun]) -> List[StageCostLineShadow]:
    """Prepare per-stage cost placeholders without touching real costing."""

    result: List[StageCostLineShadow] = []
    for stage in stages:
        bucket = stage.output_quantity_node_key or stage.input_quantity_node_key
        result.append(
            StageCostLineShadow(
                stage_code=stage.stage_code,
                stage_type=stage.stage_type,
                stage_seed_key=None,
                bucket=bucket,
                material_cost=0.0,
                operation_cost=0.0,
                total_cost=0.0,
                notes=None,
            )
        )
    return result


def _attach_materials_to_stage_contexts(
    env,
    contexts: List[StageContext],
    material_reqs: List[MaterialRequirementShadow],
    *,
    consumable_state: Optional[Dict[str, Any]] = None,
) -> None:
    """Attach material profiles to StageContexts in shadow mode.

    This helper uses Engine Material Attachment Rules (Phase 8) plus the
    Stage Template input role contract to attach materials / consumables to
    stages. It is best-effort and never affects Engine V2 core behaviour.
    """

    if not contexts or not material_reqs:
        return

    InputRole = env["corr.engine.input.role"].sudo()

    roles_by_code = {r.code: r for r in InputRole.search([])}
    ctx_by_template_id: Dict[int, StageContext] = {
        ctx.stage_template_id: ctx for ctx in contexts if ctx.stage_template_id
    }
    ctx_by_contract_code: Dict[str, StageContext] = {
        ctx.stage_contract_code: ctx for ctx in contexts if ctx.stage_contract_code
    }

    # Index input role snapshots per context for quick lookup.
    required_roles_per_ctx: Dict[int, List[StageInputRoleSnapshot]] = {}
    for ctx in contexts:
        required_roles_per_ctx[id(ctx)] = [
            r for r in ctx.input_roles if getattr(r, "requirement", "") == "required"
        ]

    # 1) Attach each material requirement to the appropriate context(s).
    for req in material_reqs:
        ctx = (
            ctx_by_template_id.get(req.stage_template_id)
            or ctx_by_contract_code.get(req.stage_contract_code or "")
        )

        role_code = _infer_input_role_code(req, ctx=ctx)
        role_rec = roles_by_code.get(role_code) if role_code else None
        category = role_rec.category if role_rec else None
        if consumable_state and category == "consumable" and not should_include_consumable(
            consumable_state,
            role_code or req.semantic_role or req.role or "",
            material_type=req.material_type or "",
            category=category,
        ):
            continue

        attachment = StageAttachment(
            stage_template_id=req.stage_template_id or None,
            stage_contract_code=req.stage_contract_code or None,
            capability_code=req.capability_code or None,
            role_code=role_code,
            semantic_role=req.semantic_role or None,
            legacy_role_aliases=list(req.legacy_role_aliases or []),
            category=category,
            source="material_profile_rule",
            material_profile_id=req.material_profile_id or None,
            product_tmpl_id=req.product_tmpl_id or None,
            rule_id=req.rule_id or None,
            material_type=req.material_type or None,
            default_bucket=req.default_bucket or None,
            input_quantity_node_key=req.input_quantity_node_key,
            output_quantity_node_key=req.output_quantity_node_key,
        )

        if ctx:
            # Check whether the stage explicitly allows this role.
            if role_code:
                allowed = any(r.role_code == role_code for r in ctx.input_roles)
            else:
                allowed = False

            if not allowed and ctx.input_roles:
                ctx.warnings.append(
                    f"unconfigured_input_role_attached:{role_code or req.role}"
                )

            if category == "consumable":
                ctx.consumable_attachments.append(attachment)
            elif category == "intermediate":
                ctx.intermediate_attachments.append(attachment)
            else:
                ctx.rm_attachments.append(attachment)
        else:
            # No matching StageContext for this stage contract in the current routing.
            # Record a generic warning on all contexts for diagnostics, but do
            # not raise or affect core behaviour.
            for c in contexts:
                c.warnings.append(
                    "material_without_stage_context:%s"
                    % (
                        req.stage_contract_code
                        or ("stage_template:%s" % req.stage_template_id if req.stage_template_id else "unknown")
                    )
                )
            # Only need to warn once; break for performance.
            break

    # 2) Validate required roles per stage: for each input rule marked required,
    #    ensure at least one attachment with that role exists.
    for ctx in contexts:
        rm_roles = {a.role_code for a in ctx.rm_attachments if a.role_code}
        cons_roles = {a.role_code for a in ctx.consumable_attachments if a.role_code}
        interm_roles = {a.role_code for a in ctx.intermediate_attachments if a.role_code}
        attached_roles = rm_roles | cons_roles | interm_roles

        for rule in required_roles_per_ctx.get(id(ctx), []):
            if rule.role_code and rule.role_code not in attached_roles:
                ctx.warnings.append(f"required_input_role_missing:{rule.role_code}")


def _attach_engine_rm_requirements_to_stage_contexts(
    env,
    contexts: List[StageContext],
    engine_result: Any,
    *,
    consumable_state: Optional[Dict[str, Any]] = None,
) -> None:
    """Expose Engine V2 RM rows as StageContext attachments for debug/runtime views."""

    if not contexts or engine_result is None:
        return

    try:
        rm_requirements = list(getattr(engine_result, "rm_requirements", []) or [])
    except Exception:
        rm_requirements = []
    if not rm_requirements:
        return

    InputRole = env["corr.engine.input.role"].sudo()
    roles_by_code = {r.code: r for r in InputRole.search([])}
    ctx_by_stage_code = {
        str(ctx.stage_code or ""): ctx
        for ctx in contexts
        if str(ctx.stage_code or "")
    }

    existing = set()
    for ctx in contexts:
        for attachment in list(ctx.rm_attachments or []) + list(ctx.consumable_attachments or []) + list(ctx.intermediate_attachments or []):
            existing.add(
                (
                    id(ctx),
                    str(attachment.semantic_role or ""),
                    str(attachment.role_code or ""),
                    int(attachment.product_tmpl_id or 0),
                    str(attachment.source or ""),
                )
            )

    for rm in rm_requirements:
        stage_key = str(getattr(rm, "stage_key", "") or "")
        ctx = ctx_by_stage_code.get(stage_key)
        if not ctx:
            continue
        role = str(getattr(rm, "role", "") or "").strip().lower()
        product_key = str(getattr(rm, "product_key", "") or "").strip().lower()
        metadata = dict(getattr(rm, "metadata", {}) or {})
        if consumable_state and not should_include_consumable(
            consumable_state,
            role,
            product_key=product_key,
            metadata=metadata,
            material_type=_material_type_for_engine_rm_role(role),
        ):
            continue
        semantic_role = role or str(metadata.get("semantic_role") or "").strip().lower()
        material_type = _material_type_for_engine_rm_role(semantic_role)
        role_code = _infer_input_role_code(
            MaterialRequirementShadow(
                stage_template_id=ctx.stage_template_id or 0,
                stage_contract_code=ctx.stage_contract_code or "",
                stage_code=ctx.stage_code or "",
                stage_type=ctx.stage_type or "",
                capability_code=ctx.capability_code or None,
                stage_seed_key=None,
                semantic_role=semantic_role or None,
                material_profile_id=0,
                material_type=material_type,
                role=role or product_key,
                product_tmpl_id=int(metadata.get("product_tmpl_id") or 0),
                default_bucket=None,
                attach_quantity_basis="engine_v2_qty",
                is_mandatory=False,
                rule_id=0,
                legacy_role_aliases=list(metadata.get("legacy_role_aliases") or []),
                input_quantity_node_key=ctx.input_quantity_node_key,
                output_quantity_node_key=ctx.output_quantity_node_key,
            ),
            ctx=ctx,
        )
        role_rec = roles_by_code.get(role_code) if role_code else None
        category = role_rec.category if role_rec else ("consumable" if material_type == "consumable" else ("raw" if material_type else None))
        product_tmpl_id = int(metadata.get("product_tmpl_id") or 0) or None
        identity = (id(ctx), semantic_role, str(role_code or ""), int(product_tmpl_id or 0), "engine_v2_rm_requirement")
        if identity in existing:
            continue
        existing.add(identity)
        attachment = StageAttachment(
            stage_template_id=ctx.stage_template_id or None,
            stage_contract_code=ctx.stage_contract_code or None,
            capability_code=ctx.capability_code or None,
            role_code=role_code,
            semantic_role=semantic_role or None,
            legacy_role_aliases=list(metadata.get("legacy_role_aliases") or []),
            category=category,
            source="engine_v2_rm_requirement",
            material_profile_id=None,
            product_tmpl_id=product_tmpl_id,
            rule_id=None,
            material_type=material_type or None,
            default_bucket=None,
            input_quantity_node_key=ctx.input_quantity_node_key,
            output_quantity_node_key=ctx.output_quantity_node_key,
        )
        if category == "consumable":
            ctx.consumable_attachments.append(attachment)
        elif category == "intermediate":
            ctx.intermediate_attachments.append(attachment)
        else:
            ctx.rm_attachments.append(attachment)


def _run_quantity_planner(
    env,
    contexts: List[StageContext],
    engine_result,
    order_qty=None,
    qgraph=None,
) -> Optional[Dict[str, Any]]:
    """Run Phase 4 quantity planner in shadow mode.

    This helper delegates to the pure-engine quantity planner module and
    then projects its results back onto StageContext instances. It never
    mutates Engine V2 core structures or affects live behaviour.
    """

    if not contexts or engine_result is None:
        return None

    try:
        # Import lazily to avoid hard dependency at module import time.
        from odoo.addons.corr_engine_v2.engine_v2 import quantity_planner as qp
    except Exception:
        return None

    metrics = getattr(engine_result, "metrics", None)

    # Prefer the EngineResult quantity_stage_graph when present; otherwise
    # fall back to the qgraph passed in from the debug wizard.
    qty_graph = getattr(engine_result, "quantity_stage_graph", None) or qgraph

    # Derive order quantity if not explicitly provided.
    eff_order_qty: float
    try:
        eff_order_qty = float(order_qty or 0.0)
    except Exception:
        eff_order_qty = 0.0

    if eff_order_qty <= 0.0 and qty_graph is not None:
        try:
            nodes = getattr(qty_graph, "nodes", {}) or {}
            fg_node = nodes.get("finished_goods")
            if fg_node is not None:
                eff_order_qty = float(getattr(fg_node, "qty", 0.0) or 0.0)
        except Exception:
            eff_order_qty = 0.0

    if eff_order_qty <= 0.0:
        # Nothing meaningful to plan without an order quantity.
        return None

    stage_chain: List[Dict[str, Any]] = []
    for ctx in contexts:
        stage_chain.append(
            {
                "index": ctx.index,
                "stage_template_id": ctx.stage_template_id,
                "stage_contract_code": ctx.stage_contract_code,
                "stage_code": ctx.stage_code,
                "stage_name": ctx.stage_name,
                "stage_type": ctx.stage_type,
                "execution_type": ctx.execution_type,
                "capability_code": ctx.capability_code,
                "input_quantity_node_key": ctx.input_quantity_node_key,
                "output_quantity_node_key": ctx.output_quantity_node_key,
                "consumes_previous_output": ctx.consumes_previous_output,
                "previous_output_required": ctx.previous_output_required,
                "output_kind": ctx.output_kind,
                "output_stock_behavior": ctx.output_stock_behavior,
                "can_be_terminal": ctx.can_be_terminal,
                "bom_mapping_policy": ctx.bom_mapping_policy,
                "quantity_profile_code": ctx.quantity_profile_code,
                "parent_material_index": ctx.parent_material_index,
            }
        )

    report = qp.run_quantity_planner(
        stage_chain=stage_chain,
        metrics=metrics,
        qgraph=qty_graph,
        order_qty=eff_order_qty,
    )

    if not isinstance(report, dict):
        return None

    per_stage = {s.get("index"): s for s in report.get("stages") or [] if isinstance(s, dict)}

    for ctx in contexts:
        data = per_stage.get(ctx.index)
        if not data:
            continue

        try:
            ctx.fg_demand_qty = float(data.get("fg_demand_qty", 0.0) or 0.0)
        except Exception:
            ctx.fg_demand_qty = 0.0

        try:
            ctx.target_output_qty = float(data.get("target_output_qty", 0.0) or 0.0)
        except Exception:
            ctx.target_output_qty = 0.0

        ctx.target_output_uom = data.get("target_output_uom") or None

        try:
            ctx.input_qty_from_prev = float(data.get("input_qty_from_prev", 0.0) or 0.0)
        except Exception:
            ctx.input_qty_from_prev = 0.0

        input_from_rm = data.get("input_qty_from_rm") or {}
        if isinstance(input_from_rm, dict):
            ctx.input_qty_from_rm = {str(k): float(v) for k, v in input_from_rm.items()}

        metrics_block = data.get("computed_metrics") or {}
        if isinstance(metrics_block, dict):
            ctx.computed_metrics = metrics_block

        output = data.get("output") or None
        if isinstance(output, dict) and output.get("quantity"):
            try:
                ctx.output_descriptor = StageOutput(
                    kind=str(output.get("kind") or ""),
                    quantity=float(output.get("quantity", 0.0) or 0.0),
                    uom=str(output.get("uom") or ""),
                    fg_equivalent_qty=float(output.get("fg_equivalent_qty", 0.0) or 0.0),
                    dimensions=dict(output.get("dimensions") or {}),
                    tags=list(output.get("tags") or []),
                )
            except Exception:
                ctx.output_descriptor = None

    return report


def _build_stage_contexts(env, stages: List[RoutingStageRun]) -> List[StageContext]:
    """Build structural StageContext objects from routing stages and templates.

    This function is intentionally read-only and does not compute any quantities
    or RM assignments. It is used only for debug JSON exposure in Phase 1/2.
    """

    if not stages:
        return []

    StageTemplate = env["corr.engine.stage.template"].sudo()

    template_ids = {s.stage_template_id for s in stages if s.stage_template_id}
    templates_by_id = {
        t.id: t for t in StageTemplate.browse(list(template_ids))
    }

    contexts: List[StageContext] = []

    last_material_index: Optional[int] = None
    last_material_code: Optional[str] = None

    for idx, stage in enumerate(stages):
        tmpl = templates_by_id.get(stage.stage_template_id)
        if not tmpl:
            stage_type = _fallback_stage_type_from_code(stage.stage_code, stage.stage_type)
            exec_type = "material" if stage_type in ("corrugation", "conversion", "fg") else "operation"
            warnings = ["stage_template_unavailable"]
            if exec_type == "material":
                parent_index = None
                parent_code = None
                last_material_index = idx
                last_material_code = stage.stage_code
            else:
                parent_index = last_material_index
                parent_code = last_material_code
                if last_material_index is None:
                    warnings.append("operation_stage_without_parent_material")

            ctx = StageContext(
                stage_template_id=stage.stage_template_id,
                stage_contract_code=stage.stage_contract_code or _stage_contract_code(None, stage.stage_code),
                stage_code=stage.stage_code,
                stage_name=stage.stage_name or stage.stage_code,
                stage_type=stage_type,
                sequence=stage.sequence,
                index=idx,
                is_optional=stage.is_optional,
                execution_type=exec_type,
                capability_code=stage.capability_code or _resolved_capability_code(None, stage_type),
                capability_name=None,
                input_quantity_node_key=stage.input_quantity_node_key,
                output_quantity_node_key=stage.output_quantity_node_key,
                consumes_previous_output=stage_type not in ("corrugation",),
                previous_output_required=stage_type not in ("corrugation", "fg"),
                output_kind="board" if stage_type == "corrugation" else ("box" if stage_type in ("conversion", "fg") else None),
                output_stock_behavior="intermediate" if stage_type in ("corrugation", "conversion") else ("fg" if stage_type == "fg" else None),
                can_be_terminal=stage_type == "fg",
                bom_mapping_policy=None,
                quantity_profile_code=None,
                quantity_profile_id=None,
                parent_material_index=parent_index,
                parent_material_stage_code=parent_code,
                input_roles=[],
                warnings=warnings,
            )
            contexts.append(ctx)
            continue

        stage_type = tmpl.stage_type or stage.stage_type or "auxiliary"

        # Execution type: prefer explicit template value, otherwise derive from stage_type.
        exec_type = tmpl.execution_type or (
            "material" if stage_type in ("corrugation", "conversion", "fg") else "operation"
        )

        warnings: List[str] = []

        if exec_type == "material":
            # Material stages form the backbone; they do not have a parent
            # material stage themselves.
            parent_index = None
            parent_code = None
            last_material_index = idx
            last_material_code = stage.stage_code
        else:
            parent_index = last_material_index
            parent_code = last_material_code
            if last_material_index is None:
                warnings.append(
                    "operation_stage_without_parent_material"
                )

        capability = tmpl.capability_id
        qp = tmpl.quantity_profile_id

        input_roles: List[StageInputRoleSnapshot] = []
        for rule in tmpl.input_rule_ids:
            role = rule.role_id
            if not role:
                continue
            input_roles.append(
                StageInputRoleSnapshot(
                    role_code=role.code or "",
                    category=role.category or "",
                    requirement=rule.requirement or "",
                    source=rule.source or "",
                    priority=int(rule.priority or 0),
                )
            )

        ctx = StageContext(
            stage_template_id=tmpl.id,
            stage_contract_code=_stage_contract_code(tmpl, stage.stage_code),
            stage_code=stage.stage_code,
            stage_name=stage.stage_name,
            stage_type=stage.stage_type,
            sequence=stage.sequence,
            index=idx,
            is_optional=stage.is_optional,
            execution_type=exec_type,
            capability_code=_resolved_capability_code(tmpl, stage.stage_type),
            capability_name=capability.name if capability else None,
            input_quantity_node_key=stage.input_quantity_node_key,
            output_quantity_node_key=stage.output_quantity_node_key,
            consumes_previous_output=bool(tmpl.consumes_previous_output),
            previous_output_required=bool(tmpl.previous_output_required),
            output_kind=tmpl.output_kind or None,
            output_stock_behavior=tmpl.output_stock_behavior or None,
            can_be_terminal=bool(tmpl.can_be_terminal),
            bom_mapping_policy=tmpl.bom_mapping_policy or None,
            quantity_profile_code=qp.code if qp else None,
            quantity_profile_id=qp.id if qp else None,
            parent_material_index=parent_index,
            parent_material_stage_code=parent_code,
            input_roles=input_roles,
            warnings=warnings,
        )
        contexts.append(ctx)

    return contexts


def _serialize_routing_instance(routing: RoutingInstance) -> Dict[str, Any]:
    payload = {
        "routing_template_id": routing.routing_template_id,
        "routing_template_name": routing.routing_template_name,
        "routing_template_code": routing.routing_template_code,
        "seed_key": routing.seed_key,
        "plant_id": routing.plant_id,
        "warnings": list(routing.warnings or []),
        "stages": [],
        "selection_source": routing.selection_source,
        "selection_details": dict(routing.selection_details or {}),
    }
    for s in routing.stages:
        payload["stages"].append(
            {
                "stage_template_id": s.stage_template_id,
                "stage_contract_code": s.stage_contract_code,
                "stage_code": s.stage_code,
                "stage_name": s.stage_name,
                "stage_type": s.stage_type,
                "capability_code": s.capability_code,
                "sequence": s.sequence,
                "is_optional": s.is_optional,
                "input_quantity_node_key": s.input_quantity_node_key,
                "output_quantity_node_key": s.output_quantity_node_key,
                "plant_binding_id": s.plant_binding_id,
                "workcenter_id": s.workcenter_id,
                "routing_workcenter_id": s.routing_workcenter_id,
            }
        )
    return payload


def _asdict_safe(obj) -> Dict[str, Any]:
    try:
        data = asdict(obj)
    except Exception:
        # Fallback when dataclasses.asdict fails unexpectedly.
        data = dict(obj.__dict__)
    # Ensure all values are JSON-safe; avoid recordsets or complex objects.
    for key, val in list(data.items()):
        if hasattr(val, "_name"):
            # Likely a record; keep only its id.
            data[key] = getattr(val, "id", None)
        elif isinstance(val, (list, tuple)):
            cleaned = []
            for item in val:
                if hasattr(item, "_name"):
                    cleaned.append(getattr(item, "id", None))
                else:
                    cleaned.append(item)
            data[key] = cleaned
    return data



