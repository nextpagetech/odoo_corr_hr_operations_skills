from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


WRITER_SKELETON_VERSION = "7B-D7.0"

CONTEXT_ENABLE_KEY = "corr_materialization_writer_enabled"
PARAM_ENABLE_KEY = "corr_mrp_bridge.materialization_writer_enabled"

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaterializationWriterConfig:
    writes_enabled: bool = False
    creates_enabled: bool = False
    archives_enabled: bool = False
    allow_multi_level_boms: bool = False
    allow_single_bom_fallback: bool = True
    writer_strategy: str = "auto"
    context_flag_enabled: bool = False
    system_parameter_enabled: bool = False
    product_create_enabled: bool = False
    bom_create_enabled: bool = False
    archive_delete_enabled: bool = False
    mo_creation_enabled: bool = False


@dataclass(frozen=True)
class MaterializationWriterStep:
    step_type: str
    candidate_key: str
    action: str
    status: str = "planned_only"
    reason: str = "writer_disabled"
    metadata: Dict[str, Any] = field(default_factory=dict)


class MaterializationWriterDisabledError(RuntimeError):
    pass


def build_materialization_writer_plan(
    resolver_dry_run: Dict[str, Any],
    *,
    materialization_plan: Optional[Dict[str, Any]] = None,
    config: Optional[MaterializationWriterConfig] = None,
) -> Dict[str, Any]:
    """Return the intended D2 writer sequence without performing ORM writes.

    This is a guarded skeleton for Phase 7B-D2. It translates resolver actions
    into ordered writer steps, but refuses execution until explicit writer
    flags and concrete implementation are added in a later phase.
    """

    writer_config = config or MaterializationWriterConfig()
    strategy = build_materialization_writer_strategy(
        resolver_dry_run,
        materialization_plan=materialization_plan,
        config=writer_config,
    )
    products = list((resolver_dry_run or {}).get("products") or [])
    boms = list((resolver_dry_run or {}).get("boms") or [])
    blockers = list((resolver_dry_run or {}).get("blockers") or [])

    steps: List[MaterializationWriterStep] = []
    for product in products:
        steps.append(_product_step(product))
    for bom in boms:
        steps.append(_bom_step(bom))

    disabled_reason = _disabled_reason(writer_config)
    return {
        "writer_skeleton_version": WRITER_SKELETON_VERSION,
        "dry_run": True,
        "writes_enabled": False,
        "creates_enabled": False,
        "archives_enabled": False,
        "execution_allowed": False,
        "disabled_reason": disabled_reason,
        "input_has_blockers": bool(blockers),
        "blockers": blockers,
        "steps": [step.__dict__ for step in steps],
        "metadata": {
            "allow_single_bom_fallback": writer_config.allow_single_bom_fallback,
            "allow_multi_level_boms": writer_config.allow_multi_level_boms,
            "writer_strategy": strategy["strategy"],
            "source_resolver_version": (resolver_dry_run or {}).get("resolver_version"),
            "mode": (resolver_dry_run or {}).get("mode"),
        },
        "writer_strategy": strategy,
        "implementation_plan": build_guarded_writer_implementation_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=writer_config,
        ),
        "orm_payload_plan": build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=writer_config,
            env=None,
        ),
    }


def execute_materialization_writer(
    env: Any,
    resolver_dry_run: Dict[str, Any],
    *,
    materialization_plan: Optional[Dict[str, Any]] = None,
    config: Optional[MaterializationWriterConfig] = None,
) -> Dict[str, Any]:
    """Guarded D7 executor for materialized products and BoMs.

    Default configuration is disabled. When both gates are open this performs
    only the scoped product/BoM/line/operation creates. It never creates MOs and
    never archives/deletes records.
    """

    writer_config = config or writer_config_from_env(env)
    plan = build_materialization_writer_plan(
        resolver_dry_run,
        materialization_plan=materialization_plan,
        config=writer_config,
    )
    if not _real_write_gate_open(writer_config):
        raise MaterializationWriterDisabledError(plan["disabled_reason"])
    if list((resolver_dry_run or {}).get("blockers") or []):
        raise MaterializationWriterDisabledError("resolver_blockers_present")
    strategy = plan.get("writer_strategy") or {}
    if strategy.get("strategy") == "legacy_single_bom_flattened":
        return {
            "phase": "7B-D7",
            "executed": False,
            "reason": "legacy_single_bom_fallback_preserved",
            "real_orm_writes_enabled": False,
        }
    payload_plan = build_orm_writer_payload_plan(
        resolver_dry_run,
        materialization_plan=materialization_plan,
        config=writer_config,
        env=env,
    )
    return _execute_orm_payload_plan(env, payload_plan, resolver_dry_run)


def writer_config_from_env(env: Any) -> MaterializationWriterConfig:
    """Read D4 writer gates without enabling writes by default.

    Real writes require an explicit context flag and an explicit system
    parameter. Archive/delete and MO creation remain disabled in D4.
    """

    context = getattr(env, "context", None) or {}
    context_enabled = _as_bool(context.get(CONTEXT_ENABLE_KEY))
    parameter_enabled = _get_param_bool(env, PARAM_ENABLE_KEY, default=False)
    enabled = context_enabled and parameter_enabled
    return MaterializationWriterConfig(
        writes_enabled=enabled,
        creates_enabled=enabled,
        archives_enabled=False,
        allow_multi_level_boms=enabled,
        allow_single_bom_fallback=True,
        writer_strategy=str(context.get("corr_materialization_writer_strategy") or "auto"),
        context_flag_enabled=context_enabled,
        system_parameter_enabled=parameter_enabled,
        product_create_enabled=enabled,
        bom_create_enabled=enabled,
        archive_delete_enabled=False,
        mo_creation_enabled=False,
    )


def build_materialization_writer_strategy(
    resolver_dry_run: Dict[str, Any],
    *,
    materialization_plan: Optional[Dict[str, Any]] = None,
    config: Optional[MaterializationWriterConfig] = None,
) -> Dict[str, Any]:
    """Plan which ORM writer family should own the materialization.

    The result is intentionally declarative. It does not call Odoo and does not
    create products, BoMs, operations, lines, or MOs.
    """

    writer_config = config or MaterializationWriterConfig()
    mode = str((resolver_dry_run or {}).get("mode") or (materialization_plan or {}).get("mode") or "")
    resolver_boms = list((resolver_dry_run or {}).get("boms") or [])
    plan_boms = list((materialization_plan or {}).get("boms") or [])
    plan_bom_by_key = {str(item.get("bom_key") or ""): item for item in plan_boms}
    has_sfg_bom = any(str(item.get("bom_role") or "") == "sfg" for item in resolver_boms or plan_boms)
    requested = str(writer_config.writer_strategy or "auto").strip().lower() or "auto"

    if requested == "legacy_single_bom" or mode == "single_bom":
        strategy = "legacy_single_bom_flattened"
    elif requested == "materialized_multi_level" or has_sfg_bom:
        strategy = "materialized_multi_level"
    else:
        strategy = "legacy_single_bom_flattened"

    boundaries = []
    for bom in resolver_boms:
        bom_key = str(bom.get("bom_candidate_key") or "")
        plan_bom = plan_bom_by_key.get(bom_key.replace("bom:", "bom:", 1)) or plan_bom_by_key.get(bom_key)
        role = str(bom.get("bom_role") or "")
        boundaries.append(
            {
                "bom_candidate_key": bom_key,
                "bom_role": role,
                "product_candidate_key": bom.get("product_candidate_key"),
                "action": bom.get("action"),
                "writer_family": "materialized_bom_writer"
                if strategy == "materialized_multi_level"
                else "legacy_flattened_bom_writer",
                "operation_scope": "local_bom_operations"
                if strategy == "materialized_multi_level"
                else "flattened_fg_bom_operations",
                "operation_count": len(list((plan_bom or {}).get("operations") or [])),
                "line_count": len(list((plan_bom or {}).get("lines") or [])),
                "creates_stock_boundary": role in ("sfg", "fg"),
            }
        )

    return {
        "strategy": strategy,
        "writer_family": strategy,
        "operation_scope": "local_bom_operations"
        if strategy == "materialized_multi_level"
        else "flattened_fg_bom_operations",
        "dry_run": True,
        "execution_allowed": False,
        "legacy_single_bom_fallback_preserved": bool(writer_config.allow_single_bom_fallback),
        "multi_level_writer_enabled": bool(writer_config.allow_multi_level_boms),
        "real_orm_writes_enabled": bool(
            writer_config.writes_enabled
            and writer_config.creates_enabled
            and writer_config.context_flag_enabled
            and writer_config.system_parameter_enabled
        ),
        "guard": _guard_diagnostics(writer_config),
        "reason": _strategy_reason(strategy, mode, has_sfg_bom, requested),
        "boundaries": boundaries,
    }


def build_guarded_writer_implementation_plan(
    resolver_dry_run: Dict[str, Any],
    *,
    materialization_plan: Optional[Dict[str, Any]] = None,
    config: Optional[MaterializationWriterConfig] = None,
) -> Dict[str, Any]:
    """Describe D4 real-writer sequence while keeping all writes disabled."""

    writer_config = config or MaterializationWriterConfig()
    products = list((resolver_dry_run or {}).get("products") or [])
    boms = list((resolver_dry_run or {}).get("boms") or [])
    blockers = list((resolver_dry_run or {}).get("blockers") or [])
    product_steps = [_guarded_product_write_step(product, writer_config) for product in products]
    bom_steps = [_guarded_bom_write_step(bom, writer_config) for bom in boms]
    unsupported = [
        step for step in product_steps + bom_steps
        if step.get("guard_status") == "unsupported_in_d4"
    ]
    return {
        "phase": "7B-D4",
        "dry_run": True,
        "execution_allowed": False,
        "real_orm_writes_enabled": False,
        "guard": _guard_diagnostics(writer_config),
        "input_has_blockers": bool(blockers),
        "blockers": blockers,
        "product_write_steps": product_steps,
        "bom_write_steps": bom_steps,
        "unsupported_steps": unsupported,
        "no_mo_creation": True,
        "no_archive_delete": True,
        "legacy_single_bom_fallback_preserved": bool(writer_config.allow_single_bom_fallback),
        "materialization_mode": (resolver_dry_run or {}).get("mode") or (materialization_plan or {}).get("mode"),
    }


def build_orm_writer_payload_plan(
    resolver_dry_run: Dict[str, Any],
    *,
    materialization_plan: Optional[Dict[str, Any]] = None,
    config: Optional[MaterializationWriterConfig] = None,
    env: Any = None,
) -> Dict[str, Any]:
    """Prepare ORM-style values for the future writer without writing them."""

    writer_config = config or MaterializationWriterConfig()
    resolver_products = list((resolver_dry_run or {}).get("products") or [])
    resolver_boms = list((resolver_dry_run or {}).get("boms") or [])
    materialized_boms = list((materialization_plan or {}).get("boms") or [])
    materialized_product_by_key = {
        str(product.get("candidate_key") or ""): product
        for product in list((materialization_plan or {}).get("products") or [])
    }
    product_by_key = {
        str(product.get("product_candidate_key") or ""): product
        for product in resolver_products
    }
    resolver_bom_by_key = {
        str(bom.get("bom_candidate_key") or ""): bom
        for bom in resolver_boms
    }
    resolved_line_by_key = _resolved_line_diagnostic_by_key(resolver_boms)
    materialized_bom_by_key = {
        str(bom.get("bom_key") or ""): bom
        for bom in materialized_boms
    }

    sfg_display_context_by_key = _sfg_display_context_by_product_key(
        materialization_plan or {},
        product_by_key,
        materialized_product_by_key,
        env=env,
    )
    product_payloads = [
        _product_create_payload(
            product,
            sfg_display_context_by_key.get(str(product.get("product_candidate_key") or ""), {}),
        )
        for product in resolver_products
        if product.get("action") == "create_product" and product.get("product_role") == "sfg"
    ]
    product_sync_payloads = [
        _product_create_payload(
            product,
            sfg_display_context_by_key.get(str(product.get("product_candidate_key") or ""), {}),
        )
        for product in resolver_products
        if product.get("action") == "reuse_product" and product.get("product_role") == "sfg"
    ]
    bom_payloads = []
    line_payloads = []
    operation_payloads = []
    refresh_reused_generated_content = _plan_requires_reused_generated_content_refresh(materialized_boms)
    for bom_key, resolver_bom in resolver_bom_by_key.items():
        materialized_bom = materialized_bom_by_key.get(bom_key) or {}
        action = resolver_bom.get("action")
        should_write_payload = (
            action == "create_bom"
            or refresh_reused_generated_content
            or _resolver_reused_bom_is_incomplete(env, resolver_bom, materialized_bom)
        )
        if should_write_payload:
            bom_payloads.append(_bom_create_payload(resolver_bom, materialized_bom, product_by_key))
            for line in list(materialized_bom.get("lines") or []):
                line_payloads.append(
                    _bom_line_create_payload(
                        resolver_bom,
                        _line_with_resolved_component(line, resolved_line_by_key),
                    )
                )
            for operation in list(materialized_bom.get("operations") or []):
                operation_payloads.append(_bom_operation_create_payload(resolver_bom, operation))

    return {
        "phase": "7B-D5",
        "dry_run": True,
        "execution_allowed": False,
        "real_orm_writes_enabled": False,
        "guard": _guard_diagnostics(writer_config),
        "payloads_are_values_only": True,
        "no_create_calls": True,
        "no_write_calls": True,
        "no_archive_delete": True,
        "no_mo_creation": True,
        "product_template_create_values": product_payloads,
        "product_template_sync_values": product_sync_payloads,
        "bom_create_values": bom_payloads,
        "bom_line_create_values": line_payloads,
        "operation_create_values": operation_payloads,
        "product_create_vals": product_payloads,
        "product_sync_vals": product_sync_payloads,
        "bom_create_vals": bom_payloads,
        "bom_line_create_vals": line_payloads,
        "bom_operation_create_vals": operation_payloads,
        "refresh_reused_generated_content": refresh_reused_generated_content,
    }


def build_guarded_executor_diagnostics(
    resolver_dry_run: Dict[str, Any],
    *,
    materialization_plan: Optional[Dict[str, Any]] = None,
    config: Optional[MaterializationWriterConfig] = None,
) -> Dict[str, Any]:
    """Expose D7 executor readiness without executing ORM writes."""

    writer_config = config or MaterializationWriterConfig()
    payload_plan = build_orm_writer_payload_plan(
        resolver_dry_run,
        materialization_plan=materialization_plan,
        config=writer_config,
    )
    strategy = build_materialization_writer_strategy(
        resolver_dry_run,
        materialization_plan=materialization_plan,
        config=writer_config,
    )
    blockers = list((resolver_dry_run or {}).get("blockers") or [])
    gate_open = _real_write_gate_open(writer_config)
    legacy_fallback = strategy.get("strategy") == "legacy_single_bom_flattened"
    return {
        "phase": "7B-D7",
        "dry_run": True,
        "executor_scaffold_available": True,
        "controlled_execution_available": True,
        "execution_attempted": False,
        "execution_allowed_by_gates": gate_open,
        "would_execute_materialized_writer": bool(gate_open and not blockers and not legacy_fallback),
        "real_orm_writes_enabled": False,
        "disabled_reason": "" if gate_open else _disabled_reason(writer_config),
        "payload_plan_phase": payload_plan.get("phase"),
        "writer_strategy": strategy.get("strategy"),
        "legacy_single_bom_fallback_preserved": bool(legacy_fallback),
        "input_has_blockers": bool(blockers),
        "blockers": blockers,
        "models_in_scope": [
            "product.template",
            "mrp.bom",
            "mrp.bom.line",
            "mrp.routing.workcenter",
        ],
        "execution_scope": {
            "create_or_reuse_sfg_products": True,
            "create_or_reuse_boms": True,
            "create_bom_lines_for_new_boms": True,
            "create_operations_for_new_boms": True,
            "create_mos": False,
            "archive_delete": False,
            "destructive_updates": False,
        },
        "idempotency": {
            "product_lookup_key": "corr_sfg_key",
            "bom_lookup_keys": ["product_tmpl_id", "corr_engine_v2_signature", "corr_sfg_key"],
            "existing_bom_lines_are_not_modified": True,
            "existing_operations_are_not_modified": True,
        },
        "no_mo_creation": True,
        "no_archive_delete": True,
        "no_destructive_updates": True,
        "guard": _guard_diagnostics(writer_config),
    }


def _resolver_reused_bom_is_incomplete(
    env: Any,
    resolver_bom: Dict[str, Any],
    materialized_bom: Dict[str, Any],
) -> bool:
    if resolver_bom.get("action") != "reuse_bom":
        return False
    match = resolver_bom.get("existing_bom_match") or {}
    record = _browse_one(env, "mrp.bom", match.get("id"))
    if not record:
        return False
    expected = {
        "_corr_strategy_type": ((materialized_bom or {}).get("metadata") or {}).get("strategy_type"),
        "_corr_require_material_signature_match": _requires_product_material_signature_match(
            dict(((materialized_bom or {}).get("metadata") or {}))
        ),
        "_corr_expected_line_count": len(list((materialized_bom or {}).get("lines") or [])),
        "_corr_expected_operation_count": len(list((materialized_bom or {}).get("operations") or [])),
    }
    return _existing_bom_is_incomplete_for_payload(record, expected)


def _plan_requires_reused_generated_content_refresh(materialized_boms: List[Dict[str, Any]]) -> bool:
    """Return true when reused BoMs must be refreshed from current plan data.

    Materialized Engine V2 BoMs can be reused by signature/corr_sfg_key after
    route or stage-contract configuration changes. Reused records may still
    contain older generated lines, such as paper RMs on a downstream diecut
    SFG after the current plan correctly assigns those RMs to board. Manual
    BoM content remains protected by _clear_generated_bom_content(), so refresh
    reused generated content whenever the current plan has generated lines or
    operations to write.
    """

    for bom in list(materialized_boms or []):
        if list((bom or {}).get("lines") or []) or list((bom or {}).get("operations") or []):
            return True
        if _safe_float((bom or {}).get("product_qty"), 1.0) > 1.0:
            return True
        metadata = dict((bom or {}).get("metadata") or {})
        if str(metadata.get("yield_basis") or "").strip().lower() == "fg_per_sheet":
            return True
        operation_metrics = dict(metadata.get("operation_metrics") or {})
        if str(operation_metrics.get("yield_basis") or "").strip().lower() == "fg_per_sheet":
            return True
        for operation in list((bom or {}).get("operations") or []):
            operation_metadata = dict((operation or {}).get("metadata") or {})
            if str(operation_metadata.get("yield_basis") or "").strip().lower() == "fg_per_sheet":
                return True
            if _safe_float(operation_metadata.get("fg_per_sheet"), 0.0) > 1.0:
                return True
        for line in list((bom or {}).get("lines") or []):
            line_metadata = dict((line or {}).get("metadata") or {})
            if str(line_metadata.get("yield_basis") or "").strip().lower() == "fg_per_sheet":
                return True
            if _safe_float(line_metadata.get("fg_per_sheet"), 0.0) > 1.0:
                return True
            preview_metadata = dict(line_metadata.get("preview_metadata") or {})
            if str(preview_metadata.get("yield_basis") or "").strip().lower() == "fg_per_sheet":
                return True
            if _safe_float(preview_metadata.get("fg_per_sheet"), 0.0) > 1.0:
                return True
    return False


def _resolved_line_diagnostic_by_key(resolver_boms: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for bom in list(resolver_boms or []):
        for diagnostic in list((bom or {}).get("line_diagnostics") or []):
            reason = str(diagnostic.get("reason") or "")
            if reason not in {
                "component_product_resolved",
                "component_product_resolved_by_live_paper_resolution",
            }:
                continue
            line_key = str(diagnostic.get("line_key") or "")
            product_id = diagnostic.get("product_id")
            if line_key and product_id:
                result[line_key] = diagnostic
    return result


def _line_with_resolved_component(line: Dict[str, Any], resolved_line_by_key: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    line_key = str((line or {}).get("line_key") or "")
    diagnostic = resolved_line_by_key.get(line_key)
    if not diagnostic:
        return line
    product_id = diagnostic.get("product_id")
    if not product_id:
        return line
    enriched = dict(line or {})
    metadata = dict(enriched.get("metadata") or {})
    metadata["product_id"] = product_id
    metadata["product_name"] = diagnostic.get("product_name") or metadata.get("product_name")
    metadata["writer_product_key"] = "product.product:%s" % product_id
    metadata["resolution_status"] = "resolved"
    metadata["resolution_note"] = "Resolved by materialization resolver"
    resolution = dict(metadata.get("resolution") or {})
    resolution["product_id"] = product_id
    resolution["product_name"] = diagnostic.get("product_name") or resolution.get("product_name")
    resolution["status"] = "resolved"
    resolution["resolution_note"] = "Resolved by materialization resolver"
    metadata["resolution"] = resolution
    enriched["metadata"] = metadata
    return enriched


def _product_step(product: Dict[str, Any]) -> MaterializationWriterStep:
    action = str(product.get("action") or "")
    return MaterializationWriterStep(
        step_type="product",
        candidate_key=str(product.get("product_candidate_key") or ""),
        action=action,
        reason=_step_reason(action),
        metadata={
            "product_role": product.get("product_role"),
            "corr_sfg_key": product.get("corr_sfg_key"),
            "existing_product_id": (product.get("existing_product_match") or {}).get("id"),
        },
    )


def _bom_step(bom: Dict[str, Any]) -> MaterializationWriterStep:
    action = str(bom.get("action") or "")
    return MaterializationWriterStep(
        step_type="bom",
        candidate_key=str(bom.get("bom_candidate_key") or ""),
        action=action,
        reason=_step_reason(action),
        metadata={
            "bom_role": bom.get("bom_role"),
            "bom_type": bom.get("bom_type"),
            "product_candidate_key": bom.get("product_candidate_key"),
            "existing_bom_id": (bom.get("existing_bom_match") or {}).get("id"),
            "stale_bom_id": (bom.get("stale_bom_match") or {}).get("id"),
        },
    )


def _guarded_product_write_step(
    product: Dict[str, Any],
    config: MaterializationWriterConfig,
) -> Dict[str, Any]:
    action = str(product.get("action") or "")
    role = str(product.get("product_role") or "")
    if action == "reuse_product":
        guard_status = "ready_when_writer_enabled"
    elif action == "create_product" and role == "sfg":
        guard_status = "ready_when_writer_enabled" if config.product_create_enabled else "disabled"
    elif action == "create_product":
        guard_status = "unsupported_in_d4"
    elif action == "blocked_product":
        guard_status = "blocked_by_resolver"
    else:
        guard_status = "planned_only"
    return {
        "step_type": "product",
        "candidate_key": product.get("product_candidate_key"),
        "action": action,
        "product_role": role,
        "corr_sfg_key": product.get("corr_sfg_key"),
        "existing_product_id": (product.get("existing_product_match") or {}).get("id"),
        "guard_status": guard_status,
        "writes_enabled": False,
        "create_allowed_now": False,
    }


def _guarded_bom_write_step(
    bom: Dict[str, Any],
    config: MaterializationWriterConfig,
) -> Dict[str, Any]:
    action = str(bom.get("action") or "")
    if action == "reuse_bom":
        guard_status = "ready_when_writer_enabled"
    elif action == "create_bom":
        guard_status = "ready_when_writer_enabled" if config.bom_create_enabled else "disabled"
    elif action == "archive_bom_candidate":
        guard_status = "unsupported_in_d4"
    elif action == "blocked_bom":
        guard_status = "blocked_by_resolver"
    else:
        guard_status = "planned_only"
    return {
        "step_type": "bom",
        "candidate_key": bom.get("bom_candidate_key"),
        "action": action,
        "bom_role": bom.get("bom_role"),
        "bom_type": bom.get("bom_type"),
        "product_candidate_key": bom.get("product_candidate_key"),
        "existing_bom_id": (bom.get("existing_bom_match") or {}).get("id"),
        "stale_bom_id": (bom.get("stale_bom_match") or {}).get("id"),
        "guard_status": guard_status,
        "writes_enabled": False,
        "create_allowed_now": False,
        "archive_allowed_now": False,
    }


def _product_create_payload(product: Dict[str, Any], display_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    display_context = dict(display_context or {})
    product_role = product.get("product_role")
    metadata = dict(product.get("metadata") or {})
    require_material_signature_match = _requires_product_material_signature_match(metadata)
    return {
        "candidate_key": product.get("product_candidate_key"),
        "model": "product.template",
        "values": {
            "name": _planned_product_name(product, display_context),
            "type": "consu" if product_role == "sfg" else product.get("product_type_hint"),
            "product_role": product_role,
            "sale_ok": False if product_role == "sfg" else None,
            "purchase_ok": False if product_role == "sfg" else None,
            "is_storable": True if product_role == "sfg" else None,
            "corr_sfg_key": product.get("corr_sfg_key"),
            "corr_sfg_type": display_context.get("corr_sfg_type"),
            "corr_stage_code": display_context.get("corr_stage_code"),
            "corr_sheet_length_mm": display_context.get("corr_sheet_length_mm"),
            "corr_sheet_width_mm": display_context.get("corr_sheet_width_mm"),
            "corr_flute": display_context.get("corr_flute"),
            "corr_top_sheet_ref": display_context.get("corr_top_sheet_ref"),
            "corr_material_signature": product.get("corr_material_signature"),
            "corr_engine_v2_signature": product.get("corr_plan_signature"),
            "corr_sfg_source_segment_key": product.get("corr_segment_key"),
            "uom_name": product.get("uom"),
            "route_policy": product.get("route_policy"),
            "_corr_strategy_type": metadata.get("strategy_type") or metadata.get("strategy"),
            "_corr_require_material_signature_match": require_material_signature_match,
            "_corr_shareable_branch_sfg": bool(
                metadata.get("strategy_type") == "independent_routed_piece"
                and not bool(metadata.get("terminal_piece_sfg"))
                and not bool(metadata.get("fg_merge_sfg"))
            ),
        },
        "lookup_key": {
            "corr_sfg_key": product.get("corr_sfg_key"),
        },
    }


def _requires_product_material_signature_match(metadata: Dict[str, Any]) -> bool:
    strategy_type = str(metadata.get("strategy_type") or metadata.get("strategy") or "").strip()
    if strategy_type != "independent_routed_piece":
        return False
    if metadata.get("source_kind") == "piece_effective_recipe":
        return True
    if metadata.get("manufacturing_source") == "piece_effective_recipe":
        return True
    if metadata.get("piece_2ply_set"):
        return True
    for field_name in ("stack_source", "surface_source", "top_sheet_source"):
        value = str(metadata.get(field_name) or "").strip().lower()
        if value and value != "inherited":
            return True
    return False


def _execute_orm_payload_plan(
    env: Any,
    payload_plan: Dict[str, Any],
    resolver_dry_run: Dict[str, Any],
) -> Dict[str, Any]:
    created_products: Dict[str, Dict[str, Any]] = {}
    created_boms: Dict[str, Dict[str, Any]] = {}
    reused_existing_products: Dict[str, Dict[str, Any]] = {}
    reused_existing_boms: Dict[str, Dict[str, Any]] = {}
    repaired_existing_boms: Dict[str, Dict[str, Any]] = {}
    skipped_payloads: List[Dict[str, Any]] = []
    bom_execution_order: List[Dict[str, Any]] = []
    bom_write_trace: List[Dict[str, Any]] = []
    reused_products = _reused_product_map(resolver_dry_run)
    reused_boms = _reused_bom_map(resolver_dry_run)
    resolver_product_by_key = {
        str(product.get("product_candidate_key") or ""): product
        for product in list((resolver_dry_run or {}).get("products") or [])
    }

    for payload in list(payload_plan.get("product_template_create_values") or []):
        candidate_key = str(payload.get("candidate_key") or "")
        existing = _find_existing_product_for_payload(env, payload)
        if existing:
            _ensure_sfg_product_defaults(env, existing.get("id"), payload.get("values") or {})
            reused_existing_products[candidate_key] = existing
            continue
        values = _prepare_product_template_values(env, payload.get("values") or {})
        record = _create_record(env, payload["model"], values)
        template_id = _record_id(record)
        created_products[candidate_key] = {
            "id": template_id,
            "product_variant_id": _record_id(_get(record, "product_variant_id")),
        }

    for payload in list(payload_plan.get("product_template_sync_values") or []):
        candidate_key = str(payload.get("candidate_key") or "")
        existing = _find_existing_product_for_payload(env, payload)
        if not existing:
            skipped_payloads.append(
                {
                    "candidate_key": candidate_key,
                    "payload_type": "product_template_sync",
                    "reason": "reused_sfg_product_not_found_for_sync",
                }
            )
            continue
        _ensure_sfg_product_defaults(env, existing.get("id"), payload.get("values") or {})
        reused_existing_products[candidate_key] = existing

    product_lookup = {**reused_products, **reused_existing_products, **created_products}
    for candidate_key, product_info in list(product_lookup.items()):
        resolver_product = resolver_product_by_key.get(str(candidate_key or ""), {})
        if resolver_product.get("product_role") == "sfg":
            _ensure_sfg_product_defaults(
                env,
                product_info.get("id"),
                {
                    "product_role": "sfg",
                    "route_policy": resolver_product.get("route_policy"),
                    "corr_sfg_key": resolver_product.get("corr_sfg_key"),
                },
            )
    for payload in list(payload_plan.get("bom_create_values") or []):
        values = dict(payload.get("values") or {})
        product_key = values.get("product_candidate_key")
        product_info = product_lookup.get(str(product_key or ""), {})
        values["product_tmpl_id"] = values.get("product_tmpl_id") or product_info.get("id")
        if not values.get("product_tmpl_id") and values.get("bom_role") == "fg":
            values["product_tmpl_id"] = _context_int(env, "corr_materialization_fg_product_tmpl_id")
        if not values.get("product_tmpl_id"):
            raise MaterializationWriterDisabledError(
                "missing_product_tmpl_id_for_bom:%s" % (payload.get("candidate_key") or "")
            )
        payload_with_resolved_product = {**payload, "values": values}
        candidate_key = str(payload.get("candidate_key") or "")
        existing = _find_existing_bom_for_payload(env, payload_with_resolved_product)
        if existing:
            before = _bom_header_debug_snapshot(env, existing.get("id"))
            _sync_existing_bom_header(env, existing.get("id"), values)
            after = _bom_header_debug_snapshot(env, existing.get("id"))
            trace = {
                "event": "bom_header_sync",
                "candidate_key": candidate_key,
                "bom_id": existing.get("id"),
                "product_name": after.get("product_name") or before.get("product_name"),
                "product_qty_before": before.get("product_qty"),
                "planned_product_qty": values.get("product_qty"),
                "product_qty_after": after.get("product_qty"),
            }
            bom_write_trace.append(trace)
            _logger.info("Engine V2 BoM writer: %s", trace)
            existing["bom_role"] = values.get("bom_role")
            reuse_source = str(existing.get("source") or "")
            if reuse_source == "unused_current_engine_v2_fg_bom_for_new_signature":
                repaired_existing_boms[candidate_key] = existing
                action = "reuse_unused_current_bom_for_new_signature"
            else:
                reused_existing_boms[candidate_key] = existing
                action = "reuse_bom"
            bom_execution_order.append(
                {
                    "candidate_key": candidate_key,
                    "id": existing.get("id"),
                    "bom_role": values.get("bom_role"),
                    "action": action,
                }
            )
            continue
        incomplete_existing = _find_incomplete_existing_bom_for_payload(env, payload_with_resolved_product)
        if incomplete_existing:
            incomplete_existing["bom_role"] = values.get("bom_role")
            repaired_existing_boms[candidate_key] = incomplete_existing
            bom_execution_order.append(
                {
                    "candidate_key": candidate_key,
                    "id": incomplete_existing.get("id"),
                    "bom_role": values.get("bom_role"),
                    "action": "repair_incomplete_bom",
                }
            )
            continue
        record = _create_record(env, payload["model"], values)
        created_snapshot = _bom_header_debug_snapshot(env, _record_id(record))
        trace = {
            "event": "bom_header_create",
            "candidate_key": candidate_key,
            "bom_id": _record_id(record),
            "product_name": created_snapshot.get("product_name"),
            "planned_product_qty": values.get("product_qty"),
            "product_qty_after": created_snapshot.get("product_qty"),
        }
        bom_write_trace.append(trace)
        _logger.info("Engine V2 BoM writer: %s", trace)
        created_boms[candidate_key] = {
            "id": _record_id(record),
            "bom_role": values.get("bom_role"),
        }
        bom_execution_order.append(
            {
                "candidate_key": candidate_key,
                "id": _record_id(record),
                "bom_role": values.get("bom_role"),
                "action": "create_bom",
            }
        )

    bom_lookup = {**reused_boms, **reused_existing_boms, **repaired_existing_boms, **created_boms}
    line_writable_bom_keys = set(created_boms.keys()) | set(repaired_existing_boms.keys())
    refresh_reused_generated_content = bool((payload_plan or {}).get("refresh_reused_generated_content"))
    if refresh_reused_generated_content:
        line_writable_bom_keys |= set(reused_boms.keys()) | set(reused_existing_boms.keys())
    refreshed_bom_ids: set = set()
    line_owner_by_bom_id: Dict[Any, str] = {}
    prepared_line_payloads: List[Dict[str, Any]] = []
    for payload in list(payload_plan.get("bom_line_create_values") or []):
        parent_key = str(payload.get("parent_bom_candidate_key") or "")
        if parent_key not in line_writable_bom_keys:
            skipped_payloads.append(
                {
                    "model": payload.get("model"),
                    "candidate_key": payload.get("candidate_key"),
                    "parent_bom_candidate_key": parent_key,
                    "reason": "parent_bom_reused_existing_no_destructive_update",
                }
            )
            continue
        values = dict(payload.get("values") or {})
        bom_info = bom_lookup.get(parent_key, {})
        values["bom_id"] = bom_info.get("id")
        if values.get("bom_id") and values.get("bom_id") not in refreshed_bom_ids:
            clear_trace = _clear_generated_bom_content(env, values.get("bom_id"))
            bom_write_trace.extend(clear_trace)
            for trace in clear_trace:
                _logger.info("Engine V2 BoM writer: %s", trace)
            refreshed_bom_ids.add(values.get("bom_id"))
        existing_owner = line_owner_by_bom_id.get(values.get("bom_id"))
        if existing_owner and existing_owner != parent_key:
            skipped_payloads.append(
                {
                    "model": payload.get("model"),
                    "candidate_key": payload.get("candidate_key"),
                    "parent_bom_candidate_key": parent_key,
                    "reason": "target_bom_already_written_in_same_execution",
                }
            )
            bom_write_trace.append(
                {
                    "event": "bom_line_skip",
                    "candidate_key": payload.get("candidate_key"),
                    "parent_bom_candidate_key": parent_key,
                    "skip_reason": "missing_component_product_id",
                    "planned_line_qty": values.get("product_qty"),
                    "bom_id": values.get("bom_id"),
                    "generated": values.get("corr_engine_generated"),
                    "manual": values.get("corr_manual"),
                }
            )
            continue
        line_owner_by_bom_id.setdefault(values.get("bom_id"), parent_key)
        if not values.get("product_id") and values.get("product_candidate_key"):
            product_info = product_lookup.get(str(values.get("product_candidate_key") or ""), {})
            values["product_id"] = (
                product_info.get("product_variant_id")
                or _product_variant_id_for_template_id(env, product_info.get("id"))
                or product_info.get("id")
            )
        if not values.get("product_id"):
            skipped_payloads.append(
                {
                    "model": payload.get("model"),
                    "candidate_key": payload.get("candidate_key"),
                    "parent_bom_candidate_key": parent_key,
                    "reason": "missing_component_product_id",
                    "line_type": values.get("line_type"),
                    "role_code": values.get("role_code"),
                    "product_candidate_key": values.get("product_candidate_key"),
                    "product_uom_name": values.get("product_uom_name"),
                    "operation_segment_key": values.get("operation_segment_key"),
                    "corr_component_key": values.get("corr_component_key"),
                }
            )
            continue
        if not values.get("product_uom_id"):
            values["product_uom_id"] = _resolve_line_uom_id(
                env,
                values.get("product_id"),
                values.get("product_uom_name"),
            )
        prepared_payload = dict(payload)
        prepared_payload["values"] = values
        prepared_line_payloads.append(prepared_payload)

    for payload in _collapse_bom_line_payloads(prepared_line_payloads):
        values = dict(payload.get("values") or {})
        line_record = _create_record(env, payload["model"], values)
        trace = {
            "event": "bom_line_create",
            "candidate_key": payload.get("candidate_key"),
            "parent_bom_candidate_key": payload.get("parent_bom_candidate_key"),
            "bom_id": values.get("bom_id"),
            "component_name": _product_name_for_id(env, values.get("product_id")),
            "planned_line_qty": values.get("product_qty"),
            "final_line_qty": _safe_float(_get(line_record, "product_qty"), values.get("product_qty")),
            "corr_component_key": values.get("corr_component_key"),
            "generated": values.get("corr_engine_generated"),
            "manual": values.get("corr_manual"),
        }
        bom_write_trace.append(trace)
        _logger.info("Engine V2 BoM writer: %s", trace)

    missing_component_payloads = [
        item for item in skipped_payloads
        if item.get("reason") == "missing_component_product_id"
    ]
    if missing_component_payloads:
        preview = "; ".join(
            "%s/%s/%s" % (
                item.get("parent_bom_candidate_key") or "-",
                item.get("role_code") or "-",
                item.get("product_uom_name") or "-",
            )
            for item in missing_component_payloads[:6]
        )
        raise MaterializationWriterDisabledError(
            "missing_component_product_id_for_bom_lines:%s" % preview
        )

    operation_owner_by_bom_id: Dict[Any, str] = {}
    for payload in list(payload_plan.get("operation_create_values") or []):
        parent_key = str(payload.get("parent_bom_candidate_key") or "")
        if parent_key not in line_writable_bom_keys:
            skipped_payloads.append(
                {
                    "model": payload.get("model"),
                    "candidate_key": payload.get("candidate_key"),
                    "parent_bom_candidate_key": parent_key,
                    "reason": "parent_bom_reused_existing_no_destructive_update",
                }
            )
            continue
        values = dict(payload.get("values") or {})
        bom_info = bom_lookup.get(parent_key, {})
        values["bom_id"] = bom_info.get("id")
        if values.get("bom_id") and values.get("bom_id") not in refreshed_bom_ids:
            clear_trace = _clear_generated_bom_content(env, values.get("bom_id"))
            bom_write_trace.extend(clear_trace)
            for trace in clear_trace:
                _logger.info("Engine V2 BoM writer: %s", trace)
            refreshed_bom_ids.add(values.get("bom_id"))
        existing_owner = operation_owner_by_bom_id.get(values.get("bom_id"))
        if existing_owner and existing_owner != parent_key:
            skipped_payloads.append(
                {
                    "model": payload.get("model"),
                    "candidate_key": payload.get("candidate_key"),
                    "parent_bom_candidate_key": parent_key,
                    "reason": "target_bom_operations_already_written_in_same_execution",
                }
            )
            continue
        operation_owner_by_bom_id.setdefault(values.get("bom_id"), parent_key)
        if not values.get("workcenter_id"):
            values["workcenter_id"] = _default_operation_workcenter_id(env)
        if not values.get("corr_operation_type_id"):
            op_type_id = _corr_operation_type_id_for_operation_values(env, values)
            if op_type_id:
                values["corr_operation_type_id"] = op_type_id
        _create_record(env, payload["model"], values)

    for item in bom_execution_order:
        if item.get("bom_role") not in ("fg", "single_bom"):
            continue
        bom_id = item.get("id")
        try:
            bom = env["mrp.bom"].sudo().browse(int(bom_id or 0)).exists()
            if bom and hasattr(bom, "_corr_mark_as_current_engine_v2_fg_bom"):
                bom._corr_mark_as_current_engine_v2_fg_bom()
        except Exception as exc:
            _logger.info(
                "Engine V2 current BoM mark skipped: bom_id=%s reason=%s",
                bom_id,
                exc,
            )

    return {
        "phase": "7B-D7",
        "executed": True,
        "real_orm_writes_enabled": True,
        "created_product_templates": created_products,
        "created_boms": created_boms,
        "repaired_boms": repaired_existing_boms,
        "reused_products": {**reused_products, **reused_existing_products},
        "reused_boms": {**reused_boms, **reused_existing_boms},
        "bom_execution_order": bom_execution_order,
        "skipped_payloads": skipped_payloads,
        "bom_write_trace": bom_write_trace,
        "idempotency": {
            "product_lookup_key": "corr_sfg_key",
            "bom_lookup_keys": ["product_tmpl_id", "corr_engine_v2_signature", "corr_sfg_key"],
            "existing_bom_generated_lines_are_refreshed": True,
            "existing_generated_operations_are_refreshed": True,
            "manual_bom_lines_are_not_modified": True,
            "incomplete_empty_bom_headers_are_repaired": True,
        },
        "no_mo_creation": True,
        "no_archive_delete": True,
        "no_destructive_updates": True,
    }


def _create_record(env: Any, model_name: str, values: Dict[str, Any]) -> Any:
    model = env[model_name].sudo()
    clean_values = _filter_model_values(model, values)
    return model.create(clean_values)


def _corr_operation_type_id_for_operation_values(env: Any, values: Dict[str, Any]) -> Any:
    try:
        RoutingOp = env["mrp.routing.workcenter"].sudo()
        if "corr_operation_type_id" not in getattr(RoutingOp, "_fields", {}):
            return None
        OpType = env["corr.operation.type"].sudo()
    except Exception:
        return None

    name = _clean_text((values or {}).get("name"))
    stage_code = _clean_text((values or {}).get("stage_code"))
    text = " ".join(filter(None, [name, stage_code])).lower()

    canonical = None
    if "cut" in text and "die" not in text:
        canonical = "Cutting"
    elif "print" in text:
        canonical = "Printing"
    elif "corrug" in text or "2ply" in text or "2-ply" in text:
        canonical = "Corrugation"
    elif "board" in text:
        canonical = "Board Making"
    elif "die" in text or "slot" in text:
        canonical = "Slot/Die"
    elif "peel" in text or "waste stripping" in text:
        canonical = "Peeling"
    elif "seam" in text or "joint" in text or "gluing" in text:
        canonical = "Seaming"

    labels = [canonical, name]
    for label in labels:
        if not label:
            continue
        try:
            op_type = OpType.find_from_label(label)
            if op_type:
                return op_type.id
        except Exception:
            continue
    return None


def _sync_existing_bom_header(env: Any, bom_id: Any, values: Dict[str, Any]) -> None:
    if not bom_id:
        return
    record = _browse_one(env, "mrp.bom", bom_id)
    if not record:
        return
    model = env["mrp.bom"].sudo()
    update_values = {
        key: value
        for key, value in dict(values or {}).items()
        if key
        in {
            "product_tmpl_id",
            "product_id",
            "product_qty",
            "type",
            "bom_role",
            "corr_engine_v2_generated",
            "corr_sfg_key",
            "corr_engine_v2_signature",
            "corr_segment_key",
            "_corr_strategy_type",
            "_corr_shareable_branch_sfg",
            "_corr_expected_line_count",
            "_corr_expected_operation_count",
        }
    }
    clean_values = _filter_model_values(model, update_values)
    if not clean_values:
        return
    try:
        record.write(clean_values)
    except AttributeError:
        for key, value in clean_values.items():
            setattr(record, key, value)


def _clear_generated_bom_content(env: Any, bom_id: Any) -> List[Dict[str, Any]]:
    """Refresh only generated BoM content before rewriting planned lines.

    Reused materialized BoMs can carry older generated quantities. Manual lines
    remain protected; this removes Engine V2-generated lines/operations only.
    """

    if not bom_id:
        return []
    bom = _browse_one(env, "mrp.bom", bom_id)
    if not bom:
        return []
    trace: List[Dict[str, Any]] = []
    try:
        lines = _get(bom, "bom_line_ids")
        engine_owned_bom = bool(
            _get(bom, "corr_engine_generated")
            or _get(bom, "corr_engine_v2_signature")
            or _get(bom, "corr_segment_key")
        )
        generated_lines = lines.filtered(
            lambda line: bool(_get(line, "corr_engine_generated"))
            or (
                bool(_get(line, "corr_component_key"))
                and not bool(_get(line, "corr_manual"))
            )
            or (
                engine_owned_bom
                and not bool(_get(line, "corr_manual"))
            )
        )
        for line in list(generated_lines or []):
            trace.append(
                {
                    "event": "bom_line_clear_generated",
                    "bom_id": bom_id,
                    "component_name": _product_name_for_record(_get(line, "product_id")),
                    "old_line_qty": _safe_float(_get(line, "product_qty"), 0.0),
                    "corr_component_key": _get(line, "corr_component_key"),
                    "generated": bool(_get(line, "corr_engine_generated")),
                    "manual": bool(_get(line, "corr_manual")),
                    "parent_engine_owned_bom": engine_owned_bom,
                }
            )
        if generated_lines:
            generated_lines.unlink()
    except AttributeError:
        pass
    try:
        operations = _get(bom, "operation_ids")
        generated_operations = operations.filtered(lambda op: bool(_get(op, "corr_engine_generated")))
        if generated_operations:
            generated_operations.unlink()
    except AttributeError:
        pass
    return trace


def _bom_header_debug_snapshot(env: Any, bom_id: Any) -> Dict[str, Any]:
    record = _browse_one(env, "mrp.bom", bom_id)
    if not record:
        return {}
    return {
        "bom_id": bom_id,
        "product_name": _product_name_for_record(_get(record, "product_tmpl_id")),
        "product_qty": _safe_float(_get(record, "product_qty"), 0.0),
        "corr_sfg_key": _get(record, "corr_sfg_key"),
    }


def _product_name_for_id(env: Any, product_id: Any) -> Optional[str]:
    if not product_id:
        return None
    product = _browse_one(env, "product.product", product_id)
    return _product_name_for_record(product)


def _product_name_for_record(record: Any) -> Optional[str]:
    if not record:
        return None
    template = _get(record, "product_tmpl_id")
    for item in (record, template):
        if not item:
            continue
        value = _get(item, "display_name") or _get(item, "name")
        if isinstance(value, dict):
            return value.get("en_US") or next(iter(value.values()), None)
        if value:
            return str(value)
    return None


def _prepare_product_template_values(env: Any, values: Dict[str, Any]) -> Dict[str, Any]:
    prepared = dict(values or {})
    if _is_sfg_product_values(prepared):
        prepared["product_role"] = "sfg"
        prepared["type"] = "consu"
        prepared["sale_ok"] = False
        prepared["purchase_ok"] = False
        prepared["is_storable"] = True
        route_ids = _manufacture_route_ids(env)
        if route_ids:
            prepared["route_ids"] = [(6, 0, route_ids)]
    return prepared


def _ensure_sfg_product_defaults(env: Any, product_tmpl_id: Any, values: Dict[str, Any]) -> None:
    if not product_tmpl_id or not _is_sfg_product_values(values):
        return
    model = env["product.template"].sudo()
    record = _browse_one(env, "product.template", product_tmpl_id)
    if not record:
        return
    prepared = _prepare_product_template_values(env, values)
    update_values = {
        key: prepared.get(key)
        for key in (
            "name",
            "type",
            "product_role",
            "sale_ok",
            "purchase_ok",
            "is_storable",
            "route_ids",
            "corr_sfg_key",
            "corr_sfg_type",
            "corr_stage_code",
            "corr_sheet_length_mm",
            "corr_sheet_width_mm",
            "corr_flute",
            "corr_top_sheet_ref",
            "corr_material_signature",
            "corr_engine_v2_signature",
            "corr_sfg_source_segment_key",
        )
        if key in prepared
    }
    if "name" in update_values and not _should_sync_sfg_name(update_values.get("name")):
        update_values.pop("name", None)
    clean_values = _filter_model_values(model, update_values)
    if not clean_values:
        return
    try:
        record.write(clean_values)
    except AttributeError:
        for key, value in clean_values.items():
            setattr(record, key, value)


def _is_sfg_product_values(values: Dict[str, Any]) -> bool:
    return str((values or {}).get("product_role") or "").strip() == "sfg" or bool((values or {}).get("corr_sfg_key"))


def _should_sync_sfg_name(value: Any) -> bool:
    name = _clean_text(value)
    if not name or name.endswith(" Candidate"):
        return False
    return True


def _manufacture_route_ids(env: Any) -> List[int]:
    ids: List[int] = []
    for xmlid in ("mrp.route_warehouse0_manufacture", "mrp.route_manufacture"):
        route_id = _env_ref_id(env, xmlid)
        if route_id and route_id not in ids:
            ids.append(route_id)
    if ids:
        return ids
    for domain in (
        [("name", "=", "Manufacture")],
        [("name", "ilike", "Manufacture")],
    ):
        route = _search_first(env, "stock.route", domain)
        route_id = _record_id(route)
        if route_id and route_id not in ids:
            ids.append(route_id)
            break
    return ids


def _env_ref_id(env: Any, xmlid: str) -> Optional[int]:
    try:
        record = env.ref(xmlid, raise_if_not_found=False)
    except Exception:
        return None
    return _record_id(record)


def _resolve_line_uom_id(env: Any, product_id: Any, uom_name: Any) -> Optional[int]:
    product = _browse_one(env, "product.product", product_id)
    product_uom = _get(product, "uom_id")
    product_uom_id = _record_id(product_uom)
    if product_uom_id:
        return product_uom_id

    name = str(uom_name or "").strip()
    if not name:
        return None
    aliases = {
        "unit": ["unit", "units", "pcs", "piece", "pieces"],
        "kg": ["kg", "kgs", "kilogram", "kilograms"],
        "sheet": ["sheet", "sheets"],
        "m2": ["m2", "m²", "sqm", "square meter", "square meters"],
    }
    normalized_names = [name]
    lowered = name.lower().replace(".", "").strip()
    if lowered in {"sq m", "sq meter", "sq meters"}:
        lowered = "m2"
    for canonical, values in aliases.items():
        if lowered in values:
            normalized_names.extend(values)
            normalized_names.append(canonical)
            break
    seen = set()
    for candidate in normalized_names:
        candidate = str(candidate or "").strip()
        if not candidate or candidate.lower() in seen:
            continue
        seen.add(candidate.lower())
        record = _search_first(env, "uom.uom", [("name", "=", candidate)])
        uom_id = _record_id(record)
        if uom_id:
            return uom_id
    return None


def _default_operation_workcenter_id(env: Any) -> Optional[int]:
    for domain in (
        [("corr_is_placeholder", "=", True)],
        [("name", "=", "Unassigned Corr Operation")],
    ):
        record = _search_first(env, "mrp.workcenter", domain)
        record_id = _record_id(record)
        if record_id:
            return record_id
    return None


def _product_variant_id_for_template_id(env: Any, product_tmpl_id: Any) -> Optional[int]:
    template = _browse_one(env, "product.template", product_tmpl_id)
    variant_id = _record_id(_get(template, "product_variant_id"))
    if variant_id:
        return variant_id
    try:
        product = env["product.product"].sudo().search(
            [("product_tmpl_id", "=", int(product_tmpl_id or 0))],
            limit=1,
        )
    except Exception:
        product = None
    return _record_id(product)


def _find_existing_product_for_payload(env: Any, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    values = dict(payload.get("values") or {})
    corr_sfg_key = ((payload.get("lookup_key") or {}).get("corr_sfg_key"))
    if not corr_sfg_key:
        corr_sfg_key = values.get("corr_sfg_key")
    if not corr_sfg_key:
        return None
    record = _search_first(env, "product.template", [("corr_sfg_key", "=", corr_sfg_key)])
    if not record:
        return None
    strategy_type = str(values.get("_corr_strategy_type") or "").strip()
    require_material_signature_match = bool(values.get("_corr_require_material_signature_match"))
    required_material_signature = str(values.get("corr_material_signature") or "").strip()
    existing_material_signature = str(_get(record, "corr_material_signature") or "").strip()
    if strategy_type == "independent_routed_piece" and require_material_signature_match and required_material_signature:
        if not existing_material_signature or existing_material_signature != required_material_signature:
            return None
    return {
        "id": _record_id(record),
        "product_variant_id": _record_id(_get(record, "product_variant_id")),
        "source": "existing_corr_sfg_key",
        "corr_sfg_key": corr_sfg_key,
        "corr_material_signature": existing_material_signature,
    }


def _find_existing_bom_for_payload(env: Any, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    values = dict(payload.get("values") or {})
    product_tmpl_id = values.get("product_tmpl_id")
    signature = values.get("corr_engine_v2_signature")
    corr_sfg_key = values.get("corr_sfg_key")
    strategy_type = str(values.get("_corr_strategy_type") or "").strip()
    shareable_branch_sfg = bool(values.get("_corr_shareable_branch_sfg"))
    require_material_signature_match = bool(values.get("_corr_require_material_signature_match"))
    exact_signature_only = (
        strategy_type == "shared_upstream_split"
        or require_material_signature_match
        or (strategy_type == "independent_routed_piece" and not shareable_branch_sfg)
    )
    domains: List[List[Any]] = []
    if product_tmpl_id and signature:
        domains.append(
            [
                ("product_tmpl_id", "=", product_tmpl_id),
                ("corr_engine_v2_signature", "=", signature),
            ]
        )
    if corr_sfg_key and not exact_signature_only:
        domains.append([("corr_sfg_key", "=", corr_sfg_key)])
    for domain in domains:
        record = _search_first(env, "mrp.bom", domain)
        if record:
            if _existing_bom_is_incomplete_for_payload(record, values):
                continue
            return {
                "id": _record_id(record),
                "source": "existing_bom_candidate_key",
                "corr_sfg_key": corr_sfg_key,
                "corr_engine_v2_signature": signature,
            }
    current = _find_unused_current_engine_v2_fg_bom_for_payload(env, values)
    if current:
        return {
            "id": _record_id(current),
            "source": "unused_current_engine_v2_fg_bom_for_new_signature",
            "corr_sfg_key": corr_sfg_key,
            "corr_engine_v2_signature": signature,
        }
    return None


def _find_unused_current_engine_v2_fg_bom_for_payload(env: Any, values: Dict[str, Any]) -> Any:
    product_tmpl_id = values.get("product_tmpl_id")
    signature = values.get("corr_engine_v2_signature")
    bom_role = str(values.get("bom_role") or "").strip().lower()
    if not product_tmpl_id or not signature or bom_role not in {"fg", "single_bom"}:
        return None
    if values.get("corr_sfg_key"):
        return None
    domain = [
        ("product_tmpl_id", "=", product_tmpl_id),
        ("corr_engine_v2_generated", "=", True),
        ("corr_is_current_engine_v2_bom", "=", True),
        ("corr_engine_v2_signature", "!=", False),
        ("corr_sfg_key", "=", False),
        ("corr_stage", "=", False),
        ("type", "=", "normal"),
        ("active", "=", True),
    ]
    record = _search_first(env, "mrp.bom", domain)
    if not record or _bom_has_mo_reference(env, _record_id(record)):
        return None
    return record


def _bom_has_mo_reference(env: Any, bom_id: Any) -> bool:
    bom_id = _safe_int(bom_id, 0)
    if not bom_id:
        return False
    try:
        count = env["mrp.production"].sudo().search_count([("bom_id", "=", bom_id)], limit=1)
    except TypeError:
        count = env["mrp.production"].sudo().search_count([("bom_id", "=", bom_id)])
    except Exception:
        return True
    return bool(count)


def _find_incomplete_existing_bom_for_payload(env: Any, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    values = dict(payload.get("values") or {})
    product_tmpl_id = values.get("product_tmpl_id")
    signature = values.get("corr_engine_v2_signature")
    corr_sfg_key = values.get("corr_sfg_key")
    strategy_type = str(values.get("_corr_strategy_type") or "").strip()
    shareable_branch_sfg = bool(values.get("_corr_shareable_branch_sfg"))
    require_material_signature_match = bool(values.get("_corr_require_material_signature_match"))
    exact_signature_only = (
        strategy_type == "shared_upstream_split"
        or require_material_signature_match
        or (strategy_type == "independent_routed_piece" and not shareable_branch_sfg)
    )
    domains: List[List[Any]] = []
    if product_tmpl_id and signature:
        domains.append(
            [
                ("product_tmpl_id", "=", product_tmpl_id),
                ("corr_engine_v2_signature", "=", signature),
            ]
        )
    if corr_sfg_key and not exact_signature_only:
        domains.append([("corr_sfg_key", "=", corr_sfg_key)])
    for domain in domains:
        record = _search_first(env, "mrp.bom", domain)
        if record and _existing_bom_is_incomplete_for_payload(record, values):
            return {
                "id": _record_id(record),
                "source": "incomplete_existing_bom_header",
                "corr_sfg_key": corr_sfg_key,
                "corr_engine_v2_signature": signature,
            }
    return None


def _search_first(env: Any, model_name: str, domain: List[Any]) -> Any:
    try:
        records = env[model_name].sudo().search(domain, limit=1)
    except Exception:
        return None
    if isinstance(records, list):
        return records[0] if records else None
    return records if _record_id(records) else None


def _existing_bom_is_incomplete_for_payload(record: Any, values: Dict[str, Any]) -> bool:
    strategy_type = str(values.get("_corr_strategy_type") or "").strip()
    require_material_signature_match = bool(values.get("_corr_require_material_signature_match"))
    if strategy_type not in {"shared_upstream_split", "independent_routed_piece"} and not require_material_signature_match:
        return False
    expected_line_count = _safe_int(values.get("_corr_expected_line_count"), 0)
    expected_operation_count = _safe_int(values.get("_corr_expected_operation_count"), 0)
    if expected_line_count > 0 and _relation_count(_get(record, "bom_line_ids")) <= 0:
        return True
    if expected_operation_count > 0 and _relation_count(_get(record, "operation_ids")) <= 0:
        return True
    return False


def _relation_count(value: Any) -> int:
    if not value:
        return 0
    try:
        return len(value)
    except Exception:
        pass
    try:
        return int(getattr(value, "ids", []) and len(value.ids) or 0)
    except Exception:
        return 0


def _browse_one(env: Any, model_name: str, record_id: Any) -> Any:
    try:
        record_id_int = int(record_id or 0)
    except Exception:
        record_id_int = 0
    if not record_id_int:
        return None
    try:
        records = env[model_name].sudo().browse(record_id_int)
    except Exception:
        return None
    if isinstance(records, list):
        return records[0] if records else None
    return records if _record_id(records) else None


def _filter_model_values(model: Any, values: Dict[str, Any]) -> Dict[str, Any]:
    fields = getattr(model, "_fields", None)
    if not fields:
        return dict(values or {})
    return {
        key: value
        for key, value in dict(values or {}).items()
        if key in fields and value is not None
    }


def _reused_product_map(resolver_dry_run: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for product in list((resolver_dry_run or {}).get("products") or []):
        if product.get("action") != "reuse_product":
            continue
        match = product.get("existing_product_match") or {}
        result[str(product.get("product_candidate_key") or "")] = {
            "id": match.get("id"),
            "product_variant_id": match.get("product_variant_id"),
        }
    return result


def _reused_bom_map(resolver_dry_run: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for bom in list((resolver_dry_run or {}).get("boms") or []):
        if bom.get("action") != "reuse_bom":
            continue
        match = bom.get("existing_bom_match") or {}
        result[str(bom.get("bom_candidate_key") or "")] = {"id": match.get("id")}
    return result


def _bom_create_payload(
    resolver_bom: Dict[str, Any],
    materialized_bom: Dict[str, Any],
    product_by_key: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    product_candidate_key = resolver_bom.get("product_candidate_key")
    product = product_by_key.get(str(product_candidate_key or ""), {})
    metadata = dict((materialized_bom or {}).get("metadata") or {})
    return {
        "candidate_key": resolver_bom.get("bom_candidate_key"),
        "model": "mrp.bom",
        "values": {
            "product_candidate_key": product_candidate_key,
            "product_tmpl_id": (product.get("existing_product_match") or {}).get("id"),
            "product_qty": _safe_float((materialized_bom or {}).get("product_qty"), 1.0),
            "type": resolver_bom.get("bom_type") or (materialized_bom or {}).get("bom_type"),
            "bom_role": resolver_bom.get("bom_role"),
            "corr_engine_v2_generated": True,
            "corr_sfg_key": product.get("corr_sfg_key"),
            "corr_engine_v2_signature": _bom_signature(materialized_bom or {}),
            "corr_segment_key": (materialized_bom or {}).get("segment_key"),
            "_corr_strategy_type": metadata.get("strategy_type"),
            "_corr_require_material_signature_match": _requires_product_material_signature_match(metadata),
            "_corr_shareable_branch_sfg": _is_shareable_branch_sfg_bom(materialized_bom or {}),
            "_corr_expected_line_count": len(list((materialized_bom or {}).get("lines") or [])),
            "_corr_expected_operation_count": len(list((materialized_bom or {}).get("operations") or [])),
        },
    }


def _is_shareable_branch_sfg_bom(materialized_bom: Dict[str, Any]) -> bool:
    metadata = dict((materialized_bom or {}).get("metadata") or {})
    return (
        metadata.get("strategy_type") == "independent_routed_piece"
        and not bool(metadata.get("terminal_piece_bom"))
        and not bool(metadata.get("fg_merge_bom"))
    )


def _bom_signature(materialized_bom: Dict[str, Any]) -> str:
    metadata = dict((materialized_bom or {}).get("metadata") or {})
    shareable_branch_sfg = (
        metadata.get("strategy_type") == "independent_routed_piece"
        and not bool(metadata.get("terminal_piece_bom"))
        and not bool(metadata.get("fg_merge_bom"))
    )
    payload = {
        "strategy_type": metadata.get("strategy_type"),
        "shared_upstream_model": metadata.get("shared_upstream_model"),
        "segment_key": metadata.get("stage_key") if shareable_branch_sfg else materialized_bom.get("segment_key"),
        "corr_plan_signature": None if shareable_branch_sfg else materialized_bom.get("corr_plan_signature"),
        "corr_material_signature": materialized_bom.get("corr_material_signature"),
        "corr_route_signature": materialized_bom.get("corr_route_signature"),
        "corr_layout_signature": materialized_bom.get("corr_layout_signature"),
        "corr_resolution_signature": materialized_bom.get("corr_resolution_signature"),
        "lines": [
            {
                "line_type": line.get("line_type"),
                "role_code": line.get("role_code"),
                "product_candidate_key": (line.get("metadata") or {}).get("component_corr_sfg_key") if shareable_branch_sfg else line.get("product_candidate_key"),
                "source_segment_key": None if shareable_branch_sfg else line.get("source_segment_key"),
                "qty": line.get("qty"),
                "uom": line.get("uom"),
                "operation_segment_key": None if shareable_branch_sfg else line.get("operation_segment_key"),
                "product_id": (line.get("metadata") or {}).get("product_id"),
                "writer_product_key": (line.get("metadata") or {}).get("writer_product_key"),
                "product_tmpl_id": (line.get("metadata") or {}).get("product_tmpl_id"),
            }
            for line in list(materialized_bom.get("lines") or [])
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _bom_line_create_payload(
    resolver_bom: Dict[str, Any],
    line: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = dict(line.get("metadata") or {})
    corr_role = _bom_line_corr_role(line, metadata)
    corr_layer_no = _safe_int(metadata.get("layer_no") or (metadata.get("preview_metadata") or {}).get("layer_no"), 0)
    corr_flute_code = _clean_text(
        metadata.get("flute_code")
        or metadata.get("flute")
        or (metadata.get("preview_metadata") or {}).get("flute_code")
        or ((metadata.get("resolution") or {}).get("flute_code"))
    )
    return {
        "candidate_key": line.get("line_key"),
        "parent_bom_candidate_key": resolver_bom.get("bom_candidate_key"),
        "model": "mrp.bom.line",
        "values": {
            "line_type": line.get("line_type"),
            "product_id": metadata.get("product_id") or _product_id_from_writer_key(metadata.get("writer_product_key")),
            "product_candidate_key": line.get("product_candidate_key"),
            "source_segment_key": line.get("source_segment_key"),
            "product_qty": _safe_float(line.get("qty"), 0.0),
            "product_uom_name": line.get("uom"),
            "role_code": line.get("role_code"),
            "operation_segment_key": line.get("operation_segment_key"),
            "corr_component_key": _bom_line_component_key(line, metadata),
            "corr_engine_generated": True,
            "corr_role": corr_role,
            "corr_layer_no": corr_layer_no or None,
            "corr_flute_code": corr_flute_code or None,
            "corr_gsm": _positive_float(metadata.get("gsm") or (metadata.get("preview_metadata") or {}).get("gsm"), 0.0) or None,
        },
    }


def _bom_line_corr_role(line: Dict[str, Any], metadata: Dict[str, Any]) -> Optional[str]:
    role = _normalize_token(
        metadata.get("semantic_role")
        or line.get("role_code")
        or metadata.get("role")
        or metadata.get("type")
    )
    if role in {"top_sheet_external", "top_roll", "top_sheet"}:
        return "top_sheet"
    if role in {"liner", "medium", "adhesive", "ink", "lamination", "wire", "tape"}:
        return role
    if role.startswith("adhesive"):
        return "adhesive"
    return None


def _bom_line_component_key(line: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    corr_role = _bom_line_corr_role(line, metadata) or _clean_text(line.get("role_code"))
    product_id = metadata.get("product_id") or _product_id_from_writer_key(metadata.get("writer_product_key")) or ""
    layer_no = _safe_int(metadata.get("layer_no") or (metadata.get("preview_metadata") or {}).get("layer_no"), 0)
    source = _clean_text(line.get("source_segment_key") or line.get("operation_segment_key") or line.get("bom_key"))
    return "%s:%s:%s:%s" % (corr_role or "component", product_id or "", layer_no or "", source)


def _mergeable_bom_line_identity(values: Dict[str, Any]) -> Optional[Any]:
    if _clean_text(values.get("line_type")) != "previous_output":
        return None
    product_id = values.get("product_id")
    if not product_id:
        return None
    return (
        values.get("bom_id"),
        product_id,
        values.get("product_uom_id") or values.get("product_uom_name"),
        values.get("operation_segment_key"),
    )


def _collapse_bom_line_payloads(payloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    collapsed: List[Dict[str, Any]] = []
    index_by_identity: Dict[Any, int] = {}
    for payload in payloads:
        values = dict(payload.get("values") or {})
        identity = _mergeable_bom_line_identity(values)
        if not identity:
            collapsed.append(payload)
            continue
        existing_index = index_by_identity.get(identity)
        if existing_index is None:
            index_by_identity[identity] = len(collapsed)
            collapsed.append(payload)
            continue
        existing_payload = collapsed[existing_index]
        existing_values = existing_payload.setdefault("values", {})
        existing_values["product_qty"] = _safe_float(existing_values.get("product_qty"), 0.0) + _safe_float(
            values.get("product_qty"),
            0.0,
        )
        merged_sources = list(existing_values.get("merged_source_segment_keys") or [])
        for source_key in (
            existing_values.get("source_segment_key"),
            values.get("source_segment_key"),
        ):
            source_key = _clean_text(source_key)
            if source_key and source_key not in merged_sources:
                merged_sources.append(source_key)
        if merged_sources:
            existing_values["merged_source_segment_keys"] = merged_sources
    return collapsed


def _bom_operation_create_payload(
    resolver_bom: Dict[str, Any],
    operation: Dict[str, Any],
) -> Dict[str, Any]:
    time_cycle = _positive_float(operation.get("time_cycle_manual") or operation.get("duration_expected"), 1.0)
    capacity = _positive_float(operation.get("capacity"), 1.0)
    operation_name = _bom_operation_display_name(operation)
    workcenter_id = operation.get("routing_workcenter_id") or operation.get("workcenter_id") or False
    return {
        "candidate_key": operation.get("operation_candidate_key"),
        "parent_bom_candidate_key": resolver_bom.get("bom_candidate_key"),
        "model": "mrp.routing.workcenter",
        "operation_scope": "local_bom_operations",
        "values": {
            "name": operation_name,
            "sequence": operation.get("sequence"),
            "time_mode": "manual",
            "time_cycle_manual": time_cycle,
            "capacity": capacity,
            "workcenter_id": workcenter_id,
            "corr_engine_generated": True,
            "stage_contract_code": operation.get("stage_contract_code"),
            "stage_template_id": operation.get("stage_template_id"),
            "stage_code": operation.get("stage_code"),
            "input_qty": operation.get("input_qty"),
            "output_qty": operation.get("output_qty"),
            "uom": operation.get("uom"),
        },
    }


def _bom_operation_display_name(operation: Dict[str, Any]) -> Any:
    operation_name = operation.get("operation_name")
    stage_name = operation.get("stage_name")
    if _normalize_token(operation_name) == "conversion" and stage_name:
        return stage_name
    return operation_name or stage_name or operation.get("stage_code") or operation.get("segment_key")


def _sfg_display_context_by_product_key(
    materialization_plan: Dict[str, Any],
    product_by_key: Dict[str, Dict[str, Any]],
    materialized_product_by_key: Dict[str, Dict[str, Any]],
    *,
    env: Any = None,
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    plan_metadata = dict((materialization_plan or {}).get("metadata") or {})
    plan_layout = _selected_layout_dict(plan_metadata.get("selected_layout"))
    fallback_top_sheet_ref = _top_sheet_ref_from_plan_metadata(plan_metadata)
    for bom in list((materialization_plan or {}).get("boms") or []):
        product_key = str(bom.get("product_candidate_key") or "")
        if not product_key or product_key not in product_by_key:
            continue
        product = product_by_key.get(product_key) or {}
        materialized_product = materialized_product_by_key.get(product_key) or {}
        lines = list(bom.get("lines") or [])
        operation = _first_operation(bom)
        stage_code = _stage_code_for_display(product, bom, operation)
        if _is_template_fallback_stage(stage_code) and _has_material_role(
            lines,
            {"top_roll", "top_sheet", "top_sheet_external"},
        ):
            stage_code = "top_sheet_cut"
        line_layout = _first_line_layout(lines)
        product_metadata = {
            **dict(materialized_product.get("metadata") or {}),
            **dict(product.get("metadata") or {}),
        }
        bom_metadata = dict(bom.get("metadata") or {})
        context = {
            "corr_sfg_type": _sfg_type_from_stage(stage_code),
            "corr_stage_code": stage_code,
            "shared_upstream_sfg": bool(product_metadata.get("shared_upstream_sfg") or bom_metadata.get("shared_upstream_bom")),
            "split_output_sfg": bool(product_metadata.get("split_output_sfg") or bom_metadata.get("split_output_bom")),
            "piece_2ply_set": bool(product_metadata.get("piece_2ply_set") or bom_metadata.get("piece_2ply_set")),
            "terminal_piece_sfg": bool(product_metadata.get("terminal_piece_sfg") or bom_metadata.get("terminal_piece_bom")),
            "fg_product_name": product_metadata.get("fg_product_name") or bom_metadata.get("fg_product_name"),
            "piece_index": product_metadata.get("piece_index") or bom_metadata.get("piece_index"),
            "set_key": product_metadata.get("set_key") or bom_metadata.get("set_key"),
            "corr_sheet_length_mm": _sheet_dimension_mm("length", product, materialized_product, bom, line_layout, plan_layout),
            "corr_sheet_width_mm": _sheet_dimension_mm("width", product, materialized_product, bom, line_layout, plan_layout),
            "input_deckle_width_mm": _layout_metadata_value("input_deckle_width_mm", bom, line_layout, plan_layout),
            "source_width_mm": _layout_metadata_value("source_width_mm", bom, line_layout, plan_layout),
            "slitting_ups": _layout_metadata_value("slitting_ups", bom, line_layout, plan_layout),
            "production_cycles": _layout_metadata_value("production_cycles", bom, line_layout, plan_layout),
            "corr_flute": _first_material_value(lines, "flute_code", "flute", "flute_type_code"),
            "corr_top_sheet_ref": _top_sheet_ref(lines, env=env) or fallback_top_sheet_ref,
            "liner_gsm": _material_value_by_role(lines, {"liner", "top_liner"}, "gsm"),
            "medium_gsm": _material_value_by_role(lines, {"medium", "fluting"}, "gsm"),
            "liner_bf": _material_value_by_role(lines, {"liner", "top_liner"}, "bf"),
            "medium_bf": _material_value_by_role(lines, {"medium", "fluting"}, "bf"),
            "top_roll_gsm": _material_value_by_role(lines, {"top_roll", "top_sheet", "top_sheet_external"}, "gsm"),
            "top_roll_bf": _material_value_by_role(lines, {"top_roll", "top_sheet", "top_sheet_external"}, "bf"),
            "stage_operation_name": _operation_display_name(operation, stage_code),
        }
        result[product_key] = context

    for bom in list((materialization_plan or {}).get("boms") or []):
        product_key = str(bom.get("product_candidate_key") or "")
        context = result.get(product_key)
        if not context:
            continue
        inherited_surface_context = _inherit_surface_context_from_previous_output(bom, result)
        if inherited_surface_context:
            if not _clean_text(context.get("corr_top_sheet_ref")):
                context["corr_top_sheet_ref"] = inherited_surface_context.get("corr_top_sheet_ref")
            if context.get("top_roll_gsm") in (None, ""):
                context["top_roll_gsm"] = inherited_surface_context.get("top_roll_gsm")
            if context.get("top_roll_bf") in (None, ""):
                context["top_roll_bf"] = inherited_surface_context.get("top_roll_bf")

    two_ply_name_counts: Dict[str, int] = {}
    two_ply_base_names: Dict[str, str] = {}
    for product_key, context in result.items():
        if not _is_2ply_stage(context.get("corr_stage_code")):
            continue
        base_name = _planned_2ply_product_name({}, context, include_set_key=False)
        two_ply_base_names[product_key] = base_name
        two_ply_name_counts[base_name] = two_ply_name_counts.get(base_name, 0) + 1

    for product_key, context in result.items():
        if not _is_2ply_stage(context.get("corr_stage_code")):
            continue
        base_name = two_ply_base_names.get(product_key) or ""
        context["show_set_key_in_name"] = two_ply_name_counts.get(base_name, 0) > 1
    return result


def _planned_product_name(product: Dict[str, Any], display_context: Optional[Dict[str, Any]] = None) -> str:
    display_context = dict(display_context or {})
    stage_code = _normalize_token(display_context.get("corr_stage_code") or product.get("corr_segment_key"))
    length_mm = display_context.get("corr_sheet_length_mm")
    width_mm = display_context.get("corr_sheet_width_mm")
    dimensions = _dimension_text_cm(length_mm, width_mm)
    if bool(display_context.get("terminal_piece_sfg")):
        fg_name = _clean_text(display_context.get("fg_product_name"))
        piece_index = _clean_number(display_context.get("piece_index"))
        if fg_name and piece_index:
            return "%s - Piece %s" % (fg_name, piece_index)
        if fg_name:
            return "%s - Piece" % fg_name
    if bool(display_context.get("shared_upstream_sfg")):
        if dimensions:
            return "Shared 2-Ply %s cm" % dimensions
        return "Shared 2-Ply"
    if bool(display_context.get("split_output_sfg")):
        if dimensions:
            return "2-Ply %s cm" % dimensions
        return "2-Ply"
    if _is_cutting_stage(stage_code):
        return _planned_cutting_product_name(product, display_context)
    if _is_printing_stage(stage_code):
        return _planned_surface_product_name("Top-sheet Printed", display_context)
    if _is_2ply_stage(stage_code):
        return _planned_2ply_product_name(product, display_context)
    if _is_board_stage(stage_code):
        top_ref = _clean_text(display_context.get("corr_top_sheet_ref"))
        if dimensions and top_ref:
            return "Board %s cm (%s)" % (dimensions, top_ref)
        if dimensions:
            return "Board %s cm" % dimensions
        if top_ref:
            return "Board (%s)" % top_ref
        return "Board"
    top_ref = _clean_text(display_context.get("corr_top_sheet_ref"))
    operation_name = _clean_text(display_context.get("stage_operation_name")) or _fallback_stage_name(stage_code)
    if top_ref:
        return "%s (%s)" % (operation_name, top_ref)
    if operation_name:
        return operation_name
    role = str(product.get("product_role") or "sfg").upper()
    return "%s Candidate" % role


def _inherit_surface_context_from_previous_output(
    bom: Dict[str, Any],
    display_context_by_product_key: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    for line in list((bom or {}).get("lines") or []):
        if _clean_text(line.get("line_type")) != "previous_output":
            continue
        upstream_key = _clean_text(line.get("product_candidate_key"))
        if not upstream_key:
            continue
        upstream_context = dict(display_context_by_product_key.get(upstream_key) or {})
        if (
            _clean_text(upstream_context.get("corr_top_sheet_ref"))
            or upstream_context.get("top_roll_gsm") not in (None, "")
            or upstream_context.get("top_roll_bf") not in (None, "")
        ):
            return upstream_context
    return {}


def _first_operation(bom: Dict[str, Any]) -> Dict[str, Any]:
    operations = list((bom or {}).get("operations") or [])
    return dict(operations[0] or {}) if operations else {}


def _stage_code_for_display(product: Dict[str, Any], bom: Dict[str, Any], operation: Dict[str, Any]) -> str:
    return _normalize_token(
        operation.get("stage_code")
        or bom.get("corr_segment_key")
        or bom.get("segment_key")
        or product.get("corr_segment_key")
        or operation.get("stage_contract_code")
    )


def _sfg_type_from_stage(stage_code: Any) -> str:
    stage = _normalize_token(stage_code)
    if _is_2ply_stage(stage):
        return "2ply"
    if _is_board_stage(stage):
        return "board"
    return "conversion"


def _is_2ply_stage(stage_code: Any) -> bool:
    stage = _normalize_token(stage_code)
    return "2ply" in stage or "2_ply" in stage or "2-ply" in stage or stage.endswith(".2ply")


def _is_board_stage(stage_code: Any) -> bool:
    stage = _normalize_token(stage_code)
    return "board" in stage


def _is_template_fallback_stage(stage_code: Any) -> bool:
    stage = _normalize_token(stage_code)
    return not stage or stage.startswith("template") or "stage_template" in stage


def _is_cutting_stage(stage_code: Any) -> bool:
    stage = _normalize_token(stage_code)
    return "cutting" in stage or "ts_cut" in stage or "top_sheet_cut" in stage


def _is_printing_stage(stage_code: Any) -> bool:
    stage = _normalize_token(stage_code)
    return "printing" in stage or "ts_print" in stage or "top_sheet_print" in stage


def _planned_cutting_product_name(
    product: Dict[str, Any],
    display_context: Optional[Dict[str, Any]] = None,
) -> str:
    return _planned_surface_product_name(
        _fallback_stage_name((display_context or {}).get("corr_stage_code") or product.get("corr_segment_key")),
        display_context,
    )


def _planned_surface_product_name(
    label: str,
    display_context: Optional[Dict[str, Any]] = None,
) -> str:
    display_context = dict(display_context or {})
    length_mm = display_context.get("corr_sheet_length_mm")
    width_mm = display_context.get("corr_sheet_width_mm")
    dimensions = _dimension_text_cm(length_mm, width_mm)
    gsm = _clean_number(display_context.get("top_roll_gsm"))
    bf = _clean_number(display_context.get("top_roll_bf"))
    parts = []
    if gsm:
        parts.append("%s GSM" % gsm)
    if bf:
        parts.append("BF:%s" % bf)
    material = " ".join(parts)
    if dimensions and material:
        return "%s %s cm (%s)" % (label, dimensions, material)
    if dimensions:
        return "%s %s cm" % (label, dimensions)
    if material:
        return "%s (%s)" % (label, material)
    return label


def _planned_2ply_product_name(
    product: Dict[str, Any],
    display_context: Optional[Dict[str, Any]] = None,
    *,
    include_set_key: Optional[bool] = None,
) -> str:
    display_context = dict(display_context or {})
    length_mm = display_context.get("corr_sheet_length_mm")
    width_mm = display_context.get("corr_sheet_width_mm")
    dimensions = _dimension_text_cm(length_mm, width_mm)
    set_key = _clean_text(display_context.get("set_key"))
    flute = _clean_text(display_context.get("corr_flute")).upper()
    liner_gsm = _clean_number(display_context.get("liner_gsm") or display_context.get("corr_liner_gsm"))
    medium_gsm = _clean_number(display_context.get("medium_gsm") or display_context.get("corr_medium_gsm"))
    liner_bf = _clean_number(display_context.get("liner_bf") or display_context.get("corr_liner_bf"))
    medium_bf = _clean_number(display_context.get("medium_bf") or display_context.get("corr_medium_bf"))
    material = _two_ply_material_text(flute, liner_gsm, medium_gsm, liner_bf, medium_bf)
    if include_set_key is None:
        include_set_key = bool(display_context.get("show_set_key_in_name"))
    label = "2-Ply %s" % set_key.upper() if (include_set_key and set_key) else "2-Ply"
    if dimensions and material:
        return "%s %s cm (%s)" % (label, dimensions, material)
    if dimensions:
        return "%s %s cm" % (label, dimensions)
    if material:
        return "%s (%s)" % (label, material)
    return label


def _operation_display_name(operation: Dict[str, Any], stage_code: Any) -> str:
    metadata = dict((operation or {}).get("metadata") or {})
    output_label = _clean_text(metadata.get("yield_output_label")).lower()
    output_kind = _clean_text(metadata.get("yield_output_kind")).lower()
    normalized_stage = _normalize_token(stage_code)
    if ("diecut" in normalized_stage or "slot_die" in normalized_stage or "slot-die" in normalized_stage) and (
        output_kind == "gross_sheet" or output_label == "gross diecut sheet"
    ):
        return "Diecut Gross Sheet"
    if "peel" in normalized_stage and output_label == "usable blanks":
        return "Peeloff Usable Blank"
    name = _clean_text((operation or {}).get("operation_name"))
    if name:
        return _short_operation_name(name)
    return _fallback_stage_name(stage_code)


def _short_operation_name(value: Any) -> str:
    text = _clean_text(value)
    lowered = text.lower()
    replacements = {
        "slot/die": "Die Cutting",
        "slot die": "Die Cutting",
        "slotting": "Die Cutting",
        "die cutting": "Die Cutting",
        "die-cutting": "Die Cutting",
        "die cut": "Die Cutting",
        "peel-off": "Peeling",
        "peel off": "Peeling",
        "waste stripping": "Peeling",
        "peeling": "Peeling",
        "stripping": "Stripping",
        "stitching": "Stitching",
        "seaming": "Seaming",
        "bundling": "Bundling",
        "packing": "Packing",
    }
    for key, label in replacements.items():
        if key in lowered:
            return label
    return text


def _fallback_stage_name(stage_code: Any) -> str:
    text = _normalize_token(stage_code)
    if not text:
        return "SFG"
    for token, label in (
        ("die_cut", "Die Cutting"),
        ("diecut", "Die Cutting"),
        ("peeling", "Peeling"),
        ("stripping", "Stripping"),
        ("stitching", "Stitching"),
        ("seaming", "Seaming"),
        ("bundling", "Bundling"),
        ("packing", "Packing"),
        ("top_sheet_cut", "Top-sheet Cut"),
        ("top sheet cut", "Top-sheet Cut"),
        ("ts_cut", "Top-sheet Cut"),
        ("cutting", "Cutting"),
        ("board", "Board"),
        ("2ply", "2-Ply"),
        ("2_ply", "2-Ply"),
    ):
        if token in text:
            return label
    tail = text.replace(".", "_").replace("-", "_").split("_")[-1]
    return " ".join(part.capitalize() for part in tail.split() if part) or "SFG"


def _sheet_dimension_mm(
    axis: str,
    product: Dict[str, Any],
    materialized_product: Dict[str, Any],
    bom: Dict[str, Any],
    line_layout: Dict[str, Any],
    plan_layout: Dict[str, Any],
) -> Optional[float]:
    output_keys = [
        "output_sheet_%s_mm" % axis,
        "slit_sheet_%s_mm" % axis,
        "usable_sheet_%s_mm" % axis,
    ]
    keys = [
        "corr_sheet_%s_mm" % axis,
        "piece_%s_mm" % axis,
        "sheet_%s_mm" % axis,
        "final_sheet_%s_mm" % axis,
    ]
    for source in (
        dict(product.get("metadata") or {}),
        dict(materialized_product.get("metadata") or {}),
        dict(bom.get("metadata") or {}),
        line_layout,
        dict(line_layout.get("option") or {}),
        plan_layout,
        dict(plan_layout.get("option") or {}),
    ):
        for key in output_keys:
            value = _positive_float(source.get(key), 0.0)
            if value > 0.0:
                return value
        for key in keys:
            value = _positive_float(source.get(key), 0.0)
            if value > 0.0:
                if axis == "width" and key == "final_sheet_width_mm":
                    up_count = _layout_up_count(source)
                    if up_count > 1:
                        return value / float(up_count)
                return value
    return None


def _layout_metadata_value(
    key: str,
    bom: Dict[str, Any],
    line_layout: Dict[str, Any],
    plan_layout: Dict[str, Any],
) -> Any:
    for source in (
        dict((bom or {}).get("metadata") or {}),
        line_layout,
        dict(line_layout.get("option") or {}),
        plan_layout,
        dict(plan_layout.get("option") or {}),
    ):
        value = source.get(key)
        if value not in (None, "", False):
            return value
    return None


def _layout_up_count(source: Dict[str, Any]) -> int:
    for key in ("up_count", "slitting_ups"):
        try:
            value = int(float(source.get(key) or 0))
        except (TypeError, ValueError):
            value = 0
        if value > 1:
            return value
    return 1


def _first_line_layout(lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    for line in lines or []:
        metadata = dict((line or {}).get("metadata") or {})
        layout = _selected_layout_dict(metadata.get("selected_layout"))
        if layout:
            return layout
        preview = dict(metadata.get("preview_metadata") or {})
        layout = _selected_layout_dict(preview.get("selected_layout"))
        if layout:
            return layout
    return {}


def _selected_layout_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _top_sheet_ref(lines: List[Dict[str, Any]], *, env: Any = None) -> str:
    for line in lines or []:
        metadata = dict((line or {}).get("metadata") or {})
        role = _normalize_token(
            metadata.get("semantic_role")
            or (line or {}).get("role_code")
            or metadata.get("role")
            or metadata.get("type")
        )
        aliases = {_normalize_token(item) for item in list(metadata.get("legacy_role_aliases") or [])}
        if role not in {"top_sheet_external", "top_sheet", "top_roll"} and "top_roll" not in aliases:
            continue
        preview = dict(metadata.get("preview_metadata") or {})
        roll_code = _clean_text(
            metadata.get("top_sheet_roll_code")
            or preview.get("top_sheet_roll_code")
            or (preview.get("resolution") or {}).get("top_sheet_roll_code")
        )
        if role == "top_roll" or "top_roll" in aliases or roll_code:
            return "ROLL:%s" % roll_code if roll_code else ""
        ref = _clean_text(
            metadata.get("default_code")
            or metadata.get("product_default_code")
            or metadata.get("internal_ref")
            or metadata.get("product_code")
            or preview.get("default_code")
            or preview.get("product_default_code")
            or preview.get("internal_ref")
            or (preview.get("resolution") or {}).get("default_code")
        )
        if ref:
            return "TOP:%s" % _top_sheet_code_from_ref(ref)
        orm_ref = _top_sheet_orm_ref(env, metadata)
        if orm_ref:
            return "TOP:%s" % orm_ref
        display_ref = _top_sheet_display_ref(metadata, preview)
        if display_ref:
            return display_ref
    return ""


def _top_sheet_ref_from_plan_metadata(metadata: Dict[str, Any]) -> str:
    mode = _normalize_token(metadata.get("top_sheet_mode"))
    roll_code = _clean_text(metadata.get("top_sheet_roll_code"))
    if mode == "roll" and roll_code:
        return "ROLL:%s" % roll_code
    return ""


def _top_sheet_orm_ref(env: Any, metadata: Dict[str, Any]) -> str:
    if env is None:
        return ""
    product_id = metadata.get("product_id") or _product_id_from_writer_key(metadata.get("writer_product_key"))
    product = _browse_one(env, "product.product", product_id) if product_id else None
    ref = _clean_text(
        _get(product, "default_code")
        or _get(product, "barcode")
        or _get(product, "display_name")
    )
    if ref:
        return _top_sheet_code_from_ref(ref)
    tmpl_id = metadata.get("product_tmpl_id")
    template = _browse_one(env, "product.template", tmpl_id) if tmpl_id else None
    variant = _get(template, "product_variant_id") if template else None
    ref = _clean_text(
        _get(variant, "default_code")
        or _get(template, "default_code")
        or _get(variant, "barcode")
        or _get(template, "barcode")
        or _get(variant, "display_name")
        or _get(template, "display_name")
    )
    return _top_sheet_code_from_ref(ref)


def _top_sheet_display_ref(metadata: Dict[str, Any], preview: Dict[str, Any]) -> str:
    resolution = dict(preview.get("resolution") or metadata.get("resolution") or {})
    display = _clean_text(
        metadata.get("product_name")
        or metadata.get("product_label")
        or metadata.get("display_name")
        or preview.get("product_name")
        or preview.get("product_label")
        or preview.get("display_name")
        or resolution.get("product_name")
        or resolution.get("product_label")
        or resolution.get("display_name")
    )
    if not display:
        return ""
    bracket_ref = _strip_display_ref(display)
    if bracket_ref == display:
        upper_display = display.upper()
        if not (upper_display.startswith("TOP:") or upper_display.startswith("ROLL:")):
            return ""
    ref = bracket_ref or display
    upper_ref = ref.upper()
    if upper_ref.startswith("TOP:"):
        return "TOP:%s" % ref.split(":", 1)[1].strip()
    if upper_ref.startswith("ROLL:"):
        return "ROLL:%s" % ref.split(":", 1)[1].strip()
    return ref


def _strip_display_ref(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if text.startswith("[") and "]" in text:
        return _clean_text(text.split("]", 1)[0].lstrip("["))
    return text


def _top_sheet_code_from_ref(value: Any) -> str:
    ref = _strip_display_ref(value)
    upper_ref = ref.upper()
    if upper_ref.startswith("TOP:"):
        return _clean_text(ref.split(":", 1)[1])
    return ref


def _first_material_value(lines: List[Dict[str, Any]], *keys: str) -> Any:
    for line in lines or []:
        metadata = dict((line or {}).get("metadata") or {})
        preview = dict(metadata.get("preview_metadata") or {})
        resolution = dict(preview.get("resolution") or metadata.get("resolution") or {})
        for key in keys:
            if metadata.get(key) not in (None, ""):
                return metadata.get(key)
            if preview.get(key) not in (None, ""):
                return preview.get(key)
            if resolution.get(key) not in (None, ""):
                return resolution.get(key)
    return None


def _material_value_by_role(lines: List[Dict[str, Any]], roles: set, key: str) -> Any:
    normalized_roles = {_normalize_token(role) for role in roles}
    for line in lines or []:
        metadata = dict((line or {}).get("metadata") or {})
        role = _normalize_token(
            metadata.get("semantic_role")
            or (line or {}).get("role_code")
            or metadata.get("role")
            or metadata.get("type")
        )
        if role not in normalized_roles:
            continue
        preview = dict(metadata.get("preview_metadata") or {})
        resolution = dict(preview.get("resolution") or metadata.get("resolution") or {})
        for source in (metadata, preview, resolution):
            if source.get(key) not in (None, ""):
                return source.get(key)
    return None


def _has_material_role(lines: List[Dict[str, Any]], roles: set) -> bool:
    normalized_roles = {_normalize_token(role) for role in roles}
    for line in lines or []:
        metadata = dict((line or {}).get("metadata") or {})
        role = _normalize_token(
            metadata.get("semantic_role")
            or (line or {}).get("role_code")
            or metadata.get("role")
            or metadata.get("type")
        )
        if role in normalized_roles:
            return True
    return False


def _two_ply_material_text(flute: str, liner_gsm: str, medium_gsm: str, liner_bf: str, medium_bf: str) -> str:
    parts = []
    if flute and liner_gsm and medium_gsm:
        parts.append("%s:%s/%s GSM" % (flute, liner_gsm, medium_gsm))
    elif flute:
        parts.append(flute)
    if liner_bf and medium_bf:
        parts.append("BF:%s/%s" % (liner_bf, medium_bf))
    return " ".join(parts)


def _dimension_text_cm(length_mm: Any, width_mm: Any) -> str:
    length = _positive_float(length_mm, 0.0)
    width = _positive_float(width_mm, 0.0)
    if length <= 0.0 or width <= 0.0:
        return ""
    return "%sx%s" % (_clean_number(length / 10.0), _clean_number(width / 10.0))


def _clean_number(value: Any) -> str:
    number = _positive_float(value, 0.0)
    if number <= 0.0:
        return ""
    rounded = round(number)
    if abs(number - rounded) < 0.05:
        return str(int(rounded))
    return ("%.1f" % number).rstrip("0").rstrip(".")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_token(value: Any) -> str:
    return _clean_text(value).lower().replace("-", "_").replace(" ", "_")


def _positive_float(value: Any, default: float = 0.0) -> float:
    result = _safe_float(value, default)
    return result if result > 0.0 else default


def _product_id_from_writer_key(value: Any) -> Optional[int]:
    text = str(value or "")
    if not text.startswith("product.product:"):
        return None
    try:
        return int(text.split(":", 1)[1])
    except Exception:
        return None


def _step_reason(action: str) -> str:
    if action.startswith("create_"):
        return "would_create_when_writer_enabled"
    if action.startswith("reuse_"):
        return "would_reuse_existing_record"
    if action.startswith("archive_"):
        return "would_archive_and_create_new_version_when_writer_enabled"
    if action.startswith("blocked_"):
        return "resolver_blocked_candidate"
    return "planned_only"


def _disabled_reason(config: MaterializationWriterConfig) -> str:
    if not config.context_flag_enabled:
        return "context_flag_disabled"
    if not config.system_parameter_enabled:
        return "system_parameter_disabled"
    if not config.writes_enabled:
        return "writes_disabled"
    if not config.creates_enabled:
        return "creates_disabled"
    if not config.allow_multi_level_boms:
        return "multi_level_bom_writer_disabled"
    return "phase_7b_d6_guarded_executor_only_real_writer_not_enabled_by_default"


def _strategy_reason(strategy: str, mode: str, has_sfg_bom: bool, requested: str) -> str:
    if strategy == "legacy_single_bom_flattened":
        if mode == "single_bom":
            return "single_bom_mode_uses_legacy_flattened_writer"
        if requested == "legacy_single_bom":
            return "legacy_single_bom_strategy_requested"
        return "no_sfg_boundaries_detected"
    if has_sfg_bom:
        return "sfg_boundaries_require_isolated_multi_level_writer"
    return "materialized_multi_level_strategy_requested"


def _guard_diagnostics(config: MaterializationWriterConfig) -> Dict[str, Any]:
    return {
        "context_enable_key": CONTEXT_ENABLE_KEY,
        "system_parameter_key": PARAM_ENABLE_KEY,
        "context_flag_enabled": bool(config.context_flag_enabled),
        "system_parameter_enabled": bool(config.system_parameter_enabled),
        "writes_enabled": bool(config.writes_enabled),
        "creates_enabled": bool(config.creates_enabled),
        "product_create_enabled": bool(config.product_create_enabled),
        "bom_create_enabled": bool(config.bom_create_enabled),
        "archives_enabled": False,
        "archive_delete_enabled": False,
        "mo_creation_enabled": False,
        "default_disabled": not (
            config.context_flag_enabled and config.system_parameter_enabled
        ),
    }


def _real_write_gate_open(config: MaterializationWriterConfig) -> bool:
    return bool(
        config.writes_enabled
        and config.creates_enabled
        and config.allow_multi_level_boms
        and config.context_flag_enabled
        and config.system_parameter_enabled
        and not config.archive_delete_enabled
        and not config.mo_creation_enabled
    )


def _get_param_bool(env: Any, key: str, *, default: bool = False) -> bool:
    if env is None:
        return default
    try:
        value = env["ir.config_parameter"].sudo().get_param(key, "1" if default else "0")
    except Exception:
        return default
    return _as_bool(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


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


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _context_int(env: Any, key: str) -> Optional[int]:
    context = getattr(env, "context", None) or {}
    try:
        value = int(context.get(key) or 0)
    except Exception:
        value = 0
    return value or None


def _record_id(record: Any) -> Optional[int]:
    try:
        value = _get(record, "id")
        return int(value) if value else None
    except Exception:
        return None



