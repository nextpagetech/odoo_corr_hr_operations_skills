from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional


PHASE_G1_VERSION = "G1-D1.0"
PHASE_G2_VERSION = "G2-D1.2"
PHASE_H1_VERSION = "H1-D1.0"
PHASE_H2_VERSION = "H2-D1.0"
PHASE_H_SHARED_UPSTREAM_MODEL = "split_output_sfg_v4"


def build_phase_g1_materialization_debug_payload(
    preview_payload: Dict[str, Any],
    *,
    mode: str = "stockable",
    paper_resolver: Any = None,
) -> Dict[str, Any]:
    """Build the Phase G1 multi-piece materialization dry-run payload.

    Phase G1 is intentionally planning-only. It produces product and BoM
    candidates that follow the Phase 7B resolver/writer contracts, but it does
    not create products, BoMs, MOs, or piece routes.
    """

    payload = enrich_phase_g1_piece_rm_requirements(
        preview_payload or {},
        paper_resolver=paper_resolver,
    )
    guard = validate_phase_g1_guardrails(payload)
    debug = {
        "enabled": True,
        "dry_run": True,
        "phase": "G1",
        "phase_g1_version": PHASE_G1_VERSION,
        "description": "Terminal piece SFG + piece BoM + FG merge BoM dry-run only.",
        "guard": guard,
        "writes_enabled": False,
        "materialization_enabled": False,
    }
    if not guard.get("passed"):
        debug["materialization_plan"] = {
            "materialization_version": PHASE_G1_VERSION,
            "source_plan_version": "engine_v2_preview",
            "mode": mode or "stockable",
            "corr_plan_signature": _plan_signature(payload),
            "fg_segment_key": "fg_merge",
            "products": [],
            "boms": [],
            "warnings": [
                {
                    "code": "multi_piece_g1_guard_failed",
                    "message": "Phase G1 guardrails failed; materialization plan is blocked.",
                    "metadata": {"blockers": list(guard.get("blockers") or [])},
                }
            ],
            "metadata": {
                "dry_run": True,
                "blocked": True,
                "blockers": list(guard.get("blockers") or []),
            },
        }
        return debug

    debug["materialization_plan"] = build_phase_g1_materialization_plan(payload, mode=mode)
    return debug


def build_phase_g2_routed_piece_materialization_debug_payload(
    preview_payload: Dict[str, Any],
    *,
    mode: str = "stockable",
    paper_resolver: Any = None,
) -> Dict[str, Any]:
    """Build the Phase G2 routed-piece materialization dry-run payload."""

    payload = _payload_with_piece_specific_rm_requirements(preview_payload or {})
    payload = enrich_phase_g1_piece_rm_requirements(
        payload,
        paper_resolver=paper_resolver,
    )
    route_segmentation = segment_phase_g2_route(payload)
    guard = validate_phase_g2_guardrails(payload, route_segmentation=route_segmentation)
    debug = {
        "enabled": True,
        "dry_run": True,
        "phase": "G2",
        "phase_g2_version": PHASE_G2_VERSION,
        "description": "Routed piece stage chain + terminal piece SFG + FG merge BoM dry-run only.",
        "route_segmentation": route_segmentation,
        "guard": guard,
        "writes_enabled": False,
        "materialization_enabled": False,
    }
    if not guard.get("passed"):
        debug["materialization_plan"] = {
            "materialization_version": PHASE_G2_VERSION,
            "source_plan_version": "engine_v2_preview",
            "mode": mode or "stockable",
            "strategy_type": "independent_routed_piece",
            "corr_plan_signature": _plan_signature(payload),
            "fg_segment_key": "fg_merge",
            "products": [],
            "boms": [],
            "warnings": [
                {
                    "code": "multi_piece_g2_guard_failed",
                    "message": "Phase G2 guardrails failed; routed piece materialization plan is blocked.",
                    "metadata": {"blockers": list(guard.get("blockers") or [])},
                }
            ],
            "metadata": {
                "dry_run": True,
                "blocked": True,
                "blockers": list(guard.get("blockers") or []),
            },
        }
        return debug

    debug["materialization_plan"] = build_phase_g2_materialization_plan(
        payload,
        route_segmentation=route_segmentation,
        mode=mode,
    )
    debug["piece_chains"] = _g2_piece_chain_debug(debug["materialization_plan"])
    return debug


def build_phase_h_shared_upstream_split_debug_payload(
    preview_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Build Phase H1 shared-upstream split diagnostics.

    H1 is preview-only. It validates and describes a split-after-stage plan,
    but it does not create product, BoM, or MO candidates.
    """

    payload = dict(preview_payload or {})
    multi_piece = dict(payload.get("multi_piece") or {})
    route_segmentation = segment_phase_h_shared_upstream_route(payload)
    guard = validate_phase_h_shared_upstream_guardrails(
        payload,
        route_segmentation=route_segmentation,
    )
    source_blank = _h_source_blank(multi_piece)
    split_axis = str(multi_piece.get("split_axis") or "").strip().lower()
    pieces = sorted([dict(item or {}) for item in multi_piece.get("pieces") or []], key=_piece_key)
    split_outputs = [_h_split_output(piece, split_axis, source_blank) for piece in pieces]
    allocations = _h_allocation_rows(multi_piece, pieces, split_axis, source_blank)

    return {
        "enabled": True,
        "dry_run": True,
        "phase": "H1",
        "phase_h1_version": PHASE_H1_VERSION,
        "description": "Shared upstream split route segmentation dry-run only. No products, BoMs, or MOs are created.",
        "strategy": "shared_upstream_split",
        "status": "allowed" if guard.get("passed") else "blocked",
        "split_after_stage": route_segmentation.get("split_after_stage_key") or "",
        "route_segmentation": route_segmentation,
        "shared_stages": route_segmentation.get("shared_stages") or [],
        "shared_stage_keys": route_segmentation.get("shared_stage_keys") or [],
        "split_node": route_segmentation.get("split_node") or {},
        "post_split_branch_stage_keys": route_segmentation.get("post_split_branch_stage_keys") or [],
        "terminal_piece_stage": route_segmentation.get("terminal_piece_stage") or {},
        "merge_stage": route_segmentation.get("merge_stage") or {},
        "source_blank": source_blank,
        "source_deckle_cm": _safe_float(source_blank.get("width_mm"), 0.0) / 10.0,
        "split_axis": split_axis,
        "split_outputs": split_outputs,
        "allocation_percentages": allocations,
        "allocation": allocations,
        "post_split_route_per_piece": [
            {
                "piece_key": _piece_key(piece),
                "piece_name": piece.get("piece_name") or _piece_key(piece),
                "stage_keys": route_segmentation.get("post_split_branch_stage_keys") or [],
                "terminal_stage_key": (route_segmentation.get("terminal_piece_stage") or {}).get("stage_key") or "",
            }
            for piece in pieces
        ],
        "guard": guard,
        "blockers": list(guard.get("blockers") or []),
        "warnings": list(guard.get("warnings") or []),
        "diagnostics": {
            "capacity_checks": list(guard.get("capacity_checks") or []),
            "allocation_total": sum(_safe_float(row.get("ratio"), 0.0) for row in allocations),
            "nested_split_detected": bool(guard.get("nested_split_detected")),
        },
        "writes_enabled": False,
        "materialization_enabled": False,
        "products": [],
        "boms": [],
        "mos": [],
    }


def build_phase_h2_shared_upstream_materialization_debug_payload(
    preview_payload: Dict[str, Any],
    *,
    mode: str = "stockable",
    paper_resolver: Any = None,
) -> Dict[str, Any]:
    """Build Phase H2 shared-upstream materialization candidates.

    H2 is still dry-run only. It produces the product/BoM candidate shape that
    the existing Phase 7B resolver understands, but it does not enable writes.
    """

    payload = enrich_phase_g1_piece_rm_requirements(
        preview_payload or {},
        paper_resolver=paper_resolver,
    )
    route_segmentation = segment_phase_h_shared_upstream_route(payload)
    guard = validate_phase_h_shared_upstream_guardrails(
        payload,
        route_segmentation=route_segmentation,
    )
    debug = {
        "enabled": True,
        "dry_run": True,
        "phase": "H2",
        "phase_h2_version": PHASE_H2_VERSION,
        "description": "Shared upstream SFG + post-split branch SFG + terminal piece SFG + FG merge BoM dry-run only.",
        "strategy": "shared_upstream_split",
        "route_segmentation": route_segmentation,
        "guard": guard,
        "writes_enabled": False,
        "materialization_enabled": False,
    }
    if not guard.get("passed"):
        debug["materialization_plan"] = _blocked_h2_materialization_plan(
            payload,
            guard,
            mode=mode,
        )
        debug["candidate_summary"] = build_h2_candidate_summary(debug["materialization_plan"])
        return debug

    debug["materialization_plan"] = build_phase_h2_shared_upstream_materialization_plan(
        payload,
        route_segmentation=route_segmentation,
        mode=mode,
    )
    debug["shared_upstream_flow"] = build_h2_shared_upstream_flow(
        debug["materialization_plan"],
        preview_payload=payload,
    )
    debug["materialization_plan"].setdefault("metadata", {})["shared_upstream_flow"] = debug["shared_upstream_flow"]
    debug["candidate_summary"] = build_h2_candidate_summary(debug["materialization_plan"])
    return debug


def build_phase_h2_shared_upstream_materialization_plan(
    preview_payload: Dict[str, Any],
    *,
    route_segmentation: Optional[Dict[str, Any]] = None,
    mode: str = "stockable",
) -> Dict[str, Any]:
    payload = dict(preview_payload or {})
    multi_piece = dict(payload.get("multi_piece") or {})
    pieces = sorted([dict(item or {}) for item in multi_piece.get("pieces") or []], key=_piece_key)
    route_segmentation = dict(route_segmentation or segment_phase_h_shared_upstream_route(payload))
    shared_stages = [dict(stage or {}) for stage in route_segmentation.get("shared_stages") or []]
    branch_stages = [dict(stage or {}) for stage in route_segmentation.get("post_split_branch_stages") or []]
    board_stage_key = _g2_board_stage_key(branch_stages)
    plan_signature = _plan_signature(payload)
    source_blank = _h_source_blank(multi_piece)
    split_axis = str(multi_piece.get("split_axis") or "").strip().lower()
    allocations = {
        str(row.get("piece_key") or ""): dict(row or {})
        for row in _h_allocation_rows(multi_piece, pieces, split_axis, source_blank)
    }

    products: List[Dict[str, Any]] = []
    boms: List[Dict[str, Any]] = []

    shared_product = _h2_shared_upstream_product_candidate(
        payload,
        multi_piece,
        shared_stages,
        source_blank,
        plan_signature,
    )
    products.append(shared_product)
    boms.append(
        _h2_shared_upstream_bom_candidate(
            payload,
            multi_piece,
            shared_product,
            shared_stages,
            source_blank,
            plan_signature,
        )
    )

    branch_product_by_piece: Dict[str, Dict[str, Dict[str, Any]]] = {}
    split_output_product_by_piece: Dict[str, Dict[str, Any]] = {}
    terminal_product_by_piece: Dict[str, Dict[str, Any]] = {}
    for piece in pieces:
        piece_key = _piece_key(piece)
        branch_product_by_piece[piece_key] = {}
        allocation = _safe_float((allocations.get(piece_key) or {}).get("ratio"), 0.0) or 1.0
        split_output_product = _h2_split_output_product_candidate(
            payload,
            multi_piece,
            piece,
            shared_product,
            allocation,
            plan_signature,
        )
        products.append(split_output_product)
        split_output_product_by_piece[piece_key] = split_output_product
        boms.append(
            _h2_split_output_bom_candidate(
                payload,
                multi_piece,
                piece,
                split_output_product,
                shared_product,
                allocation,
                plan_signature,
            )
        )
        previous_product = split_output_product
        previous_qty = 1.0
        for index, stage in enumerate(branch_stages):
            product = _h2_branch_stage_product_candidate(
                piece,
                stage,
                plan_signature,
                payload,
            )
            products.append(product)
            branch_product_by_piece[piece_key][str(stage.get("stage_key") or "")] = product
            boms.append(
                _h2_branch_stage_bom_candidate(
                    payload,
                    piece,
                    stage,
                    product,
                    previous_product,
                    previous_qty=previous_qty,
                    is_first_stage=(index == 0),
                    board_stage_key=board_stage_key,
                    plan_signature=plan_signature,
                )
            )
            previous_product = product
            previous_qty = 1.0

        terminal_product = _h2_piece_terminal_product_candidate(
            piece,
            route_segmentation,
            plan_signature,
            payload,
        )
        products.append(terminal_product)
        terminal_product_by_piece[piece_key] = terminal_product
        boms.append(
            _h2_piece_terminal_bom_candidate(
                piece,
                terminal_product,
                previous_product,
                route_segmentation,
                plan_signature,
            )
        )

    fg_product = _g2_fg_product_candidate(multi_piece, pieces, source_blank, plan_signature)
    fg_product["metadata"] = dict(
        fg_product.get("metadata") or {},
        phase="H2",
        strategy_type="shared_upstream_split",
    )
    products.append(fg_product)
    merge_bom = _fg_merge_bom_candidate(
        multi_piece,
        pieces,
        fg_product,
        terminal_product_by_piece,
        plan_signature,
    )
    _h2_mark_bom(merge_bom, strategy_note="fg_merge_after_shared_upstream_split")
    for line in merge_bom.get("lines") or []:
        line["metadata"] = dict(line.get("metadata") or {}, phase="H2", strategy_type="shared_upstream_split")
    for operation in merge_bom.get("operations") or []:
        operation["metadata"] = dict(operation.get("metadata") or {}, phase="H2", strategy_type="shared_upstream_split")
    boms.append(merge_bom)

    return {
        "materialization_version": PHASE_H2_VERSION,
        "source_plan_version": "engine_v2_preview",
        "mode": mode or "stockable",
        "strategy_type": "shared_upstream_split",
        "corr_plan_signature": plan_signature,
        "fg_segment_key": "fg_merge",
        "products": products,
        "boms": boms,
        "warnings": [],
        "metadata": {
            "dry_run": True,
            "phase": "H2",
            "writes_enabled": False,
            "creates_enabled": False,
            "piece_sfg_strategy": "shared_upstream_split",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "shared_upstream_reusable": True,
            "branch_sfg_reusable": True,
            "split_output_sfg_reusable": True,
            "terminal_piece_sfg_fg_specific": True,
            "merge_strategy": "fg_merge_bom",
            "source_blank": source_blank,
            "split": _split_meta(multi_piece),
            "allocation_percentages": list(allocations.values()),
            "route_segmentation": {
                "shared_stage_keys": list(route_segmentation.get("shared_stage_keys") or []),
                "split_after_stage_key": route_segmentation.get("split_after_stage_key") or "",
                "post_split_branch_stage_keys": list(route_segmentation.get("post_split_branch_stage_keys") or []),
                "terminal_piece_stage_key": route_segmentation.get("terminal_piece_stage_key") or "",
                "merge_stage_key": route_segmentation.get("merge_stage_key") or "",
            },
            "cost_rollup": {
                "shared_upstream_cost_allocated_to_piece_branches": True,
                "terminal_piece_costs_feed_fg_merge_components": True,
                "fg_merge_bom_has_no_raw_paper_lines": True,
            },
        },
    }


def build_h2_shared_upstream_flow(
    materialization_plan: Dict[str, Any],
    *,
    preview_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan = dict(materialization_plan or {})
    payload = dict(preview_payload or {})
    metadata = dict(plan.get("metadata") or {})
    source_blank = dict(metadata.get("source_blank") or {})
    split_meta = dict(metadata.get("split") or {})
    allocations = {
        str(row.get("piece_key") or ""): dict(row or {})
        for row in list(metadata.get("allocation_percentages") or [])
    }
    products = {item.get("candidate_key"): dict(item or {}) for item in list(plan.get("products") or [])}
    pieces = sorted(
        [dict(piece or {}) for piece in list((payload.get("multi_piece") or {}).get("pieces") or [])],
        key=lambda item: _safe_int(item.get("piece_index"), 0) or item.get("piece_key") or "",
    )
    fg_name = _fg_product_name(payload)
    source_label = "Shared 2-Ply %s" % _h_size_cm_text(
        source_blank.get("length_mm"),
        source_blank.get("width_mm"),
    )
    ratio_text = "/".join(
        _format_h_percent((allocations.get(_piece_key(piece)) or {}).get("percentage"))
        for piece in pieces
        if allocations.get(_piece_key(piece))
    )
    if not ratio_text:
        ratio_text = "-"
    split_axis = str(split_meta.get("split_axis") or (payload.get("multi_piece") or {}).get("split_axis") or "").strip().title()
    split_mode = str(split_meta.get("split_mode") or (payload.get("multi_piece") or {}).get("split_mode") or "").strip().lower() or "equal"
    real_slitting_bom = split_mode == "equal"
    split_label = "%s Split (%s)" % (split_axis or "Split", ratio_text)

    outputs = []
    tree_lines = [source_label, "  -> %s" % split_label]
    for index, piece in enumerate(pieces):
        key = _piece_key(piece)
        piece_index = _safe_int(piece.get("piece_index"), index + 1) or (index + 1)
        piece_name = "%s - Piece %s" % (fg_name, piece_index) if fg_name else "Piece %s" % piece_index
        size_text = _h_size_cm_text(piece.get("piece_length_mm"), piece.get("piece_width_mm"))
        split_output_key = "product:piece:%s:split_output" % key
        branch_products = [
            product
            for product in products.values()
            if (product.get("metadata") or {}).get("piece_key") == key
            and (product.get("metadata") or {}).get("post_split_branch_sfg")
        ]
        branch_products = sorted(
            branch_products,
            key=lambda item: _safe_int((item.get("metadata") or {}).get("stage_sequence"), 0),
        )
        branch_nodes = [
            {
                "candidate_key": product.get("candidate_key"),
                "label": "%s %s"
                % (
                    (product.get("metadata") or {}).get("stage_name")
                    or (product.get("metadata") or {}).get("stage_key")
                    or "Branch",
                    size_text,
                ),
                "stage_key": (product.get("metadata") or {}).get("stage_key") or "",
                "size": {
                    "length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
                    "width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
                },
            }
            for product in branch_products
        ]
        output = {
            "piece_key": key,
            "piece_index": piece_index,
            "piece_name": piece_name,
            "candidate_key": split_output_key,
            "label": "2-Ply %s" % size_text,
            "size": {
                "length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
                "width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
                "size_cm": size_text,
            },
            "allocation": allocations.get(key) or {},
            "branch": branch_nodes,
            "terminal_piece": {
                "candidate_key": "product:piece:%s:sfg" % key,
                "label": piece_name,
            },
        }
        outputs.append(output)

        branch_prefix = "  |- " if index < len(pieces) - 1 else "  `- "
        nested_prefix = "  |  " if index < len(pieces) - 1 else "     "
        tree_lines.append("%s%s" % (branch_prefix, output["label"]))
        for branch in branch_nodes:
            tree_lines.append("%s   -> %s" % (nested_prefix, branch["label"]))
        tree_lines.append("%s   -> %s" % (nested_prefix, piece_name))
        if index < len(pieces) - 1:
            tree_lines.append("%s" % "  |")

    merge_inputs = [output["terminal_piece"]["label"] for output in outputs]
    tree_lines.append("")
    tree_lines.append("%s" % (" + ".join(merge_inputs) if merge_inputs else "Pieces"))
    tree_lines.append("  -> %s" % (fg_name or "FG Product"))

    return {
        "tree_text_lines": tree_lines,
        "shared_sfg": {
            "candidate_key": "product:shared_upstream:sfg",
            "label": source_label,
            "size": {
                "length_mm": _safe_float(source_blank.get("length_mm"), 0.0),
                "width_mm": _safe_float(source_blank.get("width_mm"), 0.0),
                "size_cm": _h_size_cm_text(source_blank.get("length_mm"), source_blank.get("width_mm")),
            },
        },
        "split_node": {
            "label": split_label,
            "split_axis": (split_axis or "").lower(),
            "split_mode": split_mode,
            "ratio_text": ratio_text,
            "logical_only": not real_slitting_bom,
            "real_slitting_bom": real_slitting_bom,
        },
        "outputs": outputs,
        "merge": {
            "inputs": merge_inputs,
            "fg_product": fg_name or "FG Product",
            "label": "%s -> %s" % (" + ".join(merge_inputs), fg_name or "FG Product") if merge_inputs else "",
        },
    }


def build_h2_candidate_summary(
    materialization_plan: Dict[str, Any],
    resolver_dry_run: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan = dict(materialization_plan or {})
    resolver = dict(resolver_dry_run or {})
    products_by_key = {
        item.get("product_candidate_key"): dict(item or {})
        for item in list(resolver.get("products") or [])
    }
    boms_by_key = {
        item.get("bom_candidate_key"): dict(item or {})
        for item in list(resolver.get("boms") or [])
    }
    summary = {
        "products": [
            _h2_candidate_summary_row(
                item,
                resolver_row=products_by_key.get(item.get("candidate_key")),
                key_field="candidate_key",
                resolver_key_field="product_candidate_key",
            )
            for item in list(plan.get("products") or [])
        ],
        "boms": [
            _h2_candidate_summary_row(
                item,
                resolver_row=boms_by_key.get(item.get("bom_key")),
                key_field="bom_key",
                resolver_key_field="bom_candidate_key",
            )
            for item in list(plan.get("boms") or [])
        ],
    }
    summary["dependency_chain"] = _h2_dependency_chain(plan, resolver)
    return summary


def validate_phase_h3_write_preconditions(preview_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(preview_payload or {})
    multi_piece = dict(payload.get("multi_piece") or {})
    blockers = []
    if not multi_piece.get("enabled"):
        blockers.append("multi_piece_not_enabled")
    if _multi_piece_strategy(multi_piece) != "shared_upstream_split":
        blockers.append("shared_upstream_split_strategy_not_selected")
    if str(multi_piece.get("split_mode") or "").strip().lower() not in ("", "equal"):
        blockers.append("h4_write_supports_equal_split_only")

    h1_debug = dict(payload.get("phase_h_shared_upstream_split_dry_run") or {})
    h2_debug = dict(payload.get("phase_h2_shared_upstream_materialization_dry_run") or {})
    h1_guard = dict(h1_debug.get("guard") or {})
    h2_guard = dict(h2_debug.get("guard") or {})
    resolver = dict(h2_debug.get("resolver_dry_run") or {})
    resolver_blockers = list(resolver.get("blockers") or [])

    if not h1_debug:
        blockers.append("h1_dry_run_missing")
    elif not h1_guard.get("passed"):
        blockers.extend(str(item) for item in list(h1_guard.get("blockers") or []))
    if not h2_debug:
        blockers.append("h2_dry_run_missing")
    elif not h2_guard.get("passed"):
        blockers.extend(str(item) for item in list(h2_guard.get("blockers") or []))
    elif not resolver:
        blockers.append("h2_resolver_dry_run_missing")
    for blocker in resolver_blockers:
        if isinstance(blocker, dict):
            blockers.append(str(blocker.get("reason") or blocker.get("candidate_key") or blocker))
        else:
            blockers.append(str(blocker))

    blockers = sorted(set(item for item in blockers if item))
    return {
        "passed": not blockers,
        "blockers": blockers,
        "policy": {
            "strategy": "shared_upstream_split",
            "requires_h1_allowed": True,
            "requires_h2_guard_passed": True,
            "requires_h2_resolver_without_blockers": True,
            "requires_equal_split": True,
            "uses_phase7b_writer": True,
            "mo_creation_enabled": False,
            "real_split_operation_enabled": True,
        },
    }


def validate_phase_j3c_write_preconditions(preview_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(preview_payload or {})
    multi_piece = dict(payload.get("multi_piece") or {})
    pieces = [dict(item or {}) for item in list(multi_piece.get("pieces") or [])]
    blockers: List[str] = []

    if not multi_piece.get("enabled"):
        blockers.append("multi_piece_not_enabled")
    strategy = _multi_piece_strategy(multi_piece)
    if strategy != "independent_routed_piece":
        if strategy == "shared_upstream_split":
            blockers.append("shared_upstream_split_custom_piece_materialization_not_supported")
        else:
            blockers.append("independent_routed_piece_strategy_required")
    if int(multi_piece.get("piece_count") or len(pieces) or 0) != 2 or len(pieces) != 2:
        blockers.append("requires_exactly_two_pieces")
    if str(multi_piece.get("split_mode") or "").strip().lower() not in ("", "equal"):
        blockers.append("custom_piece_materialization_supports_equal_split_only")
    if _contains_shared_upstream_marker(payload):
        blockers.append("shared_upstream_split_not_supported_for_custom_piece_materialization")

    effective_piece_map = _effective_piece_manufacturing_by_key(payload)
    if not effective_piece_map:
        blockers.append("effective_piece_manufacturing_required")

    custom_piece_keys = sorted(
        key for key, piece in effective_piece_map.items()
        if _piece_has_custom_effective_recipe(piece)
    )
    if not custom_piece_keys:
        blockers.append("custom_piece_overrides_not_present")

    route_segmentation = segment_phase_g2_route(payload)
    g2_guard = validate_phase_g2_guardrails(payload, route_segmentation=route_segmentation)
    if not g2_guard.get("passed"):
        blockers.extend(str(item) for item in list(g2_guard.get("blockers") or []))

    blockers = sorted(set(item for item in blockers if item))
    return {
        "passed": not blockers,
        "blockers": blockers,
        "custom_piece_keys": custom_piece_keys,
        "policy": {
            "strategy": "independent_routed_piece",
            "requires_exact_piece_count": 2,
            "requires_equal_split_only": True,
            "shared_upstream_supported": False,
            "piece_specific_routing_supported": False,
            "uses_phase_g2_writer_path": True,
            "requires_effective_piece_manufacturing": True,
        },
    }


def enrich_phase_g1_piece_rm_requirements(
    preview_payload: Dict[str, Any],
    *,
    paper_resolver: Any = None,
) -> Dict[str, Any]:
    """Populate piece deckle/resolution metadata before G1 plan building."""

    payload = deepcopy(dict(preview_payload or {}))
    multi_piece = dict(payload.get("multi_piece") or {})
    pieces = [dict(item or {}) for item in list(multi_piece.get("pieces") or [])]
    piece_by_key = {_piece_key(piece): piece for piece in pieces if _piece_key(piece)}
    deckle_by_piece = {
        key: _piece_required_deckle_cm(payload, piece)
        for key, piece in piece_by_key.items()
    }

    enriched_rows = []
    for row in list(payload.get("rm_requirements") or []):
        row = deepcopy(dict(row or {}))
        key = _rm_piece_key(row)
        deckle_cm = _safe_float(deckle_by_piece.get(key), 0.0)
        if not key or deckle_cm <= 0.0:
            enriched_rows.append(row)
            continue
        role = _paper_role(row)
        if role not in {"liner", "medium"}:
            enriched_rows.append(row)
            continue

        metadata = dict(row.get("metadata") or {})
        resolution = dict(metadata.get("resolution") or {})
        preview_metadata = dict(metadata.get("preview_metadata") or {})
        metadata["required_deckle_cm"] = _safe_float(
            metadata.get("required_deckle_cm") or resolution.get("required_deckle_cm"),
            deckle_cm,
        ) or deckle_cm
        resolution["required_deckle_cm"] = _safe_float(
            resolution.get("required_deckle_cm") or metadata.get("required_deckle_cm"),
            deckle_cm,
        ) or deckle_cm
        preview_metadata["required_deckle_cm"] = _safe_float(
            preview_metadata.get("required_deckle_cm") or metadata.get("required_deckle_cm"),
            deckle_cm,
        ) or deckle_cm

        if paper_resolver and not _safe_int(metadata.get("product_id") or resolution.get("product_id"), 0):
            resolved = _resolve_piece_paper(row, metadata, resolution, preview_metadata, role, deckle_cm, paper_resolver)
            if resolved:
                resolution.update(resolved)
                metadata["resolution_status"] = resolved.get("status") or metadata.get("resolution_status")
                if resolved.get("product_id"):
                    metadata["product_id"] = resolved.get("product_id")
                    metadata["product_name"] = resolved.get("product_name")
                    metadata["writer_product_key"] = "product.product:%s" % resolved.get("product_id")
                if resolved.get("uom_id"):
                    metadata["uom_id"] = resolved.get("uom_id")
                    metadata["uom_name"] = resolved.get("uom_name")
                metadata["resolution_note"] = "Exact paper variant resolved"
                resolution["resolution_note"] = "Exact paper variant resolved"

        metadata["resolution"] = resolution
        metadata["preview_metadata"] = preview_metadata
        row["metadata"] = metadata
        enriched_rows.append(row)

    payload["rm_requirements"] = enriched_rows
    return payload


def _payload_with_piece_specific_rm_requirements(preview_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(preview_payload or {}))
    piece_specific = dict(payload.get("piece_specific_rm_breakdown") or {})
    rows = list(piece_specific.get("rows") or [])
    if not rows:
        return payload

    def _product_key(role: str, metadata: Dict[str, Any]) -> str:
        normalized = str(role or metadata.get("role") or metadata.get("type") or "").strip().lower()
        kind = str(metadata.get("kind") or "").strip().lower()
        if normalized == "medium":
            return "rm_medium"
        if normalized == "top_roll" or kind == "top_roll":
            return "rm_top_roll"
        if normalized == "top_sheet_external" or kind == "top_sheet_external":
            return "rm_top_sheet_external"
        return "rm_liner"

    rm_rows = []
    for row in rows:
        row = deepcopy(dict(row or {}))
        metadata = deepcopy(dict(row.get("metadata") or {}))
        resolution = deepcopy(dict(metadata.get("resolution") or {}))
        role = str(row.get("role") or metadata.get("role") or metadata.get("type") or "").strip()
        qty_uom = row.get("uom") or metadata.get("uom_name") or metadata.get("qty_uom") or ""

        for key in (
            "piece_key",
            "piece_index",
            "piece_name",
            "stack_source",
            "surface_source",
            "top_sheet_source",
            "recipe_signature",
            "source_layer_no",
            "gsm",
            "bf",
            "color",
            "flute",
            "flute_code",
            "flute_type",
            "flute_type_code",
            "flute_type_id",
            "flute_tuf",
        ):
            value = row.get(key)
            if value not in (None, ""):
                metadata[key] = value
        normalized_flute = _normalize_flute_code(
            metadata.get("flute_code")
            or metadata.get("flute")
            or metadata.get("flute_type_code")
            or metadata.get("flute_type")
        )
        if normalized_flute:
            metadata["flute"] = normalized_flute
            metadata["flute_code"] = normalized_flute

        paper_role = str(role or "").strip().lower() in {"liner", "medium", "top_roll"}
        product_id = (
            metadata.get("product_id")
            or row.get("product_id")
            or metadata.get("top_sheet_product_id")
            or row.get("top_sheet_product_id")
            or resolution.get("product_id")
        )
        if product_id and not metadata.get("writer_product_key"):
            metadata["writer_product_key"] = "product.product:%s" % product_id
        if product_id and not resolution.get("product_id"):
            resolution["product_id"] = product_id
        display_product_name = row.get("product_name") or row.get("display_product") or row.get("product")
        metadata_product_name = metadata.get("product_name") or resolution.get("product_name")
        product_name = display_product_name or metadata_product_name
        if product_name:
            metadata["product_name"] = product_name
        if product_name and not resolution.get("product_name"):
            resolution["product_name"] = product_name
        display_metadata_mismatch = (
            str(role or "").strip().lower() in {"liner", "medium", "top_roll"}
            and bool(display_product_name)
            and bool(metadata_product_name)
            and _g2_normalize_product_text(display_product_name) != _g2_normalize_product_text(metadata_product_name)
        )
        stale_product_conflict = _g2_resolved_product_conflicts_with_stack_line(
            product_name,
            metadata.get("gsm"),
            metadata.get("bf"),
        )
        if paper_role:
            metadata["force_live_paper_resolution"] = True
            metadata["source_kind"] = "piece_effective_recipe"
        if paper_role or stale_product_conflict or display_metadata_mismatch:
            product_id = None
            metadata.pop("product_id", None)
            metadata.pop("product_tmpl_id", None)
            metadata.pop("writer_product_key", None)
            resolution.pop("product_id", None)
            resolution.pop("product_tmpl_id", None)
            if stale_product_conflict or display_metadata_mismatch:
                product_name = None
                metadata.pop("product_name", None)
                resolution.pop("product_name", None)
        product_tmpl_id = None if (paper_role or stale_product_conflict or display_metadata_mismatch) else (metadata.get("product_tmpl_id") or row.get("product_tmpl_id"))
        if product_tmpl_id:
            metadata["product_tmpl_id"] = product_tmpl_id
        uom_id = metadata.get("uom_id") or row.get("uom_id")
        if uom_id:
            metadata["uom_id"] = uom_id
        if uom_id and not resolution.get("uom_id"):
            resolution["uom_id"] = uom_id
        required_deckle_cm = metadata.get("required_deckle_cm") or row.get("required_deckle_cm")
        if required_deckle_cm:
            metadata["required_deckle_cm"] = required_deckle_cm
        if required_deckle_cm and not resolution.get("required_deckle_cm"):
            resolution["required_deckle_cm"] = required_deckle_cm
        if product_id:
            metadata["product_id"] = product_id
            metadata["resolution_status"] = metadata.get("resolution_status") or "resolved"
            resolution["status"] = resolution.get("status") or "resolved"
        metadata["resolution"] = resolution

        rm_rows.append(
            {
                "product_key": row.get("product_key") or _product_key(role, metadata),
                "role": role,
                "stage_key": row.get("stage_code") or row.get("stage_key") or metadata.get("stage_key"),
                "layer_no": row.get("layer_no") or metadata.get("layer_no"),
                "qty_per_fg": row.get("qty_per_fg", 0.0),
                "qty_uom": qty_uom,
                "required_deckle_cm": required_deckle_cm,
                "product_id": product_id,
                "uom_id": uom_id,
                "consumption_type": metadata.get("consumption_type") or ("unit" if qty_uom == "unit" else "area"),
                "metadata": metadata,
            }
        )

    payload["rm_requirements"] = rm_rows
    payload["j3_piece_effective_rm_requirements_applied"] = {
        "enabled": True,
        "source": "piece_specific_rm_breakdown.rows",
        "row_count": len(rm_rows),
    }
    return payload


def validate_phase_g1_guardrails(preview_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(preview_payload or {})
    multi_piece = dict(payload.get("multi_piece") or {})
    pieces = [dict(item or {}) for item in list(multi_piece.get("pieces") or [])]
    blockers: List[str] = []

    if not multi_piece.get("enabled"):
        blockers.append("multi_piece_not_enabled")
    if int(multi_piece.get("piece_count") or len(pieces) or 0) != 2 or len(pieces) != 2:
        blockers.append("requires_exactly_two_pieces")
    if not str(multi_piece.get("merge_stage_key") or "").strip():
        blockers.append("merge_stage_required")

    piece_keys = [_piece_key(piece) for piece in pieces]
    if sorted(piece_keys) != ["A", "B"]:
        blockers.append("requires_piece_keys_A_and_B")

    for piece in pieces:
        key = _piece_key(piece) or "?"
        if _safe_float(piece.get("piece_qty_per_fg"), 0.0) <= 0.0:
            blockers.append(f"piece_{key}_qty_per_fg_required")
        if _safe_float(piece.get("piece_length_mm"), 0.0) <= 0.0:
            blockers.append(f"piece_{key}_length_required")
        if _safe_float(piece.get("piece_width_mm"), 0.0) <= 0.0:
            blockers.append(f"piece_{key}_width_required")
        if not _piece_manufacturing_source_is_inherited(piece.get("stack_source")):
            blockers.append(f"piece_{key}_stack_override_not_supported")
        if not _piece_manufacturing_source_is_inherited(piece.get("surface_source")):
            blockers.append(f"piece_{key}_surface_override_not_supported")
        if not _piece_manufacturing_source_is_inherited(piece.get("top_sheet_source")):
            blockers.append(f"piece_{key}_top_sheet_override_not_supported")

    overrides = dict(payload.get("piece_manufacturing_overrides") or {})
    for override in list(overrides.get("pieces") or []):
        override = dict(override or {})
        key = _piece_key(override) or "?"
        if not _piece_manufacturing_source_is_inherited(
            override.get("stack_source") or (override.get("stack") or {}).get("source")
        ):
            blockers.append(f"piece_{key}_override_stack_source_not_supported")
        if not _piece_manufacturing_source_is_inherited(
            override.get("surface_source") or (override.get("surface") or {}).get("source")
        ):
            blockers.append(f"piece_{key}_override_surface_source_not_supported")
        if not _piece_manufacturing_source_is_inherited(
            override.get("top_sheet_source") or (override.get("top_sheet") or {}).get("source")
        ):
            blockers.append(f"piece_{key}_override_top_sheet_source_not_supported")

    planning = dict(payload.get("piece_layout_planning") or {})
    planning_by_piece = {
        _piece_key(item): dict(item or {})
        for item in list(planning.get("pieces") or [])
        if _piece_key(item)
    }
    for key in piece_keys:
        item = planning_by_piece.get(key) or {}
        selected = dict(item.get("selected_option") or item.get("preferred_option") or {})
        if not selected:
            blockers.append(f"piece_{key}_layout_required")
            continue
        if _safe_int(selected.get("up_count") or selected.get("ups") or selected.get("slits"), 0) != 1:
            blockers.append(f"piece_{key}_layout_ups_must_be_1")

    if _contains_shared_upstream_marker(payload):
        blockers.append("shared_upstream_split_not_supported_in_g1")

    rm_rows = [dict(row or {}) for row in list(payload.get("rm_requirements") or [])]
    for key in piece_keys:
        if key and not [row for row in rm_rows if _rm_piece_key(row) == key]:
            blockers.append(f"piece_{key}_rm_rows_required")

    blockers = sorted(set(blockers))
    return {
        "passed": not blockers,
        "blockers": blockers,
        "policy": {
            "exact_piece_count": 2,
            "supported_piece_keys": ["A", "B"],
            "inherited_manufacturing_only": True,
            "piece_layout_ups": 1,
            "shared_upstream_supported": False,
            "write_enabled": False,
        },
    }


def build_phase_g1_materialization_plan(
    preview_payload: Dict[str, Any],
    *,
    mode: str = "stockable",
) -> Dict[str, Any]:
    payload = dict(preview_payload or {})
    multi_piece = dict(payload.get("multi_piece") or {})
    pieces = sorted([dict(item or {}) for item in multi_piece.get("pieces") or []], key=_piece_key)
    plan_signature = _plan_signature(payload)
    source_blank = {
        "length_mm": _safe_float(multi_piece.get("source_length_mm"), 0.0),
        "width_mm": _safe_float(multi_piece.get("source_width_mm"), 0.0),
    }

    piece_products = [_piece_product_candidate(piece, plan_signature, payload) for piece in pieces]
    fg_product = {
        "candidate_key": "product:fg",
        "segment_key": "fg_merge",
        "product_role": "fg",
        "corr_sfg_key": None,
        "corr_segment_key": "fg_merge",
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": _hash_json({"role": "fg", "pieces": [_piece_key(p) for p in pieces]}),
        "corr_material_signature": _hash_json({"merge_inputs": [_piece_key(p) for p in pieces]}),
        "corr_route_signature": _hash_json({"merge_stage_key": multi_piece.get("merge_stage_key")}),
        "corr_layout_signature": _hash_json({"source_blank": source_blank, "split": _split_meta(multi_piece)}),
        "corr_resolution_signature": _hash_json({"phase": "G1", "resolution": "piece_sfg_merge"}),
        "stock_mode": "finished_good",
        "product_type_hint": "product",
        "route_policy": "reuse_existing_fg",
        "uom": "Units",
        "metadata": {
            "phase": "G1",
            "source": "current_fg_product",
            "writes_enabled": False,
        },
    }
    product_by_piece = {_piece_key(piece): product for piece, product in zip(pieces, piece_products)}

    piece_boms = [
        _piece_bom_candidate(payload, piece, product_by_piece[_piece_key(piece)], plan_signature)
        for piece in pieces
    ]
    merge_bom = _fg_merge_bom_candidate(
        multi_piece,
        pieces,
        fg_product,
        product_by_piece,
        plan_signature,
    )

    return {
        "materialization_version": PHASE_G1_VERSION,
        "source_plan_version": "engine_v2_preview",
        "mode": mode or "stockable",
        "corr_plan_signature": plan_signature,
        "fg_segment_key": "fg_merge",
        "products": [*piece_products, fg_product],
        "boms": [*piece_boms, merge_bom],
        "warnings": [],
        "metadata": {
            "dry_run": True,
            "phase": "G1",
            "writes_enabled": False,
            "piece_sfg_strategy": "terminal_piece_sfg",
            "merge_strategy": "fg_merge_bom",
            "shared_upstream_supported": False,
            "source_blank": source_blank,
            "split": _split_meta(multi_piece),
            "cost_rollup": {
                "piece_bom_costs_feed_fg_merge_components": True,
                "fg_merge_bom_has_no_raw_paper_lines": True,
            },
        },
    }


def segment_phase_g2_route(preview_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(preview_payload or {})
    multi_piece = dict(payload.get("multi_piece") or {})
    merge_stage = str(multi_piece.get("merge_stage_key") or "").strip()
    stages = _active_route_stage_rows(payload)

    stage_keys = [str(stage.get("stage_key") or "").strip() for stage in stages if stage.get("stage_key")]
    merge_index = None
    for index, key in enumerate(stage_keys):
        if _stage_key_matches(key, merge_stage):
            merge_index = index
            break

    if merge_index is None:
        branch_stages = [
            stage
            for stage in stages
            if not _stage_key_matches(str(stage.get("stage_key") or ""), merge_stage)
            and _is_g2_branch_stage(stage)
        ]
        merge_stage_row = {}
    else:
        branch_stages = [
            stage
            for stage in stages[:merge_index]
            if _is_g2_branch_stage(stage)
        ]
        merge_stage_row = dict(stages[merge_index] or {})

    terminal_stage = dict(branch_stages[-1]) if branch_stages else {}
    return {
        "source": "phase5_preview_model" if (payload.get("phase5_preview_model") or {}).get("stages") else "phase8_shadow_runtime",
        "all_stage_keys": stage_keys,
        "branch_stage_keys": [stage.get("stage_key") for stage in branch_stages],
        "branch_stages": branch_stages,
        "merge_stage_key": merge_stage,
        "merge_stage_found": bool(merge_stage_row),
        "merge_stage": merge_stage_row,
        "terminal_stage_key": terminal_stage.get("stage_key") or "",
        "terminal_stage": terminal_stage,
        "post_merge_stage_keys": stage_keys[merge_index + 1 :] if merge_index is not None else [],
    }


def segment_phase_h_shared_upstream_route(preview_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(preview_payload or {})
    multi_piece = dict(payload.get("multi_piece") or {})
    split_after = str(multi_piece.get("split_after_stage_key") or "").strip()
    merge_stage = str(multi_piece.get("merge_stage_key") or "").strip()
    stages = _active_route_stage_rows(payload)
    stage_keys = [str(stage.get("stage_key") or "").strip() for stage in stages if stage.get("stage_key")]

    split_index = None
    merge_index = None
    for index, key in enumerate(stage_keys):
        if split_index is None and _stage_key_matches(key, split_after):
            split_index = index
        if merge_index is None and _stage_key_matches(key, merge_stage):
            merge_index = index

    shared_stages = stages[: split_index + 1] if split_index is not None else []
    split_node = dict(stages[split_index]) if split_index is not None else {}
    if merge_index is not None:
        post_split_stages = stages[split_index + 1 : merge_index] if split_index is not None else []
        merge_stage_row = dict(stages[merge_index] or {})
        post_merge_stage_keys = stage_keys[merge_index + 1 :]
    else:
        post_split_stages = stages[split_index + 1 :] if split_index is not None else []
        merge_stage_row = {}
        post_merge_stage_keys = []
    terminal_stage = dict(post_split_stages[-1]) if post_split_stages else {}

    return {
        "source": "phase5_preview_model" if (payload.get("phase5_preview_model") or {}).get("stages") else "phase8_shadow_runtime",
        "all_stage_keys": stage_keys,
        "split_after_stage_key": split_after,
        "split_after_stage_found": bool(split_node),
        "shared_stage_keys": [stage.get("stage_key") for stage in shared_stages],
        "shared_stages": shared_stages,
        "split_node": split_node,
        "post_split_branch_stage_keys": [stage.get("stage_key") for stage in post_split_stages],
        "post_split_branch_stages": post_split_stages,
        "terminal_piece_stage_key": terminal_stage.get("stage_key") or "",
        "terminal_piece_stage": terminal_stage,
        "merge_stage_key": merge_stage,
        "merge_stage_found": bool(merge_stage_row),
        "merge_stage": merge_stage_row,
        "post_merge_stage_keys": post_merge_stage_keys,
    }


def validate_phase_h_shared_upstream_guardrails(
    preview_payload: Dict[str, Any],
    *,
    route_segmentation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = dict(preview_payload or {})
    multi_piece = dict(payload.get("multi_piece") or {})
    pieces = [dict(item or {}) for item in multi_piece.get("pieces") or []]
    route_segmentation = dict(route_segmentation or segment_phase_h_shared_upstream_route(payload))
    blockers: List[str] = []
    warnings: List[str] = []

    if not multi_piece.get("enabled"):
        blockers.append("multi_piece_not_enabled")
    if _multi_piece_strategy(multi_piece) != "shared_upstream_split":
        blockers.append("shared_upstream_split_strategy_not_selected")
    if int(multi_piece.get("piece_count") or len(pieces) or 0) != 2 or len(pieces) != 2:
        blockers.append("exactly_two_pieces_required")
    if not str(multi_piece.get("split_after_stage_key") or "").strip():
        blockers.append("split_after_stage_required")
    if not route_segmentation.get("split_after_stage_found"):
        blockers.append("split_after_stage_not_found_in_active_route")
    if not str(multi_piece.get("merge_stage_key") or "").strip():
        blockers.append("merge_stage_required")
    if not route_segmentation.get("merge_stage_found"):
        blockers.append("merge_stage_not_found_in_active_route")
    if route_segmentation.get("split_after_stage_found") and route_segmentation.get("merge_stage_found"):
        all_keys = list(route_segmentation.get("all_stage_keys") or [])
        split_key = route_segmentation.get("split_after_stage_key")
        merge_key = route_segmentation.get("merge_stage_key")
        split_pos = _stage_key_position(all_keys, split_key)
        merge_pos = _stage_key_position(all_keys, merge_key)
        if split_pos is not None and merge_pos is not None and split_pos >= merge_pos:
            blockers.append("split_after_stage_must_be_before_merge_stage")
    if not route_segmentation.get("post_split_branch_stage_keys"):
        blockers.append("post_split_branch_stages_required")

    for piece in pieces:
        key = _piece_key(piece) or "unknown"
        if abs(_safe_float(piece.get("piece_qty_per_fg"), 0.0) - 1.0) > 1e-6:
            blockers.append(f"piece_{key}_qty_per_fg_must_be_1")
        if not _piece_manufacturing_source_is_inherited(piece.get("stack_source")):
            blockers.append(f"piece_{key}_stack_override_not_supported")
        if not _piece_manufacturing_source_is_inherited(piece.get("surface_source")):
            blockers.append(f"piece_{key}_surface_override_not_supported")
        if not _piece_manufacturing_source_is_inherited(piece.get("top_sheet_source")):
            blockers.append(f"piece_{key}_top_sheet_override_not_supported")

    blockers.extend(_h_piece_layout_ups_blockers(payload, pieces))

    nested_split_detected = _h_nested_split_detected(payload)
    if nested_split_detected:
        blockers.append("nested_split_not_supported")

    allocations = _h_allocation_rows(multi_piece, pieces, str(multi_piece.get("split_axis") or ""), _h_source_blank(multi_piece))
    allocation_total = sum(_safe_float(row.get("ratio"), 0.0) for row in allocations)
    if pieces and abs(allocation_total - 1.0) > 0.001:
        blockers.append("allocation_ratios_must_sum_to_1")

    capacity_checks = _h_capacity_checks(payload, multi_piece, route_segmentation, pieces)
    for check in capacity_checks:
        if check.get("status") in {"failed", "blocked"}:
            blockers.append(str(check.get("blocker") or "capacity_check_failed"))
        elif check.get("status") == "not_checked":
            warnings.append(str(check.get("reason") or "capacity_check_not_available"))

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    return {
        "passed": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "capacity_checks": capacity_checks,
        "nested_split_detected": nested_split_detected,
        "policy": {
            "strategy": "shared_upstream_split",
            "exact_piece_count": 2,
            "supported_piece_keys": ["A", "B"],
            "piece_qty_per_fg": 1,
            "inherited_manufacturing_only": True,
            "piece_layout_ups": 1,
            "requires_split_after_stage_in_route": True,
            "requires_merge_stage_in_route": True,
            "nested_split_supported": False,
            "write_enabled": False,
        },
    }


def validate_phase_g2_guardrails(
    preview_payload: Dict[str, Any],
    *,
    route_segmentation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = dict(preview_payload or {})
    multi_piece = dict(payload.get("multi_piece") or {})
    pieces = [dict(item or {}) for item in list(multi_piece.get("pieces") or [])]
    blockers: List[str] = []
    route_segmentation = dict(route_segmentation or segment_phase_g2_route(payload))

    if not multi_piece.get("enabled"):
        blockers.append("multi_piece_not_enabled")
    if _multi_piece_strategy(multi_piece) != "independent_routed_piece":
        blockers.append("independent_routed_piece_strategy_required")
    if int(multi_piece.get("piece_count") or len(pieces) or 0) != 2 or len(pieces) != 2:
        blockers.append("requires_exactly_two_pieces")
    if not str(multi_piece.get("merge_stage_key") or "").strip():
        blockers.append("merge_stage_required")

    piece_keys = [_piece_key(piece) for piece in pieces]
    if sorted(piece_keys) != ["A", "B"]:
        blockers.append("requires_piece_keys_A_and_B")
    for piece in pieces:
        key = _piece_key(piece) or "?"
        if _safe_float(piece.get("piece_qty_per_fg"), 0.0) <= 0.0:
            blockers.append(f"piece_{key}_qty_per_fg_required")
        if _safe_float(piece.get("piece_length_mm"), 0.0) <= 0.0:
            blockers.append(f"piece_{key}_length_required")
        if _safe_float(piece.get("piece_width_mm"), 0.0) <= 0.0:
            blockers.append(f"piece_{key}_width_required")

    planning = dict(payload.get("piece_layout_planning") or {})
    planning_by_piece = {
        _piece_key(item): dict(item or {})
        for item in list(planning.get("pieces") or [])
        if _piece_key(item)
    }
    for key in piece_keys:
        item = planning_by_piece.get(key) or {}
        selected = dict(item.get("selected_option") or item.get("preferred_option") or {})
        if not selected:
            blockers.append(f"piece_{key}_layout_required")
            continue
        if _safe_int(selected.get("up_count") or selected.get("ups") or selected.get("slits"), 0) != 1:
            blockers.append(f"piece_{key}_layout_ups_must_be_1")

    if _contains_shared_upstream_marker(payload):
        blockers.append("shared_upstream_split_not_supported_in_g2")
    if not route_segmentation.get("merge_stage_found"):
        blockers.append("merge_stage_not_found_in_active_route")
    if not route_segmentation.get("branch_stage_keys"):
        blockers.append("branch_stages_required")
    if not route_segmentation.get("terminal_stage_key"):
        blockers.append("terminal_piece_stage_required")
    if any(
        not _piece_manufacturing_source_is_inherited(piece.get("routing_source"))
        for piece in pieces
    ):
        blockers.append("piece_specific_routing_not_supported")

    blockers = sorted(set(blockers))
    return {
        "passed": not blockers,
        "blockers": blockers,
        "policy": {
            "exact_piece_count": 2,
            "supported_piece_keys": ["A", "B"],
            "inherited_manufacturing_only": False,
            "piece_layout_ups": 1,
            "shared_upstream_supported": False,
            "split_after_stage_supported": False,
            "requires_merge_stage_in_route": True,
            "requires_branch_stages": True,
            "write_enabled": False,
        },
    }


def build_phase_g2_materialization_plan(
    preview_payload: Dict[str, Any],
    *,
    route_segmentation: Optional[Dict[str, Any]] = None,
    mode: str = "stockable",
) -> Dict[str, Any]:
    payload = dict(preview_payload or {})
    multi_piece = dict(payload.get("multi_piece") or {})
    pieces = sorted([dict(item or {}) for item in multi_piece.get("pieces") or []], key=_piece_key)
    route_segmentation = dict(route_segmentation or segment_phase_g2_route(payload))
    branch_stages = [dict(stage or {}) for stage in route_segmentation.get("branch_stages") or []]
    board_stage_key = _g2_board_stage_key(branch_stages)
    plan_signature = _plan_signature(payload)
    source_blank = {
        "length_mm": _safe_float(multi_piece.get("source_length_mm"), 0.0),
        "width_mm": _safe_float(multi_piece.get("source_width_mm"), 0.0),
    }
    piece_effective_map = _effective_piece_manufacturing_by_key(payload)

    products: List[Dict[str, Any]] = []
    product_by_piece_stage: Dict[str, Dict[str, Dict[str, Any]]] = {}
    two_ply_sets_by_piece: Dict[str, List[Dict[str, Any]]] = {}
    terminal_product_by_piece: Dict[str, Dict[str, Any]] = {}
    for piece in pieces:
        key = _piece_key(piece)
        piece_effective = _effective_piece_payload(piece, piece_effective_map.get(key) or {})
        product_by_piece_stage[key] = {}
        for stage in branch_stages:
            sets = _g2_piece_2ply_sets(payload, piece_effective, stage)
            if sets:
                two_ply_sets_by_piece[key] = sets
                for group in sets:
                    group["set_count"] = len(sets)
                    product = _g2_piece_2ply_set_product_candidate(piece_effective, stage, group, plan_signature)
                    products.append(product)
                    group["product"] = product
                continue
            product = _g2_piece_stage_product_candidate(piece_effective, stage, plan_signature)
            products.append(product)
            product_by_piece_stage[key][str(stage.get("stage_key") or "")] = product
        terminal = _g2_piece_terminal_product_candidate(piece_effective, route_segmentation, plan_signature, payload)
        products.append(terminal)
        terminal_product_by_piece[key] = terminal

    fg_product = _g2_fg_product_candidate(multi_piece, pieces, source_blank, plan_signature)
    products.append(fg_product)

    boms: List[Dict[str, Any]] = []
    for piece in pieces:
        key = _piece_key(piece)
        piece_effective = _effective_piece_payload(piece, piece_effective_map.get(key) or {})
        previous_products: List[Dict[str, Any]] = []
        for index, stage in enumerate(branch_stages):
            stage_key = str(stage.get("stage_key") or "")
            two_ply_sets = two_ply_sets_by_piece.get(key) if index == 0 else None
            if two_ply_sets:
                stage_qty = _g2_previous_output_component_qty(
                    payload,
                    stage,
                    branch_stages[index + 1] if index + 1 < len(branch_stages) else {},
                    piece_key=key,
                )
                for group in two_ply_sets:
                    product = dict(group.get("product") or {})
                    boms.append(_g2_piece_2ply_set_bom_candidate(payload, piece_effective, stage, group, product, plan_signature))
                single_set_qty = max(
                    _safe_float((two_ply_sets[0] or {}).get("qty"), 1.0),
                    _safe_float(stage_qty.get("qty"), 0.0),
                ) if len(two_ply_sets) == 1 else 0.0
                previous_products = []
                for group in two_ply_sets:
                    metadata = {
                        "piece_2ply_set": True,
                        "layer_pairs": group.get("layer_pairs"),
                    }
                    if len(two_ply_sets) > 1:
                        metadata["set_key"] = group.get("set_key")
                    previous_products.append({
                        "product": dict(group.get("product") or {}),
                        "qty": single_set_qty if single_set_qty > 0.0 else _safe_float(group.get("qty"), 1.0),
                        "quantity_source": "planner_or_piece_2ply_set_identity" if single_set_qty > 0.0 else "piece_2ply_set_identity",
                        "metadata": metadata,
                    })
                continue

            current_product = product_by_piece_stage[key][stage_key]
            bom = _g2_piece_stage_bom_candidate(
                payload,
                piece_effective,
                stage,
                current_product,
                previous_products,
                is_first_stage=(index == 0),
                board_stage_key=board_stage_key,
                plan_signature=plan_signature,
            )
            boms.append(bom)
            previous_products = [{"product": current_product, "qty": 1.0, "quantity_source": "stage_output"}]
        terminal_bom = _g2_piece_terminal_bom_candidate(
            piece_effective,
            terminal_product_by_piece[key],
            previous_products[0]["product"] if previous_products else None,
            route_segmentation,
            plan_signature,
        )
        boms.append(terminal_bom)

    merge_bom = _fg_merge_bom_candidate(
        multi_piece,
        pieces,
        fg_product,
        terminal_product_by_piece,
        plan_signature,
    )
    terminal_corr_sfg_keys = [
        terminal_product_by_piece.get(_piece_key(piece), {}).get("corr_sfg_key")
        for piece in pieces
    ]
    merge_bom["corr_material_signature"] = _hash_json(
        {
            "phase": "G2",
            "merge_piece_products": [line["product_candidate_key"] for line in merge_bom.get("lines") or []],
            "terminal_corr_sfg_keys": terminal_corr_sfg_keys,
        }
    )
    merge_bom["corr_route_signature"] = _hash_json(
        {"phase": "G2", "merge_stage_key": multi_piece.get("merge_stage_key")}
    )
    merge_bom["corr_resolution_signature"] = _hash_json(
        {"phase": "G2", "resolution": "g2_terminal_piece_sfg_components"}
    )
    merge_bom["metadata"] = dict(merge_bom.get("metadata") or {}, phase="G2", strategy_type="independent_routed_piece")
    for line in merge_bom.get("lines") or []:
        metadata = dict(line.get("metadata") or {})
        metadata["phase"] = "G2"
        line["metadata"] = metadata
    for operation in merge_bom.get("operations") or []:
        metadata = dict(operation.get("metadata") or {})
        metadata["phase"] = "G2"
        operation["metadata"] = metadata
    boms.append(merge_bom)

    return {
        "materialization_version": PHASE_G2_VERSION,
        "source_plan_version": "engine_v2_preview",
        "mode": mode or "stockable",
        "strategy_type": "independent_routed_piece",
        "corr_plan_signature": plan_signature,
        "fg_segment_key": "fg_merge",
        "products": products,
        "boms": boms,
        "warnings": [],
        "metadata": {
            "dry_run": True,
            "phase": "G2",
            "writes_enabled": False,
            "piece_sfg_strategy": "routed_piece_stage_chain",
            "merge_strategy": "fg_merge_bom",
            "shared_upstream_supported": False,
            "source_blank": source_blank,
            "split": _split_meta(multi_piece),
            "j3_piece_recipe_overrides_enabled": any(
                _piece_has_custom_effective_recipe(item)
                for item in piece_effective_map.values()
            ),
            "piece_effective_recipe_map": piece_effective_map,
            "route_segmentation": {
                "branch_stage_keys": list(route_segmentation.get("branch_stage_keys") or []),
                "merge_stage_key": route_segmentation.get("merge_stage_key") or "",
                "terminal_stage_key": route_segmentation.get("terminal_stage_key") or "",
            },
            "cost_rollup": {
                "piece_stage_costs_feed_terminal_piece_sfg": True,
                "terminal_piece_costs_feed_fg_merge_components": True,
                "fg_merge_bom_has_no_raw_paper_lines": True,
            },
        },
    }


def _piece_product_candidate(piece: Dict[str, Any], plan_signature: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    key = _piece_key(piece)
    segment_key = f"piece:{key}:terminal"
    corr_sfg_key = _piece_corr_sfg_key(piece, plan_signature)
    fg_name = _fg_product_name(payload or {})
    candidate = {
        "candidate_key": f"product:piece:{key}:sfg",
        "segment_key": segment_key,
        "product_role": "sfg",
        "corr_sfg_key": corr_sfg_key,
        "corr_segment_key": segment_key,
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": _hash_json({"piece": key, "output": "terminal_sfg"}),
        "corr_material_signature": _hash_json({"piece": key, "manufacturing": "inherited"}),
        "corr_route_signature": _hash_json({"piece": key, "route": "terminal_only_g1"}),
        "corr_layout_signature": _hash_json(
            {
                "piece": key,
                "length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
                "width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
                "ups": 1,
            }
        ),
        "corr_resolution_signature": _hash_json({"piece": key, "resolution": "inherited_parent"}),
        "stock_mode": "stockable",
        "product_type_hint": "product",
        "route_policy": "manufacture",
        "uom": "Units",
        "metadata": {
            "phase": "G1",
            "piece_key": key,
            "piece_name": piece.get("piece_name") or f"Piece {key}",
            "piece_index": _safe_int(piece.get("piece_index"), 0),
            "fg_product_name": fg_name,
            "piece_qty_per_fg": _safe_float(piece.get("piece_qty_per_fg"), 1.0),
            "piece_length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
            "piece_width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
            "manufacturing_source": "inherited",
            "terminal_piece_sfg": True,
        },
    }
    candidate["metadata"].update(_piece_candidate_recipe_metadata(piece))
    return candidate


def _piece_bom_candidate(
    payload: Dict[str, Any],
    piece: Dict[str, Any],
    product: Dict[str, Any],
    plan_signature: str,
) -> Dict[str, Any]:
    key = _piece_key(piece)
    bom_key = f"bom:piece:{key}:terminal"
    rm_lines = [
        _rm_line_candidate(row, key, bom_key)
        for row in list(payload.get("rm_requirements") or [])
        if _rm_piece_key(dict(row or {})) == key
    ]
    layout_signature = product.get("corr_layout_signature") or ""
    bom = {
        "bom_key": bom_key,
        "segment_key": product.get("segment_key"),
        "product_candidate_key": product.get("candidate_key"),
        "bom_role": "sfg",
        "bom_type": "normal",
        "product_qty": 1.0,
        "corr_segment_key": product.get("segment_key"),
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": product.get("corr_output_fingerprint"),
        "corr_material_signature": _hash_json({"piece": key, "rm_lines": _line_signature_rows(rm_lines)}),
        "corr_route_signature": _hash_json({"piece": key, "route": "terminal_piece_g1"}),
        "corr_layout_signature": layout_signature,
        "corr_resolution_signature": _hash_json({"piece": key, "rm_resolution": _line_resolution_rows(rm_lines)}),
        "lines": rm_lines,
        "operations": [],
        "metadata": {
            "phase": "G1",
            "piece_key": key,
            "terminal_piece_bom": True,
            "rm_filter": {"metadata.piece_key": key},
            "raw_paper_lines_allowed": True,
            "piece_layout_ups": 1,
        },
    }
    bom["metadata"].update(_piece_candidate_recipe_metadata(piece))
    return bom


def _fg_merge_bom_candidate(
    multi_piece: Dict[str, Any],
    pieces: List[Dict[str, Any]],
    fg_product: Dict[str, Any],
    product_by_piece: Dict[str, Dict[str, Any]],
    plan_signature: str,
) -> Dict[str, Any]:
    bom_key = "bom:fg:multi_piece_merge"
    merge_stage = str(multi_piece.get("merge_stage_key") or "").strip()
    lines = []
    for piece in pieces:
        key = _piece_key(piece)
        product = product_by_piece.get(key) or {}
        lines.append(
            {
                "line_key": f"{bom_key}:previous:piece:{key}",
                "bom_key": bom_key,
                "line_type": "previous_output",
                "product_candidate_key": product.get("candidate_key"),
                "source_segment_key": product.get("segment_key"),
                "input_key": None,
                "role_code": "piece_sfg",
                "qty": _safe_float(piece.get("piece_qty_per_fg"), 1.0),
                "uom": "Units",
                "operation_segment_key": "fg_merge",
                "metadata": {
                    "phase": "G1",
                    "piece_key": key,
                    "semantic_role": "piece_sfg",
                    "component_kind": "terminal_piece_sfg",
                    "raw_paper_line": False,
                },
            }
        )
    operation = {
        "operation_candidate_key": f"operation:{bom_key}:{merge_stage or 'merge'}",
        "bom_key": bom_key,
        "segment_key": "fg_merge",
        "operation_key": merge_stage,
        "operation_name": _merge_stage_name(merge_stage),
        "sequence": 10,
        "input_qty": sum(_safe_float(piece.get("piece_qty_per_fg"), 1.0) for piece in pieces),
        "output_qty": 1.0,
        "uom": "Units",
        "stage_contract_code": merge_stage,
        "stage_template_id": None,
        "stage_code": merge_stage,
        "metadata": {
            "phase": "G1",
            "merge_operation_only": True,
        },
    }
    return {
        "bom_key": bom_key,
        "segment_key": "fg_merge",
        "product_candidate_key": fg_product.get("candidate_key"),
        "bom_role": "fg",
        "bom_type": "normal",
        "product_qty": 1.0,
        "corr_segment_key": "fg_merge",
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": fg_product.get("corr_output_fingerprint"),
        "corr_material_signature": _hash_json({"merge_piece_products": [line["product_candidate_key"] for line in lines]}),
        "corr_route_signature": _hash_json({"merge_stage_key": merge_stage}),
        "corr_layout_signature": _hash_json({"fg_merge_layout": _split_meta(multi_piece)}),
        "corr_resolution_signature": _hash_json({"resolution": "piece_sfg_components"}),
        "lines": lines,
        "operations": [operation],
        "metadata": {
            "phase": "G1",
            "fg_merge_bom": True,
            "merge_stage_key": merge_stage,
            "raw_paper_lines_allowed": False,
            "raw_paper_line_count": 0,
        },
    }


def _blocked_h2_materialization_plan(
    payload: Dict[str, Any],
    guard: Dict[str, Any],
    *,
    mode: str = "stockable",
) -> Dict[str, Any]:
    return {
        "materialization_version": PHASE_H2_VERSION,
        "source_plan_version": "engine_v2_preview",
        "mode": mode or "stockable",
        "strategy_type": "shared_upstream_split",
        "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
        "corr_plan_signature": _plan_signature(payload),
        "fg_segment_key": "fg_merge",
        "products": [],
        "boms": [],
        "warnings": [
            {
                "code": "multi_piece_h2_guard_failed",
                "message": "Phase H2 guardrails failed; shared-upstream materialization dry-run is blocked.",
                "metadata": {"blockers": list(guard.get("blockers") or [])},
            }
        ],
        "metadata": {
            "dry_run": True,
            "phase": "H2",
            "blocked": True,
            "blockers": list(guard.get("blockers") or []),
            "writes_enabled": False,
        },
    }


def _h2_shared_upstream_product_candidate(
    payload: Dict[str, Any],
    multi_piece: Dict[str, Any],
    shared_stages: List[Dict[str, Any]],
    source_blank: Dict[str, Any],
    plan_signature: str,
) -> Dict[str, Any]:
    stage_keys = [str(stage.get("stage_key") or "") for stage in shared_stages]
    material_signature = _hash_json(
        {
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "shared_upstream_rm": _shared_upstream_rm_signature_rows(payload, multi_piece),
            "split_after_stage": multi_piece.get("split_after_stage_key"),
        }
    )
    route_signature = _hash_json(
        {
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "strategy": "shared_upstream_split",
            "shared_stage_keys": stage_keys,
            "split_after_stage": multi_piece.get("split_after_stage_key"),
        }
    )
    return {
        "candidate_key": "product:shared_upstream:sfg",
        "segment_key": "shared_upstream",
        "product_role": "sfg",
        "corr_sfg_key": _h2_manufacturing_corr_sfg_key(
            stage_key=str(multi_piece.get("split_after_stage_key") or "shared"),
            length_mm=source_blank.get("length_mm"),
            width_mm=source_blank.get("width_mm"),
            material_signature=material_signature,
            route_signature=route_signature,
        ),
        "corr_segment_key": "shared_upstream",
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": _hash_json(
            {
                "phase": "H2",
                "output": "shared_upstream_sfg",
                "source_blank": source_blank,
                "stage_keys": stage_keys,
            }
        ),
        "corr_material_signature": material_signature,
        "corr_route_signature": route_signature,
        "corr_layout_signature": _hash_json({"shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL, "source_blank": source_blank, "ups": 1}),
        "corr_resolution_signature": _hash_json(
            {"phase": "H2", "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL, "resolution": "shared_upstream_rm_inputs"}
        ),
        "stock_mode": "stockable",
        "product_type_hint": "product",
        "route_policy": "manufacture",
        "uom": "Units",
        "metadata": {
            "phase": "H2",
            "strategy_type": "shared_upstream_split",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "shared_upstream_sfg": True,
            "terminal_piece_sfg": False,
            "corr_sheet_length_mm": _safe_float(source_blank.get("length_mm"), 0.0),
            "corr_sheet_width_mm": _safe_float(source_blank.get("width_mm"), 0.0),
            "split_after_stage_key": multi_piece.get("split_after_stage_key") or "",
            "shared_stage_keys": stage_keys,
            "manufacturing_source": "shared_full_blank",
        },
    }


def _h2_shared_upstream_bom_candidate(
    payload: Dict[str, Any],
    multi_piece: Dict[str, Any],
    product: Dict[str, Any],
    shared_stages: List[Dict[str, Any]],
    source_blank: Dict[str, Any],
    plan_signature: str,
) -> Dict[str, Any]:
    bom_key = "bom:shared_upstream"
    rows = _shared_upstream_rm_rows(payload, multi_piece)
    lines = [_rm_line_candidate(row, "shared", bom_key) for row in rows]
    for line in lines:
        metadata = dict(line.get("metadata") or {})
        metadata.update(
            {
                "phase": "H2",
                "strategy_type": "shared_upstream_split",
                "piece_key": "",
                "shared_upstream_rm": True,
                "required_deckle_cm": _safe_float(source_blank.get("width_mm"), 0.0) / 10.0,
            }
        )
        line["metadata"] = metadata
        line["operation_segment_key"] = "shared_upstream"
    operations = [
        _h2_stage_operation_candidate(
            bom_key,
            product.get("segment_key") or "shared_upstream",
            stage,
            piece=None,
            shared=True,
        )
        for stage in shared_stages
    ]
    return {
        "bom_key": bom_key,
        "segment_key": product.get("segment_key"),
        "product_candidate_key": product.get("candidate_key"),
        "bom_role": "sfg",
        "bom_type": "normal",
        "product_qty": 1.0,
        "corr_segment_key": product.get("segment_key"),
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": product.get("corr_output_fingerprint"),
        "corr_material_signature": _hash_json({"phase": "H2", "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL, "shared_lines": _manufacturing_line_signature_rows(lines)}),
        "corr_route_signature": product.get("corr_route_signature") or "",
        "corr_layout_signature": product.get("corr_layout_signature") or "",
        "corr_resolution_signature": _hash_json({"phase": "H2", "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL, "rm_resolution": _line_resolution_rows(lines)}),
        "lines": lines,
        "operations": operations,
        "metadata": {
            "phase": "H2",
            "strategy_type": "shared_upstream_split",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "shared_upstream_bom": True,
            "raw_paper_lines_allowed": True,
            "source_blank": source_blank,
            "split_after_stage_key": multi_piece.get("split_after_stage_key") or "",
        },
    }


def _h2_split_output_product_candidate(
    payload: Dict[str, Any],
    multi_piece: Dict[str, Any],
    piece: Dict[str, Any],
    shared_product: Dict[str, Any],
    allocation_ratio: float,
    plan_signature: str,
) -> Dict[str, Any]:
    key = _piece_key(piece)
    split_axis = str(multi_piece.get("split_axis") or "").strip().lower()
    material_signature = _hash_json(
        {
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "source_shared_corr_sfg_key": shared_product.get("corr_sfg_key"),
            "split_axis": split_axis,
            "split_output_length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
            "split_output_width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
            "allocation_ratio": _safe_float(allocation_ratio, 0.0),
            "recipe": shared_product.get("corr_material_signature"),
        }
    )
    route_signature = _hash_json(
        {
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "strategy": "shared_upstream_split",
            "logical_split_after_stage": multi_piece.get("split_after_stage_key") or "",
            "split_axis": split_axis,
        }
    )
    return {
        "candidate_key": f"product:piece:{key}:split_output",
        "segment_key": f"piece:{key}:split_output",
        "product_role": "sfg",
        "corr_sfg_key": _h2_manufacturing_corr_sfg_key(
            stage_key="split_output",
            length_mm=piece.get("piece_length_mm"),
            width_mm=piece.get("piece_width_mm"),
            material_signature=material_signature,
            route_signature=route_signature,
            extra={
                "source_shared_corr_sfg_key": shared_product.get("corr_sfg_key"),
                "split_axis": split_axis,
                "allocation_ratio": _safe_float(allocation_ratio, 0.0),
                "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            },
        ),
        "corr_segment_key": f"piece:{key}:split_output",
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": _hash_json(
            {"phase": "H2", "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL, "piece": key, "output": "split_output_sfg"}
        ),
        "corr_material_signature": material_signature,
        "corr_route_signature": route_signature,
        "corr_layout_signature": _hash_json(
            {
                "piece": key,
                "stage": "split_output",
                "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
                "length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
                "width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
                "ups": 1,
            }
        ),
        "corr_resolution_signature": _hash_json(
            {"phase": "H2", "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL, "piece": key, "resolution": "logical_split_output"}
        ),
        "stock_mode": "stockable",
        "product_type_hint": "product",
        "route_policy": "manufacture",
        "uom": "Units",
        "metadata": {
            "phase": "H2",
            "strategy_type": "shared_upstream_split",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "piece_key": key,
            "piece_name": piece.get("piece_name") or f"Piece {key}",
            "piece_index": _safe_int(piece.get("piece_index"), 0),
            "piece_qty_per_fg": _safe_float(piece.get("piece_qty_per_fg"), 1.0),
            "piece_length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
            "piece_width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
            "corr_sheet_length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
            "corr_sheet_width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
            "stage_key": "split_output",
            "stage_name": "Split Output",
            "split_output_sfg": True,
            "terminal_piece_sfg": False,
            "source_shared_corr_sfg_key": shared_product.get("corr_sfg_key"),
            "allocation_ratio": _safe_float(allocation_ratio, 0.0),
            "split_axis": split_axis,
            "split_output_size": {
                "length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
                "width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
            },
            "manufacturing_source": "logical_slitting_output",
        },
    }


def _h2_split_output_bom_candidate(
    payload: Dict[str, Any],
    multi_piece: Dict[str, Any],
    piece: Dict[str, Any],
    split_product: Dict[str, Any],
    shared_product: Dict[str, Any],
    allocation_ratio: float,
    plan_signature: str,
) -> Dict[str, Any]:
    key = _piece_key(piece)
    bom_key = f"bom:piece:{key}:split_output"
    split_axis = str(multi_piece.get("split_axis") or "").strip().lower()
    split_mode = str(multi_piece.get("split_mode") or "").strip().lower() or "equal"
    equal_split = split_mode == "equal"
    lines: List[Dict[str, Any]] = []
    operations: List[Dict[str, Any]] = []
    product_qty = 1.0
    if equal_split:
        product_qty = 2.0
        shared_line = _previous_output_line_candidate(
            bom_key,
            shared_product,
            role_code="shared_upstream_sfg",
            qty=1.0,
            quantity_source="h4_equal_split_shared_upstream_input",
        )
        shared_line["operation_segment_key"] = "slitting"
        shared_metadata = dict(shared_line.get("metadata") or {})
        shared_metadata.update(
            {
                "phase": "H4",
                "strategy_type": "shared_upstream_split",
                "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
                "split_bom_component": True,
                "consumes_shared_upstream_sfg": True,
                "split_output_qty_produced": product_qty,
                "split_axis": split_axis,
                "allocation_ratio": allocation_ratio,
            }
        )
        shared_line["metadata"] = shared_metadata
        lines.append(shared_line)
        operations.append(_h4_slitting_operation_candidate(bom_key, split_product.get("segment_key") or "split_output"))
    split_metadata = {
        "phase": "H4" if equal_split else "H2",
        "strategy_type": "shared_upstream_split",
        "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
        "piece_key": key,
        "split_output_bom": True,
        "logical_slitting_output": not equal_split,
        "logical_slitting_only": not equal_split,
        "real_slitting_bom": bool(equal_split),
        "equal_split_only": bool(equal_split),
        "source_shared_product_candidate_key": shared_product.get("candidate_key"),
        "source_shared_corr_sfg_key": shared_product.get("corr_sfg_key"),
        "allocation_ratio": allocation_ratio,
        "split_allocation_ratio": allocation_ratio,
        "split_axis": split_axis,
        "split_output_size": {
            "length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
            "width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
        },
        "shared_upstream_group_key": shared_product.get("corr_sfg_key") or shared_product.get("candidate_key"),
        "no_recursive_shared_upstream_component": not equal_split,
    }
    return {
        "bom_key": bom_key,
        "segment_key": split_product.get("segment_key"),
        "product_candidate_key": split_product.get("candidate_key"),
        "bom_role": "sfg",
        "bom_type": "normal",
        "product_qty": product_qty,
        "corr_segment_key": split_product.get("segment_key"),
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": split_product.get("corr_output_fingerprint"),
        "corr_material_signature": _hash_json(
            {
                "phase": "H2",
                "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
                "logical_split_output": not equal_split,
                "real_slitting_bom": bool(equal_split),
                "source_shared_corr_sfg_key": shared_product.get("corr_sfg_key"),
                "allocation_ratio": allocation_ratio,
                "split_axis": split_axis,
                "split_output_size": split_metadata["split_output_size"],
                "split_output_lines": _manufacturing_line_signature_rows(lines),
            }
        ),
        "corr_route_signature": split_product.get("corr_route_signature") or "",
        "corr_layout_signature": split_product.get("corr_layout_signature") or "",
        "corr_resolution_signature": _hash_json(
            {
                "phase": "H4" if equal_split else "H2",
                "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
                "piece": "" if equal_split else key,
                "resolution": "equal_split_output_from_shared" if equal_split else "logical_split_output_from_shared",
            }
        ),
        "lines": lines,
        "operations": operations,
        "metadata": dict(split_metadata, raw_paper_lines_allowed=False),
    }


def _h4_slitting_operation_candidate(bom_key: str, segment_key: str) -> Dict[str, Any]:
    return {
        "operation_candidate_key": f"operation:{bom_key}:slitting",
        "bom_key": bom_key,
        "segment_key": segment_key,
        "operation_key": "slitting",
        "operation_name": "Slitting",
        "sequence": 15,
        "input_qty": 1.0,
        "output_qty": 2.0,
        "uom": "Units",
        "stage_contract_code": "slitting",
        "stage_template_id": None,
        "stage_code": "slitting",
        "metadata": {
            "phase": "H4",
            "strategy_type": "shared_upstream_split",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "slitting_operation": True,
            "equal_split_only": True,
            "merge_operation_only": False,
        },
    }


def _h2_branch_stage_product_candidate(
    piece: Dict[str, Any],
    stage: Dict[str, Any],
    plan_signature: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    key = _piece_key(piece)
    stage_key = str(stage.get("stage_key") or "").strip()
    material_signature = _hash_json(
        {
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "stage": stage_key,
            "piece_manufacturing": "inherited",
            "stage_rm_rows": _stage_rm_identity_rows(payload, key, stage_key),
        }
    )
    route_signature = _hash_json(
        {
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "stage": stage_key,
            "route": "h2_post_split_branch",
        }
    )
    return {
        "candidate_key": f"product:piece:{key}:branch:{stage_key}",
        "segment_key": f"piece:{key}:branch:{stage_key}",
        "product_role": "sfg",
        "corr_sfg_key": _h2_manufacturing_corr_sfg_key(
            stage_key=stage_key,
            length_mm=piece.get("piece_length_mm"),
            width_mm=piece.get("piece_width_mm"),
            material_signature=material_signature,
            route_signature=route_signature,
        ),
        "corr_segment_key": f"piece:{key}:branch:{stage_key}",
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": _hash_json(
            {
                "phase": "H2",
                "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
                "piece": key,
                "stage": stage_key,
                "output": "branch_stage_sfg",
            }
        ),
        "corr_material_signature": material_signature,
        "corr_route_signature": route_signature,
        "corr_layout_signature": _hash_json(
            {
                "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
                "piece": key,
                "stage": stage_key,
                "length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
                "width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
                "ups": 1,
            }
        ),
        "corr_resolution_signature": _hash_json(
            {"phase": "H2", "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL, "piece": key, "stage": stage_key}
        ),
        "stock_mode": "stockable",
        "product_type_hint": "product",
        "route_policy": "manufacture",
        "uom": "Units",
        "metadata": {
            "phase": "H2",
            "strategy_type": "shared_upstream_split",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "piece_key": key,
            "piece_name": piece.get("piece_name") or f"Piece {key}",
            "piece_index": _safe_int(piece.get("piece_index"), 0),
            "piece_qty_per_fg": _safe_float(piece.get("piece_qty_per_fg"), 1.0),
            "piece_length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
            "piece_width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
            "corr_sheet_length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
            "corr_sheet_width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
            "stage_key": stage_key,
            "stage_name": stage.get("stage_name") or _merge_stage_name(stage_key),
            "stage_sequence": _safe_int(stage.get("sequence"), 0),
            "post_split_branch_sfg": True,
            "terminal_piece_sfg": False,
            "manufacturing_source": "shared_upstream_split_branch",
        },
    }


def _h2_branch_stage_bom_candidate(
    payload: Dict[str, Any],
    piece: Dict[str, Any],
    stage: Dict[str, Any],
    product: Dict[str, Any],
    previous_product: Dict[str, Any],
    *,
    previous_qty: float,
    is_first_stage: bool,
    board_stage_key: str,
    plan_signature: str,
) -> Dict[str, Any]:
    key = _piece_key(piece)
    stage_key = str(stage.get("stage_key") or "").strip()
    bom_key = f"bom:piece:{key}:branch:{stage_key}"
    lines = [
        _previous_output_line_candidate(
            bom_key,
            previous_product,
            role_code="split_output_sfg" if is_first_stage else "previous_piece_stage_sfg",
            qty=previous_qty,
            quantity_source="split_output_sfg" if is_first_stage else "stage_output",
        )
    ]
    lines[-1]["metadata"].update(
        {
            "phase": "H2",
            "strategy_type": "shared_upstream_split",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "piece_key": key,
            "consumes_split_output_sfg": bool(is_first_stage),
            "split_axis": (payload.get("multi_piece") or {}).get("split_axis") or "",
            "split_output_size": {
                "length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
                "width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
            },
            "split_output_group_key": previous_product.get("corr_sfg_key") or previous_product.get("candidate_key"),
        }
    )
    for row in _g2_stage_rm_rows(
        payload,
        key,
        stage_key,
        is_first_stage=False,
        board_stage_key=board_stage_key,
    ):
        line = _rm_line_candidate(row, key, bom_key)
        line["metadata"] = dict(
            line.get("metadata") or {},
            phase="H2",
            strategy_type="shared_upstream_split",
            shared_upstream_model=PHASE_H_SHARED_UPSTREAM_MODEL,
            post_split_branch_rm=True,
        )
        lines.append(line)
    operation = _h2_stage_operation_candidate(bom_key, product.get("segment_key"), stage, piece=piece)
    material_signature = _hash_json(
        {
            "phase": "H2",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "stage": stage_key,
            "lines": _manufacturing_line_signature_rows(lines),
        }
    )
    product["corr_sfg_key"] = _h2_manufacturing_corr_sfg_key(
        stage_key=stage_key,
        length_mm=piece.get("piece_length_mm"),
        width_mm=piece.get("piece_width_mm"),
        material_signature=material_signature,
        route_signature=product.get("corr_route_signature") or "",
    )
    product["corr_material_signature"] = material_signature
    return {
        "bom_key": bom_key,
        "segment_key": product.get("segment_key"),
        "product_candidate_key": product.get("candidate_key"),
        "bom_role": "sfg",
        "bom_type": "normal",
        "product_qty": 1.0,
        "corr_segment_key": product.get("segment_key"),
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": product.get("corr_output_fingerprint"),
        "corr_material_signature": material_signature,
        "corr_route_signature": product.get("corr_route_signature") or "",
        "corr_layout_signature": product.get("corr_layout_signature") or "",
        "corr_resolution_signature": _hash_json(
            {
                "phase": "H2",
                "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
                "piece": key,
                "stage": stage_key,
                "line_resolution": _line_resolution_rows(lines),
            }
        ),
        "lines": lines,
        "operations": [operation],
        "metadata": {
            "phase": "H2",
            "strategy_type": "shared_upstream_split",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "piece_key": key,
            "stage_key": stage_key,
            "first_post_split_branch_stage": bool(is_first_stage),
            "terminal_piece_bom": False,
            "raw_paper_lines_allowed": False,
            "piece_layout_ups": 1,
        },
    }


def _h2_piece_terminal_product_candidate(
    piece: Dict[str, Any],
    route_segmentation: Dict[str, Any],
    plan_signature: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    product = _piece_product_candidate(piece, plan_signature, payload)
    key = _piece_key(piece)
    terminal_stage_key = str(route_segmentation.get("terminal_piece_stage_key") or "").strip()
    product["metadata"] = dict(
        product.get("metadata") or {},
        phase="H2",
        strategy_type="shared_upstream_split",
        shared_upstream_model=PHASE_H_SHARED_UPSTREAM_MODEL,
        terminal_stage_key=terminal_stage_key,
        terminal_piece_sfg=True,
        fg_product_name=_fg_product_name(payload),
    )
    product["corr_sfg_key"] = _h2_piece_corr_sfg_key(piece, "terminal", plan_signature, payload)
    product["corr_output_fingerprint"] = _hash_json(
        {"piece": key, "output": "terminal_sfg", "phase": "H2", "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL}
    )
    product["corr_material_signature"] = _hash_json(
        {"piece": key, "manufacturing": "inherited", "phase": "H2", "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL}
    )
    product["corr_route_signature"] = _hash_json(
        {
            "piece": key,
            "route": "h2_shared_upstream_terminal",
            "terminal_stage": terminal_stage_key,
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
        }
    )
    product["corr_resolution_signature"] = _hash_json(
        {"piece": key, "resolution": "h2_terminal_previous_stage_sfg", "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL}
    )
    return product


def _h2_piece_terminal_bom_candidate(
    piece: Dict[str, Any],
    terminal_product: Dict[str, Any],
    previous_product: Optional[Dict[str, Any]],
    route_segmentation: Dict[str, Any],
    plan_signature: str,
) -> Dict[str, Any]:
    bom = _g2_piece_terminal_bom_candidate(
        piece,
        terminal_product,
        previous_product,
        {"terminal_stage_key": route_segmentation.get("terminal_piece_stage_key") or ""},
        plan_signature,
    )
    _h2_mark_bom(bom, strategy_note="terminal_piece_after_shared_upstream_split")
    return bom


def _h2_stage_operation_candidate(
    bom_key: str,
    segment_key: str,
    stage: Dict[str, Any],
    *,
    piece: Optional[Dict[str, Any]],
    shared: bool = False,
) -> Dict[str, Any]:
    operation = _g2_stage_operation_candidate(bom_key, segment_key, stage, piece or {"piece_key": "shared"})
    operation["metadata"] = dict(
        operation.get("metadata") or {},
        phase="H2",
        strategy_type="shared_upstream_split",
        shared_upstream_model=PHASE_H_SHARED_UPSTREAM_MODEL,
        shared_upstream_operation=bool(shared),
        branch_operation=not shared,
    )
    return operation


def _h2_mark_bom(bom: Dict[str, Any], *, strategy_note: str = "") -> None:
    bom["metadata"] = dict(
        bom.get("metadata") or {},
        phase="H2",
        strategy_type="shared_upstream_split",
        shared_upstream_model=PHASE_H_SHARED_UPSTREAM_MODEL,
        h2_strategy_note=strategy_note,
    )
    bom["corr_route_signature"] = _hash_json(
        {
            "phase": "H2",
            "strategy": "shared_upstream_split",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "route": bom.get("corr_route_signature"),
            "note": strategy_note,
        }
    )
    for line in bom.get("lines") or []:
        line["metadata"] = dict(
            line.get("metadata") or {},
            phase="H2",
            strategy_type="shared_upstream_split",
            shared_upstream_model=PHASE_H_SHARED_UPSTREAM_MODEL,
        )


def _h2_manufacturing_corr_sfg_key(
    *,
    stage_key: str,
    length_mm: Any,
    width_mm: Any,
    material_signature: str,
    route_signature: str,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    stage = str(stage_key or "").strip().lower() or "stage"
    digest = _hash_json(
        {
            "identity_version": "h2_manufacturing_sfg_split_output_sfg_v4",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "stage_key": stage,
            "length_mm": _safe_float(length_mm, 0.0),
            "width_mm": _safe_float(width_mm, 0.0),
            "material_signature": material_signature or "",
            "route_signature": route_signature or "",
            "extra": dict(extra or {}),
        }
    )[:16]
    return f"engine_v2:multi_piece:h2:mfg:{stage}:{digest}"


def _h2_piece_corr_sfg_key(
    piece: Dict[str, Any],
    stage_key: str,
    plan_signature: str,
    payload: Dict[str, Any],
) -> str:
    key = _piece_key(piece)
    digest = _hash_json(
        {
            "identity_version": "h2_terminal_piece_fg_specific_split_output_sfg_v4",
            "shared_upstream_model": PHASE_H_SHARED_UPSTREAM_MODEL,
            "fg_product_name": _fg_product_name(payload),
            "spec_id": payload.get("spec_id"),
            "plan_signature": plan_signature,
            "piece_key": key,
            "stage_key": stage_key,
            "length": _safe_float(piece.get("piece_length_mm"), 0.0),
            "width": _safe_float(piece.get("piece_width_mm"), 0.0),
            "qty": _safe_float(piece.get("piece_qty_per_fg"), 1.0),
        }
    )[:12]
    return f"engine_v2:multi_piece:h2:piece:{key}:stage:{stage_key}:{digest}"


def _shared_upstream_rm_rows(payload: Dict[str, Any], multi_piece: Dict[str, Any]) -> List[Dict[str, Any]]:
    split_stage_key = str(multi_piece.get("split_after_stage_key") or "").strip()
    source_width = _safe_float(multi_piece.get("source_width_mm"), 0.0)
    source_rows = [
        deepcopy(dict(row or {}))
        for row in list((payload or {}).get("rm_requirements") or [])
        if _paper_role(row) in {"liner", "medium", "top_roll"}
    ]
    stage_matched_rows = []
    for row in source_rows:
        row_stage = str(row.get("stage_key") or "").strip()
        if row_stage and split_stage_key and not _stage_key_matches(row_stage, split_stage_key):
            continue
        stage_matched_rows.append(row)
    rows_to_group = stage_matched_rows or source_rows
    grouped: Dict[tuple, Dict[str, Any]] = {}
    for row in rows_to_group:
        role = _paper_role(row)
        if role not in {"liner", "medium", "top_roll"}:
            continue
        identity = (
            role,
            _rm_layer_no(row),
            (row.get("metadata") or {}).get("product_id") or row.get("product_id"),
            (row.get("metadata") or {}).get("gsm") or row.get("gsm"),
            (row.get("metadata") or {}).get("bf") or row.get("bf"),
            (row.get("metadata") or {}).get("flute") or row.get("flute"),
            (row.get("metadata") or {}).get("color") or row.get("color"),
        )
        qty = _safe_float(row.get("qty_per_fg"), 0.0)
        if identity in grouped:
            grouped[identity]["qty_per_fg"] = _safe_float(grouped[identity].get("qty_per_fg"), 0.0) + qty
            continue
        metadata = dict(row.get("metadata") or {})
        resolution = dict(metadata.get("resolution") or {})
        if source_width > 0.0:
            required_deckle_cm = source_width / 10.0
            _clear_stale_paper_resolution_for_deckle_override(row, metadata, resolution)
            metadata["required_deckle_cm"] = required_deckle_cm
            resolution["required_deckle_cm"] = required_deckle_cm
        metadata["resolution"] = resolution
        metadata["phase"] = "H2"
        metadata["strategy_type"] = "shared_upstream_split"
        row["metadata"] = metadata
        row["piece_key"] = ""
        row["qty_per_fg"] = qty
        grouped[identity] = row
    return list(grouped.values())


def _clear_stale_paper_resolution_for_deckle_override(
    row: Dict[str, Any],
    metadata: Dict[str, Any],
    resolution: Dict[str, Any],
) -> None:
    stale_product_id = (
        metadata.get("product_id")
        or resolution.get("product_id")
        or row.get("product_id")
        or metadata.get("writer_product_key")
    )
    if stale_product_id:
        metadata["source_product_id_before_deckle_override"] = metadata.get("product_id") or resolution.get("product_id") or row.get("product_id")
        metadata["source_writer_product_key_before_deckle_override"] = metadata.get("writer_product_key")
    for target in (metadata, resolution, row):
        target.pop("product_id", None)
        target.pop("product_tmpl_id", None)
        target.pop("writer_product_key", None)
        target.pop("product_name", None)
        target.pop("product_label", None)
    metadata["resolution_status"] = "pending_full_deckle_resolution"
    metadata["resolution_note"] = "Pending full-deckle shared upstream paper resolution"
    resolution["status"] = "pending_full_deckle_resolution"
    resolution["resolution_note"] = "Pending full-deckle shared upstream paper resolution"


def _shared_upstream_rm_signature_rows(payload: Dict[str, Any], multi_piece: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [_g2_row_identity(row) for row in _shared_upstream_rm_rows(payload, multi_piece)]


def _stage_rm_identity_rows(payload: Dict[str, Any], piece_key: str, stage_key: str) -> List[Dict[str, Any]]:
    return [
        _g2_row_identity(row)
        for row in _g2_stage_rm_rows(
            payload,
            piece_key,
            stage_key,
            is_first_stage=False,
            board_stage_key=stage_key if _is_g2_board_stage(stage_key) else "",
        )
    ]


def _h2_candidate_summary_row(
    candidate: Dict[str, Any],
    *,
    resolver_row: Optional[Dict[str, Any]],
    key_field: str,
    resolver_key_field: str,
) -> Dict[str, Any]:
    resolver_row = dict(resolver_row or {})
    key = candidate.get(key_field) or resolver_row.get(resolver_key_field)
    action = resolver_row.get("action")
    if action == "reuse_product" or action == "reuse_bom":
        reuse_status = "reuse"
    elif action == "create_product" or action == "create_bom":
        reuse_status = "create"
    elif action:
        reuse_status = "blocked"
    else:
        reuse_status = "not_resolved"
    matched = (
        resolver_row.get("existing_product_match")
        or resolver_row.get("existing_bom_match")
        or resolver_row.get("matched_existing_product")
        or resolver_row.get("matched_existing_bom")
    )
    product_match = resolver_row.get("existing_product_match") or {}
    bom_match = resolver_row.get("existing_bom_match") or {}
    return {
        "candidate_key": key,
        "parent_candidate_key": candidate.get("parent_candidate_key") or "",
        "product_role": candidate.get("product_role") or candidate.get("bom_role") or "",
        "manufacturing_signature": {
            "corr_sfg_key": candidate.get("corr_sfg_key"),
            "corr_material_signature": candidate.get("corr_material_signature"),
            "corr_route_signature": candidate.get("corr_route_signature"),
            "corr_layout_signature": candidate.get("corr_layout_signature"),
            "corr_resolution_signature": candidate.get("corr_resolution_signature"),
        },
        "reuse_status": reuse_status,
        "resolver_action": action or "",
        "resolver_reason": resolver_row.get("reason") or "",
        "matched_existing_product_or_bom": matched,
        "matched_product_id": product_match.get("id"),
        "matched_bom_id": bom_match.get("id"),
        "blockers": list(resolver_row.get("blockers") or []),
    }


def _h2_dependency_chain(
    materialization_plan: Dict[str, Any],
    resolver_dry_run: Dict[str, Any],
) -> Dict[str, Any]:
    plan = dict(materialization_plan or {})
    resolver = dict(resolver_dry_run or {})
    products = {item.get("candidate_key"): dict(item or {}) for item in list(plan.get("products") or [])}
    boms = {item.get("bom_key"): dict(item or {}) for item in list(plan.get("boms") or [])}
    product_resolver = {
        item.get("product_candidate_key"): dict(item or {})
        for item in list(resolver.get("products") or [])
    }
    bom_resolver = {
        item.get("bom_candidate_key"): dict(item or {})
        for item in list(resolver.get("boms") or [])
    }
    product_bom_by_key = {
        bom.get("product_candidate_key"): bom
        for bom in boms.values()
        if bom.get("product_candidate_key")
    }

    parent_keys_by_product: Dict[str, List[str]] = {}
    children_by_parent: Dict[str, List[str]] = {}
    for product_key, bom in product_bom_by_key.items():
        parent_keys = []
        for line in list(bom.get("lines") or []):
            if str(line.get("line_type") or "") != "previous_output":
                continue
            parent_key = str(line.get("product_candidate_key") or "")
            if not parent_key:
                continue
            parent_keys.append(parent_key)
            children_by_parent.setdefault(parent_key, [])
            if product_key not in children_by_parent[parent_key]:
                children_by_parent[parent_key].append(product_key)
        parent_keys_by_product[product_key] = parent_keys

    product_nodes = []
    for key, product in products.items():
        resolver_row = product_resolver.get(key) or {}
        match = resolver_row.get("existing_product_match") or {}
        parent_keys = parent_keys_by_product.get(key) or []
        node = {
            "candidate_key": key,
            "parent_candidate_key": parent_keys[0] if len(parent_keys) == 1 else "",
            "parent_candidate_keys": parent_keys,
            "product_role": product.get("product_role") or "",
            "candidate_type": "product",
            "manufacturing_signature": {
                "corr_sfg_key": product.get("corr_sfg_key"),
                "corr_material_signature": product.get("corr_material_signature"),
                "corr_route_signature": product.get("corr_route_signature"),
                "corr_layout_signature": product.get("corr_layout_signature"),
                "corr_resolution_signature": product.get("corr_resolution_signature"),
            },
            "reuse_create_decision": resolver_row.get("action") or "not_resolved",
            "resolver_reason": resolver_row.get("reason") or "",
            "matched_product_id": match.get("id"),
            "matched_bom_id": None,
            "blockers": list(resolver_row.get("blockers") or []),
        }
        product_nodes.append(node)

    bom_nodes = []
    for key, bom in boms.items():
        resolver_row = bom_resolver.get(key) or {}
        match = resolver_row.get("existing_bom_match") or {}
        bom_nodes.append(
            {
                "candidate_key": key,
                "parent_candidate_key": bom.get("product_candidate_key") or "",
                "parent_candidate_keys": [bom.get("product_candidate_key")] if bom.get("product_candidate_key") else [],
                "product_role": bom.get("bom_role") or "",
                "candidate_type": "bom",
                "manufacturing_signature": {
                    "corr_sfg_key": products.get(bom.get("product_candidate_key"), {}).get("corr_sfg_key"),
                    "corr_material_signature": bom.get("corr_material_signature"),
                    "corr_route_signature": bom.get("corr_route_signature"),
                    "corr_layout_signature": bom.get("corr_layout_signature"),
                    "corr_resolution_signature": bom.get("corr_resolution_signature"),
                },
                "reuse_create_decision": resolver_row.get("action") or "not_resolved",
                "resolver_reason": resolver_row.get("reason") or "",
                "matched_product_id": None,
                "matched_bom_id": match.get("id"),
                "blockers": list(resolver_row.get("blockers") or []),
            }
        )

    def product_label(product_key: str) -> str:
        product = products.get(product_key) or {}
        metadata = dict(product.get("metadata") or {})
        if metadata.get("shared_upstream_sfg"):
            return "Shared Upstream SFG"
        if metadata.get("split_output_sfg"):
            size = metadata.get("split_output_size") or {}
            length = _format_h_dimension(size.get("length_mm") or metadata.get("piece_length_mm"))
            width = _format_h_dimension(size.get("width_mm") or metadata.get("piece_width_mm"))
            return "%s Split Output SFG %sx%s" % (
                metadata.get("piece_name") or product_key,
                length,
                width,
            )
        if metadata.get("terminal_piece_sfg"):
            return "%s Terminal SFG" % (metadata.get("piece_name") or product_key)
        if metadata.get("post_split_branch_sfg"):
            stage_name = metadata.get("stage_name") or metadata.get("stage_key") or "Branch"
            return "%s %s SFG" % (metadata.get("piece_name") or product_key, stage_name)
        if product.get("product_role") == "fg":
            return "FG Merge"
        return product_key

    tree_lines = []
    roots = [
        key
        for key in products
        if key != "product:fg" and not parent_keys_by_product.get(key)
    ]
    if "product:shared_upstream:sfg" in products:
        roots = ["product:shared_upstream:sfg"] + [key for key in roots if key != "product:shared_upstream:sfg"]

    def append_tree(product_key: str, prefix: str = "", is_last: bool = True) -> None:
        connector = "" if not prefix else ("â””â”€ " if is_last else "â”œâ”€ ")
        tree_lines.append("%s%s%s" % (prefix, connector, product_label(product_key)))
        children = sorted(children_by_parent.get(product_key) or [])
        next_prefix = prefix + ("   " if is_last else "â”‚  ") if prefix else ""
        for index, child_key in enumerate(children):
            append_tree(child_key, next_prefix, index == len(children) - 1)

    for index, root_key in enumerate(roots):
        append_tree(root_key, "", index == len(roots) - 1)
    if "product:fg" in products:
        tree_lines.append("")
        tree_lines.append("FG Merge")
        for parent_key in parent_keys_by_product.get("product:fg") or []:
            tree_lines.append("â””â”€ %s" % product_label(parent_key))

    return {
        "tree_text_lines": tree_lines,
        "product_nodes": product_nodes,
        "bom_nodes": bom_nodes,
        "all_candidates": product_nodes + bom_nodes,
        "edges": [
            {"parent_candidate_key": parent, "child_candidate_key": child}
            for child, parents in parent_keys_by_product.items()
            for parent in parents
        ],
    }


def _active_route_stage_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    preview_model = dict(payload.get("phase5_preview_model") or {})
    rows = []
    for index, stage in enumerate(list(preview_model.get("stages") or [])):
        stage = dict(stage or {})
        key = str(stage.get("stage_code") or stage.get("stage_key") or "").strip()
        if not key:
            continue
        rows.append(
            {
                "stage_key": key,
                "stage_code": key,
                "stage_name": stage.get("stage_name") or stage.get("name") or _merge_stage_name(key),
                "stage_type": stage.get("stage_type") or "",
                "execution_type": stage.get("execution_type") or "",
                "capability_code": stage.get("capability_code") or "",
                "sequence": _safe_int(stage.get("sequence"), (index + 1) * 10),
                "stage_template_id": stage.get("stage_template_id"),
                "stage_contract_code": stage.get("stage_contract_code") or key,
                "output_kind": stage.get("output_kind") or stage.get("output_stock_behavior") or "",
                "min_sheet_length_mm": stage.get("min_sheet_length_mm") or stage.get("min_length_mm"),
                "max_sheet_length_mm": stage.get("max_sheet_length_mm") or stage.get("max_length_mm"),
                "min_sheet_width_mm": stage.get("min_sheet_width_mm") or stage.get("min_width_mm"),
                "max_sheet_width_mm": stage.get("max_sheet_width_mm") or stage.get("max_width_mm"),
            }
        )
    if rows:
        return _dedupe_stages(rows)

    shadow = dict(payload.get("phase8_shadow_runtime") or {})
    for index, stage in enumerate(list(shadow.get("stage_contexts") or [])):
        stage = dict(stage or {})
        key = str(stage.get("stage_code") or stage.get("stage_key") or "").strip()
        if not key:
            continue
        rows.append(
            {
                "stage_key": key,
                "stage_code": key,
                "stage_name": stage.get("stage_name") or stage.get("name") or _merge_stage_name(key),
                "stage_type": stage.get("stage_type") or "",
                "execution_type": stage.get("execution_type") or "",
                "capability_code": stage.get("capability_code") or "",
                "sequence": _safe_int(stage.get("sequence"), (index + 1) * 10),
                "stage_template_id": stage.get("stage_template_id"),
                "stage_contract_code": stage.get("stage_contract_code") or key,
                "output_kind": (stage.get("output_descriptor") or {}).get("kind") or stage.get("output_kind") or "",
                "min_sheet_length_mm": stage.get("min_sheet_length_mm") or stage.get("min_length_mm"),
                "max_sheet_length_mm": stage.get("max_sheet_length_mm") or stage.get("max_length_mm"),
                "min_sheet_width_mm": stage.get("min_sheet_width_mm") or stage.get("min_width_mm"),
                "max_sheet_width_mm": stage.get("max_sheet_width_mm") or stage.get("max_width_mm"),
            }
        )
    return _dedupe_stages(rows)


def _dedupe_stages(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for row in sorted(rows, key=lambda item: (_safe_int(item.get("sequence"), 0), str(item.get("stage_key") or ""))):
        key = str(row.get("stage_key") or "")
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _is_g2_branch_stage(stage: Dict[str, Any]) -> bool:
    key = str(stage.get("stage_key") or "").strip().lower()
    stage_type = str(stage.get("stage_type") or "").strip().lower()
    execution_type = str(stage.get("execution_type") or "").strip().lower()
    capability = str(stage.get("capability_code") or "").strip().lower()
    if key in {"seaming", "jointing", "gluing", "stitching", "assembly", "merge", "fg"}:
        return False
    if execution_type == "material":
        return True
    if stage_type in {"corrugation", "lamination", "conversion", "cutting", "printing", "diecut"}:
        return True
    if capability in {"corrugation", "board", "lamination", "cutting", "printing", "diecut"}:
        return True
    return key in {"2ply", "board", "diecut", "cutting", "printing"}


def _g2_piece_2ply_sets(payload: Dict[str, Any], piece: Dict[str, Any], stage: Dict[str, Any]) -> List[Dict[str, Any]]:
    stage_key = str(stage.get("stage_key") or stage.get("stage_code") or "").strip()
    if not _is_g2_2ply_stage(stage_key):
        return []
    piece_key = _piece_key(piece)
    rows = []
    for row in list((payload or {}).get("rm_requirements") or []):
        row = deepcopy(dict(row or {}))
        row_piece = _rm_piece_key(row)
        if row_piece and row_piece != piece_key:
            continue
        if _paper_role(row) not in {"liner", "medium"}:
            continue
        row_stage = str(row.get("stage_key") or "").strip()
        if row_stage and not _stage_key_matches(row_stage, stage_key):
            continue
        rows.append(row)
    rows = _g2_expand_collapsed_rows_from_spec_stack(payload, piece_key, stage_key, rows)
    rows = _g2_apply_piece_resolution_to_2ply_rows(payload, piece_key, rows)

    mediums = sorted(
        [row for row in rows if _paper_role(row) == "medium"],
        key=lambda row: (_rm_layer_no(row) or 9999, str(row.get("product_key") or "")),
    )
    liners = sorted(
        [row for row in rows if _paper_role(row) == "liner"],
        key=lambda row: (_rm_layer_no(row) or 9999, str(row.get("product_key") or "")),
    )
    if not mediums or not liners:
        return []

    used_liner_ids = set()
    groups: Dict[str, Dict[str, Any]] = {}
    ordered_groups: List[Dict[str, Any]] = []
    for medium in mediums:
        liner = _g2_matching_liner_for_medium(medium, liners, used_liner_ids)
        if not liner:
            continue
        used_liner_ids.add(id(liner))
        signature = _g2_2ply_set_signature(liner, medium)
        group = groups.get(signature)
        if not group:
            group = {
                "set_key": f"set{len(ordered_groups) + 1}",
                "signature": signature,
                "recipe_signature": signature,
                "qty": 0.0,
                "representative_rows": [deepcopy(liner), deepcopy(medium)],
                "layer_pairs": [],
            }
            groups[signature] = group
            ordered_groups.append(group)
        group["qty"] = _safe_float(group.get("qty"), 0.0) + 1.0
        group["layer_pairs"].append([_rm_layer_no(liner), _rm_layer_no(medium)])

    if not ordered_groups:
        return []
    return ordered_groups


def _g2_expand_collapsed_rows_from_spec_stack(
    payload: Dict[str, Any],
    piece_key: str,
    stage_key: str,
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    stack_lines = _g2_effective_stack_lines_for_piece(payload, piece_key)
    if not stack_lines:
        return rows
    stack_medium_count = len([line for line in stack_lines if str(line.get("role") or "").strip().lower() == "medium"])
    row_medium_count = len({_rm_layer_no(row) or str(row.get("product_key") or "") for row in rows if _paper_role(row) == "medium"})
    if row_medium_count >= stack_medium_count:
        return rows

    paper_by_key = _g2_paper_resolution_by_role_source_layer(payload, piece_key=piece_key)
    prototype_by_role = _g2_prototype_rows_by_role(rows)
    role_counts = {
        "liner": len([line for line in stack_lines if str(line.get("role") or "").strip().lower() == "liner"]),
        "medium": stack_medium_count,
    }
    expanded: List[Dict[str, Any]] = []
    for line in stack_lines:
        role = str(line.get("role") or "").strip().lower()
        layer_no = _safe_int(line.get("layer_no"), 0)
        paper = _g2_find_piece_paper_resolution(paper_by_key, line, role)
        prototype = dict(prototype_by_role.get(role) or {})
        metadata = dict(prototype.get("metadata") or {})
        resolution = dict(metadata.get("resolution") or {})
        if paper:
            paper_product_id = paper.get("product_id")
            paper_product_name = paper.get("product_name")
            if _g2_resolved_product_conflicts_with_stack_line(
                paper_product_name,
                line.get("gsm") or metadata.get("gsm"),
                line.get("bf") or metadata.get("bf"),
            ):
                paper_product_id = None
                paper_product_name = None
                metadata.pop("product_id", None)
                metadata.pop("product_name", None)
                metadata.pop("product_tmpl_id", None)
                metadata.pop("writer_product_key", None)
                resolution.pop("product_id", None)
                resolution.pop("product_name", None)
            resolution.update(
                {
                    "product_name": paper_product_name,
                    "required_deckle_cm": paper.get("required_deckle_cm"),
                    "status": paper.get("status") or paper.get("resolution_status"),
                }
            )
            metadata["product_name"] = paper_product_name or metadata.get("product_name")
            metadata["force_live_paper_resolution"] = True
            metadata["source_kind"] = "piece_effective_recipe"
            metadata.pop("product_id", None)
            metadata.pop("product_tmpl_id", None)
            metadata.pop("writer_product_key", None)
            resolution.pop("product_id", None)
            resolution.pop("product_tmpl_id", None)
        metadata.update(
            {
                "piece_key": piece_key,
                "semantic_role": role,
                "layer_no": layer_no,
                "source_layer_no": layer_no,
                "gsm": line.get("gsm") or metadata.get("gsm"),
                "bf": line.get("bf") or metadata.get("bf"),
                "color": line.get("color") or metadata.get("color"),
                "flute": _normalize_flute_code(
                    line.get("flute_code")
                    or line.get("flute")
                    or line.get("flute_type_code")
                    or line.get("flute_type")
                    or metadata.get("flute_code")
                    or metadata.get("flute")
                ),
                "flute_code": _normalize_flute_code(
                    line.get("flute_code")
                    or line.get("flute")
                    or line.get("flute_type_code")
                    or line.get("flute_type")
                    or metadata.get("flute_code")
                    or metadata.get("flute")
                ),
                "resolution": resolution,
            }
        )
        qty = _safe_float(paper.get("qty_per_fg"), 0.0)
        if qty <= 0.0:
            qty = _safe_float(prototype.get("qty_per_fg"), 0.0)
            divisor = role_counts.get(role) or 1
            if qty > 0.0 and divisor > 1:
                qty = qty / float(divisor)
        expanded.append(
            {
                "product_key": f"piece:{piece_key}:stack:{role}:{layer_no}",
                "role": role,
                "stage_key": stage_key,
                "layer_no": layer_no,
                "qty_per_fg": qty,
                "qty_uom": paper.get("qty_uom") or paper.get("uom_name") or prototype.get("qty_uom") or "kg",
                "consumption_type": prototype.get("consumption_type") or "area",
                "metadata": metadata,
            }
        )
    return expanded


def _g2_apply_piece_resolution_to_2ply_rows(
    payload: Dict[str, Any],
    piece_key: str,
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    paper_by_key = _g2_paper_resolution_by_role_source_layer(payload, piece_key=piece_key)
    if not paper_by_key:
        return rows
    enriched = []
    for row in rows:
        row = deepcopy(dict(row or {}))
        role = _paper_role(row)
        if role not in {"liner", "medium"}:
            enriched.append(row)
            continue
        layer_no = _rm_layer_no(row)
        paper = _g2_find_piece_paper_resolution(paper_by_key, row, role)
        if not paper:
            enriched.append(row)
            continue
        metadata = dict(row.get("metadata") or {})
        resolution = dict(metadata.get("resolution") or {})
        # J3 piece-specific RM rows are the authoritative source for custom
        # piece recipes. Parent rm_requirements can still carry old inherited
        # product_ids, so the matched piece/layer paper resolution must win.
        product_id = None
        product_name = paper.get("product_name") or metadata.get("product_name") or resolution.get("product_name")
        row_gsm = metadata.get("gsm") or (metadata.get("preview_metadata") or {}).get("gsm") or resolution.get("gsm")
        row_bf = metadata.get("bf") or (metadata.get("preview_metadata") or {}).get("bf") or resolution.get("bf")
        if _g2_resolved_product_conflicts_with_stack_line(product_name, row_gsm, row_bf):
            product_id = None
            product_name = None
            metadata.pop("product_id", None)
            metadata.pop("product_name", None)
            metadata.pop("product_tmpl_id", None)
            metadata.pop("writer_product_key", None)
            resolution.pop("product_id", None)
            resolution.pop("product_name", None)
        metadata["force_live_paper_resolution"] = True
        metadata["source_kind"] = "piece_effective_recipe"
        metadata.pop("product_id", None)
        metadata.pop("product_tmpl_id", None)
        metadata.pop("writer_product_key", None)
        resolution.pop("product_id", None)
        resolution.pop("product_tmpl_id", None)
        if product_name:
            metadata["product_name"] = product_name
            resolution["product_name"] = product_name
        required_deckle_cm = (
            paper.get("required_deckle_cm")
            or metadata.get("required_deckle_cm")
            or resolution.get("required_deckle_cm")
        )
        if required_deckle_cm:
            metadata["required_deckle_cm"] = required_deckle_cm
            resolution["required_deckle_cm"] = required_deckle_cm
        if paper.get("uom_id") and not metadata.get("uom_id"):
            metadata["uom_id"] = paper.get("uom_id")
        if paper.get("uom_name") and not metadata.get("uom_name"):
            metadata["uom_name"] = paper.get("uom_name")
        paper_qty = _safe_float(paper.get("qty_per_fg"), 0.0)
        if paper_qty > 0.0:
            row["qty_per_fg"] = paper_qty
            metadata["writer_qty"] = paper_qty
        paper_uom = paper.get("qty_uom") or paper.get("uom_name")
        if paper_uom:
            row["qty_uom"] = paper_uom
            metadata["writer_uom"] = paper_uom
        metadata["resolution_status"] = "pending_live_paper_resolution"
        resolution["status"] = "pending_live_paper_resolution"
        metadata["resolution_note"] = "Piece-effective paper row will be resolved at materialization time"
        resolution["resolution_note"] = "Piece-effective paper row will be resolved at materialization time"
        metadata["resolution"] = resolution
        row["metadata"] = metadata
        enriched.append(row)
    return enriched


def _g2_resolved_product_conflicts_with_stack_line(product_name: Any, gsm: Any, bf: Any) -> bool:
    text = str(product_name or "").strip()
    if not text:
        return False
    requested_gsm = _safe_float(gsm, 0.0)
    requested_bf = _safe_float(bf, 0.0)
    if requested_gsm <= 0.0 and requested_bf <= 0.0:
        return False
    numbers = [float(match) for match in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", text)]
    if requested_gsm > 0.0:
        likely_gsm = [
            value for value in numbers
            if 80.0 <= value <= 500.0
        ]
        if likely_gsm and all(abs(value - requested_gsm) > 0.001 for value in likely_gsm):
            return True
    if requested_bf > 0.0:
        likely_bf = [
            value for value in numbers
            if 5.0 <= value <= 80.0
        ]
        if likely_bf and all(abs(value - requested_bf) > 0.001 for value in likely_bf):
            # A product name may also contain deckle in this range, so only BF
            # conflict on its own is too noisy. GSM conflict above is decisive.
            return False
    return False


def _g2_normalize_product_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _g2_effective_stack_lines_for_piece(payload: Dict[str, Any], piece_key: str) -> List[Dict[str, Any]]:
    effective_piece = _effective_piece_manufacturing_by_key(payload).get(str(piece_key or ""), {})
    stack_lines = list((effective_piece.get("effective_stack_lines") or []))
    if not stack_lines:
        stack_lines = list((effective_piece.get("stack") or {}).get("effective_stack_lines") or [])
    if not stack_lines:
        stack_lines = list((payload or {}).get("spec_stack_lines") or [])
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(stack_lines, start=1):
        item = dict(item or {})
        role = _g2_normalize_stack_paper_role(item.get("role"))
        if role not in {"liner", "medium"}:
            continue
        row = dict(item)
        row["role"] = role
        source_layer_no = _safe_int(
            row.get("source_layer_no")
            or row.get("layer_no")
            or row.get("stack_layer_no"),
            0,
        )
        if source_layer_no <= 0:
            source_layer_no = index
        row["source_layer_no"] = source_layer_no
        row["layer_no"] = source_layer_no
        normalized_flute = _normalize_flute_code(
            row.get("flute_code")
            or row.get("flute")
            or row.get("flute_type_code")
            or row.get("flute_type")
        )
        if normalized_flute:
            row["flute"] = normalized_flute
            row["flute_code"] = normalized_flute
        result.append(row)
    return result


def _g2_normalize_stack_paper_role(role: Any) -> str:
    role_norm = str(role or "").strip().lower()
    if role_norm in {"outer_liner", "inner_liner", "center_liner", "liner"}:
        return "liner"
    if role_norm in {"medium", "fluting"}:
        return "medium"
    return role_norm


def _g2_paper_resolution_by_role_source_layer(
    payload: Dict[str, Any],
    *,
    piece_key: str = "",
) -> Dict[tuple, Dict[str, Any]]:
    result: Dict[tuple, Dict[str, Any]] = {}
    normalized_piece = _normalize_piece_key(piece_key)

    def _add_resolution(item: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> None:
        item = dict(item or {})
        metadata = dict(metadata or item.get("metadata") or {})
        row_piece = _normalize_piece_key(item.get("piece_key") or metadata.get("piece_key"))
        if normalized_piece and row_piece and row_piece != normalized_piece:
            return
        role = str(item.get("role") or metadata.get("role") or metadata.get("type") or metadata.get("semantic_role") or "").strip().lower()
        if role == "top_roll":
            role = "liner"
        if role not in {"liner", "medium"}:
            return
        resolution = dict(metadata.get("resolution") or {})
        source_layer = _safe_int(
            item.get("source_layer_no")
            or metadata.get("source_layer_no")
            or metadata.get("stack_layer_no"),
            0,
        )
        effective_layer = _safe_int(
            item.get("layer_no")
            or metadata.get("layer_no")
            or source_layer,
            0,
        )
        product_id = item.get("product_id") or metadata.get("product_id") or resolution.get("product_id")
        display_product_name = item.get("product_name") or item.get("display_product") or item.get("product")
        metadata_product_name = metadata.get("product_name") or metadata.get("resolution_product_name") or resolution.get("product_name")
        product_name = display_product_name or metadata_product_name
        if (
            display_product_name
            and metadata_product_name
            and _g2_normalize_product_text(display_product_name) != _g2_normalize_product_text(metadata_product_name)
        ):
            product_id = None
        required_deckle_cm = (
            item.get("required_deckle_cm")
            or metadata.get("required_deckle_cm")
            or resolution.get("required_deckle_cm")
        )
        resolved = {
            "product_id": product_id,
            "product_name": product_name,
            "required_deckle_cm": required_deckle_cm,
            "status": item.get("status") or item.get("resolution_status") or metadata.get("resolution_status") or resolution.get("status"),
            "qty_per_fg": item.get("qty_per_fg"),
            "qty_uom": item.get("qty_uom") or item.get("uom") or metadata.get("uom_name"),
            "uom_id": item.get("uom_id") or metadata.get("uom_id") or resolution.get("uom_id"),
            "uom_name": item.get("uom_name") or metadata.get("uom_name") or resolution.get("uom_name"),
            "gsm": item.get("gsm") or metadata.get("gsm") or resolution.get("gsm"),
            "bf": item.get("bf") or metadata.get("bf") or resolution.get("bf"),
            "color": item.get("color") or metadata.get("color") or resolution.get("color"),
            "flute": _normalize_flute_code(
                item.get("flute")
                or item.get("flute_code")
                or metadata.get("flute")
                or metadata.get("flute_code")
                or resolution.get("flute")
                or resolution.get("flute_code")
            ),
        }
        if product_id or required_deckle_cm:
            for layer_key in (source_layer, effective_layer):
                if layer_key > 0:
                    result[(role, layer_key)] = resolved
            result.setdefault((role, 0), resolved)

    for item in list((payload or {}).get("paper_resolution") or []):
        _add_resolution(dict(item or {}))
    for row in list(((payload or {}).get("piece_specific_rm_breakdown") or {}).get("rows") or []):
        row = dict(row or {})
        _add_resolution(row, metadata=dict(row.get("metadata") or {}))
    return result


def _g2_find_piece_paper_resolution(
    paper_by_key: Dict[tuple, Dict[str, Any]],
    row: Dict[str, Any],
    role: str,
) -> Dict[str, Any]:
    row = dict(row or {})
    role = str(role or _paper_role(row) or "").strip().lower()
    if role not in {"liner", "medium"}:
        return {}

    metadata = dict(row.get("metadata") or {})
    row_layers = []
    for value in (
        row.get("source_layer_no"),
        metadata.get("source_layer_no"),
        row.get("layer_no"),
        metadata.get("layer_no"),
        row.get("stack_layer_no"),
        metadata.get("stack_layer_no"),
        _rm_layer_no(row),
    ):
        layer_no = _safe_int(value, 0)
        if layer_no > 0 and layer_no not in row_layers:
            row_layers.append(layer_no)

    for layer_no in row_layers:
        paper = paper_by_key.get((role, layer_no))
        if paper:
            return paper

    matching_candidates = []
    role_candidates = []
    for (candidate_role, layer_no), paper in paper_by_key.items():
        if candidate_role != role or layer_no <= 0:
            continue
        role_candidates.append(paper)
        if _g2_piece_paper_resolution_matches_row(paper, row):
            matching_candidates.append(paper)

    unique_matches = _g2_unique_paper_resolution_candidates(matching_candidates)
    if len(unique_matches) == 1:
        return unique_matches[0]

    unique_role_candidates = _g2_unique_paper_resolution_candidates(role_candidates)
    if len(unique_role_candidates) == 1:
        return unique_role_candidates[0]

    return {}


def _g2_unique_paper_resolution_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: List[Dict[str, Any]] = []
    seen = set()
    for candidate in candidates:
        key = (
            candidate.get("product_id"),
            _g2_normalize_product_text(candidate.get("product_name")),
            _safe_float(candidate.get("required_deckle_cm"), 0.0),
            _safe_float(candidate.get("qty_per_fg"), 0.0),
            str(candidate.get("qty_uom") or candidate.get("uom_name") or ""),
            _safe_float(candidate.get("gsm"), 0.0),
            _safe_float(candidate.get("bf"), 0.0),
            _g2_normalize_product_text(candidate.get("color")),
            _normalize_flute_code(candidate.get("flute")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _g2_piece_paper_resolution_matches_row(paper: Dict[str, Any], row: Dict[str, Any]) -> bool:
    identity = _g2_row_identity(row)
    for numeric_field in ("gsm", "bf"):
        expected = _safe_float(identity.get(numeric_field), 0.0)
        candidate = _safe_float(paper.get(numeric_field), 0.0)
        if expected > 0.0 and candidate > 0.0 and abs(expected - candidate) > 0.001:
            return False

    expected_color = _g2_normalize_product_text(identity.get("color"))
    candidate_color = _g2_normalize_product_text(paper.get("color"))
    if expected_color and candidate_color and expected_color != candidate_color:
        return False

    expected_flute = _normalize_flute_code(identity.get("flute"))
    candidate_flute = _normalize_flute_code(paper.get("flute"))
    if expected_flute and candidate_flute and expected_flute != candidate_flute:
        return False

    return True


def _g2_prototype_rows_by_role(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        role = _paper_role(dict(row or {}))
        if role in {"liner", "medium"} and role not in result:
            result[role] = dict(row or {})
    return result


def _g2_matching_liner_for_medium(
    medium: Dict[str, Any],
    liners: List[Dict[str, Any]],
    used_liner_ids: set,
) -> Optional[Dict[str, Any]]:
    medium_layer = _rm_layer_no(medium)
    candidates = [row for row in liners if id(row) not in used_liner_ids]
    if not candidates:
        return None
    if medium_layer > 0:
        for target in (medium_layer + 1, medium_layer - 1):
            exact = [row for row in candidates if _rm_layer_no(row) == target]
            if exact:
                return exact[0]
        return sorted(
            candidates,
            key=lambda row: (
                abs((_rm_layer_no(row) or medium_layer) - medium_layer),
                _rm_layer_no(row) or 9999,
            ),
        )[0]
    return candidates[0]


def _g2_2ply_set_signature(liner: Dict[str, Any], medium: Dict[str, Any]) -> str:
    return _hash_json(
        {
            "liner": _g2_row_identity(liner),
            "medium": _g2_row_identity(medium),
            "signature_version": "j3_piece_effective_stack_v3",
        }
    )


def _g2_row_identity(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    preview_metadata = dict(metadata.get("preview_metadata") or {})
    resolution = dict(metadata.get("resolution") or {})
    product_id = metadata.get("product_id") or resolution.get("product_id") or row.get("product_id")
    force_live = bool(metadata.get("force_live_paper_resolution"))
    return {
        "role": _paper_role(row),
        "product_id": product_id,
        "product_key": "" if (product_id or force_live) else (metadata.get("writer_product_key") or row.get("product_key")),
        "gsm": metadata.get("gsm") or preview_metadata.get("gsm") or resolution.get("gsm"),
        "bf": metadata.get("bf") or preview_metadata.get("bf") or resolution.get("bf"),
        "color": metadata.get("color") or preview_metadata.get("color") or resolution.get("color"),
        "flute": _normalize_flute_code(
            metadata.get("flute_code")
            or metadata.get("flute")
            or preview_metadata.get("flute_code")
            or preview_metadata.get("flute")
            or resolution.get("flute_code")
            or resolution.get("flute")
        ),
        "required_deckle_cm": metadata.get("required_deckle_cm") or resolution.get("required_deckle_cm"),
        "uom": row.get("qty_uom") or metadata.get("writer_uom"),
    }


def _g2_piece_2ply_set_product_candidate(
    piece: Dict[str, Any],
    stage: Dict[str, Any],
    group: Dict[str, Any],
    plan_signature: str,
) -> Dict[str, Any]:
    product = _g2_piece_stage_product_candidate(piece, stage, plan_signature)
    key = _piece_key(piece)
    stage_key = str(stage.get("stage_key") or "").strip()
    set_key = str(group.get("set_key") or "set1")
    use_explicit_set_key = _safe_int(group.get("set_count"), 1) > 1 or set_key != "set1"
    if use_explicit_set_key:
        product["candidate_key"] = f"product:piece:{key}:stage:{stage_key}:{set_key}"
        product["segment_key"] = f"piece:{key}:stage:{stage_key}:{set_key}"
        product["corr_segment_key"] = product["segment_key"]
    identity_stage_key = f"{stage_key}:{set_key}" if use_explicit_set_key else stage_key
    product["corr_sfg_key"] = _g2_manufacturing_corr_sfg_key(
        stage_key=identity_stage_key,
        piece=piece,
        material_signature=group.get("signature") or "",
        route_signature=_hash_json({"stage": stage_key, "route": "g2_routed_piece_2ply"}),
        extra={"set_explicit": bool(use_explicit_set_key)},
    )
    product["corr_output_fingerprint"] = _hash_json(
        {"piece": key, "stage": stage_key, "set": set_key if use_explicit_set_key else None}
    )
    product["corr_material_signature"] = group.get("signature") or product.get("corr_material_signature")
    metadata = dict(
        product.get("metadata") or {},
        piece_2ply_set=True,
        set_qty=_safe_float(group.get("qty"), 1.0),
        grouped_set_qty=_safe_float(group.get("qty"), 1.0),
        layer_pairs=list(group.get("layer_pairs") or []),
    )
    if use_explicit_set_key:
        metadata["set_key"] = set_key
    product["metadata"] = metadata
    return product


def _g2_piece_2ply_set_bom_candidate(
    payload: Dict[str, Any],
    piece: Dict[str, Any],
    stage: Dict[str, Any],
    group: Dict[str, Any],
    product: Dict[str, Any],
    plan_signature: str,
) -> Dict[str, Any]:
    key = _piece_key(piece)
    stage_key = str(stage.get("stage_key") or "").strip()
    set_key = str(group.get("set_key") or "set1")
    use_explicit_set_key = _safe_int(group.get("set_count"), 1) > 1 or set_key != "set1"
    bom_key = f"bom:piece:{key}:stage:{stage_key}:{set_key}" if use_explicit_set_key else f"bom:piece:{key}:stage:{stage_key}"
    representative_rows = list(group.get("representative_rows") or [])
    extra_rows = _g2_first_stage_extra_rows(payload, key, stage_key, representative_rows)
    lines = [_rm_line_candidate(row, key, bom_key) for row in [*representative_rows, *extra_rows]]
    operation = _g2_stage_operation_candidate(bom_key, product.get("segment_key"), stage, piece)
    stage_recipe_identity = _g2_stage_recipe_identity(piece, stage_key)
    stage_material_signature = _hash_json(
        {
            "stage": stage_key,
            "set": set_key if use_explicit_set_key else "",
            "recipe": stage_recipe_identity,
            "lines": _manufacturing_line_signature_rows(lines),
        }
    )
    stage_route_signature = _hash_json({"stage": stage_key, "set": set_key, "route": "g2_routed_piece"})
    identity_stage_key = f"{stage_key}:{set_key}" if use_explicit_set_key else stage_key
    product["corr_sfg_key"] = _g2_manufacturing_corr_sfg_key(
        stage_key=identity_stage_key,
        piece=piece,
        material_signature=stage_material_signature,
        route_signature=stage_route_signature,
        extra={"set_explicit": bool(use_explicit_set_key)},
    )
    product["corr_material_signature"] = stage_material_signature
    product["corr_route_signature"] = stage_route_signature
    metadata = {
        "phase": "G2",
        "strategy_type": "independent_routed_piece",
        "piece_key": key,
        "stage_key": stage_key,
        "first_piece_stage": True,
        "terminal_piece_bom": False,
        "raw_paper_lines_allowed": True,
        "piece_layout_ups": 1,
        "piece_2ply_set": True,
        "set_qty": _safe_float(group.get("qty"), 1.0),
        "grouped_set_qty": _safe_float(group.get("qty"), 1.0),
        "layer_pairs": list(group.get("layer_pairs") or []),
    }
    metadata.update(_piece_candidate_recipe_metadata(piece))
    if use_explicit_set_key:
        metadata["set_key"] = set_key
    return {
        "bom_key": bom_key,
        "segment_key": product.get("segment_key"),
        "product_candidate_key": product.get("candidate_key"),
        "bom_role": "sfg",
        "bom_type": "normal",
        "product_qty": 1.0,
        "corr_segment_key": product.get("segment_key"),
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": product.get("corr_output_fingerprint"),
        "corr_material_signature": stage_material_signature,
        "corr_route_signature": stage_route_signature,
        "corr_layout_signature": product.get("corr_layout_signature") or "",
        "corr_resolution_signature": _hash_json(
            {
                "stage": stage_key,
                "set": set_key,
                "recipe": stage_recipe_identity,
                "rm_resolution": _line_resolution_rows(lines),
            }
        ),
        "lines": lines,
        "operations": [operation],
        "metadata": metadata,
    }


def _g2_first_stage_extra_rows(
    payload: Dict[str, Any],
    piece_key: str,
    stage_key: str,
    representative_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    representative_keys = {
        (
            row.get("product_key"),
            _paper_role(row),
            _rm_layer_no(row),
        )
        for row in representative_rows
    }
    rows = []
    for row in list((payload or {}).get("rm_requirements") or []):
        row = deepcopy(dict(row or {}))
        row_piece = _rm_piece_key(row)
        if row_piece and row_piece != piece_key:
            continue
        if str(row.get("role") or "").strip().lower() != "top_roll":
            continue
        row_stage = str(row.get("stage_key") or "").strip()
        if row_stage and not _stage_key_matches(row_stage, stage_key):
            continue
        identity = (row.get("product_key"), _paper_role(row), _rm_layer_no(row))
        if identity in representative_keys:
            continue
        rows.append(row)
    return rows


def _stage_key_matches(stage_key: str, expected: str) -> bool:
    left = str(stage_key or "").strip().lower().replace("_", "").replace("-", "")
    right = str(expected or "").strip().lower().replace("_", "").replace("-", "")
    if not left or not right:
        return False
    aliases = {
        "seaming": {"seaming", "jointing", "seamingjointing"},
        "jointing": {"seaming", "jointing", "seamingjointing"},
        "gluing": {"gluing", "glue"},
        "stitching": {"stitching", "stitch"},
        "assembly": {"assembly", "assemble"},
    }
    if left == right:
        return True
    return right in aliases.get(left, set()) or left in aliases.get(right, set())


def _g2_piece_stage_product_candidate(piece: Dict[str, Any], stage: Dict[str, Any], plan_signature: str) -> Dict[str, Any]:
    key = _piece_key(piece)
    stage_key = str(stage.get("stage_key") or "").strip()
    segment_key = f"piece:{key}:stage:{stage_key}"
    stage_recipe_identity = _g2_stage_recipe_identity(piece, stage_key)
    candidate = {
        "candidate_key": f"product:piece:{key}:stage:{stage_key}",
        "segment_key": segment_key,
        "product_role": "sfg",
        "corr_sfg_key": _g2_manufacturing_corr_sfg_key(
            stage_key=stage_key,
            piece=piece,
            material_signature=_hash_json({"stage": stage_key, "recipe": stage_recipe_identity, "material": "pending"}),
            route_signature=_hash_json({"stage": stage_key, "route": "g2_routed_piece"}),
        ),
        "corr_segment_key": segment_key,
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": _hash_json({"piece": key, "stage": stage_key, "output": "stage_sfg"}),
        "corr_material_signature": _hash_json({"stage": stage_key, "recipe": stage_recipe_identity}),
        "corr_route_signature": _hash_json({"piece": key, "stage": stage_key, "route": "g2_routed_piece"}),
        "corr_layout_signature": _hash_json(
            {
                "stage": stage_key,
                "length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
                "width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
                "ups": 1,
            }
        ),
        "corr_resolution_signature": _hash_json(
            {
                "stage": stage_key,
                "resolution": "effective_piece_recipe",
                "recipe": stage_recipe_identity,
            }
        ),
        "stock_mode": "stockable",
        "product_type_hint": "product",
        "route_policy": "manufacture",
        "uom": "Units",
        "metadata": {
            "phase": "G2",
            "strategy_type": "independent_routed_piece",
            "piece_key": key,
            "piece_name": piece.get("piece_name") or f"Piece {key}",
            "piece_qty_per_fg": _safe_float(piece.get("piece_qty_per_fg"), 1.0),
            "piece_length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
            "piece_width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
            "stage_key": stage_key,
            "stage_name": stage.get("stage_name") or _merge_stage_name(stage_key),
            "stage_sequence": _safe_int(stage.get("sequence"), 0),
            "terminal_piece_sfg": False,
            "manufacturing_source": "piece_effective_recipe",
        },
    }
    candidate["metadata"].update(_piece_candidate_recipe_metadata(piece))
    candidate["corr_material_signature"] = _hash_json(
        {
            "stage": stage_key,
            "recipe": stage_recipe_identity,
        }
    )
    candidate["corr_resolution_signature"] = _hash_json(
        {
            "stage": stage_key,
            "resolution": "effective_piece_recipe",
            "recipe": stage_recipe_identity,
        }
    )
    return candidate


def _g2_piece_terminal_product_candidate(
    piece: Dict[str, Any],
    route_segmentation: Dict[str, Any],
    plan_signature: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    product = _piece_product_candidate(piece, plan_signature, payload)
    key = _piece_key(piece)
    terminal_stage_key = str(route_segmentation.get("terminal_stage_key") or "").strip()
    product["metadata"] = dict(
        product.get("metadata") or {},
        phase="G2",
        strategy_type="independent_routed_piece",
        terminal_stage_key=terminal_stage_key,
        terminal_piece_sfg=True,
    )
    product["metadata"].update(_piece_candidate_recipe_metadata(piece))
    product["corr_sfg_key"] = _g2_piece_corr_sfg_key(piece, "terminal", plan_signature)
    product["corr_output_fingerprint"] = _hash_json({"piece": key, "output": "terminal_sfg", "phase": "G2"})
    product["corr_material_signature"] = _hash_json(
        {
            "piece": key,
            "phase": "G2",
            "recipe_signature": product["metadata"].get("recipe_signature") or "",
            "effective_stack_signature": product["metadata"].get("effective_stack_signature") or "",
            "effective_surface_profile_id": product["metadata"].get("effective_surface_profile_id"),
            "effective_top_sheet_mode": product["metadata"].get("effective_top_sheet_mode") or "",
        }
    )
    product["corr_route_signature"] = _hash_json({"piece": key, "route": "g2_routed_piece", "terminal_stage": terminal_stage_key})
    product["corr_resolution_signature"] = _hash_json(
        {
            "piece": key,
            "resolution": "g2_terminal_previous_stage_sfg",
            "recipe_signature": product["metadata"].get("recipe_signature") or "",
        }
    )
    return product


def _g2_fg_product_candidate(
    multi_piece: Dict[str, Any],
    pieces: List[Dict[str, Any]],
    source_blank: Dict[str, Any],
    plan_signature: str,
) -> Dict[str, Any]:
    fg = {
        "candidate_key": "product:fg",
        "segment_key": "fg_merge",
        "product_role": "fg",
        "corr_sfg_key": None,
        "corr_segment_key": "fg_merge",
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": _hash_json({"role": "fg", "pieces": [_piece_key(p) for p in pieces], "phase": "G2"}),
        "corr_material_signature": _hash_json({"merge_inputs": [_piece_key(p) for p in pieces], "phase": "G2"}),
        "corr_route_signature": _hash_json({"merge_stage_key": multi_piece.get("merge_stage_key"), "phase": "G2"}),
        "corr_layout_signature": _hash_json({"source_blank": source_blank, "split": _split_meta(multi_piece)}),
        "corr_resolution_signature": _hash_json({"phase": "G2", "resolution": "piece_sfg_merge"}),
        "stock_mode": "finished_good",
        "product_type_hint": "product",
        "route_policy": "reuse_existing_fg",
        "uom": "Units",
        "metadata": {
            "phase": "G2",
            "source": "current_fg_product",
            "writes_enabled": False,
        },
    }
    return fg


def _g2_piece_stage_bom_candidate(
    payload: Dict[str, Any],
    piece: Dict[str, Any],
    stage: Dict[str, Any],
    product: Dict[str, Any],
    previous_products: Optional[List[Dict[str, Any]]],
    *,
    is_first_stage: bool,
    board_stage_key: str = "",
    plan_signature: str,
) -> Dict[str, Any]:
    key = _piece_key(piece)
    stage_key = str(stage.get("stage_key") or "").strip()
    bom_key = f"bom:piece:{key}:stage:{stage_key}"
    lines: List[Dict[str, Any]] = []
    for previous_entry in list(previous_products or []):
        previous_product = dict(previous_entry.get("product") or previous_entry or {})
        explicit_qty = _safe_float(previous_entry.get("qty"), 0.0)
        if explicit_qty > 0.0:
            previous_qty = {
                "qty": explicit_qty,
                "source": previous_entry.get("quantity_source") or "explicit_previous_stage_qty",
                "previous_stage_key": previous_product.get("metadata", {}).get("stage_key") or previous_product.get("stage_key") or "",
                "current_stage_key": stage_key,
            }
        else:
            previous_qty = _g2_previous_output_component_qty(
                payload,
                {"stage_key": previous_product.get("metadata", {}).get("stage_key") or previous_product.get("stage_key") or ""},
                stage,
                piece_key=key,
            )
        lines.append(
            _previous_output_line_candidate(
                bom_key,
                previous_product,
                role_code="previous_piece_stage_sfg",
                qty=previous_qty.get("qty"),
                quantity_source=str(previous_qty.get("source") or "default"),
            )
        )
        lines[-1]["metadata"].update(previous_qty)
        lines[-1]["metadata"].update(dict(previous_entry.get("metadata") or {}))

    for row in _g2_stage_rm_rows(payload, key, stage_key, is_first_stage=is_first_stage, board_stage_key=board_stage_key):
        lines.append(_rm_line_candidate(row, key, bom_key))

    operation = _g2_stage_operation_candidate(bom_key, product.get("segment_key"), stage, piece)
    stage_recipe_identity = _g2_stage_recipe_identity(piece, stage_key)
    stage_material_signature = _hash_json(
        {
            "stage": stage_key,
            "recipe": stage_recipe_identity,
            "lines": _manufacturing_line_signature_rows(lines),
        }
    )
    stage_route_signature = _hash_json({"stage": stage_key, "route": "g2_routed_piece"})
    product["corr_sfg_key"] = _g2_manufacturing_corr_sfg_key(
        stage_key=stage_key,
        piece=piece,
        material_signature=stage_material_signature,
        route_signature=stage_route_signature,
    )
    product["corr_material_signature"] = stage_material_signature
    product["corr_route_signature"] = stage_route_signature
    return {
        "bom_key": bom_key,
        "segment_key": product.get("segment_key"),
        "product_candidate_key": product.get("candidate_key"),
        "bom_role": "sfg",
        "bom_type": "normal",
        "product_qty": 1.0,
        "corr_segment_key": product.get("segment_key"),
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": product.get("corr_output_fingerprint"),
        "corr_material_signature": stage_material_signature,
        "corr_route_signature": stage_route_signature,
        "corr_layout_signature": product.get("corr_layout_signature") or "",
        "corr_resolution_signature": _hash_json(
            {
                "stage": stage_key,
                "recipe": stage_recipe_identity,
                "rm_resolution": _line_resolution_rows(lines),
            }
        ),
        "lines": lines,
        "operations": [operation],
        "metadata": {
            "phase": "G2",
            "strategy_type": "independent_routed_piece",
            "piece_key": key,
            "stage_key": stage_key,
            "first_piece_stage": bool(is_first_stage),
            "terminal_piece_bom": False,
            "raw_paper_lines_allowed": bool(is_first_stage),
            "piece_layout_ups": 1,
        },
    }
    bom["metadata"].update(_piece_candidate_recipe_metadata(piece))
    return bom


def _g2_piece_terminal_bom_candidate(
    piece: Dict[str, Any],
    terminal_product: Dict[str, Any],
    previous_product: Optional[Dict[str, Any]],
    route_segmentation: Dict[str, Any],
    plan_signature: str,
) -> Dict[str, Any]:
    key = _piece_key(piece)
    bom_key = f"bom:piece:{key}:terminal"
    lines = []
    if previous_product:
        lines.append(_previous_output_line_candidate(bom_key, previous_product, role_code="terminal_piece_input_sfg"))
    bom = {
        "bom_key": bom_key,
        "segment_key": terminal_product.get("segment_key"),
        "product_candidate_key": terminal_product.get("candidate_key"),
        "bom_role": "sfg",
        "bom_type": "normal",
        "product_qty": 1.0,
        "corr_segment_key": terminal_product.get("segment_key"),
        "corr_plan_signature": plan_signature,
        "corr_output_fingerprint": terminal_product.get("corr_output_fingerprint"),
        "corr_material_signature": _hash_json({"piece": key, "terminal_input": previous_product.get("candidate_key") if previous_product else ""}),
        "corr_route_signature": _hash_json({"piece": key, "route": "g2_terminal", "terminal_stage": route_segmentation.get("terminal_stage_key")}),
        "corr_layout_signature": terminal_product.get("corr_layout_signature") or "",
        "corr_resolution_signature": _hash_json({"piece": key, "terminal_resolution": "previous_stage_sfg"}),
        "lines": lines,
        "operations": [],
        "metadata": {
            "phase": "G2",
            "strategy_type": "independent_routed_piece",
            "piece_key": key,
            "terminal_stage_key": route_segmentation.get("terminal_stage_key") or "",
            "terminal_piece_bom": True,
            "g1_terminal_bom_compatibility": True,
            "raw_paper_lines_allowed": False,
            "piece_layout_ups": 1,
        },
    }
    bom["metadata"].update(_piece_candidate_recipe_metadata(piece))
    return bom


def _previous_output_line_candidate(
    bom_key: str,
    previous_product: Dict[str, Any],
    *,
    role_code: str,
    qty: float = 1.0,
    quantity_source: str = "default",
) -> Dict[str, Any]:
    product_key = previous_product.get("candidate_key")
    component_qty = _safe_float(qty, 1.0)
    if component_qty <= 0.0:
        component_qty = 1.0
    return {
        "line_key": _stable_key("line", bom_key, "previous", product_key),
        "bom_key": bom_key,
        "line_type": "previous_output",
        "product_candidate_key": product_key,
        "source_segment_key": previous_product.get("segment_key"),
        "input_key": None,
        "role_code": role_code,
        "qty": component_qty,
        "uom": "Units",
        "operation_segment_key": previous_product.get("segment_key"),
        "metadata": {
            "phase": "G2",
            "component_kind": role_code,
            "component_corr_sfg_key": previous_product.get("corr_sfg_key"),
            "component_material_signature": previous_product.get("corr_material_signature"),
            "raw_paper_line": False,
            "quantity_source": quantity_source,
        },
    }


def _g2_previous_output_component_qty(
    payload: Dict[str, Any],
    previous_stage: Dict[str, Any],
    current_stage: Dict[str, Any],
    *,
    piece_key: str = "",
) -> Dict[str, Any]:
    previous_key = str(previous_stage.get("stage_key") or previous_stage.get("stage_code") or "").strip()
    current_key = str(current_stage.get("stage_key") or current_stage.get("stage_code") or "").strip()
    previous_metrics = _g2_stage_quantity_metrics(payload, previous_key)
    current_metrics = _g2_stage_quantity_metrics(payload, current_key)
    subassembly_count = _g2_piece_2ply_subassembly_count(payload, piece_key, previous_key, current_key)

    input_qty = _safe_float(
        current_metrics.get("input_qty_from_prev")
        or current_metrics.get("input_qty"),
        0.0,
    )
    source_output_qty = _safe_float(
        previous_metrics.get("target_output_qty")
        or previous_metrics.get("output_qty")
        or (previous_metrics.get("output") or {}).get("quantity"),
        0.0,
    )
    if input_qty > 0.0 and source_output_qty > 0.0:
        normalized = input_qty / source_output_qty
        if subassembly_count > normalized:
            return {
                "qty": subassembly_count,
                "source": "piece_medium_subassembly_count",
                "input_qty_from_prev": input_qty,
                "source_output_qty": source_output_qty,
                "previous_stage_key": previous_key,
                "current_stage_key": current_key,
            }
        if normalized > 0.0:
            return {
                "qty": normalized,
                "source": "stage_quantity_ratio",
                "input_qty_from_prev": input_qty,
                "source_output_qty": source_output_qty,
                "previous_stage_key": previous_key,
                "current_stage_key": current_key,
            }
    if input_qty > 0.0:
        return {
            "qty": input_qty,
            "source": "stage_input_qty_from_prev",
            "input_qty_from_prev": input_qty,
            "source_output_qty": source_output_qty,
            "previous_stage_key": previous_key,
            "current_stage_key": current_key,
        }
    if subassembly_count > 0.0:
        return {
            "qty": subassembly_count,
            "source": "piece_medium_subassembly_count",
            "input_qty_from_prev": input_qty,
            "source_output_qty": source_output_qty,
            "previous_stage_key": previous_key,
            "current_stage_key": current_key,
        }
    return {
        "qty": 1.0,
        "source": "default",
        "input_qty_from_prev": input_qty,
        "source_output_qty": source_output_qty,
        "previous_stage_key": previous_key,
        "current_stage_key": current_key,
    }


def _g2_stage_quantity_metrics(payload: Dict[str, Any], stage_key: str) -> Dict[str, Any]:
    key = str(stage_key or "").strip()
    if not key:
        return {}

    for row in _g2_quantity_rows(payload):
        row = dict(row or {})
        row_key = str(row.get("stage_key") or row.get("stage_code") or row.get("code") or "").strip()
        if _stage_key_matches(row_key, key):
            return row
    return {}


def _g2_quantity_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = dict(payload or {})
    rows: List[Dict[str, Any]] = []

    def add_rows(items: Any) -> None:
        if isinstance(items, dict):
            iterable = items.values()
        else:
            iterable = items or []
        for item in iterable:
            if isinstance(item, dict):
                rows.append(dict(item))

    preview_model = dict(payload.get("phase5_preview_model") or {})
    shadow = dict(payload.get("phase8_shadow_runtime") or {})
    add_rows((payload.get("quantity_report") or {}).get("stages"))
    add_rows((preview_model.get("quantity_report") or {}).get("stages"))
    add_rows((shadow.get("quantity_report") or {}).get("stages"))
    add_rows(preview_model.get("stages"))
    add_rows(shadow.get("stage_contexts"))
    return rows


def _g2_piece_2ply_subassembly_count(
    payload: Dict[str, Any],
    piece_key: str,
    previous_stage_key: str,
    current_stage_key: str,
) -> float:
    if not _is_g2_2ply_stage(previous_stage_key) or not _is_g2_board_stage(current_stage_key):
        return 0.0

    layers = set()
    fallback_count = 0
    seen_unlayered = set()
    for row in list((payload or {}).get("rm_requirements") or []):
        row = dict(row or {})
        row_piece = _rm_piece_key(row)
        if row_piece and piece_key and row_piece != piece_key:
            continue
        if _paper_role(row) != "medium":
            continue
        row_stage = str(row.get("stage_key") or "").strip()
        if row_stage and not _stage_key_matches(row_stage, previous_stage_key):
            continue
        layer_no = _rm_layer_no(row)
        if layer_no > 0:
            layers.add(layer_no)
            continue
        identity = (
            row.get("product_key"),
            row.get("input_key"),
            _safe_float(row.get("qty_per_fg"), 0.0),
            row.get("qty_uom"),
        )
        if identity in seen_unlayered:
            continue
        seen_unlayered.add(identity)
        fallback_count += 1
    return float(len(layers) + fallback_count)


def _rm_layer_no(row: Dict[str, Any]) -> int:
    metadata = dict(row.get("metadata") or {})
    preview_metadata = dict(metadata.get("preview_metadata") or {})
    return _safe_int(
        row.get("layer_no")
        or row.get("source_layer_no")
        or metadata.get("layer_no")
        or metadata.get("source_layer_no")
        or preview_metadata.get("layer_no")
        or preview_metadata.get("source_layer_no"),
        0,
    )


def _is_g2_2ply_stage(stage_key: str) -> bool:
    key = str(stage_key or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    return "2ply" in key or "twoply" in key or "corrugation" in key


def _is_g2_board_stage(stage_key: str) -> bool:
    key = str(stage_key or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    return "board" in key or "lamination" in key


def _g2_stage_rm_rows(
    payload: Dict[str, Any],
    piece_key: str,
    stage_key: str,
    *,
    is_first_stage: bool,
    board_stage_key: str = "",
) -> List[Dict[str, Any]]:
    rows = []
    for row in list(payload.get("rm_requirements") or []):
        row = dict(row or {})
        role = _paper_role(row)
        row_stage = str(row.get("stage_key") or "").strip()
        row_piece = _rm_piece_key(row)
        if row_piece and row_piece != piece_key:
            continue
        if is_first_stage and role in {"liner", "medium"}:
            rows.append(row)
            continue
        if role in {"liner", "medium"}:
            continue
        if role in {"top_sheet_external", "top_sheet"}:
            top_mode = str((row.get("metadata") or {}).get("top_sheet_mode") or row.get("top_sheet_mode") or "").strip().lower()
            if top_mode in {"roll", "roll_sheet", "roll_top"}:
                if is_first_stage:
                    rows.append(row)
                continue
            if board_stage_key:
                if _stage_key_matches(stage_key, board_stage_key):
                    row = deepcopy(row)
                    row["stage_key"] = stage_key
                    metadata = dict(row.get("metadata") or {})
                    metadata["original_stage_key"] = row_stage
                    metadata["assigned_route_stage"] = stage_key
                    row["metadata"] = metadata
                    rows.append(row)
                continue
            if _stage_key_matches(row_stage, stage_key):
                rows.append(row)
            continue
        if row_piece == piece_key and _stage_key_matches(row_stage, stage_key):
            rows.append(row)
    return rows


def _g2_board_stage_key(branch_stages: List[Dict[str, Any]]) -> str:
    for stage in branch_stages:
        key = str(stage.get("stage_key") or "")
        stage_type = str(stage.get("stage_type") or "").strip().lower()
        capability = str(stage.get("capability_code") or "").strip().lower()
        name = str(stage.get("stage_name") or "").strip().lower()
        if _stage_key_matches(key, "board") or stage_type in {"lamination", "board"} or capability in {"board", "lamination"} or "board" in name:
            return key
    return ""


def _g2_stage_operation_candidate(
    bom_key: str,
    segment_key: str,
    stage: Dict[str, Any],
    piece: Dict[str, Any],
) -> Dict[str, Any]:
    stage_key = str(stage.get("stage_key") or "").strip()
    key = _piece_key(piece)
    return {
        "operation_candidate_key": f"operation:{bom_key}:{stage_key}",
        "bom_key": bom_key,
        "segment_key": segment_key,
        "operation_key": stage_key,
        "operation_name": stage.get("stage_name") or _merge_stage_name(stage_key),
        "sequence": _safe_int(stage.get("sequence"), 10),
        "input_qty": 1.0,
        "output_qty": 1.0,
        "uom": "Units",
        "stage_contract_code": stage.get("stage_contract_code") or stage_key,
        "stage_template_id": stage.get("stage_template_id"),
        "stage_code": stage_key,
        "metadata": {
            "phase": "G2",
            "piece_key": key,
            "branch_operation": True,
            "merge_operation_only": False,
        },
    }


def _g2_piece_corr_sfg_key(piece: Dict[str, Any], stage_key: str, plan_signature: str) -> str:
    key = _piece_key(piece)
    digest = _hash_json(
        {
            "plan_signature": plan_signature,
            "piece_key": key,
            "stage_key": stage_key,
            "length": _safe_float(piece.get("piece_length_mm"), 0.0),
            "width": _safe_float(piece.get("piece_width_mm"), 0.0),
            "qty": _safe_float(piece.get("piece_qty_per_fg"), 1.0),
            "phase": "G2",
        }
    )[:12]
    return f"engine_v2:multi_piece:g2:piece:{key}:stage:{stage_key}:{digest}"


def _g2_manufacturing_corr_sfg_key(
    *,
    stage_key: str,
    piece: Dict[str, Any],
    material_signature: str,
    route_signature: str,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    stage = str(stage_key or "").strip().lower() or "stage"
    digest = _hash_json(
        {
            "identity_version": "g2_manufacturing_sfg_v1",
            "stage_key": stage,
            "length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
            "width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
            "material_signature": material_signature or "",
            "route_signature": route_signature or "",
            "extra": dict(extra or {}),
        }
    )[:16]
    return f"engine_v2:multi_piece:g2:mfg:{stage}:{digest}"


def _g2_piece_chain_debug(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    products = {product.get("candidate_key"): product for product in list(plan.get("products") or [])}
    chains: Dict[str, Dict[str, Any]] = {}
    for bom in list(plan.get("boms") or []):
        metadata = dict(bom.get("metadata") or {})
        key = metadata.get("piece_key")
        if not key:
            continue
        chains.setdefault(key, {"piece_key": key, "stages": []})
        product = products.get(bom.get("product_candidate_key")) or {}
        chains[key]["stages"].append(
            {
                "bom_candidate_key": bom.get("bom_key"),
                "product_candidate_key": bom.get("product_candidate_key"),
                "stage_key": metadata.get("stage_key") or metadata.get("terminal_stage_key") or "terminal",
                "terminal": bool(metadata.get("terminal_piece_bom")),
                "components": [
                    line.get("product_candidate_key") or line.get("input_key") or line.get("role_code")
                    for line in list(bom.get("lines") or [])
                ],
                "product_role": product.get("product_role"),
            }
        )
    return list(chains.values())


def _rm_line_candidate(row: Dict[str, Any], piece_key: str, bom_key: str) -> Dict[str, Any]:
    row = dict(row or {})
    metadata = dict(row.get("metadata") or {})
    resolution = dict(metadata.get("resolution") or {})
    row_required_deckle_cm = row.get("required_deckle_cm") or metadata.get("required_deckle_cm") or resolution.get("required_deckle_cm")
    force_live_paper_resolution = bool(metadata.get("force_live_paper_resolution"))
    if force_live_paper_resolution:
        product_id = None
        writer_product_key = ""
        product_tmpl_id = None
        metadata.pop("product_id", None)
        metadata.pop("product_tmpl_id", None)
        metadata.pop("writer_product_key", None)
        resolution.pop("product_id", None)
        resolution.pop("product_tmpl_id", None)
    else:
        product_id = (
            _safe_int(
                metadata.get("product_id")
                or metadata.get("top_sheet_product_id")
                or resolution.get("product_id")
                or row.get("product_id")
                or row.get("top_sheet_product_id"),
                0,
            )
            or None
        )
        writer_product_key = str(metadata.get("writer_product_key") or "")
        if product_id and not writer_product_key:
            writer_product_key = f"product.product:{product_id}"
        if product_id:
            metadata["resolution_status"] = metadata.get("resolution_status") or resolution.get("status") or "resolved"
            metadata["resolution_note"] = "Exact paper variant resolved"
            resolution["resolution_note"] = "Exact paper variant resolved"
        product_tmpl_id = (
            metadata.get("product_tmpl_id")
            or metadata.get("top_sheet_product_tmpl_id")
            or resolution.get("product_tmpl_id")
            or row.get("product_tmpl_id")
            or row.get("top_sheet_product_tmpl_id")
        )
    line_key = _stable_key(
        "line",
        bom_key,
        piece_key,
        row.get("product_key"),
        row.get("role"),
        row.get("stage_key"),
        row.get("layer_no"),
    )
    normalized_flute = _normalize_flute_code(
        metadata.get("flute_code")
        or metadata.get("flute")
        or metadata.get("flute_type_code")
        or metadata.get("flute_type")
    )
    metadata.update(
        {
            "piece_key": piece_key,
            "semantic_role": metadata.get("semantic_role") or row.get("role"),
            "product_id": product_id,
            "product_tmpl_id": product_tmpl_id,
            "writer_product_key": writer_product_key,
            "writer_qty": row.get("qty_per_fg"),
            "writer_uom": row.get("qty_uom"),
            "source_kind": metadata.get("source_kind") or metadata.get("source") or "engine_v2_rm_requirement",
            "required_deckle_cm": row_required_deckle_cm,
            "resolution": resolution,
        }
    )
    if row_required_deckle_cm and not resolution.get("required_deckle_cm"):
        resolution["required_deckle_cm"] = row_required_deckle_cm
        metadata["resolution"] = resolution
    if normalized_flute:
        metadata["flute"] = normalized_flute
        metadata["flute_code"] = normalized_flute
    return {
        "line_key": line_key,
        "bom_key": bom_key,
        "line_type": "rm_input",
        "product_candidate_key": None,
        "source_segment_key": None,
        "input_key": row.get("product_key"),
        "role_code": row.get("role"),
        "qty": _safe_float(row.get("qty_per_fg"), 0.0),
        "uom": row.get("qty_uom"),
        "operation_segment_key": row.get("stage_key"),
        "metadata": metadata,
    }


def _multi_piece_strategy(multi_piece: Dict[str, Any]) -> str:
    return str(
        multi_piece.get("multi_piece_strategy")
        or multi_piece.get("strategy")
        or "independent_routed_piece"
    ).strip().lower() or "independent_routed_piece"


def _effective_piece_manufacturing_by_key(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = list(payload.get("effective_piece_manufacturing") or [])
    if not rows:
        rows = list((payload.get("piece_manufacturing_overrides") or {}).get("pieces") or [])
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        row = dict(row or {})
        key = _piece_key(row)
        if key:
            result[key] = row
    return result


def _effective_piece_payload(piece: Dict[str, Any], effective: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(piece or {})
    effective = dict(effective or {})
    stack = dict(effective.get("stack") or {})
    surface = dict(effective.get("surface") or {})
    top_sheet = dict(effective.get("top_sheet") or {})
    result.update(
        {
            "stack_source": effective.get("stack_source") or stack.get("source") or result.get("stack_source") or "inherited",
            "surface_source": effective.get("surface_source") or surface.get("source") or result.get("surface_source") or "inherited",
            "top_sheet_source": effective.get("top_sheet_source") or top_sheet.get("source") or result.get("top_sheet_source") or "inherited",
            "recipe_signature": effective.get("recipe_signature") or result.get("recipe_signature") or "",
            "effective_top_sheet_mode": top_sheet.get("effective_top_sheet_mode") or effective.get("effective_top_sheet_mode") or "",
            "effective_top_sheet_product": top_sheet.get("effective_top_sheet_product") or effective.get("effective_top_sheet_product") or {},
            "effective_top_sheet_product_id": top_sheet.get("effective_top_sheet_product_id")
            or ((top_sheet.get("effective_top_sheet_product") or {}).get("id"))
            or ((effective.get("effective_top_sheet_product") or {}).get("id")),
            "effective_surface_profile": surface.get("effective_surface_profile") or effective.get("effective_surface_profile") or {},
            "effective_surface_profile_id": surface.get("effective_surface_profile_id")
            or ((surface.get("effective_surface_profile") or {}).get("id"))
            or ((effective.get("effective_surface_profile") or {}).get("id")),
            "effective_stack_lines": list(stack.get("effective_stack_lines") or effective.get("effective_stack_lines") or []),
        }
    )
    result["effective_stack_signature"] = _piece_effective_stack_signature(result)
    return result


def _piece_effective_stack_signature(piece: Dict[str, Any]) -> str:
    rows = []
    for line in list(piece.get("effective_stack_lines") or []):
        line = dict(line or {})
        rows.append(
            {
                "sequence": _safe_int(line.get("sequence"), 0),
                "role": str(line.get("role") or "").strip().lower(),
                "gsm": _safe_float(line.get("gsm"), 0.0),
                "bf": _safe_float(line.get("bf"), 0.0),
                "color": str(line.get("color") or "").strip(),
                "flute": _normalize_flute_code(
                    line.get("flute_code")
                    or line.get("flute")
                    or line.get("flute_type_code")
                    or line.get("flute_type")
                ),
                "material_product_id": _safe_int(line.get("material_product_id"), 0),
            }
        )
    return _hash_json(rows)


def _piece_candidate_recipe_metadata(piece: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "piece_key": _piece_key(piece),
        "piece_index": _safe_int(piece.get("piece_index"), 0),
        "stack_source": piece.get("stack_source") or "inherited",
        "surface_source": piece.get("surface_source") or "inherited",
        "top_sheet_source": piece.get("top_sheet_source") or "inherited",
        "recipe_signature": piece.get("recipe_signature") or "",
        "effective_top_sheet_mode": piece.get("effective_top_sheet_mode") or "",
        "effective_top_sheet_product_id": piece.get("effective_top_sheet_product_id"),
        "effective_stack_signature": piece.get("effective_stack_signature") or "",
        "effective_surface_profile_id": piece.get("effective_surface_profile_id"),
        "strategy": "independent_routed_piece",
    }


def _piece_has_custom_effective_recipe(piece: Dict[str, Any]) -> bool:
    return any(
        not _piece_manufacturing_source_is_inherited(piece.get(field_name))
        for field_name in ("stack_source", "surface_source", "top_sheet_source")
    )


def _g2_stage_recipe_identity(piece: Dict[str, Any], stage_key: str) -> Dict[str, Any]:
    stage = str(stage_key or "").strip().lower()
    identity = {
        "stage_key": stage,
        "length_mm": _safe_float(piece.get("piece_length_mm"), 0.0),
        "width_mm": _safe_float(piece.get("piece_width_mm"), 0.0),
    }
    if _is_g2_board_stage(stage):
        identity["effective_surface_profile_id"] = _safe_int(piece.get("effective_surface_profile_id"), 0)
        if str(piece.get("effective_top_sheet_mode") or "").strip().lower() == "unit_sheet":
            identity["effective_top_sheet_mode"] = "unit_sheet"
            identity["effective_top_sheet_product_id"] = _safe_int(piece.get("effective_top_sheet_product_id"), 0)
        return identity
    return identity


def _h_source_blank(multi_piece: Dict[str, Any]) -> Dict[str, float]:
    return {
        "length_mm": _safe_float(multi_piece.get("source_length_mm"), 0.0),
        "width_mm": _safe_float(multi_piece.get("source_width_mm"), 0.0),
    }


def _h_split_output(piece: Dict[str, Any], split_axis: str, source_blank: Dict[str, float]) -> Dict[str, Any]:
    length = _safe_float(piece.get("piece_length_mm"), 0.0)
    width = _safe_float(piece.get("piece_width_mm"), 0.0)
    return {
        "piece_key": _piece_key(piece),
        "piece_name": piece.get("piece_name") or _piece_key(piece),
        "length_mm": length,
        "width_mm": width,
        "deckle_cm": width / 10.0 if width > 0.0 else 0.0,
        "split_axis": split_axis,
        "source_length_mm": _safe_float(source_blank.get("length_mm"), 0.0),
        "source_width_mm": _safe_float(source_blank.get("width_mm"), 0.0),
    }


def _h_allocation_rows(
    multi_piece: Dict[str, Any],
    pieces: List[Dict[str, Any]],
    split_axis: str,
    source_blank: Dict[str, float],
) -> List[Dict[str, Any]]:
    axis = str(split_axis or multi_piece.get("split_axis") or "").strip().lower()
    denominator = _safe_float(
        source_blank.get("width_mm") if axis == "width" else source_blank.get("length_mm"),
        0.0,
    )
    rows = []
    for piece in pieces:
        numerator = _safe_float(
            piece.get("piece_width_mm") if axis == "width" else piece.get("piece_length_mm"),
            0.0,
        )
        qty = _safe_float(piece.get("piece_qty_per_fg"), 1.0) or 1.0
        ratio = (numerator * qty / denominator) if denominator > 0.0 else 0.0
        rows.append(
            {
                "piece_key": _piece_key(piece),
                "piece_name": piece.get("piece_name") or _piece_key(piece),
                "ratio": ratio,
                "percentage": ratio * 100.0,
                "axis": axis,
                "basis_mm": numerator,
                "source_basis_mm": denominator,
            }
        )
    return rows


def _stage_key_position(stage_keys: List[str], wanted: str) -> Optional[int]:
    for index, key in enumerate(stage_keys):
        if _stage_key_matches(key, wanted):
            return index
    return None


def _h_piece_layout_ups_blockers(payload: Dict[str, Any], pieces: List[Dict[str, Any]]) -> List[str]:
    planning = dict(payload.get("piece_layout_planning") or {})
    planning_rows = [dict(item or {}) for item in list(planning.get("pieces") or [])]
    rows_by_key = {_piece_key(row): row for row in planning_rows if _piece_key(row)}
    blockers = []
    for piece in pieces:
        key = _piece_key(piece)
        row = rows_by_key.get(key) or {}
        selected = dict(row.get("selected_option") or row.get("preferred_option") or {})
        ups = _safe_float(selected.get("up_count") or selected.get("ups") or 1.0, 1.0)
        if abs(ups - 1.0) > 1e-6:
            blockers.append(f"piece_{key}_layout_ups_must_be_1")
    return blockers


def _h_nested_split_detected(payload: Dict[str, Any]) -> bool:
    multi_piece = dict(payload.get("multi_piece") or {})
    for key in ("nested_split", "nested_splits", "split_nodes", "additional_split_nodes"):
        value = multi_piece.get(key)
        if value not in (None, "", False, [], {}):
            return True
    for piece in list(multi_piece.get("pieces") or []):
        piece = dict(piece or {})
        if piece.get("split_after_stage_key") or piece.get("split_outputs"):
            return True
    return False


def _h_capacity_checks(
    payload: Dict[str, Any],
    multi_piece: Dict[str, Any],
    route_segmentation: Dict[str, Any],
    pieces: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    source_blank = _h_source_blank(multi_piece)
    for stage in list(route_segmentation.get("shared_stages") or []):
        checks.append(
            _h_stage_fit_check(
                payload,
                stage,
                source_blank.get("length_mm"),
                source_blank.get("width_mm"),
                scope="shared_full_blank",
                blocker_prefix="shared_full_blank",
            )
        )
    for piece in pieces:
        piece_key = _piece_key(piece)
        length = _safe_float(piece.get("piece_length_mm"), 0.0)
        width = _safe_float(piece.get("piece_width_mm"), 0.0)
        for stage in list(route_segmentation.get("post_split_branch_stages") or []):
            checks.append(
                _h_stage_fit_check(
                    payload,
                    stage,
                    length,
                    width,
                    scope=f"piece_{piece_key}_branch_output",
                    blocker_prefix=f"piece_{piece_key}_branch_output",
                )
            )
    return checks


def _h_stage_fit_check(
    payload: Dict[str, Any],
    stage: Dict[str, Any],
    length_mm: Any,
    width_mm: Any,
    *,
    scope: str,
    blocker_prefix: str,
) -> Dict[str, Any]:
    stage = dict(stage or {})
    stage_key = str(stage.get("stage_key") or "").strip()
    length = _safe_float(length_mm, 0.0)
    width = _safe_float(width_mm, 0.0)
    capacity = _h_stage_capacity(payload, stage)
    check = {
        "scope": scope,
        "stage_key": stage_key,
        "stage_name": stage.get("stage_name") or stage_key,
        "length_mm": length,
        "width_mm": width,
        "capacity": capacity,
    }
    if not capacity.get("has_limits"):
        reason = (
            "workcenter_not_configured_for_stage"
            if not capacity.get("workcenter_configured")
            else "capacity_limits_missing_on_workcenter"
        )
        check.update(
            {
                "status": "not_checked",
                "reason": reason,
            }
        )
        return check

    orientation_checks = []
    checks = [{"orientation": "direct", "length_mm": length, "width_mm": width}]
    if capacity.get("rotation_allowed") and abs(length - width) > 1e-6:
        checks.append({"orientation": "rotated", "length_mm": width, "width_mm": length})

    for item in checks:
        failures = _h_dimension_failures(capacity, item["length_mm"], item["width_mm"])
        orientation_checks.append(
            {
                "orientation": item["orientation"],
                "length_mm": item["length_mm"],
                "width_mm": item["width_mm"],
                "status": "feasible" if not failures else "blocked",
                "rejection_reasons": failures,
            }
        )

    feasible = next((item for item in orientation_checks if item["status"] == "feasible"), None)
    check["orientation_checks"] = orientation_checks
    if feasible:
        check.update(
            {
                "status": "feasible",
                "reason": "fits_known_workcenter_capacity",
                "orientation": feasible["orientation"],
            }
        )
    else:
        failures = _h_orientation_failure_summary(orientation_checks)
        check.update(
            {
                "status": "blocked",
                "reason": "; ".join(failures),
                "blocker": f"{blocker_prefix}_{stage_key}_capacity_failed",
            }
        )
    return check


def _h_stage_capacity(payload: Dict[str, Any], stage: Dict[str, Any]) -> Dict[str, Any]:
    stage_key = str(stage.get("stage_key") or "").strip()
    base = _h_capacity_from_stage_entry(dict(stage or {}))
    for entry in _h_stage_capacity_candidates(payload, stage_key):
        candidate_capacity = _h_capacity_from_stage_entry(dict(entry or {}))
        if candidate_capacity.get("has_limits") or candidate_capacity.get("workcenter_configured"):
            return candidate_capacity
    return base


def _h_dimension_failures(capacity: Dict[str, Any], length: float, width: float) -> List[str]:
    failures = []
    min_length = _safe_float(capacity.get("min_sheet_length_mm"), 0.0)
    max_length = _safe_float(capacity.get("max_sheet_length_mm"), 0.0)
    min_width = _safe_float(capacity.get("min_sheet_width_mm"), 0.0)
    max_width = _safe_float(capacity.get("max_sheet_width_mm"), 0.0)
    if min_length > 0.0 and length < min_length:
        failures.append(f"length {length:.1f} mm below min {min_length:.1f} mm")
    if max_length > 0.0 and length > max_length:
        failures.append(f"length {length:.1f} mm exceeds max {max_length:.1f} mm")
    if min_width > 0.0 and width < min_width:
        failures.append(f"width {width:.1f} mm below min {min_width:.1f} mm")
    if max_width > 0.0 and width > max_width:
        failures.append(f"width {width:.1f} mm exceeds max {max_width:.1f} mm")
    return failures


def _h_orientation_failure_summary(orientation_checks: List[Dict[str, Any]]) -> List[str]:
    summary = []
    for item in orientation_checks or []:
        reasons = list(item.get("rejection_reasons") or [])
        if not reasons:
            continue
        summary.append(
            "%s (%s x %s mm): %s"
            % (
                item.get("orientation") or "direct",
                _format_h_dimension(item.get("length_mm")),
                _format_h_dimension(item.get("width_mm")),
                "; ".join(str(reason) for reason in reasons),
            )
        )
    return summary


def _format_h_dimension(value: Any) -> str:
    number = _safe_float(value, 0.0)
    if abs(number - round(number)) <= 1e-6:
        return str(int(round(number)))
    return "%.1f" % number


def _h_size_cm_text(length_mm: Any, width_mm: Any) -> str:
    length_cm = _safe_float(length_mm, 0.0) / 10.0
    width_cm = _safe_float(width_mm, 0.0) / 10.0
    return "%sx%s cm" % (_format_h_dimension(length_cm), _format_h_dimension(width_cm))


def _format_h_percent(value: Any) -> str:
    percent = _safe_float(value, 0.0)
    if abs(percent - round(percent)) <= 1e-6:
        return str(int(round(percent)))
    return "%.1f" % percent


def _h_capacity_from_stage_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    entry = dict(entry or {})
    capacity_sources: List[Dict[str, Any]] = []
    for candidate in list(entry.get("candidate_workcenters") or []):
        if isinstance(candidate, dict):
            capacity_sources.append(dict(candidate or {}))
    for candidate in list(entry.get("candidates") or []):
        if isinstance(candidate, dict):
            capacity_sources.append(dict(candidate or {}))
    if not capacity_sources:
        if not _h_candidate_has_workcenter(entry):
            return _h_capacity_with_rotation(
                {
                    "min_sheet_length_mm": None,
                    "max_sheet_length_mm": None,
                    "min_sheet_width_mm": None,
                    "max_sheet_width_mm": None,
                    "workcenter_id": None,
                    "workcenter_name": "",
                    "workcenter_configured": False,
                    "has_limits": False,
                    "source": "runtime_feasibility",
                    "constraint_source": "",
                },
                entry,
            )
        capacity_sources.append(entry)

    fallback = _h_capacity_from_candidate(entry)
    for candidate in capacity_sources:
        capacity = _h_capacity_from_candidate(candidate)
        if capacity.get("has_limits") or capacity.get("workcenter_configured"):
            return _h_capacity_with_rotation(capacity, entry)
    return _h_capacity_with_rotation(fallback, entry)


def _h_capacity_from_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    candidate = dict(candidate or {})
    resolved = dict(candidate.get("resolved_constraints") or {})
    matched = dict(candidate.get("matched_workcenter") or {})
    constraints = dict(candidate.get("constraints_applied") or {})

    min_width_cm = _first_capacity_value(
        candidate,
        "width_min_cm",
        "deckle_min_cm",
        "min_width_cm",
    ) or _first_capacity_value(resolved, "width_min_cm")
    max_width_cm = _first_capacity_value(
        candidate,
        "width_max_cm",
        "deckle_max_cm",
        "max_width_cm",
    ) or _first_capacity_value(resolved, "width_max_cm")

    min_width_mm = (
        min_width_cm * 10.0
        if min_width_cm > 0.0
        else _first_capacity_value(candidate, "min_sheet_width_mm", "min_width_mm")
        or _first_capacity_value(constraints, "min_sheet_width_mm", "min_width_mm")
    )
    max_width_mm = (
        max_width_cm * 10.0
        if max_width_cm > 0.0
        else _first_capacity_value(
            candidate,
            "max_sheet_width_mm",
            "max_width_mm",
        )
        or _first_capacity_value(constraints, "max_sheet_width_mm", "max_width_mm")
        or _first_capacity_value(resolved, "width_limit_mm")
    )
    min_length_mm = (
        _first_capacity_value(candidate, "min_sheet_length_mm", "min_length_mm", "length_min_mm")
        or _first_capacity_value(constraints, "min_sheet_length_mm", "min_length_mm", "length_min_mm")
    )
    max_length_mm = (
        _first_capacity_value(candidate, "max_sheet_length_mm", "max_length_mm", "length_max_mm")
        or _first_capacity_value(constraints, "max_sheet_length_mm", "max_length_mm", "length_max_mm")
        or _first_capacity_value(resolved, "length_limit_mm")
    )
    workcenter_id = (
        _safe_int(candidate.get("workcenter_id"), 0)
        or _safe_int(matched.get("workcenter_id"), 0)
        or None
    )
    workcenter_name = (
        candidate.get("workcenter_name")
        or matched.get("workcenter_name")
        or ""
    )
    capacity = {
        "min_sheet_length_mm": min_length_mm or None,
        "max_sheet_length_mm": max_length_mm or None,
        "min_sheet_width_mm": min_width_mm or None,
        "max_sheet_width_mm": max_width_mm or None,
        "workcenter_id": workcenter_id,
        "workcenter_name": workcenter_name,
        "workcenter_configured": bool(workcenter_id or workcenter_name),
        "source": candidate.get("source") or "runtime_feasibility",
        "constraint_source": candidate.get("constraint_source") or candidate.get("deckle_max_source") or "",
    }
    capacity["has_limits"] = any(
        _safe_float(capacity.get(key), 0.0) > 0.0
        for key in (
            "min_sheet_length_mm",
            "max_sheet_length_mm",
            "min_sheet_width_mm",
            "max_sheet_width_mm",
        )
    )
    return capacity


def _h_candidate_has_workcenter(candidate: Dict[str, Any]) -> bool:
    candidate = dict(candidate or {})
    matched = dict(candidate.get("matched_workcenter") or {})
    return bool(
        _safe_int(candidate.get("workcenter_id"), 0)
        or _safe_int(matched.get("workcenter_id"), 0)
        or candidate.get("workcenter_name")
        or matched.get("workcenter_name")
    )


def _h_capacity_with_rotation(capacity: Dict[str, Any], stage_entry: Dict[str, Any]) -> Dict[str, Any]:
    capacity = dict(capacity or {})
    blank_dimensions = dict(stage_entry.get("blank_dimensions") or {})
    current_dimensions = dict(stage_entry.get("current_dimensions") or {})
    checked_orientations = list(current_dimensions.get("checked_orientations") or [])
    if "rotation_allowed" in blank_dimensions:
        rotation_allowed = bool(blank_dimensions.get("rotation_allowed"))
    elif checked_orientations:
        rotation_allowed = len(checked_orientations) > 1
    else:
        rotation_allowed = True
    capacity["rotation_allowed"] = rotation_allowed
    return capacity


def _h_stage_capacity_candidates(payload: Dict[str, Any], stage_key: str) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    raw = payload.get("stage_feasibility")
    if isinstance(raw, dict):
        if isinstance(raw.get("stages"), list):
            iterable = list(raw.get("stages") or [])
        else:
            iterable = [
                value
                for value in raw.values()
                if isinstance(value, dict)
            ]
    else:
        iterable = list(raw or [])
    for item in iterable:
        if not isinstance(item, dict):
            continue
        item = dict(item or {})
        key = str(item.get("stage_key") or item.get("stage_code") or "").strip()
        if key and _stage_key_matches(key, stage_key):
            candidates.append(item)
    shadow = dict(payload.get("phase8_shadow_runtime") or {})
    slitting = dict(shadow.get("slitting_options") or {})
    for stage in list(slitting.get("stages") or []):
        stage = dict(stage or {})
        key = str(stage.get("stage_key") or stage.get("stage_code") or "").strip()
        if key and _stage_key_matches(key, stage_key):
            candidates.append(stage)
    return candidates


def _first_capacity_value(source: Dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return _safe_float(value, 0.0)
    return 0.0


def _contains_shared_upstream_marker(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            norm = str(key or "").strip().lower()
            if norm in {
                "shared_upstream",
                "shared_upstream_split",
                "split_stage",
                "split_after_stage",
                "shared_upstream_stage_key",
            } and item not in (None, "", False, [], {}):
                return True
            if isinstance(item, (dict, list)) and _contains_shared_upstream_marker(item):
                return True
    elif isinstance(value, list):
        return any(_contains_shared_upstream_marker(item) for item in value)
    return False


def _normalize_piece_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    upper = raw.upper()
    compact = upper.replace("-", "_").replace(" ", "_")
    aliases = {
        "PIECE_A": "A",
        "PIECE_1": "A",
        "A1": "A",
        "PIECE_B": "B",
        "PIECE_2": "B",
        "B1": "B",
    }
    if compact in aliases:
        return aliases[compact]
    if upper in {"A", "B"}:
        return upper
    return upper


def _piece_key(piece: Dict[str, Any]) -> str:
    return _normalize_piece_key(piece.get("piece_key") or piece.get("piece_code") or piece.get("piece_name"))


def _piece_manufacturing_source_is_inherited(value: Any) -> bool:
    value = str(value or "inherited").strip().lower()
    return value in {"", "inherited"}


def _rm_piece_key(row: Dict[str, Any]) -> str:
    metadata = dict(row.get("metadata") or {})
    return _normalize_piece_key(row.get("piece_key") or metadata.get("piece_key"))


def _piece_required_deckle_cm(payload: Dict[str, Any], piece: Dict[str, Any]) -> float:
    key = _piece_key(piece)
    planning = dict(payload.get("piece_layout_planning") or {})
    for item in list(planning.get("pieces") or []):
        item = dict(item or {})
        if _piece_key(item) != key:
            continue
        selected = dict(item.get("selected_option") or item.get("preferred_option") or {})
        width_mm = _safe_float(
            selected.get("final_sheet_width_mm")
            or selected.get("sheet_width_mm")
            or selected.get("blank_width_mm"),
            0.0,
        )
        if width_mm > 0.0:
            return width_mm / 10.0
    width_mm = _safe_float(piece.get("piece_width_mm"), 0.0)
    return width_mm / 10.0 if width_mm > 0.0 else 0.0


def _paper_role(row: Dict[str, Any]) -> str:
    metadata = dict(row.get("metadata") or {})
    role = str(metadata.get("semantic_role") or row.get("role") or "").strip().lower()
    if role == "top_roll":
        return "liner"
    return role


def _resolve_piece_paper(
    row: Dict[str, Any],
    metadata: Dict[str, Any],
    resolution: Dict[str, Any],
    preview_metadata: Dict[str, Any],
    role: str,
    deckle_cm: float,
    paper_resolver: Any,
) -> Dict[str, Any]:
    gsm = _first_value(resolution, preview_metadata, metadata, row, keys=("gsm", "paper_gsm", "basis_weight"))
    bf = _first_value(resolution, preview_metadata, metadata, row, keys=("bf", "bursting_factor"))
    color = _first_value(resolution, preview_metadata, metadata, row, keys=("color", "paper_color"))
    flute = _normalize_flute_code(
        _first_value(
            resolution,
            preview_metadata,
            metadata,
            row,
            keys=("flute_code", "flute", "flute_type_code", "flute_type"),
        )
    )
    try:
        result = paper_resolver(
            role=role,
            gsm=gsm,
            bf=bf,
            color=color,
            flute=flute if role == "medium" else None,
            deckle_cm=deckle_cm,
        )
    except Exception:
        return {}
    return dict(result or {})


def _first_value(*sources: Dict[str, Any], keys: Iterable[str]) -> Any:
    for source in sources:
        for key in keys:
            value = (source or {}).get(key)
            if value not in (None, ""):
                return value
    return None


def _normalize_flute_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("_", " ").replace("-", " ")
    parts = [part for part in re.split(r"\s+", text) if part]
    if len(parts) >= 2 and parts[0].lower() == "flute":
        text = parts[1]
    else:
        text = parts[0]
    return re.sub(r"[^A-Za-z0-9]", "", text).upper()


def _piece_corr_sfg_key(piece: Dict[str, Any], plan_signature: str) -> str:
    key = _piece_key(piece)
    digest = _hash_json(
        {
            "plan_signature": plan_signature,
            "piece_key": key,
            "length": _safe_float(piece.get("piece_length_mm"), 0.0),
            "width": _safe_float(piece.get("piece_width_mm"), 0.0),
            "qty": _safe_float(piece.get("piece_qty_per_fg"), 1.0),
            "phase": "G1",
        }
    )[:12]
    return f"engine_v2:multi_piece:g1:piece:{key}:{digest}"


def _split_meta(multi_piece: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "split_axis": multi_piece.get("split_axis"),
        "split_mode": multi_piece.get("split_mode"),
        "source_length_mm": _safe_float(multi_piece.get("source_length_mm"), 0.0),
        "source_width_mm": _safe_float(multi_piece.get("source_width_mm"), 0.0),
    }


def _merge_stage_name(stage_key: str) -> str:
    normalized = str(stage_key or "").strip().replace("_", " ")
    return normalized.title() if normalized else "Merge"


def _line_signature_rows(lines: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "line_type": line.get("line_type"),
            "role_code": line.get("role_code"),
            "input_key": line.get("input_key"),
            "qty": line.get("qty"),
            "uom": line.get("uom"),
            "piece_key": (line.get("metadata") or {}).get("piece_key"),
        }
        for line in lines
    ]


def _manufacturing_line_signature_rows(lines: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for line in lines:
        metadata = dict(line.get("metadata") or {})
        result.append(
            {
                "line_type": line.get("line_type"),
                "role_code": line.get("role_code"),
                "component_corr_sfg_key": metadata.get("component_corr_sfg_key"),
                "component_material_signature": metadata.get("component_material_signature"),
                "product_id": metadata.get("product_id"),
                "product_tmpl_id": metadata.get("product_tmpl_id"),
                "writer_product_key": metadata.get("writer_product_key"),
                "source_layer_no": metadata.get("source_layer_no") or metadata.get("layer_no"),
                "gsm": metadata.get("gsm"),
                "bf": metadata.get("bf"),
                "color": metadata.get("color"),
                "flute": metadata.get("flute") or metadata.get("flute_code"),
                "qty": line.get("qty"),
                "uom": line.get("uom"),
            }
        )
    return result


def _line_resolution_rows(lines: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "role_code": line.get("role_code"),
            "product_id": (line.get("metadata") or {}).get("product_id"),
            "product_tmpl_id": (line.get("metadata") or {}).get("product_tmpl_id"),
            "writer_product_key": (line.get("metadata") or {}).get("writer_product_key"),
        }
        for line in lines
    ]


def _plan_signature(payload: Dict[str, Any]) -> str:
    effective_piece_rows = []
    for piece in list(payload.get("effective_piece_manufacturing") or []):
        piece = dict(piece or {})
        stack = list(piece.get("effective_stack_lines") or (piece.get("stack") or {}).get("effective_stack_lines") or [])
        effective_piece_rows.append(
            {
                "piece_key": _piece_key(piece),
                "stack_source": piece.get("stack_source") or (piece.get("stack") or {}).get("source"),
                "surface_source": piece.get("surface_source") or (piece.get("surface") or {}).get("source"),
                "top_sheet_source": piece.get("top_sheet_source") or (piece.get("top_sheet") or {}).get("source"),
                "recipe_signature": piece.get("recipe_signature"),
                "top_sheet_mode": piece.get("effective_top_sheet_mode") or (piece.get("top_sheet") or {}).get("effective_top_sheet_mode"),
                "top_sheet_product_id": (
                    piece.get("effective_top_sheet_product_id")
                    or (piece.get("top_sheet") or {}).get("effective_top_sheet_product_id")
                ),
                "surface_profile_id": (
                    piece.get("effective_surface_profile_id")
                    or (piece.get("surface") or {}).get("effective_surface_profile_id")
                ),
                "stack": [
                    {
                        "sequence": row.get("sequence"),
                        "layer_no": row.get("layer_no"),
                        "role": row.get("role"),
                        "gsm": row.get("gsm"),
                        "bf": row.get("bf"),
                        "color": row.get("color"),
                        "flute": _normalize_flute_code(
                            row.get("flute_code")
                            or row.get("flute")
                            or row.get("flute_type_code")
                            or row.get("flute_type")
                        ),
                        "material_product_id": row.get("material_product_id"),
                    }
                    for row in stack
                    if isinstance(row, dict)
                ],
            }
        )
    relevant = {
        "multi_piece_materialization_signature_version": "piece_corrugated_sets_v5",
        "spec_id": payload.get("spec_id"),
        "style_code": payload.get("style_code"),
        "multi_piece": payload.get("multi_piece"),
        "selected_layout": payload.get("selected_layout"),
        "effective_piece_manufacturing": effective_piece_rows,
        "piece_specific_rm_breakdown": [
            {
                "piece_key": _normalize_piece_key(row.get("piece_key") or (row.get("metadata") or {}).get("piece_key")),
                "source_layer_no": row.get("source_layer_no") or (row.get("metadata") or {}).get("source_layer_no"),
                "layer_no": row.get("layer_no") or (row.get("metadata") or {}).get("layer_no"),
                "role": row.get("role") or (row.get("metadata") or {}).get("role"),
                "gsm": row.get("gsm") or (row.get("metadata") or {}).get("gsm"),
                "bf": row.get("bf") or (row.get("metadata") or {}).get("bf"),
                "color": row.get("color") or (row.get("metadata") or {}).get("color"),
                "flute": _normalize_flute_code(row.get("flute") or (row.get("metadata") or {}).get("flute_code")),
                "product_id": row.get("product_id") or (row.get("metadata") or {}).get("product_id"),
                "required_deckle_cm": row.get("required_deckle_cm") or (row.get("metadata") or {}).get("required_deckle_cm"),
                "qty_per_fg": row.get("qty_per_fg"),
                "uom": row.get("uom"),
            }
            for row in list(((payload or {}).get("piece_specific_rm_breakdown") or {}).get("rows") or [])
            if isinstance(row, dict)
        ],
        "spec_stack_lines": [
            {
                "layer_no": row.get("layer_no"),
                "role": row.get("role"),
                "gsm": row.get("gsm"),
                "bf": row.get("bf"),
                "color": row.get("color"),
                "flute_code": row.get("flute_code"),
            }
            for row in list(payload.get("spec_stack_lines") or [])
            if isinstance(row, dict)
        ],
        "rm_requirements": [
            {
                "product_key": row.get("product_key"),
                "role": row.get("role"),
                "stage_key": row.get("stage_key"),
                "layer_no": row.get("layer_no"),
                "qty_per_fg": row.get("qty_per_fg"),
                "qty_uom": row.get("qty_uom"),
                "piece_key": _rm_piece_key(dict(row or {})),
            }
            for row in list(payload.get("rm_requirements") or [])
            if isinstance(row, dict)
        ],
    }
    return _hash_json(relevant)


def _fg_product_name(payload: Dict[str, Any]) -> str:
    payload = dict(payload or {})
    summary = dict(payload.get("summary") or payload.get("spec_summary") or {})
    preview_model = dict(payload.get("phase5_preview_model") or {})
    preview_summary = dict(preview_model.get("summary") or {})
    product_name_keys = (
        "fg_product_name",
        "finished_product_name",
        "product_name",
        "product_display_name",
        "fg_name",
    )
    fallback_name_keys = ("name", "spec_name")
    for keys in (product_name_keys, fallback_name_keys):
        for source in (payload, summary, preview_summary):
            for key in keys:
                value = str((source or {}).get(key) or "").strip()
                if value:
                    return value
    return ""


def _stable_key(*parts: Any) -> str:
    text = ":".join(str(part or "") for part in parts)
    return text + ":" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def _hash_json(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


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



