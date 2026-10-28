from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .engine_v2.consumables import is_consumable_role, should_include_consumable


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


def _metrics_snapshot(metrics: Any) -> Dict[str, Any]:
    if not metrics:
        return {}
    extra = getattr(metrics, "extra", {}) or {}
    style = dict(extra.get("style") or {})
    return {
        "m2_per_box": _safe_float(getattr(metrics, "m2_per_box", 0.0), 0.0),
        "kg_per_box": _safe_float(getattr(metrics, "kg_per_box", 0.0), 0.0),
        "boxes_per_sheet": _safe_float(extra.get("boxes_per_sheet"), 0.0),
        "blanks_per_sheet": _safe_int(extra.get("layout_blanks_per_sheet"), 0),
        "layout_orientation": extra.get("orientation") or "",
        "style": style,
    }


def _style_yield_metadata(metrics: Any) -> Dict[str, Any]:
    try:
        extra = getattr(metrics, "extra", {}) or {}
        style = dict(extra.get("style") or {})
        return dict(style.get("metadata") or {})
    except Exception:
        return {}


def _apply_fg_per_sheet_order_yield(
    rows: List[Dict[str, Any]],
    metrics: Any,
    order_qty: float,
) -> Dict[str, Any]:
    metadata = _style_yield_metadata(metrics)
    if metadata.get("yield_basis") != "fg_per_sheet":
        return {}
    fg_per_sheet = _safe_int(metadata.get("fg_per_sheet") or metadata.get("diecut_ups"), 0)
    if fg_per_sheet <= 0:
        return {}
    order_qty_f = _safe_float(order_qty, 0.0)
    required_sheets = int(math.ceil(order_qty_f / float(fg_per_sheet))) if order_qty_f > 0.0 else 0
    fg_equivalent_qty = float(required_sheets * fg_per_sheet)
    paper_roles = {"liner", "medium", "top_roll", "top_sheet_external"}
    for row in rows:
        role = str(row.get("role") or "").strip().lower()
        metadata_row = dict(row.get("metadata") or {})
        semantic_role = str(row.get("semantic_role") or metadata_row.get("semantic_role") or "").strip().lower()
        if role not in paper_roles and semantic_role not in paper_roles:
            continue
        qty_per_fg = _safe_float(row.get("qty_per_fg"), 0.0)
        if qty_per_fg <= 0.0:
            continue
        row["total_qty"] = qty_per_fg * fg_equivalent_qty
        row["yield_total_qty"] = row["total_qty"]
        row["yield_fg_equivalent_qty"] = fg_equivalent_qty
        row["yield_required_sheets"] = required_sheets
        metadata_row.update(
            {
                "yield_basis": "fg_per_sheet",
                "fg_per_sheet": fg_per_sheet,
                "required_sheets": required_sheets,
                "fg_equivalent_qty_for_order_rm": fg_equivalent_qty,
            }
        )
        row["metadata"] = metadata_row
        notes = list(row.get("notes") or [])
        note = "order-level RM rounded to whole sheets"
        if note not in notes:
            notes.append(note)
        row["notes"] = notes
    return {
        "fg_per_sheet": fg_per_sheet,
        "required_sheets": required_sheets,
        "fg_equivalent_qty_for_order_rm": fg_equivalent_qty,
    }


def _apply_fg_per_sheet_operation_yield(
    operations: List[Dict[str, Any]],
    metrics: Any,
    order_qty: float,
) -> List[Dict[str, Any]]:
    metadata = _style_yield_metadata(metrics)
    if metadata.get("yield_basis") != "fg_per_sheet":
        return operations
    fg_per_sheet = _safe_int(metadata.get("fg_per_sheet") or metadata.get("diecut_ups"), 0)
    if fg_per_sheet <= 0:
        return operations

    order_qty_f = _safe_float(order_qty, 0.0)
    required_sheets = float(math.ceil(order_qty_f / float(fg_per_sheet)))
    upstream_qty = required_sheets
    yield_output_qty = required_sheets * float(fg_per_sheet)
    downstream_qty = yield_output_qty
    quantity_basis = "diecut_sheet_required_sheets_ceil"

    yield_stage_key = str(metadata.get("yield_stage_key") or "").strip().lower()
    yield_stage_kind = str(metadata.get("yield_stage_kind") or "").strip().lower()
    downstream_stage_key = str(metadata.get("yield_downstream_stage_key") or "").strip().lower()
    downstream_output_label = metadata.get("yield_downstream_output_label")
    yield_index = None
    downstream_index = None
    for index, operation in enumerate(operations or []):
        stage = str(operation.get("stage") or "").strip().lower()
        stage_code = str(operation.get("stage_code") or "").strip().lower()
        stage_codes = {
            str(code or "").strip().lower()
            for code in (operation.get("stage_codes") or [])
            if str(code or "").strip()
        }
        if downstream_stage_key and (
            downstream_stage_key == stage or downstream_stage_key == stage_code or downstream_stage_key in stage_codes
        ):
            downstream_index = index
        if yield_stage_key and yield_stage_key in stage_codes:
            yield_index = index
            continue
        if yield_stage_key and (stage == yield_stage_key or stage_code == yield_stage_key):
            yield_index = index
            continue
        if yield_stage_kind and stage == yield_stage_kind:
            yield_index = index

    if yield_index is None:
        return operations
    if downstream_index is None:
        for index, operation in enumerate(operations or []):
            if index <= yield_index:
                continue
            stage_text = " ".join(
                [
                    str(operation.get("stage") or "").strip().lower(),
                    str(operation.get("stage_code") or "").strip().lower(),
                    str(operation.get("operation_key") or "").strip().lower(),
                    str(operation.get("stage_name") or "").strip().lower(),
                    str(operation.get("stage_type") or "").strip().lower(),
                    str(operation.get("capability_code") or "").strip().lower(),
                ]
            )
            if "peel" in stage_text or "stripping" in stage_text:
                downstream_index = index
                downstream_output_label = downstream_output_label or "usable blanks"
                break
    yield_output_label = metadata.get("yield_output_label")
    if downstream_index is not None and downstream_index > yield_index:
        yield_output_label = "gross blanks"
        downstream_output_label = downstream_output_label or "usable blanks"

    adjusted: List[Dict[str, Any]] = []
    for index, operation in enumerate(operations or []):
        row = dict(operation)
        stage_text = " ".join(
            [
                str(row.get("stage") or "").strip().lower(),
                str(row.get("stage_code") or "").strip().lower(),
                str(row.get("operation_key") or "").strip().lower(),
                str(row.get("stage_name") or "").strip().lower(),
                str(row.get("stage_type") or "").strip().lower(),
                str(row.get("capability_code") or "").strip().lower(),
            ]
        )
        if index < yield_index:
            row["input_qty"] = upstream_qty
            row["output_qty"] = upstream_qty
            row["input_uom"] = row.get("input_uom") or "sheet"
            row["output_uom"] = "sheet"
            row["uom"] = "sheet"
        elif index == yield_index:
            row["input_qty"] = upstream_qty
            row["output_qty"] = yield_output_qty
            row["input_uom"] = row.get("input_uom") or "sheet"
            row["output_uom"] = "unit"
            row["uom"] = "unit"
            row["yield_output_kind"] = "blank"
            if yield_output_label:
                row["yield_output_label"] = yield_output_label
            if downstream_output_label:
                row["yield_downstream_output_label"] = downstream_output_label
        else:
            row["input_qty"] = downstream_qty
            if any(token in stage_text for token in ("seaming", "jointing", "gluing", "stitching", "conversion", "fg", "final")):
                row["output_qty"] = order_qty_f
                row["yield_output_label"] = "finished goods"
            else:
                row["output_qty"] = downstream_qty
            row["input_uom"] = row.get("input_uom") or "unit"
            row["output_uom"] = "unit"
            row["uom"] = "unit"
            row["yield_output_kind"] = row.get("yield_output_kind") or "blank"
            if downstream_output_label and index == downstream_index:
                row["yield_output_label"] = downstream_output_label
        row["yield_basis"] = "fg_per_sheet"
        row["fg_per_sheet"] = fg_per_sheet
        row["yield_quantity_basis"] = quantity_basis
        row["yield_relation_input_sheets"] = upstream_qty
        row["yield_relation_output_blanks"] = yield_output_qty
        adjusted.append(row)
    return adjusted


def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _legacy_stage_lookup(shadow_runtime: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for item in list(shadow_runtime.get("stage_contexts") or []) + list(shadow_runtime.get("material_requirements") or []):
        stage_code = str(item.get("stage_code") or "")
        if not stage_code:
            continue
        stage_template_id = _safe_int(item.get("stage_template_id"), 0)
        stage_contract_code = str(item.get("stage_contract_code") or "")
        if not stage_template_id and not stage_contract_code:
            continue
        lookup.setdefault(
            stage_code,
            {
                "stage_template_id": stage_template_id or None,
                "stage_contract_code": stage_contract_code,
            },
        )
    return lookup


def _normalize_rm_requirement(item: Any, legacy_stage_lookup: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    if isinstance(item, dict):
        data = dict(item)
    else:
        data = {
            "product_key": getattr(item, "product_key", "") or "",
            "role": getattr(item, "role", "") or "",
            "stage_key": getattr(item, "stage_key", "") or "",
            "stage_template_id": getattr(item, "stage_template_id", None),
            "stage_contract_code": getattr(item, "stage_contract_code", "") or "",
            "layer_no": getattr(item, "layer_no", None),
            "qty_per_fg": getattr(item, "qty_per_fg", 0.0) or 0.0,
            "qty_uom": getattr(item, "qty_uom", "") or "",
            "consumption_type": getattr(item, "consumption_type", "") or "",
            "metadata": getattr(item, "metadata", {}) or {},
        }
    data["product_key"] = str(data.get("product_key") or "")
    data["role"] = str(data.get("role") or "")
    data["stage_key"] = str(data.get("stage_key") or "")
    data["stage_template_id"] = _safe_int(data.get("stage_template_id"), 0) or None
    data["stage_contract_code"] = str(data.get("stage_contract_code") or "")
    data["qty_per_fg"] = _safe_float(data.get("qty_per_fg"), 0.0)
    data["qty_uom"] = str(data.get("qty_uom") or "")
    data["consumption_type"] = str(data.get("consumption_type") or "")
    data["metadata"] = dict(data.get("metadata") or {})
    metadata = data["metadata"]
    data["layer_no"] = _safe_int(data.get("layer_no") or metadata.get("layer_no"), 0) or None
    if not data["stage_template_id"]:
        data["stage_template_id"] = _safe_int(metadata.get("stage_template_id"), 0) or None
    if not data["stage_contract_code"]:
        data["stage_contract_code"] = str(metadata.get("stage_contract_code") or "")
    if legacy_stage_lookup and (not data["stage_template_id"] and not data["stage_contract_code"]):
        stage_meta = legacy_stage_lookup.get(data["stage_key"]) or {}
        data["stage_template_id"] = _safe_int(stage_meta.get("stage_template_id"), 0) or None
        data["stage_contract_code"] = str(stage_meta.get("stage_contract_code") or "")
    data["semantic_role"] = str(data.get("semantic_role") or metadata.get("semantic_role") or "")
    data["legacy_role_aliases"] = _dedupe_strings(
        list(data.get("legacy_role_aliases") or []) + list(metadata.get("legacy_role_aliases") or [])
    )
    return data


def _legacy_category_for_requirement(item: Dict[str, Any]) -> str:
    role = str(item.get("role") or item.get("semantic_role") or "").strip().lower()
    material_type = str(item.get("material_type") or "").strip().lower()
    metadata = dict(item.get("metadata") or {})
    product_key = str(item.get("product_key") or metadata.get("product_key") or "").strip().lower()
    semantic_role = str(metadata.get("semantic_role") or "").strip().lower()
    role_probe = " ".join(filter(None, [role, semantic_role, product_key]))
    if (
        role_probe.startswith("adhesive")
        or "adhesive" in role_probe
        or role in ("ink", "ink_total", "print", "printing", "printing_ink", "varnish", "lamination", "wire", "stitching_wire", "tape")
        or product_key in ("rm_ink", "rm_print", "rm_printing_ink", "rm_wire", "rm_tape", "rm_lamination_film")
    ):
        return "consumable"
    if material_type in ("consumable", "intermediate", "rm"):
        return material_type
    if role in ("sheet", "board", "box", "component"):
        return "intermediate"
    return "rm"


def _consumable_profile_key_for_requirement(item: Dict[str, Any]) -> str:
    role = str(item.get("role") or item.get("semantic_role") or "").strip().lower()
    metadata = dict(item.get("metadata") or {})
    product_key = str(item.get("product_key") or metadata.get("product_key") or "").strip().lower()
    probe = " ".join(filter(None, [role, product_key, str(metadata.get("kind") or "").strip().lower()]))
    if "ink" in probe or "print" in probe:
        return "ink"
    if "wire" in probe or "stitch" in probe:
        return "wire"
    if "tape" in probe:
        return "tape"
    if "film" in probe or "lamination" in probe:
        return "lamination"
    if "adhesive" in probe or "glue" in probe or "gum" in probe:
        if "corrugation" in probe:
            return "adhesive_corrugation"
        if "fg" in probe or "joint" in probe or "seam" in probe:
            return "adhesive_fg"
        return "adhesive_board"
    return ""


def _consumable_profile_key_for_role(role: str, metadata: Optional[Dict[str, Any]] = None, product_key: str = "") -> str:
    probe = " ".join(
        filter(
            None,
            [
                str(role or "").strip().lower(),
                str(product_key or "").strip().lower(),
                str((metadata or {}).get("kind") or "").strip().lower(),
                str((metadata or {}).get("semantic_role") or "").strip().lower(),
            ],
        )
    )
    if "ink" in probe or "print" in probe:
        return "ink"
    if "wire" in probe or "stitch" in probe:
        return "wire"
    if "tape" in probe:
        return "tape"
    if "film" in probe or "lamination" in probe:
        return "lamination"
    if "adhesive" in probe or "glue" in probe or "gum" in probe:
        if "corrugation" in probe:
            return "adhesive_corrugation"
        if "fg" in probe or "joint" in probe or "seam" in probe:
            return "adhesive_fg"
        return "adhesive_board"
    return ""


def _apply_consumable_profile_product(
    metadata: Dict[str, Any],
    product_label: str,
    product_tmpl_id: Optional[int],
    qty_uom: str,
    profile_product: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], str, Optional[int], str]:
    profile_product = dict(profile_product or {})
    profile_product_tmpl_id = _safe_int(profile_product.get("product_tmpl_id"), 0) or None
    if profile_product_tmpl_id:
        product_tmpl_id = product_tmpl_id or profile_product_tmpl_id
        metadata.setdefault("product_tmpl_id", profile_product_tmpl_id)
        metadata.setdefault("product_name", profile_product.get("product_label") or "")
        metadata.setdefault("resolution_product_name", profile_product.get("product_label") or "")
        metadata.setdefault("resolution_status", "resolved")
        metadata.setdefault("resolution_source", "engine_material_profile_role")
        resolution = dict(metadata.get("resolution") or {})
        resolution.setdefault("status", "resolved")
        resolution.setdefault("source", "engine_material_profile_role")
        resolution.setdefault("product_tmpl_id", profile_product_tmpl_id)
        resolution.setdefault("product_name", profile_product.get("product_label") or "")
        metadata["resolution"] = resolution
    if profile_product.get("uom_id"):
        metadata.setdefault("uom_id", profile_product.get("uom_id"))
        resolution = dict(metadata.get("resolution") or {})
        resolution.setdefault("uom_id", profile_product.get("uom_id"))
        metadata["resolution"] = resolution
    if profile_product.get("uom_name"):
        qty_uom = str(profile_product.get("uom_name") or qty_uom or "")
        metadata.setdefault("uom_name", qty_uom)
        resolution = dict(metadata.get("resolution") or {})
        resolution.setdefault("uom_name", qty_uom)
        metadata["resolution"] = resolution
    if profile_product.get("product_label"):
        product_label = str(profile_product.get("product_label") or product_label or "")
    return metadata, product_label, product_tmpl_id, qty_uom


def _normalize_paper_resolution_entries(entries: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "").strip().lower()
        if role not in ("liner", "medium", "top_roll", "top_sheet_external"):
            continue
        item = dict(entry)
        item["role"] = "top_roll" if role == "top_sheet_external" else role
        item["layer_no"] = _safe_int(item.get("layer_no"), 0) or None
        item["qty_per_fg"] = _safe_float(item.get("qty_per_fg"), 0.0)
        item["qty_uom"] = str(item.get("qty_uom") or item.get("uom_name") or "")
        item["status"] = str(item.get("status") or "").strip().lower() or "missing"
        item["trace_only"] = item["status"] == "trace"
        normalized.append(item)
    return normalized


def _paper_resolution_for_attachment(
    role: str,
    metadata: Dict[str, Any],
    paper_resolution: List[Dict[str, Any]],
    used_paper_resolution: set,
) -> Optional[Dict[str, Any]]:
    role_key = str(role or "").strip().lower()
    if role_key == "top_sheet_external":
        role_key = "top_roll"
    layer_no = _safe_int(metadata.get("layer_no"), 0) or None

    if layer_no:
        for index, entry in enumerate(paper_resolution or []):
            if index in used_paper_resolution:
                continue
            if entry.get("trace_only"):
                continue
            if entry.get("role") == role_key and _safe_int(entry.get("layer_no"), 0) == layer_no:
                used_paper_resolution.add(index)
                return entry

    for index, entry in enumerate(paper_resolution or []):
        if index in used_paper_resolution:
            continue
        if entry.get("trace_only"):
            continue
        if entry.get("role") == role_key:
            used_paper_resolution.add(index)
            return entry
    return None


def _suppress_replaced_liner_paper_entries(
    paper_resolution: List[Dict[str, Any]],
    used_paper_resolution: set,
    replaced_layers: set,
    row_trace: List[Dict[str, Any]],
    *,
    stage_code: str,
    attachment_role: str,
    source: str,
) -> None:
    if not replaced_layers:
        return
    for index, entry in enumerate(paper_resolution or []):
        if index in used_paper_resolution:
            continue
        role = str(entry.get("role") or "").strip().lower()
        layer_no = _safe_int(entry.get("layer_no"), 0) or None
        if role != "liner" or layer_no not in replaced_layers:
            continue
        used_paper_resolution.add(index)
        row_trace.append(
            {
                "action": "suppressed_replaced_liner_from_paper_resolution",
                "path": "paper_resolution",
                "stage_code": stage_code,
                "attachment_role": attachment_role,
                "matched_role": "",
                "layer_no": layer_no,
                "qty_source": "paper_resolution",
                "source": source,
                "product_key": "",
                "product": str(entry.get("product_name") or ""),
            }
        )


def _mark_matching_legacy_used(
    legacy: List[Dict[str, Any]],
    used_legacy: set,
    paper_entry: Optional[Dict[str, Any]],
) -> None:
    if not paper_entry:
        return
    role = str(paper_entry.get("role") or "").strip().lower()
    layer_no = _safe_int(paper_entry.get("layer_no"), 0) or None
    for index, item in enumerate(legacy or []):
        if index in used_legacy:
            continue
        if str(item.get("role") or "").strip().lower() != role:
            continue
        item_layer = _safe_int(item.get("layer_no") or (item.get("metadata") or {}).get("layer_no"), 0) or None
        if layer_no and item_layer and layer_no != item_layer:
            continue
        used_legacy.add(index)
        return


def _mark_matching_paper_resolution_used(
    paper_resolution: List[Dict[str, Any]],
    used_paper_resolution: set,
    rm_item: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not rm_item:
        return None
    role = str(rm_item.get("role") or "").strip().lower()
    if role == "top_sheet_external":
        role = "top_roll"
    if role not in ("liner", "medium", "top_roll"):
        return None
    layer_no = _safe_int(rm_item.get("layer_no") or (rm_item.get("metadata") or {}).get("layer_no"), 0) or None
    for index, entry in enumerate(paper_resolution or []):
        if index in used_paper_resolution:
            continue
        if entry.get("trace_only"):
            continue
        if str(entry.get("role") or "").strip().lower() != role:
            continue
        entry_layer = _safe_int(entry.get("layer_no"), 0) or None
        if layer_no and entry_layer and layer_no != entry_layer:
            continue
        used_paper_resolution.add(index)
        return entry
    return None


def _top_replacement_layers(
    legacy: List[Dict[str, Any]],
    paper_resolution: List[Dict[str, Any]],
) -> set:
    layers = set()
    for item in list(legacy or []) + list(paper_resolution or []):
        role = str(item.get("role") or "").strip().lower()
        metadata = dict(item.get("metadata") or {})
        debug = dict(item.get("debug") or {})
        resolution = dict(item.get("resolution") or {})
        replaces_liner = bool(
            metadata.get("top_sheet_replaces_liner")
            or debug.get("top_sheet_replaces_liner")
            or resolution.get("top_sheet_replaces_liner")
        )
        if role != "top_roll" and not (role == "top_sheet_external" and replaces_liner):
            continue
        layer_no = _safe_int(item.get("layer_no") or (item.get("metadata") or {}).get("layer_no"), 0) or None
        if layer_no:
            layers.add(layer_no)
    return layers


def _is_replaced_liner_attachment(metadata: Dict[str, Any], replaced_layers: set) -> bool:
    if not replaced_layers:
        return False
    layer_no = _safe_int((metadata or {}).get("layer_no"), 0) or None
    return bool(layer_no and layer_no in replaced_layers)


def _is_replaced_liner_requirement(item: Optional[Dict[str, Any]], replaced_layers: set) -> bool:
    if not item or not replaced_layers:
        return False
    role = str(item.get("role") or "").strip().lower()
    if role != "liner":
        return False
    layer_no = _safe_int(item.get("layer_no") or (item.get("metadata") or {}).get("layer_no"), 0) or None
    return bool(layer_no and layer_no in replaced_layers)


def _has_available_role_requirement(
    available: List[Dict[str, Any]],
    used: set,
    role: str,
    replaced_layers: Optional[set] = None,
) -> bool:
    role = str(role or "").strip().lower()
    for index, item in enumerate(available or []):
        if index in used:
            continue
        if str(item.get("role") or "").strip().lower() != role:
            continue
        if role == "liner" and _is_replaced_liner_requirement(item, replaced_layers or set()):
            continue
        return True
    return False


def _stage_identity_candidates(item: Dict[str, Any]) -> List[Tuple[str, Any]]:
    candidates: List[Tuple[str, Any]] = []
    stage_template_id = _safe_int(item.get("stage_template_id"), 0)
    stage_contract_code = str(item.get("stage_contract_code") or "")
    if stage_template_id:
        candidates.append(("stage_template_id", stage_template_id))
    if stage_contract_code:
        candidates.append(("stage_contract_code", stage_contract_code))
    return candidates


def _collect_basis_map(
    shadow_runtime: Dict[str, Any],
) -> Dict[Tuple[str, Any, str, Optional[int], str], Dict[str, Any]]:
    basis_map: Dict[Tuple[str, Any, str, Optional[int], str], Dict[str, Any]] = {}
    for item in shadow_runtime.get("material_requirements") or []:
        roles = _dedupe_strings(
            [
                item.get("role"),
                item.get("semantic_role"),
                *list(item.get("legacy_role_aliases") or []),
            ]
        )
        product_tmpl_id = item.get("product_tmpl_id")
        material_type = str(item.get("material_type") or "")
        for stage_identity in _stage_identity_candidates(item):
            for role in roles:
                key = (stage_identity[0], stage_identity[1], role, product_tmpl_id, material_type)
                basis_map[key] = dict(item)
    return basis_map


def _match_basis(
    basis_map: Dict[Tuple[str, Any, str, Optional[int], str], Dict[str, Any]],
    stage_identity: List[Tuple[str, Any]],
    role: str,
    product_tmpl_id: Optional[int],
    material_type: str,
) -> Dict[str, Any]:
    for identity_type, identity_value in stage_identity:
        candidates = [
            (identity_type, identity_value, role, product_tmpl_id, material_type),
            (identity_type, identity_value, role, product_tmpl_id, ""),
            (identity_type, identity_value, role, None, material_type),
            (identity_type, identity_value, role, None, ""),
        ]
        for key in candidates:
            item = basis_map.get(key)
            if item:
                return item
    return {}


def _match_rm_requirement(
    ctx: Dict[str, Any],
    attachment: Dict[str, Any],
    role_aliases: List[str],
    available: List[Dict[str, Any]],
    used: set,
) -> Tuple[Optional[Dict[str, Any]], str]:
    stage_identities = _stage_identity_candidates(attachment) or _stage_identity_candidates(ctx)
    attachment_metadata = dict((attachment or {}).get("metadata") or {})
    attachment_layer_no = _safe_int(attachment_metadata.get("layer_no"), 0) or None
    paper_aliases = {"liner", "medium", "top_roll", "top_sheet_external"}
    is_paper_match = bool(set(role_aliases or []) & paper_aliases)

    def _stage_matches(item: Dict[str, Any]) -> bool:
        if not stage_identities:
            return str(item.get("stage_key") or "") == str(ctx.get("stage_code") or "")
        item_template_id = _safe_int(item.get("stage_template_id"), 0)
        item_contract_code = str(item.get("stage_contract_code") or "")
        for identity_type, identity_value in stage_identities:
            if identity_type == "stage_template_id" and item_template_id and item_template_id == identity_value:
                return True
            if identity_type == "stage_contract_code" and item_contract_code and item_contract_code == identity_value:
                return True
        if not item_template_id and not item_contract_code:
            return str(item.get("stage_key") or "") == str(ctx.get("stage_code") or "")
        return False

    def _layer_matches(item: Dict[str, Any]) -> bool:
        if not is_paper_match or not attachment_layer_no:
            return True
        item_layer_no = _safe_int(item.get("layer_no") or (item.get("metadata") or {}).get("layer_no"), 0) or None
        return bool(item_layer_no and item_layer_no == attachment_layer_no)

    exact = [
        (idx, item)
        for idx, item in enumerate(available)
        if idx not in used and _stage_matches(item) and item.get("role") in role_aliases and _layer_matches(item)
    ]
    if exact:
        idx, item = exact[0]
        used.add(idx)
        return item, "legacy_rm_requirement"

    allow_stage_only_fallback = not is_paper_match
    stage_only = [
        (idx, item)
        for idx, item in enumerate(available)
        if allow_stage_only_fallback and idx not in used and _stage_matches(item)
    ]
    if len(stage_only) == 1:
        idx, item = stage_only[0]
        used.add(idx)
        return item, "legacy_stage_fallback"

    role_only = [
        (idx, item)
        for idx, item in enumerate(available)
        if idx not in used and item.get("role") in role_aliases and _layer_matches(item)
    ]
    if len(role_only) == 1:
        idx, item = role_only[0]
        used.add(idx)
        return item, "legacy_role_fallback"

    return None, "shadow_pending"


def _legacy_aliases_for_semantic_role(semantic_role: str) -> List[str]:
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


def _normalize_attachment_role(role: str, attachment: Optional[Dict[str, Any]] = None) -> str:
    semantic_role = str((attachment or {}).get("semantic_role") or "").strip().lower()
    if semantic_role:
        return semantic_role
    role = str(role or "").strip()
    if not role:
        return ""
    upper = role.upper()
    alias_map = {
        "RM_PAPER_LINER": "liner",
        "RM_PAPER_MEDIUM": "medium",
        "RM_PAPER_TOP": "top_sheet_external",
        "RM_PAPER_GENERIC": "paper",
        "CONSUMABLE_ADHESIVE_CORRUGATION": "adhesive",
        "CONSUMABLE_ADHESIVE_BOARD": "adhesive",
        "CONSUMABLE_ADHESIVE_FG": "adhesive",
        "CONSUMABLE_INK": "ink",
        "CONSUMABLE_PRINT": "ink",
        "CONSUMABLE_PRINTING_INK": "ink",
        "CONSUMABLE_VARNISH": "varnish",
        "CONSUMABLE_WIRE": "wire",
        "CONSUMABLE_STITCHING_WIRE": "wire",
        "CONSUMABLE_TAPE": "tape",
        "CONSUMABLE_JOINTING_TAPE": "tape",
        "INT_SHEET": "sheet",
        "INT_BOARD": "board",
        "INT_2PLY_BOARD": "board",
        "INT_BOX_SEAMED": "box",
    }
    if upper in alias_map:
        return alias_map[upper]
    return role.strip().lower()


def _stage_classifier(row: Dict[str, Any]) -> str:
    capability_code = str(row.get("capability_code") or "").strip().lower()
    if capability_code:
        return capability_code

    stage_type = str(row.get("stage_type") or "").strip().lower()
    if stage_type:
        return stage_type

    output_stock_behavior = str(row.get("output_stock_behavior") or "").strip().lower()
    if output_stock_behavior == "fg":
        return "fg"

    output_kind = str(row.get("output_kind") or "").strip().lower()
    output_kind_map = {
        "board": "corrugation",
        "box": "conversion",
        "fg": "fg",
    }
    return output_kind_map.get(output_kind, "")


def _role_aliases_for_attachment(ctx: Dict[str, Any], attachment: Dict[str, Any], role: str) -> List[str]:
    role = str(role or "").strip().lower()
    semantic_role = str((attachment or {}).get("semantic_role") or "").strip().lower()
    runtime_aliases = _dedupe_strings(list((attachment or {}).get("legacy_role_aliases") or []))
    if runtime_aliases:
        return runtime_aliases
    if semantic_role:
        return _legacy_aliases_for_semantic_role(semantic_role)

    if role in ("ink", "print", "printing", "printing_ink"):
        return ["ink", "ink_total", "print", "printing", "printing_ink"]
    if role in ("top_sheet", "top_sheet_external", "top_roll"):
        return ["top_sheet_external", "top_roll", role]
    if role == "lamination":
        return ["lamination", "lamination_film"]
    return [role]


def _build_stage_rows(stage_contexts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = sorted(
        (dict(item) for item in stage_contexts or []),
        key=lambda item: (_safe_int(item.get("sequence"), 0), _safe_int(item.get("index"), 0)),
    )
    stages: List[Dict[str, Any]] = []
    for ctx in ordered:
        output = dict(ctx.get("output_descriptor") or {})
        rm_attachments = list(ctx.get("rm_attachments") or [])
        consumable_attachments = list(ctx.get("consumable_attachments") or [])
        intermediate_attachments = list(ctx.get("intermediate_attachments") or [])
        warnings = [str(w) for w in (ctx.get("warnings") or []) if w]
        stages.append(
            {
                "index": _safe_int(ctx.get("index"), 0),
                "sequence": _safe_int(ctx.get("sequence"), 0),
                "stage_template_id": _safe_int(ctx.get("stage_template_id"), 0),
                "stage_contract_code": str(ctx.get("stage_contract_code") or ""),
                "stage_code": str(ctx.get("stage_code") or ""),
                "stage_name": str(ctx.get("stage_name") or ""),
                "stage_type": str(ctx.get("stage_type") or ""),
                "execution_type": str(ctx.get("execution_type") or ""),
                "capability_code": str(ctx.get("capability_code") or ""),
                "input_quantity_node_key": str(ctx.get("input_quantity_node_key") or ""),
                "output_quantity_node_key": str(ctx.get("output_quantity_node_key") or ""),
                "consumes_previous_output": bool(ctx.get("consumes_previous_output")),
                "previous_output_required": bool(ctx.get("previous_output_required")),
                "output_kind": str(output.get("kind") or ctx.get("output_kind") or ""),
                "output_qty": _safe_float(ctx.get("target_output_qty"), 0.0),
                "output_uom": str(ctx.get("target_output_uom") or output.get("uom") or ""),
                "input_qty": _safe_float(ctx.get("input_qty_from_prev"), 0.0),
                "fg_demand_qty": _safe_float(ctx.get("fg_demand_qty"), 0.0),
                "output_stock_behavior": str(ctx.get("output_stock_behavior") or ""),
                "bom_mapping_policy": str(ctx.get("bom_mapping_policy") or ""),
                "quantity_profile_code": str(ctx.get("quantity_profile_code") or ""),
                "rm_attachment_count": len(rm_attachments),
                "consumable_attachment_count": len(consumable_attachments),
                "intermediate_attachment_count": len(intermediate_attachments),
                "warnings": warnings,
                "notes": list(output.get("tags") or []),
                "computed_metrics": dict(ctx.get("computed_metrics") or {}),
            }
        )
    return stages


def _route_stage_quantities_from_qgraph(qgraph: Optional[Any]) -> Dict[str, Dict[str, Any]]:
    if qgraph is None:
        return {}

    stage_map: Dict[str, Dict[str, Any]] = {}
    try:
        nodes = getattr(qgraph, "nodes", {}) or {}
    except Exception:
        nodes = {}

    for node in nodes.values():
        meta = getattr(node, "meta", {}) or {}
        for item in list(meta.get("route_stage_quantities") or []):
            if not isinstance(item, dict):
                continue
            stage_code = str(item.get("stage_code") or "").strip()
            if not stage_code or stage_code in stage_map:
                continue
            stage_map[stage_code] = dict(item)
    return stage_map


def _overlay_stage_quantities(
    stage_rows: List[Dict[str, Any]],
    qgraph: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    route_stage_map = _route_stage_quantities_from_qgraph(qgraph)
    if not route_stage_map:
        return stage_rows

    rows: List[Dict[str, Any]] = []
    for row in stage_rows:
        stage_code = str(row.get("stage_code") or "").strip()
        overlay = route_stage_map.get(stage_code)
        if not overlay:
            rows.append(row)
            continue

        merged = dict(row)
        merged["input_qty"] = _safe_float(overlay.get("input_qty"), _safe_float(row.get("input_qty"), 0.0))
        merged["output_qty"] = _safe_float(overlay.get("output_qty"), _safe_float(row.get("output_qty"), 0.0))
        overlay_uom = str(overlay.get("output_uom") or "").strip()
        if overlay_uom:
            merged["output_uom"] = overlay_uom

        computed_metrics = dict(merged.get("computed_metrics") or {})
        computed_metrics["quantity_overlay_source"] = "qgraph_route_stage_propagation"
        computed_metrics["quantity_overlay_bucket"] = str(overlay.get("bucket_node") or "")
        computed_metrics["quantity_overlay_operation_key"] = str(overlay.get("operation_key") or "")
        merged["computed_metrics"] = computed_metrics
        rows.append(merged)

    return rows


def _legacy_bucket_for_stage(row: Dict[str, Any]) -> str:
    classifier = _stage_classifier(row)
    bucket_by_classifier = {
        "corrugation": "board",
        "printing": "sheet",
        "lamination": "sheet",
        "diecut": "blanks",
        "conversion": "finished_goods",
        "fg": "finished_goods",
    }
    if classifier in bucket_by_classifier:
        return bucket_by_classifier[classifier]

    output_stock_behavior = str(row.get("output_stock_behavior") or "").strip().lower()
    if output_stock_behavior == "fg":
        return "finished_goods"

    output_kind = str(row.get("output_kind") or "").strip().lower()
    output_kind_bucket_map = {
        "board": "board",
        "sheet": "sheet",
        "box": "finished_goods",
        "fg": "finished_goods",
    }
    return output_kind_bucket_map.get(output_kind, "")


def _legacy_operation_name(row: Dict[str, Any]) -> str:
    classifier = _stage_classifier(row)
    operation_by_classifier = {
        "corrugation": "corrugator",
        "printing": "printing",
        "lamination": "lamination",
        "diecut": "diecut",
        "peeling": "peeloff",
        "stripping": "peeloff",
        "waste stripping": "peeloff",
        "peel-off": "peeloff",
        "peeloff": "peeloff",
        "conversion": "conversion",
        "fg": "fg",
    }
    if classifier in operation_by_classifier:
        return operation_by_classifier[classifier]

    stage_text = " ".join(
        [
            str(row.get("stage_code") or "").strip().lower(),
            str(row.get("stage_type") or "").strip().lower(),
            str(row.get("stage_name") or "").strip().lower(),
            str(row.get("capability_code") or "").strip().lower(),
        ]
    )
    if "peel" in stage_text or "stripping" in stage_text:
        return "peeloff"

    output_stock_behavior = str(row.get("output_stock_behavior") or "").strip().lower()
    if output_stock_behavior == "fg":
        return "fg"

    output_kind = str(row.get("output_kind") or "").strip().lower()
    execution_type = str(row.get("execution_type") or "").strip().lower()
    if output_kind == "board" and execution_type == "material":
        return "corrugator"
    if output_kind in ("box", "fg") and execution_type == "material":
        return "conversion"

    return str(row.get("stage_code") or "")


def _build_flow_buckets(stage_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = ["board", "sheet", "blanks", "finished_goods"]
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in stage_rows:
        bucket = _legacy_bucket_for_stage(row)
        if not bucket:
            continue
        entry = buckets.setdefault(
            bucket,
            {
                "bucket": bucket,
                "qty": 0.0,
                "uom": row.get("output_uom") or "",
                "stages": [],
            },
        )
        entry["qty"] = max(float(entry.get("qty", 0.0) or 0.0), _safe_float(row.get("output_qty"), 0.0))
        entry["uom"] = row.get("output_uom") or entry.get("uom") or ""
        if row.get("stage_code"):
            entry["stages"].append(row.get("stage_code"))
    return [buckets[key] for key in order if key in buckets]


def _build_operations(
    stage_rows: List[Dict[str, Any]],
    material_rows: List[Dict[str, Any]],
    qgraph: Optional[Any] = None,
    metrics: Any = None,
    order_qty: float = 0.0,
) -> List[Dict[str, Any]]:
    def _stage_detail(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "stage_code": str(row.get("stage_code") or ""),
            "stage_contract_code": str(row.get("stage_contract_code") or ""),
            "stage_template_id": _safe_int(row.get("stage_template_id"), 0) or None,
            "input_qty": _safe_float(row.get("input_qty"), 0.0),
            "output_qty": _safe_float(row.get("output_qty"), 0.0),
            "uom": row.get("output_uom") or "",
            "sequence": _safe_int(row.get("sequence"), 0),
        }

    grouped: Dict[str, Dict[str, Any]] = {}
    for row in stage_rows:
        name = _legacy_operation_name(row)
        if not name:
            continue
        current = grouped.get(name)
        if not current:
            grouped[name] = {
                "stage": name,
                "stage_codes": [row.get("stage_code")],
                "input_qty": _safe_float(row.get("input_qty"), 0.0),
                "output_qty": _safe_float(row.get("output_qty"), 0.0),
                "uom": row.get("output_uom") or "",
                "output_kind": row.get("output_kind") or "",
                "execution_type": row.get("execution_type") or "",
                "attachment_count": (
                    _safe_int(row.get("rm_attachment_count"), 0)
                    + _safe_int(row.get("consumable_attachment_count"), 0)
                    + _safe_int(row.get("intermediate_attachment_count"), 0)
                ),
                "stage_details": [_stage_detail(row)],
                "sequence": _safe_int(row.get("sequence"), 0),
            }
            continue

        current["stage_codes"].append(row.get("stage_code"))
        current.setdefault("stage_details", []).append(_stage_detail(row))
        current["input_qty"] = min(
            _safe_float(current.get("input_qty"), 0.0),
            _safe_float(row.get("input_qty"), 0.0),
        )
        current["output_qty"] = max(
            _safe_float(current.get("output_qty"), 0.0),
            _safe_float(row.get("output_qty"), 0.0),
        )
        current["attachment_count"] = _safe_int(current.get("attachment_count"), 0) + (
            _safe_int(row.get("rm_attachment_count"), 0)
            + _safe_int(row.get("consumable_attachment_count"), 0)
            + _safe_int(row.get("intermediate_attachment_count"), 0)
        )
        current["sequence"] = min(
            _safe_int(current.get("sequence"), 0),
            _safe_int(row.get("sequence"), 0),
        )

    graph_overlay: Dict[str, Dict[str, float]] = {}
    if qgraph is not None:
        try:
            nodes = getattr(qgraph, "nodes", {}) or {}
            edges = list(getattr(qgraph, "edges", []) or [])
            edge_map = {(getattr(e, "from_node", ""), getattr(e, "to_node", "")): e for e in edges}

            def _node_qty(node_name: str) -> float:
                return _safe_float(getattr(nodes.get(node_name), "qty", 0.0), 0.0)

            if "raw_material" in nodes and "board" in nodes and ("raw_material", "board") in edge_map:
                graph_overlay["corrugator"] = {
                    "input_qty": _node_qty("raw_material"),
                    "output_qty": _node_qty("board"),
                }
            if "sheet" in nodes and "blanks" in nodes and ("sheet", "blanks") in edge_map:
                graph_overlay["printing"] = {
                    "input_qty": _node_qty("sheet"),
                    "output_qty": _node_qty("blanks"),
                }
            if "blanks" in nodes and "finished_goods" in nodes and ("blanks", "finished_goods") in edge_map:
                graph_overlay["diecut"] = {
                    "input_qty": _node_qty("blanks"),
                    "output_qty": _node_qty("finished_goods"),
                }
        except Exception:
            graph_overlay = {}

    corr_input = _safe_float((graph_overlay.get("corrugator") or {}).get("input_qty"), 0.0)
    if corr_input <= 0.0:
        for item in material_rows or []:
            if _legacy_operation_name(item) == "corrugator":
                corr_input += _safe_float(item.get("total_qty"), 0.0)
    if corr_input <= 0.0:
        for item in material_rows or []:
            corr_input += _safe_float(item.get("total_qty"), 0.0)
    if "corrugator" in grouped and corr_input > 0.0:
        grouped["corrugator"]["input_qty"] = corr_input
    for op_name, overlay in graph_overlay.items():
        if op_name not in grouped:
            continue
        overlay_input = _safe_float(overlay.get("input_qty"), 0.0)
        overlay_output = _safe_float(overlay.get("output_qty"), 0.0)
        if overlay_input > 0.0:
            grouped[op_name]["input_qty"] = overlay_input
        if overlay_output > 0.0:
            grouped[op_name]["output_qty"] = overlay_output

    items = sorted(grouped.values(), key=lambda item: (_safe_int(item.get("sequence"), 0), item.get("stage") or ""))
    for item in items:
        item["stage_details"] = sorted(
            list(item.get("stage_details") or []),
            key=lambda detail: (
                _safe_int(detail.get("sequence"), 0),
                str(detail.get("stage_code") or ""),
            ),
        )
        item.pop("sequence", None)
    return _apply_fg_per_sheet_operation_yield(items, metrics, order_qty)


def _build_routed_operations(
    stage_rows: List[Dict[str, Any]],
    metrics: Any = None,
    order_qty: float = 0.0,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in stage_rows or []:
        operation_key = _legacy_operation_name(row)
        if not operation_key:
            continue
        input_qty = _safe_float(row.get("input_qty"), 0.0)
        output_qty = _safe_float(row.get("output_qty"), 0.0)
        items.append(
            {
                "stage": operation_key,
                "operation_key": operation_key,
                "stage_code": str(row.get("stage_code") or ""),
                "stage_contract_code": str(row.get("stage_contract_code") or ""),
                "stage_template_id": _safe_int(row.get("stage_template_id"), 0) or None,
                "stage_name": str(row.get("stage_name") or ""),
                "stage_type": str(row.get("stage_type") or ""),
                "capability_code": str(row.get("capability_code") or ""),
                "input_qty": input_qty,
                "output_qty": output_qty,
                "uom": row.get("output_uom") or "",
                "output_kind": row.get("output_kind") or "",
                "execution_type": row.get("execution_type") or "",
                "attachment_count": (
                    _safe_int(row.get("rm_attachment_count"), 0)
                    + _safe_int(row.get("consumable_attachment_count"), 0)
                    + _safe_int(row.get("intermediate_attachment_count"), 0)
                ),
                "sequence": _safe_int(row.get("sequence"), 0),
            }
        )
    items = sorted(
        items,
        key=lambda item: (
            _safe_int(item.get("sequence"), 0),
            str(item.get("stage_code") or ""),
            str(item.get("operation_key") or ""),
        ),
    )
    return _apply_fg_per_sheet_operation_yield(items, metrics, order_qty)


def _build_material_rows(
    stage_contexts: List[Dict[str, Any]],
    shadow_runtime: Dict[str, Any],
    rm_requirements: Iterable[Any],
    order_qty: float,
    product_templates: Optional[Dict[int, str]] = None,
    consumable_profile_products: Optional[Dict[str, Dict[str, Any]]] = None,
    paper_resolution: Optional[Iterable[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, int]]:
    product_templates = product_templates or {}
    consumable_profile_products = consumable_profile_products or {}
    legacy_stage_lookup = _legacy_stage_lookup(shadow_runtime)
    legacy = [_normalize_rm_requirement(item, legacy_stage_lookup=legacy_stage_lookup) for item in (rm_requirements or [])]
    used_legacy = set()
    paper_resolution_entries = _normalize_paper_resolution_entries(paper_resolution)
    used_paper_resolution = set()
    replaced_top_liner_layers = _top_replacement_layers(legacy, paper_resolution_entries)
    basis_map = _collect_basis_map(shadow_runtime)
    stage_rows = {str(item.get("stage_code") or ""): item for item in stage_contexts or []}
    consumable_state = dict((shadow_runtime or {}).get("consumable_state") or {})

    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    row_trace: List[Dict[str, Any]] = []
    stats = {
        "matched_qty_rows": 0,
        "pending_qty_rows": 0,
        "legacy_unmatched_rows": 0,
    }
    for entry in paper_resolution_entries:
        if not isinstance(entry, dict):
            continue
        trace_action = str((entry.get("debug") or {}).get("trace_action") or "").strip()
        if trace_action:
            row_trace.append(
                {
                    "action": trace_action,
                    "path": "paper_resolution",
                    "stage_code": str(entry.get("stage_code") or ""),
                    "role": str(entry.get("role") or ""),
                    "layer_no": _safe_int(entry.get("layer_no"), 0) or None,
                    "qty_source": "paper_resolution",
                    "source": "paper_resolution_recovery",
                    "product_key": "",
                    "product": str(entry.get("product_name") or ""),
                    "debug": dict(entry.get("debug") or {}),
                }
            )

    attachment_groups = (
        ("rm", "rm_attachments"),
        ("consumable", "consumable_attachments"),
        ("intermediate", "intermediate_attachments"),
    )

    for ctx in stage_contexts or []:
        stage_code = str(ctx.get("stage_code") or "")
        stage_name = str(ctx.get("stage_name") or "")
        stage_output_qty = _safe_float(ctx.get("target_output_qty"), 0.0)
        stage_output_uom = str(ctx.get("target_output_uom") or "")

        for category, field_name in attachment_groups:
            for att in list(ctx.get(field_name) or []):
                role = _normalize_attachment_role(att.get("role_code") or "", attachment=att)
                display_role = role
                role_aliases = _role_aliases_for_attachment(ctx, att, role)
                product_tmpl_id = att.get("product_tmpl_id")
                material_type = str(att.get("material_type") or category or "")
                stage_identity = _stage_identity_candidates(att) or _stage_identity_candidates(ctx)
                basis = _match_basis(basis_map, stage_identity, role, product_tmpl_id, material_type)
                qty_basis = str(basis.get("attach_quantity_basis") or "")
                rm_item, qty_source = _match_rm_requirement(ctx, att, role_aliases, legacy, used_legacy)
                paper_entry = None
                attachment_metadata = dict(att.get("metadata") or {})
                if consumable_state and category == "consumable" and not should_include_consumable(
                    consumable_state,
                    role,
                    product_key=att.get("product_key") or "",
                    metadata=attachment_metadata,
                    material_type=material_type,
                    category=category,
                ):
                    row_trace.append(
                        {
                            "action": "suppressed_disabled_consumable_attachment",
                            "path": field_name,
                            "stage_code": stage_code,
                            "role": role,
                            "qty_source": "",
                            "source": str(att.get("source") or ""),
                            "product_key": str(att.get("product_key") or ""),
                            "product": "",
                        }
                    )
                    continue

                qty_per_fg = 0.0
                qty_total = 0.0
                qty_uom = ""
                metadata = {}
                notes: List[str] = []
                product_label = ""

                if rm_item:
                    if _is_replaced_liner_requirement(rm_item, replaced_top_liner_layers):
                        row_trace.append(
                            {
                                "action": "suppressed_replaced_liner",
                                "path": "attachment_match",
                                "stage_code": stage_code,
                                "attachment_role": role,
                                "matched_role": str(rm_item.get("role") or ""),
                                "layer_no": _safe_int(rm_item.get("layer_no") or (rm_item.get("metadata") or {}).get("layer_no"), 0) or None,
                                "qty_source": qty_source,
                                "source": str(att.get("source") or ""),
                                "product_key": str(rm_item.get("product_key") or ""),
                                "product": str((rm_item.get("metadata") or {}).get("resolution_product_name") or rm_item.get("product_key") or ""),
                            }
                        )
                        # StageContext may still carry seeded top-liner
                        # attachments, and legacy fallback matching can bind
                        # them before the top-sheet row is rendered. The top
                        # sheet requirement is authoritative for this layer.
                        continue
                    rm_role = str(rm_item.get("role") or "").strip().lower()
                    if rm_role in ("top_roll", "top_sheet_external"):
                        display_role = rm_role
                    matched_paper_resolution = _mark_matching_paper_resolution_used(
                        paper_resolution_entries,
                        used_paper_resolution,
                        rm_item,
                    )
                    qty_per_fg = _safe_float(rm_item.get("qty_per_fg"), 0.0)
                    qty_total = qty_per_fg * float(order_qty or 0.0)
                    qty_uom = str(rm_item.get("qty_uom") or "")
                    metadata = dict(rm_item.get("metadata") or {})
                    order_rounding = str(metadata.get("order_qty_rounding") or "").strip().lower()
                    if order_rounding == "ceil_by_ups" and qty_uom.strip().lower() in ("unit", "units"):
                        ups = _safe_int(metadata.get("carton_board_ups"), 1) or 1
                        qty_total = float(math.ceil(float(order_qty or 0.0) / float(ups or 1)))
                        notes.append("order-level quantity rounded to whole sheets by UPS")
                    if rm_item.get("consumption_type"):
                        metadata.setdefault("consumption_type", str(rm_item.get("consumption_type") or ""))
                    if matched_paper_resolution:
                        resolution_status_from_paper = str(matched_paper_resolution.get("status") or "").strip().lower()
                        resolution_name_from_paper = str(matched_paper_resolution.get("product_name") or "")
                        metadata["resolution_status"] = resolution_status_from_paper
                        metadata["resolution_product_name"] = resolution_name_from_paper
                        metadata["required_deckle_cm"] = matched_paper_resolution.get("required_deckle_cm")
                        if matched_paper_resolution.get("resolution_note"):
                            metadata["resolution_note"] = matched_paper_resolution.get("resolution_note")
                        metadata["resolution"] = dict(matched_paper_resolution)
                    resolution_status = str(metadata.get("resolution_status") or "").strip().lower()
                    resolution_note = str(metadata.get("resolution_note") or "").strip()
                    product_label = (
                        metadata.get("resolution_product_name")
                        or product_templates.get(_safe_int(product_tmpl_id, 0))
                        or rm_item.get("product_key")
                        or ""
                    )
                    if category == "consumable":
                        profile_key = _consumable_profile_key_for_role(
                            display_role or role,
                            metadata,
                            product_key=str(rm_item.get("product_key") or att.get("product_key") or ""),
                        )
                        if profile_key:
                            metadata, product_label, product_tmpl_id, qty_uom = _apply_consumable_profile_product(
                                metadata,
                                product_label,
                                _safe_int(product_tmpl_id, 0) or None,
                                qty_uom,
                                consumable_profile_products.get(profile_key),
                            )
                    if role in ("liner", "medium", "top_sheet_external", "top_roll") and resolution_status == "missing":
                        product_label = "Missing / Unresolved"
                        if resolution_note:
                            notes.append(resolution_note)
                    if qty_source == "legacy_stage_fallback":
                        notes.append("quantity matched by contract fallback")
                else:
                    if role in ("liner", "medium", "top_sheet_external", "top_roll"):
                        if (
                            role == "liner"
                            and not _safe_int(attachment_metadata.get("layer_no"), 0)
                            and replaced_top_liner_layers
                        ):
                            _suppress_replaced_liner_paper_entries(
                                paper_resolution_entries,
                                used_paper_resolution,
                                replaced_top_liner_layers,
                                row_trace,
                                stage_code=stage_code,
                                attachment_role=role,
                                source=str(att.get("source") or ""),
                            )
                        paper_entry = _paper_resolution_for_attachment(
                            role,
                            attachment_metadata,
                            paper_resolution_entries,
                            used_paper_resolution,
                        )
                    if paper_entry:
                        paper_role = str(paper_entry.get("role") or role).strip().lower()
                        paper_layer_no = _safe_int(paper_entry.get("layer_no"), 0) or None
                        if paper_role == "liner" and paper_layer_no in replaced_top_liner_layers:
                            row_trace.append(
                                {
                                    "action": "suppressed_replaced_liner_from_paper_resolution",
                                    "path": "paper_resolution",
                                    "stage_code": stage_code,
                                    "attachment_role": role,
                                    "matched_role": "",
                                    "layer_no": paper_layer_no,
                                    "qty_source": "paper_resolution",
                                    "source": str(att.get("source") or ""),
                                    "product_key": "",
                                    "product": str(paper_entry.get("product_name") or ""),
                                }
                            )
                            continue
                        display_role = str(paper_entry.get("role") or role)
                        _mark_matching_legacy_used(legacy, used_legacy, paper_entry)
                        qty_per_fg = _safe_float(paper_entry.get("qty_per_fg"), 0.0)
                        qty_total = qty_per_fg * float(order_qty or 0.0)
                        qty_uom = str(paper_entry.get("qty_uom") or paper_entry.get("uom_name") or "")
                        metadata = {
                            "layer_no": paper_entry.get("layer_no"),
                            "resolution_status": paper_entry.get("status") or "missing",
                            "resolution_product_name": paper_entry.get("product_name") or "",
                            "resolution_note": paper_entry.get("resolution_note") or "",
                            "required_deckle_cm": paper_entry.get("required_deckle_cm"),
                            "resolution": dict(paper_entry),
                        }
                        product_label = (
                            paper_entry.get("product_name")
                            if (paper_entry.get("status") or "") == "exact"
                            else "Missing / Unresolved"
                        )
                        qty_source = "paper_resolution"
                        if paper_entry.get("resolution_note"):
                            notes.append(str(paper_entry.get("resolution_note")))
                    elif role in ("top_sheet_external", "top_roll"):
                        # Seeded/configured top-sheet attachment templates are
                        # not proof that a job requires a top sheet. Emit them
                        # only when RM rules or paper resolution produced an
                        # actual top-sheet requirement.
                        continue
                    elif role in ("liner", "medium"):
                        if role == "liner" and _is_replaced_liner_attachment(
                            attachment_metadata,
                            replaced_top_liner_layers,
                        ):
                            continue
                        if (
                            role == "liner"
                            and replaced_top_liner_layers
                            and not _safe_int(attachment_metadata.get("layer_no"), 0)
                            and not _has_available_role_requirement(
                                legacy,
                                used_legacy,
                                "liner",
                                replaced_top_liner_layers,
                            )
                        ):
                            row_trace.append(
                                {
                                    "action": "suppressed_replaced_liner",
                                    "path": "unmatched_stage_context_attachment",
                                    "stage_code": stage_code,
                                    "attachment_role": role,
                                    "matched_role": "",
                                    "layer_no": None,
                                    "qty_source": qty_source,
                                    "source": str(att.get("source") or ""),
                                    "product_key": "",
                                    "product": "",
                                }
                            )
                            continue
                        product_label = "Missing / Unresolved"
                        qty_source = "paper_resolution"
                        notes.append("Exact paper variant not found for selected layout")
                    else:
                        product_label = (
                            product_templates.get(_safe_int(product_tmpl_id, 0))
                            or ("tmpl:%s" % product_tmpl_id if product_tmpl_id else "")
                        )
                        if category == "consumable":
                            profile_key = _consumable_profile_key_for_role(
                                display_role or role,
                                metadata,
                                product_key=str(att.get("product_key") or ""),
                            )
                            if profile_key:
                                metadata, product_label, product_tmpl_id, qty_uom = _apply_consumable_profile_product(
                                    metadata,
                                    product_label,
                                    _safe_int(product_tmpl_id, 0) or None,
                                    qty_uom,
                                    consumable_profile_products.get(profile_key),
                                )

                if stage_output_qty > 0.0 and stage_output_uom == "sheet" and qty_basis == "per_box" and qty_uom == "unit":
                    qty_total = stage_output_qty
                    qty_per_fg = (qty_total / float(order_qty or 1.0)) if order_qty else 0.0
                    notes.append("sheet-stage total aligned to StageContext output")

                if not rm_item and not paper_entry and role not in ("liner", "medium", "top_sheet_external", "top_roll"):
                    is_consumable_like = (
                        category == "consumable"
                        or str(material_type or "").strip().lower() == "consumable"
                        or is_consumable_role(
                            role,
                            product_key=att.get("product_key") or "",
                            metadata=attachment_metadata,
                        )
                    )
                    if is_consumable_like:
                        # Preview parity should not show seeded-but-inactive
                        # consumables when the current engine did not compute
                        # any corresponding requirement for this scenario.
                        row_trace.append(
                            {
                                "action": "suppressed_shadow_pending_consumable_attachment",
                                "path": field_name,
                                "stage_code": stage_code,
                                "role": role,
                                "qty_source": qty_source,
                                "source": str(att.get("source") or ""),
                                "product_key": str(att.get("product_key") or ""),
                                "product": product_label,
                            }
                        )
                        continue
                    stats["pending_qty_rows"] += 1
                    notes.append("quantity pending in shadow preview adapter")
                else:
                    stats["matched_qty_rows"] += 1

                rows.append(
                    {
                        "stage_code": stage_code,
                        "stage_template_id": _safe_int(ctx.get("stage_template_id"), 0) or _safe_int(att.get("stage_template_id"), 0),
                        "stage_contract_code": str(att.get("stage_contract_code") or ctx.get("stage_contract_code") or ""),
                        "stage_name": stage_name,
                        "category": category,
                        "role": display_role,
                        "product_tmpl_id": product_tmpl_id,
                        "product_key": str((rm_item or {}).get("product_key") or att.get("product_key") or ""),
                        "product": product_label,
                        "material_type": material_type,
                        "layer_no": _safe_int(metadata.get("layer_no") or (rm_item or {}).get("layer_no"), 0) or None,
                        "qty_per_fg": qty_per_fg,
                        "total_qty": qty_total,
                        "uom": qty_uom,
                        "quantity_basis": qty_basis,
                        "quantity_source": qty_source,
                        "source": str(att.get("source") or ""),
                        "metadata": metadata,
                        "notes": notes,
                        "semantic_role": str(att.get("semantic_role") or ""),
                        "legacy_role_aliases": list(att.get("legacy_role_aliases") or []),
                        "stage_type": str(ctx.get("stage_type") or ""),
                        "capability_code": str(ctx.get("capability_code") or ""),
                        "execution_type": str(ctx.get("execution_type") or ""),
                        "output_kind": str((ctx.get("output_descriptor") or {}).get("kind") or ctx.get("output_kind") or ""),
                        "output_stock_behavior": str(ctx.get("output_stock_behavior") or ""),
                        "input_quantity_node_key": str(att.get("input_quantity_node_key") or ctx.get("input_quantity_node_key") or ""),
                        "output_quantity_node_key": str(att.get("output_quantity_node_key") or ctx.get("output_quantity_node_key") or ""),
                    }
                )
                row_trace.append(
                    {
                        "action": "emitted",
                        "path": "attachment_match" if rm_item else ("paper_resolution" if paper_entry else "stage_context_attachment"),
                        "stage_code": stage_code,
                        "role": display_role,
                        "attachment_role": role,
                        "matched_role": str((rm_item or {}).get("role") or ""),
                        "layer_no": _safe_int(metadata.get("layer_no") or (rm_item or {}).get("layer_no"), 0) or None,
                        "qty_source": qty_source,
                        "source": str(att.get("source") or ""),
                        "product_key": str((rm_item or {}).get("product_key") or ""),
                        "product": product_label,
                    }
                )

    for idx, rm_item in enumerate(legacy):
        if idx in used_legacy:
            continue
        if _is_replaced_liner_requirement(rm_item, replaced_top_liner_layers):
            row_trace.append(
                {
                    "action": "suppressed_replaced_liner",
                    "path": "unmatched_legacy_rm_requirement",
                    "stage_code": str(rm_item.get("stage_key") or ""),
                    "role": str(rm_item.get("role") or ""),
                    "layer_no": _safe_int(rm_item.get("layer_no") or (rm_item.get("metadata") or {}).get("layer_no"), 0) or None,
                    "qty_source": "legacy_rm_requirement",
                    "source": "legacy_rm_requirement",
                    "product_key": str(rm_item.get("product_key") or ""),
                    "product": str((rm_item.get("metadata") or {}).get("resolution_product_name") or rm_item.get("product_key") or ""),
                }
            )
            used_legacy.add(idx)
            continue
        metadata = dict(rm_item.get("metadata") or {})
        original_stage_code = str(rm_item.get("stage_key") or "")
        stage_code, fallback_metadata = _fallback_stage_code_for_rm_requirement(
            original_stage_code,
            rm_item,
            stage_rows,
        )
        if fallback_metadata:
            metadata.update(fallback_metadata)
        stage_row = stage_rows.get(stage_code) or {}
        category = _legacy_category_for_requirement(rm_item)
        profile_product = (
            consumable_profile_products.get(_consumable_profile_key_for_requirement(rm_item))
            if category == "consumable"
            else None
        ) or {}
        profile_product_tmpl_id = _safe_int(profile_product.get("product_tmpl_id"), 0) or None
        if profile_product_tmpl_id:
            metadata.setdefault("product_tmpl_id", profile_product_tmpl_id)
            metadata.setdefault("product_name", profile_product.get("product_label") or "")
            metadata.setdefault("resolution_product_name", profile_product.get("product_label") or "")
            metadata.setdefault("resolution_status", "resolved")
            metadata.setdefault("resolution_source", "engine_material_profile_role")
            metadata.setdefault(
                "resolution",
                {
                    "status": "resolved",
                    "source": "engine_material_profile_role",
                    "product_tmpl_id": profile_product_tmpl_id,
                    "product_name": profile_product.get("product_label") or "",
                },
            )
        legacy_qty_uom = str(rm_item.get("qty_uom") or "")
        if category == "consumable":
            metadata, _profile_label, _profile_tmpl_id, legacy_qty_uom = _apply_consumable_profile_product(
                metadata,
                metadata.get("resolution_product_name")
                or profile_product.get("product_label")
                or product_templates.get(_safe_int(rm_item.get("product_tmpl_id"), 0) or profile_product_tmpl_id or 0)
                or rm_item.get("product_key")
                or "",
                _safe_int(rm_item.get("product_tmpl_id"), 0) or profile_product_tmpl_id or None,
                legacy_qty_uom,
                profile_product,
            )
            profile_product_tmpl_id = _profile_tmpl_id or profile_product_tmpl_id
        if consumable_state and category == "consumable" and not should_include_consumable(
            consumable_state,
            rm_item.get("role") or "",
            product_key=rm_item.get("product_key") or "",
            metadata=metadata,
            material_type=rm_item.get("material_type") or category,
            category=category,
        ):
            row_trace.append(
                {
                    "action": "suppressed_disabled_consumable_legacy_requirement",
                    "path": "unmatched_legacy_rm_requirement",
                    "stage_code": original_stage_code,
                    "role": str(rm_item.get("role") or ""),
                    "layer_no": _safe_int(rm_item.get("layer_no") or metadata.get("layer_no"), 0) or None,
                    "qty_source": "legacy_rm_requirement",
                    "source": "legacy_rm_requirement",
                    "product_key": str(rm_item.get("product_key") or ""),
                    "product": str(metadata.get("resolution_product_name") or rm_item.get("product_key") or ""),
                }
            )
            used_legacy.add(idx)
            continue
        legacy_qty_per_fg = _safe_float(rm_item.get("qty_per_fg"), 0.0)
        legacy_total_qty = legacy_qty_per_fg * float(order_qty or 0.0)
        legacy_notes = ["emitted from unmatched legacy rm requirement"]
        if str(metadata.get("order_qty_rounding") or "").strip().lower() == "ceil_by_ups" and legacy_qty_uom.strip().lower() in ("unit", "units"):
            ups = _safe_int(metadata.get("carton_board_ups"), 1) or 1
            legacy_total_qty = float(math.ceil(float(order_qty or 0.0) / float(ups or 1)))
            legacy_notes.append("order-level quantity rounded to whole sheets by UPS")
        if fallback_metadata:
            legacy_notes.append(
                "fallback stage %s -> %s"
                % (fallback_metadata.get("original_semantic_stage"), fallback_metadata.get("assigned_route_stage"))
            )
        if metadata.get("resolution_note"):
            legacy_notes.append(metadata.get("resolution_note"))
        rows.append(
            {
                "stage_code": stage_code,
                "stage_template_id": _safe_int(rm_item.get("stage_template_id"), 0) or _safe_int(stage_row.get("stage_template_id"), 0) or None,
                "stage_contract_code": str(rm_item.get("stage_contract_code") or stage_row.get("stage_contract_code") or ""),
                "stage_name": str(stage_row.get("stage_name") or stage_code or ""),
                "category": category,
                "role": str(rm_item.get("role") or ""),
                "product_tmpl_id": _safe_int(rm_item.get("product_tmpl_id"), 0) or profile_product_tmpl_id or None,
                "product": (
                    "Missing / Unresolved"
                    if str(metadata.get("resolution_status") or "").strip().lower() == "missing"
                    and str(rm_item.get("role") or "").strip().lower() in ("liner", "medium", "top_roll")
                    else (
                        metadata.get("resolution_product_name")
                        or product_templates.get(_safe_int(rm_item.get("product_tmpl_id"), 0) or profile_product_tmpl_id or 0)
                        or rm_item.get("product_key")
                        or ""
                    )
                ),
                "material_type": str(rm_item.get("material_type") or category),
                "layer_no": _safe_int(rm_item.get("layer_no") or metadata.get("layer_no"), 0) or None,
                "qty_per_fg": legacy_qty_per_fg,
                "total_qty": legacy_total_qty,
                "uom": legacy_qty_uom,
                "quantity_basis": "",
                "quantity_source": "legacy_rm_requirement",
                "source": "legacy_rm_requirement",
                "metadata": metadata,
                "notes": list(filter(None, legacy_notes)),
                "semantic_role": str(rm_item.get("semantic_role") or ""),
                "legacy_role_aliases": list(rm_item.get("legacy_role_aliases") or []),
                "stage_type": str(stage_row.get("stage_type") or ""),
                "capability_code": str(stage_row.get("capability_code") or ""),
                "execution_type": str(stage_row.get("execution_type") or ""),
                "output_kind": str((stage_row.get("output_descriptor") or {}).get("kind") or stage_row.get("output_kind") or ""),
                "output_stock_behavior": str(stage_row.get("output_stock_behavior") or ""),
                "input_quantity_node_key": str(stage_row.get("input_quantity_node_key") or ""),
                "output_quantity_node_key": str(stage_row.get("output_quantity_node_key") or ""),
            }
        )
        used_legacy.add(idx)
        row_trace.append(
            {
                "action": "emitted",
                "path": "unmatched_legacy_rm_requirement",
                "stage_code": stage_code,
                "original_stage_code": original_stage_code,
                "role": str(rm_item.get("role") or ""),
                "layer_no": _safe_int(rm_item.get("layer_no") or metadata.get("layer_no"), 0) or None,
                "qty_source": "legacy_rm_requirement",
                "source": "legacy_rm_requirement",
                "product_key": str(rm_item.get("product_key") or ""),
                "product": str(metadata.get("resolution_product_name") or rm_item.get("product_key") or ""),
            }
        )

    matched_roles = {row.get("role") for row in rows if row.get("quantity_source") != "shadow_pending"}
    filtered_rows = []
    for row in rows:
        if (
            row.get("quantity_source") == "paper_resolution"
            and not _safe_float(row.get("total_qty"), 0.0)
        ):
            row_trace.append(
                {
                    "action": "suppressed_zero_qty_paper_resolution_row",
                    "path": "final_filter",
                    "stage_code": str(row.get("stage_code") or ""),
                    "role": str(row.get("role") or ""),
                    "layer_no": _safe_int(row.get("layer_no"), 0) or None,
                    "qty_source": str(row.get("quantity_source") or ""),
                    "source": str(row.get("source") or ""),
                    "product_key": "",
                    "product": str(row.get("product") or ""),
                }
            )
            continue
        if (
            row.get("quantity_source") == "shadow_pending"
            and not _safe_float(row.get("total_qty"), 0.0)
            and row.get("role") in matched_roles
        ):
            row_trace.append(
                {
                    "action": "suppressed_shadow_pending_duplicate_role",
                    "path": "final_filter",
                    "stage_code": str(row.get("stage_code") or ""),
                    "role": str(row.get("role") or ""),
                    "layer_no": _safe_int(row.get("layer_no"), 0) or None,
                    "qty_source": str(row.get("quantity_source") or ""),
                    "source": str(row.get("source") or ""),
                    "product_key": "",
                    "product": str(row.get("product") or ""),
                }
            )
            continue
        if (
            row.get("category") == "consumable"
            and consumable_state
            and (
                not _safe_float(row.get("total_qty"), 0.0)
                or not should_include_consumable(
                    consumable_state,
                    row.get("role") or "",
                    product_key=row.get("product_key") or "",
                    metadata=row.get("metadata") or {},
                    material_type=row.get("material_type") or "",
                    category=row.get("category") or "",
                )
            )
        ):
            row_trace.append(
                {
                    "action": "suppressed_disabled_or_zero_consumable_row",
                    "path": "final_filter",
                    "stage_code": str(row.get("stage_code") or ""),
                    "role": str(row.get("role") or ""),
                    "layer_no": _safe_int(row.get("layer_no"), 0) or None,
                    "qty_source": str(row.get("quantity_source") or ""),
                    "source": str(row.get("source") or ""),
                    "product_key": "",
                    "product": str(row.get("product") or ""),
                }
            )
            continue
        filtered_rows.append(row)
    rows = _dedupe_material_rows_prefer_resolved(filtered_rows, row_trace)
    rows = _assign_remaining_liner_rows_to_board_stage(rows, stage_rows, row_trace)
    rows = _apply_multi_piece_fg_rollup_from_legacy_requirements(
        rows,
        legacy,
        row_trace,
        order_qty,
    )
    rows = _suppress_unit_top_sheet_legacy_paper_fallbacks(rows, row_trace)
    for row in rows:
        row_trace.append(
            {
                "action": "final_material_row",
                "path": "final_material_rows",
                "stage_code": str(row.get("stage_code") or ""),
                "role": str(row.get("role") or ""),
                "layer_no": _safe_int(row.get("layer_no"), 0) or None,
                "qty_source": str(row.get("quantity_source") or ""),
                "source": str(row.get("source") or ""),
                "product_key": "",
                "product": str(row.get("product") or ""),
            }
        )

    stats["matched_qty_rows"] = len([row for row in rows if row.get("quantity_source") != "shadow_pending"])
    stats["pending_qty_rows"] = len([row for row in rows if row.get("quantity_source") == "shadow_pending"])
    stats["legacy_unmatched_rows"] = len([idx for idx in range(len(legacy)) if idx not in used_legacy])
    stats["row_trace"] = row_trace
    if stats["pending_qty_rows"]:
        warnings.append(
            "preview_adapter:%s shadow attachment rows still lack quantitative parity"
            % stats["pending_qty_rows"]
        )
    if stats["legacy_unmatched_rows"]:
        warnings.append(
            "preview_adapter:%s legacy rm rows were not mapped to StageContext attachments"
            % stats["legacy_unmatched_rows"]
        )

    rows.sort(
        key=lambda item: (
            0
            if item.get("category") == "rm"
            and item.get("role") in ("top_roll", "top_sheet_external", "liner", "medium")
            and _safe_int(item.get("layer_no"), 0)
            else 1,
            _safe_int(item.get("layer_no"), 0)
            if item.get("category") == "rm"
            and item.get("role") in ("top_roll", "top_sheet_external", "liner", "medium")
            and _safe_int(item.get("layer_no"), 0)
            else 0,
            _safe_int(stage_rows.get(item.get("stage_code"), {}).get("sequence"), 0),
            item.get("stage_code") or "",
            item.get("category") or "",
            item.get("role") or "",
        )
    )
    return rows, warnings, stats


def _assign_remaining_liner_rows_to_board_stage(
    rows: List[Dict[str, Any]],
    stage_rows: Dict[str, Dict[str, Any]],
    row_trace: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Move top-surface liner rows from 2-ply to board when the route has both stages.

    A route with separate 2ply and board material stages represents a
    single-face subassembly followed by board formation. Legacy RM rows can
    still arrive with every liner/medium layer assigned to 2ply. For preview
    and BoM-from-preview, the top-surface liner belongs to the board stage so
    the board BoM consumes the 2-ply output plus that liner/top-sheet layer.
    """

    if not rows or not stage_rows:
        return rows

    two_ply_codes = [
        code
        for code in stage_rows.keys()
        if _canonical_route_stage(code) == "2ply"
    ]
    board_codes = [
        code
        for code in stage_rows.keys()
        if _canonical_route_stage(code) == "board"
    ]
    if not two_ply_codes or not board_codes:
        return rows

    two_ply_code_set = set(two_ply_codes)
    board_code_set = set(board_codes)
    explicit_surface_row = _explicit_top_sheet_surface_stage_row(rows, board_code_set)
    if explicit_surface_row:
        row_trace.append(
            {
                "action": "preserved_2ply_liner_due_explicit_top_sheet_stage",
                "path": "route_material_semantics",
                "stage_code": str(explicit_surface_row.get("stage_code") or ""),
                "role": str(explicit_surface_row.get("role") or ""),
                "layer_no": _safe_int(
                    explicit_surface_row.get("layer_no")
                    or (explicit_surface_row.get("metadata") or {}).get("layer_no"),
                    0,
                )
                or None,
                "qty_source": str(explicit_surface_row.get("quantity_source") or ""),
                "source": str(explicit_surface_row.get("source") or ""),
                "product_key": str(explicit_surface_row.get("product_key") or ""),
                "product": str(explicit_surface_row.get("product") or ""),
            }
        )
        return rows

    has_board_surface_row = any(
        str(row.get("stage_code") or "") in board_code_set
        and str(row.get("role") or "").strip().lower() in {"liner", "top_sheet_external", "top_sheet", "top_roll"}
        for row in rows
    )
    if has_board_surface_row:
        return rows

    has_2ply_medium = any(
        str(row.get("stage_code") or "") in two_ply_code_set
        and str(row.get("role") or "").strip().lower() == "medium"
        for row in rows
    )
    if not has_2ply_medium:
        return rows

    liner_candidates = [
        row
        for row in rows
        if str(row.get("stage_code") or "") in two_ply_code_set
        and str(row.get("category") or "") == "rm"
        and str(row.get("role") or "").strip().lower() == "liner"
    ]
    if not liner_candidates:
        return rows

    top_surface_layer_no = min(
        _safe_int(row.get("layer_no") or (row.get("metadata") or {}).get("layer_no"), 0)
        for row in liner_candidates
    )
    if top_surface_layer_no <= 0:
        return rows

    board_code = board_codes[0]
    board_stage = stage_rows.get(board_code) or {}
    changed = False
    result: List[Dict[str, Any]] = []
    for row in rows:
        layer_no = _safe_int(row.get("layer_no") or (row.get("metadata") or {}).get("layer_no"), 0)
        should_move = (
            row in liner_candidates
            and layer_no == top_surface_layer_no
        )
        if not should_move:
            result.append(row)
            continue

        original_stage = str(row.get("stage_code") or "")
        updated = dict(row)
        metadata = dict(updated.get("metadata") or {})
        notes = list(updated.get("notes") or [])
        metadata.setdefault("original_semantic_stage", _canonical_route_stage(original_stage) or original_stage)
        metadata["assigned_route_stage"] = board_code
        metadata["fallback_reason"] = "top_surface_liner_board_stage"
        updated.update(
            {
                "stage_code": board_code,
                "stage_template_id": _safe_int(board_stage.get("stage_template_id"), 0) or updated.get("stage_template_id"),
                "stage_contract_code": str(board_stage.get("stage_contract_code") or updated.get("stage_contract_code") or ""),
                "stage_name": str(board_stage.get("stage_name") or board_code or ""),
                "stage_type": str(board_stage.get("stage_type") or ""),
                "capability_code": str(board_stage.get("capability_code") or ""),
                "execution_type": str(board_stage.get("execution_type") or ""),
                "output_kind": str((board_stage.get("output_descriptor") or {}).get("kind") or board_stage.get("output_kind") or ""),
                "output_stock_behavior": str(board_stage.get("output_stock_behavior") or ""),
                "input_quantity_node_key": str(board_stage.get("input_quantity_node_key") or ""),
                "output_quantity_node_key": str(board_stage.get("output_quantity_node_key") or ""),
                "metadata": metadata,
                "notes": notes,
            }
        )
        if "assigned to board stage as top surface liner" not in notes:
            notes.append("assigned to board stage as top surface liner")
        row_trace.append(
            {
                "action": "assigned_top_surface_liner_to_board_stage",
                "path": "route_material_semantics",
                "stage_code": board_code,
                "original_stage_code": original_stage,
                "role": str(row.get("role") or ""),
                "layer_no": layer_no,
                "qty_source": str(row.get("quantity_source") or ""),
                "source": str(row.get("source") or ""),
                "product_key": "",
                "product": str(row.get("product") or ""),
            }
        )
        result.append(updated)
        changed = True

    return result if changed else rows


def _explicit_top_sheet_surface_stage_row(
    rows: List[Dict[str, Any]],
    board_code_set: set,
) -> Optional[Dict[str, Any]]:
    """Return the row when a separate route stage already owns top-sheet paper."""

    for row in rows or []:
        role = str(row.get("role") or "").strip().lower()
        if role not in {"top_sheet_external", "top_sheet", "top_roll"}:
            continue
        stage_code = str(row.get("stage_code") or "")
        if not stage_code or stage_code in board_code_set:
            continue
        return row
    return None


def _base_stage_code(value: Any) -> str:
    text = str(value or "").strip()
    if "__piece_" in text:
        return text.split("__piece_", 1)[0]
    return text


def _material_product_identity(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _multi_piece_material_rollup_key(
    *,
    stage_code: Any,
    category: Any,
    role: Any,
    product: Any,
    layer_no: Any,
    uom: Any,
) -> Tuple[Any, ...]:
    return (
        _base_stage_code(stage_code),
        str(category or ""),
        str(role or ""),
        _material_product_identity(product),
        _safe_int(layer_no, 0) or "",
        str(uom or ""),
    )


def _legacy_material_product_label(row: Dict[str, Any]) -> str:
    metadata = dict(row.get("metadata") or {})
    resolution = dict(metadata.get("resolution") or {})
    return (
        metadata.get("resolution_product_name")
        or metadata.get("product_label")
        or metadata.get("product_name")
        or resolution.get("resolution_product_name")
        or resolution.get("product_name")
        or row.get("product_key")
        or ""
    )


def _multi_piece_material_fallback_rollup_key(
    *,
    stage_code: Any,
    category: Any,
    role: Any,
    layer_no: Any,
    uom: Any,
) -> Tuple[Any, ...]:
    return (
        _base_stage_code(stage_code),
        str(category or ""),
        str(role or ""),
        _safe_int(layer_no, 0) or "",
        str(uom or ""),
    )


def _paper_display_attrs_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    resolution = dict(metadata.get("resolution") or {})
    product_text = str(
        row.get("product")
        or metadata.get("resolution_product_name")
        or metadata.get("product_name")
        or resolution.get("product_name")
        or ""
    )
    attrs = {
        "gsm": metadata.get("gsm") or resolution.get("gsm"),
        "bf": metadata.get("bf") or resolution.get("bf"),
        "color": metadata.get("color") or resolution.get("color"),
        "flute": metadata.get("flute") or metadata.get("flute_code") or resolution.get("flute") or resolution.get("flute_code"),
    }
    # Product display names often carry variant values even when row metadata
    # is sparse, for example: Kraft Paper (180, 56, Natural Shade(NS), 18).
    if product_text and "(" in product_text and ")" in product_text:
        inside = product_text.split("(", 1)[-1].rsplit(")", 1)[0]
        parts = [part.strip() for part in inside.split(",")]
        if len(parts) >= 4:
            if not attrs.get("gsm"):
                attrs["gsm"] = parts[0]
            if not attrs.get("color"):
                attrs["color"] = parts[2]
            if not attrs.get("bf"):
                attrs["bf"] = parts[3]
    return {key: value for key, value in attrs.items() if value not in (None, "")}


def _apply_multi_piece_fg_rollup_from_legacy_requirements(
    rows: List[Dict[str, Any]],
    legacy: List[Dict[str, Any]],
    row_trace: List[Dict[str, Any]],
    order_qty: float,
) -> List[Dict[str, Any]]:
    aggregates: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    fallback_aggregates: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for rm in legacy or []:
        metadata = dict(rm.get("metadata") or {})
        if not metadata.get("multi_piece") and not metadata.get("piece_key"):
            continue
        role = str(rm.get("role") or "").strip().lower()
        if role not in {"liner", "medium", "top_roll", "top_sheet_external", "lamination_film"}:
            continue
        product = _legacy_material_product_label(rm)
        key = _multi_piece_material_rollup_key(
            stage_code=rm.get("stage_key"),
            category=_legacy_category_for_requirement(rm),
            role=rm.get("role"),
            product=product,
            layer_no=rm.get("layer_no") or metadata.get("layer_no"),
            uom=rm.get("qty_uom"),
        )
        entry = aggregates.setdefault(
            key,
            {
                "qty_per_fg": 0.0,
                "total_qty": 0.0,
                "piece_keys": [],
            },
        )
        fallback_key = _multi_piece_material_fallback_rollup_key(
            stage_code=rm.get("stage_key"),
            category=_legacy_category_for_requirement(rm),
            role=rm.get("role"),
            layer_no=rm.get("layer_no") or metadata.get("layer_no"),
            uom=rm.get("qty_uom"),
        )
        fallback_entry = fallback_aggregates.setdefault(
            fallback_key,
            {
                "qty_per_fg": 0.0,
                "total_qty": 0.0,
                "piece_keys": [],
            },
        )
        qty_per_fg = _safe_float(rm.get("qty_per_fg"), 0.0)
        for target in (entry, fallback_entry):
            target["qty_per_fg"] += qty_per_fg
            target["total_qty"] += qty_per_fg * float(order_qty or 0.0)
        piece_key = str(metadata.get("piece_key") or "").strip()
        for target in (entry, fallback_entry):
            if piece_key and piece_key not in target["piece_keys"]:
                target["piece_keys"].append(piece_key)

    if not aggregates:
        return rows

    for row in rows:
        role = str(row.get("role") or "").strip().lower()
        if role not in {"liner", "medium", "top_roll", "top_sheet_external", "lamination_film"}:
            continue
        key = _multi_piece_material_rollup_key(
            stage_code=row.get("stage_code"),
            category=row.get("category"),
            role=row.get("role"),
            product=row.get("product"),
            layer_no=row.get("layer_no") or (row.get("metadata") or {}).get("layer_no"),
            uom=row.get("uom"),
        )
        entry = aggregates.get(key)
        if not entry:
            fallback_key = _multi_piece_material_fallback_rollup_key(
                stage_code=row.get("stage_code"),
                category=row.get("category"),
                role=row.get("role"),
                layer_no=row.get("layer_no") or (row.get("metadata") or {}).get("layer_no"),
                uom=row.get("uom"),
            )
            entry = fallback_aggregates.get(fallback_key)
        if not entry:
            continue
        display_attrs = _paper_display_attrs_from_row(row)
        if display_attrs:
            metadata = dict(row.get("metadata") or {})
            resolution = dict(metadata.get("resolution") or {})
            for attr_key, attr_value in display_attrs.items():
                metadata.setdefault(attr_key, attr_value)
                resolution.setdefault(attr_key, attr_value)
            metadata["resolution"] = resolution
            row["metadata"] = metadata
        old_qty_per_fg = _safe_float(row.get("qty_per_fg"), 0.0)
        new_qty_per_fg = _safe_float(entry.get("qty_per_fg"), 0.0)
        metadata = dict(row.get("metadata") or {})
        piece_keys = list(entry.get("piece_keys") or [])
        if piece_keys:
            metadata["rollup_piece_keys"] = piece_keys
            metadata["piece_key"] = ",".join(piece_keys)
        metadata["multi_piece"] = True
        metadata["fg_level_rollup"] = True
        metadata["fg_level_rollup_source"] = "legacy_rm_requirements"
        row["metadata"] = metadata
        if new_qty_per_fg <= 0.0 or abs(new_qty_per_fg - old_qty_per_fg) < 0.00000001:
            continue
        row["qty_per_fg"] = new_qty_per_fg
        row["total_qty"] = _safe_float(entry.get("total_qty"), new_qty_per_fg * float(order_qty or 0.0))
        notes = list(row.get("notes") or [])
        if "multi-piece FG-level quantity rollup" not in notes:
            notes.append("multi-piece FG-level quantity rollup")
        row["notes"] = notes
        row_trace.append(
            {
                "action": "applied_multi_piece_fg_rollup_quantity",
                "path": "final_rollup",
                "stage_code": str(row.get("stage_code") or ""),
                "role": str(row.get("role") or ""),
                "layer_no": _safe_int(row.get("layer_no"), 0) or None,
                "qty_source": str(row.get("quantity_source") or ""),
                "source": str(row.get("source") or ""),
                "product_key": "",
                "product": str(row.get("product") or ""),
                "old_qty_per_fg": old_qty_per_fg,
                "rollup_qty_per_fg": row.get("qty_per_fg"),
                "rollup_total_qty": row.get("total_qty"),
            }
        )
    return rows


def _dedupe_material_rows_prefer_resolved(
    rows: List[Dict[str, Any]],
    row_trace: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    index_by_identity: Dict[Tuple[Any, ...], int] = {}
    multi_piece_index_by_identity: Dict[Tuple[Any, ...], int] = {}
    has_unit_top_sheet = any(
        str(row.get("role") or "").strip().lower() == "top_sheet_external"
        and str(row.get("uom") or "").strip().lower() in {"unit", "units", "sheet", "sheets", "pcs", "pc"}
        for row in rows
    )

    def _identity(row: Dict[str, Any]) -> Tuple[Any, ...]:
        return (
            str(row.get("stage_code") or ""),
            str(row.get("category") or ""),
            str(row.get("role") or ""),
            str(row.get("product") or ""),
            _safe_int(row.get("layer_no"), 0) or "",
            round(_safe_float(row.get("qty_per_fg"), 0.0), 8),
            str(row.get("uom") or ""),
        )

    def _multi_piece_rollup_identity(row: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
        metadata = dict(row.get("metadata") or {})
        if not metadata.get("multi_piece") and not metadata.get("piece_key"):
            return None
        role = str(row.get("role") or "").strip().lower()
        if role not in {"liner", "medium", "top_roll", "top_sheet_external", "lamination_film"}:
            return None
        return _multi_piece_material_rollup_key(
            stage_code=row.get("stage_code"),
            category=row.get("category"),
            role=row.get("role"),
            product=row.get("product"),
            layer_no=row.get("layer_no"),
            uom=row.get("uom"),
        )

    def _merge_multi_piece_row(existing: Dict[str, Any], row: Dict[str, Any]) -> None:
        existing["qty_per_fg"] = _safe_float(existing.get("qty_per_fg"), 0.0) + _safe_float(row.get("qty_per_fg"), 0.0)
        existing["total_qty"] = _safe_float(existing.get("total_qty"), 0.0) + _safe_float(row.get("total_qty"), 0.0)
        existing_metadata = dict(existing.get("metadata") or {})
        row_metadata = dict(row.get("metadata") or {})
        piece_keys = list(existing_metadata.get("rollup_piece_keys") or [])
        for key in (existing_metadata.get("piece_key"), row_metadata.get("piece_key")):
            text = str(key or "").strip()
            if text and text not in piece_keys:
                piece_keys.append(text)
        if piece_keys:
            existing_metadata["rollup_piece_keys"] = piece_keys
            existing_metadata["piece_key"] = ",".join(piece_keys)
        existing_metadata["multi_piece"] = True
        existing_metadata["fg_level_rollup"] = True
        existing["metadata"] = existing_metadata
        notes = list(existing.get("notes") or [])
        for note in list(row.get("notes") or []):
            if note not in notes:
                notes.append(note)
        if "multi-piece FG-level quantity rollup" not in notes:
            notes.append("multi-piece FG-level quantity rollup")
        existing["notes"] = notes

    def _unit_sheet_shift_identity(row: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
        if not has_unit_top_sheet:
            return None
        role = str(row.get("role") or "").strip().lower()
        if role not in {"liner", "medium"}:
            return None
        metadata = dict(row.get("metadata") or {})
        layer_no = _safe_int(row.get("layer_no") or metadata.get("layer_no"), 0) or None
        source_layer_no = _safe_int(metadata.get("source_layer_no"), 0) or None
        return (
            str(row.get("stage_code") or "").strip().lower(),
            str(row.get("category") or "").strip().lower(),
            role,
            layer_no,
            source_layer_no,
            " ".join(str(row.get("product") or "").split()).lower(),
            str(row.get("uom") or "").strip().lower(),
        )

    def _resolution_rank(row: Dict[str, Any]) -> int:
        source = str(row.get("quantity_source") or "")
        metadata = dict(row.get("metadata") or {})
        if source == "paper_resolution":
            return 2
        if bool(metadata.get("resolution_product_name")):
            return 1
        return 0

    def _find_shifted_legacy_duplicate_index(row: Dict[str, Any]) -> Optional[int]:
        shift_ident = _unit_sheet_shift_identity(row)
        if shift_ident is None:
            return None
        row_rank = _resolution_rank(row)
        for index, existing in enumerate(result):
            if _unit_sheet_shift_identity(existing) != shift_ident:
                continue
            if _resolution_rank(existing) == row_rank:
                continue
            return index
        return None

    def _suppress_duplicate(
        existing: Dict[str, Any],
        row: Dict[str, Any],
        existing_index: int,
    ) -> None:
        if _resolution_rank(row) > _resolution_rank(existing):
            row_trace.append(
                {
                    "action": "suppressed_duplicate_legacy_material_row",
                    "path": "final_dedupe",
                    "stage_code": str(existing.get("stage_code") or ""),
                    "role": str(existing.get("role") or ""),
                    "layer_no": _safe_int(existing.get("layer_no"), 0) or None,
                    "qty_source": str(existing.get("quantity_source") or ""),
                    "source": str(existing.get("source") or ""),
                    "product_key": "",
                    "product": str(existing.get("product") or ""),
                    "kept_qty_source": str(row.get("quantity_source") or ""),
                    "kept_layer_no": _safe_int(row.get("layer_no"), 0) or None,
                }
            )
            result[existing_index] = row
        else:
            row_trace.append(
                {
                    "action": "suppressed_duplicate_material_row",
                    "path": "final_dedupe",
                    "stage_code": str(row.get("stage_code") or ""),
                    "role": str(row.get("role") or ""),
                    "layer_no": _safe_int(row.get("layer_no"), 0) or None,
                    "qty_source": str(row.get("quantity_source") or ""),
                    "source": str(row.get("source") or ""),
                    "product_key": "",
                    "product": str(row.get("product") or ""),
                    "kept_qty_source": str(existing.get("quantity_source") or ""),
                    "kept_layer_no": _safe_int(existing.get("layer_no"), 0) or None,
                }
            )

    for row in rows:
        multi_piece_ident = _multi_piece_rollup_identity(row)
        if multi_piece_ident is not None:
            existing_index = multi_piece_index_by_identity.get(multi_piece_ident)
            if existing_index is not None:
                existing = result[existing_index]
                _merge_multi_piece_row(existing, row)
                row_trace.append(
                    {
                        "action": "merged_multi_piece_material_row",
                        "path": "final_dedupe",
                        "stage_code": str(row.get("stage_code") or ""),
                        "role": str(row.get("role") or ""),
                        "layer_no": _safe_int(row.get("layer_no"), 0) or None,
                        "qty_source": str(row.get("quantity_source") or ""),
                        "source": str(row.get("source") or ""),
                        "product_key": "",
                        "product": str(row.get("product") or ""),
                        "kept_stage_code": str(existing.get("stage_code") or ""),
                        "rollup_qty_per_fg": existing.get("qty_per_fg"),
                        "rollup_total_qty": existing.get("total_qty"),
                    }
                )
                continue
            multi_piece_index_by_identity[multi_piece_ident] = len(result)

        ident = _identity(row)
        existing_index = index_by_identity.get(ident)
        if existing_index is None:
            existing_index = _find_shifted_legacy_duplicate_index(row)
        if existing_index is None:
            index_by_identity[ident] = len(result)
            result.append(row)
            continue
        existing = result[existing_index]
        _suppress_duplicate(existing, row, existing_index)
    return result


def _suppress_unit_top_sheet_legacy_paper_fallbacks(
    rows: List[Dict[str, Any]],
    row_trace: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    has_unit_top_sheet = any(
        str(row.get("role") or "").strip().lower() == "top_sheet_external"
        and str(row.get("uom") or "").strip().lower() in {"unit", "units", "sheet", "sheets", "pcs", "pc"}
        for row in rows
    )
    if not has_unit_top_sheet:
        return rows

    def _paper_key(row: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
        role = str(row.get("role") or "").strip().lower()
        if role not in {"liner", "medium"}:
            return None
        metadata = dict(row.get("metadata") or {})
        layer_no = _safe_int(row.get("layer_no") or metadata.get("layer_no"), 0) or None
        source_layer_no = _safe_int(metadata.get("source_layer_no"), 0) or None
        product = " ".join(str(row.get("product") or "").split()).strip().lower()
        if not product:
            return None
        return (
            str(row.get("stage_code") or "").strip().lower(),
            str(row.get("category") or "").strip().lower(),
            role,
            layer_no,
            source_layer_no,
            product,
            str(row.get("uom") or "").strip().lower(),
        )

    resolved_keys = set()
    for row in rows:
        if str(row.get("quantity_source") or "").strip() != "paper_resolution":
            continue
        key = _paper_key(row)
        if key:
            resolved_keys.add(key)
    if not resolved_keys:
        return rows

    kept: List[Dict[str, Any]] = []
    for row in rows:
        source = str(row.get("quantity_source") or "").strip()
        key = _paper_key(row)
        if key in resolved_keys and source in {"legacy_role_fallback", "legacy_rm_requirement", "legacy_stage_fallback"}:
            row_trace.append(
                {
                    "action": "suppressed_unit_top_sheet_legacy_paper_fallback",
                    "path": "final_unit_top_sheet_filter",
                    "stage_code": str(row.get("stage_code") or ""),
                    "role": str(row.get("role") or ""),
                    "layer_no": _safe_int(row.get("layer_no"), 0) or None,
                    "qty_source": source,
                    "source": str(row.get("source") or ""),
                    "product_key": "",
                    "product": str(row.get("product") or ""),
                    "kept_qty_source": "paper_resolution",
                }
            )
            continue
        kept.append(row)
    return kept


def _fallback_stage_code_for_rm_requirement(
    original_stage_code: str,
    rm_item: Dict[str, Any],
    stage_rows: Dict[str, Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    original = str(original_stage_code or "").strip()
    if original in stage_rows:
        return original, {}
    semantic = _semantic_stage_for_rm_requirement(original, rm_item)
    assigned = _nearest_downstream_stage_code(semantic, stage_rows)
    if not assigned or assigned == original:
        return original, {}
    return assigned, {
        "original_semantic_stage": semantic or original,
        "assigned_route_stage": assigned,
        "fallback_reason": "missing_stage",
    }


def _semantic_stage_for_rm_requirement(stage_code: str, rm_item: Dict[str, Any]) -> str:
    stage = _canonical_route_stage(stage_code)
    if stage:
        return stage
    role = _canonical_role(rm_item)
    if role in {"liner", "medium", "fluting", "top_liner", "bottom_liner"}:
        return "2ply"
    if role in {"top_sheet_external", "top_sheet", "top_roll"}:
        return "board"
    if "adhesive_corrugation" in role:
        return "2ply"
    if "adhesive_board" in role:
        return "board"
    return ""


def _canonical_role(rm_item: Dict[str, Any]) -> str:
    text = str(
        rm_item.get("semantic_role")
        or rm_item.get("role")
        or (rm_item.get("metadata") or {}).get("semantic_role")
        or ""
    ).strip().lower()
    return text.replace("-", "_").replace(" ", "_")


def _nearest_downstream_stage_code(semantic_stage: str, stage_rows: Dict[str, Dict[str, Any]]) -> str:
    active_codes = [str(code or "") for code in stage_rows.keys() if code]
    if not active_codes:
        return ""
    canonical_by_code = {code: _canonical_route_stage(code) for code in active_codes}
    for code, canonical in canonical_by_code.items():
        if canonical == semantic_stage:
            return code
    precedence = ["2ply", "board", "printing", "lamination", "diecut", "seaming", "packing"]
    try:
        start = precedence.index(semantic_stage)
    except ValueError:
        start = -1
    if start >= 0:
        for stage in precedence[start + 1 :]:
            for code, canonical in canonical_by_code.items():
                if canonical == stage:
                    return code
    return active_codes[0]


def _canonical_route_stage(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return ""
    if "2ply" in text or "2_ply" in text or "corrug" in text:
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


def _summary_block(
    stage_rows: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    quantity_report: Dict[str, Any],
    costing: Optional[Dict[str, Any]],
    spec_name: str,
    style: str,
    plant_name: str,
    order_qty: float,
    summary_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sheet_stages = [row for row in stage_rows if row.get("output_uom") == "sheet"]
    fg_stages = [
        row
        for row in stage_rows
        if row.get("output_stock_behavior") == "fg"
        or row.get("stage_type") == "fg"
    ]
    sheet_stage_total_qty = max([_safe_float(row.get("output_qty"), 0.0) for row in sheet_stages] or [0.0])
    parent_sheets_required = _safe_float(quantity_report.get("parent_sheets_required"), 0.0)
    if parent_sheets_required <= 0.0:
        parent_sheets_required = sheet_stage_total_qty
    slit_sheets_produced = _safe_float(quantity_report.get("slit_sheets_produced"), 0.0)
    if slit_sheets_produced <= 0.0:
        slit_sheets_produced = _safe_float(order_qty, 0.0)

    summary = {
        "spec_name": spec_name,
        "style": style,
        "plant_name": plant_name,
        "order_qty": _safe_float(order_qty, 0.0),
        "m2_per_box": _safe_float(metrics.get("m2_per_box"), 0.0),
        "kg_per_box": _safe_float(metrics.get("kg_per_box"), 0.0),
        "blanks_per_sheet": _safe_int(metrics.get("blanks_per_sheet"), 0),
        "boxes_per_sheet": _safe_float(metrics.get("boxes_per_sheet"), 0.0),
        "layout_orientation": str(metrics.get("layout_orientation") or ""),
        "sheets_required": parent_sheets_required,
        "parent_sheets_required": parent_sheets_required,
        "slit_sheets_produced": slit_sheets_produced,
        "sheet_stage_total_qty": sheet_stage_total_qty,
        "sheet_stage_codes": [str(row.get("stage_code") or "") for row in sheet_stages],
        "fg_stage_total_qty": max([_safe_float(row.get("output_qty"), 0.0) for row in fg_stages] or [0.0]),
        "fg_stage_codes": [str(row.get("stage_code") or "") for row in fg_stages],
        "routing_stage_count": len(stage_rows),
        "quantity_warning_count": len(quantity_report.get("warnings") or []),
    }
    if costing:
        summary["costing"] = {
            "cost_source": costing.get("cost_source") or "",
            "effective_cost_sources": list(costing.get("effective_cost_sources") or []),
            "total_cost_per_fg": _safe_float(costing.get("total_cost_per_fg"), 0.0),
            "total_cost_for_order": _safe_float(costing.get("total_cost_for_order"), 0.0),
            "total_operation_cost": _safe_float(costing.get("total_operation_cost"), 0.0),
            "total_production_cost": _safe_float(costing.get("total_production_cost"), 0.0),
            "currency": dict(costing.get("currency") or {}),
        }
    if summary_context:
        summary["context"] = dict(summary_context or {})
    return summary


def build_preview_model(
    shadow_runtime: Dict[str, Any],
    *,
    spec_name: str = "",
    style: str = "",
    plant_name: str = "",
    order_qty: float = 0.0,
    metrics: Any = None,
    costing: Optional[Dict[str, Any]] = None,
    rm_requirements: Optional[Iterable[Any]] = None,
    product_templates: Optional[Dict[int, str]] = None,
    consumable_profile_products: Optional[Dict[str, Dict[str, Any]]] = None,
    paper_resolution: Optional[Iterable[Dict[str, Any]]] = None,
    qgraph: Optional[Any] = None,
    summary_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runtime = dict(shadow_runtime or {})
    stage_contexts = list(runtime.get("stage_contexts") or [])
    quantity_report = dict(runtime.get("quantity_report") or {})
    routing = dict(runtime.get("routing") or {})
    route_source = dict(runtime.get("route_source") or {})
    parity_report = dict(runtime.get("parity_report") or {})

    stage_rows = _build_stage_rows(stage_contexts)
    stage_rows = _overlay_stage_quantities(stage_rows, qgraph=qgraph)
    material_rows, material_warnings, material_stats = _build_material_rows(
        stage_contexts,
        runtime,
        rm_requirements or [],
        float(order_qty or 0.0),
        product_templates=product_templates,
        consumable_profile_products=consumable_profile_products,
        paper_resolution=paper_resolution,
    )
    yield_summary = _apply_fg_per_sheet_order_yield(
        material_rows,
        metrics,
        float(order_qty or 0.0),
    )
    metrics_block = _metrics_snapshot(metrics)
    summary = _summary_block(
        stage_rows,
        metrics_block,
        quantity_report,
        costing,
        spec_name,
        style,
        plant_name,
        float(order_qty or 0.0),
        summary_context=summary_context,
    )
    if yield_summary:
        summary["yield"] = yield_summary

    warnings: List[str] = []
    warnings.extend([str(w) for w in (routing.get("warnings") or []) if w])
    warnings.extend([str(w) for w in (quantity_report.get("warnings") or []) if w])
    warnings.extend(material_warnings)
    style_metadata = _style_yield_metadata(metrics)
    style_warning = str(style_metadata.get("yield_warning") or "")
    if style_warning:
        warnings.append(style_warning)
    for stage in stage_rows:
        for warning in stage.get("warnings") or []:
            warnings.append("%s:%s" % (stage.get("stage_code") or "", warning))

    notes: List[str] = []
    if route_source:
        notes.append(
            "route_source:%s"
            % (route_source.get("selection_source") or routing.get("selection_source") or "unknown")
        )
    operations = _build_operations(
        stage_rows,
        material_rows,
        qgraph=qgraph,
        metrics=metrics,
        order_qty=float(order_qty or 0.0),
    )
    routed_operations = _build_routed_operations(
        stage_rows,
        metrics=metrics,
        order_qty=float(order_qty or 0.0),
    )
    flow_buckets = _build_flow_buckets(stage_rows)

    return {
        "version": "phase5-preview-v1",
        "source": "stage_context_runtime",
        "summary": summary,
        "materials": material_rows,
        "operations": operations,
        "routed_operations": routed_operations,
        "stages": stage_rows,
        "flow_buckets": flow_buckets,
        "warnings": warnings,
        "notes": notes,
        "material_stats": material_stats,
        "material_row_trace": list(material_stats.get("row_trace") or []),
        "routing": {
            "selection_source": routing.get("selection_source") or "",
            "selection_details": dict(routing.get("selection_details") or {}),
            "stage_count": len(routing.get("stages") or []),
        },
        "parity_report": parity_report,
    }



