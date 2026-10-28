from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional, Tuple


RESOLVER_VERSION = "7B-D1.7"


def resolve_materialization_plan_dry_run(
    env: Any,
    materialization_plan: Any,
    *,
    company_id: Optional[int] = None,
    fg_product_tmpl_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Resolve product/BoM actions needed for a materialization plan.

    This function is read-only. It performs best-effort ORM searches when an
    Odoo env is provided, but never creates, writes, archives, or updates.
    """

    plan = _as_dict(materialization_plan)
    products = [_as_dict(item) for item in _get(plan, "products", []) or []]
    boms = [_as_dict(item) for item in _get(plan, "boms", []) or []]
    plan_warnings = [_as_dict(item) for item in _get(plan, "warnings", []) or []]

    blockers: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    product_results: List[Dict[str, Any]] = []
    bom_results: List[Dict[str, Any]] = []

    _validate_plan_warnings(plan_warnings, blockers, warnings)

    product_result_by_key: Dict[str, Dict[str, Any]] = {}
    for candidate in products:
        result = _resolve_product_candidate(
            env,
            candidate,
            company_id=company_id,
            fg_product_tmpl_id=fg_product_tmpl_id,
        )
        product_results.append(result)
        product_result_by_key[result["product_candidate_key"]] = result
        if result["action"] == "blocked_product":
            blockers.append(
                {
                    "scope": "product",
                    "candidate_key": result["product_candidate_key"],
                    "reason": result["reason"],
                }
            )

    for candidate in boms:
        result = _resolve_bom_candidate(
            env,
            candidate,
            product_result_by_key,
            company_id=company_id,
        )
        bom_results.append(result)
        if result["action"] == "blocked_bom":
            blockers.append(
                {
                    "scope": "bom",
                    "candidate_key": result["bom_candidate_key"],
                    "reason": result["reason"],
                }
            )
        for warning in result.get("warnings") or []:
            warnings.append(warning)

    return {
        "resolver_version": RESOLVER_VERSION,
        "dry_run": True,
        "mode": _get(plan, "mode"),
        "corr_plan_signature": _get(plan, "corr_plan_signature"),
        "resolver_layout_signature": _first_layout_signature(boms),
        "company_id": company_id,
        "products": product_results,
        "boms": bom_results,
        "blockers": blockers,
        "warnings": warnings,
        "metadata": {
            "writes_enabled": False,
            "creates_enabled": False,
            "archives_enabled": False,
        },
    }


def _resolve_product_candidate(
    env: Any,
    candidate: Dict[str, Any],
    *,
    company_id: Optional[int],
    fg_product_tmpl_id: Optional[int],
) -> Dict[str, Any]:
    candidate_key = str(_get(candidate, "candidate_key") or "")
    product_role = str(_get(candidate, "product_role") or "")
    if product_role == "fg":
        match = _record_snapshot(_browse_record(_model(env, "product.template"), fg_product_tmpl_id))
        return {
            "product_candidate_key": candidate_key,
            "product_role": product_role,
            "corr_sfg_key": _get(candidate, "corr_sfg_key"),
            "existing_product_match": match,
            "action": "reuse_product",
            "reason": "finished_good_product_supplied_by_current_flow",
            "blockers": [],
        }

    corr_sfg_key = _get(candidate, "corr_sfg_key")
    if not corr_sfg_key:
        return {
            "product_candidate_key": candidate_key,
            "product_role": product_role,
            "corr_sfg_key": corr_sfg_key,
            "existing_product_match": None,
            "action": "blocked_product",
            "reason": "missing_corr_sfg_key",
            "blockers": ["missing_corr_sfg_key"],
        }

    ProductTemplate = _model(env, "product.template")
    match = _search_first(
        ProductTemplate,
        [("corr_sfg_key", "=", corr_sfg_key), ("active", "in", [True, False])],
    )
    company_reason = _company_incompatibility(match, company_id)
    if company_reason:
        return {
            "product_candidate_key": candidate_key,
            "product_role": product_role,
            "corr_sfg_key": corr_sfg_key,
            "existing_product_match": _record_snapshot(match),
            "action": "blocked_product",
            "reason": company_reason,
            "blockers": [company_reason],
        }
    if _record_exists(match):
        return {
            "product_candidate_key": candidate_key,
            "product_role": product_role,
            "corr_sfg_key": corr_sfg_key,
            "existing_product_match": _record_snapshot(match),
            "action": "reuse_product",
            "reason": "corr_sfg_key_match",
            "blockers": [],
        }
    return {
        "product_candidate_key": candidate_key,
        "product_role": product_role,
        "corr_sfg_key": corr_sfg_key,
        "existing_product_match": None,
        "action": "create_product",
        "reason": "no_existing_corr_sfg_key_match",
        "blockers": [],
    }


def _resolve_bom_candidate(
    env: Any,
    candidate: Dict[str, Any],
    product_result_by_key: Dict[str, Dict[str, Any]],
    *,
    company_id: Optional[int],
) -> Dict[str, Any]:
    bom_key = str(_get(candidate, "bom_key") or "")
    product_candidate_key = str(_get(candidate, "product_candidate_key") or "")
    product_result = product_result_by_key.get(product_candidate_key)
    blockers: List[str] = []
    warnings: List[Dict[str, Any]] = []
    line_diagnostics: List[Dict[str, Any]] = []

    if not product_result:
        blockers.append("missing_product_candidate_resolution")
    elif product_result.get("action") == "blocked_product":
        blockers.append("product_candidate_blocked")
        if product_result.get("reason"):
            blockers.append(str(product_result.get("reason")))

    diagnostic_only = list((_get(candidate, "metadata", {}) or {}).get("diagnostic_only_lines") or [])
    if diagnostic_only:
        warnings.append(
            {
                "scope": "bom",
                "candidate_key": bom_key,
                "reason": "diagnostic_only_lines_excluded",
                "count": len(diagnostic_only),
            }
        )

    lines = [_as_dict(item) for item in _get(candidate, "lines", []) or []]
    for line in lines:
        line_blockers, diagnostics = _validate_bom_line(
            env,
            line,
            product_result_by_key,
            company_id=company_id,
            bom_candidate_key=bom_key,
        )
        blockers.extend(line_blockers)
        line_diagnostics.extend(diagnostics)

    existing_bom, stale_bom = _find_existing_bom(env, candidate, product_result, company_id=company_id)
    if _existing_bom_is_incomplete_for_candidate(existing_bom, candidate):
        stale_bom = existing_bom
        existing_bom = None
    exact_snapshot = _record_snapshot(existing_bom)
    stale_snapshot = _record_snapshot(stale_bom)
    if stale_snapshot:
        warnings.append(
            {
                "scope": "bom",
                "candidate_key": bom_key,
                "reason": "stale_bom_signature_candidate",
                "existing_bom_match": stale_snapshot,
            }
        )

    company_record = existing_bom if _record_exists(existing_bom) else stale_bom
    company_reason = _company_incompatibility(company_record, company_id)
    if company_reason:
        blockers.append(company_reason)

    if blockers:
        action = "blocked_bom"
        reason = ";".join(sorted(set(blockers)))
    elif exact_snapshot:
        action = "reuse_bom"
        reason = "signature_match"
    elif stale_snapshot:
        action = "create_bom"
        reason = "stale_signature_match_create_new_version"
    else:
        action = "create_bom"
        reason = "no_existing_bom_match"

    return {
        "bom_candidate_key": bom_key,
        "product_candidate_key": product_candidate_key,
        "bom_role": _get(candidate, "bom_role"),
        "bom_type": _get(candidate, "bom_type"),
        "existing_bom_match": exact_snapshot,
        "stale_bom_match": stale_snapshot,
        "action": action,
        "reason": reason,
        "blockers": sorted(set(blockers)),
        "warnings": warnings,
        "line_diagnostics": line_diagnostics,
    }


def _validate_plan_warnings(
    plan_warnings: List[Dict[str, Any]],
    blockers: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
) -> None:
    for warning in plan_warnings:
        code = str(_get(warning, "code") or "")
        item = {
            "scope": "plan",
            "code": code,
            "segment_key": _get(warning, "segment_key"),
            "message": _get(warning, "message"),
        }
        if code == "cycle_detected":
            blockers.append({**item, "reason": "cycle_detected"})
        elif code.startswith("disconnected") and (_get(warning, "metadata", {}) or {}).get("required"):
            blockers.append({**item, "reason": "disconnected_required_segment"})
        else:
            warnings.append(item)


def _validate_bom_line(
    env: Any,
    line: Dict[str, Any],
    product_result_by_key: Dict[str, Dict[str, Any]],
    *,
    company_id: Optional[int],
    bom_candidate_key: str,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    blockers: List[str] = []
    diagnostics: List[Dict[str, Any]] = []
    if (_get(line, "metadata", {}) or {}).get("diagnostic_only"):
        blockers.append("diagnostic_only_line_in_create_payload")
        diagnostics.append(_line_diagnostic(line, bom_candidate_key, "diagnostic_only_line_in_create_payload"))
    if _safe_float(_get(line, "qty"), 0.0) <= 0.0:
        blockers.append("line_qty_not_positive")
        diagnostics.append(_line_diagnostic(line, bom_candidate_key, "line_qty_not_positive"))
    if not str(_get(line, "uom") or "").strip():
        blockers.append("line_missing_uom")
        diagnostics.append(_line_diagnostic(line, bom_candidate_key, "line_missing_uom"))

    line_type = str(_get(line, "line_type") or "")
    if line_type == "previous_output":
        product_candidate_key = _get(line, "product_candidate_key")
        product_result = product_result_by_key.get(str(product_candidate_key or ""))
        if not product_result:
            blockers.append("previous_output_missing_sfg_product_candidate")
            diagnostics.append(_line_diagnostic(line, bom_candidate_key, "previous_output_missing_sfg_product_candidate"))
        elif product_result.get("action") == "blocked_product":
            blockers.append("previous_output_sfg_product_blocked")
            diagnostics.append(_line_diagnostic(line, bom_candidate_key, "previous_output_sfg_product_blocked"))
        return blockers, diagnostics

    product, lookup_diagnostics = _resolve_component_product(env, line)
    if product is None:
        blockers.append("component_product_not_found")
        diagnostics.append(
            _line_diagnostic(
                line,
                bom_candidate_key,
                "component_product_not_found",
                lookup_diagnostics=lookup_diagnostics,
            )
        )
        return blockers, diagnostics
    diagnostics.append(
        _line_diagnostic(
            line,
            bom_candidate_key,
            "component_product_resolved_by_live_paper_resolution"
            if _has_live_paper_resolution_match(lookup_diagnostics)
            else "component_product_resolved",
            product=product,
            lookup_diagnostics=lookup_diagnostics,
        )
    )
    company_reason = _company_incompatibility(product, company_id)
    if company_reason:
        blockers.append(company_reason)
        diagnostics.append(
            _line_diagnostic(
                line,
                bom_candidate_key,
                company_reason,
                product=product,
                lookup_diagnostics=lookup_diagnostics,
            )
        )

    Uom = _model(env, "uom.uom")
    uom = _resolve_uom(env, _get(line, "uom"), product=product)
    if Uom is not None and uom is None:
        blockers.append("line_uom_not_found")
        diagnostics.append(
            _line_diagnostic(
                line,
                bom_candidate_key,
                "line_uom_not_found",
                product=product,
                lookup_diagnostics=lookup_diagnostics,
            )
        )
    elif uom is not None and not _uom_compatible(product, uom):
        blockers.append("line_uom_category_incompatible")
        diagnostics.append(
            _line_diagnostic(
                line,
                bom_candidate_key,
                "line_uom_category_incompatible",
                product=product,
                lookup_diagnostics=lookup_diagnostics,
            )
        )
    return blockers, diagnostics


def _has_live_paper_resolution_match(lookup_diagnostics: List[Dict[str, Any]]) -> bool:
    for item in lookup_diagnostics or []:
        if item.get("source") != "live_paper_resolution":
            continue
        if item.get("skipped") or item.get("error"):
            continue
        if item.get("exists"):
            return True
    return False


def _find_existing_bom(
    env: Any,
    candidate: Dict[str, Any],
    product_result: Optional[Dict[str, Any]],
    *,
    company_id: Optional[int],
) -> Tuple[Any, Any]:
    Bom = _model(env, "mrp.bom")
    if Bom is None:
        return None, None
    exact_domain = _bom_domain(candidate, product_result, company_id=company_id, exact_signature=True)
    exact = _search_first(Bom, exact_domain) if exact_domain else None
    stale_domain = _bom_domain(candidate, product_result, company_id=company_id, exact_signature=False)
    stale = _search_first(Bom, stale_domain) if stale_domain else None
    if exact is not None and stale is not None and _record_id(exact) == _record_id(stale):
        stale = None
    return exact, stale


def _bom_domain(
    candidate: Dict[str, Any],
    product_result: Optional[Dict[str, Any]],
    *,
    company_id: Optional[int],
    exact_signature: bool,
) -> List[Any]:
    domain: List[Any] = []
    product_match = (product_result or {}).get("existing_product_match") or {}
    product_tmpl_id = product_match.get("id")
    if product_tmpl_id:
        domain.append(("product_tmpl_id", "=", product_tmpl_id))
    corr_sfg_key = (product_result or {}).get("corr_sfg_key")
    if corr_sfg_key:
        domain.append(("corr_sfg_key", "=", corr_sfg_key))
    if not domain:
        return []
    if company_id:
        domain.append(("company_id", "in", [False, company_id]))
    if exact_signature:
        domain.append(("corr_engine_v2_signature", "=", _bom_signature(candidate)))
    return domain


def _bom_signature(candidate: Dict[str, Any]) -> str:
    metadata = _get(candidate, "metadata", {}) or {}
    payload = {
        "strategy_type": metadata.get("strategy_type"),
        "shared_upstream_model": metadata.get("shared_upstream_model"),
        "segment_key": _get(candidate, "segment_key"),
        "corr_plan_signature": _get(candidate, "corr_plan_signature"),
        "corr_material_signature": _get(candidate, "corr_material_signature"),
        "corr_route_signature": _get(candidate, "corr_route_signature"),
        "corr_layout_signature": _get(candidate, "corr_layout_signature"),
        "corr_resolution_signature": _get(candidate, "corr_resolution_signature"),
        "lines": [
            {
                "line_type": _get(line, "line_type"),
                "role_code": _get(line, "role_code"),
                "product_candidate_key": _get(line, "product_candidate_key"),
                "source_segment_key": _get(line, "source_segment_key"),
                "qty": _get(line, "qty"),
                "uom": _get(line, "uom"),
                "operation_segment_key": _get(line, "operation_segment_key"),
                "product_id": (_get(line, "metadata", {}) or {}).get("product_id"),
                "writer_product_key": (_get(line, "metadata", {}) or {}).get("writer_product_key"),
                "product_tmpl_id": (_get(line, "metadata", {}) or {}).get("product_tmpl_id"),
            }
            for line in list(_get(candidate, "lines", []) or [])
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _resolve_component_product(env: Any, line: Dict[str, Any]) -> Tuple[Any, List[Dict[str, Any]]]:
    metadata = _get(line, "metadata", {}) or {}
    Product = _model(env, "product.product")
    ProductTemplate = _model(env, "product.template")
    diagnostics: List[Dict[str, Any]] = []

    def browse_product(model: Any, model_name: str, record_id: int) -> Any:
        record = _browse_record(model, record_id)
        diagnostics.append(_lookup_diagnostic(model, model_name, record_id, record))
        return record if _record_exists(record) else None

    def variant_from_template(template_id: int) -> Any:
        tmpl = browse_product(ProductTemplate, "product.template", template_id)
        if tmpl is None:
            return None
        variant = _first_record(_get(tmpl, "product_variant_id"))
        diagnostics.append(
            _lookup_diagnostic(
                Product,
                "product.product",
                _record_id(variant),
                variant,
                source="template_default_variant",
            )
        )
        return variant if _record_exists(variant) else None

    if metadata.get("force_live_paper_resolution"):
        product = _resolve_live_paper_variant(env, line, diagnostics)
        if product is not None:
            return product, diagnostics
        return None, diagnostics

    product_id = _safe_int(metadata.get("product_id"), 0)
    if product_id:
        product = browse_product(Product, "product.product", product_id)
        if product is not None:
            return product, diagnostics
        product = variant_from_template(product_id)
        if product is not None:
            return product, diagnostics
    writer_product_key = str(metadata.get("writer_product_key") or "")
    product_model, product_id = _parse_writer_product_key(writer_product_key)
    if product_model == "product.product" and product_id:
        product = browse_product(Product, "product.product", product_id)
        if product is not None:
            return product, diagnostics
        product = variant_from_template(product_id)
        if product is not None:
            return product, diagnostics
    if product_model == "product.template" and product_id:
        product = variant_from_template(product_id)
        if product is not None:
            return product, diagnostics
    product = _resolve_live_paper_variant(env, line, diagnostics)
    if product is not None:
        return product, diagnostics
    product = _resolve_consumable_product_map(env, line, diagnostics)
    if product is not None:
        return product, diagnostics
    product_tmpl_id = _safe_int(metadata.get("product_tmpl_id"), 0)
    if product_tmpl_id:
        product = variant_from_template(product_tmpl_id)
        if product is not None:
            return product, diagnostics
    return None, diagnostics


def _resolve_consumable_product_map(
    env: Any,
    line: Dict[str, Any],
    diagnostics: List[Dict[str, Any]],
) -> Any:
    metadata = _get(line, "metadata", {}) or {}
    role = str(metadata.get("semantic_role") or _get(line, "role_code") or "").strip().lower()
    writer_key = str(metadata.get("writer_product_key") or _get(line, "product_candidate_key") or "").strip().lower()
    base_role = _consumable_base_role(role or writer_key)
    if not base_role:
        if (
            str(_get(line, "line_type") or "") == "consumable_input"
            or str(metadata.get("category") or "").strip().lower() == "consumable"
        ):
            diagnostics.append(
                _consumable_resolution_diagnostic(
                    skipped_reason="unsupported_role",
                    role=role,
                    writer_product_key=writer_key,
                )
            )
        return None

    Map, map_diagnostic = _material_product_map_model(env)
    if Map is None:
        diagnostics.append(
            _consumable_resolution_diagnostic(
                skipped_reason="resolver_model_unavailable",
                role=role,
                writer_product_key=writer_key,
                base_role=base_role,
                extra=map_diagnostic,
            )
        )
        return None

    engine_code = _consumable_engine_code(base_role, role=role, writer_product_key=writer_key, metadata=metadata, env=env)
    try:
        mapping = Map.find_match(base_role, engine_code=engine_code)
    except Exception as exc:
        diagnostics.append(
            _consumable_resolution_diagnostic(
                skipped_reason="resolver_error",
                role=role,
                writer_product_key=writer_key,
                base_role=base_role,
                engine_code=engine_code,
                extra={"error": "%s: %s" % (exc.__class__.__name__, str(exc))},
            )
        )
        return None

    product = _get(mapping, "product_id")
    diagnostics.append(
        _consumable_resolution_diagnostic(
            skipped_reason="" if _record_exists(product) else _missing_consumable_message(base_role, engine_code),
            role=role,
            writer_product_key=writer_key,
            base_role=base_role,
            engine_code=engine_code,
            product_id=_record_id(product),
            exists=_record_exists(product),
        )
    )
    return product if _record_exists(product) else None


def _consumable_base_role(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text.startswith("rm_"):
        text = text[3:]
    if text.startswith("adhesive") or text in {"corrugation_adhesive", "lamination_adhesive", "jointing_glue"}:
        return "adhesive"
    if text.startswith("ink") or "printing_ink" in text:
        return "ink"
    if text.startswith("wire") or "stitching_wire" in text:
        return "wire"
    if text.startswith("tape") or "jointing_tape" in text:
        return "tape"
    if text.startswith("lamination") or "film" in text:
        return "lamination"
    return ""


def _consumable_engine_code(
    base_role: str,
    *,
    role: str,
    writer_product_key: str,
    metadata: Dict[str, Any],
    env: Any,
) -> Optional[str]:
    token = role or writer_product_key
    if token.startswith("rm_adhesive_"):
        token = token.replace("rm_adhesive_", "", 1)
    if token.startswith("adhesive_"):
        token = token.replace("adhesive_", "", 1)
    if base_role == "adhesive":
        if token in {"corrugation", "corrugation_adhesive"}:
            return "corrugation"
        if token in {"board", "lamination", "lamination_adhesive"}:
            return "board"
        if token in {"fg", "seaming", "jointing", "jointing_glue"}:
            return "fg"
        return _normalize_subtype_code(metadata.get("kind"))
    if base_role == "ink":
        return _normalize_subtype_code(metadata.get("print_process"))
    if base_role == "wire":
        return _normalize_subtype_code(metadata.get("wire_grade"))
    if base_role == "lamination":
        lam_code = metadata.get("lamination_type")
        if not lam_code:
            lam_id = _safe_int(metadata.get("lamination_type_id"), 0)
            if lam_id:
                try:
                    LamType = _active_test_false_model(_sudo_model(env["corr.lamination.type"]))
                    lam = _browse_record(LamType, lam_id)
                    if lam and _record_exists(lam):
                        lam_code = _get(lam, "code")
                except Exception:
                    lam_code = None
        return _normalize_subtype_code(lam_code)
    return _normalize_subtype_code(metadata.get("kind"))


def _missing_consumable_message(base_role: str, engine_code: Optional[str]) -> str:
    labels = {
        ("adhesive", "corrugation"): "Consumable product not configured for Corrugation Adhesive",
        ("adhesive", "board"): "Consumable product not configured for Board/Lamination Adhesive",
        ("adhesive", "fg"): "Consumable product not configured for FG/Jointing Adhesive",
        ("ink", None): "Consumable product not configured for Printing Ink",
        ("wire", None): "Consumable product not configured for Stitching Wire",
        ("tape", None): "Consumable product not configured for Jointing Tape",
        ("lamination", None): "Consumable product not configured for Lamination Film",
    }
    return labels.get((base_role, engine_code)) or labels.get((base_role, None)) or "Consumable product not configured"


def _normalize_subtype_code(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return text.replace(" ", "_").replace("-", "_")


def _material_product_map_model(env: Any) -> Tuple[Any, Dict[str, Any]]:
    Map, diagnostic = _live_paper_map_model(env)
    if Map is not None:
        diagnostic["finder_method_visible"] = callable(getattr(Map, "find_match", None))
        if callable(getattr(Map, "find_match", None)):
            return Map, diagnostic
    return None, diagnostic


def _consumable_resolution_diagnostic(
    *,
    skipped_reason: str,
    role: Optional[str] = None,
    writer_product_key: Optional[str] = None,
    base_role: Optional[str] = None,
    engine_code: Optional[str] = None,
    product_id: Optional[int] = None,
    exists: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = {
        "source": "consumable_product_map",
        "searched_model": "corr.material.product.map",
        "searched_id": None,
        "exists": bool(exists),
        "skipped": bool(skipped_reason),
        "skipped_reason": skipped_reason or "",
        "role": role,
        "writer_product_key": writer_product_key,
        "base_role": base_role,
        "engine_code": engine_code,
        "product_id": product_id,
    }
    if extra:
        result.update(extra)
    return result


def _resolve_live_paper_variant(
    env: Any,
    line: Dict[str, Any],
    diagnostics: List[Dict[str, Any]],
) -> Any:
    metadata = _get(line, "metadata", {}) or {}
    role = str(metadata.get("semantic_role") or _get(line, "role_code") or "").strip().lower()
    if role == "top_roll":
        role = "liner"
    if role not in ("liner", "medium"):
        diagnostics.append(
            _live_paper_resolution_diagnostic(
                skipped_reason="unsupported_role",
                role=role,
            )
        )
        return None
    deckle_cm = _safe_float(metadata.get("required_deckle_cm"), 0.0)
    if deckle_cm <= 0.0:
        diagnostics.append(
            _live_paper_resolution_diagnostic(
                skipped_reason="missing_required_deckle_cm",
                role=role,
                required_deckle_cm=deckle_cm,
            )
        )
        return None
    preview_metadata = dict(metadata.get("preview_metadata") or {})
    resolution = dict(preview_metadata.get("resolution") or metadata.get("resolution") or {})
    gsm = resolution.get("gsm") or preview_metadata.get("gsm") or metadata.get("gsm")
    bf = resolution.get("bf") or preview_metadata.get("bf") or metadata.get("bf")
    color = resolution.get("color") or preview_metadata.get("color") or metadata.get("color")
    material_template_id = _safe_int(
        resolution.get("material_template_id")
        or preview_metadata.get("material_template_id")
        or metadata.get("material_template_id"),
        0,
    )
    Map, map_diagnostic = _live_paper_map_model(env)
    if Map is None:
        diagnostics.append(
            _live_paper_resolution_diagnostic(
                skipped_reason="resolver_model_unavailable",
                role=role,
                gsm=gsm,
                bf=bf,
                color=color,
                required_deckle_cm=deckle_cm,
                extra=map_diagnostic,
            )
        )
        return None
    resolver = getattr(Map, "resolve_engine_paper_variant_v1", None)
    if not callable(resolver):
        diagnostics.append(
            _live_paper_resolution_diagnostic(
                skipped_reason="resolver_method_unavailable",
                role=role,
                gsm=gsm,
                bf=bf,
                color=color,
                required_deckle_cm=deckle_cm,
                extra=map_diagnostic,
            )
        )
        return None
    try:
        result = resolver(
            role=role,
            gsm=gsm,
            bf=bf,
            color=color,
            deckle_cm=deckle_cm,
            material_template_id=material_template_id,
            exact_resolution_only=True,
        )
    except Exception as exc:
        diagnostics.append(
            {
                "source": "live_paper_resolution",
                "searched_model": "corr.material.product.map",
                "searched_id": None,
                "exists": False,
                "error": str(exc),
                "role": role,
                "gsm": gsm,
                "bf": bf,
                "color": color,
                "required_deckle_cm": deckle_cm,
            }
        )
        return None
    product = _get(result or {}, "product")
    if material_template_id and _record_exists(product):
        product_tmpl = _get(product, "product_tmpl_id")
        if _record_id(product_tmpl) != material_template_id:
            product = None
    diagnostics.append(
        {
            "source": "live_paper_resolution",
            "searched_model": "corr.material.product.map",
            "searched_id": None,
            "exists": _record_exists(product),
            "skipped": False,
            "status": _get(result or {}, "status"),
            "product_id": _record_id(product),
            "candidate_ids": list(_get(result or {}, "candidate_ids", []) or []),
            "role": role,
            "gsm": gsm,
            "bf": bf,
            "color": color,
            "material_template_id": material_template_id,
            "required_deckle_cm": deckle_cm,
        }
    )
    if _record_exists(product):
        return product
    return _resolve_paper_variant_by_product_attributes(
        env,
        role=role,
        gsm=gsm,
        bf=bf,
        color=color,
        flute=metadata.get("flute") or metadata.get("flute_code") or resolution.get("flute") or resolution.get("flute_code"),
        deckle_cm=deckle_cm,
        material_template_id=material_template_id,
        diagnostics=diagnostics,
    )


def _resolve_paper_variant_by_product_attributes(
    env: Any,
    *,
    role: str,
    gsm: Any,
    bf: Any,
    color: Any,
    flute: Any,
    deckle_cm: Any,
    diagnostics: List[Dict[str, Any]],
    material_template_id: Any = None,
) -> Any:
    try:
        Product = env["product.product"].sudo().with_context(active_test=False)
    except Exception as exc:
        diagnostics.append(
            {
                "source": "paper_variant_attribute_fallback",
                "exists": False,
                "skipped": True,
                "skipped_reason": "product_model_unavailable",
                "error": str(exc),
            }
        )
        return None
    try:
        domain = []
        material_template_id = _safe_int(material_template_id, 0)
        if material_template_id:
            domain.append(("product_tmpl_id", "=", material_template_id))
        candidates = Product.search(domain)
    except Exception as exc:
        diagnostics.append(
            {
                "source": "paper_variant_attribute_fallback",
                "exists": False,
                "skipped": True,
                "skipped_reason": "product_search_failed",
                "error": str(exc),
            }
        )
        return None

    role_norm = str(role or "").strip().lower()
    gsm_val = _safe_float(gsm, 0.0)
    bf_val = _safe_float(bf, 0.0)
    deckle_val = _safe_float(deckle_cm, 0.0)
    color_norm = _normalize_color_token(color)
    flute_norm = _normalize_token(flute)
    accepted: List[Tuple[Tuple[Any, ...], Any]] = []
    candidate_debug: List[Dict[str, Any]] = []
    for product in _iter_records(candidates):
        attrs = _product_attribute_map(product)
        product_gsm = _first_float_attr(attrs, ["gsm"])
        product_bf = _first_float_attr(attrs, ["bf", "burst factor", "burst factor bf"])
        product_deckle = _first_float_attr(attrs, ["deckle (cm)", "deckle", "deckle(cm)", "reel width (cm)", "reel width"])
        product_color = _normalize_color_token(attrs.get("color"))
        product_flute = _normalize_token(attrs.get("flute"))
        default_code = str(_get(product, "default_code") or "")
        row = {
            "product_id": _record_id(product),
            "default_code": default_code,
            "gsm": product_gsm,
            "bf": product_bf,
            "deckle_cm": product_deckle,
            "color": attrs.get("color"),
            "flute": attrs.get("flute"),
            "accepted": False,
            "rejection_reason": "",
        }
        if role_norm == "liner" and product_flute:
            row["rejection_reason"] = "liner_candidate_has_flute"
            candidate_debug.append(row)
            continue
        if role_norm == "medium" and flute_norm and product_flute and product_flute != flute_norm:
            row["rejection_reason"] = "medium_flute_mismatch"
            candidate_debug.append(row)
            continue
        if gsm_val > 0.0 and (product_gsm is None or abs(product_gsm - gsm_val) > 0.001):
            row["rejection_reason"] = "gsm_mismatch"
            candidate_debug.append(row)
            continue
        if bf_val > 0.0 and (product_bf is None or abs(product_bf - bf_val) > 0.001):
            row["rejection_reason"] = "bf_mismatch"
            candidate_debug.append(row)
            continue
        if deckle_val > 0.0 and (product_deckle is None or abs(product_deckle - deckle_val) > 0.5):
            row["rejection_reason"] = "deckle_mismatch"
            candidate_debug.append(row)
            continue
        color_exact = product_color and color_norm and product_color == color_norm
        row["accepted"] = True
        candidate_debug.append(row)
        accepted.append(
            (
                (
                    0 if color_exact else 1,
                    abs((product_deckle or 0.0) - deckle_val),
                    abs((product_gsm or 0.0) - gsm_val),
                    abs((product_bf or 0.0) - bf_val),
                    _record_id(product) or 0,
                ),
                product,
            )
        )
    accepted.sort(key=lambda item: item[0])
    product = accepted[0][1] if accepted else None
    diagnostics.append(
        {
            "source": "paper_variant_attribute_fallback",
            "exists": _record_exists(product),
            "skipped": False,
            "product_id": _record_id(product),
            "candidate_ids": [_record_id(item[1]) for item in accepted],
            "role": role,
            "gsm": gsm,
            "bf": bf,
            "color": color,
            "flute": flute,
            "material_template_id": material_template_id,
            "required_deckle_cm": deckle_cm,
            "candidate_count": len(candidate_debug),
            "accepted_count": len(accepted),
            "candidates": candidate_debug[:50],
        }
    )
    return product if _record_exists(product) else None


def _iter_records(records: Any) -> List[Any]:
    if not records:
        return []
    try:
        return list(records)
    except Exception:
        return [records]


def _product_attribute_map(product: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    values = _get(product, "product_template_attribute_value_ids")
    for value in _iter_records(values):
        attribute = _get(value, "attribute_id")
        name = str(_get(attribute, "name") or "").strip().lower()
        if not name:
            continue
        result[name] = _get(value, "name")
    return result


def _first_float_attr(attrs: Dict[str, Any], names: List[str]) -> Optional[float]:
    for name in names:
        value = attrs.get(str(name or "").strip().lower())
        parsed = _parse_float(value)
        if parsed is not None:
            return parsed
    return None


def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        pass
    try:
        import re

        match = re.search(r"[-+]?\d*\.?\d+", str(value))
        if match:
            return float(match.group(0))
    except Exception:
        pass
    return None


def _normalize_color_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    try:
        import re

        text = re.sub(r"\(.*?\)", "", text)
    except Exception:
        pass
    return " ".join(text.split())


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def _live_paper_resolution_diagnostic(
    *,
    skipped_reason: str,
    role: Optional[str] = None,
    gsm: Any = None,
    bf: Any = None,
    color: Any = None,
    required_deckle_cm: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = {
        "source": "live_paper_resolution",
        "searched_model": "corr.material.product.map",
        "searched_id": None,
        "exists": False,
        "skipped": True,
        "skipped_reason": skipped_reason,
        "role": role,
        "gsm": gsm,
        "bf": bf,
        "color": color,
        "required_deckle_cm": required_deckle_cm,
    }
    if extra:
        result.update(extra)
    return result


def _live_paper_map_model(env: Any) -> Tuple[Any, Dict[str, Any]]:
    diagnostic: Dict[str, Any] = {
        "env_available": bool(env),
        "model_lookup": "not_attempted",
        "sudo_applied": False,
        "active_test_false_applied": False,
    }
    if env is None:
        diagnostic["model_lookup"] = "missing_env"
        return None, diagnostic
    try:
        model = env["corr.material.product.map"]
        diagnostic["model_lookup"] = "ok"
    except Exception as exc:
        diagnostic["model_lookup"] = "failed"
        diagnostic["model_lookup_error"] = "%s: %s" % (exc.__class__.__name__, str(exc))
        try:
            registry = getattr(env, "registry", None)
            diagnostic["registry_has_model"] = bool(
                registry and "corr.material.product.map" in registry
            )
        except Exception:
            pass
        return None, diagnostic
    try:
        model = model.sudo()
        diagnostic["sudo_applied"] = True
    except Exception as exc:
        diagnostic["sudo_error"] = "%s: %s" % (exc.__class__.__name__, str(exc))
    try:
        model = model.with_context(active_test=False)
        diagnostic["active_test_false_applied"] = True
    except Exception as exc:
        diagnostic["active_test_false_error"] = "%s: %s" % (exc.__class__.__name__, str(exc))
    diagnostic["resolver_method_visible"] = callable(
        getattr(model, "resolve_engine_paper_variant_v1", None)
    )
    return model, diagnostic


def _line_diagnostic(
    line: Dict[str, Any],
    bom_candidate_key: str,
    reason: str,
    *,
    product: Any = None,
    lookup_diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    metadata = _get(line, "metadata", {}) or {}
    return {
        "bom_candidate_key": bom_candidate_key,
        "line_key": _get(line, "line_key"),
        "candidate_key": _get(line, "input_key") or _get(line, "product_candidate_key"),
        "line_type": _get(line, "line_type"),
        "role": metadata.get("semantic_role") or _get(line, "role_code"),
        "qty": _get(line, "qty"),
        "uom": _get(line, "uom"),
        "product_id": metadata.get("product_id") or _record_id(product),
        "product_name": metadata.get("product_name") or metadata.get("product_label") or _get(product, "display_name") or _get(product, "name"),
        "product_tmpl_id": metadata.get("product_tmpl_id"),
        "writer_product_key": metadata.get("writer_product_key"),
        "source_kind": metadata.get("source_kind") or metadata.get("source") or metadata.get("resolution_source"),
        "required_deckle_cm": metadata.get("required_deckle_cm"),
        "selected_layout": dict(metadata.get("selected_layout") or {}),
        "product_lookup": list(lookup_diagnostics or []),
        "reason": reason,
    }


def _lookup_diagnostic(
    model: Any,
    model_name: str,
    record_id: Optional[int],
    record: Any,
    *,
    source: str = "direct",
) -> Dict[str, Any]:
    uom = _get(record, "uom_id") or _get(_get(record, "product_tmpl_id"), "uom_id")
    category = _get(uom, "category_id")
    sudo_model = _sudo_model(model)
    active_test_false_model = _active_test_false_model(sudo_model)
    sudo_record = _browse_record(sudo_model, record_id)
    active_test_false_record = _browse_record(active_test_false_model, record_id)
    search_ids = _search_ids(model, record_id)
    active_test_false_search_ids = _search_ids(active_test_false_model, record_id)
    sql_snapshot = _sql_record_snapshot(model, model_name, record_id)
    exists = _record_exists(record)
    return {
        "source": source,
        "db_name": _model_dbname(model),
        "searched_model": model_name,
        "searched_id": record_id,
        "exists": exists,
        "browse_exists": exists,
        "browse_record_id": _record_id(record),
        "browse_ids": _record_ids(record),
        "search_ids": search_ids,
        "search_count": len(search_ids),
        "sudo_browse_exists": _record_exists(sudo_record),
        "sudo_browse_record_id": _record_id(sudo_record),
        "sudo_browse_ids": _record_ids(sudo_record),
        "active_test_false_exists": _record_exists(active_test_false_record),
        "active_test_false_record_id": _record_id(active_test_false_record),
        "active_test_false_ids": _record_ids(active_test_false_record),
        "active_test_false_search_ids": active_test_false_search_ids,
        "active_test_false_search_count": len(active_test_false_search_ids),
        "env": _orm_env_snapshot(model),
        "sql": sql_snapshot,
        "diagnostic_mismatch": "sql_found_but_orm_exists_false"
        if sql_snapshot.get("found") and not exists
        else "",
        "active": _record_active(record),
        "company_id": _record_id(_get(record, "company_id")),
        "uom_id": _record_id(uom),
        "uom_category": _get(category, "display_name") or _get(category, "name") or _record_id(category),
    }


def _first_layout_signature(boms: List[Dict[str, Any]]) -> str:
    for bom in boms or []:
        value = _get(bom, "corr_layout_signature")
        if value:
            return str(value)
    return ""


def _resolve_uom(env: Any, name: Any, *, product: Any = None) -> Any:
    Uom = _model(env, "uom.uom")
    text = str(name or "").strip()
    if Uom is None or not text:
        return None
    exact = _search_first(Uom, [("name", "=", text)])
    if exact is not None:
        return exact
    for alias in _uom_name_aliases(text):
        alias_match = _search_first(Uom, [("name", "=", alias)])
        if alias_match is not None:
            return alias_match
    product_uom = _get(product, "uom_id") or _get(_get(product, "product_tmpl_id"), "uom_id")
    if _is_unit_uom_alias(text) and _uom_category_name(product_uom) == "unit":
        return product_uom
    return None


def _uom_name_aliases(name: str) -> List[str]:
    normalized = _normalize_uom_name(name)
    aliases = {
        "unit": ["Units", "Unit", "unit", "units"],
        "pcs": ["Units", "Unit", "pcs", "Pcs", "Pieces", "Piece"],
        "piece": ["Units", "Unit", "Pieces", "Piece", "pcs"],
        "kg": ["kg", "KG", "Kilogram", "Kilograms"],
        "m2": ["m²", "m2", "M2", "Square Meter", "Square Meters", "sqm", "SQM"],
    }
    return [alias for alias in aliases.get(normalized, []) if alias != name]


def _is_unit_uom_alias(name: str) -> bool:
    return _normalize_uom_name(name) in {"unit", "pcs", "piece"}


def _normalize_uom_name(name: Any) -> str:
    text = str(name or "").strip().lower().replace(".", "")
    if text in {"units", "unit(s)", "unit"}:
        return "unit"
    if text in {"pc", "pcs", "piece", "pieces", "nos", "no", "each", "ea"}:
        return "pcs"
    if text in {"kilogram", "kilograms", "kgs", "kg"}:
        return "kg"
    if text in {"m2", "m²", "sqm", "sq m", "sq meter", "sq meters", "square meter", "square meters"}:
        return "m2"
    return text


def _uom_category_name(uom: Any) -> str:
    category = _get(uom, "category_id")
    return _normalize_uom_name(_get(category, "display_name") or _get(category, "name") or "")


def _uom_compatible(product: Any, uom: Any) -> bool:
    product_uom = _get(product, "uom_id") or _get(_get(product, "product_tmpl_id"), "uom_id")
    product_category = _record_id(_get(product_uom, "category_id"))
    line_category = _record_id(_get(uom, "category_id"))
    if not product_category or not line_category:
        return True
    return product_category == line_category


def _company_incompatibility(record: Any, company_id: Optional[int]) -> Optional[str]:
    if not _record_exists(record) or not company_id:
        return None
    record_company_id = _record_id(_get(record, "company_id"))
    if record_company_id and record_company_id != company_id:
        return "company_incompatible"
    return None


def _model(env: Any, model_name: str) -> Any:
    if env is None:
        return None
    try:
        return _active_test_false_model(_sudo_model(env[model_name]))
    except Exception:
        return None


def _search_first(model: Any, domain: List[Any]) -> Any:
    if model is None:
        return None
    try:
        return _first_record(model.search(domain, limit=1))
    except Exception:
        return None


def _browse_record(model: Any, record_id: Optional[int]) -> Any:
    if model is None or not record_id:
        return None
    try:
        return _first_record(model.browse(record_id))
    except Exception:
        return None


def _sudo_model(model: Any) -> Any:
    if model is None:
        return model
    try:
        return model.sudo()
    except Exception:
        return model


def _active_test_false_model(model: Any) -> Any:
    if model is None:
        return model
    try:
        return model.with_context(active_test=False)
    except Exception:
        return model


def _search_ids(model: Any, record_id: Optional[int]) -> List[int]:
    if model is None or not record_id:
        return []
    try:
        found = model.search([("id", "=", int(record_id))])
        ids = getattr(found, "ids", None)
        if ids is not None:
            return list(ids)
        return [_record_id(item) for item in list(found or []) if _record_id(item)]
    except Exception:
        return []


def _record_ids(record: Any) -> List[int]:
    if not record:
        return []
    ids = getattr(record, "ids", None)
    if ids is not None:
        try:
            return [int(item) for item in list(ids or []) if item]
        except Exception:
            return []
    record_id = _record_id(record)
    return [record_id] if record_id else []


def _orm_env_snapshot(model: Any) -> Dict[str, Any]:
    env = getattr(model, "env", None)
    context = getattr(env, "context", None) or {}
    company = getattr(env, "company", None)
    companies = getattr(env, "companies", None)
    registry = getattr(env, "registry", None)
    cursor = getattr(env, "cr", None)
    return {
        "uid": getattr(env, "uid", None),
        "user_id": _record_id(getattr(env, "user", None)),
        "company_id": _record_id(company),
        "company_ids": _record_ids(companies),
        "allowed_company_ids": list(context.get("allowed_company_ids") or []),
        "active_test": context.get("active_test"),
        "context_keys": sorted([str(key) for key in context.keys()]),
        "registry_id": id(registry) if registry is not None else None,
        "cursor_id": id(cursor) if cursor is not None else None,
        "cursor_dbname": getattr(cursor, "dbname", None),
        "model_name": getattr(model, "_name", None),
    }


def _sql_record_snapshot(model: Any, model_name: str, record_id: Optional[int]) -> Dict[str, Any]:
    if model is None or not record_id:
        return {"checked": False, "reason": "missing_model_or_id"}
    env = getattr(model, "env", None)
    cr = getattr(env, "cr", None)
    if not cr:
        return {"checked": False, "reason": "missing_cursor"}
    if model_name == "product.product":
        sql = """
            SELECT pp.id, pp.product_tmpl_id, pt.active, pt.company_id, pp.default_code, pt.name
              FROM product_product pp
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE pp.id = %s
             LIMIT 1
        """
    elif model_name == "product.template":
        sql = """
            SELECT pt.id, pt.id AS product_tmpl_id, pt.active, pt.company_id, pt.default_code, pt.name
              FROM product_template pt
             WHERE pt.id = %s
             LIMIT 1
        """
    else:
        return {"checked": False, "reason": "unsupported_model"}
    try:
        cr.execute(sql, (int(record_id),))
        row = cr.dictfetchone()
    except Exception as exc:
        return {
            "checked": True,
            "error": "%s: %s" % (exc.__class__.__name__, str(exc)),
            "cursor_dbname": getattr(cr, "dbname", None),
            "cursor_id": id(cr),
        }
    return {
        "checked": True,
        "found": bool(row),
        "row": dict(row or {}),
        "cursor_dbname": getattr(cr, "dbname", None),
        "cursor_id": id(cr),
    }


def _model_dbname(model: Any) -> Optional[str]:
    try:
        return getattr(getattr(model, "env", None).cr, "dbname", None)
    except Exception:
        return None


def _first_record(recordset: Any) -> Any:
    if not recordset:
        return None
    try:
        if len(recordset) == 0:
            return None
    except Exception:
        pass
    try:
        return recordset[0]
    except Exception:
        return recordset


def _record_snapshot(record: Any) -> Optional[Dict[str, Any]]:
    if not record:
        return None
    return {
        "id": _record_id(record),
        "display_name": _get(record, "display_name") or _get(record, "name"),
        "company_id": _record_id(_get(record, "company_id")),
        "product_variant_id": _record_id(_get(record, "product_variant_id")),
        "corr_sfg_key": _get(record, "corr_sfg_key"),
        "corr_engine_v2_signature": _get(record, "corr_engine_v2_signature"),
    }


def _existing_bom_is_incomplete_for_candidate(record: Any, candidate: Dict[str, Any]) -> bool:
    if not _record_exists(record):
        return False
    metadata = _get(candidate, "metadata", {}) or {}
    strategy_type = str(metadata.get("strategy_type") or metadata.get("strategy") or "").strip()
    requires_piece_effective_match = (
        strategy_type == "independent_routed_piece"
        and (
            metadata.get("source_kind") == "piece_effective_recipe"
            or metadata.get("manufacturing_source") == "piece_effective_recipe"
            or metadata.get("piece_2ply_set")
            or any(
                str(metadata.get(field_name) or "").strip().lower() not in ("", "inherited")
                for field_name in ("stack_source", "surface_source", "top_sheet_source")
            )
        )
    )
    if strategy_type != "shared_upstream_split" and not requires_piece_effective_match:
        return False
    expected_line_count = len(list(_get(candidate, "lines", []) or []))
    expected_operation_count = len(list(_get(candidate, "operations", []) or []))
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


def _record_exists(record: Any) -> bool:
    if not record:
        return False
    try:
        exists = record.exists()
        return bool(exists)
    except Exception:
        return bool(_record_id(record))


def _record_active(record: Any) -> Optional[bool]:
    if not record:
        return None
    active = _get(record, "active", None)
    if active is None:
        return None
    return bool(active)


def _record_id(record: Any) -> Optional[int]:
    try:
        value = _get(record, "id")
        return int(value) if value else None
    except Exception:
        return None


def _parse_writer_product_key(value: str) -> Tuple[Optional[str], Optional[int]]:
    text = str(value or "").strip()
    if ":" not in text:
        return None, None
    model, raw_id = text.split(":", 1)
    return model, _safe_int(raw_id, 0) or None


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if is_dataclass(value):
        return asdict(value)
    return {}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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



