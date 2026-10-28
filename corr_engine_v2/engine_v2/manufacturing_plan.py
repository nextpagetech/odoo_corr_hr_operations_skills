from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .consumables import should_include_consumable


_logger = logging.getLogger(__name__)

PLAN_VERSION = "7B-A.1"
MATERIALIZATION_PLAN_VERSION = "7B-C1.1"
MATERIALIZATION_MODES = {"single_bom", "phantom", "stockable", "mto"}


@dataclass(frozen=True)
class ManufacturingPlanWarning:
    code: str
    segment_key: Optional[str] = None
    message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManufacturingInputRole:
    role_code: str
    category: str
    requirement: str
    source: str
    priority: int = 0


@dataclass(frozen=True)
class ManufacturingInput:
    input_key: str
    role_code: Optional[str]
    semantic_role: Optional[str]
    category: Optional[str]
    source: str
    material_profile_id: Optional[int] = None
    product_tmpl_id: Optional[int] = None
    rule_id: Optional[int] = None
    material_type: Optional[str] = None
    default_bucket: Optional[str] = None
    input_quantity_node_key: Optional[str] = None
    output_quantity_node_key: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManufacturingOutput:
    output_key: str
    output_role: str  # "sfg", "fg", or "non_stock"
    output_kind: Optional[str]
    stock_behavior: Optional[str]
    qty: float
    uom: Optional[str]
    descriptor: Dict[str, Any] = field(default_factory=dict)
    identity_fingerprint: str = ""
    product_hint: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ManufacturingSegment:
    key: str
    sequence: int
    index: int
    stage_template_id: Optional[int]
    stage_contract_code: str
    stage_code: str
    stage_name: str
    execution_type: str
    capability_code: Optional[str]
    stage_type: Optional[str]
    input_qty_from_prev: float
    target_output_qty: float
    target_output_uom: Optional[str]
    output_kind: Optional[str]
    output_stock_behavior: Optional[str]
    output_descriptor: Dict[str, Any] = field(default_factory=dict)
    output: Optional[ManufacturingOutput] = None
    input_roles: List[ManufacturingInputRole] = field(default_factory=list)
    material_inputs: List[ManufacturingInput] = field(default_factory=list)
    consumable_inputs: List[ManufacturingInput] = field(default_factory=list)
    intermediate_inputs: List[ManufacturingInput] = field(default_factory=list)
    parent_material_key: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManufacturingEdge:
    edge_type: str  # "previous_output", "rm_input", "consumable_input", "intermediate_input"
    from_key: Optional[str]
    to_key: str
    output_key: Optional[str] = None
    input_key: Optional[str] = None
    role_code: Optional[str] = None
    qty: float = 0.0
    uom: Optional[str] = None
    source: str = "stagecontext"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManufacturingPlan:
    plan_version: str
    source: str
    spec_id: Optional[int]
    order_qty: float
    fg_segment_key: Optional[str]
    segments: List[ManufacturingSegment] = field(default_factory=list)
    edges: List[ManufacturingEdge] = field(default_factory=list)
    warnings: List[ManufacturingPlanWarning] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MaterializedProductCandidate:
    candidate_key: str
    segment_key: str
    product_role: str  # "fg" or "sfg"
    corr_sfg_key: Optional[str]
    corr_segment_key: str
    corr_plan_signature: str
    corr_output_fingerprint: str
    corr_material_signature: str
    corr_route_signature: str
    corr_layout_signature: str
    corr_resolution_signature: str
    stock_mode: str
    product_type_hint: str
    route_policy: str
    uom: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MaterializedBomLineCandidate:
    line_key: str
    bom_key: str
    line_type: str  # "previous_output", "rm_input", "consumable_input", "intermediate_input"
    product_candidate_key: Optional[str] = None
    source_segment_key: Optional[str] = None
    input_key: Optional[str] = None
    role_code: Optional[str] = None
    qty: float = 0.0
    uom: Optional[str] = None
    operation_segment_key: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MaterializedOperationCandidate:
    operation_candidate_key: str
    bom_key: str
    segment_key: str
    operation_key: Optional[str]
    operation_name: Optional[str]
    stage_name: Optional[str]
    sequence: int
    input_qty: float
    output_qty: float
    uom: Optional[str]
    stage_contract_code: str
    stage_template_id: Optional[int]
    stage_code: str
    workcenter_id: Optional[int]
    routing_workcenter_id: Optional[int]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MaterializedBomCandidate:
    bom_key: str
    segment_key: str
    product_candidate_key: Optional[str]
    bom_role: str  # "fg", "sfg", or "single_bom"
    bom_type: str  # "normal" or "phantom"
    product_qty: float
    corr_segment_key: str
    corr_plan_signature: str
    corr_output_fingerprint: str
    corr_material_signature: str
    corr_route_signature: str
    corr_layout_signature: str
    corr_resolution_signature: str
    lines: List[MaterializedBomLineCandidate] = field(default_factory=list)
    operations: List[MaterializedOperationCandidate] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManufacturingMaterializationPlan:
    materialization_version: str
    source_plan_version: str
    mode: str
    corr_plan_signature: str
    fg_segment_key: Optional[str]
    products: List[MaterializedProductCandidate] = field(default_factory=list)
    boms: List[MaterializedBomCandidate] = field(default_factory=list)
    warnings: List[ManufacturingPlanWarning] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_manufacturing_plan_from_stagecontexts(
    stage_contexts: Iterable[Any],
    *,
    spec_id: Optional[int] = None,
    order_qty: float = 0.0,
    metadata: Optional[Dict[str, Any]] = None,
) -> ManufacturingPlan:
    """Build a pure manufacturing plan from StageContext-like objects.

    The builder is intentionally duck-typed so callers can pass real
    StageContext dataclasses, plain dictionaries, or test fixtures. It does
    not import Odoo, create records, or mutate the input objects.
    """

    contexts = list(stage_contexts or [])
    warnings: List[ManufacturingPlanWarning] = []
    segments: List[ManufacturingSegment] = []
    edges: List[ManufacturingEdge] = []
    raw_parent_keys: List[Optional[str]] = []
    consumable_state = dict((metadata or {}).get("consumable_state") or {})

    for fallback_index, ctx in enumerate(contexts):
        segment_key = _segment_key(ctx, fallback_index)
        output_descriptor = _object_to_dict(_get(ctx, "output_descriptor", None))
        target_output_qty = _safe_float(_get(ctx, "target_output_qty", 0.0), 0.0)
        target_output_uom = _none_if_blank(_get(ctx, "target_output_uom", None))
        execution_type = _norm(_get(ctx, "execution_type", ""))
        output_stock_behavior = _norm(_get(ctx, "output_stock_behavior", ""))
        output_kind = _none_if_blank(
            output_descriptor.get("kind") or _get(ctx, "output_kind", None)
        )
        stage_warnings = [str(item) for item in _as_list(_get(ctx, "warnings", []))]

        output = _build_output(
            ctx,
            segment_key,
            execution_type=execution_type,
            output_stock_behavior=output_stock_behavior,
            output_kind=output_kind,
            target_output_qty=target_output_qty,
            target_output_uom=target_output_uom,
            output_descriptor=output_descriptor,
            warnings=warnings,
            stage_warnings=stage_warnings,
        )

        input_roles = [_build_input_role(item) for item in _as_list(_get(ctx, "input_roles", []))]
        material_inputs = [
            _build_input(item, segment_key, "rm", idx)
            for idx, item in enumerate(_as_list(_get(ctx, "rm_attachments", [])))
        ]
        consumable_inputs = [
            _build_input(item, segment_key, "consumable", idx)
            for idx, item in enumerate(_as_list(_get(ctx, "consumable_attachments", [])))
        ]
        if consumable_state:
            consumable_inputs = [
                item
                for item in consumable_inputs
                if should_include_consumable(
                    consumable_state,
                    item.semantic_role or item.role_code or "",
                    metadata=item.metadata or {},
                    material_type=item.material_type or "",
                    category=item.category or "",
                )
            ]
        intermediate_inputs = [
            _build_input(item, segment_key, "intermediate", idx)
            for idx, item in enumerate(_as_list(_get(ctx, "intermediate_attachments", [])))
        ]

        for item in material_inputs:
            edges.append(_input_edge("rm_input", item, segment_key))
        for item in consumable_inputs:
            edges.append(_input_edge("consumable_input", item, segment_key))
        for item in intermediate_inputs:
            edges.append(_input_edge("intermediate_input", item, segment_key))

        raw_parent_keys.append(_parent_material_identity(ctx))
        segments.append(
            ManufacturingSegment(
                key=segment_key,
                sequence=_safe_int(_get(ctx, "sequence", 0), 0),
                index=_safe_int(_get(ctx, "index", fallback_index), fallback_index),
                stage_template_id=_safe_optional_int(_get(ctx, "stage_template_id", None)),
                stage_contract_code=str(_get(ctx, "stage_contract_code", "") or ""),
                stage_code=str(_get(ctx, "stage_code", "") or ""),
                stage_name=str(_get(ctx, "stage_name", "") or ""),
                execution_type=execution_type,
                capability_code=_none_if_blank(_get(ctx, "capability_code", None)),
                stage_type=_none_if_blank(_get(ctx, "stage_type", None)),
                input_qty_from_prev=_safe_float(_get(ctx, "input_qty_from_prev", 0.0), 0.0),
                target_output_qty=target_output_qty,
                target_output_uom=target_output_uom,
                output_kind=output_kind,
                output_stock_behavior=output_stock_behavior or None,
                output_descriptor=output_descriptor,
                output=output,
                input_roles=input_roles,
                material_inputs=material_inputs,
                consumable_inputs=consumable_inputs,
                intermediate_inputs=intermediate_inputs,
                parent_material_key=None,
                warnings=stage_warnings,
                metadata={
                    "consumes_previous_output": bool(_get(ctx, "consumes_previous_output", False)),
                    "previous_output_required": bool(_get(ctx, "previous_output_required", False)),
                    "can_be_terminal": bool(_get(ctx, "can_be_terminal", False)),
                    "bom_mapping_policy": _none_if_blank(_get(ctx, "bom_mapping_policy", None)),
                    "quantity_profile_code": _none_if_blank(_get(ctx, "quantity_profile_code", None)),
                    "quantity_profile_id": _safe_optional_int(_get(ctx, "quantity_profile_id", None)),
                    "operation_key": _none_if_blank(_get(ctx, "_plan_operation_key", None)),
                    "operation_name": _none_if_blank(_get(ctx, "_plan_operation_name", None)),
                    "operation_metrics": dict(_get(ctx, "_plan_operation_metrics", {}) or {}),
                    "workcenter_id": _safe_optional_int(_get(ctx, "workcenter_id", None)),
                    "routing_workcenter_id": _safe_optional_int(_get(ctx, "routing_workcenter_id", None)),
                },
            )
        )

    key_by_parent_identity = _parent_identity_index(contexts, segments)
    segments = [
        _segment_with_parent_key(segment, key_by_parent_identity.get(raw_parent_keys[idx]))
        for idx, segment in enumerate(segments)
    ]
    segments = _apply_style_family_terminal_semantics(segments, metadata or {})

    edges.extend(_previous_output_edges(contexts, segments, warnings))
    fg_segment_key = _fg_segment_key(segments)

    return ManufacturingPlan(
        plan_version=PLAN_VERSION,
        source="stagecontext",
        spec_id=spec_id,
        order_qty=_safe_float(order_qty, 0.0),
        fg_segment_key=fg_segment_key,
        segments=segments,
        edges=edges,
        warnings=warnings,
        metadata=dict(metadata or {}),
    )


def build_manufacturing_plan_from_stagecontext_preview_payload(
    payload: Dict[str, Any],
) -> ManufacturingPlan:
    """Build a ManufacturingPlan from the current StageContext preview payload.

    Preview material and operation rows are copied into the plan as read-only
    parity context. No Odoo APIs are imported or called.
    """

    shadow_runtime = payload.get("phase8_shadow_runtime") or {}
    preview_model = payload.get("phase5_preview_model") or {}
    stage_contexts = [dict(item) for item in list(shadow_runtime.get("stage_contexts") or [])]
    preview_materials = list(preview_model.get("materials") or [])
    preview_operations = list(preview_model.get("operations") or [])
    preview_routed_operations = list(preview_model.get("routed_operations") or [])
    preview_stages = list(preview_model.get("stages") or [])
    paper_resolution_by_key = _paper_resolution_lookup(payload.get("paper_resolution") or [])
    consumable_state = dict(
        payload.get("consumable_state")
        or (payload.get("phase8_shadow_runtime") or {}).get("consumable_state")
        or preview_model.get("consumable_state")
        or {}
    )

    ctx_by_identity: Dict[Any, Dict[str, Any]] = {}
    for ctx in stage_contexts:
        _index_by_stage_identity(ctx_by_identity, ctx, ctx)

    preview_ops_by_key: Dict[str, Dict[str, Any]] = {}
    for item in preview_operations:
        op_key = _norm(_get(item, "stage", ""))
        if op_key and op_key not in preview_ops_by_key:
            preview_ops_by_key[op_key] = item

    preview_stages_by_identity = _identity_lookup(preview_stages)
    preview_routed_by_identity = _identity_lookup(preview_routed_operations)
    preview_operation_stage_by_identity = _operation_stage_identity_lookup(preview_operations)

    for ctx in stage_contexts:
        op_key = _operation_key_from_stage_context_dict(ctx)
        metrics = _operation_metrics_from_preview(
            ctx,
            op_key,
            preview_ops_by_key,
            preview_stages_by_identity,
            preview_routed_by_identity,
            preview_operation_stage_by_identity,
        )
        ctx["_plan_operation_key"] = op_key
        ctx["_plan_operation_name"] = _operation_name_from_key(op_key)
        ctx["_plan_operation_metrics"] = metrics
        if (
            str(metrics.get("yield_basis") or "").strip().lower() == "fg_per_sheet"
            and bool(ctx.get("consumes_previous_output") or ctx.get("previous_output_required"))
        ):
            input_qty = _safe_float(metrics.get("input_qty"), 0.0)
            if input_qty > 0.0:
                ctx["input_qty_from_prev"] = input_qty
        _promote_yield_operation_context_for_materialization(ctx, metrics)
        _promote_stock_output_operation_context_for_materialization(ctx, metrics)
    _rebalance_fg_per_sheet_peeloff_chain(stage_contexts)
    _promote_upstream_sheet_operations_before_yield(stage_contexts)

    for row in preview_materials:
        if not isinstance(row, dict):
            continue
        ctx = _lookup_by_stage_identity(ctx_by_identity, row)
        fallback_metadata: Dict[str, Any] = {}
        if not ctx:
            ctx, fallback_metadata = _fallback_context_for_material_row(stage_contexts, row)
            if not ctx:
                continue
        enriched_row = _enrich_preview_material_with_resolution(row, paper_resolution_by_key)
        if fallback_metadata:
            enriched_row = {**enriched_row, **fallback_metadata}
            enriched_metadata = dict(enriched_row.get("metadata") or {})
            enriched_metadata.update(fallback_metadata)
            enriched_row["metadata"] = enriched_metadata
        attachment = _preview_material_as_attachment(enriched_row)
        category = _norm(row.get("category") or row.get("material_type"))
        if category == "consumable":
            if (
                consumable_state
                and (
                    _safe_float(row.get("total_qty") or row.get("qty_per_fg"), 0.0) <= 0.0
                    or not should_include_consumable(
                        consumable_state,
                        row.get("role") or "",
                        metadata=row.get("metadata") or {},
                        material_type=row.get("material_type") or "",
                        category=row.get("category") or "",
                    )
                )
            ):
                continue
            ctx.setdefault("consumable_attachments", []).append(attachment)
        else:
            ctx.setdefault("rm_attachments", []).append(attachment)

    return build_manufacturing_plan_from_stagecontexts(
        stage_contexts,
        spec_id=_safe_optional_int(payload.get("spec_id")),
        order_qty=_safe_float(payload.get("order_qty"), 0.0),
        metadata={
            "plant_id": payload.get("plant_id"),
            "style_code": payload.get("style_code"),
            "style_family": _style_family_from_preview_payload(payload),
            "selected_layout": _selected_layout_from_payload(payload),
            "top_sheet_mode": _top_sheet_mode_from_preview_payload(payload),
            "top_sheet_roll_code": _top_sheet_roll_code_from_preview_payload(payload),
            "consumable_state": consumable_state,
        },
    )


def flatten_manufacturing_plan_to_single_bom_writer_payload(
    plan: ManufacturingPlan,
) -> Dict[str, Any]:
    """Flatten a ManufacturingPlan to the current single-BoM writer payload.

    SFG candidate outputs are ignored. All input edges are emitted as normal
    FG BoM component lines for parity with the current StageContext writer.
    """

    routed_operations = _flatten_plan_routed_operations(plan)
    writer_payload = {
        "product_qty": _safe_float(plan.order_qty, 0.0),
        "components": _flatten_plan_components(plan),
        "operations": _flatten_plan_grouped_operations(routed_operations),
        "routed_operations": routed_operations,
    }
    selected_layout = (plan.metadata or {}).get("selected_layout")
    if isinstance(selected_layout, dict) and selected_layout:
        writer_payload["selected_layout"] = selected_layout
    return writer_payload


def build_materialization_plan_from_manufacturing_plan(
    plan: ManufacturingPlan,
    *,
    mode: str = "stockable",
    identity_scope: str = "global",
) -> ManufacturingMaterializationPlan:
    """Build a dry-run materialization plan from a ManufacturingPlan.

    The result describes product/BoM/line/operation candidates only. It never
    imports Odoo and never creates products, BoMs, or MOs.
    """

    mode_key = _norm(mode) or "stockable"
    warnings: List[ManufacturingPlanWarning] = []
    if mode_key not in MATERIALIZATION_MODES:
        warnings.append(
            ManufacturingPlanWarning(
                code="unsupported_materialization_mode",
                message="Unsupported mode; falling back to stockable.",
                metadata={"requested_mode": mode},
            )
        )
        mode_key = "stockable"

    plan_signature = _plan_signature(plan)
    segment_by_key = {segment.key: segment for segment in plan.segments}
    output_segment_by_key = {
        segment.key: segment
        for segment in plan.segments
        if segment.output and segment.output.output_role in ("sfg", "fg")
    }
    previous_edges = [edge for edge in plan.edges if edge.edge_type == "previous_output"]
    input_edges = [
        edge
        for edge in plan.edges
        if edge.edge_type in ("rm_input", "consumable_input", "intermediate_input")
    ]
    five_ply_decompositions = _five_ply_decompositions(plan)

    warnings.extend(_graph_warnings(plan, previous_edges))

    products: List[MaterializedProductCandidate] = []
    product_by_segment: Dict[str, MaterializedProductCandidate] = {}

    if mode_key == "single_bom":
        fg_segment = segment_by_key.get(plan.fg_segment_key or "") or _last_output_segment(plan)
        if fg_segment:
            product = _product_candidate_for_segment(
                plan,
                fg_segment,
                mode_key=mode_key,
                identity_scope=identity_scope,
                plan_signature=plan_signature,
                product_role="fg",
            )
            products.append(product)
            product_by_segment[fg_segment.key] = product
    else:
        for segment in plan.segments:
            if not segment.output or segment.output.output_role not in ("sfg", "fg"):
                continue
            if segment.key in five_ply_decompositions:
                continue
            product = _product_candidate_for_segment(
                plan,
                segment,
                mode_key=mode_key,
                identity_scope=identity_scope,
                plan_signature=plan_signature,
                product_role=segment.output.output_role,
            )
            products.append(product)
            product_by_segment[segment.key] = product
        for decomp in five_ply_decompositions.values():
            for product in _five_ply_product_candidates(plan, decomp, mode_key, identity_scope, plan_signature):
                products.append(product)
                product_by_segment[product.segment_key] = product

    operations_by_owner = _operation_owner_map(plan, output_segment_by_key)
    if mode_key == "single_bom":
        boms = _single_bom_candidates(
            plan,
            plan_signature,
            product_by_segment,
            input_edges,
            previous_edges,
            operations_by_owner,
        )
    else:
        boms = _multi_level_bom_candidates(
            plan,
            mode_key,
            plan_signature,
            product_by_segment,
            input_edges,
            previous_edges,
            operations_by_owner,
            five_ply_decompositions,
        )

    return ManufacturingMaterializationPlan(
        materialization_version=MATERIALIZATION_PLAN_VERSION,
        source_plan_version=plan.plan_version,
        mode=mode_key,
        corr_plan_signature=plan_signature,
        fg_segment_key=plan.fg_segment_key,
        products=products,
        boms=boms,
        warnings=warnings,
        metadata={
            "identity_scope": identity_scope,
            "dry_run": True,
            "graph_support": "linear_first",
            "selected_layout": (plan.metadata or {}).get("selected_layout") or {},
            "top_sheet_mode": (plan.metadata or {}).get("top_sheet_mode") or "",
            "top_sheet_roll_code": (plan.metadata or {}).get("top_sheet_roll_code") or "",
            "future_mo_chain": _future_mo_chain(plan, product_by_segment, previous_edges),
        },
    )


def _top_sheet_mode_from_preview_payload(payload: Dict[str, Any]) -> str:
    for value in (
        payload.get("top_sheet_mode"),
        ((payload.get("effective_inputs") or {}).get("top_sheet") or {}).get("effective_top_sheet_mode"),
        ((payload.get("effective_surface_inputs") or {}).get("top_sheet") or {}).get("effective_top_sheet_mode"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _top_sheet_roll_code_from_preview_payload(payload: Dict[str, Any]) -> str:
    for value in (
        payload.get("top_sheet_roll_code"),
        ((payload.get("effective_inputs") or {}).get("top_sheet") or {}).get("top_sheet_roll_code"),
        ((payload.get("effective_surface_inputs") or {}).get("top_sheet") or {}).get("top_sheet_roll_code"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def build_materialization_debug_payload_from_stagecontext_preview_payload(
    payload: Dict[str, Any],
    *,
    mode: str = "stockable",
    identity_scope: str = "global",
) -> Dict[str, Any]:
    """Return a JSON-friendly dry-run materialization payload for debug views.

    This is a pure projection over the preview payload. It creates no products,
    BoMs, MOs, or ORM records.
    """

    plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)
    materialized = build_materialization_plan_from_manufacturing_plan(
        plan,
        mode=mode,
        identity_scope=identity_scope,
    )
    selected_layout = (plan.metadata or {}).get("selected_layout") or {}
    return {
        "enabled": True,
        "dry_run": True,
        "mode": materialized.mode,
        "identity_scope": identity_scope,
        "layout_diagnostics": _layout_diagnostics(selected_layout, materialized),
        "manufacturing_plan": plan.to_dict(),
        "materialization_plan": materialized.to_dict(),
    }


def _single_bom_candidates(
    plan: ManufacturingPlan,
    plan_signature: str,
    product_by_segment: Dict[str, MaterializedProductCandidate],
    input_edges: List[ManufacturingEdge],
    previous_edges: List[ManufacturingEdge],
    operations_by_owner: Dict[str, List[ManufacturingSegment]],
) -> List[MaterializedBomCandidate]:
    fg_segment = (
        {segment.key: segment for segment in plan.segments}.get(plan.fg_segment_key or "")
        or _last_output_segment(plan)
    )
    if not fg_segment:
        return []
    product = product_by_segment.get(fg_segment.key)
    lines, diagnostic_lines = _candidate_lines_from_input_edges(
        input_edges,
        fg_segment.key,
        "bom:single",
        plan,
    )
    operations = [
        _operation_candidate_from_segment(op_segment, "bom:single")
        for op_segment in _all_operation_segments_for_single_bom(plan)
    ]
    signatures = _segment_signatures(plan, fg_segment, plan_signature)
    bom_product_qty = _bom_product_qty_for_segment(fg_segment)
    return [
        MaterializedBomCandidate(
            bom_key="bom:single",
            segment_key=fg_segment.key,
            product_candidate_key=product.candidate_key if product else None,
            bom_role="single_bom",
            bom_type="normal",
            product_qty=bom_product_qty,
            corr_segment_key=fg_segment.key,
            lines=lines,
            operations=operations,
            metadata={
                "ignored_previous_output_edges": len(previous_edges),
                "diagnostic_only_lines": diagnostic_lines,
                **_layout_batch_metadata(plan, fg_segment, _segment_output_qty(fg_segment, plan.order_qty)),
            },
            **signatures,
        )
    ]


def _multi_level_bom_candidates(
    plan: ManufacturingPlan,
    mode_key: str,
    plan_signature: str,
    product_by_segment: Dict[str, MaterializedProductCandidate],
    input_edges: List[ManufacturingEdge],
    previous_edges: List[ManufacturingEdge],
    operations_by_owner: Dict[str, List[ManufacturingSegment]],
    five_ply_decompositions: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[MaterializedBomCandidate]:
    five_ply_decompositions = five_ply_decompositions or {}
    incoming_inputs: Dict[str, List[Tuple[ManufacturingEdge, str]]] = {}
    incoming_previous: Dict[str, List[ManufacturingEdge]] = {}
    outgoing_previous: Dict[str, List[ManufacturingEdge]] = {}
    segment_by_key = {segment.key: segment for segment in plan.segments}
    output_segment_by_key = {
        segment.key: segment
        for segment in plan.segments
        if segment.output and segment.output.output_role in ("sfg", "fg")
    }
    input_owner_by_segment = _materialized_owner_by_segment(plan, output_segment_by_key)
    for edge in input_edges:
        owner_key = input_owner_by_segment.get(edge.to_key)
        if not owner_key:
            continue
        source_segment = segment_by_key.get(edge.to_key)
        operation_segment_key = owner_key
        if source_segment and (source_segment.metadata or {}).get("operation_key"):
            operation_segment_key = source_segment.key
        incoming_inputs.setdefault(owner_key, []).append((edge, operation_segment_key))
    incoming_inputs = _filter_explicit_top_sheet_duplicate_inputs(
        incoming_inputs,
        plan,
        segment_by_key,
    )
    for edge in previous_edges:
        incoming_previous.setdefault(edge.to_key, []).append(edge)
        outgoing_previous.setdefault(edge.from_key or "", []).append(edge)

    boms: List[MaterializedBomCandidate] = []
    for decomp in five_ply_decompositions.values():
        boms.extend(
            _five_ply_subassembly_boms(
                plan,
                decomp,
                mode_key,
                plan_signature,
                product_by_segment,
                operations_by_owner,
            )
        )
    output_segments = [
        segment
        for segment in plan.segments
        if segment.output and segment.output.output_role in ("sfg", "fg") and segment.key not in five_ply_decompositions
    ]
    for segment in output_segments:
        product = product_by_segment.get(segment.key)
        bom_key = f"bom:{segment.key}"
        lines = []
        diagnostic_lines = []
        for edge in incoming_previous.get(segment.key, []):
            decomp = five_ply_decompositions.get(edge.from_key or "")
            if decomp:
                for sub_line in _five_ply_previous_lines_for_board(decomp, bom_key, segment.key, product_by_segment):
                    if sub_line.product_candidate_key and sub_line.qty > 0.0:
                        lines.append(sub_line)
                    else:
                        diagnostic_lines.append(
                            _diagnostic_line_from_candidate(
                                sub_line,
                                "previous_output_missing_product_or_qty",
                            )
                        )
                continue
            source_product = product_by_segment.get(edge.from_key or "")
            source_segment = segment_by_key.get(edge.from_key or "")
            previous_line = MaterializedBomLineCandidate(
                line_key=f"{bom_key}:previous:{edge.from_key or 'missing'}",
                bom_key=bom_key,
                line_type="previous_output",
                product_candidate_key=source_product.candidate_key if source_product else None,
                source_segment_key=edge.from_key,
                qty=_previous_output_component_qty(edge, source_segment, segment),
                uom=edge.uom,
                operation_segment_key=segment.key,
                metadata={
                    **dict(edge.metadata or {}),
                    **_previous_output_qty_debug_metadata(edge, source_segment, segment, plan),
                },
            )
            if previous_line.product_candidate_key and previous_line.qty > 0.0:
                lines.append(previous_line)
            else:
                diagnostic_lines.append(
                    _diagnostic_line_from_candidate(
                        previous_line,
                        "previous_output_missing_product_or_qty",
                    )
                )

        input_edges_for_segment = incoming_inputs.get(segment.key, [])
        input_edges_for_segment = _filter_five_ply_board_inputs(
            input_edges_for_segment,
            segment.key,
            five_ply_decompositions,
            plan,
        )
        input_lines, input_diagnostics = _candidate_lines_from_input_edges(
            input_edges_for_segment,
            segment.key,
            bom_key,
            plan,
        )
        lines.extend(input_lines)
        diagnostic_lines.extend(input_diagnostics)
        for decomp in five_ply_decompositions.values():
            if decomp.get("board_segment_key") != segment.key:
                continue
            remaining_lines, remaining_diagnostics = _candidate_lines_from_input_edges(
                [(edge, segment.key) for edge in decomp.get("remaining_liner_edges") or []],
                segment.key,
                bom_key,
                plan,
            )
            lines.extend(remaining_lines)
            diagnostic_lines.extend(remaining_diagnostics)

        operations = [
            _operation_candidate_from_segment(op_segment, bom_key)
            for op_segment in operations_by_owner.get(segment.key, [])
        ]
        signatures = _segment_signatures(plan, segment, plan_signature)
        bom_product_qty = _bom_product_qty_for_segment_with_previous_sources(
            segment,
            incoming_previous.get(segment.key, []),
            outgoing_previous.get(segment.key, []),
            segment_by_key,
        )
        boms.append(
            MaterializedBomCandidate(
                bom_key=bom_key,
                segment_key=segment.key,
                product_candidate_key=product.candidate_key if product else None,
                bom_role=segment.output.output_role,
                bom_type="phantom" if mode_key == "phantom" and segment.output.output_role == "sfg" else "normal",
                product_qty=bom_product_qty,
                corr_segment_key=segment.key,
                lines=lines,
                operations=operations,
                metadata={
                    "diagnostic_only_lines": diagnostic_lines,
                    **_layout_batch_metadata(plan, segment, _segment_output_qty(segment, 1.0)),
                },
                **signatures,
            )
        )
    return boms


def _future_mo_chain(
    plan: ManufacturingPlan,
    product_by_segment: Dict[str, MaterializedProductCandidate],
    previous_edges: List[ManufacturingEdge],
) -> Dict[str, Any]:
    child_keys_by_parent: Dict[str, List[str]] = {}
    parent_key_by_child: Dict[str, str] = {}
    for edge in previous_edges:
        if not edge.from_key or not edge.to_key:
            continue
        child_keys_by_parent.setdefault(edge.to_key, []).append(edge.from_key)
        parent_key_by_child[edge.from_key] = edge.to_key

    nodes = []
    for segment in plan.segments:
        product = product_by_segment.get(segment.key)
        if not product:
            continue
        nodes.append(
            {
                "segment_key": segment.key,
                "stage_contract_code": segment.stage_contract_code,
                "product_candidate_key": product.candidate_key,
                "product_role": product.product_role,
                "mo_role": "fg_mo" if product.product_role == "fg" else "child_mo_candidate",
                "parent_segment_key": parent_key_by_child.get(segment.key),
                "child_segment_keys": list(child_keys_by_parent.get(segment.key) or []),
            }
        )

    root_segment_key = plan.fg_segment_key
    if not root_segment_key:
        fg_nodes = [node for node in nodes if node["product_role"] == "fg"]
        root_segment_key = fg_nodes[-1]["segment_key"] if fg_nodes else None
    return {
        "dry_run_only": True,
        "writes_enabled": False,
        "root_segment_key": root_segment_key,
        "nodes": nodes,
    }


def _five_ply_decompositions(plan: ManufacturingPlan) -> Dict[str, Dict[str, Any]]:
    input_by_key = _input_by_key(plan)
    edges_by_segment: Dict[str, List[ManufacturingEdge]] = {}
    for edge in plan.edges:
        if edge.edge_type == "rm_input":
            edges_by_segment.setdefault(edge.to_key, []).append(edge)
    result: Dict[str, Dict[str, Any]] = {}
    for segment in plan.segments:
        if not segment.output or segment.output.output_role != "sfg" or not _is_2ply_segment(segment):
            continue
        rm_edges = edges_by_segment.get(segment.key) or []
        resolved_edges = _dedupe_resolved_material_edges(
            [edge for edge in rm_edges if _edge_has_resolved_material(edge, input_by_key)],
            input_by_key,
        )
        liner_edges = [edge for edge in resolved_edges if _input_role(input_by_key.get(edge.input_key or "")) == "liner"]
        medium_edges = [edge for edge in resolved_edges if _input_role(input_by_key.get(edge.input_key or "")) == "medium"]
        if len(liner_edges) < 1 or len(medium_edges) < 2:
            continue
        expected_liner_layers = _expected_five_ply_liner_layers(liner_edges, medium_edges, input_by_key)
        used_liner_layers = set()
        board_segment_key = _downstream_board_segment_key(plan, segment.key)
        prefer_actual_liner_layers = _board_segment_has_explicit_top_roll(plan, board_segment_key)
        subassemblies = []
        for medium_edge in sorted(
            medium_edges,
            key=lambda edge: _input_layer_no(input_by_key.get(edge.input_key or "")) or 9999,
        ):
            medium_layer_no = _input_layer_no(input_by_key.get(medium_edge.input_key or ""))
            # Engine V2 stack order treats layer 1 as the board-stage surface liner.
            # For 5-ply decomposition, each medium should pair with the immediately
            # following liner: (2,3) and (4,5). The surface liner remains on board.
            expected_liner_layer = medium_layer_no + 1 if medium_layer_no and medium_layer_no > 0 else 0
            if expected_liner_layer <= 0:
                expected_liner_layer = _nearest_unused_liner_layer(
                    medium_layer_no,
                    expected_liner_layers,
                    used_liner_layers,
                )
            liner_edge = _representative_liner_edge_for_layer(
                expected_liner_layer,
                liner_edges,
                input_by_key,
                used_liner_layers if prefer_actual_liner_layers else None,
            )
            if not liner_edge:
                continue
            actual_liner_layer = _input_layer_no(input_by_key.get(liner_edge.input_key or ""))
            if prefer_actual_liner_layers:
                if actual_liner_layer > 0:
                    used_liner_layers.add(actual_liner_layer)
            elif expected_liner_layer > 0:
                used_liner_layers.add(expected_liner_layer)
            signature = _two_ply_subassembly_signature(plan, segment, liner_edge, medium_edge)
            subassemblies.append(
                {
                    "signature": signature,
                    "segment_key": "%s:2ply:%s" % (segment.key, signature[:12]),
                    "source_segment_key": segment.key,
                    "liner_edge": liner_edge,
                    "medium_edge": medium_edge,
                    "liner_layer_no": (
                        actual_liner_layer
                        if prefer_actual_liner_layers
                        else (expected_liner_layer or actual_liner_layer)
                    ),
                    "medium_layer_no": medium_layer_no,
                }
            )
        if len(subassemblies) < 2:
            continue
        grouped: Dict[str, Dict[str, Any]] = {}
        for sub in subassemblies:
            current = grouped.setdefault(
                sub["signature"],
                {**sub, "qty": 0.0, "instances": []},
            )
            current["qty"] += 1.0
            current["instances"].append(sub)
        remaining_liner_edges = [
            edge
            for edge in liner_edges
            if (_input_layer_no(input_by_key.get(edge.input_key or "")) or 0) not in used_liner_layers
        ]
        result[segment.key] = {
            "source_segment_key": segment.key,
            "board_segment_key": board_segment_key,
            "groups": list(grouped.values()),
            "remaining_liner_edges": remaining_liner_edges,
            "used_liner_layers": sorted(used_liner_layers),
        }
    return result


def _filter_five_ply_board_inputs(
    edges: List[Tuple[ManufacturingEdge, str]],
    segment_key: str,
    five_ply_decompositions: Dict[str, Dict[str, Any]],
    plan: ManufacturingPlan,
) -> List[Tuple[ManufacturingEdge, str]]:
    used_layers = set()
    for decomp in five_ply_decompositions.values():
        if decomp.get("board_segment_key") == segment_key:
            used_layers.update(_safe_int(layer, 0) for layer in decomp.get("used_liner_layers") or [])
    if not used_layers:
        return edges
    input_by_key = _input_by_key(plan)
    result = []
    for edge, operation_segment_key in edges:
        item = input_by_key.get(edge.input_key or "")
        layer_no = _input_layer_no(item)
        role = _input_role(item)
        if layer_no in used_layers and role in {"liner", "top_roll", "top_sheet", "top_sheet_external"}:
            continue
        result.append((edge, operation_segment_key))
    return result


def _dedupe_resolved_material_edges(
    edges: List[ManufacturingEdge],
    input_by_key: Dict[str, ManufacturingInput],
) -> List[ManufacturingEdge]:
    result: List[ManufacturingEdge] = []
    seen = set()
    for edge in edges:
        item = input_by_key.get(edge.input_key or "")
        metadata = dict(item.metadata or {}) if item else {}
        preview_metadata = dict(metadata.get("preview_metadata") or {})
        identity = (
            _input_role(item) or edge.role_code,
            preview_metadata.get("layer_no") or preview_metadata.get("source_layer_no"),
            metadata.get("writer_product_key") or metadata.get("product_id") or (item.product_tmpl_id if item else None),
            _safe_float(metadata.get("writer_qty"), 0.0),
            metadata.get("writer_uom"),
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(edge)
    return result


def _edge_has_resolved_material(
    edge: ManufacturingEdge,
    input_by_key: Dict[str, ManufacturingInput],
) -> bool:
    item = input_by_key.get(edge.input_key or "")
    if not item:
        return False
    metadata = dict(item.metadata or {})
    if metadata.get("trace_only"):
        return False
    qty = _safe_float(metadata.get("writer_qty"), 0.0)
    product_key = metadata.get("writer_product_key") or metadata.get("product_id") or item.product_tmpl_id
    uom = metadata.get("writer_uom")
    return qty > 0.0 and bool(product_key) and bool(uom)


def _expected_five_ply_liner_layers(
    liner_edges: List[ManufacturingEdge],
    medium_edges: List[ManufacturingEdge],
    input_by_key: Dict[str, ManufacturingInput],
) -> List[int]:
    medium_layers = [
        _input_layer_no(input_by_key.get(edge.input_key or ""))
        for edge in medium_edges
        if _input_layer_no(input_by_key.get(edge.input_key or "")) > 0
    ]
    available_liner_layers = {
        _input_layer_no(input_by_key.get(edge.input_key or ""))
        for edge in liner_edges
        if _input_layer_no(input_by_key.get(edge.input_key or "")) > 0
    }
    layers = set()
    for layer_no in medium_layers:
        expected_layer = layer_no + 1
        if expected_layer in available_liner_layers:
            layers.add(expected_layer)
    if not layers:
        layers = {layer for layer in available_liner_layers if layer > 1}
    return sorted(layer for layer in layers if layer > 0)


def _nearest_unused_liner_layer(
    medium_layer_no: int,
    expected_liner_layers: List[int],
    used_liner_layers: set,
) -> int:
    candidates = [layer for layer in expected_liner_layers if layer not in used_liner_layers]
    if not candidates:
        candidates = list(expected_liner_layers)
    if not candidates:
        return 0
    return sorted(candidates, key=lambda layer: (abs(layer - medium_layer_no), layer))[0]


def _representative_liner_edge_for_layer(
    layer_no: int,
    liner_edges: List[ManufacturingEdge],
    input_by_key: Dict[str, ManufacturingInput],
    excluded_layers: Optional[set] = None,
) -> Optional[ManufacturingEdge]:
    if not liner_edges:
        return None
    excluded_layers = excluded_layers or set()
    if layer_no > 0:
        exact = [
            edge
            for edge in liner_edges
            if _input_layer_no(input_by_key.get(edge.input_key or "")) == layer_no
            and _input_layer_no(input_by_key.get(edge.input_key or "")) not in excluded_layers
        ]
        if exact:
            return exact[0]
    if layer_no > 0:
        preferred = [
            edge
            for edge in liner_edges
            if _input_layer_no(input_by_key.get(edge.input_key or "")) not in excluded_layers
        ] or list(liner_edges)
        return sorted(
            preferred,
            key=lambda edge: (
                abs((_input_layer_no(input_by_key.get(edge.input_key or "")) or layer_no) - layer_no),
                _input_layer_no(input_by_key.get(edge.input_key or "")) or 9999,
            ),
        )[0]
    return liner_edges[0]


def _board_segment_has_explicit_top_roll(plan: ManufacturingPlan, board_segment_key: Optional[str]) -> bool:
    if not board_segment_key:
        return False
    segment = next((seg for seg in plan.segments if seg.key == board_segment_key), None)
    if not segment:
        return False
    for item in segment.material_inputs or []:
        metadata = dict(item.metadata or {})
        preview_metadata = dict(metadata.get("preview_metadata") or {})
        role = _norm(
            item.semantic_role
            or item.role_code
            or preview_metadata.get("semantic_role")
            or preview_metadata.get("role")
            or preview_metadata.get("kind")
        )
        if role != "top_roll":
            continue
        if metadata.get("writer_product_key") or metadata.get("product_id"):
            return True
    return False


def _is_2ply_segment(segment: ManufacturingSegment) -> bool:
    values = [
        segment.key,
        segment.stage_code,
        segment.stage_type,
        segment.capability_code,
        segment.stage_contract_code,
        (segment.metadata or {}).get("operation_key"),
        (segment.metadata or {}).get("operation_name"),
    ]
    return any(_is_2ply_stage(value) for value in values)


def _is_2ply_stage(value: Any) -> bool:
    text = _norm(value).replace("_", "").replace("-", "").replace(" ", "")
    return any(token in text for token in ("2ply", "twoply", "singleface"))


def _input_role(item: Optional[ManufacturingInput]) -> str:
    if not item:
        return ""
    metadata = dict(item.metadata or {})
    preview_metadata = dict(metadata.get("preview_metadata") or {})
    return _norm(
        item.semantic_role
        or item.role_code
        or preview_metadata.get("semantic_role")
        or preview_metadata.get("role")
        or preview_metadata.get("type")
    )


def _input_layer_no(item: Optional[ManufacturingInput]) -> int:
    if not item:
        return 0
    metadata = dict(item.metadata or {})
    preview_metadata = dict(metadata.get("preview_metadata") or {})
    return _safe_int(preview_metadata.get("layer_no"), 0)


def _downstream_board_segment_key(plan: ManufacturingPlan, source_segment_key: str) -> Optional[str]:
    segment_by_key = {segment.key: segment for segment in plan.segments}
    for edge in plan.edges:
        if edge.edge_type != "previous_output" or edge.from_key != source_segment_key:
            continue
        target = segment_by_key.get(edge.to_key or "")
        if target and _is_board_segment(target):
            return target.key
    return None


def _is_board_segment(segment: ManufacturingSegment) -> bool:
    values = [
        segment.key,
        segment.stage_code,
        segment.stage_type,
        segment.capability_code,
        segment.stage_contract_code,
        (segment.metadata or {}).get("operation_key"),
        (segment.metadata or {}).get("operation_name"),
    ]
    return any("board" in _norm(value) for value in values)


def _two_ply_subassembly_signature(
    plan: ManufacturingPlan,
    segment: ManufacturingSegment,
    liner_edge: ManufacturingEdge,
    medium_edge: ManufacturingEdge,
) -> str:
    input_by_key = _input_by_key(plan)
    liner = input_by_key.get(liner_edge.input_key or "")
    medium = input_by_key.get(medium_edge.input_key or "")
    return _stable_hash(
        {
            "kind": "five_ply_2ply_subassembly",
            "output_sheet": _layout_identity_metadata(plan, segment),
            "liner": _two_ply_signature_input_identity(liner, liner_edge),
            "medium": _two_ply_signature_input_identity(medium, medium_edge),
        }
    )


def _two_ply_subassembly_signatures(
    plan: ManufacturingPlan,
    segment: ManufacturingSegment,
    liner_edge: ManufacturingEdge,
    medium_edge: ManufacturingEdge,
    *,
    plan_signature: str,
) -> Dict[str, str]:
    input_by_key = _input_by_key(plan)
    liner = input_by_key.get(liner_edge.input_key or "")
    medium = input_by_key.get(medium_edge.input_key or "")
    liner_identity = _two_ply_signature_input_identity(liner, liner_edge)
    medium_identity = _two_ply_signature_input_identity(medium, medium_edge)
    material_signature = _stable_hash(
        {
            "segment_key": segment.key,
            "inputs": [
                {
                    "edge_type": "rm_input",
                    "role_code": liner_edge.role_code,
                    "qty": liner_edge.qty,
                    "uom": liner_edge.uom,
                    "input": liner_identity,
                },
                {
                    "edge_type": "rm_input",
                    "role_code": medium_edge.role_code,
                    "qty": medium_edge.qty,
                    "uom": medium_edge.uom,
                    "input": medium_identity,
                },
            ],
        }
    )
    base_signatures = _segment_signatures(plan, segment, plan_signature)
    resolution_signature = _stable_hash(
        {
            "segment_key": segment.key,
            "entries": [
                {
                    "product_tmpl_id": liner.product_tmpl_id if liner else None,
                    "material_profile_id": liner.material_profile_id if liner else None,
                    "metadata": dict(liner.metadata or {}) if liner else {},
                },
                {
                    "product_tmpl_id": medium.product_tmpl_id if medium else None,
                    "material_profile_id": medium.material_profile_id if medium else None,
                    "metadata": dict(medium.metadata or {}) if medium else {},
                },
            ],
        }
    )
    return {
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": base_signatures["corr_output_fingerprint"],
        "corr_material_signature": material_signature,
        "corr_route_signature": base_signatures["corr_route_signature"],
        "corr_layout_signature": base_signatures["corr_layout_signature"],
        "corr_resolution_signature": resolution_signature,
    }


def _two_ply_signature_input_identity(
    item: Optional[ManufacturingInput],
    edge: ManufacturingEdge,
) -> Optional[Dict[str, Any]]:
    if not item:
        return None
    metadata = dict(item.metadata or {})
    preview_metadata = dict(metadata.get("preview_metadata") or {})
    resolved_product_key = (
        metadata.get("writer_product_key")
        or metadata.get("product_id")
        or item.product_tmpl_id
    )
    identity = {
        "role": _input_role(item) or edge.role_code,
        "resolved_product_key": resolved_product_key,
        "qty": edge.qty,
        "uom": edge.uom,
    }
    if not resolved_product_key:
        for key, value in {
            "gsm": _first_existing(preview_metadata, metadata, ("gsm", "paper_gsm", "basis_weight")),
            "bf": _first_existing(preview_metadata, metadata, ("bf", "bursting_factor")),
            "flute": _first_existing(preview_metadata, metadata, ("flute", "flute_code", "flute_type")),
            "shade": _first_existing(preview_metadata, metadata, ("shade", "paper_shade")),
        }.items():
            if value not in (None, "", [], {}):
                identity[key] = value
    return identity


def _input_material_identity(
    item: Optional[ManufacturingInput],
    edge: ManufacturingEdge,
) -> Dict[str, Any]:
    metadata = dict(item.metadata or {}) if item else {}
    preview_metadata = dict(metadata.get("preview_metadata") or {})
    resolved_product_key = (
        metadata.get("writer_product_key")
        or metadata.get("product_id")
        or (item.product_tmpl_id if item else None)
    )
    return {
        "role": _input_role(item) or edge.role_code,
        "resolved_product_key": resolved_product_key,
        "gsm": _first_existing(preview_metadata, metadata, ("gsm", "paper_gsm", "basis_weight")),
        "bf": _first_existing(preview_metadata, metadata, ("bf", "bursting_factor")),
        "flute": _first_existing(preview_metadata, metadata, ("flute", "flute_code", "flute_type")),
        "shade": _first_existing(preview_metadata, metadata, ("shade", "paper_shade")),
    }


def _first_existing(*args: Any) -> Any:
    keys = args[-1]
    mappings = args[:-1]
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        for key in keys:
            value = mapping.get(key)
            if value not in (None, ""):
                return value
    return None


def _five_ply_product_candidates(
    plan: ManufacturingPlan,
    decomp: Dict[str, Any],
    mode_key: str,
    identity_scope: str,
    plan_signature: str,
) -> List[MaterializedProductCandidate]:
    source = {segment.key: segment for segment in plan.segments}.get(decomp.get("source_segment_key"))
    if not source:
        return []
    products = []
    for group in decomp.get("groups") or []:
        signatures = _two_ply_subassembly_signatures(
            plan,
            source,
            group["liner_edge"],
            group["medium_edge"],
            plan_signature=plan_signature,
        )
        material_signature = signatures["corr_material_signature"]
        output_fingerprint = signatures["corr_output_fingerprint"]
        route_signature = signatures["corr_route_signature"]
        corr_sfg_key = "sfg:%s:%s:%s:%s" % (
            str(identity_scope or "global").strip().lower() or "global",
            output_fingerprint[:12],
            material_signature[:12],
            route_signature[:12],
        )
        products.append(
            MaterializedProductCandidate(
                candidate_key=f"product:{group['segment_key']}",
                segment_key=group["segment_key"],
                product_role="sfg",
                corr_sfg_key=corr_sfg_key,
                corr_segment_key=source.key,
                stock_mode=mode_key,
                product_type_hint="consu" if mode_key == "phantom" else "consu_storable",
                route_policy="phantom" if mode_key == "phantom" else ("manufacture_mto" if mode_key == "mto" else "manufacture"),
                uom=source.output.uom if source.output else source.target_output_uom,
                metadata={
                    "identity_scope": identity_scope,
                    "source_segment_key": source.key,
                    "five_ply_subassembly": True,
                    "merge_qty": group.get("qty"),
                    "merged_layer_pairs": _five_ply_layer_pairs(group),
                },
                **signatures,
            )
        )
    return products


def _five_ply_subassembly_boms(
    plan: ManufacturingPlan,
    decomp: Dict[str, Any],
    mode_key: str,
    plan_signature: str,
    product_by_segment: Dict[str, MaterializedProductCandidate],
    operations_by_owner: Dict[str, List[ManufacturingSegment]],
) -> List[MaterializedBomCandidate]:
    source_key = decomp.get("source_segment_key")
    source = {segment.key: segment for segment in plan.segments}.get(source_key)
    if not source:
        return []
    boms = []
    for group in decomp.get("groups") or []:
        segment_key = group["segment_key"]
        bom_key = f"bom:{segment_key}"
        lines, diagnostics = _candidate_lines_from_input_edges(
            [(group["liner_edge"], source.key), (group["medium_edge"], source.key)],
            source.key,
            bom_key,
            plan,
        )
        operations = [
            _operation_candidate_from_segment(op_segment, bom_key)
            for op_segment in operations_by_owner.get(source.key, [source])
        ]
        product = product_by_segment.get(segment_key)
        signatures = _two_ply_subassembly_signatures(
            plan,
            source,
            group["liner_edge"],
            group["medium_edge"],
            plan_signature=plan_signature,
        )
        boms.append(
            MaterializedBomCandidate(
                bom_key=bom_key,
                segment_key=segment_key,
                product_candidate_key=product.candidate_key if product else None,
                bom_role="sfg",
                bom_type="phantom" if mode_key == "phantom" else "normal",
                product_qty=1.0,
                corr_segment_key=source.key,
                lines=lines,
                operations=operations,
                metadata={
                    "diagnostic_only_lines": diagnostics,
                    "five_ply_subassembly": True,
                    "source_segment_key": source.key,
                    "merged_layer_pairs": _five_ply_layer_pairs(group),
                    **_layout_batch_metadata(plan, source, _segment_output_qty(source, 1.0)),
                },
                **signatures,
            )
        )
    return boms


def _five_ply_previous_lines_for_board(
    decomp: Dict[str, Any],
    bom_key: str,
    board_segment_key: str,
    product_by_segment: Dict[str, MaterializedProductCandidate],
) -> List[MaterializedBomLineCandidate]:
    lines = []
    for group in decomp.get("groups") or []:
        product = product_by_segment.get(group["segment_key"])
        lines.append(
            MaterializedBomLineCandidate(
                line_key=f"{bom_key}:previous:{group['segment_key']}",
                bom_key=bom_key,
                line_type="previous_output",
                product_candidate_key=product.candidate_key if product else None,
                source_segment_key=group["segment_key"],
                qty=_safe_float(group.get("qty"), 0.0),
                uom=product.uom if product else None,
                operation_segment_key=board_segment_key,
                metadata={
                    "five_ply_subassembly": True,
                    "source_2ply_segment_key": decomp.get("source_segment_key"),
                    "subassembly_signature": group["signature"],
                    "subassembly_count": _safe_float(group.get("qty"), 0.0),
                    "merged_layer_pairs": _five_ply_layer_pairs(group),
                },
            )
        )
    return lines


def _five_ply_layer_pairs(group: Dict[str, Any]) -> List[List[int]]:
    pairs = []
    for item in group.get("instances") or [group]:
        liner_layer = _safe_int(item.get("liner_layer_no"), 0)
        medium_layer = _safe_int(item.get("medium_layer_no"), 0)
        if liner_layer or medium_layer:
            pairs.append([liner_layer, medium_layer])
    return pairs


def _product_candidate_for_segment(
    plan: ManufacturingPlan,
    segment: ManufacturingSegment,
    *,
    mode_key: str,
    identity_scope: str,
    plan_signature: str,
    product_role: str,
) -> MaterializedProductCandidate:
    signatures = _segment_signatures(plan, segment, plan_signature)
    output_fingerprint = signatures["corr_output_fingerprint"]
    material_signature = signatures["corr_material_signature"]
    sfg_key = None
    if product_role == "sfg":
        sfg_key = "sfg:%s:%s:%s:%s" % (
            str(identity_scope or "global").strip().lower() or "global",
            output_fingerprint[:12],
            material_signature[:12],
            signatures["corr_route_signature"][:12],
        )
    if mode_key == "phantom":
        product_type_hint = "consu"
        route_policy = "phantom"
    elif mode_key == "mto":
        product_type_hint = "consu_storable"
        route_policy = "manufacture_mto"
    elif mode_key == "stockable":
        product_type_hint = "consu_storable"
        route_policy = "manufacture"
    else:
        product_type_hint = "existing_fg"
        route_policy = "existing"
    return MaterializedProductCandidate(
        candidate_key=f"product:{segment.key}",
        segment_key=segment.key,
        product_role=product_role,
        corr_sfg_key=sfg_key,
        corr_segment_key=segment.key,
        stock_mode=mode_key,
        product_type_hint=product_type_hint,
        route_policy=route_policy,
        uom=segment.output.uom if segment.output else segment.target_output_uom,
        metadata={"identity_scope": identity_scope},
        **signatures,
    )


def _line_candidate_from_input_edge(
    edge: ManufacturingEdge,
    owning_segment_key: str,
    bom_key: str,
    plan: ManufacturingPlan,
) -> MaterializedBomLineCandidate:
    input_item = _input_by_key(plan).get(edge.input_key or "")
    qty = _safe_float(edge.qty, 0.0)
    uom = edge.uom
    metadata = dict(edge.metadata or {})
    if input_item:
        input_metadata = dict(input_item.metadata or {})
        preview_metadata = dict(input_metadata.get("preview_metadata") or {})
        qty = qty or _safe_float(input_metadata.get("writer_qty"), 0.0)
        uom = uom or input_metadata.get("writer_uom")
        metadata.update(
            {
                "semantic_role": input_item.semantic_role,
                "category": input_item.category,
                "source": input_item.source,
                "material_profile_id": input_item.material_profile_id,
                "product_id": input_metadata.get("product_id"),
                "product_name": input_metadata.get("product_name") or input_metadata.get("product_label"),
                "product_tmpl_id": input_item.product_tmpl_id,
                "rule_id": input_item.rule_id,
                "writer_product_key": input_metadata.get("writer_product_key"),
                "writer_qty": input_metadata.get("writer_qty"),
                "writer_uom": input_metadata.get("writer_uom"),
                "qty_per_fg": input_metadata.get("qty_per_fg"),
                "total_qty": input_metadata.get("total_qty"),
                "writer_stage": input_metadata.get("writer_stage"),
                "quantity_source": input_metadata.get("quantity_source"),
                "source_kind": input_metadata.get("source_kind") or input_item.source,
                "resolution_source": input_metadata.get("resolution_source"),
                "required_deckle_cm": input_metadata.get("required_deckle_cm"),
                "selected_layout": dict(input_metadata.get("selected_layout") or {}),
                "uom_id": input_metadata.get("uom_id"),
                "uom_name": input_metadata.get("uom_name"),
                "trace_only": bool(input_metadata.get("trace_only") or preview_metadata.get("trace_only")),
                "debug": dict(preview_metadata.get("debug") or {}),
                "layer_no": preview_metadata.get("layer_no"),
                "preview_metadata": preview_metadata,
            }
        )
    qty = _normalized_input_component_qty(qty, uom, metadata, owning_segment_key, plan)
    return MaterializedBomLineCandidate(
        line_key=f"{bom_key}:{edge.edge_type}:{edge.input_key or 'input'}",
        bom_key=bom_key,
        line_type=edge.edge_type,
        input_key=edge.input_key,
        role_code=edge.role_code,
        qty=qty,
        uom=uom,
        operation_segment_key=owning_segment_key,
        metadata=metadata,
    )


def _normalized_input_component_qty(
    qty: float,
    uom: Any,
    metadata: Dict[str, Any],
    operation_segment_key: str,
    plan: ManufacturingPlan,
) -> float:
    raw_qty = _safe_float(qty, 0.0)
    if raw_qty <= 0.0:
        return raw_qty
    role = _norm(metadata.get("semantic_role") or metadata.get("role") or metadata.get("type"))
    segment = {item.key: item for item in plan.segments}.get(operation_segment_key)
    if _is_upstream_yield_sheet_bom_segment(segment):
        per_fg_qty = _safe_float(metadata.get("qty_per_fg"), 0.0)
        fg_per_sheet = _fg_per_sheet_for_plan(plan)
        if per_fg_qty > 0.0 and fg_per_sheet > 1.0 and abs(raw_qty - per_fg_qty) <= 0.000001:
            metadata.update(
                {
                    "qty_basis_used": "upstream_yield_sheet_full_sheet",
                    "component_qty": per_fg_qty * fg_per_sheet,
                    "fg_per_sheet": fg_per_sheet,
                    "yield_normalization_applied": True,
                    **_yield_area_debug_metadata(plan),
                }
            )
            return per_fg_qty * fg_per_sheet
        metadata.update(
            {
                "qty_basis_used": "upstream_yield_sheet_raw_qty",
                "component_qty": raw_qty,
                "fg_per_sheet": fg_per_sheet,
                "yield_normalization_applied": False,
                **_yield_area_debug_metadata(plan),
            }
        )
    if role not in {"top_sheet_external", "top_sheet", "top_roll"}:
        return raw_qty
    if _norm(uom) not in {"unit", "units", "pcs", "piece", "sheet", "sheets"}:
        return raw_qty
    output_qty = _segment_output_qty(segment, 0.0) if segment else 0.0
    if output_qty > 1.0 and abs(raw_qty - output_qty) <= 0.000001:
        return 1.0
    return raw_qty


def _is_upstream_yield_sheet_bom_segment(segment: Optional[ManufacturingSegment]) -> bool:
    if not segment:
        return False
    policy = _norm((segment.metadata or {}).get("bom_mapping_policy"))
    if policy != "upstream_yield_sheet_boundary":
        return False
    uom = _norm(segment.output.uom if segment.output else segment.target_output_uom)
    return uom in {"sheet", "sheets"}


def _yield_area_debug_metadata(plan: ManufacturingPlan) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for segment in plan.segments or []:
        metadata = dict(segment.metadata or {})
        metrics = dict(metadata.get("operation_metrics") or {})
        for source in (metrics, metadata):
            if "area_m2_per_sheet" in source and "area_m2_per_sheet" not in result:
                result["area_m2_per_sheet"] = source.get("area_m2_per_sheet")
            if "area_m2_per_fg" in source and "area_m2_per_fg" not in result:
                result["area_m2_per_fg"] = source.get("area_m2_per_fg")
        if "area_m2_per_sheet" in result and "area_m2_per_fg" in result:
            break
    return result


def _fg_per_sheet_for_plan(plan: ManufacturingPlan) -> float:
    for segment in plan.segments or []:
        metrics = dict((segment.metadata or {}).get("operation_metrics") or {})
        if str(metrics.get("yield_basis") or "").strip().lower() != "fg_per_sheet":
            continue
        qty = _safe_float(metrics.get("fg_per_sheet") or metrics.get("yield_relation_output_blanks"), 0.0)
        if qty > 0.0:
            return qty
    return 0.0


def _candidate_lines_from_input_edges(
    edges: List[Any],
    owning_segment_key: str,
    bom_key: str,
    plan: ManufacturingPlan,
) -> Tuple[List[MaterializedBomLineCandidate], List[Dict[str, Any]]]:
    candidate_by_identity: Dict[Any, MaterializedBomLineCandidate] = {}
    diagnostics: List[Dict[str, Any]] = []
    for edge_item in edges:
        if isinstance(edge_item, tuple):
            edge, operation_segment_key = edge_item
        else:
            edge = edge_item
            operation_segment_key = owning_segment_key
        line = _line_candidate_from_input_edge(edge, operation_segment_key, bom_key, plan)
        reason = _materialization_line_diagnostic_reason(line)
        if reason:
            diagnostics.append(_diagnostic_line_from_candidate(line, reason))
            continue
        identity = _materialization_line_identity(line)
        existing = candidate_by_identity.get(identity)
        if not existing:
            candidate_by_identity[identity] = line
            continue
        candidate_by_identity[identity] = _merge_materialization_lines(existing, line)
        diagnostics.append(_diagnostic_line_from_candidate(line, "merged_duplicate_candidate_line"))
    return list(candidate_by_identity.values()), diagnostics


def _materialization_line_diagnostic_reason(line: MaterializedBomLineCandidate) -> Optional[str]:
    metadata = dict(line.metadata or {})
    if metadata.get("trace_only") or (metadata.get("debug") or {}).get("trace_only"):
        return "trace_only"
    if _safe_float(line.qty, 0.0) <= 0.0:
        return "zero_or_missing_qty"
    if not str(line.uom or "").strip():
        return "missing_uom"
    if line.line_type in ("rm_input", "consumable_input", "intermediate_input"):
        if not (metadata.get("product_id") or metadata.get("writer_product_key") or metadata.get("product_tmpl_id")):
            return "missing_resolved_product"
    return None


def _materialization_line_identity(line: MaterializedBomLineCandidate) -> Any:
    metadata = dict(line.metadata or {})
    return (
        line.bom_key,
        line.line_type,
        metadata.get("product_id") or metadata.get("writer_product_key") or metadata.get("product_tmpl_id") or line.product_candidate_key,
        line.role_code or metadata.get("semantic_role"),
        metadata.get("layer_no"),
        line.uom,
        line.operation_segment_key,
    )


def _merge_materialization_lines(
    left: MaterializedBomLineCandidate,
    right: MaterializedBomLineCandidate,
) -> MaterializedBomLineCandidate:
    metadata = dict(left.metadata or {})
    duplicate_keys = list(metadata.get("merged_input_keys") or [])
    if right.input_key:
        duplicate_keys.append(right.input_key)
    metadata["merged_input_keys"] = duplicate_keys
    merged_qty = _safe_float(left.qty, 0.0) + _safe_float(right.qty, 0.0)
    if _is_unit_top_sheet_line(left):
        merged_qty = max(_safe_float(left.qty, 0.0), _safe_float(right.qty, 0.0))
    elif left.line_type == "rm_input" and _safe_int(metadata.get("layer_no"), 0) > 0:
        # Duplicate resolved paper-layer rows can be emitted by preview/runtime
        # overlay paths for the same physical layer. They should collapse to a
        # single layer consumption line, not double the quantity.
        merged_qty = max(_safe_float(left.qty, 0.0), _safe_float(right.qty, 0.0))
    return MaterializedBomLineCandidate(
        line_key=left.line_key,
        bom_key=left.bom_key,
        line_type=left.line_type,
        product_candidate_key=left.product_candidate_key,
        source_segment_key=left.source_segment_key,
        input_key=left.input_key,
        role_code=left.role_code,
        qty=merged_qty,
        uom=left.uom,
        operation_segment_key=left.operation_segment_key,
        metadata=metadata,
    )


def _is_unit_top_sheet_line(line: MaterializedBomLineCandidate) -> bool:
    metadata = dict(line.metadata or {})
    role = _norm(metadata.get("semantic_role") or line.role_code or metadata.get("role") or metadata.get("type"))
    return role in {"top_sheet_external", "top_sheet", "top_roll"} and _norm(line.uom) in {
        "unit",
        "units",
        "pcs",
        "piece",
        "sheet",
        "sheets",
    }


def _diagnostic_line_from_candidate(
    line: MaterializedBomLineCandidate,
    reason: str,
) -> Dict[str, Any]:
    data = asdict(line)
    data["diagnostic_only"] = True
    data["diagnostic_reason"] = reason
    return data


def _operation_candidate_from_segment(
    segment: ManufacturingSegment,
    bom_key: str,
) -> MaterializedOperationCandidate:
    metrics = dict((segment.metadata or {}).get("operation_metrics") or {})
    operation_key = (segment.metadata or {}).get("operation_key")
    operation_name = (segment.metadata or {}).get("operation_name")
    return MaterializedOperationCandidate(
        operation_candidate_key=f"{bom_key}:operation:{segment.key}",
        bom_key=bom_key,
        segment_key=segment.key,
        operation_key=operation_key,
        operation_name=operation_name,
        stage_name=segment.stage_name or None,
        sequence=_safe_int(segment.sequence, 0),
        input_qty=_safe_float(metrics.get("input_qty"), segment.input_qty_from_prev),
        output_qty=_safe_float(metrics.get("output_qty"), segment.target_output_qty),
        uom=metrics.get("uom") or segment.target_output_uom,
        stage_contract_code=segment.stage_contract_code,
        stage_template_id=segment.stage_template_id,
        stage_code=segment.stage_code,
        workcenter_id=_safe_optional_int((segment.metadata or {}).get("workcenter_id")),
        routing_workcenter_id=_safe_optional_int((segment.metadata or {}).get("routing_workcenter_id")),
        metadata={
            key: value
            for key, value in {
                "source": metrics.get("source") or "manufacturing_plan",
                "input_uom": metrics.get("input_uom"),
                "output_uom": metrics.get("output_uom"),
                "yield_basis": metrics.get("yield_basis"),
                "fg_per_sheet": metrics.get("fg_per_sheet"),
                "yield_quantity_basis": metrics.get("yield_quantity_basis"),
                "yield_relation_input_sheets": metrics.get("yield_relation_input_sheets"),
                "yield_relation_output_blanks": metrics.get("yield_relation_output_blanks"),
                "yield_output_label": metrics.get("yield_output_label"),
                "yield_downstream_output_label": metrics.get("yield_downstream_output_label"),
                "yield_output_kind": metrics.get("yield_output_kind"),
            }.items()
            if value not in (None, "")
        },
    )


def _operation_owner_map(
    plan: ManufacturingPlan,
    output_segment_by_key: Dict[str, ManufacturingSegment],
) -> Dict[str, List[ManufacturingSegment]]:
    owners: Dict[str, List[ManufacturingSegment]] = {
        segment.key: [] for segment in output_segment_by_key.values()
    }
    last_output_key: Optional[str] = None
    for segment in plan.segments:
        owner_key = segment.key if segment.key in output_segment_by_key else None
        if not owner_key:
            owner_key = segment.parent_material_key or last_output_key
        if (segment.metadata or {}).get("operation_key") and owner_key and owner_key in owners:
            owners.setdefault(owner_key, []).append(segment)
        if segment.key in output_segment_by_key:
            last_output_key = segment.key
    return owners


def _materialized_owner_by_segment(
    plan: ManufacturingPlan,
    output_segment_by_key: Dict[str, ManufacturingSegment],
) -> Dict[str, str]:
    owners: Dict[str, str] = {}
    last_output_key: Optional[str] = None
    for segment in plan.segments:
        owner_key = segment.key if segment.key in output_segment_by_key else None
        if not owner_key:
            owner_key = segment.parent_material_key or last_output_key
        if owner_key and owner_key in output_segment_by_key:
            owners[segment.key] = owner_key
        if segment.key in output_segment_by_key:
            last_output_key = segment.key
    return owners


def _all_operation_segments_for_single_bom(plan: ManufacturingPlan) -> List[ManufacturingSegment]:
    return [
        segment
        for segment in plan.segments
        if (segment.metadata or {}).get("operation_key")
    ]


def _graph_warnings(
    plan: ManufacturingPlan,
    previous_edges: List[ManufacturingEdge],
) -> List[ManufacturingPlanWarning]:
    warnings: List[ManufacturingPlanWarning] = []
    segment_keys = {segment.key for segment in plan.segments}
    outgoing: Dict[str, List[str]] = {}
    incoming: Dict[str, List[str]] = {}
    for edge in previous_edges:
        if edge.from_key not in segment_keys or edge.to_key not in segment_keys:
            warnings.append(
                ManufacturingPlanWarning(
                    code="disconnected_previous_output_edge",
                    segment_key=edge.to_key,
                    message="Previous-output edge references a missing segment.",
                    metadata={"from_key": edge.from_key, "to_key": edge.to_key},
                )
            )
            continue
        outgoing.setdefault(edge.from_key or "", []).append(edge.to_key)
        incoming.setdefault(edge.to_key, []).append(edge.from_key or "")

    for segment_key, predecessors in incoming.items():
        if len(predecessors) > 1:
            warnings.append(
                ManufacturingPlanWarning(
                    code="multiple_predecessors_not_materialized_yet",
                    segment_key=segment_key,
                    message="Dry-run structure records this merge but C1 is linear-first.",
                    metadata={"predecessors": predecessors},
                )
            )

    if _has_cycle(outgoing):
        warnings.append(
            ManufacturingPlanWarning(
                code="cycle_detected",
                message="Previous-output graph contains a cycle.",
            )
        )

    output_segments = [
        segment
        for segment in plan.segments
        if segment.output and segment.output.output_role in ("sfg", "fg")
    ]
    connected = set(incoming.keys()) | set(outgoing.keys())
    for segment in output_segments:
        if segment.output and segment.output.output_role == "fg":
            continue
        if segment.key not in connected:
            warnings.append(
                ManufacturingPlanWarning(
                    code="disconnected_output_segment",
                    segment_key=segment.key,
                    message="SFG output segment has no previous-output connection.",
                )
            )
    return warnings


def _has_cycle(outgoing: Dict[str, List[str]]) -> bool:
    visiting = set()
    visited = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for child in outgoing.get(node, []):
            if visit(child):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in list(outgoing))


def _segment_signatures(
    plan: ManufacturingPlan,
    segment: ManufacturingSegment,
    plan_signature: str,
) -> Dict[str, str]:
    yield_identity = _segment_yield_identity_metadata(plan, segment)
    return {
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": _stable_hash(
            {
                "segment_key": segment.key,
                "output": asdict(segment.output) if segment.output else None,
                "target_output_uom": segment.target_output_uom,
                "layout_output": _layout_identity_metadata(plan, segment),
                "yield_identity": yield_identity,
            }
        ),
        "corr_material_signature": _material_signature_for_segment(plan, segment.key),
        "corr_route_signature": _stable_hash(
            {
                "segment_key": segment.key,
                "stage_contract_code": segment.stage_contract_code,
                "stage_template_id": segment.stage_template_id,
                "execution_type": segment.execution_type,
                "operation_key": (segment.metadata or {}).get("operation_key"),
                "yield_identity": yield_identity,
            }
        ),
        "corr_layout_signature": _stable_hash(
            {
                "segment_key": segment.key,
                "output_descriptor": segment.output_descriptor,
                "plan_layout": (plan.metadata or {}).get("selected_layout"),
            }
        ),
        "corr_resolution_signature": _resolution_signature_for_segment(plan, segment.key),
    }


def _segment_yield_identity_metadata(
    plan: ManufacturingPlan,
    segment: ManufacturingSegment,
) -> Optional[Dict[str, Any]]:
    direct_yield_qty = _fg_per_sheet_yield_output_qty(segment)
    if direct_yield_qty > 1.0:
        return {
            "yield_basis": "fg_per_sheet",
            "identity_basis": "segment_output_qty",
            "fg_per_sheet": direct_yield_qty,
            "output_kind": _norm(segment.output_kind),
        }
    outgoing_edges = [
        edge
        for edge in (plan.edges or [])
        if edge.edge_type == "previous_output" and edge.from_key == segment.key
    ]
    if not outgoing_edges:
        return None
    batch_qty = max((_safe_float(edge.qty, 0.0) for edge in outgoing_edges), default=0.0)
    if batch_qty <= 1.0:
        return None
    if not _segment_carries_downstream_batch_yield(segment, outgoing_edges):
        return None
    downstream_stage_codes = sorted(
        {
            _norm(
                next(
                    (
                        target.stage_code
                        for target in plan.segments
                        if target.key == edge.to_key
                    ),
                    "",
                )
            )
            for edge in outgoing_edges
            if edge.to_key
        }
    )
    return {
        "yield_basis": "fg_per_sheet",
        "identity_basis": "downstream_batch_yield",
        "fg_per_sheet": batch_qty,
        "downstream_stage_codes": downstream_stage_codes,
    }


def _segment_carries_downstream_batch_yield(
    segment: ManufacturingSegment,
    outgoing_edges: List[ManufacturingEdge],
) -> bool:
    stage_text = " ".join(
        [
            _norm(segment.stage_code),
            _norm(segment.stage_contract_code),
            _norm(segment.stage_name),
            _norm(segment.stage_type),
            _norm(segment.capability_code),
            _norm((segment.metadata or {}).get("operation_key")),
            _norm((segment.metadata or {}).get("operation_name")),
        ]
    )
    if not any(token in stage_text for token in ("die", "slot")):
        return False
    return any(_safe_float(edge.qty, 0.0) > 1.0 for edge in outgoing_edges or [])


def _segment_output_qty(segment: ManufacturingSegment, default: float = 1.0) -> float:
    output_qty = _safe_float(segment.output.qty if segment.output else None, 0.0)
    if output_qty > 0.0:
        return output_qty
    target_qty = _safe_float(segment.target_output_qty, 0.0)
    if target_qty > 0.0:
        return target_qty
    default_qty = _safe_float(default, 0.0)
    return default_qty if default_qty > 0.0 else 1.0


def _bom_product_qty_for_segment(segment: ManufacturingSegment) -> float:
    if _is_upstream_yield_sheet_bom_segment(segment):
        return 1.0
    yield_qty = _fg_per_sheet_yield_output_qty(segment)
    if yield_qty > 0.0:
        return yield_qty
    output_uom = _norm(segment.output.uom if segment.output else segment.target_output_uom)
    output_kind = _norm(segment.output_kind)
    if (
        segment.output
        and segment.output.output_role != "fg"
        and output_uom in {"unit", "units"}
        and output_kind in {"blank", "gross_blank", "usable_blank"}
    ):
        output_qty = _segment_output_qty(segment, 0.0)
        if output_qty > 1.0:
            return output_qty
    # Generated staged BoMs describe one usable output unit. Batch/run sizes
    # such as slitting UPS belong in metadata and operation planning, not in
    # the static component relationship shown by BoM Overview.
    return 1.0


def _fg_per_sheet_yield_output_qty(segment: ManufacturingSegment) -> float:
    if not _is_fg_per_sheet_yield_segment(segment):
        return 0.0
    metrics = dict((segment.metadata or {}).get("operation_metrics") or {})
    metadata = dict(segment.metadata or {})
    yield_basis = str(metrics.get("yield_basis") or metadata.get("yield_basis") or "").strip().lower()
    if yield_basis != "fg_per_sheet":
        return 0.0
    if segment.output and segment.output.output_role == "fg":
        return 0.0
    qty = _safe_float(
        metrics.get("fg_per_sheet")
        or metadata.get("fg_per_sheet")
        or metrics.get("yield_relation_output_blanks")
        or metadata.get("yield_relation_output_blanks"),
        0.0,
    )
    return qty if qty > 0.0 else 0.0


def _bom_product_qty_for_segment_with_previous_sources(
    segment: ManufacturingSegment,
    previous_edges: List[ManufacturingEdge],
    outgoing_edges: List[ManufacturingEdge],
    segment_by_key: Dict[str, ManufacturingSegment],
) -> float:
    base_qty = _bom_product_qty_for_segment(segment)
    if base_qty > 1.0:
        return base_qty
    outgoing_batch_qty = _batch_yield_qty_from_outgoing_previous_edges(segment, outgoing_edges)
    if outgoing_batch_qty > 1.0:
        return outgoing_batch_qty
    if not (segment.output and segment.output.output_role == "fg"):
        return base_qty
    for edge in previous_edges or []:
        source = segment_by_key.get(edge.from_key or "")
        if not (_is_fg_per_sheet_primary_yield_segment(source) or _is_slot_die_batch_boundary_source(source)):
            continue
        qty = _safe_float(edge.qty, 0.0)
        if qty > 1.0:
            return qty
    return base_qty


def _batch_yield_qty_from_outgoing_previous_edges(
    segment: ManufacturingSegment,
    outgoing_edges: List[ManufacturingEdge],
) -> float:
    if not _is_batch_yield_boundary_source(segment):
        return 0.0
    for edge in outgoing_edges or []:
        qty = _safe_float(edge.qty, 0.0)
        if qty > 1.0:
            return qty
    return 0.0


def _is_batch_yield_boundary_source(segment: Optional[ManufacturingSegment]) -> bool:
    if not segment or not segment.output or segment.output.output_role == "fg":
        return False
    metrics = dict((segment.metadata or {}).get("operation_metrics") or {})
    metadata = dict(segment.metadata or {})
    if str(metrics.get("yield_basis") or metadata.get("yield_basis") or "").strip().lower() == "fg_per_sheet":
        return True
    source_uom = _norm(segment.output.uom or segment.target_output_uom)
    source_kind = _norm(segment.output_kind)
    source_policy = _norm(metadata.get("bom_mapping_policy"))
    source_stage_text = " ".join(
        [
            _norm(segment.stage_code),
            _norm(segment.stage_type),
            _norm(segment.stage_name),
            _norm(segment.capability_code),
        ]
    )
    return (
        source_uom in {"unit", "units"}
        and source_kind in {"blank", "gross_blank", "usable_blank", ""}
        and (source_policy == "yield_boundary" or "die" in source_stage_text or "slot" in source_stage_text)
    )


def _is_slot_die_batch_boundary_source(segment: Optional[ManufacturingSegment]) -> bool:
    if not _is_batch_yield_boundary_source(segment):
        return False
    source_stage_text = " ".join(
        [
            _norm(segment.stage_code if segment else ""),
            _norm(segment.stage_type if segment else ""),
            _norm(segment.stage_name if segment else ""),
            _norm(segment.capability_code if segment else ""),
        ]
    )
    return "die" in source_stage_text or "slot" in source_stage_text


def _is_fg_per_sheet_yield_segment(segment: Optional[ManufacturingSegment]) -> bool:
    if not segment:
        return False
    metrics = dict((segment.metadata or {}).get("operation_metrics") or {})
    metadata = dict(segment.metadata or {})
    yield_basis = str(metrics.get("yield_basis") or metadata.get("yield_basis") or "").strip().lower()
    if yield_basis != "fg_per_sheet":
        return False
    input_uom = str(metrics.get("input_uom") or "").strip().lower()
    output_uom = str(metrics.get("output_uom") or metrics.get("uom") or segment.target_output_uom or "").strip().lower()
    output_label = str(metrics.get("yield_output_label") or "").strip().lower()
    output_kind = str(metrics.get("yield_output_kind") or segment.output_kind or "").strip().lower()
    has_blank_output = (
        output_label in {"gross blanks", "usable blanks"}
        or output_kind in {"gross_blank", "blank", "usable_blank"}
        or output_uom in {"unit", "units"}
    )
    return bool(has_blank_output)


def _is_fg_per_sheet_primary_yield_segment(segment: Optional[ManufacturingSegment]) -> bool:
    if not segment:
        return False
    metrics = dict((segment.metadata or {}).get("operation_metrics") or {})
    metadata = dict(segment.metadata or {})
    yield_basis = str(metrics.get("yield_basis") or metadata.get("yield_basis") or "").strip().lower()
    if yield_basis != "fg_per_sheet":
        return False
    input_uom = str(metrics.get("input_uom") or "").strip().lower()
    output_uom = str(metrics.get("output_uom") or metrics.get("uom") or segment.target_output_uom or "").strip().lower()
    output_label = str(metrics.get("yield_output_label") or "").strip().lower()
    output_kind = str(metrics.get("yield_output_kind") or segment.output_kind or "").strip().lower()
    return (
        (input_uom == "sheet" and output_uom in {"unit", "units"})
        or output_label == "gross blanks"
        or output_kind == "gross_blank"
    )


def _previous_output_component_qty(
    edge: ManufacturingEdge,
    source_segment: Optional[ManufacturingSegment],
    target_segment: Optional[ManufacturingSegment] = None,
) -> float:
    qty = _safe_float(edge.qty, 0.0)
    source_qty = _segment_output_qty(source_segment, 0.0) if source_segment else 0.0
    target_qty = _segment_output_qty(target_segment, 0.0) if target_segment else 0.0
    if _is_fg_per_sheet_primary_yield_segment(source_segment) and not (
        target_segment and target_segment.output and target_segment.output.output_role == "fg"
    ):
        yield_qty = _fg_per_sheet_yield_output_qty(source_segment) if source_segment else 0.0
        return max(qty, yield_qty, 1.0)
    if (edge.metadata or {}).get("preserve_previous_output_qty"):
        return qty if qty > 0.0 else 1.0
    source_uom = _norm(source_segment.output.uom if source_segment and source_segment.output else None)
    target_uom = _norm(target_segment.target_output_uom if target_segment else None)
    source_metrics = dict((source_segment.metadata or {}).get("operation_metrics") or {}) if source_segment else {}
    target_metrics = dict((target_segment.metadata or {}).get("operation_metrics") or {}) if target_segment else {}
    source_metadata = dict(source_segment.metadata or {}) if source_segment else {}
    target_metadata = dict(target_segment.metadata or {}) if target_segment else {}
    fg_per_sheet_chain = (
        str(source_metrics.get("yield_basis") or source_metadata.get("yield_basis") or "").strip().lower() == "fg_per_sheet"
        or str(target_metrics.get("yield_basis") or target_metadata.get("yield_basis") or "").strip().lower() == "fg_per_sheet"
    )
    if fg_per_sheet_chain and source_uom in {"sheet", "sheets"} and target_uom in {"sheet", "sheets"}:
        if target_qty > 0.0:
            return min(qty, target_qty) if qty > 0.0 else target_qty
        return qty if qty > 0.0 else 1.0
    if _is_upstream_yield_sheet_bom_segment(source_segment) and _is_upstream_yield_sheet_bom_segment(target_segment):
        if (
            qty > 0.0
            and source_qty > 0.0
            and target_qty > 0.0
            and qty > target_qty
            and abs(qty - source_qty) <= 0.000001
        ):
            return target_qty
        return qty if qty > 0.0 else 1.0
    if _is_fg_per_sheet_primary_yield_segment(source_segment):
        return qty if qty > 0.0 else 1.0
    if qty > 0.0 and source_qty > 0.0:
        normalized = qty / source_qty
        if normalized > 0.0:
            return normalized
    return qty


def _previous_output_qty_debug_metadata(
    edge: ManufacturingEdge,
    source_segment: Optional[ManufacturingSegment],
    target_segment: Optional[ManufacturingSegment],
    plan: ManufacturingPlan,
) -> Dict[str, Any]:
    qty = _safe_float(edge.qty, 0.0)
    component_qty = _previous_output_component_qty(edge, source_segment, target_segment)
    source_qty = _segment_output_qty(source_segment, 0.0) if source_segment else 0.0
    preserved = bool((edge.metadata or {}).get("preserve_previous_output_qty"))
    whole_sheet_chain = _is_upstream_yield_sheet_bom_segment(source_segment) and _is_upstream_yield_sheet_bom_segment(
        target_segment
    )
    yield_source = _is_fg_per_sheet_primary_yield_segment(source_segment)
    normalized = (
        qty > 0.0
        and source_qty > 0.0
        and not preserved
        and not whole_sheet_chain
        and not yield_source
        and abs(component_qty - (qty / source_qty)) <= 0.000001
    )
    if whole_sheet_chain:
        basis = "upstream_yield_sheet_whole_sheet_chain"
    elif preserved:
        basis = "preserved_previous_output_qty"
    elif yield_source:
        basis = "fg_per_sheet_yield_output"
    elif normalized:
        basis = "normalized_by_source_output_qty"
    else:
        basis = "raw_previous_output_qty"
    return {
        "source_stage_code": source_segment.stage_code if source_segment else None,
        "target_stage_code": target_segment.stage_code if target_segment else None,
        "source_stage_product_qty": _bom_product_qty_for_segment(source_segment) if source_segment else None,
        "target_stage_product_qty": _bom_product_qty_for_segment(target_segment) if target_segment else None,
        "source_output_qty": source_qty,
        "component_qty": component_qty,
        "qty_basis_used": basis,
        "fg_per_sheet": _fg_per_sheet_for_plan(plan),
        "yield_normalization_applied": normalized,
        **_yield_area_debug_metadata(plan),
    }


def _should_preserve_previous_output_qty(
    source_segment: Optional[ManufacturingSegment],
    target_segment: Optional[ManufacturingSegment],
) -> bool:
    if not source_segment or not target_segment:
        return False
    source_uom = _norm(source_segment.output.uom if source_segment.output else source_segment.target_output_uom)
    target_uom = _norm(target_segment.target_output_uom)
    if source_uom not in {"sheet", "sheets"}:
        return False
    if target_uom and target_uom not in {"sheet", "sheets", "unit", "units"}:
        return False

    source_policy = _norm((source_segment.metadata or {}).get("bom_mapping_policy"))
    target_policy = _norm((target_segment.metadata or {}).get("bom_mapping_policy"))
    whole_sheet_policies = {
        "upstream_yield_sheet_boundary",
        "yield_boundary",
        "stock_output_operation_boundary",
    }
    if source_policy in whole_sheet_policies or target_policy in whole_sheet_policies:
        return True
    return _is_fg_per_sheet_yield_segment(target_segment)


def _layout_batch_metadata(
    plan: ManufacturingPlan,
    segment: ManufacturingSegment,
    output_qty: float,
) -> Dict[str, Any]:
    selected_layout = dict((plan.metadata or {}).get("selected_layout") or {})
    option = dict(selected_layout.get("option") or selected_layout)
    up_count = _safe_int(option.get("up_count"), 0)
    if up_count <= 0:
        up_count = _safe_int(option.get("slitting_ups"), 0)
    input_width = _safe_float(
        option.get("input_deckle_width_mm")
        or option.get("source_width_mm")
        or option.get("deckle_width_mm")
        or option.get("final_sheet_width_mm"),
        0.0,
    )
    input_length = _safe_float(
        option.get("input_sheet_length_mm")
        or option.get("source_length_mm")
        or option.get("deckle_length_mm")
        or option.get("final_sheet_length_mm"),
        0.0,
    )
    output_width = _safe_float(
        option.get("output_sheet_width_mm")
        or option.get("slit_sheet_width_mm")
        or option.get("usable_sheet_width_mm"),
        0.0,
    )
    output_length = _safe_float(
        option.get("output_sheet_length_mm")
        or option.get("slit_sheet_length_mm")
        or option.get("usable_sheet_length_mm")
        or option.get("final_sheet_length_mm"),
        0.0,
    )
    if output_width <= 0.0 and input_width > 0.0:
        output_width = input_width / float(up_count) if up_count > 1 else input_width
    metadata: Dict[str, Any] = {}
    if output_length > 0.0:
        metadata["output_sheet_length_mm"] = output_length
    if output_width > 0.0:
        metadata["output_sheet_width_mm"] = output_width
    if input_length > 0.0:
        metadata["input_sheet_length_mm"] = input_length
    if input_width > 0.0:
        metadata["input_deckle_width_mm"] = input_width
        metadata["source_width_mm"] = input_width
    if up_count > 1:
        metadata["slitting_ups"] = up_count
        cycles = _safe_float(output_qty, 0.0) / float(up_count)
        if cycles > 0.0:
            metadata["production_cycles"] = cycles
    if metadata and selected_layout:
        metadata["selected_layout"] = selected_layout
    return metadata


def _layout_identity_metadata(plan: ManufacturingPlan, segment: ManufacturingSegment) -> Dict[str, Any]:
    metadata = _layout_batch_metadata(plan, segment, _segment_output_qty(segment, 1.0))
    return {
        key: metadata.get(key)
        for key in (
            "output_sheet_length_mm",
            "output_sheet_width_mm",
            "input_deckle_width_mm",
            "slitting_ups",
        )
        if metadata.get(key) not in (None, "", False)
    }


def _plan_signature(plan: ManufacturingPlan) -> str:
    return _stable_hash(
        {
            "plan_version": plan.plan_version,
            "spec_id": plan.spec_id,
            "fg_segment_key": plan.fg_segment_key,
            "segments": [
                {
                    "key": segment.key,
                    "stage_contract_code": segment.stage_contract_code,
                    "stage_template_id": segment.stage_template_id,
                    "execution_type": segment.execution_type,
                    "output": asdict(segment.output) if segment.output else None,
                    "inputs": [item.input_key for item in _segment_inputs(segment)],
                }
                for segment in plan.segments
            ],
            "edges": [asdict(edge) for edge in plan.edges],
        }
    )


def _material_signature_for_segment(plan: ManufacturingPlan, segment_key: str) -> str:
    return _material_signature_for_segment_seen(plan, segment_key, set())


def _material_signature_for_segment_seen(plan: ManufacturingPlan, segment_key: str, seen: set) -> str:
    if segment_key in seen:
        return _stable_hash({"segment_key": segment_key, "cycle": True})
    seen = set(seen)
    seen.add(segment_key)
    inputs = []
    input_by_key = _input_by_key(plan)
    segment = {item.key: item for item in plan.segments}.get(segment_key)
    input_edges = [
        edge
        for edge in plan.edges
        if edge.to_key == segment_key and edge.edge_type in ("rm_input", "consumable_input", "intermediate_input")
    ]
    if segment and _is_2ply_segment(segment):
        resolved_edges = _dedupe_signature_resolved_material_edges(input_edges, input_by_key)
        if resolved_edges:
            input_edges = sorted(
                resolved_edges,
                key=lambda edge: (
                    _norm(
                        (_two_ply_signature_input_identity(input_by_key.get(edge.input_key or ""), edge) or {}).get("role")
                        or edge.role_code
                    ),
                    str(edge.input_key or ""),
                ),
            )
    for edge in input_edges:
        item = input_by_key.get(edge.input_key or "")
        input_identity = (
            _two_ply_signature_input_identity(item, edge)
            if segment and _is_2ply_segment(segment) and edge.edge_type == "rm_input"
            else _material_signature_input_identity(item)
        )
        inputs.append(
            {
                "edge_type": edge.edge_type,
                "role_code": edge.role_code,
                "qty": edge.qty,
                "uom": edge.uom,
                "input": input_identity,
            }
        )
    for edge in plan.edges:
        if edge.to_key == segment_key and edge.edge_type == "previous_output":
            inputs.append(
                {
                    "edge_type": edge.edge_type,
                    "from_key": edge.from_key,
                    "output_key": edge.output_key,
                    "qty": edge.qty,
                    "uom": edge.uom,
                    "source_material_signature": _material_signature_for_segment_seen(plan, edge.from_key or "", seen)
                    if edge.from_key
                    else "",
                }
            )
    return _stable_hash({"segment_key": segment_key, "inputs": inputs})


def _material_signature_input_identity(item: Optional[ManufacturingInput]) -> Optional[Dict[str, Any]]:
    if not item:
        return None
    metadata = dict(item.metadata or {})
    preview_metadata = dict(metadata.get("preview_metadata") or {})
    resolution = dict(preview_metadata.get("resolution") or metadata.get("resolution") or {})
    return {
        "role_code": item.role_code,
        "semantic_role": item.semantic_role,
        "category": item.category,
        "source": item.source,
        "product_tmpl_id": item.product_tmpl_id,
        "material_type": item.material_type,
        "default_bucket": item.default_bucket,
        "input_quantity_node_key": item.input_quantity_node_key,
        "output_quantity_node_key": item.output_quantity_node_key,
        "metadata": {
            "writer_product_key": metadata.get("writer_product_key"),
            "product_id": metadata.get("product_id") or resolution.get("product_id"),
            "uom_id": metadata.get("uom_id") or resolution.get("uom_id"),
            "uom_name": metadata.get("uom_name") or resolution.get("uom_name"),
            "writer_uom": metadata.get("writer_uom"),
            "source_kind": metadata.get("source_kind"),
            "resolution_source": metadata.get("resolution_source"),
            "required_deckle_cm": metadata.get("required_deckle_cm"),
            "material_template_id": metadata.get("material_template_id"),
            "material_template_name": metadata.get("material_template_name"),
            "legacy_role_aliases": list(metadata.get("legacy_role_aliases") or []),
            "preview": {
                key: preview_metadata.get(key)
                for key in (
                    "gsm",
                    "bf",
                    "color",
                    "flute",
                    "flute_code",
                    "default_code",
                    "product_default_code",
                    "internal_ref",
                    "material_template_id",
                    "material_template_name",
                    "top_sheet_mode",
                    "top_sheet_product_id",
                    "top_sheet_roll_code",
                    "type",
                    "kind",
                    "selected_layout",
                )
                if preview_metadata.get(key) not in (None, "", [], {})
            },
        },
    }


def _dedupe_signature_resolved_material_edges(
    edges: List[ManufacturingEdge],
    input_by_key: Dict[str, ManufacturingInput],
) -> List[ManufacturingEdge]:
    result: List[ManufacturingEdge] = []
    seen = set()
    for edge in edges:
        item = input_by_key.get(edge.input_key or "")
        identity = _material_signature_input_identity(item)
        if not _material_signature_identity_looks_resolved(identity):
            continue
        key = (
            edge.edge_type,
            edge.role_code,
            _stable_hash(identity),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result


def _material_signature_identity_looks_resolved(identity: Optional[Dict[str, Any]]) -> bool:
    if not identity:
        return False
    metadata = dict(identity.get("metadata") or {})
    return bool(
        metadata.get("writer_product_key")
        or metadata.get("product_id")
    )


def _resolution_signature_for_segment(plan: ManufacturingPlan, segment_key: str) -> str:
    input_by_key = _input_by_key(plan)
    entries = []
    for edge in plan.edges:
        if edge.to_key != segment_key:
            continue
        item = input_by_key.get(edge.input_key or "")
        if not item:
            continue
        entries.append(
            {
                "input_key": item.input_key,
                "product_tmpl_id": item.product_tmpl_id,
                "material_profile_id": item.material_profile_id,
                "metadata": item.metadata,
            }
        )
    return _stable_hash({"segment_key": segment_key, "entries": entries})


def _input_by_key(plan: ManufacturingPlan) -> Dict[str, ManufacturingInput]:
    result: Dict[str, ManufacturingInput] = {}
    for segment in plan.segments:
        for item in _segment_inputs(segment):
            result[item.input_key] = item
    return result


def _segment_inputs(segment: ManufacturingSegment) -> List[ManufacturingInput]:
    return (
        list(segment.material_inputs or [])
        + list(segment.consumable_inputs or [])
        + list(segment.intermediate_inputs or [])
    )


def _last_output_segment(plan: ManufacturingPlan) -> Optional[ManufacturingSegment]:
    for segment in reversed(plan.segments):
        if segment.output and segment.output.output_role in ("sfg", "fg"):
            return segment
    return None


def _apply_style_family_terminal_semantics(
    segments: List[ManufacturingSegment],
    metadata: Dict[str, Any],
) -> List[ManufacturingSegment]:
    """Apply family-level terminal output semantics before materialization.

    Non-box product families such as sheet and roll are sellable at the
    material output itself. If the selected route has no explicit FG stage,
    promote the final material output to FG so the BoM attaches to the
    spec's finished product instead of a generated terminal SFG.
    """

    family = _norm((metadata or {}).get("style_family"))
    if family not in {"sheet", "roll"}:
        return segments
    if any(segment.output and segment.output.output_role == "fg" for segment in segments):
        return segments

    last_idx = None
    for idx, segment in enumerate(segments):
        if segment.output and segment.output.output_role == "sfg":
            last_idx = idx
    if last_idx is None:
        return segments

    segment = segments[last_idx]
    output = segment.output
    if output is None:
        return segments
    promoted_output = replace(
        output,
        output_role="fg",
        stock_behavior="fg",
    )
    promoted_segment = replace(
        segment,
        output=promoted_output,
        output_stock_behavior="fg",
    )
    result = list(segments)
    result[last_idx] = promoted_segment
    return result


def _build_output(
    ctx: Any,
    segment_key: str,
    *,
    execution_type: str,
    output_stock_behavior: str,
    output_kind: Optional[str],
    target_output_qty: float,
    target_output_uom: Optional[str],
    output_descriptor: Dict[str, Any],
    warnings: List[ManufacturingPlanWarning],
    stage_warnings: List[str],
) -> Optional[ManufacturingOutput]:
    is_material = execution_type == "material"
    if not is_material:
        return None

    if target_output_qty <= 0.0:
        code = "material_output_qty_missing"
        stage_warnings.append(code)
        warnings.append(
            ManufacturingPlanWarning(
                code=code,
                segment_key=segment_key,
                message="Material stage has no positive target output quantity.",
            )
        )
        return None

    if output_stock_behavior in ("semi_finished", "sfg"):
        output_role = "sfg"
    elif output_stock_behavior == "fg":
        output_role = "fg"
    else:
        output_role = "non_stock"

    output_key = f"{segment_key}:output"
    fingerprint_payload = {
        "stage_contract_code": _get(ctx, "stage_contract_code", None),
        "stage_template_id": _get(ctx, "stage_template_id", None),
        "execution_type": execution_type,
        "output_stock_behavior": output_stock_behavior,
        "output_kind": output_kind,
        "output_descriptor": output_descriptor,
        "target_output_uom": target_output_uom,
    }
    return ManufacturingOutput(
        output_key=output_key,
        output_role=output_role,
        output_kind=output_kind,
        stock_behavior=output_stock_behavior or None,
        qty=target_output_qty,
        uom=target_output_uom,
        descriptor=output_descriptor,
        identity_fingerprint=_stable_hash(fingerprint_payload),
        product_hint=None,
    )


def _previous_output_edges(
    contexts: List[Any],
    segments: List[ManufacturingSegment],
    warnings: List[ManufacturingPlanWarning],
) -> List[ManufacturingEdge]:
    result: List[ManufacturingEdge] = []
    previous_output_segment: Optional[ManufacturingSegment] = None
    pending_surface_output_segment: Optional[ManufacturingSegment] = None
    pending_corrugation_output_segment: Optional[ManufacturingSegment] = None

    for ctx, segment in zip(contexts, segments):
        consumes_previous = bool(_get(ctx, "consumes_previous_output", False))
        previous_required = bool(_get(ctx, "previous_output_required", False))
        input_qty = _safe_float(_get(ctx, "input_qty_from_prev", 0.0), 0.0)
        should_link = consumes_previous or previous_required

        if should_link:
            source_segment = previous_output_segment
            bypass_surface_into_corrugation = bool(
                source_segment
                and (
                    _is_surface_roll_output_segment(source_segment)
                    or _is_surface_processing_output_segment(source_segment)
                )
                and _is_corrugation_segment(segment)
            )
            if bypass_surface_into_corrugation:
                source_segment = None

            if source_segment and source_segment.output:
                result.append(
                    ManufacturingEdge(
                        edge_type="previous_output",
                        from_key=source_segment.key,
                        to_key=segment.key,
                        output_key=source_segment.output.output_key,
                        qty=input_qty,
                        uom=source_segment.output.uom,
                        metadata={
                            "consumes_previous_output": consumes_previous,
                            "previous_output_required": previous_required,
                            "preserve_previous_output_qty": _should_preserve_previous_output_qty(
                                source_segment,
                                segment,
                            ),
                        },
                    )
                )
            elif not bypass_surface_into_corrugation:
                warnings.append(
                    ManufacturingPlanWarning(
                        code="previous_output_source_missing",
                        segment_key=segment.key,
                        message="Stage requires previous output but no prior material output is available.",
                        metadata={
                            "consumes_previous_output": consumes_previous,
                            "previous_output_required": previous_required,
                        },
                    )
                )

            if (
                pending_corrugation_output_segment
                and pending_corrugation_output_segment.output
                and pending_corrugation_output_segment.key != (source_segment.key if source_segment else "")
                and not (source_segment and _is_board_segment(source_segment))
                and _is_board_segment(segment)
            ):
                result.append(
                    ManufacturingEdge(
                        edge_type="previous_output",
                        from_key=pending_corrugation_output_segment.key,
                        to_key=segment.key,
                        output_key=pending_corrugation_output_segment.output.output_key,
                        qty=_segment_output_qty(pending_corrugation_output_segment, 1.0),
                        uom=pending_corrugation_output_segment.output.uom,
                        metadata={
                            "consumes_previous_output": True,
                            "previous_output_required": True,
                            "corrugation_merge_input": True,
                            "preserve_previous_output_qty": True,
                        },
                    )
                )

            if (
                pending_surface_output_segment
                and pending_surface_output_segment.output
                and pending_surface_output_segment.key != (source_segment.key if source_segment else "")
                and not (source_segment and _is_board_segment(source_segment))
                and _is_board_segment(segment)
            ):
                result.append(
                    ManufacturingEdge(
                        edge_type="previous_output",
                        from_key=pending_surface_output_segment.key,
                        to_key=segment.key,
                        output_key=pending_surface_output_segment.output.output_key,
                        qty=_segment_output_qty(pending_surface_output_segment, 1.0),
                        uom=pending_surface_output_segment.output.uom,
                        metadata={
                            "consumes_previous_output": True,
                            "previous_output_required": True,
                            "surface_merge_input": True,
                            "preserve_previous_output_qty": True,
                        },
                    )
                )

        if segment.output and segment.output.output_role in ("sfg", "fg"):
            if _is_surface_roll_output_segment(segment) or (
                pending_surface_output_segment and _is_surface_processing_output_segment(segment)
            ):
                pending_surface_output_segment = segment
            if _is_corrugation_segment(segment) and not _is_board_segment(segment):
                pending_corrugation_output_segment = segment
            previous_output_segment = segment

    return result


def _filter_explicit_top_sheet_duplicate_inputs(
    incoming_inputs: Dict[str, List[Tuple[ManufacturingEdge, str]]],
    plan: ManufacturingPlan,
    segment_by_key: Dict[str, ManufacturingSegment],
) -> Dict[str, List[Tuple[ManufacturingEdge, str]]]:
    """Drop only stack-liner duplicates already owned by explicit top-sheet cut.

    Server data can carry the same resolved paper once as an explicit
    ``top_roll`` allocation on Top Sheet Cut and again as a degraded stack
    ``liner`` allocation on the downstream board/top-sheet output. Keep the
    explicit top-sheet owner and suppress only the exact duplicate stack row.
    """

    input_by_key = _input_by_key(plan)
    explicit_top_sheet_keys = set()
    allocation_trace = []

    for owner_key, rows in (incoming_inputs or {}).items():
        for edge, operation_segment_key in rows or []:
            item = input_by_key.get(edge.input_key or "")
            role = _input_role(item) or _norm(edge.role_code)
            if role not in {"top_roll", "top_sheet", "top_sheet_external"}:
                continue
            segment = segment_by_key.get(edge.to_key or "")
            if not _is_top_sheet_cut_segment(segment):
                continue
            identity = _top_sheet_duplicate_identity(owner_key, edge, item)
            if not identity:
                continue
            explicit_top_sheet_keys.add(identity)
            allocation_trace.append(
                _allocation_trace_row(
                    "explicit_top_sheet_owner",
                    owner_key,
                    operation_segment_key,
                    edge,
                    item,
                )
            )

    if not explicit_top_sheet_keys:
        return incoming_inputs

    filtered: Dict[str, List[Tuple[ManufacturingEdge, str]]] = {}
    for owner_key, rows in (incoming_inputs or {}).items():
        kept = []
        for edge, operation_segment_key in rows or []:
            item = input_by_key.get(edge.input_key or "")
            role = _input_role(item) or _norm(edge.role_code)
            identity = _top_sheet_duplicate_identity(owner_key, edge, item)
            if role == "liner" and identity in explicit_top_sheet_keys:
                allocation_trace.append(
                    _allocation_trace_row(
                        "suppressed_stack_liner_duplicate",
                        owner_key,
                        operation_segment_key,
                        edge,
                        item,
                    )
                )
                continue
            kept.append((edge, operation_segment_key))
        filtered[owner_key] = kept

    if allocation_trace:
        for row in allocation_trace:
            _logger.info("Engine V2 Top Sheet Cut allocation trace: %s", row)
        object.__setattr__(plan, "metadata", {
            **dict(plan.metadata or {}),
            "top_sheet_allocation_trace": allocation_trace,
        })
    return filtered


def _top_sheet_duplicate_identity(
    owner_key: str,
    edge: ManufacturingEdge,
    item: Optional[ManufacturingInput],
) -> Optional[Tuple[Any, ...]]:
    if not item:
        return None
    metadata = dict(item.metadata or {})
    preview_metadata = dict(metadata.get("preview_metadata") or {})
    product = (
        metadata.get("writer_product_key")
        or metadata.get("product_id")
        or item.product_tmpl_id
    )
    source_layer = (
        preview_metadata.get("source_layer_no")
        or metadata.get("source_layer_no")
        or preview_metadata.get("layer_no")
        or metadata.get("layer_no")
    )
    source_piece = metadata.get("piece_key") or preview_metadata.get("piece_key")
    unit_basis = _norm(metadata.get("writer_uom") or edge.uom)
    qty_basis = _norm(metadata.get("quantity_basis") or metadata.get("quantity_source"))
    if not product or not source_layer or not unit_basis:
        return None
    return (
        owner_key,
        "top_sheet_surface",
        product,
        _safe_int(source_layer, 0),
        _norm(source_piece),
        unit_basis,
        qty_basis,
    )


def _allocation_trace_row(
    allocation_source: str,
    owner_key: str,
    operation_segment_key: str,
    edge: ManufacturingEdge,
    item: Optional[ManufacturingInput],
) -> Dict[str, Any]:
    metadata = dict(item.metadata or {}) if item else {}
    preview_metadata = dict(metadata.get("preview_metadata") or {})
    return {
        "allocation_source": allocation_source,
        "stage_code": edge.to_key,
        "operation_segment_key": operation_segment_key,
        "owner_stage_output_node": owner_key,
        "stage_template_id": metadata.get("stage_template_id") or preview_metadata.get("stage_template_id"),
        "rm_role": _input_role(item) or _norm(edge.role_code),
        "product_id": (metadata.get("product_id") or item.product_tmpl_id) if item else None,
        "writer_product_key": metadata.get("writer_product_key"),
        "quantity": edge.qty,
        "uom": edge.uom,
        "source_layer": (
            preview_metadata.get("source_layer_no")
            or metadata.get("source_layer_no")
            or preview_metadata.get("layer_no")
            or metadata.get("layer_no")
        ),
        "source_piece": metadata.get("piece_key") or preview_metadata.get("piece_key"),
    }


def _is_top_sheet_cut_segment(segment: Optional[ManufacturingSegment]) -> bool:
    if not segment:
        return False
    text = " ".join(
        _norm(value)
        for value in (
            segment.key,
            segment.stage_code,
            segment.stage_name,
            segment.stage_contract_code,
            segment.capability_code,
            (segment.metadata or {}).get("operation_key"),
            (segment.metadata or {}).get("operation_name"),
        )
    )
    return "top_sheet_cut" in text or "ts_cut" in text or ("top" in text and "cut" in text)


def _is_surface_roll_output_segment(segment: ManufacturingSegment) -> bool:
    if not segment or not segment.output:
        return False
    stage_text = " ".join(
        [
            _norm(segment.stage_code),
            _norm(segment.stage_name),
            _norm(segment.stage_type),
            _norm(segment.capability_code),
            _norm(segment.stage_contract_code),
        ]
    )
    if not any(token in stage_text for token in ("cut", "print", "top_sheet")):
        return False
    for item in segment.material_inputs or []:
        if _input_role(item) == "top_roll":
            return True
    return False


def _is_surface_processing_output_segment(segment: ManufacturingSegment) -> bool:
    if not segment or not segment.output:
        return False
    stage_text = " ".join(
        [
            _norm(segment.stage_code),
            _norm(segment.stage_name),
            _norm(segment.stage_type),
            _norm(segment.capability_code),
            _norm(segment.stage_contract_code),
        ]
    )
    return any(token in stage_text for token in ("print", "lamination", "laminat", "top_sheet"))


def _is_corrugation_segment(segment: ManufacturingSegment) -> bool:
    values = [
        segment.key,
        segment.stage_code,
        segment.stage_type,
        segment.capability_code,
        segment.stage_contract_code,
        (segment.metadata or {}).get("operation_key"),
        (segment.metadata or {}).get("operation_name"),
    ]
    return any("corrug" in _norm(value) or "2ply" in _norm(value) or "2_ply" in _norm(value) for value in values)


def _build_input_role(item: Any) -> ManufacturingInputRole:
    return ManufacturingInputRole(
        role_code=str(_get(item, "role_code", "") or ""),
        category=str(_get(item, "category", "") or ""),
        requirement=str(_get(item, "requirement", "") or ""),
        source=str(_get(item, "source", "") or ""),
        priority=_safe_int(_get(item, "priority", 0), 0),
    )


def _build_input(item: Any, segment_key: str, bucket: str, index: int) -> ManufacturingInput:
    role_code = _none_if_blank(_get(item, "role_code", None))
    semantic_role = _none_if_blank(_get(item, "semantic_role", None))
    extra_metadata = {}
    if isinstance(item, dict):
        for key in (
            "preview_metadata",
            "writer_product_key",
            "writer_qty",
            "writer_uom",
            "qty_per_fg",
            "total_qty",
            "writer_stage",
            "original_semantic_stage",
            "assigned_route_stage",
            "fallback_reason",
            "quantity_source",
            "trace_only",
            "product_id",
            "product_name",
            "product_label",
            "uom_id",
            "uom_name",
            "source_kind",
            "resolution_source",
            "required_deckle_cm",
            "selected_layout",
        ):
            if key in item:
                extra_metadata[key] = item.get(key)
    extra_metadata["legacy_role_aliases"] = list(_as_list(_get(item, "legacy_role_aliases", [])))
    input_key = ":".join(
        [
            segment_key,
            bucket,
            str(role_code or semantic_role or "input"),
            str(_safe_optional_int(_get(item, "material_profile_id", None)) or ""),
            str(_safe_optional_int(_get(item, "rule_id", None)) or ""),
            str(index),
        ]
    )
    return ManufacturingInput(
        input_key=input_key,
        role_code=role_code,
        semantic_role=semantic_role,
        category=_none_if_blank(_get(item, "category", None)),
        source=str(_get(item, "source", "") or ""),
        material_profile_id=_safe_optional_int(_get(item, "material_profile_id", None)),
        product_tmpl_id=_safe_optional_int(_get(item, "product_tmpl_id", None)),
        rule_id=_safe_optional_int(_get(item, "rule_id", None)),
        material_type=_none_if_blank(_get(item, "material_type", None)),
        default_bucket=_none_if_blank(_get(item, "default_bucket", None)),
        input_quantity_node_key=_none_if_blank(_get(item, "input_quantity_node_key", None)),
        output_quantity_node_key=_none_if_blank(_get(item, "output_quantity_node_key", None)),
        metadata=extra_metadata,
    )


def _input_edge(edge_type: str, item: ManufacturingInput, segment_key: str) -> ManufacturingEdge:
    return ManufacturingEdge(
        edge_type=edge_type,
        from_key=None,
        to_key=segment_key,
        input_key=item.input_key,
        role_code=item.role_code,
        qty=0.0,
        uom=None,
        metadata={
            "semantic_role": item.semantic_role,
            "category": item.category,
            "material_profile_id": item.material_profile_id,
            "product_tmpl_id": item.product_tmpl_id,
            "rule_id": item.rule_id,
        },
    )


def _flatten_plan_components(plan: ManufacturingPlan) -> List[Dict[str, Any]]:
    components: List[Dict[str, Any]] = []
    segment_by_key = {segment.key: segment for segment in plan.segments}
    input_by_key: Dict[str, ManufacturingInput] = {}
    for segment in plan.segments:
        for item in list(segment.material_inputs or []) + list(segment.consumable_inputs or []) + list(segment.intermediate_inputs or []):
            input_by_key[item.input_key] = item

    for edge in plan.edges:
        if edge.edge_type not in ("rm_input", "consumable_input", "intermediate_input"):
            continue
        segment = segment_by_key.get(edge.to_key)
        item = input_by_key.get(edge.input_key or "")
        if not segment or not item:
            continue
        meta = dict(item.metadata or {})
        preview_metadata = dict(meta.get("preview_metadata") or {})
        semantic_role = item.semantic_role or item.role_code or ""
        layer_no = _safe_int(preview_metadata.get("layer_no"), 0)
        component_key = "|".join(
            [
                str(segment.stage_contract_code or segment.stage_code or ""),
                str(semantic_role or ""),
                str(item.product_tmpl_id or ""),
                str(layer_no or ""),
            ]
        )
        operation_key = str((segment.metadata or {}).get("operation_key") or "")
        operation_name = str((segment.metadata or {}).get("operation_name") or "")
        struct_meta = dict(preview_metadata)
        struct_meta["semantic_role"] = semantic_role
        struct_meta["legacy_role_aliases"] = list(meta.get("legacy_role_aliases") or [])
        struct_meta["stage_code"] = segment.stage_code
        struct_meta["stage_contract_code"] = segment.stage_contract_code
        struct_meta["stage_template_id"] = segment.stage_template_id
        struct_meta["operation_key"] = operation_key or None
        struct_meta["operation_name"] = operation_name or None
        struct_meta["quantity_source"] = meta.get("quantity_source") or ""
        struct_meta["component_key"] = component_key
        if str(semantic_role).startswith("adhesive"):
            struct_meta["kind"] = semantic_role
        elif semantic_role in ("liner", "medium"):
            struct_meta["type"] = semantic_role
            struct_meta["role"] = semantic_role
            if layer_no:
                struct_meta["layer_no"] = layer_no
        components.append(
            {
                "product": meta.get("writer_product_key") or "",
                "qty": _safe_float(meta.get("writer_qty"), 0.0),
                "uom": meta.get("writer_uom") or "",
                "stage": meta.get("writer_stage") or operation_key or "",
                "metadata": struct_meta,
            }
        )
    return components


def _flatten_plan_routed_operations(plan: ManufacturingPlan) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for segment in plan.segments:
        operation_key = str((segment.metadata or {}).get("operation_key") or "")
        if not operation_key:
            continue
        metrics = dict((segment.metadata or {}).get("operation_metrics") or {})
        output_qty = _safe_float(metrics.get("output_qty"), 0.0)
        if output_qty <= 0.0:
            continue
        rows.append(
            {
                "stage": operation_key,
                "operation_key": operation_key,
                "stage_code": segment.stage_code,
                "stage_contract_code": segment.stage_contract_code,
                "stage_template_id": segment.stage_template_id,
                "stage_name": segment.stage_name,
                "stage_type": segment.stage_type or "",
                "capability_code": segment.capability_code or "",
                "input_qty": _safe_float(metrics.get("input_qty"), 0.0),
                "output_qty": output_qty,
                "uom": metrics.get("uom") or "",
                "source": metrics.get("source") or "",
                "sequence": _safe_int(segment.sequence, 0),
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            _safe_int(item.get("sequence"), 0),
            str(item.get("stage_code") or ""),
            str(item.get("operation_key") or ""),
        ),
    )


def _flatten_plan_grouped_operations(routed_operations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in routed_operations:
        op_name = str(item.get("operation_key") or item.get("stage") or "")
        input_qty = _safe_float(item.get("input_qty"), 0.0)
        output_qty = _safe_float(item.get("output_qty"), 0.0)
        if input_qty <= 0.0 or output_qty <= 0.0:
            continue
        detail = {
            "stage_code": item.get("stage_code"),
            "stage_contract_code": item.get("stage_contract_code"),
            "stage_template_id": item.get("stage_template_id"),
            "input_qty": input_qty,
            "output_qty": output_qty,
            "uom": item.get("uom") or "",
            "source": item.get("source") or "",
            "sequence": _safe_int(item.get("sequence"), 0),
        }
        current = grouped.get(op_name)
        if not current:
            grouped[op_name] = {
                "stage": op_name,
                "input_qty": input_qty,
                "output_qty": output_qty,
                "stage_details": [detail],
            }
            continue
        current["input_qty"] = min(_safe_float(current.get("input_qty"), input_qty), input_qty)
        current["output_qty"] = max(_safe_float(current.get("output_qty"), output_qty), output_qty)
        current.setdefault("stage_details", []).append(detail)

    result = []
    for item in grouped.values():
        item["stage_details"] = sorted(
            list(item.get("stage_details") or []),
            key=lambda detail: (
                _safe_int(detail.get("sequence"), 0),
                str(detail.get("stage_code") or ""),
            ),
        )
        result.append(item)
    return result


def _fg_segment_key(segments: List[ManufacturingSegment]) -> Optional[str]:
    fg_segments = [
        segment.key
        for segment in segments
        if segment.output and segment.output.output_role == "fg"
    ]
    return fg_segments[-1] if fg_segments else None


def _segment_key(ctx: Any, fallback_index: int) -> str:
    contract = str(_get(ctx, "stage_contract_code", "") or "").strip()
    if contract:
        return f"contract:{contract}"
    template_id = _safe_optional_int(_get(ctx, "stage_template_id", None))
    if template_id:
        return f"template:{template_id}"
    index = _safe_int(_get(ctx, "index", fallback_index), fallback_index)
    return f"index:{index}"


def _parent_material_identity(ctx: Any) -> Optional[str]:
    contract = str(_get(ctx, "parent_material_stage_contract_code", "") or "").strip()
    if contract:
        return f"contract:{contract}"
    template_id = _safe_optional_int(_get(ctx, "parent_material_stage_template_id", None))
    if template_id:
        return f"template:{template_id}"
    parent_index = _safe_optional_int(_get(ctx, "parent_material_index", None))
    if parent_index is not None:
        return f"index:{parent_index}"
    return None


def _parent_identity_index(contexts: List[Any], segments: List[ManufacturingSegment]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for fallback_index, (ctx, segment) in enumerate(zip(contexts, segments)):
        contract = str(_get(ctx, "stage_contract_code", "") or "").strip()
        if contract:
            result[f"contract:{contract}"] = segment.key
        template_id = _safe_optional_int(_get(ctx, "stage_template_id", None))
        if template_id:
            result[f"template:{template_id}"] = segment.key
        index = _safe_int(_get(ctx, "index", fallback_index), fallback_index)
        result[f"index:{index}"] = segment.key
    return result


def _segment_with_parent_key(
    segment: ManufacturingSegment, parent_material_key: Optional[str]
) -> ManufacturingSegment:
    if segment.parent_material_key == parent_material_key:
        return segment
    data = asdict(segment)
    data["parent_material_key"] = parent_material_key
    if data.get("output"):
        data["output"] = ManufacturingOutput(**data["output"])
    data["input_roles"] = [ManufacturingInputRole(**item) for item in data.get("input_roles", [])]
    data["material_inputs"] = [ManufacturingInput(**item) for item in data.get("material_inputs", [])]
    data["consumable_inputs"] = [ManufacturingInput(**item) for item in data.get("consumable_inputs", [])]
    data["intermediate_inputs"] = [ManufacturingInput(**item) for item in data.get("intermediate_inputs", [])]
    return ManufacturingSegment(**data)


def _preview_material_as_attachment(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    resolution = dict(metadata.get("resolution") or {})
    effective_role = _effective_preview_material_role(row)
    product_id = (
        _safe_optional_int(row.get("product_id"))
        or _safe_optional_int(resolution.get("product_id"))
        or _safe_optional_int(metadata.get("top_sheet_product_id"))
    )
    writer_product_key = row.get("product_key") or row.get("writer_product_key") or ""
    if product_id and not str(writer_product_key or "").startswith("product."):
        writer_product_key = "product.product:%s" % product_id
    return {
        "role_code": effective_role,
        "semantic_role": effective_role,
        "category": row.get("category") or row.get("material_type"),
        "source": row.get("source") or "preview_material_row",
        "material_profile_id": row.get("material_profile_id"),
        "product_id": product_id,
        "product_name": (
            row.get("product_name")
            or row.get("product_label")
            or row.get("product")
            or resolution.get("product_name")
        ),
        "product_tmpl_id": row.get("product_tmpl_id"),
        "rule_id": row.get("rule_id"),
        "material_type": row.get("material_type"),
        "default_bucket": row.get("default_bucket"),
        "input_quantity_node_key": row.get("input_quantity_node_key"),
        "output_quantity_node_key": row.get("output_quantity_node_key"),
        "legacy_role_aliases": list(row.get("legacy_role_aliases") or []),
        "preview_metadata": metadata,
        "writer_product_key": writer_product_key,
        "writer_qty": row.get("total_qty"),
        "writer_uom": row.get("uom"),
        "qty_per_fg": row.get("qty_per_fg"),
        "total_qty": row.get("total_qty"),
        "writer_stage": row.get("component_stage") or row.get("writer_stage"),
        "original_semantic_stage": row.get("original_semantic_stage") or metadata.get("original_semantic_stage"),
        "assigned_route_stage": row.get("assigned_route_stage") or metadata.get("assigned_route_stage"),
        "fallback_reason": row.get("fallback_reason") or metadata.get("fallback_reason"),
        "quantity_source": row.get("quantity_source") or "",
        "uom_id": row.get("uom_id") or resolution.get("uom_id"),
        "uom_name": row.get("uom_name") or resolution.get("uom_name"),
        "source_kind": row.get("source_kind") or row.get("source") or row.get("resolution_source") or "preview_material_row",
        "resolution_source": row.get("resolution_source"),
        "required_deckle_cm": row.get("required_deckle_cm"),
        "selected_layout": dict(row.get("selected_layout") or {}),
        "trace_only": bool(row.get("trace_only")),
    }


def _fallback_context_for_material_row(
    stage_contexts: List[Dict[str, Any]],
    row: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    semantic_stage = _semantic_stage_for_material_row(row)
    if not semantic_stage:
        return None, {}
    active_stages = [_stage_token(ctx) for ctx in stage_contexts]
    if semantic_stage in active_stages:
        for ctx in stage_contexts:
            if _stage_token(ctx) == semantic_stage:
                return ctx, {
                    "original_semantic_stage": semantic_stage,
                    "assigned_route_stage": semantic_stage,
                    "fallback_reason": "semantic_stage_identity_miss",
                    "component_stage": semantic_stage,
                    "writer_stage": semantic_stage,
                }
        return None, {}
    assigned = _nearest_downstream_stage(semantic_stage, active_stages)
    if not assigned:
        return None, {}
    for ctx in stage_contexts:
        if _stage_token(ctx) == assigned:
            return ctx, {
                "original_semantic_stage": semantic_stage,
                "assigned_route_stage": assigned,
                "fallback_reason": "missing_stage",
                "component_stage": assigned,
                "writer_stage": assigned,
            }
    return None, {}


def _semantic_stage_for_material_row(row: Dict[str, Any]) -> str:
    metadata = dict((row or {}).get("metadata") or {})
    for value in (
        row.get("component_stage"),
        row.get("writer_stage"),
        row.get("stage_code"),
        row.get("stage"),
        row.get("stage_contract_code"),
        metadata.get("component_stage"),
        metadata.get("writer_stage"),
        metadata.get("stage_code"),
        metadata.get("stage"),
        metadata.get("stage_contract_code"),
    ):
        stage = _canonical_route_stage(value)
        if stage:
            return stage
    role = _effective_preview_material_role(row)
    if role in {"liner", "medium", "fluting", "top_liner", "bottom_liner"} or "liner" in role or "medium" in role:
        return "2ply"
    if role in {"top_sheet_external", "top_sheet", "top_roll"}:
        return "board"
    return ""


def _stage_token(ctx: Dict[str, Any]) -> str:
    for value in (
        ctx.get("stage_code"),
        ctx.get("stage"),
        ctx.get("stage_contract_code"),
        ctx.get("stage_name"),
        ctx.get("stage_type"),
    ):
        stage = _canonical_route_stage(value)
        if stage:
            return stage
    return ""


def _canonical_route_stage(value: Any) -> str:
    text = _norm(value)
    if not text:
        return ""
    if "top_sheet_cut" in text or "ts_cut" in text or ("top" in text and "cut" in text):
        return "top_sheet_cut"
    if "top_sheet_print" in text or "ts_print" in text or ("top" in text and "print" in text):
        return "printing"
    if "2ply" in text or "2_ply" in text or "2-ply" in text or "corrug" in text:
        return "2ply"
    if "board" in text:
        return "board"
    if "print" in text:
        return "printing"
    if "laminat" in text:
        return "lamination"
    if "die" in text or "slot" in text:
        return "diecut"
    if "seam" in text or "stitch" in text or "glue" in text:
        return "seaming"
    if "pack" in text:
        return "packing"
    return text


def _nearest_downstream_stage(semantic_stage: str, active_stages: List[str]) -> str:
    active = [stage for stage in active_stages if stage]
    if not active:
        return ""
    if semantic_stage in active:
        return semantic_stage
    precedence = ["top_sheet_cut", "printing", "2ply", "board", "lamination", "diecut", "seaming", "packing"]
    try:
        start = precedence.index(semantic_stage)
    except ValueError:
        start = -1
    if start >= 0:
        for stage in precedence[start + 1 :]:
            if stage in active:
                return stage
    return active[0]


def _paper_resolution_lookup(rows: List[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    lookup: Dict[Any, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict) or row.get("trace_only"):
            continue
        role = _effective_preview_material_role(row)
        layer_no = _safe_int(row.get("layer_no"), 0)
        product_id = _safe_optional_int(row.get("product_id"))
        if not role or not product_id:
            continue
        lookup[(role, layer_no)] = row
        lookup[(role, 0)] = row
    return lookup


def _selected_layout_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    selected = payload.get("selected_layout")
    if isinstance(selected, dict) and selected:
        return dict(selected)
    shadow_selected = ((payload.get("phase8_shadow_runtime") or {}).get("selected_layout") or {})
    if isinstance(shadow_selected, dict) and shadow_selected:
        return dict(shadow_selected)
    for entry in payload.get("paper_resolution") or []:
        if not isinstance(entry, dict):
            continue
        layout = entry.get("selected_layout")
        if isinstance(layout, dict) and layout:
            return {
                "selection_source": layout.get("selection_source") or "",
                "stage_entry": {
                    "stage_code": layout.get("stage_code") or "",
                    "stage_name": layout.get("stage_name") or "",
                },
                "option": {
                    "up_count": layout.get("up_count"),
                    "final_sheet_length_mm": layout.get("final_sheet_length_mm"),
                    "final_sheet_width_mm": layout.get("final_sheet_width_mm"),
                    "orientation_used": layout.get("orientation_used") or "",
                    "matched_workcenter": dict(layout.get("matched_workcenter") or {}),
                },
            }
    return {}


def _style_family_from_preview_payload(payload: Dict[str, Any]) -> str:
    metrics = payload.get("metrics") or {}
    extra = metrics.get("extra") or {}
    style = extra.get("style") or {}
    metadata = style.get("metadata") or {}
    family = _norm(metadata.get("family"))
    if family:
        return family
    style_code = _norm(payload.get("style_code"))
    if style_code == "0110":
        return "sheet"
    return ""


def _layout_diagnostics(
    selected_layout: Dict[str, Any],
    materialized: ManufacturingMaterializationPlan,
) -> Dict[str, Any]:
    option = dict((selected_layout or {}).get("option") or {})
    layout_signature = ""
    if materialized.boms:
        layout_signature = materialized.boms[0].corr_layout_signature
    return {
        "selected_layout_up_count": option.get("up_count"),
        "selected_layout_sheet_width_mm": option.get("final_sheet_width_mm"),
        "selected_layout_sheet_length_mm": option.get("final_sheet_length_mm"),
        "selection_source": (selected_layout or {}).get("selection_source") or "",
        "materialization_layout_signature": layout_signature,
    }


def _enrich_preview_material_with_resolution(
    row: Dict[str, Any],
    paper_resolution_by_key: Dict[Any, Dict[str, Any]],
) -> Dict[str, Any]:
    enriched = dict(row)
    metadata = dict(row.get("metadata") or {})
    role = _effective_preview_material_role(row)
    layer_no = _safe_int(row.get("layer_no") or metadata.get("layer_no"), 0)
    resolution = paper_resolution_by_key.get((role, layer_no)) or paper_resolution_by_key.get((role, 0))
    if not resolution:
        return enriched
    product_id = _safe_optional_int(resolution.get("product_id"))
    if product_id:
        enriched.setdefault("product_id", product_id)
        enriched["product_key"] = "product.product:%s" % product_id
    if resolution.get("product_name"):
        enriched.setdefault("product_name", resolution.get("product_name"))
        enriched.setdefault("product_label", resolution.get("product_name"))
    if resolution.get("uom_id"):
        enriched.setdefault("uom_id", resolution.get("uom_id"))
    if resolution.get("uom_name"):
        enriched.setdefault("uom_name", resolution.get("uom_name"))
        enriched.setdefault("uom", resolution.get("uom_name"))
    if resolution.get("required_deckle_cm") is not None:
        enriched.setdefault("required_deckle_cm", resolution.get("required_deckle_cm"))
    if isinstance(resolution.get("selected_layout"), dict):
        enriched.setdefault("selected_layout", dict(resolution.get("selected_layout") or {}))
    enriched.setdefault("source_kind", "paper_resolution")
    enriched.setdefault("resolution_source", "paper_resolution")
    return enriched


def _effective_preview_material_role(row: Optional[Dict[str, Any]]) -> str:
    row = row or {}
    metadata = dict(row.get("metadata") or {})
    kind = _norm(metadata.get("kind"))
    role = _norm(row.get("role") or row.get("role_code"))
    semantic_role = _norm(row.get("semantic_role") or metadata.get("semantic_role"))

    # Roll-top paper can arrive with semantic_role downgraded to
    # top_sheet_external while the authoritative runtime row role/kind is
    # top_roll. Preserve top_roll so plan decomposition and materialization
    # keep the outer 180 GSM liner instead of reusing the remaining 140 GSM
    # board liner.
    if role == "top_roll" or kind == "top_roll":
        return "top_roll"
    return semantic_role or role


def _operation_key_from_stage_context_dict(ctx: Dict[str, Any]) -> Optional[str]:
    classifier = _norm(ctx.get("capability_code") or ctx.get("stage_type"))
    stage_text = " ".join(
        [
            _norm(ctx.get("stage_code")),
            _norm(ctx.get("stage_contract_code")),
            _norm(ctx.get("stage_name")),
        ]
    )
    if "top_sheet_cut" in stage_text or "ts_cut" in stage_text or ("top" in stage_text and "cut" in stage_text):
        return "cutting"
    if "board" in stage_text:
        return "board"
    if "2ply" in stage_text or "2_ply" in stage_text or "2-ply" in stage_text:
        return "corrugator"
    if "peel" in stage_text or "stripping" in stage_text:
        return "peeloff"
    if classifier == "corrugation":
        return "corrugator"
    if classifier == "printing":
        return "printing"
    if classifier == "lamination":
        return "lamination"
    if classifier == "cutting":
        return "cutting"
    if classifier == "diecut":
        return "diecut"
    if classifier == "conversion":
        return "conversion"

    output_kind = _norm((ctx.get("output_descriptor") or {}).get("kind") or ctx.get("output_kind"))
    output_stock = _norm(ctx.get("output_stock_behavior"))
    execution_type = _norm(ctx.get("execution_type"))
    if output_stock == "fg":
        return None
    if output_kind == "box" and execution_type == "material":
        return "conversion"
    return _operation_key_from_bucket(
        ctx.get("output_quantity_node_key") or ctx.get("input_quantity_node_key"),
        stage_text=stage_text,
    )


def _promote_yield_operation_context_for_materialization(
    ctx: Dict[str, Any],
    metrics: Dict[str, Any],
) -> None:
    """Represent a yield operation as a materialization boundary.

    The source StageContext remains route-driven. This planner-only promotion
    lets generated BoMs express a generic batch yield such as
    one sheet producing multiple blanks.
    """

    if str((metrics or {}).get("yield_basis") or "").strip().lower() != "fg_per_sheet":
        return
    if _norm(ctx.get("execution_type")) == "material":
        return
    input_uom = str((metrics or {}).get("input_uom") or "").strip().lower()
    output_uom = str((metrics or {}).get("output_uom") or (metrics or {}).get("uom") or "").strip().lower()
    output_label = str((metrics or {}).get("yield_output_label") or "").strip().lower()
    output_kind = str((metrics or {}).get("yield_output_kind") or ctx.get("output_kind") or "").strip().lower()
    has_blank_output = (
        output_uom in {"unit", "units"}
        or output_label in {"gross blanks", "usable blanks"}
        or output_kind in {"blank", "gross_blank", "usable_blank"}
    )
    if not has_blank_output:
        return
    output_qty = _safe_float(
        metrics.get("fg_per_sheet") or metrics.get("yield_relation_output_blanks"),
        0.0,
    )
    if output_qty <= 0.0:
        return
    ctx["execution_type"] = "material"
    ctx["output_stock_behavior"] = ctx.get("output_stock_behavior") or "semi_finished"
    ctx["target_output_qty"] = output_qty
    ctx["target_output_uom"] = metrics.get("output_uom") or metrics.get("uom") or "unit"
    ctx["output_kind"] = metrics.get("yield_output_kind") or ctx.get("output_kind") or "blank"
    ctx["output_descriptor"] = {
        **dict(ctx.get("output_descriptor") or {}),
        "kind": metrics.get("yield_output_kind") or "blank",
        "quantity": output_qty,
        "uom": metrics.get("output_uom") or metrics.get("uom") or "unit",
        "label": metrics.get("yield_output_label") or "",
    }
    ctx["consumes_previous_output"] = True
    ctx["previous_output_required"] = True
    ctx["input_qty_from_prev"] = _safe_float(metrics.get("yield_relation_input_sheets"), 0.0) or _safe_float(
        metrics.get("input_qty"),
        1.0,
    )
    ctx["bom_mapping_policy"] = ctx.get("bom_mapping_policy") or "yield_boundary"


def _promote_stock_output_operation_context_for_materialization(
    ctx: Dict[str, Any],
    metrics: Dict[str, Any],
) -> None:
    if _norm(ctx.get("execution_type")) == "material":
        return
    stock_behavior = _norm(ctx.get("output_stock_behavior"))
    if stock_behavior not in {"semi_finished", "sfg"}:
        return
    output_qty = _safe_float(
        metrics.get("fg_per_sheet")
        or metrics.get("yield_relation_output_blanks")
        or metrics.get("output_qty")
        or ctx.get("target_output_qty"),
        0.0,
    )
    if output_qty <= 0.0:
        return
    output_uom = str(metrics.get("output_uom") or metrics.get("uom") or ctx.get("target_output_uom") or "unit")
    output_kind = _norm((ctx.get("output_descriptor") or {}).get("kind") or ctx.get("output_kind"))
    if not output_kind:
        output_kind = "blank" if output_uom == "unit" else "sheet"
    ctx["execution_type"] = "material"
    ctx["output_stock_behavior"] = "semi_finished"
    ctx["target_output_qty"] = output_qty
    ctx["target_output_uom"] = output_uom
    ctx["output_kind"] = output_kind
    ctx["output_descriptor"] = {
        **dict(ctx.get("output_descriptor") or {}),
        "kind": output_kind,
        "quantity": output_qty,
        "uom": output_uom,
        "label": metrics.get("yield_output_label") or "",
    }
    ctx["consumes_previous_output"] = True
    ctx["previous_output_required"] = True
    ctx["input_qty_from_prev"] = _safe_float(metrics.get("input_qty"), 0.0) or output_qty
    ctx["bom_mapping_policy"] = ctx.get("bom_mapping_policy") or "stock_output_operation_boundary"


def _rebalance_fg_per_sheet_peeloff_chain(stage_contexts: List[Dict[str, Any]]) -> None:
    diecut_index = None
    peeloff_index = None

    for index, ctx in enumerate(stage_contexts or []):
        operation_key = _norm(ctx.get("_plan_operation_key"))
        stage_text = " ".join(
            [
                _norm(ctx.get("stage_code")),
                _norm(ctx.get("stage_type")),
                _norm(ctx.get("stage_name")),
                _norm(ctx.get("stage_contract_code")),
                operation_key,
            ]
        )
        if diecut_index is None and any(token in stage_text for token in ("diecut", "slotdie", "slot_die", "slot/die", "slot die")):
            diecut_index = index
        if peeloff_index is None and any(token in stage_text for token in ("peeloff", "peel-off", "stripping", "peeling")):
            peeloff_index = index

    if diecut_index is None or peeloff_index is None or peeloff_index <= diecut_index:
        return

    diecut_ctx = stage_contexts[diecut_index]
    peeloff_ctx = stage_contexts[peeloff_index]
    diecut_metrics = dict(diecut_ctx.get("_plan_operation_metrics") or {})
    peeloff_metrics = dict(peeloff_ctx.get("_plan_operation_metrics") or {})
    yield_basis = str(diecut_metrics.get("yield_basis") or peeloff_metrics.get("yield_basis") or "").strip().lower()
    if yield_basis != "fg_per_sheet":
        return

    required_sheets = _safe_float(
        peeloff_metrics.get("yield_relation_input_sheets")
        or diecut_metrics.get("yield_relation_input_sheets")
        or diecut_ctx.get("input_qty_from_prev")
        or 0.0,
        0.0,
    )
    yield_output_qty = _safe_float(
        peeloff_metrics.get("yield_relation_output_blanks")
        or diecut_metrics.get("yield_relation_output_blanks")
        or peeloff_metrics.get("fg_per_sheet")
        or diecut_metrics.get("fg_per_sheet")
        or 0.0,
        0.0,
    )
    if required_sheets <= 0.0 or yield_output_qty <= 0.0:
        return

    diecut_metrics.update(
        {
            "input_qty": required_sheets,
            "output_qty": required_sheets,
            "uom": "sheet",
            "input_uom": "sheet",
            "output_uom": "sheet",
            "yield_output_label": "gross diecut sheet",
            "yield_output_kind": "gross_sheet",
            "yield_relation_input_sheets": required_sheets,
            "yield_relation_output_blanks": yield_output_qty,
        }
    )
    diecut_ctx["_plan_operation_metrics"] = diecut_metrics
    diecut_ctx["execution_type"] = "material"
    diecut_ctx["output_stock_behavior"] = diecut_ctx.get("output_stock_behavior") or "semi_finished"
    diecut_ctx["target_output_qty"] = required_sheets
    diecut_ctx["target_output_uom"] = "sheet"
    diecut_ctx["output_kind"] = "gross_sheet"
    diecut_ctx["consumes_previous_output"] = True
    diecut_ctx["previous_output_required"] = True
    diecut_ctx["input_qty_from_prev"] = required_sheets
    diecut_ctx["bom_mapping_policy"] = "gross_sheet_boundary"
    diecut_ctx["output_descriptor"] = {
        **dict(diecut_ctx.get("output_descriptor") or {}),
        "kind": "gross_sheet",
        "quantity": required_sheets,
        "uom": "sheet",
        "label": "Diecut Gross Sheet",
    }

    peeloff_metrics.update(
        {
            "input_qty": required_sheets,
            "output_qty": yield_output_qty,
            "uom": "unit",
            "input_uom": "sheet",
            "output_uom": "unit",
            "yield_output_label": "usable blanks",
            "yield_output_kind": "blank",
            "yield_relation_input_sheets": required_sheets,
            "yield_relation_output_blanks": yield_output_qty,
        }
    )
    peeloff_ctx["_plan_operation_metrics"] = peeloff_metrics
    peeloff_ctx["execution_type"] = "material"
    peeloff_ctx["output_stock_behavior"] = "semi_finished"
    peeloff_ctx["target_output_qty"] = yield_output_qty
    peeloff_ctx["target_output_uom"] = "unit"
    peeloff_ctx["output_kind"] = "blank"
    peeloff_ctx["consumes_previous_output"] = True
    peeloff_ctx["previous_output_required"] = True
    peeloff_ctx["input_qty_from_prev"] = required_sheets
    peeloff_ctx["bom_mapping_policy"] = "yield_boundary"
    peeloff_ctx["output_descriptor"] = {
        **dict(peeloff_ctx.get("output_descriptor") or {}),
        "kind": "blank",
        "quantity": yield_output_qty,
        "uom": "unit",
        "label": "Peeloff Usable Blank",
    }

    for index in range(peeloff_index + 1, len(stage_contexts or [])):
        downstream_ctx = stage_contexts[index]
        if not bool(downstream_ctx.get("consumes_previous_output") or downstream_ctx.get("previous_output_required")):
            continue
        downstream_ctx["input_qty_from_prev"] = yield_output_qty
        downstream_metrics = dict(downstream_ctx.get("_plan_operation_metrics") or {})
        downstream_metrics["input_qty"] = yield_output_qty
        downstream_metrics["input_uom"] = "unit"
        downstream_metrics["uom"] = downstream_metrics.get("output_uom") or downstream_metrics.get("uom") or "unit"
        if _norm(downstream_ctx.get("output_stock_behavior")) == "fg":
            downstream_metrics["output_qty"] = yield_output_qty
            downstream_metrics["output_uom"] = "unit"
            downstream_ctx["target_output_qty"] = yield_output_qty
            downstream_ctx["target_output_uom"] = "unit"
            downstream_ctx["output_descriptor"] = {
                **dict(downstream_ctx.get("output_descriptor") or {}),
                "quantity": yield_output_qty,
                "uom": "unit",
            }
        downstream_ctx["_plan_operation_metrics"] = downstream_metrics


def _promote_upstream_sheet_operations_before_yield(stage_contexts: List[Dict[str, Any]]) -> None:
    yield_index = None
    for index, ctx in enumerate(stage_contexts or []):
        metrics = dict(ctx.get("_plan_operation_metrics") or {})
        if str(metrics.get("yield_basis") or "").strip().lower() == "fg_per_sheet":
            yield_index = index
            break
    if yield_index is None:
        return

    seen_material_output = False
    for index, ctx in enumerate(stage_contexts or []):
        if index >= yield_index:
            break
        if _norm(ctx.get("execution_type")) == "material":
            if _safe_float(ctx.get("target_output_qty"), 0.0) > 0.0:
                output_descriptor = dict(ctx.get("output_descriptor") or {})
                output_uom = str(
                    ctx.get("target_output_uom")
                    or output_descriptor.get("uom")
                    or (ctx.get("_plan_operation_metrics") or {}).get("output_uom")
                    or (ctx.get("_plan_operation_metrics") or {}).get("uom")
                    or ""
                ).strip().lower()
                output_kind = _norm(output_descriptor.get("kind") or ctx.get("output_kind"))
                stage_text = " ".join(
                    [
                        _norm(ctx.get("stage_code")),
                        _norm(ctx.get("stage_contract_code")),
                        _norm(ctx.get("stage_name")),
                        _norm(ctx.get("stage_type")),
                    ]
                )
                sheet_like = (
                    output_uom in {"sheet", "sheets"}
                    or output_kind in {"sheet", "board"}
                    or "2ply" in stage_text
                    or "2-ply" in stage_text
                    or "corrugation" in stage_text
                    or "board" in stage_text
                )
                if sheet_like:
                    output_kind = output_kind or ("board" if "board" in stage_text else "sheet")
                    ctx["target_output_uom"] = output_uom or "sheet"
                    ctx["output_kind"] = ctx.get("output_kind") or output_kind
                    ctx["output_descriptor"] = {
                        **output_descriptor,
                        "kind": output_kind,
                        "quantity": _safe_float(ctx.get("target_output_qty"), 0.0),
                        "uom": ctx["target_output_uom"],
                    }
                    ctx["bom_mapping_policy"] = ctx.get("bom_mapping_policy") or "upstream_yield_sheet_boundary"
                seen_material_output = True
            continue

        metrics = dict(ctx.get("_plan_operation_metrics") or {})
        output_qty = _safe_float(metrics.get("output_qty"), _safe_float(ctx.get("target_output_qty"), 0.0))
        if output_qty <= 0.0:
            continue
        output_uom = str(metrics.get("output_uom") or metrics.get("uom") or ctx.get("target_output_uom") or "").strip()
        if output_uom and output_uom != "sheet":
            continue

        operation_key = str(ctx.get("_plan_operation_key") or "").strip().lower()
        output_kind = _norm((ctx.get("output_descriptor") or {}).get("kind") or ctx.get("output_kind"))
        stage_text = " ".join(
            [
                _norm(ctx.get("stage_code")),
                _norm(ctx.get("stage_contract_code")),
                _norm(ctx.get("stage_name")),
                _norm(ctx.get("stage_type")),
                operation_key,
            ]
        )
        is_sheet_boundary = output_kind in {"sheet", "board"} or operation_key == "board" or "board" in stage_text
        if not is_sheet_boundary:
            continue

        ctx["execution_type"] = "material"
        ctx["output_stock_behavior"] = ctx.get("output_stock_behavior") or "semi_finished"
        ctx["target_output_qty"] = output_qty
        ctx["target_output_uom"] = output_uom or "sheet"
        ctx["output_kind"] = output_kind or ("board" if "board" in stage_text else "sheet")
        ctx["output_descriptor"] = {
            **dict(ctx.get("output_descriptor") or {}),
            "kind": ctx["output_kind"],
            "quantity": output_qty,
            "uom": ctx["target_output_uom"],
        }
        if seen_material_output:
            ctx["consumes_previous_output"] = True
            ctx["previous_output_required"] = True
            ctx["input_qty_from_prev"] = _safe_float(metrics.get("input_qty"), 0.0) or 1.0
        ctx["bom_mapping_policy"] = ctx.get("bom_mapping_policy") or "upstream_yield_sheet_boundary"
        seen_material_output = True


def _operation_metrics_from_preview(
    ctx: Dict[str, Any],
    operation_key: Optional[str],
    preview_ops_by_key: Dict[str, Dict[str, Any]],
    preview_stages_by_identity: Dict[Any, Dict[str, Any]],
    preview_routed_by_identity: Dict[Any, Dict[str, Any]],
    preview_operation_stage_by_identity: Dict[Any, Dict[str, Any]],
) -> Dict[str, Any]:
    def _metrics_from_row(row: Dict[str, Any], source: str, *, default_uom: str = "") -> Dict[str, Any]:
        resolved_output_uom = str(
            ctx.get("target_output_uom") or row.get("output_uom") or default_uom or row.get("uom") or ""
        )
        result = {
            "input_qty": _safe_float(row.get("input_qty"), _safe_float(ctx.get("input_qty_from_prev"), 0.0)),
            "output_qty": _safe_float(row.get("output_qty"), _safe_float(ctx.get("target_output_qty"), 0.0)),
            "uom": resolved_output_uom,
            "output_uom": resolved_output_uom,
            "source": source,
        }
        for key in (
            "input_uom",
            "output_uom",
            "yield_basis",
            "fg_per_sheet",
            "yield_quantity_basis",
            "yield_relation_input_sheets",
            "yield_relation_output_blanks",
            "yield_output_label",
            "yield_downstream_output_label",
            "yield_output_kind",
        ):
            if key in row and row.get(key) not in (None, ""):
                if key == "output_uom" and result.get("output_uom") not in (None, ""):
                    continue
                result[key] = row.get(key)
        return result

    def _overlay_yield_metadata(target: Dict[str, Any], row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(row, dict):
            return target
        for key in (
            "input_uom",
            "output_uom",
            "yield_basis",
            "fg_per_sheet",
            "yield_quantity_basis",
            "yield_relation_input_sheets",
            "yield_relation_output_blanks",
            "yield_output_label",
            "yield_downstream_output_label",
            "yield_output_kind",
        ):
            if key in row and row.get(key) not in (None, ""):
                if key in {"input_uom", "output_uom"} and target.get(key) not in (None, ""):
                    continue
                target[key] = row.get(key)
        return target

    preview_stage_row = None
    preview_routed_row = None
    preview_operation_stage_row = None
    for key in _stage_identity_keys_for_row(ctx):
        preview_stage_row = preview_stages_by_identity.get(key)
        preview_routed_row = preview_routed_by_identity.get(key)
        preview_operation_stage_row = preview_operation_stage_by_identity.get(key)
        if preview_stage_row or preview_routed_row or preview_operation_stage_row:
            break

    operation_item = preview_ops_by_key.get(str(operation_key or ""))

    primary_row = None
    primary_source = "stagecontext"
    if preview_stage_row:
        primary_row = preview_stage_row
        primary_source = "preview_stage_overlay"
    elif preview_operation_stage_row:
        primary_row = preview_operation_stage_row
        primary_source = "preview_operation_stage_overlay"
    elif preview_routed_row:
        primary_row = preview_routed_row
        primary_source = "preview_routed_operation_overlay"
    elif operation_item:
        primary_row = operation_item
        primary_source = "preview_operation_overlay"

    if primary_row:
        result = _metrics_from_row(primary_row, primary_source)
        for overlay_row in (preview_routed_row, operation_item, preview_operation_stage_row, preview_stage_row):
            if overlay_row is primary_row:
                continue
            result = _overlay_yield_metadata(result, overlay_row)
        return result

    return {
        "input_qty": _safe_float(ctx.get("input_qty_from_prev"), 0.0),
        "output_qty": _safe_float(ctx.get("target_output_qty"), 0.0),
        "uom": str(ctx.get("target_output_uom") or ""),
        "source": "stagecontext",
    }


def _operation_name_from_key(operation_key: Optional[str]) -> Optional[str]:
    return {
        "corrugator": "Corrugation",
        "board": "Board Making",
        "printing": "Printing",
        "lamination": "Lamination",
        "cutting": "Cutting",
        "diecut": "Slot/Die",
        "peeloff": "Peel-off",
        "conversion": "Conversion",
    }.get(str(operation_key or "").strip().lower())


def _operation_key_from_bucket(bucket: Optional[str], *, stage_text: str = "") -> Optional[str]:
    bucket_key = _norm(bucket)
    if bucket_key == "board":
        return "board" if "board" in _norm(stage_text) else "corrugator"
    return {
        "sheet": "printing",
        "blanks": "diecut",
    }.get(bucket_key)


def _identity_lookup(rows: List[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    lookup: Dict[Any, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        _index_by_stage_identity(lookup, row, row)
    return lookup


def _operation_stage_identity_lookup(operations: List[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    lookup: Dict[Any, Dict[str, Any]] = {}
    for operation in operations or []:
        for detail in operation.get("stage_details") or []:
            if isinstance(detail, dict):
                _index_by_stage_identity(lookup, detail, detail)
    return lookup


def _lookup_by_stage_identity(lookup: Dict[Any, Dict[str, Any]], row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for key in _stage_identity_keys_for_row(row):
        found = lookup.get(key)
        if found:
            return found
    return None


def _index_by_stage_identity(target: Dict[Any, Dict[str, Any]], row: Dict[str, Any], value: Dict[str, Any]) -> None:
    for key in _stage_identity_keys_for_row(row):
        target.setdefault(key, value)


def _stage_identity_keys_for_row(row: Dict[str, Any]) -> List[Any]:
    keys: List[Any] = []
    contract = str(row.get("stage_contract_code") or "").strip()
    if contract:
        keys.append(("contract", contract))
    template_id = _safe_optional_int(row.get("stage_template_id"))
    if template_id:
        keys.append(("template", template_id))
    stage_code = str(row.get("stage_code") or "").strip()
    if stage_code:
        keys.append(("code", stage_code))
    return keys


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_list(value: Any) -> List[Any]:
    if value in (None, False):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _object_to_dict(value: Any) -> Dict[str, Any]:
    if value in (None, False, ""):
        return {}
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    data = {}
    for key in (
        "kind",
        "quantity",
        "uom",
        "fg_equivalent_qty",
        "dimensions",
        "tags",
    ):
        item = getattr(value, key, None)
        if item is not None:
            data[key] = item
    return data


def _none_if_blank(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


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


def _safe_optional_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        parsed = int(value)
        return parsed or None
    except Exception:
        return None


def _stable_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()



