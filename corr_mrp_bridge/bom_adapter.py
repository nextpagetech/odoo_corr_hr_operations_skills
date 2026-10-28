from __future__ import annotations

import hashlib
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from odoo import _
from odoo import _, api, fields, models
from odoo.exceptions import UserError
import json


_logger = logging.getLogger(__name__)

try:
    from odoo.addons.corr_engine_v2.engine_v2.bom_adapter import (
        build_bom_structure as build_engine_bom_structure,
    )
    from odoo.addons.corr_engine_v2.engine_v2.consumables import should_include_consumable
except Exception:  # pragma: no cover - optional addon / standalone fallback
    try:
        from custom_addons.corr_engine_v2.engine_v2.bom_adapter import (
            build_bom_structure as build_engine_bom_structure,
        )
        from custom_addons.corr_engine_v2.engine_v2.consumables import should_include_consumable
    except Exception:  # pragma: no cover - corr_engine_v2 not installed
        build_engine_bom_structure = None
        should_include_consumable = None


def _normalize_role_for_mapping(raw_role: str) -> str:
    """Normalise engine roles / product keys to mapping kinds."""

    role_norm = (raw_role or "").lower()
    role_base = role_norm.replace("rm_", "", 1)
    # Normalise all Engine V2 adhesive product keys (rm_adhesive_*)
    # to the generic adhesive kind so that sub-roles can be resolved
    # via engine_code on corr.material.product.map. This keeps the
    # mapping model schema unchanged while allowing distinct mapping
    # for corrugation/board/fg adhesive usages.
    if role_base.startswith("adhesive_") or role_base == "adhesive":
        return "adhesive"
    # Roll-mode top sheet is stack-derived printable liner and should
    # behave like liner for costing and BoM mapping; only unit-sheet
    # external top sheets use the dedicated top_sheet mapping kind.
    if role_base == "top_roll":
        return "liner"
    if role_base in ("top_sheet_external", "top_sheet"):
        return "top_sheet"
    if role_base in ("lamination_film",):
        return "lamination"
    if role_base in ("stitching_wire",):
        return "wire"
    if role_base in ("jointing_tape",):
        return "tape"
    return role_base


def _archive_operations(operations) -> None:
    """Archive BoM operations instead of deleting history-linked rows."""

    if not operations:
        return
    if "active" not in operations._fields:
        operations.unlink()
        return
    try:
        operations.action_archive()
    except Exception:
        operations.write({"active": False})


def _normalize_subtype_code(value: Any) -> Optional[str]:
    """Normalise subtype identifiers used as engine_code in mappings.

    Applies a simple, consistent scheme:
    - accepts ints/strings,
    - strips whitespace,
    - lowercases,
    - returns None when empty.
    """

    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
    except Exception:
        text = str(value)
    text = text.strip()
    return text.lower() or None


def _engine_material_profile_role_aliases(base_role: str, engine_code: Optional[str] = None) -> List[str]:
    base = str(base_role or "").strip().lower()
    code = str(engine_code or "").strip().lower()
    aliases = {
        "ink": ["ink", "print", "printing", "printing_ink", "rm_ink", "rm_print", "rm_printing_ink"],
        "wire": ["wire", "stitching_wire", "rm_wire"],
        "tape": ["tape", "jointing_tape", "rm_tape"],
        "lamination": ["lamination", "lamination_film", "film", "rm_lamination_film"],
        "adhesive": ["adhesive", "glue", "gum"],
    }.get(base, [base] if base else [])
    if base == "adhesive" and code:
        aliases.extend(
            [
                "adhesive_%s" % code,
                "%s_adhesive" % code,
                "rm_adhesive_%s" % code,
            ]
        )
    return [item for item in dict.fromkeys(aliases) if item]


def _resolve_engine_material_profile_product(env, base_role: str, engine_code: Optional[str] = None):
    aliases = set(_engine_material_profile_role_aliases(base_role, engine_code=engine_code))
    if not aliases:
        return (None, None)
    try:
        Profile = env["corr.engine.material.profile"].sudo()
    except Exception:
        return (None, None)
    try:
        profiles = Profile.search(
            [
                ("active", "=", True),
                ("material_type", "=", "consumable"),
                ("role", "!=", False),
            ],
            order="id",
        )
    except Exception:
        return (None, None)
    matches = []
    for profile in profiles:
        role = str(getattr(profile, "role", "") or "").strip().lower()
        if role not in aliases:
            continue
        tmpl = getattr(profile, "product_tmpl_id", False)
        product = getattr(tmpl, "product_variant_id", False) if tmpl else False
        if product and product.exists():
            matches.append((product, getattr(product, "uom_id", False) or None))
    if len(matches) == 1:
        return matches[0]
    return (None, None)


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


def _config_bool(env, key: str, default: bool = True) -> bool:
    try:
        value = env["ir.config_parameter"].sudo().get_param(key, "1" if default else "0")
    except Exception:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _model_has_field(model: Any, field_name: str) -> bool:
    try:
        return field_name in getattr(model, "_fields", {})
    except Exception:
        return False


def _filter_model_vals(model: Any, vals: Dict[str, Any]) -> Dict[str, Any]:
    try:
        fields_map = getattr(model, "_fields", {}) or {}
    except Exception:
        fields_map = {}
    if not fields_map:
        return dict(vals or {})
    return {key: value for key, value in dict(vals or {}).items() if key in fields_map}


def _normalize_flute_codes(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw_values = value.replace(";", ",").replace("|", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = [value]

    result: List[str] = []
    seen = set()
    for item in raw_values:
        code = str(item or "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def _required_corrugation_flutes_from_spec(spec: Any) -> List[str]:
    if not spec:
        return []

    values: List[str] = []
    try:
        stack_lines = spec.stack_line_ids.sorted(lambda line: (line.sequence or 0, line.id or 0))
    except Exception:
        stack_lines = []

    for line in stack_lines or []:
        role = str(getattr(line, "role", "") or "").strip().lower()
        if role not in ("medium", "flute"):
            continue
        flute = getattr(line, "flute_id", None)
        code = getattr(flute, "code", None) or getattr(line, "flute", None)
        values.extend(_normalize_flute_codes(code))

    if values:
        return _normalize_flute_codes(values)

    try:
        stack_json = spec.stack_json or []
    except Exception:
        stack_json = []
    if isinstance(stack_json, str):
        try:
            stack_json = json.loads(stack_json)
        except Exception:
            stack_json = []
    if isinstance(stack_json, dict):
        stack_json = stack_json.get("layers") or stack_json.get("stack") or []

    for layer in stack_json or []:
        if not isinstance(layer, dict):
            continue
        role = str(layer.get("role") or layer.get("type") or "").strip().lower()
        if role not in ("medium", "flute"):
            continue
        values.extend(_normalize_flute_codes(layer.get("flute_code") or layer.get("flute")))

    return _normalize_flute_codes(values)


def _operation_requires_corrugation_flute(operation_row: Dict[str, Any]) -> bool:
    operation_family = str((operation_row or {}).get("operation_family_name") or "").strip().lower()
    operation_key = str((operation_row or {}).get("operation_key") or "").strip().lower()
    stage_type = str((operation_row or {}).get("stage_type") or "").strip().lower()
    capability = str((operation_row or {}).get("capability_code") or "").strip().lower()
    return operation_family == "corrugation" or operation_key == "corrugator" or stage_type == "corrugation" or capability == "corrugation"


def _workcenter_supports_required_flutes(workcenter: Any, required_flutes: List[str]) -> bool:
    required = _normalize_flute_codes(required_flutes)
    if not required:
        return True
    supported = _normalize_flute_codes(getattr(workcenter, "corr_flutes_supported", None))
    if not supported:
        return False
    return all(code in supported for code in required)


def _selected_layout_workcenter_id(bom_struct: Dict[str, Any]) -> Optional[int]:
    selected_layout = bom_struct.get("selected_layout") or {}
    if not isinstance(selected_layout, dict):
        return None
    matched = selected_layout.get("matched_workcenter") or {}
    if not isinstance(matched, dict):
        return None
    wc_id = _safe_int(matched.get("workcenter_id"), 0)
    return wc_id or None


def _dedupe_strings(values: List[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _operation_key_from_bucket(bucket: Optional[str]) -> Optional[str]:
    bucket_key = str(bucket or "").strip().lower()
    return {
        "board": "corrugator",
        "sheet": "printing",
        "blanks": "diecut",
    }.get(bucket_key)


def _operation_key_from_stage_context(
    stage_type: Optional[str],
    capability_code: Optional[str],
    output_kind: Optional[str],
    output_stock_behavior: Optional[str],
    execution_type: Optional[str],
    bucket: Optional[str],
) -> Optional[str]:
    classifier = str(capability_code or "").strip().lower() or str(stage_type or "").strip().lower()
    if classifier == "corrugation":
        return "corrugator"
    if classifier in ("printing", "lamination"):
        return "printing"
    if classifier == "diecut":
        return "diecut"
    if classifier == "conversion":
        return "conversion"

    output_kind_key = str(output_kind or "").strip().lower()
    output_stock_key = str(output_stock_behavior or "").strip().lower()
    execution_key = str(execution_type or "").strip().lower()
    if output_stock_key == "fg":
        return None
    if output_kind_key == "box" and execution_key == "material":
        return "conversion"

    return _operation_key_from_bucket(bucket)


def _operation_name_from_key(operation_key: Optional[str]) -> Optional[str]:
    op_key = str(operation_key or "").strip().lower()
    return {
        "corrugator": "Corrugation",
        "printing": "Printing",
        "diecut": "Slot/Die",
        "conversion": "Conversion",
    }.get(op_key)


def _operation_name_from_bom_operation_label(operation_label: Optional[str]) -> Optional[str]:
    op_key = str(operation_label or "").strip().lower()
    op_name = _operation_name_from_key(op_key)
    if op_name:
        return op_name
    return {
        "cutting": "Cutting",
        "ts_cut": "Cutting",
        "top_sheet_cut": "Cutting",
        "top-sheet cut": "Cutting",
        "top sheet cut": "Cutting",
        "ts_print": "Printing",
        "top_sheet_print": "Printing",
        "top-sheet printing": "Printing",
        "top sheet printing": "Printing",
        "lamination": "Printing",
        "peeloff": "Peeling",
        "peel-off": "Peeling",
        "peel_off": "Peeling",
        "waste stripping": "Peeling",
        "seaming": "Seaming",
        "jointing": "Seaming",
    }.get(op_key)


def _operation_family_from_row(operation_row: Dict[str, Any], operation_key: str) -> Optional[str]:
    stage_type = str((operation_row or {}).get("stage_type") or "").strip().lower()
    capability = str((operation_row or {}).get("capability_code") or "").strip().lower()
    output_kind = str((operation_row or {}).get("output_kind") or "").strip().lower()
    stage_code = str((operation_row or {}).get("stage_code") or (operation_row or {}).get("stage") or "").strip().lower()
    stage_name = str(
        (operation_row or {}).get("stage_name")
        or (operation_row or {}).get("stage_template_name")
        or (operation_row or {}).get("name")
        or ""
    ).strip().lower()
    classifier = stage_type or capability
    text = " ".join(filter(None, [operation_key, stage_code, stage_name, capability, stage_type]))
    if "cut" in text and "die" not in text:
        return "Cutting"
    if "print" in text:
        return "Printing"
    if "peel" in text or "waste stripping" in text:
        return "Peeling"
    if "seam" in text or "joint" in text or "gluing" in text:
        return "Seaming"
    if classifier in ("lamination", "pasting"):
        return "Lamination"
    if classifier == "printing":
        return "Printing"
    if classifier == "diecut":
        return "Slot/Die"
    if classifier == "corrugation":
        return "Corrugation"
    if classifier == "conversion":
        return "Seaming" if output_kind in ("box", "fg") else "Lamination"
    return _operation_name_from_bom_operation_label(operation_key)


def _operation_display_name_from_row(
    operation_row: Dict[str, Any],
    *,
    fallback_name: Optional[str] = None,
) -> Optional[str]:
    for key in ("stage_name", "stage_template_name", "name"):
        display_name = str((operation_row or {}).get(key) or "").strip()
        if display_name:
            return display_name
    return str(fallback_name or "").strip() or None


def _preferred_bom_operation_rows(bom_struct: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
    def _prepare(rows: List[Dict[str, Any]], source: str) -> List[Dict[str, Any]]:
        prepared: List[Dict[str, Any]] = []
        for index, op in enumerate(rows or []):
            if not isinstance(op, dict):
                continue
            operation_key = str(op.get("operation_key") or op.get("stage") or "").strip().lower()
            operation_family_name = _operation_family_from_row(op, operation_key)
            operation_name = _operation_display_name_from_row(
                op,
                fallback_name=operation_family_name,
            )
            if not operation_name:
                continue
            input_qty = _safe_float(op.get("input_qty"), 0.0)
            output_qty = _safe_float(op.get("output_qty"), 0.0)
            stage_type = str(op.get("stage_type") or "")
            if source != "routed_operations":
                if input_qty <= 0.0 or output_qty <= 0.0:
                    continue
            prepared.append(
                {
                    "source": source,
                    "operation_key": operation_key,
                    "operation_name": operation_name,
                    "operation_family_name": operation_family_name or operation_name,
                    "stage": str(op.get("stage") or ""),
                    "stage_code": str(op.get("stage_code") or ""),
                    "stage_contract_code": str(op.get("stage_contract_code") or ""),
                    "stage_template_id": _safe_int(op.get("stage_template_id"), 0) or None,
                    "stage_type": stage_type,
                    "capability_code": str(op.get("capability_code") or ""),
                    "workcenter_id": _safe_int(
                        op.get("routing_workcenter_id") or op.get("workcenter_id"),
                        0,
                    )
                    or None,
                    "input_qty": input_qty,
                    "output_qty": output_qty,
                    "sequence": _safe_int(op.get("sequence"), 0) or ((index + 1) * 10),
                }
            )
        return prepared

    routed = _prepare(list(bom_struct.get("routed_operations") or []), "routed_operations")
    if routed:
        return routed, "routed_operations"
    return _prepare(list(bom_struct.get("operations") or []), "operations"), "operations"


def _preferred_stagecontext_signature_struct(
    writer_payload: Dict[str, Any],
    legacy_signature_struct: Dict[str, Any],
) -> Tuple[Dict[str, Any], str]:
    writer_struct = dict(writer_payload or {})
    legacy_struct = dict(legacy_signature_struct or {})
    if list(writer_struct.get("routed_operations") or []):
        return writer_struct, "stagecontext_writer_payload"
    if legacy_struct:
        return legacy_struct, "legacy_bom_struct_bridge"
    return writer_struct, "stagecontext_writer_payload"


def _stage_identity_keys(
    *,
    stage_contract_code: Optional[str] = None,
    stage_template_id: Optional[Any] = None,
    stage_code: Optional[str] = None,
) -> List[Any]:
    keys: List[Any] = []
    contract = str(stage_contract_code or "").strip()
    if contract:
        keys.append(("contract", contract))
    template_id = _safe_int(stage_template_id, 0)
    if template_id:
        keys.append(("template", template_id))
    code = str(stage_code or "").strip()
    if code:
        keys.append(("code", code))
    return keys


def _build_preview_stage_lookup(preview_stages: List[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    lookup: Dict[Any, Dict[str, Any]] = {}
    for row in preview_stages or []:
        if not isinstance(row, dict):
            continue
        for key in _stage_identity_keys(
            stage_contract_code=row.get("stage_contract_code"),
            stage_template_id=row.get("stage_template_id"),
            stage_code=row.get("stage_code"),
        ):
            if key not in lookup:
                lookup[key] = row
    return lookup


def _build_preview_operation_stage_lookup(preview_operations: List[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    lookup: Dict[Any, Dict[str, Any]] = {}
    for operation in preview_operations or []:
        if not isinstance(operation, dict):
            continue
        op_key = str(operation.get("stage") or "").strip().lower()
        for detail in list(operation.get("stage_details") or []):
            if not isinstance(detail, dict):
                continue
            entry = {
                "operation_key": op_key,
                "stage": op_key,
                "stage_code": str(detail.get("stage_code") or ""),
                "stage_contract_code": str(detail.get("stage_contract_code") or ""),
                "stage_template_id": _safe_int(detail.get("stage_template_id"), 0) or None,
                "input_qty": _safe_float(detail.get("input_qty"), 0.0),
                "output_qty": _safe_float(detail.get("output_qty"), 0.0),
                "uom": str(detail.get("uom") or operation.get("uom") or ""),
            }
            for key in _stage_identity_keys(
                stage_contract_code=entry.get("stage_contract_code"),
                stage_template_id=entry.get("stage_template_id"),
                stage_code=entry.get("stage_code"),
            ):
                if key not in lookup:
                    lookup[key] = entry
    return lookup


def _build_preview_routed_operation_lookup(preview_operations: List[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    lookup: Dict[Any, Dict[str, Any]] = {}
    for operation in preview_operations or []:
        if not isinstance(operation, dict):
            continue
        entry = {
            "operation_key": str(operation.get("operation_key") or operation.get("stage") or "").strip().lower(),
            "stage": str(operation.get("stage") or "").strip().lower(),
            "stage_code": str(operation.get("stage_code") or ""),
            "stage_contract_code": str(operation.get("stage_contract_code") or ""),
            "stage_template_id": _safe_int(operation.get("stage_template_id"), 0) or None,
            "input_qty": _safe_float(operation.get("input_qty"), 0.0),
            "output_qty": _safe_float(operation.get("output_qty"), 0.0),
            "uom": str(operation.get("uom") or ""),
        }
        for key in _stage_identity_keys(
            stage_contract_code=entry.get("stage_contract_code"),
            stage_template_id=entry.get("stage_template_id"),
            stage_code=entry.get("stage_code"),
        ):
            if key not in lookup:
                lookup[key] = entry
    return lookup


def _resolve_stage_operation_metrics(
    ctx: Dict[str, Any],
    preview_stage_row: Optional[Dict[str, Any]] = None,
    preview_routed_operation_item: Optional[Dict[str, Any]] = None,
    preview_operation_item: Optional[Dict[str, Any]] = None,
    preview_operation_stage_item: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    stage_row = preview_stage_row or {}
    routed_operation_item = preview_routed_operation_item or {}
    operation_item = preview_operation_item or {}
    operation_stage_item = preview_operation_stage_item or {}

    if stage_row:
        return {
            "input_qty": _safe_float(stage_row.get("input_qty"), _safe_float(ctx.get("input_qty_from_prev"), 0.0)),
            "output_qty": _safe_float(stage_row.get("output_qty"), _safe_float(ctx.get("target_output_qty"), 0.0)),
            "uom": str(stage_row.get("output_uom") or ctx.get("target_output_uom") or ""),
            "source": "preview_stage_overlay",
        }
    if routed_operation_item:
        return {
            "input_qty": _safe_float(routed_operation_item.get("input_qty"), _safe_float(ctx.get("input_qty_from_prev"), 0.0)),
            "output_qty": _safe_float(routed_operation_item.get("output_qty"), _safe_float(ctx.get("target_output_qty"), 0.0)),
            "uom": str(routed_operation_item.get("uom") or ctx.get("target_output_uom") or ""),
            "source": "preview_routed_operation_overlay",
        }
    if operation_stage_item:
        return {
            "input_qty": _safe_float(operation_stage_item.get("input_qty"), _safe_float(ctx.get("input_qty_from_prev"), 0.0)),
            "output_qty": _safe_float(operation_stage_item.get("output_qty"), _safe_float(ctx.get("target_output_qty"), 0.0)),
            "uom": str(operation_stage_item.get("uom") or ctx.get("target_output_uom") or ""),
            "source": "preview_operation_stage_overlay",
        }
    if operation_item:
        return {
            "input_qty": _safe_float(operation_item.get("input_qty"), _safe_float(ctx.get("input_qty_from_prev"), 0.0)),
            "output_qty": _safe_float(operation_item.get("output_qty"), _safe_float(ctx.get("target_output_qty"), 0.0)),
            "uom": str(operation_item.get("uom") or ctx.get("target_output_uom") or ""),
            "source": "preview_operation_overlay",
        }
    return {
        "input_qty": _safe_float(ctx.get("input_qty_from_prev"), 0.0),
        "output_qty": _safe_float(ctx.get("target_output_qty"), 0.0),
        "uom": str(ctx.get("target_output_uom") or ""),
        "source": "stagecontext",
    }


def _component_stage_label_from_bucket(bucket: Optional[str]) -> str:
    bucket_key = str(bucket or "").strip().lower()
    return {
        "board": "corrugator",
        "sheet": "printing",
        "blanks": "diecut",
        "finished_goods": "finished_goods",
    }.get(bucket_key, bucket_key or "")


def _component_operation_binding(component: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict((component or {}).get("metadata") or {})
    operation_name = str(
        (component or {}).get("component_operation_name")
        or metadata.get("operation_name")
        or ""
    ).strip()
    if not operation_name:
        operation_name = str(
            _operation_name_from_bom_operation_label(
                (component or {}).get("component_stage")
                or metadata.get("operation_key")
                or (component or {}).get("stage")
            )
            or ""
        ).strip()
    return {
        "operation_name": operation_name or None,
        "stage_contract_code": str(
            (component or {}).get("component_stage_contract_code")
            or metadata.get("stage_contract_code")
            or ""
        ).strip() or None,
        "stage_template_id": _safe_int(
            (component or {}).get("component_stage_template_id")
            or metadata.get("stage_template_id"),
            0,
        ) or None,
        "stage_code": str(
            (component or {}).get("component_stage_code")
            or metadata.get("stage_code")
            or ""
        ).strip() or None,
    }


def _lookup_operation_id_for_component(
    component: Dict[str, Any],
    operation_by_identity: Dict[Any, Any],
    operation_by_name: Dict[str, Any],
) -> Optional[Any]:
    binding = _component_operation_binding(component)
    for key in _stage_identity_keys(
        stage_contract_code=binding.get("stage_contract_code"),
        stage_template_id=binding.get("stage_template_id"),
        stage_code=binding.get("stage_code"),
    ):
        operation_id = operation_by_identity.get(key)
        if operation_id:
            return operation_id
    operation_name = str(binding.get("operation_name") or "").strip()
    if operation_name:
        return operation_by_name.get(operation_name)
    return None


def _draft_line_component_key(item: Dict[str, Any]) -> str:
    role = str(item.get("corr_role") or "").strip().lower()
    layer_no = _safe_int(item.get("corr_layer_no"), 0)
    product_id = _safe_int(item.get("product_id"), 0)
    stage_code = str(
        item.get("component_stage_code")
        or item.get("component_stage")
        or ""
    ).strip().lower()
    return "engine_v2:%s:%s:%s:%s" % (stage_code, role, layer_no, product_id)


def _legacy_product_key_for_semantic_role(semantic_role: Optional[str]) -> str:
    semantic = str(semantic_role or "").strip().lower()
    mapping = {
        "liner": "rm_liner",
        "medium": "rm_medium",
        "carton_board": "rm_carton_board",
        "top_roll": "rm_top_roll",
        "top_sheet_external": "rm_top_sheet_external",
        "paper": "rm_paper",
        "adhesive_corrugation": "rm_adhesive_corrugation",
        "adhesive_board": "rm_adhesive_board",
        "adhesive_fg": "rm_adhesive_fg",
        "ink": "rm_ink",
        "varnish": "rm_varnish",
        "lamination": "rm_lamination_film",
        "wire": "rm_wire",
        "tape": "rm_tape",
    }
    return mapping.get(semantic, "rm_%s" % semantic if semantic else "")


def _normalize_paper_resolution_map(payload: Dict[str, Any]) -> Dict[Any, Dict[str, Any]]:
    paper_resolution_map: Dict[Any, Dict[str, Any]] = {}
    paper_resolution_raw = payload.get("paper_resolution") or []
    if not isinstance(paper_resolution_raw, list):
        return paper_resolution_map
    def _entry_rank(item: Dict[str, Any]) -> int:
        status = str((item or {}).get("status") or "").strip().lower()
        note = str((item or {}).get("resolution_note") or "").strip().lower()
        try:
            product_id = int((item or {}).get("product_id") or 0)
        except Exception:
            product_id = 0
        if product_id and (status in ("exact", "resolved") or ("resolved" in note and "not found" not in note)):
            return 30
        if status in ("exact", "resolved") or ("resolved" in note and "not found" not in note):
            return 20
        if status == "missing" or "not found" in note:
            return 0
        return 10
    for entry in paper_resolution_raw:
        if _paper_resolution_entry_is_trace_only(entry):
            continue
        try:
            role = (entry.get("role") or "").strip().lower()
        except Exception:
            role = ""
        try:
            layer_idx = int(entry.get("layer_no") or 0)
        except Exception:
            layer_idx = 0
        if not role:
            continue
        key = (role, layer_idx)
        existing = paper_resolution_map.get(key)
        if not existing or _entry_rank(entry) >= _entry_rank(existing):
            paper_resolution_map[key] = entry
    return paper_resolution_map


def _paper_resolution_entry_is_trace_only(entry: Any) -> bool:
    """Return True for diagnostic paper-resolution rows that are not requirements.

    Preview keeps trace rows in raw_json for debugging. BoM creation must ignore
    them; otherwise diagnostic entries such as status=trace can block creation
    even when the final RM Breakdown already resolved the real material row.
    """
    if not isinstance(entry, dict):
        return False
    status = str(entry.get("status") or "").strip().lower()
    if status == "trace":
        return True
    debug = entry.get("debug") or {}
    if isinstance(debug, dict) and debug.get("trace_only"):
        return True
    return bool(entry.get("trace_only"))


def _paper_resolution_issue(
    *,
    role: str,
    layer_idx: int,
    status: str,
    reason: str,
    gsm: Any,
    required_deckle_cm: Any,
    key: Any,
) -> Dict[str, Any]:
    return {
        "role": role,
        "layer_no": layer_idx or None,
        "status": status or "missing",
        "reason": reason or "",
        "gsm": gsm,
        "required_deckle_cm": required_deckle_cm,
        "resolution_key": "%s:%s" % (key[0] if isinstance(key, tuple) else role, layer_idx or 0),
    }


def _paper_resolution_summary_line(entry: Dict[str, Any]) -> str:
    role = entry.get("role") or ""
    layer_no = entry.get("layer_no") or "-"
    status = entry.get("status") or "missing"
    reason = entry.get("reason") or ""
    product_name = entry.get("fallback_product_name") or ""
    authority = entry.get("fallback_authority") or ""
    label = "%s layer %s status=%s" % (role, layer_no, status)
    if reason:
        label += " (%s)" % reason
    if product_name:
        label += " -> %s" % product_name
    if authority:
        label += " via %s" % authority
    return label


def _top_sheet_mode_for_component(product_key: Optional[str], metadata: Dict[str, Any]) -> str:
    key = str(product_key or "").strip().lower()
    kind = str(metadata.get("kind") or "").strip().lower()
    mode = str(metadata.get("top_sheet_mode") or "").strip().lower()
    if mode in ("unit", "unit_sheet"):
        return "unit_sheet"
    if mode == "roll":
        return "roll"
    if kind == "top_roll" or key == "rm_top_roll":
        return "roll"
    if kind == "top_sheet_external" or key == "rm_top_sheet_external":
        return "unit_sheet"
    return ""


def _top_sheet_roll_resolution_role(metadata: Dict[str, Any]) -> str:
    role = str(metadata.get("role") or metadata.get("type") or "").strip().lower()
    if role in ("liner", "medium"):
        return role
    return "liner"


def _top_sheet_roll_resolution_entry(
    metadata: Dict[str, Any],
    paper_resolution: Optional[Dict[Any, Dict[str, Any]]],
) -> Dict[str, Any]:
    role = _top_sheet_roll_resolution_role(metadata)
    try:
        layer_idx = int(metadata.get("layer_no") or 0)
    except Exception:
        layer_idx = 0

    if paper_resolution:
        entry = paper_resolution.get((role, layer_idx)) or {}
        if entry:
            return entry
        if role != "liner":
            entry = paper_resolution.get(("liner", layer_idx)) or {}
            if entry:
                return entry
    resolution_meta = dict(metadata.get("resolution") or {})
    if resolution_meta:
        return {
            "status": resolution_meta.get("status") or "",
            "product_id": resolution_meta.get("product_id"),
            "uom_id": resolution_meta.get("uom_id"),
            "product_name": resolution_meta.get("product_name") or "",
        }
    return {}


def _normalize_preview_bom_backend(value: Any, default: str = "legacy") -> str:
    backend = str(value or default or "legacy").strip().lower()
    if backend in ("legacy", "stagecontext_shadow"):
        return backend
    return str(default or "legacy").strip().lower() or "legacy"


def _product_id_from_writer_key(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("product.product:"):
        return _safe_int(text.split(":", 1)[1], 0) or None
    return None


def _explicit_component_product(metadata: Dict[str, Any], component: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], str]:
    resolution = dict(metadata.get("resolution") or {})
    product_id = (
        _safe_int(metadata.get("product_id"), 0)
        or _safe_int(resolution.get("product_id"), 0)
        or _safe_int(component.get("product_id"), 0)
        or _product_id_from_writer_key(metadata.get("writer_product_key"))
    )
    uom_id = (
        _safe_int(metadata.get("uom_id"), 0)
        or _safe_int(resolution.get("uom_id"), 0)
        or _safe_int(component.get("uom_id"), 0)
    )
    status = str(
        metadata.get("resolution_status")
        or resolution.get("status")
        or ""
    ).strip().lower()
    return (product_id or None, uom_id or None, status)


def get_general_wizard_bom_backend(env, context: Optional[Dict[str, Any]] = None, default: str = "legacy") -> str:
    ctx = context or env.context or {}
    override = ctx.get("engine_v2_general_bom_backend")
    if override:
        return _normalize_preview_bom_backend(override, default=default)
    icp = env["ir.config_parameter"].sudo()
    value = icp.get_param("corr_mrp_bridge.engine_v2_general_bom_backend") or default
    return _normalize_preview_bom_backend(value, default=default)


def _build_translation_rule_lookup(env, payload: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    Rule = env["corr.engine.bom.translation.rule"].sudo()
    plant_id = _safe_int(payload.get("plant_id"), 0)
    rules = Rule.search(
        [
            ("active", "=", True),
            "|",
            ("plant_id", "=", False),
            ("plant_id", "=", plant_id or 0),
        ],
        order="sequence, id",
    )
    lookup: Dict[int, Dict[str, Any]] = {}
    for rule in rules:
        for stage in rule.stage_template_ids:
            if stage.id in lookup:
                continue
            lookup[stage.id] = {
                "rule_id": rule.id,
                "name": rule.name,
                "operation_behavior": rule.operation_behavior,
                "operation_ref_type": rule.operation_ref_type,
                "component_behavior": rule.component_behavior,
                "bucket_mapping": rule.bucket_mapping or "",
                "workcenter_id": rule.workcenter_id.id if rule.workcenter_id else None,
                "routing_workcenter_id": rule.routing_workcenter_id.id if rule.routing_workcenter_id else None,
            }
    return lookup


def _match_legacy_quantity_bridge(
    component: Dict[str, Any],
    rm_requirements: List[Dict[str, Any]],
) -> Dict[str, Any]:
    stage_code = str(component.get("stage_code") or "")
    role_aliases = _dedupe_strings(
        list(component.get("legacy_role_aliases") or []) + [component.get("semantic_role")]
    )
    exact = []
    role_only = []
    for rm in rm_requirements or []:
        role = str(rm.get("role") or "").strip().lower()
        stage_key = str(rm.get("stage_key") or "").strip()
        if role in role_aliases:
            role_only.append(rm)
            if stage_key == stage_code:
                exact.append(rm)
    match = exact[:1] or role_only[:1]
    if not match:
        return {
            "match_level": "none",
            "legacy_role": None,
            "legacy_stage_key": None,
            "legacy_qty_per_fg": None,
            "legacy_qty_uom": None,
        }
    rm = match[0]
    return {
        "match_level": "exact_stage_role" if exact else "role_only",
        "legacy_role": rm.get("role"),
        "legacy_stage_key": rm.get("stage_key"),
        "legacy_qty_per_fg": _safe_float(rm.get("qty_per_fg"), 0.0),
        "legacy_qty_uom": rm.get("qty_uom"),
    }


def _legacy_struct_components_for_semantic_role(
    payload: Dict[str, Any],
    semantic_role: Optional[str],
) -> List[Dict[str, Any]]:
    semantic = str(semantic_role or "").strip().lower()
    if not semantic.startswith("adhesive"):
        return []

    result: List[Dict[str, Any]] = []
    for comp in (payload.get("bom_struct") or {}).get("components") or []:
        product_key = str(comp.get("product") or "").strip().lower()
        metadata = dict(comp.get("metadata") or {})
        kind = str(metadata.get("kind") or "").strip().lower()
        if not (product_key.startswith("rm_adhesive_") or kind.startswith("adhesive")):
            continue
        result.append(
            {
                "product": comp.get("product"),
                "qty": _safe_float(comp.get("qty"), 0.0),
                "uom": comp.get("uom"),
                "stage": comp.get("stage"),
                "metadata": metadata,
            }
        )
    return result


def _is_material_stage_entry(stage_entry: Dict[str, Any]) -> bool:
    execution_type = str((stage_entry or {}).get("execution_type") or "").strip().lower()
    if execution_type:
        return execution_type == "material"
    return str((stage_entry or {}).get("stage_type") or "").strip().lower() != "operation"


def _stage_entry_semantic(stage_entry: Dict[str, Any]) -> str:
    stage_code = str((stage_entry or {}).get("stage_code") or "").strip().lower()
    stage_type = str((stage_entry or {}).get("stage_type") or "").strip().lower()
    capability_code = str((stage_entry or {}).get("capability_code") or "").strip().lower()
    output_kind = str((stage_entry or {}).get("output_kind") or "").strip().lower()
    output_stock_behavior = str((stage_entry or {}).get("output_stock_behavior") or "").strip().lower()

    if capability_code == "corrugation" or stage_type == "corrugation" or stage_code in ("2ply", "3ply", "singleface"):
        return "corrugation"
    if output_stock_behavior == "fg" or output_kind in ("box", "fg") or stage_code in ("fg", "final"):
        return "final"
    if capability_code in ("lamination", "conversion") or stage_type in ("lamination", "conversion") or output_kind == "board":
        return "board"
    return ""


def _preferred_component_stage_semantic(row: Dict[str, Any]) -> str:
    semantic_role = _effective_stagecontext_semantic_role(row)
    if semantic_role in ("liner", "medium"):
        return "corrugation"
    if semantic_role in ("top_sheet_external", "top_roll"):
        return "board"
    return ""


def _next_material_stage_from_index(ordered_stages: List[Dict[str, Any]], start_index: int) -> Dict[str, Any]:
    for idx in range(max(start_index, 0), len(ordered_stages or [])):
        stage = ordered_stages[idx]
        if _is_material_stage_entry(stage):
            return stage
    return {}


def _fallback_component_stage_entry(
    row: Dict[str, Any],
    stage_entry: Dict[str, Any],
    ordered_stages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    semantic_role = _effective_stagecontext_semantic_role(row)
    if semantic_role not in ("liner", "medium", "carton_board", "top_sheet_external", "top_roll"):
        return stage_entry or {}

    preferred_semantic = _preferred_component_stage_semantic(row)
    if stage_entry and _is_material_stage_entry(stage_entry):
        if not preferred_semantic or _stage_entry_semantic(stage_entry) == preferred_semantic:
            return stage_entry

    ordered = list(ordered_stages or [])
    if not ordered:
        return stage_entry or {}

    matched_semantic_index = -1
    last_prior_semantic_index = -1

    for idx, candidate in enumerate(ordered):
        if not _is_material_stage_entry(candidate):
            continue
        semantic = _stage_entry_semantic(candidate)
        if semantic == preferred_semantic and matched_semantic_index < 0:
            matched_semantic_index = idx
            break
        if preferred_semantic == "board" and semantic == "corrugation":
            last_prior_semantic_index = idx

    if matched_semantic_index >= 0:
        return _next_material_stage_from_index(ordered, matched_semantic_index) or stage_entry or {}

    if last_prior_semantic_index >= 0:
        return (
            _next_material_stage_from_index(ordered, last_prior_semantic_index + 1)
            or _next_material_stage_from_index(ordered, 0)
            or stage_entry
            or {}
        )

    return _next_material_stage_from_index(ordered, 0) or stage_entry or {}


def _effective_stagecontext_semantic_role(row: Optional[Dict[str, Any]]) -> str:
    row = row or {}
    metadata = dict(row.get("metadata") or {})
    role = str(row.get("role") or "").strip().lower()
    semantic_role = str(row.get("semantic_role") or metadata.get("semantic_role") or "").strip().lower()
    kind = str(metadata.get("kind") or "").strip().lower()
    product_key = str(row.get("product_key") or metadata.get("product_key") or "").strip().lower()

    # Roll-top rows can arrive with a degraded semantic_role of
    # top_sheet_external even though the authoritative row role/kind is
    # top_roll. Preserve top_roll so downstream blueprint/write planning
    # uses the exact resolved 180 GSM product instead of falling back to
    # generic top-sheet or remaining-liner semantics.
    if role == "top_roll" or kind == "top_roll":
        return "top_roll"
    if role in {"comp_generic", "component", ""}:
        probes = {semantic_role, kind, product_key}
        if probes.intersection({"rm_wire", "wire", "stitching_wire"}):
            return "wire"
        if probes.intersection({"rm_ink", "rm_print", "rm_printing_ink", "ink", "print", "printing", "printing_ink"}):
            return "ink"
        if probes.intersection({"rm_tape", "tape", "jointing_tape"}):
            return "tape"
        if probes.intersection({"rm_lamination_film", "lamination", "lamination_film", "film"}):
            return "lamination"
    if semantic_role:
        return semantic_role
    return role


def _normalise_bom_struct_for_reuse_equivalence(bom_struct: Dict[str, Any], order_qty: float) -> Dict[str, Any]:
    try:
        oq = float(order_qty or 0.0)
    except Exception:
        oq = 0.0
    if oq <= 0.0:
        oq = 1.0

    def _essential_metadata(comp: Dict[str, Any]) -> Dict[str, Any]:
        metadata = dict(comp.get("metadata") or {})
        out = {}
        for key in (
            "kind",
            "layer_no",
            "role",
            "type",
            "consumption_type",
            "top_sheet_product_id",
            "print_process",
            "wire_grade",
            "lamination_type",
            "lamination_type_id",
        ):
            value = metadata.get(key)
            if value in (None, "", [], {}):
                continue
            out[key] = value
        return out

    components_norm: List[Dict[str, Any]] = []
    for comp in bom_struct.get("components") or []:
        product_key = (comp.get("product") or "").strip()
        qty_total = _safe_float(comp.get("qty"), 0.0)
        if not product_key or qty_total <= 0.0:
            continue
        components_norm.append(
            {
                "product": product_key,
                "qty": qty_total / oq,
                "uom": comp.get("uom"),
                "stage": comp.get("stage"),
                "metadata": _essential_metadata(comp),
            }
        )

    operation_rows, operation_source = _preferred_bom_operation_rows(bom_struct)
    operations_norm: List[Dict[str, Any]] = []
    if operation_source == "routed_operations":
        for op in operation_rows:
            operations_norm.append(
                {
                    "operation_key": op.get("operation_key"),
                    "stage_code": op.get("stage_code"),
                    "stage_contract_code": op.get("stage_contract_code"),
                    "stage_template_id": op.get("stage_template_id"),
                    "sequence": _safe_int(op.get("sequence"), 0),
                    "input_qty": _safe_float(op.get("input_qty"), 0.0) / oq,
                    "output_qty": _safe_float(op.get("output_qty"), 0.0) / oq,
                }
            )
    else:
        for op in operation_rows:
            operations_norm.append(
                {
                    "stage": op.get("stage"),
                    "input_qty": _safe_float(op.get("input_qty"), 0.0) / oq,
                    "output_qty": _safe_float(op.get("output_qty"), 0.0) / oq,
                }
            )

    return {
        "product_qty": 1.0,
        "operation_source": operation_source,
        "components": sorted(
            components_norm,
            key=lambda item: (
                str(item.get("product") or ""),
                str(item.get("uom") or ""),
                str(item.get("stage") or ""),
                json.dumps(item.get("metadata") or {}, sort_keys=True, default=str),
                float(item.get("qty") or 0.0),
            ),
        ),
        "operations": (
            list(operations_norm)
            if operation_source == "routed_operations"
            else sorted(
                operations_norm,
                key=lambda item: (
                    str(item.get("stage") or ""),
                    float(item.get("input_qty") or 0.0),
                    float(item.get("output_qty") or 0.0),
                ),
            )
        ),
    }


def _is_signature_equivalent_bom_struct(
    left: Dict[str, Any],
    right: Dict[str, Any],
    order_qty: float,
) -> bool:
    return _normalise_bom_struct_for_reuse_equivalence(
        left or {},
        order_qty,
    ) == _normalise_bom_struct_for_reuse_equivalence(
        right or {},
        order_qty,
    )


def build_bom_blueprint_from_stagecontext_preview_payload(env, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise UserError(_("Invalid Engine V2 preview payload."))

    shadow_runtime = payload.get("phase8_shadow_runtime") or {}
    preview_model = payload.get("phase5_preview_model") or {}
    consumable_state = dict(
        payload.get("consumable_state")
        or (shadow_runtime or {}).get("consumable_state")
        or (preview_model or {}).get("consumable_state")
        or {}
    )
    stage_contexts = list(shadow_runtime.get("stage_contexts") or [])
    preview_materials = list(preview_model.get("materials") or [])
    preview_operations = list(preview_model.get("operations") or [])
    preview_routed_operations = list(preview_model.get("routed_operations") or [])
    preview_stages = list(preview_model.get("stages") or [])
    if not stage_contexts:
        raise UserError(_("StageContext BoM backend requires phase8_shadow_runtime.stage_contexts in the preview payload."))

    translation_rules = _build_translation_rule_lookup(env, payload)
    paper_resolution_map = _normalize_paper_resolution_map(payload) or {}
    rm_requirements = list(payload.get("rm_requirements") or [])

    preview_ops_by_key = {}
    for item in preview_operations:
        op_key = str(item.get("stage") or "").strip().lower()
        if not op_key or op_key in preview_ops_by_key:
            continue
        preview_ops_by_key[op_key] = item
    preview_stages_by_identity = _build_preview_stage_lookup(preview_stages)
    preview_routed_ops_by_identity = _build_preview_routed_operation_lookup(preview_routed_operations)
    preview_operation_stages_by_identity = _build_preview_operation_stage_lookup(preview_operations)

    stages: List[Dict[str, Any]] = []
    stage_by_identity: Dict[Any, Dict[str, Any]] = {}
    for ctx in sorted(stage_contexts, key=lambda item: (_safe_int(item.get("sequence"), 0), _safe_int(item.get("index"), 0))):
        stage_template_id = _safe_int(ctx.get("stage_template_id"), 0)
        stage_contract_code = str(ctx.get("stage_contract_code") or "")
        rule = translation_rules.get(stage_template_id) or {}
        bucket_mapping = str(rule.get("bucket_mapping") or ctx.get("output_quantity_node_key") or "")
        operation_key = _operation_key_from_stage_context(
            ctx.get("stage_type"),
            ctx.get("capability_code"),
            (ctx.get("output_descriptor") or {}).get("kind") or ctx.get("output_kind"),
            ctx.get("output_stock_behavior"),
            ctx.get("execution_type"),
            bucket_mapping,
        )
        operation_item = preview_ops_by_key.get(operation_key or "")
        preview_stage_row = None
        preview_routed_operation_row = None
        preview_operation_stage_row = None
        for lookup_key in _stage_identity_keys(
            stage_contract_code=stage_contract_code,
            stage_template_id=stage_template_id,
            stage_code=ctx.get("stage_code"),
        ):
            preview_stage_row = preview_stages_by_identity.get(lookup_key)
            preview_routed_operation_row = preview_routed_ops_by_identity.get(lookup_key)
            preview_operation_stage_row = preview_operation_stages_by_identity.get(lookup_key)
            if preview_stage_row:
                break
        operation_metrics = _resolve_stage_operation_metrics(
            ctx,
            preview_stage_row=preview_stage_row,
            preview_routed_operation_item=preview_routed_operation_row,
            preview_operation_item=operation_item,
            preview_operation_stage_item=preview_operation_stage_row,
        )
        stage_entry = {
            "stage_template_id": stage_template_id or None,
            "stage_contract_code": stage_contract_code,
            "stage_code": str(ctx.get("stage_code") or ""),
            "stage_name": str(ctx.get("stage_name") or ""),
            "stage_type": str(ctx.get("stage_type") or ""),
            "capability_code": str(ctx.get("capability_code") or ""),
            "execution_type": str(ctx.get("execution_type") or ""),
            "sequence": _safe_int(ctx.get("sequence"), 0),
            "bom_mapping_policy": str(ctx.get("bom_mapping_policy") or ""),
            "input_quantity_node_key": str(ctx.get("input_quantity_node_key") or ""),
            "output_quantity_node_key": str(ctx.get("output_quantity_node_key") or ""),
            "output_kind": str((ctx.get("output_descriptor") or {}).get("kind") or ctx.get("output_kind") or ""),
            "output_stock_behavior": str(ctx.get("output_stock_behavior") or ""),
            "operation_rule_id": rule.get("rule_id"),
            "operation_key": operation_key,
            "operation_name": _operation_name_from_key(operation_key),
            "operation_behavior": str(rule.get("operation_behavior") or ""),
            "operation_ref_type": str(rule.get("operation_ref_type") or ""),
            "workcenter_id": rule.get("workcenter_id") or _safe_int(ctx.get("workcenter_id"), 0) or None,
            "routing_workcenter_id": rule.get("routing_workcenter_id") or _safe_int(ctx.get("routing_workcenter_id"), 0) or None,
            "bucket_mapping": bucket_mapping,
            "component_behavior": str(rule.get("component_behavior") or "component_line"),
            "operation_metrics": operation_metrics,
            "authority": {
                "structure": "stagecontext_runtime",
                "operation_mapping": "translation_rules" if rule else "temporary_bridge",
                "operation_quantities": (
                    "preview_model_stage_overlay"
                    if preview_stage_row
                    else (
                        "preview_model_routed_operation_overlay"
                        if preview_routed_operation_row
                        else (
                            "preview_model_operation_stage_overlay"
                            if preview_operation_stage_row
                            else ("preview_model_operation_overlay" if operation_item else "stagecontext_runtime")
                        )
                    )
                ),
            },
        }
        stages.append(stage_entry)
        if stage_contract_code:
            stage_by_identity[("contract", stage_contract_code)] = stage_entry
        if stage_template_id:
            stage_by_identity[("template", stage_template_id)] = stage_entry
        if stage_entry["stage_code"]:
            stage_by_identity[("code", stage_entry["stage_code"])] = stage_entry

    components: List[Dict[str, Any]] = []
    struct_components: List[Dict[str, Any]] = []
    emitted_bridge_components = set()
    for row in preview_materials:
        stage_template_id = _safe_int(row.get("stage_template_id"), 0)
        stage_contract_code = str(row.get("stage_contract_code") or "")
        stage_code = str(row.get("stage_code") or "")
        stage_entry = (
            stage_by_identity.get(("contract", stage_contract_code))
            or stage_by_identity.get(("template", stage_template_id))
            or stage_by_identity.get(("code", stage_code))
            or {}
        )
        semantic_role = _effective_stagecontext_semantic_role(row)
        row_category = str(row.get("category") or row.get("material_type") or "").strip().lower()
        if (
            consumable_state
            and row_category == "consumable"
            and should_include_consumable is not None
            and (
                _safe_float(row.get("total_qty") or row.get("qty_per_fg"), 0.0) <= 0.0
                or not should_include_consumable(
                    consumable_state,
                    semantic_role,
                    metadata=row.get("metadata") or {},
                    material_type=row.get("material_type") or "",
                    category=row_category,
                )
            )
        ):
            continue
        stage_entry = _fallback_component_stage_entry(row, stage_entry, stages)
        resolved_stage_template_id = _safe_int(stage_entry.get("stage_template_id"), 0) or stage_template_id
        resolved_stage_contract_code = str(stage_entry.get("stage_contract_code") or stage_contract_code or "")
        resolved_stage_code = str(stage_entry.get("stage_code") or stage_code or "")
        resolved_stage_name = str(stage_entry.get("stage_name") or row.get("stage_name") or "")
        metadata = dict(row.get("metadata") or {})
        metadata["semantic_role"] = semantic_role
        preview_resolution = dict(metadata.get("resolution") or {})
        layer_no = _safe_int(metadata.get("layer_no"), 0)
        paper_resolution = paper_resolution_map.get((semantic_role, layer_no))
        preview_product_id = (
            _safe_int(metadata.get("product_id"), 0)
            or _safe_int(preview_resolution.get("product_id"), 0)
            or _safe_int(row.get("product_id"), 0)
        ) or None
        preview_uom_id = (
            _safe_int(metadata.get("uom_id"), 0)
            or _safe_int(preview_resolution.get("uom_id"), 0)
            or _safe_int(row.get("uom_id"), 0)
        ) or None
        preview_resolution_status = str(
            metadata.get("resolution_status")
            or preview_resolution.get("status")
            or ""
        ).strip().lower()
        exact_preview_resolution = bool(
            preview_product_id
            and preview_resolution_status in {"exact", "resolved"}
        )
        semantic_is_consumable = (
            semantic_role.startswith("adhesive")
            or semantic_role in {"ink", "varnish", "lamination", "wire", "tape"}
        )
        resolved_product_id = (
            preview_product_id
            if exact_preview_resolution
            else (_safe_int((paper_resolution or {}).get("product_id"), 0) or None)
        )
        resolved_uom_id = (
            preview_uom_id
            if exact_preview_resolution
            else (_safe_int((paper_resolution or {}).get("uom_id"), 0) or None)
        )
        resolved_product_name = (
            (metadata.get("product_name") or preview_resolution.get("product_name") or row.get("product"))
            if exact_preview_resolution
            else ((paper_resolution or {}).get("product_name") or row.get("product"))
        )
        resolved_uom_name = (
            metadata.get("uom_name")
            or preview_resolution.get("uom_name")
            or row.get("uom")
        ) if exact_preview_resolution else ((paper_resolution or {}).get("uom_name") or row.get("uom"))
        if exact_preview_resolution:
            metadata["product_id"] = preview_product_id
            metadata["uom_id"] = preview_uom_id
            if resolved_product_name:
                metadata["product_name"] = resolved_product_name
            if resolved_uom_name:
                metadata["uom_name"] = resolved_uom_name
            metadata["writer_product_key"] = "product.product:%s" % preview_product_id
            preview_resolution["product_id"] = preview_product_id
            preview_resolution["uom_id"] = preview_uom_id
            if resolved_product_name:
                preview_resolution["product_name"] = resolved_product_name
            if resolved_uom_name:
                preview_resolution["uom_name"] = resolved_uom_name
            preview_resolution["status"] = preview_resolution_status or "exact"
            metadata["resolution"] = preview_resolution
        if (row_category == "consumable" or semantic_is_consumable) and not resolved_product_id:
            product_tmpl_id = _safe_int(row.get("product_tmpl_id"), 0)
            if product_tmpl_id:
                try:
                    product_tmpl = env["product.template"].sudo().browse(product_tmpl_id)
                    product_variant = product_tmpl.product_variant_id if product_tmpl and product_tmpl.exists() else False
                except Exception:
                    product_variant = False
                if product_variant:
                    resolved_product_id = product_variant.id
                    resolved_uom_id = getattr(getattr(product_variant, "uom_id", False), "id", False) or None
                    resolved_product_name = getattr(product_variant, "display_name", "") or getattr(product_variant, "name", "") or row.get("product")
                    resolved_uom_name = (
                        getattr(getattr(product_variant, "uom_id", False), "display_name", "")
                        or getattr(getattr(product_variant, "uom_id", False), "name", "")
                        or row.get("uom")
                    )
                    metadata["product_id"] = resolved_product_id
                    if resolved_uom_id:
                        metadata["uom_id"] = resolved_uom_id
                    metadata["product_name"] = resolved_product_name
                    metadata["uom_name"] = resolved_uom_name
                    metadata["writer_product_key"] = "product.product:%s" % resolved_product_id
                    metadata["resolution_status"] = "resolved"
                    metadata["resolution_source"] = "material_profile_rule_product"
                    metadata["resolution"] = {
                        **dict(metadata.get("resolution") or {}),
                        "product_id": resolved_product_id,
                        "uom_id": resolved_uom_id,
                        "product_name": resolved_product_name,
                        "uom_name": resolved_uom_name,
                        "status": "resolved",
                        "source": "material_profile_rule_product",
                    }
        quantity_bridge = _match_legacy_quantity_bridge(row, rm_requirements)
        product_key = _legacy_product_key_for_semantic_role(semantic_role)
        quantity_source = str(row.get("quantity_source") or "")
        qty_per_fg = _safe_float(row.get("qty_per_fg"), 0.0)
        total_qty = _safe_float(row.get("total_qty"), 0.0)
        component_entry = {
            "component_key": "|".join(
                [
                    str(resolved_stage_contract_code or resolved_stage_code or ""),
                    str(semantic_role or ""),
                    str(row.get("product_tmpl_id") or ""),
                    str(layer_no or ""),
                ]
            ),
            "stage_template_id": resolved_stage_template_id or None,
            "stage_contract_code": resolved_stage_contract_code,
            "stage_code": resolved_stage_code,
            "stage_name": resolved_stage_name,
            "semantic_role": semantic_role,
            "legacy_role_aliases": list(row.get("legacy_role_aliases") or []),
            "material_type": str(row.get("material_type") or row.get("category") or ""),
            "product_tmpl_id": _safe_int(row.get("product_tmpl_id"), 0) or None,
            "product_id": resolved_product_id,
            "product_label": resolved_product_name,
            "uom_id": resolved_uom_id,
            "uom_name": resolved_uom_name,
            "qty_per_fg": qty_per_fg,
            "total_qty": total_qty,
            "quantity_basis": str(row.get("quantity_basis") or ""),
            "quantity_source": quantity_source,
            "resolution_source": (
                "preview_row_resolution"
                if exact_preview_resolution
                else ("paper_resolution" if paper_resolution else "runtime_preview_row")
            ),
            "attach_quantity_basis": str(row.get("quantity_basis") or ""),
            "translation_rule_id": stage_entry.get("operation_rule_id"),
            "product_key": product_key,
            "component_stage_label": _component_stage_label_from_bucket(stage_entry.get("bucket_mapping")),
            "operation_binding": {
                "operation_key": stage_entry.get("operation_key"),
                "operation_name": stage_entry.get("operation_name"),
                "stage_contract_code": resolved_stage_contract_code,
                "stage_template_id": resolved_stage_template_id or None,
                "stage_code": resolved_stage_code or None,
            },
            "quantity_bridge": {
                "bridge_source": quantity_source,
                "temporary_bridge": quantity_source.startswith("legacy_"),
                **quantity_bridge,
            },
            "metadata": metadata,
            "authority": {
                "structure": "stagecontext_runtime",
                "semantic_identity": "stagecontext_runtime",
                "product_resolution": "paper_resolution" if paper_resolution else "temporary_bridge",
                "quantity": "legacy_rm_requirements_bridge",
            },
        }
        legacy_struct_bridge = _legacy_struct_components_for_semantic_role(payload, semantic_role)
        if (row_category == "consumable" or semantic_is_consumable) and resolved_product_id:
            # StageContext already resolved the consumable through Engine
            # Material Profile/attachment rules. Do not replace that product
            # with legacy bom_struct technical keys such as rm_adhesive_*,
            # rm_ink, or rm_wire; those keys require the older mapping table
            # and can incorrectly block BoM creation.
            legacy_struct_bridge = []
        if legacy_struct_bridge:
            bridge_total_qty = sum(_safe_float(item.get("qty"), 0.0) for item in legacy_struct_bridge)
            order_qty_context = _safe_float(payload.get("order_qty"), 0.0)
            component_entry["quantity_bridge"]["writer_bridge_source"] = "legacy_bom_struct_components"
            component_entry["quantity_bridge"]["bridge_component_count"] = len(legacy_struct_bridge)
            component_entry["quantity_bridge"]["bridge_total_qty"] = bridge_total_qty
            component_entry["quantity_bridge"]["bridge_total_qty_per_fg"] = (
                bridge_total_qty / order_qty_context if order_qty_context > 0.0 else 0.0
            )
            component_entry["quantity_bridge"]["legacy_struct_components"] = [
                {
                    "product": item.get("product"),
                    "qty": _safe_float(item.get("qty"), 0.0),
                    "uom": item.get("uom"),
                    "stage": item.get("stage"),
                    "kind": (item.get("metadata") or {}).get("kind"),
                }
                for item in legacy_struct_bridge
            ]
        components.append(component_entry)

        if legacy_struct_bridge:
            for bridge_item in legacy_struct_bridge:
                bridge_key = (
                    bridge_item.get("product"),
                    bridge_item.get("uom"),
                    bridge_item.get("stage"),
                    _safe_float(bridge_item.get("qty"), 0.0),
                    str((bridge_item.get("metadata") or {}).get("kind") or ""),
                )
                if bridge_key in emitted_bridge_components:
                    continue
                emitted_bridge_components.add(bridge_key)
                struct_components.append(
                    {
                        "product": bridge_item.get("product"),
                        "qty": _safe_float(bridge_item.get("qty"), 0.0),
                        "uom": bridge_item.get("uom") or component_entry["uom_name"] or row.get("uom") or "",
                        "stage": bridge_item.get("stage") or component_entry["component_stage_label"],
                        "metadata": dict(bridge_item.get("metadata") or {}),
                    }
                )
        else:
            struct_meta = dict(metadata)
            struct_meta["semantic_role"] = semantic_role
            struct_meta["legacy_role_aliases"] = list(component_entry["legacy_role_aliases"] or [])
            struct_meta["stage_code"] = resolved_stage_code
            struct_meta["stage_contract_code"] = resolved_stage_contract_code
            struct_meta["stage_template_id"] = resolved_stage_template_id or None
            struct_meta["operation_key"] = stage_entry.get("operation_key")
            struct_meta["operation_name"] = stage_entry.get("operation_name")
            struct_meta["quantity_source"] = quantity_source
            struct_meta["component_key"] = component_entry["component_key"]
            if resolved_product_id:
                struct_meta["product_id"] = resolved_product_id
                struct_meta["writer_product_key"] = "product.product:%s" % resolved_product_id
            if resolved_uom_id:
                struct_meta["uom_id"] = resolved_uom_id
            if resolved_product_name:
                struct_meta["product_name"] = resolved_product_name
                struct_meta["resolution_product_name"] = resolved_product_name
            if resolved_uom_name:
                struct_meta["uom_name"] = resolved_uom_name
            if exact_preview_resolution:
                struct_meta["resolution_status"] = preview_resolution_status or "exact"
                struct_meta["resolution"] = {
                    **dict(struct_meta.get("resolution") or {}),
                    "product_id": resolved_product_id,
                    "uom_id": resolved_uom_id,
                    "product_name": resolved_product_name,
                    "uom_name": resolved_uom_name,
                    "status": preview_resolution_status or "exact",
                }
            if semantic_role.startswith("adhesive"):
                struct_meta["kind"] = semantic_role
            elif semantic_role in ("liner", "medium"):
                struct_meta["type"] = semantic_role
                struct_meta["role"] = semantic_role
                if layer_no:
                    struct_meta["layer_no"] = layer_no
            struct_components.append(
                {
                    "product": product_key,
                    "qty": total_qty,
                    "uom": component_entry["uom_name"] or row.get("uom") or "",
                    "stage": component_entry["component_stage_label"],
                    "metadata": struct_meta,
                }
            )

    routed_struct_operations: List[Dict[str, Any]] = []
    for stage in stages:
        op_key = stage.get("operation_key")
        if not op_key:
            continue
        metrics = stage.get("operation_metrics") or {}
        input_qty = _safe_float(metrics.get("input_qty"), 0.0)
        output_qty = _safe_float(metrics.get("output_qty"), 0.0)
        if output_qty <= 0.0:
            continue
        routed_struct_operations.append(
            {
                "stage": str(op_key),
                "operation_key": str(op_key),
                "stage_code": stage.get("stage_code"),
                "stage_contract_code": stage.get("stage_contract_code"),
                "stage_template_id": stage.get("stage_template_id"),
                "stage_name": stage.get("stage_name"),
                "stage_type": stage.get("stage_type"),
                "capability_code": stage.get("capability_code"),
                "input_qty": input_qty,
                "output_qty": output_qty,
                "uom": metrics.get("uom") or "",
                "source": metrics.get("source") or "",
                "sequence": _safe_int(stage.get("sequence"), 0),
                "workcenter_id": stage.get("workcenter_id"),
                "routing_workcenter_id": stage.get("routing_workcenter_id"),
            }
        )
    routed_struct_operations = sorted(
        routed_struct_operations,
        key=lambda item: (
            _safe_int(item.get("sequence"), 0),
            str(item.get("stage_code") or ""),
            str(item.get("operation_key") or ""),
        ),
    )

    struct_operations: List[Dict[str, Any]] = []
    grouped_operations: Dict[str, Dict[str, Any]] = {}
    for item in routed_struct_operations:
        op_name = str(item.get("operation_key") or item.get("stage") or "")
        input_qty = _safe_float(item.get("input_qty"), 0.0)
        output_qty = _safe_float(item.get("output_qty"), 0.0)
        if input_qty <= 0.0 or output_qty <= 0.0:
            continue
        current = grouped_operations.get(op_name)
        if not current:
            grouped_operations[op_name] = {
                "stage": op_name,
                "input_qty": input_qty,
                "output_qty": output_qty,
                "stage_details": [
                    {
                        "stage_code": item.get("stage_code"),
                        "stage_contract_code": item.get("stage_contract_code"),
                        "stage_template_id": item.get("stage_template_id"),
                        "input_qty": input_qty,
                        "output_qty": output_qty,
                        "uom": item.get("uom") or "",
                        "source": item.get("source") or "",
                        "sequence": _safe_int(item.get("sequence"), 0),
                    }
                ],
            }
            continue
        current["input_qty"] = min(_safe_float(current.get("input_qty"), input_qty), input_qty)
        current["output_qty"] = max(_safe_float(current.get("output_qty"), output_qty), output_qty)
        current.setdefault("stage_details", []).append(
            {
                "stage_code": item.get("stage_code"),
                "stage_contract_code": item.get("stage_contract_code"),
                "stage_template_id": item.get("stage_template_id"),
                "input_qty": input_qty,
                "output_qty": output_qty,
                "uom": item.get("uom") or "",
                "source": item.get("source") or "",
                "sequence": _safe_int(item.get("sequence"), 0),
            }
        )

    for item in grouped_operations.values():
        item["stage_details"] = sorted(
            list(item.get("stage_details") or []),
            key=lambda detail: (
                _safe_int(detail.get("sequence"), 0),
                str(detail.get("stage_code") or ""),
            ),
        )
        struct_operations.append(item)

    writer_payload = {
        "product_qty": _safe_float(payload.get("order_qty"), 0.0),
        "components": struct_components,
        "operations": struct_operations,
        "routed_operations": routed_struct_operations,
    }
    legacy_signature_struct = payload.get("bom_struct") or {}
    if not writer_payload["components"] and list((legacy_signature_struct or {}).get("components") or []):
        writer_payload["components"] = list((legacy_signature_struct or {}).get("components") or [])
        writer_payload["component_source"] = "legacy_bom_struct_fallback"
    selected_layout = payload.get("selected_layout")
    if not isinstance(selected_layout, dict):
        for entry in payload.get("paper_resolution") or []:
            if isinstance(entry, dict) and isinstance(entry.get("selected_layout"), dict):
                selected_layout = entry.get("selected_layout")
                break
    if isinstance(selected_layout, dict):
        writer_payload["selected_layout"] = selected_layout
    signature_struct, signature_source = _preferred_stagecontext_signature_struct(
        writer_payload,
        legacy_signature_struct,
    )
    signature_payload = {
        "spec_id": payload.get("spec_id"),
        "plant_id": payload.get("plant_id"),
        "style_code": payload.get("style_code"),
        "metrics": payload.get("metrics") or {},
        "bom_struct": signature_struct,
        "order_qty": payload.get("order_qty"),
    }
    signature_key = _compute_signature_from_preview_payload(signature_payload)
    return {
        "identity": {
            "spec_id": payload.get("spec_id"),
            "plant_id": payload.get("plant_id"),
            "order_qty_context": _safe_float(payload.get("order_qty"), 0.0),
            "signature_source": signature_source,
            "signature_key": signature_key,
        },
        "stages": stages,
        "components": components,
        "writer_payload": writer_payload,
        "diagnostics": {
            "bridges_used": sorted(
                {
                    c.get("quantity_source")
                    for c in components
                    if str(c.get("quantity_source") or "").strip()
                }
            ),
            "warnings": list((preview_model.get("warnings") or [])),
            "parity_notes": [
                "StageContext/runtime owns structure and semantic identity.",
                "legacy rm_requirements temporarily bridge component quantities.",
                "paper_resolution temporarily resolves exact liner/medium products.",
                "legacy bom_struct temporarily anchors signature/reuse parity.",
            ],
        },
    }


def build_bom_from_engine(env, engine_result: Any, fg_product) -> Any:
    """Create or update an mrp.bom from an EngineResult.

    This adapter lives entirely in corr_mrp_bridge (Odoo layer) and
    converts the pure Engine V2 output into a concrete BoM. It does not
    modify the engine or recompute any quantities.

    Inputs:
        env         - Odoo environment
        engine_result - EngineResult from engine_v2.compute()
        fg_product  - product.product instance for the finished good
    """

    Bom = env["mrp.bom"].sudo()
    BomLine = env["mrp.bom.line"].sudo()
    Spec = env["corr.spec.template"].sudo()
    Map = env["corr.material.product.map"].sudo()
    Product = env["product.product"].sudo()
    OpMap = env["corr.operation.map"].sudo()
    RoutingOp = env["mrp.routing.workcenter"].sudo()

    # Resolve spec record when available so we can link corr_spec_id.
    spec = None
    try:
        spec_id = int(getattr(engine_result, "spec_id", 0) or 0)
    except Exception:
        spec_id = 0
    if spec_id:
        spec_rec = Spec.browse(spec_id)
        if spec_rec and spec_rec.exists():
            spec = spec_rec

    # Create or update BoM for the FG product/template.
    product_tmpl = fg_product.product_tmpl_id
    if not product_tmpl:
        raise UserError(_("Finished product is missing a product template."))

    domain = [("product_tmpl_id", "=", product_tmpl.id)]
    if spec:
        domain.append(("corr_spec_id", "=", spec.id))

    bom = Bom.search(domain, limit=1)
    vals = {
        "product_tmpl_id": product_tmpl.id,
        "product_id": fg_product.id,
        "product_qty": 1.0,
        "type": "normal",
        "consumption": "warning",
    }
    if spec:
        vals["corr_spec_id"] = spec.id

    # Store engine trace for debugging when available.
    try:
        trace = list(getattr(engine_result, "trace", []) or [])
    except Exception:
        trace = []
    if trace:
        try:
            import json as _json

            trace_payload = [{"step": step, "payload": payload} for step, payload in trace]
            if _model_has_field(Bom, "corr_engine_trace"):
                vals["corr_engine_trace"] = _json.dumps(trace_payload, indent=2)
        except Exception:
            # Trace must never block BoM creation.
            pass

    if bom:
        bom.write(vals)
    else:
        bom = Bom.create(vals)

    # Remove only lines previously generated by the engine adapter so
    # that manual or external lines remain intact.
    to_unlink = bom.bom_line_ids.filtered(
        lambda l: bool(getattr(l, "corr_engine_generated", False))
        or (
            bool(getattr(l, "corr_component_key", False))
            and not bool(getattr(l, "corr_manual", False))
        )
    )
    to_unlink.unlink()

    # Map RM requirements to BoM lines using corr.material.product.map.
    rm_requirements = list(getattr(engine_result, "rm_requirements", []) or [])
    if not rm_requirements:
        return bom

    line_vals = []
    missing = []
    for rm in rm_requirements:
        raw_role = getattr(rm, "role", "") or ""
        base_role = _normalize_role_for_mapping(raw_role)

        metadata = getattr(rm, "metadata", {}) or {}

        product = None
        uom = None

        # Product-first behaviour for external unit-sheet top sheets
        # when a specific product has been selected on the surface
        # profile. This path bypasses corr.material.product.map and
        # keeps existing behaviour for roll-mode and unmapped cases.
        if base_role == "top_sheet":
            kind = (metadata.get("kind") or "").strip().lower()
            if kind == "top_sheet_external":
                ts_pid = metadata.get("top_sheet_product_id")
                try:
                    ts_pid_int = int(ts_pid or 0)
                except Exception:
                    ts_pid_int = 0
                if ts_pid_int:
                    prod_rec = Product.browse(ts_pid_int)
                    if prod_rec and prod_rec.exists():
                        product = prod_rec
                        uom = prod_rec.uom_id

        if base_role == "wire":
            wire_pid = metadata.get("wire_product_id") or metadata.get("product_id")
            try:
                wire_pid_int = int(wire_pid or 0)
            except Exception:
                wire_pid_int = 0
            if wire_pid_int:
                prod_rec = Product.browse(wire_pid_int)
                if prod_rec and prod_rec.exists():
                    product = prod_rec
                    uom = prod_rec.uom_id

        if product is None or uom is None:
            engine_code = None
            product_key = str(getattr(rm, "product_key", "") or "").strip().lower()
            if base_role == "adhesive":
                kind = str(metadata.get("kind") or "").strip().lower()
                if not kind:
                    kind = str(getattr(rm, "semantic_role", "") or "").strip().lower()
                if not kind and product_key.startswith("rm_adhesive_"):
                    kind = product_key.replace("rm_adhesive_", "", 1)
                if kind in ("adhesive_corrugation", "corrugation"):
                    engine_code = "corrugation"
                elif kind in ("adhesive_board", "board"):
                    engine_code = "board"
                elif kind in ("adhesive_fg", "fg", "seaming"):
                    engine_code = "fg"
            elif base_role == "ink":
                engine_code = _normalize_subtype_code(metadata.get("print_process"))
            elif base_role == "wire":
                engine_code = _normalize_subtype_code(metadata.get("wire_grade"))
            elif base_role == "lamination":
                engine_code = _normalize_subtype_code(
                    metadata.get("lamination_type") or metadata.get("kind")
                )
            mapping = Map.find_match(base_role, engine_code=engine_code)
            if not mapping:
                product, uom = _resolve_engine_material_profile_product(
                    env,
                    base_role,
                    engine_code=engine_code,
                )
                if product is None or uom is None:
                    missing.append(base_role or raw_role)
                    continue
            else:
                product = mapping.product_id
                uom = mapping.uom_id or product.uom_id
        qty = float(getattr(rm, "qty_per_fg", 0.0) or 0.0)
        if qty <= 0:
            continue

        layer_no = getattr(rm, "layer_no", None)
        try:
            layer_idx = int(layer_no or 0)
        except Exception:
            layer_idx = 0

        line_vals.append(
            {
                "bom_id": bom.id,
                "product_id": product.id,
                "product_qty": qty,
                "product_uom_id": uom.id,
                "corr_role": base_role,
                "corr_layer_no": layer_idx,
                "corr_engine_generated": True,
            }
        )

    if missing:
        # Surface missing mappings explicitly so users can configure them.
        missing_str = ", ".join(sorted(set(missing)))
        raise UserError(_("Missing material/product mappings for roles: %s") % missing_str)

    if line_vals:
        BomLine.create([_filter_model_vals(BomLine, vals) for vals in line_vals])

    # Derive simple operations from the StageGraph when present.
    try:
        stage_graph = getattr(engine_result, "stage_graph", None)
    except Exception:
        stage_graph = None
    if stage_graph:
        metrics = getattr(engine_result, "metrics", None)
        _build_operations_from_stage_graph(RoutingOp, OpMap, bom, stage_graph, metrics)

    return bom


def _aggregate_consumables(
    line_items: List[Dict[str, Any]], bom_struct: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Aggregate consumable BoM lines in the *adapter layer only*.

    Key guarantees:
    - Aggregation happens after Engine V2 has produced its RM list and
      ``bom_struct``; the engine result and preview/raw_json are never
      mutated here.
    - Aggregation is currently **product-level only** for adhesive
      consumables (``corr_role == \"adhesive\"``), grouping by
      ``(product_id, product_uom_id)`` and summing ``product_qty`` per
      group. The logical stage (corrugation/board/fg) is *not* part of
      the aggregation key; stage-specific behaviour is preserved only
      in the preview payload and adapter metadata.
    - Non-adhesive components are passed through unchanged.
    - Stage-aware fallback / merging rules (e.g., handling plants with
      different material-stage layouts) are **not implemented yet**.
      When required, those rules should be added here without changing
      the Engine V2 payload shape.

    The ``bom_struct`` argument is accepted for future enhancements
    (e.g., stage-aware policies) but is not inspected or mutated by the
    current implementation.
    """

    if not line_items:
        return line_items

    aggregated: List[Dict[str, Any]] = []
    # Roles that are eligible for product-level aggregation. This is
    # intentionally narrow for now so that future consumables (ink,
    # lamination, wire, tape, etc.) can opt in gradually.
    aggregatable_roles = {"adhesive"}
    # Keyed by product + UoM; the corr_role for adhesive is always the
    # generic "adhesive" on BoM lines, so including it in the key would
    # not add information but could interfere if future adapters
    # decide to expose more granular roles.
    groups: Dict[tuple, Dict[str, Any]] = {}

    for item in line_items:
        role = (item.get("corr_role") or "").strip().lower()
        if role not in aggregatable_roles:
            aggregated.append(item)
            continue

        key = (item.get("product_id"), item.get("product_uom_id"))
        try:
            qty = float(item.get("product_qty", 0.0) or 0.0)
        except Exception:
            qty = 0.0

        existing = groups.get(key)
        if not existing:
            cloned = dict(item)
            cloned["product_qty"] = qty
            # Keep a lightweight record of original consumable kinds for
            # debugging without exposing it on the final BoM line. This
            # metadata is adapter-internal and never written to mrp.bom.
            meta = dict(item.get("metadata") or {})
            kind = (meta.get("kind") or "").strip().lower()
            if kind:
                meta["original_consumable_kinds"] = [kind]
            cloned["metadata"] = meta
            groups[key] = cloned
        else:
            existing["product_qty"] = float(existing.get("product_qty", 0.0) or 0.0) + qty
            meta = dict(existing.get("metadata") or {})
            kinds = list(meta.get("original_consumable_kinds") or [])
            # Pull kind from current item's metadata when present.
            k = (item.get("metadata", {}) or {}).get("kind")
            if k:
                k_norm = str(k).strip().lower()
                if k_norm and k_norm not in kinds:
                    kinds.append(k_norm)
            if kinds:
                meta["original_consumable_kinds"] = kinds
            existing["metadata"] = meta

    # Append aggregated adhesive groups to the result.
    aggregated.extend(groups.values())

    return aggregated


def _build_operations_from_stage_graph(RoutingOp, OpMap, bom, stage_graph, metrics) -> None:
    """Create basic routing operations for the given BoM from a StageGraph.

    One operation is created per material stage except for the final
    stage (FG). Workcenters are resolved via corr.operation.map when
    available; otherwise operations are created without a workcenter.
    """

    stages = list(getattr(stage_graph, "stages", []) or [])
    if not stages:
        return

    # Remove any operations previously generated by the engine adapter.
    ops_to_unlink = bom.operation_ids.filtered(
        lambda op: bool(getattr(op, "corr_engine_generated", False))
    )
    _archive_operations(ops_to_unlink)

    final_key = stage_graph.final_stage_key()
    ops_to_create: List[Dict[str, Any]] = []

    # Derive passes_per_sheet from layout and UPS when available.
    passes_per_sheet = 1
    try:
        layout_blanks_per_sheet = int(
            (getattr(metrics, "extra", {}) or {}).get("layout_blanks_per_sheet", 0) or 0
        )
        effective_ups = int(getattr(metrics, "effective_ups", 0) or 0)
        if layout_blanks_per_sheet > 0 and effective_ups > 0 and layout_blanks_per_sheet > effective_ups:
            passes_per_sheet = int(math.ceil(layout_blanks_per_sheet / float(effective_ups)))
    except Exception:
        passes_per_sheet = 1
    if passes_per_sheet <= 0:
        passes_per_sheet = 1

    legacy_material_keys = {"2ply", "board", "fg"}

    def _stage_text(stage, attr: str) -> str:
        try:
            return str(getattr(stage, attr, "") or "").strip().lower()
        except Exception:
            return ""

    def _stage_ops(stage) -> List[str]:
        try:
            raw_ops = list(getattr(stage, "operations", []) or [])
        except Exception:
            raw_ops = []
        return [str(item or "").strip().lower() for item in raw_ops if str(item or "").strip()]

    def _is_route_driven() -> bool:
        for stage in stages:
            if _stage_text(stage, "stage_type") == "operation":
                return True
            if _stage_text(stage, "output_kind"):
                return True
            key = _stage_text(stage, "key")
            if key and key not in legacy_material_keys:
                return True
        return False

    def _op_name_for_stage(stage) -> Optional[str]:
        """Map an engine stage to a high-level operation name."""

        role = _stage_text(stage, "logical_role")
        key = _stage_text(stage, "key")
        output_kind = _stage_text(stage, "output_kind")
        ops_norm = _stage_ops(stage)

        if role == "corrugation" or key in ("2ply", "3ply", "singleface") or output_kind == "board":
            return "Corrugation"
        # Printing is driven by routing/profile operations rather than
        # blindly injected: look for an explicit printing/ts_print step
        # on the material stage or its logical role.
        has_print = any(o in ("ts_print", "printing", "lamination") for o in ops_norm)
        if role in ("printing", "lamination") or key in ("ts_print", "lamination") or output_kind == "sheet" or has_print:
            return "Printing"
        # Diecut / slotting similarly comes from routing operations.
        has_diecut = any(o in ("diecut", "slot/die", "peeloff", "peel-off") for o in ops_norm)
        if role == "diecut" or key in ("diecut", "peeloff", "peel-off") or output_kind == "blank" or has_diecut:
            return "Slot/Die"
        if role == "conversion" or key in ("seaming", "conversion", "final") or output_kind == "box":
            return "Conversion"
        return None

    def _is_final_fg_sink(stage) -> bool:
        key = _stage_text(stage, "key")
        role = _stage_text(stage, "logical_role")
        output_kind = _stage_text(stage, "output_kind")
        return bool(final_key and key == str(final_key).strip().lower() and role == "fg" and output_kind in ("", "fg"))

    def _resolve_wc_id(op_name: str) -> Optional[int]:
        wc_id = False
        try:
            mapping = OpMap.search(
                [("op_name", "=", op_name), ("active", "=", True), ("workcenter_id", "!=", False)],
                limit=1,
            )
            if mapping and mapping.workcenter_id:
                wc_id = mapping.workcenter_id.id
        except Exception:
            wc_id = False
        if not wc_id:
            try:
                WC = RoutingOp.env["mrp.workcenter"].sudo()
                wc = WC.search([], limit=1)
                if not wc:
                    wc = WC.create({"name": "Engine V2 Auto Workcenter"})
                wc_id = wc.id
            except Exception:
                wc_id = False
        return wc_id or None

    sequence = 10
    seen_names = set()
    route_driven = _is_route_driven()
    for stage in stages:
        if _is_final_fg_sink(stage):
            continue
        op_name = _op_name_for_stage(stage)
        if not op_name:
            continue
        if op_name in seen_names:
            continue
        seen_names.add(op_name)

        # Resolve workcenter from corr.operation.map when possible,
        # falling back to a generic auto-created workcenter.
        wc_id = _resolve_wc_id(op_name)

        vals = {
            "bom_id": bom.id,
            "name": op_name,
            "sequence": sequence,
            "workcenter_id": wc_id,
            "time_mode": "manual",
            # Use passes_per_sheet as a simple proxy for cycle time on
            # printing and diecut stages when layout requires multiple
            # passes; leave others at zero for now.
            "time_cycle_manual": float(passes_per_sheet if op_name in ("Printing", "Slot/Die") else 0.0),
            "corr_engine_generated": True,
        }
        ops_to_create.append(vals)
        sequence += 10

    # Fallback: ensure a Printing operation exists when layout and UPS
    # are defined but no printing stage produced an operation. This
    # keeps routing semantics compatible with older flows where
    # printing was injected unconditionally.
    if passes_per_sheet > 0 and "Printing" not in seen_names and not route_driven:
        wc_id = _resolve_wc_id("Printing")

        vals = {
            "bom_id": bom.id,
            "name": "Printing",
            "sequence": sequence,
            "workcenter_id": wc_id,
            "time_mode": "manual",
            "time_cycle_manual": float(passes_per_sheet),
            "corr_engine_generated": True,
        }
        ops_to_create.append(vals)

    if ops_to_create:
        RoutingOp.create([_filter_model_vals(RoutingOp, vals) for vals in ops_to_create])


def create_bom_from_engine(env, engine_result: Any, fg_product, order_qty: float) -> Any:
    """Create or update an mrp.bom from EngineResult using the Engine V2 BOM adapter.

    This helper uses the pure-Python BOM structure produced by
    engine_v2.bom_adapter.build_bom_structure and converts it into
    concrete mrp.bom, mrp.bom.line and routing operations. It does not
    recompute any engine quantities.

    Inputs:
        env         - Odoo environment
        engine_result - EngineResult from engine_v2.compute()
        fg_product  - product.product instance for the finished good
        order_qty   - finished good quantity represented by this BoM
    """

    if build_engine_bom_structure is None:
        raise UserError(_("Corrugation Engine v2 is not installed."))

    # Build BOM structure from the engine adapter (pure Python).
    try:
        bom_struct = build_engine_bom_structure(engine_result, order_qty)
    except Exception as exc:  # pragma: no cover - defensive
        raise UserError(_("Engine V2 BOM adapter failed: %s") % exc)
    try:
        spec_id = int(getattr(engine_result, "spec_id", 0) or 0)
    except Exception:
        spec_id = None
    try:
        trace = list(getattr(engine_result, "trace", []) or [])
    except Exception:
        trace = None

    signature = _compute_signature_from_engine_result(engine_result, bom_struct, order_qty)
    return _create_bom_from_struct(
        env,
        fg_product,
        bom_struct,
        spec_id=spec_id,
        trace=trace,
        signature=signature,
        order_qty=order_qty,
    )


def _create_bom_from_struct(
    env,
    fg_product,
    bom_struct: Dict[str, Any],
    *,
    spec_id: Optional[int] = None,
    trace: Optional[Any] = None,
    signature: Optional[str] = None,
    order_qty: Optional[float] = None,
    paper_resolution: Optional[Dict[Any, Dict[str, Any]]] = None,
    paper_resolution_policy: str = "fallback_unresolved",
    diagnostics_sink: Optional[Dict[str, Any]] = None,
) -> Any:
    Bom = env["mrp.bom"].sudo()
    BomLine = env["mrp.bom.line"].sudo()
    Spec = env["corr.spec.template"].sudo()
    Map = env["corr.material.product.map"].sudo()
    OpMap = env["corr.operation.map"].sudo()
    RoutingOp = env["mrp.routing.workcenter"].sudo()
    Product = env["product.product"].sudo()
    Uom = env["uom.uom"].sudo()

    # BoM base quantity is always 1 FG unit for reusability.
    base_product_qty = 1.0

    spec = None
    try:
        sid = int(spec_id or 0)
    except Exception:
        sid = 0
    if sid:
        spec_rec = Spec.browse(sid)
        if spec_rec and spec_rec.exists():
            spec = spec_rec

    product_tmpl = fg_product.product_tmpl_id
    if not product_tmpl:
        raise UserError(_("Finished product is missing a product template."))

    has_v2_signature_field = _model_has_field(Bom, "corr_engine_v2_signature")

    def _bom_has_mo_reference(candidate) -> bool:
        if not candidate:
            return False
        try:
            return bool(
                env["mrp.production"].sudo().search_count(
                    [("bom_id", "=", candidate.id)],
                    limit=1,
                )
            )
        except TypeError:
            return bool(
                env["mrp.production"].sudo().search_count(
                    [("bom_id", "=", candidate.id)]
                )
            )
        except Exception:
            _logger.exception(
                "Engine V2 BoM usage check failed for bom_id=%s; treating BoM as used",
                candidate.id,
            )
            return True

    def _current_engine_v2_fg_bom():
        current = getattr(product_tmpl, "corr_current_engine_v2_bom_id", Bom.browse())
        try:
            current = current.exists()
        except Exception:
            current = Bom.browse()
        if current and hasattr(current, "_corr_is_valid_current_engine_v2_bom_for"):
            try:
                if current._corr_is_valid_current_engine_v2_bom_for(fg_product, env.company):
                    return current
            except Exception:
                pass
        domain_current = [
            ("product_tmpl_id", "=", product_tmpl.id),
            ("corr_engine_v2_generated", "=", True),
            ("corr_is_current_engine_v2_bom", "=", True),
            ("corr_engine_v2_signature", "!=", False),
            ("corr_sfg_key", "=", False),
            ("corr_stage", "=", False),
            ("type", "=", "normal"),
            ("active", "=", True),
        ]
        if "company_id" in getattr(Bom, "_fields", {}):
            domain_current.append(("company_id", "in", [False, env.company.id]))
        for candidate in Bom.search(
            domain_current,
            order="company_id desc, corr_engine_v2_version desc, id desc",
        ):
            if not hasattr(candidate, "_corr_is_valid_current_engine_v2_bom_for"):
                return candidate
            try:
                if candidate._corr_is_valid_current_engine_v2_bom_for(fg_product, env.company):
                    return candidate
            except Exception:
                continue
        return Bom.browse()

    exact_bom = Bom.browse()
    reusable_current_bom = Bom.browse()
    if signature and has_v2_signature_field:
        exact_bom = Bom.search(
            [
                ("product_tmpl_id", "=", product_tmpl.id),
                ("corr_engine_v2_signature", "=", signature),
            ],
            limit=1,
        )
        bom = exact_bom
        if not bom:
            current_bom = _current_engine_v2_fg_bom()
            if current_bom and not _bom_has_mo_reference(current_bom):
                reusable_current_bom = current_bom
                bom = current_bom
    else:
        domain = [("product_tmpl_id", "=", product_tmpl.id)]
        if spec:
            # Fallback for legacy callers when no signature is provided.
            domain.append(("corr_spec_id", "=", spec.id))
        bom = Bom.search(domain, limit=1)

    vals = {
        "product_tmpl_id": product_tmpl.id,
        "product_id": fg_product.id,
        "product_qty": base_product_qty,
        "type": "normal",
        "consumption": "warning",
    }
    if spec:
        vals["corr_spec_id"] = spec.id
    if signature and has_v2_signature_field:
        vals["corr_engine_v2_signature"] = signature
    if signature and _model_has_field(Bom, "corr_engine_v2_generated"):
        vals["corr_engine_v2_generated"] = True

    if trace is not None:
        try:
            if isinstance(trace, list) and trace and isinstance(trace[0], tuple):
                trace_payload = [{"step": step, "payload": payload} for step, payload in trace]
            else:
                trace_payload = trace
            if _model_has_field(Bom, "corr_engine_trace"):
                vals["corr_engine_trace"] = json.dumps(trace_payload, indent=2)
        except Exception:
            pass

    def _mark_current_engine_v2_bom():
        try:
            if signature and hasattr(bom, "_corr_mark_as_current_engine_v2_fg_bom"):
                bom._corr_mark_as_current_engine_v2_fg_bom()
        except Exception:
            _logger.exception("Engine V2 current BoM mark failed for flat adapter BoM %s", bom.id)

    components = list(bom_struct.get("components") or [])
    if not components:
        with env.cr.savepoint():
            if bom:
                bom.write(vals)
            else:
                bom = Bom.create(vals)
            _mark_current_engine_v2_bom()
        return bom

    # Normalise component quantities to a per-FG basis using the
    # order quantity that the preview/engine used to build the
    # BOM structure. This keeps the BoM reusable (base qty = 1 FG)
    # regardless of the preview order size.
    try:
        oq = float(order_qty or 0.0)
    except Exception:
        oq = 0.0
    if oq <= 0.0:
        oq = 1.0

    draft_lines: List[Dict[str, Any]] = []
    missing: List[str] = []
    unresolved_paper: List[Dict[str, Any]] = []
    paper_resolution_fallbacks: List[Dict[str, Any]] = []
    uom_warnings: List[Dict[str, Any]] = []

    for comp in components:
          product_key = (comp.get("product") or "").strip()
          try:
              qty_total = float(comp.get("qty", 0.0) or 0.0)
          except Exception:
              qty_total = 0.0
          if not product_key or qty_total <= 0.0:
              continue

          qty = qty_total / oq

          base_role = _normalize_role_for_mapping(product_key)
          metadata = comp.get("metadata") or {}
          kind = str(metadata.get("kind") or metadata.get("role") or "").strip().lower()
          component_binding = _component_operation_binding(comp)
          layer_idx = _safe_int(metadata.get("layer_no"), 0)
          exact_paper_role = base_role in ("liner", "medium", "carton_board") or kind == "top_roll" or product_key.strip().lower() == "rm_top_roll"

          product = None
          uom = None
          engine_code = None
          paper_issue = None
          explicit_product_id, explicit_uom_id, explicit_status = _explicit_component_product(
              metadata,
              comp,
          )
          strict_paper_resolution = (paper_resolution_policy or "fallback_unresolved").strip().lower() == "strict_required"

          if explicit_product_id and explicit_status not in ("missing", "unresolved") and (
              not exact_paper_role or not strict_paper_resolution or explicit_status in ("exact", "resolved")
          ):
              prod_rec = Product.browse(explicit_product_id)
              if prod_rec and prod_rec.exists():
                  product = prod_rec
                  if explicit_uom_id:
                      uom_rec = Uom.browse(explicit_uom_id)
                      uom = uom_rec if uom_rec and uom_rec.exists() else prod_rec.uom_id
                  else:
                      uom = prod_rec.uom_id

          # When preview provides paper_resolution, use it for board papers
          # (liner/medium/carton_board) instead of role-based fallback mapping.
          if product is None and paper_resolution is not None and (kind == "top_roll" or product_key.strip().lower() == "rm_top_roll"):
              resolution_entry = _top_sheet_roll_resolution_entry(metadata, paper_resolution)
              status = str(resolution_entry.get("status") or "").strip().lower()
              prod_id = resolution_entry.get("product_id")
              uom_id = resolution_entry.get("uom_id")
              if prod_id:
                  prod_rec = Product.browse(prod_id)
                  if prod_rec and prod_rec.exists():
                      product = prod_rec
                      if uom_id:
                          uom_rec = Uom.browse(uom_id)
                          uom = uom_rec if uom_rec and uom_rec.exists() else prod_rec.uom_id
                      else:
                          uom = prod_rec.uom_id
              if product is None or uom is None or status != "exact":
                  paper_issue = _paper_resolution_issue(
                      role="top_roll",
                      layer_idx=layer_idx,
                      status=status or "missing",
                      reason=(resolution_entry or {}).get("resolution_note") or metadata.get("resolution_note") or "exact top roll variant not resolved in preview payload",
                      gsm=metadata.get("gsm"),
                      required_deckle_cm=metadata.get("required_deckle_cm"),
                      key=("top_roll", layer_idx),
                  )
          elif product is None and paper_resolution is not None and base_role in ("liner", "medium", "carton_board"):
              try:
                  layer_no = metadata.get("layer_no")
                  layer_idx = int(layer_no or 0)
              except Exception:
                  layer_idx = 0
              key = (base_role, layer_idx)
              res_entry = paper_resolution.get(key)
              status = str((res_entry or {}).get("status") or "missing").strip().lower()
              resolution_note = str((res_entry or {}).get("resolution_note") or "").strip().lower()
              if status == "resolved" or ("resolved" in resolution_note and "not found" not in resolution_note):
                  status = "exact"
              # Gather target GSM and deckle when available so that any
              # error messages can surface the intended requirement.
              try:
                  required_gsm = (res_entry or {}).get("gsm")
              except Exception:
                  required_gsm = None
              if required_gsm is None:
                  required_gsm = metadata.get("gsm")
              try:
                  required_deckle_cm = (res_entry or {}).get("required_deckle_cm")
              except Exception:
                  required_deckle_cm = None
              if required_deckle_cm is None:
                  required_deckle_cm = metadata.get("required_deckle_cm")

              if not res_entry or status == "missing":
                  paper_issue = _paper_resolution_issue(
                      role=base_role,
                      layer_idx=layer_idx,
                      status=status,
                      reason=(res_entry or {}).get("resolution_note") or "exact paper variant not resolved in preview payload",
                      gsm=required_gsm,
                      required_deckle_cm=required_deckle_cm,
                      key=key,
                  )
              elif status != "exact":
                  paper_issue = _paper_resolution_issue(
                      role=base_role,
                      layer_idx=layer_idx,
                      status=status,
                      reason=(res_entry or {}).get("resolution_note") or "preview paper resolution must be exact for BoM creation",
                      gsm=required_gsm,
                      required_deckle_cm=required_deckle_cm,
                      key=key,
                  )
              else:
                  prod_id = res_entry.get("product_id") or None
                  uom_id = res_entry.get("uom_id") or None

                  if not prod_id:
                      paper_issue = _paper_resolution_issue(
                          role=base_role,
                          layer_idx=layer_idx,
                          status=status,
                              reason=(res_entry or {}).get("resolution_note") or "preview exact resolution missing product_id",
                              gsm=required_gsm,
                              required_deckle_cm=required_deckle_cm,
                              key=key,
                          )
                  else:
                      product = Product.browse(prod_id)
                      if not product or not product.exists():
                          paper_issue = _paper_resolution_issue(
                              role=base_role,
                              layer_idx=layer_idx,
                              status=status,
                              reason="resolved product does not exist",
                              gsm=required_gsm,
                              required_deckle_cm=required_deckle_cm,
                              key=key,
                          )
                          product = None
                      else:
                          if uom_id:
                              uom = Uom.browse(uom_id)
                              if not uom or not uom.exists():
                                  uom = product.uom_id
                          else:
                              uom = product.uom_id

          # Non-paper roles are resolved via corr.material.product.map.
          # For adhesives we treat rm_adhesive_* product keys as the
          # generic "adhesive" kind and use engine_code to distinguish
          # corrugation/board/fg usage when mappings are present.
          if product is None and base_role == "top_sheet":
              top_sheet_mode = _top_sheet_mode_for_component(product_key, metadata)
              if top_sheet_mode == "roll":
                  role = _top_sheet_roll_resolution_role(metadata)
                  resolution_entry = _top_sheet_roll_resolution_entry(metadata, paper_resolution)
                  status = str(resolution_entry.get("status") or "").strip().lower()
                  prod_id = resolution_entry.get("product_id")
                  uom_id = resolution_entry.get("uom_id")
                  prod_rec = Product.browse(prod_id) if prod_id else Product.browse()
                  if prod_rec and prod_rec.exists():
                      product = prod_rec
                      if uom_id:
                          uom_rec = Uom.browse(uom_id)
                          uom = uom_rec if uom_rec and uom_rec.exists() else prod_rec.uom_id
                      else:
                          uom = prod_rec.uom_id
                  if product is None or uom is None:
                      pass
                  if product is None or uom is None or status != "exact":
                      layer_no = metadata.get("layer_no")
                      required_deckle_cm = metadata.get("required_deckle_cm")
                      detail = metadata.get("resolution_note") or ""
                      if required_deckle_cm not in (None, ""):
                          detail = (detail + " | " if detail else "") + ("required deckle %.1f cm" % float(required_deckle_cm))
                      raise UserError(
                          _(
                              "Top sheet roll mode could not resolve a paper variant "
                              "(role=%s, layer=%s, status=%s)."
                          )
                          % (role, layer_no or "-", status or "missing")
                          + (" %s" % detail if detail else "")
                      )
              elif top_sheet_mode == "unit_sheet":
                  ts_pid = metadata.get("top_sheet_product_id")
                  try:
                      ts_pid_int = int(ts_pid or 0)
                  except Exception:
                      ts_pid_int = 0
                  if ts_pid_int:
                      prod_rec = Product.browse(ts_pid_int)
                      if prod_rec and prod_rec.exists():
                          product = prod_rec
                          uom = prod_rec.uom_id
                  if product is None or uom is None:
                      raise UserError(
                          _(
                              "Top sheet unit mode requires a selected top-sheet product "
                              "on the spec/surface profile."
                          )
                      )
          if product is None or uom is None:
              if exact_paper_role and paper_resolution is None and strict_paper_resolution:
                  paper_issue = _paper_resolution_issue(
                      role=base_role or kind or product_key,
                      layer_idx=layer_idx,
                      status="missing",
                      reason="preview payload has no exact paper product resolution",
                      gsm=metadata.get("gsm"),
                      required_deckle_cm=metadata.get("required_deckle_cm"),
                      key=(base_role or kind or product_key, layer_idx),
                  )
              if paper_issue and exact_paper_role and strict_paper_resolution:
                  unresolved_paper.append(paper_issue)
                  continue
              if base_role == "top_sheet":
                  missing.append(base_role or product_key)
                  continue
              if base_role == "adhesive":
                  kind = (metadata.get("kind") or "").strip().lower()
                  # Prefer explicit kind from metadata when present.
                  if not kind and product_key.lower().startswith("rm_adhesive_"):
                      kind = product_key.lower().replace("rm_adhesive_", "", 1)
                  if kind in ("adhesive_corrugation", "corrugation"):
                      engine_code = "corrugation"
                  elif kind in ("adhesive_board", "board"):
                      engine_code = "board"
                  elif kind in ("adhesive_fg", "fg", "seaming"):
                      engine_code = "fg"
              elif base_role == "ink":
                  # Ink subtype: normalised print_process from surface profile.
                  engine_code = _normalize_subtype_code(metadata.get("print_process"))
              elif base_role == "wire":
                  # Wire subtype: normalised wire_grade from locking configuration.
                  engine_code = _normalize_subtype_code(metadata.get("wire_grade"))
              elif base_role == "lamination":
                  # Film subtype: normalised lamination_type code. Prefer any
                  # explicit lamination_type in metadata; otherwise, resolve
                  # from lamination_type_id when available.
                  lam_code = metadata.get("lamination_type")
                  if not lam_code:
                      lam_id = metadata.get("lamination_type_id")
                      try:
                          lam_int = int(lam_id or 0)
                      except Exception:
                          lam_int = 0
                      if lam_int:
                          try:
                              LamType = env["corr.lamination.type"].sudo()
                              lam = LamType.browse(lam_int)
                              if lam and lam.exists():
                                  lam_code = lam.code or None
                          except Exception:
                              lam_code = lam_code
                  engine_code = _normalize_subtype_code(lam_code)
              mapping = Map.find_match(base_role, engine_code=engine_code)
              if not mapping:
                  product, uom = _resolve_engine_material_profile_product(
                      env,
                      base_role,
                      engine_code=engine_code,
                  )
                  if product is not None and uom is not None:
                      mapping = None
                  else:
                      product = None
                      uom = None
                  if product is not None and uom is not None:
                      pass
                  elif paper_issue and exact_paper_role and paper_resolution is not None:
                      unresolved_paper.append(
                          {
                              **paper_issue,
                                "fallback_attempted": False,
                                "fallback_authority": "corr.material.product.map",
                              "fallback_outcome": "blocked_by_exact_only_policy",
                          }
                      )
                      continue
                  else:
                      missing.append(base_role or product_key)
                      continue
              if not mapping and product is not None and uom is not None:
                  pass
              elif not mapping:
                  if paper_issue and exact_paper_role and paper_resolution is not None:
                      unresolved_paper.append(
                          {
                              **paper_issue,
                                "fallback_attempted": False,
                                "fallback_authority": "corr.material.product.map",
                              "fallback_outcome": "blocked_by_exact_only_policy",
                          }
                      )
                      continue
                  missing.append(base_role or product_key)
                  continue
              elif paper_issue and exact_paper_role and paper_resolution is not None:
                  unresolved_paper.append(
                      {
                          **paper_issue,
                          "fallback_attempted": False,
                          "fallback_authority": "corr.material.product.map",
                          "fallback_outcome": "blocked_by_exact_only_policy",
                      }
                  )
                  continue
              if mapping:
                  product = mapping.product_id
                  uom = mapping.uom_id or product.uom_id

          ctype = (metadata.get("consumption_type") or "area").lower()
          try:
              ref_kg = env.ref("uom.product_uom_kgm")
          except Exception:
              ref_kg = None
          try:
              ref_unit = env.ref("uom.product_uom_unit")
          except Exception:
              ref_unit = None
          try:
              ref_m = env.ref("uom.product_uom_meter")
          except Exception:
              ref_m = None

          def _cat(u):
              return u.category_id.id if u and u.category_id else None

          uom_cat = _cat(uom)
          weight_cat = _cat(ref_kg)
          unit_cat = _cat(ref_unit)
          length_cat = _cat(ref_m)

          warn = False
          if ctype == "area" and weight_cat and uom_cat and uom_cat != weight_cat:
              warn = True
          elif ctype == "unit" and unit_cat and uom_cat and uom_cat != unit_cat:
              warn = True
          elif ctype == "length" and uom_cat and length_cat and weight_cat:
              if uom_cat not in (length_cat, weight_cat):
                  warn = True

          if warn:
              uom_warnings.append(
                  {
                      "product": product.display_name or product_key,
                      "product_key": product_key,
                      "uom": uom.display_name or uom.name,
                      "uom_category": uom.category_id.display_name if uom.category_id else "",
                      "consumption_type": ctype,
                      "level": "warning",
                  }
              )

          draft_lines.append(
              {
                  "product_id": product.id,
                  "product_qty": qty,
                  "product_uom_id": uom.id,
                  "corr_role": base_role,
                  "corr_layer_no": layer_idx,
                  "corr_engine_generated": True,
                  # Carry engine metadata for possible aggregation and
                  # diagnostics; removed before BoM line creation.
                  "metadata": metadata,
                  "product_key": product_key,
                  "component_stage": comp.get("stage"),
                  "component_operation_name": component_binding.get("operation_name"),
                  "component_stage_contract_code": component_binding.get("stage_contract_code"),
                  "component_stage_template_id": component_binding.get("stage_template_id"),
                  "component_stage_code": component_binding.get("stage_code"),
              }
          )

    if unresolved_paper:
        details = []
        for item in unresolved_paper:
            role = item.get("role") or ""
            layer_no = item.get("layer_no")
            status = item.get("status") or "missing"
            reason = item.get("reason") or ""
            required_gsm = item.get("gsm")
            required_deckle_cm = item.get("required_deckle_cm")

            label = "%s layer %s" % (role or "", layer_no or "-")
            if required_gsm is not None:
                label += " gsm=%s" % required_gsm
            if required_deckle_cm not in (None, 0):
                try:
                    label += " deckle=%.1fcm" % float(required_deckle_cm)
                except Exception:
                    pass
            label += " status=%s" % status
            if reason:
                label += " (%s)" % reason
            details.append(label)
        raise UserError(
            _("Engine V2 paper resolution failed for: %s") % "; ".join(details)
        )

    if missing:
        missing_str = ", ".join(sorted(set(missing)))
        raise UserError(_("Missing material/product mappings for roles: %s") % missing_str)

    # If the same Engine V2 signature already has a BoM that is referenced by
    # any MO, reuse it without refreshing generated lines/operations. The
    # signature says the effective spec is unchanged, and preserving linked
    # BoM lines protects historical/in-progress MOs.
    if exact_bom and _bom_has_mo_reference(exact_bom):
        bom = exact_bom
        _mark_current_engine_v2_bom()
        if diagnostics_sink is not None:
            diagnostics_sink["bom_versioning_decision"] = "reuse_exact_used_bom_without_refresh"
            diagnostics_sink["bom_id"] = bom.id
        return bom

    with env.cr.savepoint():
        if bom:
            bom.write(vals)
        else:
            bom = Bom.create(vals)
        if diagnostics_sink is not None:
            if exact_bom:
                decision = "reuse_exact_signature_bom"
            elif reusable_current_bom:
                decision = "reuse_unused_current_bom_for_new_signature"
            else:
                decision = "create_new_bom_version"
            diagnostics_sink["bom_versioning_decision"] = decision
            diagnostics_sink["bom_id"] = bom.id

        to_unlink = bom.bom_line_ids.filtered(lambda l: bool(getattr(l, "corr_engine_generated", False)))
        to_unlink.unlink()
        for item in draft_lines:
            item["bom_id"] = bom.id

    if uom_warnings:
        try:
            bom.write({"corr_uom_warning": json.dumps(uom_warnings, indent=2)})
        except Exception:
            pass
        try:
            summary_lines = []
            count = len(uom_warnings)
            header = _("Engine UoM warnings: %(count)d issue(s) detected") % {"count": count}
            summary_lines.append(header)
            for item in uom_warnings:
                role = item.get("product") or item.get("product_key") or ""
                uom_name = item.get("uom") or ""
                ctype = (item.get("consumption_type") or "").lower()
                if ctype == "area":
                    basis = "area-based"
                elif ctype == "unit":
                    basis = "unit-based"
                elif ctype == "length":
                    basis = "length-based"
                else:
                    basis = ctype or "unknown"
                summary_lines.append(
                    _("- %(role)s: UoM '%(uom)s' may not match %(basis)s consumption")
                    % {"role": role, "uom": uom_name, "basis": basis}
                )
            bom.write({"corr_uom_warning_text": "\n".join(summary_lines)})
        except Exception:
            pass
    else:
        clear_vals = {}
        if _model_has_field(Bom, "corr_uom_warning"):
            clear_vals["corr_uom_warning"] = False
        if _model_has_field(Bom, "corr_uom_warning_text"):
            clear_vals["corr_uom_warning_text"] = False
        if clear_vals:
            try:
                bom.write(clear_vals)
            except Exception:
                pass

    if paper_resolution_fallbacks:
        try:
            existing_warning_text = bom.corr_uom_warning_text or ""
        except Exception:
            existing_warning_text = ""
        fallback_lines = [
            _("Paper resolution fallbacks: %(count)d issue(s) detected") % {"count": len(paper_resolution_fallbacks)}
        ]
        for item in paper_resolution_fallbacks:
            fallback_lines.append("- %s" % _paper_resolution_summary_line(item))
        combined = "\n".join(filter(None, [existing_warning_text, "\n".join(fallback_lines)]))
        try:
            bom.write({"corr_uom_warning_text": combined})
        except Exception:
            pass

    if diagnostics_sink is not None:
        diagnostics_sink["paper_resolution_policy"] = (paper_resolution_policy or "fallback_unresolved").strip().lower()
        diagnostics_sink["paper_resolution_fallbacks"] = list(paper_resolution_fallbacks)
        diagnostics_sink["paper_resolution_unresolved"] = list(unresolved_paper)
        diagnostics_sink["paper_resolution_fallback_used"] = bool(paper_resolution_fallbacks)

    if paper_resolution_fallbacks:
        try:
            existing_trace = bom.corr_engine_trace or ""
            trace_payload = None
            if existing_trace:
                try:
                    trace_payload = json.loads(existing_trace)
                except Exception:
                    trace_payload = {"raw_trace": existing_trace}
            if trace_payload in (None, False, ""):
                trace_payload = {}
            if not isinstance(trace_payload, dict):
                trace_payload = {"engine_trace": trace_payload}
            writer_diag = dict(trace_payload.get("writer_diagnostics") or {})
            writer_diag["paper_resolution_policy"] = (paper_resolution_policy or "fallback_unresolved").strip().lower()
            writer_diag["paper_resolution_fallbacks"] = list(paper_resolution_fallbacks)
            trace_payload["writer_diagnostics"] = writer_diag
            bom.write({"corr_engine_trace": json.dumps(trace_payload, indent=2)})
        except Exception:
            pass

    ops_to_unlink = bom.operation_ids.filtered(
        lambda op: bool(getattr(op, "corr_engine_generated", False))
    )
    _archive_operations(ops_to_unlink)

    ops_to_create: List[Dict[str, Any]] = []
    operation_rows, operation_source = _preferred_bom_operation_rows(bom_struct)
    sequence = 10
    auto_assign_operation_workcenter = _config_bool(
        env,
        "corr_engine_v2.auto_assign_operation_workcenter",
        True,
    )
    required_corrugation_flutes = _required_corrugation_flutes_from_spec(spec)
    selected_layout_wc_id = _selected_layout_workcenter_id(bom_struct)

    def _wc_if_compatible(wc_id: Any, operation_row: Dict[str, Any]) -> Optional[int]:
        wc_int = _safe_int(wc_id, 0)
        if not wc_int:
            return None
        try:
            wc = env["mrp.workcenter"].sudo().browse(wc_int)
            if not wc or not wc.exists():
                return None
        except Exception:
            return None
        if _operation_requires_corrugation_flute(operation_row) and not _workcenter_supports_required_flutes(
            wc,
            required_corrugation_flutes,
        ):
            return None
        return wc.id

    def _resolve_wc_id(op_name: str, operation_row: Dict[str, Any]) -> Optional[int]:
        if not auto_assign_operation_workcenter:
            return None

        configured_wc_id = _wc_if_compatible(_safe_int(operation_row.get("workcenter_id"), 0), operation_row)
        if configured_wc_id:
            return configured_wc_id

        if _operation_requires_corrugation_flute(operation_row):
            layout_wc_id = _wc_if_compatible(selected_layout_wc_id, operation_row)
            if layout_wc_id:
                return layout_wc_id

        try:
            mapping = OpMap.search(
                [("op_name", "=", op_name), ("active", "=", True), ("workcenter_id", "!=", False)],
                order="sequence, id",
            )
            for row in mapping:
                wc_id = _wc_if_compatible(row.workcenter_id.id if row.workcenter_id else None, operation_row)
                if wc_id:
                    return wc_id
        except Exception:
            return None
        return None

    for op in operation_rows:
        op_name = str(op.get("operation_name") or "").strip()
        if not op_name:
            continue

        operation_family_name = str(op.get("operation_family_name") or op_name).strip()
        wc_id = _resolve_wc_id(operation_family_name, op)
        op_type_id = None
        if _model_has_field(RoutingOp, "corr_operation_type_id"):
            try:
                op_type = env["corr.operation.type"].sudo().find_from_label(operation_family_name)
                if not op_type and operation_family_name != op_name:
                    op_type = env["corr.operation.type"].sudo().find_from_label(op_name)
                op_type_id = op_type.id if op_type else None
            except Exception:
                op_type_id = None

        op_sequence = _safe_int(op.get("sequence"), 0)
        if op_sequence <= 0:
            op_sequence = sequence

        vals_op = {
            "bom_id": bom.id,
            "name": op_name,
            "sequence": op_sequence,
            "time_mode": "manual",
            "time_cycle_manual": 0.0,
            "corr_engine_generated": True,
        }
        if wc_id:
            vals_op["workcenter_id"] = wc_id
        else:
            vals_op["workcenter_id"] = False
        if op_type_id:
            vals_op["corr_operation_type_id"] = op_type_id
        ops_to_create.append(vals_op)
        sequence = max(sequence, op_sequence + 10)

    if diagnostics_sink is not None:
        diagnostics_sink["operation_source"] = operation_source
        diagnostics_sink["operation_count"] = len(ops_to_create)

    created_operations = RoutingOp.browse()
    if ops_to_create:
        created_operations = RoutingOp.create([_filter_model_vals(RoutingOp, vals) for vals in ops_to_create])

    # Aggregate consumable components after operations are available so
    # BoM lines can link to their consumed-in operation when possible.
    if draft_lines:
        aggregated_lines = _aggregate_consumables(draft_lines, bom_struct)
        operation_by_identity: Dict[Any, Any] = {}
        operation_by_name: Dict[str, Any] = {}
        for op_row, op_rec in zip(operation_rows, created_operations):
            op_id = getattr(op_rec, "id", None)
            if not op_id:
                continue
            for key in _stage_identity_keys(
                stage_contract_code=op_row.get("stage_contract_code"),
                stage_template_id=op_row.get("stage_template_id"),
                stage_code=op_row.get("stage_code"),
            ):
                operation_by_identity.setdefault(key, op_id)
            op_name = str(op_row.get("operation_name") or "").strip()
            if op_name:
                operation_by_name.setdefault(op_name, op_id)
            family_name = str(op_row.get("operation_family_name") or "").strip()
            if family_name:
                operation_by_name.setdefault(family_name, op_id)

        line_vals = []
        for item in aggregated_lines:
            vals_line = {
                "bom_id": item.get("bom_id"),
                "product_id": item.get("product_id"),
                "product_qty": item.get("product_qty"),
                "product_uom_id": item.get("product_uom_id"),
                "corr_role": item.get("corr_role"),
                "corr_component_key": _draft_line_component_key(item),
                "corr_layer_no": item.get("corr_layer_no", 0),
                "corr_engine_generated": item.get("corr_engine_generated", True),
            }
            operation_id = _lookup_operation_id_for_component(
                item,
                operation_by_identity,
                operation_by_name,
            )
            if operation_id:
                vals_line["operation_id"] = operation_id
            line_vals.append(vals_line)
        if line_vals:
            BomLine.create([_filter_model_vals(BomLine, vals) for vals in line_vals])

    _mark_current_engine_v2_bom()
    return bom


def create_bom_from_stagecontext_preview_payload(
    env,
    payload: Dict[str, Any],
    fg_product,
    diagnostics_sink: Optional[Dict[str, Any]] = None,
) -> Any:
    if not isinstance(payload, dict):
        raise UserError(_("Invalid Engine V2 preview payload."))

    blueprint = build_bom_blueprint_from_stagecontext_preview_payload(env, payload)
    writer_payload = dict(blueprint.get("writer_payload") or {})
    spec_id = payload.get("spec_id")
    trace = payload.get("trace")
    order_qty = payload.get("order_qty")
    legacy_signature_struct = payload.get("bom_struct") or {}
    signature_struct, _signature_source = _preferred_stagecontext_signature_struct(
        writer_payload,
        legacy_signature_struct,
    )
    signature = _compute_signature_from_preview_payload(
        {
            "spec_id": payload.get("spec_id"),
            "plant_id": payload.get("plant_id"),
            "style_code": payload.get("style_code"),
            "metrics": payload.get("metrics") or {},
            "bom_struct": signature_struct,
            "order_qty": order_qty,
        }
    )
    paper_resolution_map = _normalize_paper_resolution_map(payload) or None

    return _create_bom_from_struct(
        env,
        fg_product,
        writer_payload,
        spec_id=spec_id,
        trace=trace,
        signature=signature,
        order_qty=order_qty,
        paper_resolution=paper_resolution_map,
        paper_resolution_policy="strict_required",
        diagnostics_sink=diagnostics_sink,
    )


def create_bom_from_selected_preview_payload(
    env,
    payload: Dict[str, Any],
    fg_product,
    bom_backend: str = "legacy",
    diagnostics_sink: Optional[Dict[str, Any]] = None,
) -> Any:
    backend = _normalize_preview_bom_backend(bom_backend, default="legacy")
    if diagnostics_sink is not None:
        diagnostics_sink["bom_backend_requested"] = backend
    if backend == "stagecontext_shadow":
        bom = create_bom_from_stagecontext_preview_payload(
            env,
            payload,
            fg_product,
            diagnostics_sink=diagnostics_sink,
        )
    else:
        bom = create_bom_from_preview_payload(
            env,
            payload,
            fg_product,
            diagnostics_sink=diagnostics_sink,
        )
    if diagnostics_sink is not None:
        diagnostics_sink["bom_backend_applied"] = backend
    return bom


def create_bom_from_preview_payload(
    env,
    payload: Dict[str, Any],
    fg_product,
    diagnostics_sink: Optional[Dict[str, Any]] = None,
) -> Any:
    if not isinstance(payload, dict):
        raise UserError(_("Invalid Engine V2 preview payload."))

    bom_struct = payload.get("bom_struct") or {}
    if not isinstance(bom_struct, dict):
        raise UserError(_("Engine V2 preview payload is missing a valid BoM structure."))

    spec_id = payload.get("spec_id")
    trace = payload.get("trace")
    order_qty = payload.get("order_qty")
    signature = _compute_signature_from_preview_payload(payload)

    # Optional paper-resolution map from preview payload: key is
    # (role, layer_no) from Engine V2 board layers.
    paper_resolution_raw = payload.get("paper_resolution") or []
    paper_resolution_map: Optional[Dict[Any, Dict[str, Any]]] = None
    if isinstance(paper_resolution_raw, list) and paper_resolution_raw:
        paper_resolution_map = {}
        for entry in paper_resolution_raw:
            if _paper_resolution_entry_is_trace_only(entry):
                continue
            try:
                role = (entry.get("role") or "").strip().lower()
            except Exception:
                role = ""
            try:
                layer_no = entry.get("layer_no")
                layer_idx = int(layer_no or 0)
            except Exception:
                layer_idx = 0
            if not role:
                continue
            paper_resolution_map[(role, layer_idx)] = entry

    return _create_bom_from_struct(
        env,
        fg_product,
        bom_struct,
        spec_id=spec_id,
        trace=trace,
        signature=signature,
        order_qty=order_qty,
        paper_resolution=paper_resolution_map,
        paper_resolution_policy="strict_required",
        diagnostics_sink=diagnostics_sink,
    )


def build_debug_preview_payload(
    env,
    spec,
    plant,
    order_qty: float = 1.0,
    preview_backend: str = "stagecontext",
) -> Dict[str, Any]:
    DebugWizard = env["corr.engine_v2.debug.wizard"]
    wiz = DebugWizard.create(
        {
            "spec_id": spec.id,
            "plant_id": plant.id,
            "order_qty": float(order_qty or 1.0),
            "preview_backend": (preview_backend or "stagecontext"),
        }
    )
    wiz.action_run_debug()
    try:
        payload = json.loads(wiz.raw_json or "{}")
    except Exception:
        raise UserError(_("The Engine V2 debug preview payload could not be parsed."))
    if not isinstance(payload, dict):
        raise UserError(_("The Engine V2 debug preview payload is invalid."))
    return {
        "wizard": wiz,
        "payload": payload,
        "preview_backend_applied": payload.get("preview_backend_applied") or wiz.preview_backend_applied or "legacy",
    }


def create_bom_from_debug_preview(
    env,
    spec,
    plant,
    fg_product,
    order_qty: float = 1.0,
    bom_backend: str = "legacy",
    preview_backend: str = "stagecontext",
    diagnostics_sink: Optional[Dict[str, Any]] = None,
):
    preview_result = build_debug_preview_payload(
        env,
        spec,
        plant,
        order_qty=order_qty,
        preview_backend=preview_backend,
    )
    payload = dict(preview_result.get("payload") or {})
    bom = create_bom_from_selected_preview_payload(
        env,
        payload,
        fg_product,
        bom_backend=bom_backend,
        diagnostics_sink=diagnostics_sink,
    )
    if diagnostics_sink is not None:
        diagnostics_sink["preview_payload_source"] = "debug_wizard"
        diagnostics_sink["preview_backend_applied"] = preview_result.get("preview_backend_applied") or "legacy"
        diagnostics_sink["debug_preview_wizard_id"] = preview_result["wizard"].id
    return bom, payload, preview_result["wizard"]


def _serialize_metrics_for_signature(metrics: Any) -> Dict[str, Any]:
    if not metrics:
        return {}
    try:
        extra = dict(getattr(metrics, "extra", {}) or {})
    except Exception:
        extra = {}
    return {
        "m2_per_box": float(getattr(metrics, "m2_per_box", 0.0) or 0.0),
        "kg_per_box": float(getattr(metrics, "kg_per_box", 0.0) or 0.0),
        "extra": extra,
    }


def _normalise_bom_struct_for_signature(bom_struct: Dict[str, Any], order_qty: float) -> Dict[str, Any]:
    """Return a copy of bom_struct normalised to per-FG quantities.

    This removes the dependence of the signature on the preview
    order quantity so that the same underlying structure reuses
    the same BoM.
    """

    try:
        oq = float(order_qty or 0.0)
    except Exception:
        oq = 0.0
    if oq <= 0.0:
        oq = 1.0

    components_norm: List[Dict[str, Any]] = []
    for comp in bom_struct.get("components") or []:
        product_key = (comp.get("product") or "").strip()
        try:
            qty_total = float(comp.get("qty", 0.0) or 0.0)
        except Exception:
            qty_total = 0.0
        if not product_key or qty_total <= 0.0:
            continue
        qty = qty_total / oq
        components_norm.append(
            {
                "product": product_key,
                "qty": qty,
                "uom": comp.get("uom"),
                "stage": comp.get("stage"),
                "metadata": comp.get("metadata") or {},
            }
        )

    operation_rows, operation_source = _preferred_bom_operation_rows(bom_struct)
    operations_norm: List[Dict[str, Any]] = []
    if operation_source == "routed_operations":
        for op in operation_rows:
            operations_norm.append(
                {
                    "operation_key": op.get("operation_key"),
                    "stage_code": op.get("stage_code"),
                    "stage_contract_code": op.get("stage_contract_code"),
                    "stage_template_id": op.get("stage_template_id"),
                    "sequence": _safe_int(op.get("sequence"), 0),
                    "input_qty": _safe_float(op.get("input_qty"), 0.0) / oq,
                    "output_qty": _safe_float(op.get("output_qty"), 0.0) / oq,
                }
            )
    else:
        for op in operation_rows:
            operations_norm.append(
                {
                    "stage": op.get("stage"),
                    "input_qty": _safe_float(op.get("input_qty"), 0.0) / oq,
                    "output_qty": _safe_float(op.get("output_qty"), 0.0) / oq,
                }
            )

    return {
        "product_qty": 1.0,
        "operation_source": operation_source,
        "components": components_norm,
        "operations": operations_norm,
    }


def _compute_signature_from_preview_payload(payload: Dict[str, Any]) -> str:
    """Compute a stable signature for a preview payload.

    The signature is based on spec, plant, metrics and the
    normalised BoM structure (per-FG quantities), but does not
    depend on the preview order quantity itself.
    """

    bom_struct = payload.get("bom_struct") or {}
    order_qty = payload.get("order_qty") or 1.0
    bom_norm = _normalise_bom_struct_for_signature(bom_struct, order_qty)

    metrics = _serialize_metrics_for_signature(payload.get("metrics"))

    sig_source = {
        "spec_id": payload.get("spec_id"),
        "plant_id": payload.get("plant_id"),
        "style_code": payload.get("style_code"),
        "metrics": metrics,
        "bom_struct": bom_norm,
    }
    data = json.dumps(sig_source, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(data.encode("utf-8")).hexdigest()


def _compute_signature_from_engine_result(engine_result: Any, bom_struct: Dict[str, Any], order_qty: float) -> str:
    """Compute a stable signature for EngineResult-based BoMs."""

    metrics = _serialize_metrics_for_signature(getattr(engine_result, "metrics", None))
    bom_norm = _normalise_bom_struct_for_signature(bom_struct, order_qty)

    sig_source = {
        "spec_id": getattr(engine_result, "spec_id", None),
        "style_code": getattr(engine_result, "style_code", ""),
        "metrics": metrics,
        "bom_struct": bom_norm,
    }
    data = json.dumps(sig_source, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(data.encode("utf-8")).hexdigest()
