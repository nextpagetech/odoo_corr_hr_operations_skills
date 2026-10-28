from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


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


def _style_yield_metadata(metrics: Optional[Any]) -> Dict[str, Any]:
    extra = getattr(metrics, "extra", {}) if metrics is not None else {}
    candidates = [
        ((extra or {}).get("style") or {}).get("metadata"),
        (extra or {}).get("style_metadata"),
        (extra or {}).get("style"),
    ]
    for item in candidates:
        if isinstance(item, dict) and item.get("yield_basis"):
            return dict(item)
    return {}


def _diecut_sheet_stage_quantity(
    *,
    code: str,
    stage_type: str,
    fg_qty: float,
    required_sheets: int,
    fg_per_sheet: int,
    previous_output_qty: Optional[float],
) -> Optional[Dict[str, Any]]:
    code_key = str(code or "").strip().lower()
    stage_type_key = str(stage_type or "").strip().lower()
    gross_blanks = float(required_sheets * fg_per_sheet) if required_sheets > 0 else 0.0

    if code_key in ("2ply", "board", "ts_print") or stage_type_key in ("corrugation", "printing", "lamination"):
        return {
            "target_qty": float(required_sheets),
            "target_uom": "sheet",
            "effective_kind": "board" if code_key in ("2ply", "board") or stage_type_key == "corrugation" else "sheet",
            "input_from_prev": float(previous_output_qty or 0.0),
            "metrics": {
                "required_sheets": float(required_sheets),
                "fg_per_sheet": float(fg_per_sheet),
                "quantity_rule": "diecut_sheet_required_sheets_ceil",
            },
            "tags": ["diecut_sheet_upstream_sheet_qty"],
        }

    if code_key in ("diecut", "slot_die", "slot/die") or stage_type_key == "diecut":
        return {
            "target_qty": gross_blanks,
            "target_uom": "unit",
            "effective_kind": "blank",
            "input_from_prev": float(previous_output_qty or required_sheets or 0.0),
            "metrics": {
                "required_sheets": float(required_sheets),
                "fg_per_sheet": float(fg_per_sheet),
                "gross_blanks": gross_blanks,
                "quantity_rule": "diecut_sheet_diecut_gross_blanks",
            },
            "tags": ["gross_blanks"],
        }

    if code_key in ("peeloff", "peel-off", "peeling", "stripping"):
        return {
            "target_qty": float(previous_output_qty or gross_blanks or fg_qty),
            "target_uom": "unit",
            "effective_kind": "blank",
            "input_from_prev": float(previous_output_qty or gross_blanks or 0.0),
            "metrics": {
                "required_sheets": float(required_sheets),
                "fg_per_sheet": float(fg_per_sheet),
                "usable_blanks": float(previous_output_qty or gross_blanks or fg_qty),
                "quantity_rule": "diecut_sheet_peeloff_usable_blanks",
            },
            "tags": ["usable_blanks"],
        }

    if code_key in ("seaming", "jointing", "gluing", "stitching", "fg", "final") or stage_type_key in ("conversion", "fg"):
        return {
            "target_qty": float(fg_qty),
            "target_uom": "unit",
            "effective_kind": "box",
            "input_from_prev": float(previous_output_qty or fg_qty),
            "metrics": {
                "required_sheets": float(required_sheets),
                "fg_per_sheet": float(fg_per_sheet),
                "final_fg_qty": float(fg_qty),
                "quantity_rule": "diecut_sheet_final_fg_qty",
            },
            "tags": ["finished_goods"],
        }

    return None


def run_quantity_planner(
    stage_chain: List[Dict[str, Any]],
    metrics: Optional[Any],
    qgraph: Optional[Any],
    order_qty: float,
) -> Dict[str, Any]:
    """Compute per-stage quantities for Phase 4 in shadow mode.

    This planner is intentionally conservative and uses only metrics and
    quantity-stage-graph information already produced by Engine V2. It
    introduces no new waste/scrap assumptions and never mutates engine
    state. The result is a diagnostic quantity_report structure.
    """

    # Normalise order quantity as finished-goods demand.
    fg_raw = _safe_float(order_qty, 0.0)
    if fg_raw < 0.0:
        fg_raw = 0.0
    fg_qty = float(fg_raw)

    extra = getattr(metrics, "extra", {}) if metrics is not None else {}
    yield_metadata = _style_yield_metadata(metrics)
    is_diecut_sheet_yield = yield_metadata.get("yield_basis") == "fg_per_sheet"
    fg_per_sheet = _safe_int(yield_metadata.get("fg_per_sheet") or yield_metadata.get("diecut_ups"), 0)

    boxes_per_sheet = _safe_float(extra.get("boxes_per_sheet"), 0.0)
    layout_blanks_per_sheet = _safe_int(extra.get("layout_blanks_per_sheet", 0), 0)
    m2_per_box = _safe_float(getattr(metrics, "m2_per_box", 0.0) if metrics is not None else 0.0, 0.0)

    warnings: List[str] = []

    if boxes_per_sheet <= 0.0:
        warnings.append("quantity_planner:boxes_per_sheet_missing_or_zero")
    if layout_blanks_per_sheet <= 0:
        warnings.append("quantity_planner:layout_blanks_per_sheet_missing_or_zero")
    if m2_per_box <= 0.0:
        warnings.append("quantity_planner:m2_per_box_missing_or_zero")

    # Resolve required sheets from quantity StageGraph when available; fall
    # back to the same calculation used by build_stage_graph.
    sheets_required = 0
    if qgraph is not None:
        try:
            nodes = getattr(qgraph, "nodes", {}) or {}
            sheet_node = nodes.get("sheet")
            if sheet_node is not None:
                sheets_required = _safe_int(getattr(sheet_node, "qty", 0), 0)
        except Exception:
            sheets_required = 0

    if is_diecut_sheet_yield and fg_per_sheet > 0 and fg_qty > 0:
        sheets_required = int(math.ceil(float(fg_qty) / float(fg_per_sheet)))
    elif sheets_required <= 0 and boxes_per_sheet > 0.0 and fg_qty > 0:
        sheets_required = int(math.ceil(float(fg_qty) / boxes_per_sheet))

    slit_sheets_produced = 0.0
    if qgraph is not None:
        try:
            nodes = getattr(qgraph, "nodes", {}) or {}
            blanks_node = nodes.get("blanks")
            if blanks_node is not None:
                slit_sheets_produced = _safe_float(getattr(blanks_node, "qty", 0), 0.0)
        except Exception:
            slit_sheets_produced = 0.0
    if slit_sheets_produced <= 0.0 and sheets_required > 0 and layout_blanks_per_sheet > 0:
        slit_sheets_produced = float(sheets_required * layout_blanks_per_sheet)
    if slit_sheets_produced <= 0.0 and fg_qty > 0:
        slit_sheets_produced = float(fg_qty)

    # Area per sheet follows the existing StageGraph logic:
    #   area_per_sheet = m2_per_box * boxes_per_sheet
    if m2_per_box > 0.0 and boxes_per_sheet > 0.0:
        sheet_area_m2 = m2_per_box * boxes_per_sheet
    else:
        sheet_area_m2 = 0.0

    stages_out: List[Dict[str, Any]] = []
    prev_output_qty: Optional[float] = None
    layout_split_seen = False

    for desc in stage_chain:
        code = (str(desc.get("stage_code") or "")).strip()
        stage_type = (str(desc.get("stage_type") or "")).strip()
        exec_type = (str(desc.get("execution_type") or "")).strip()
        qp_code = (str(desc.get("quantity_profile_code") or "")).strip()
        output_kind = (str(desc.get("output_kind") or "")).strip() or None
        output_stock = (str(desc.get("output_stock_behavior") or "")).strip() or None
        can_be_terminal = bool(desc.get("can_be_terminal"))
        consumes_prev = bool(desc.get("consumes_previous_output"))

        fg_demand_qty = float(fg_qty)

        target_qty = 0.0
        target_uom: Optional[str] = None
        metrics_block: Dict[str, Any] = {}
        tags: List[str] = []
        effective_kind = output_kind

        diecut_sheet_quantity = None
        if is_diecut_sheet_yield and fg_per_sheet > 0:
            diecut_sheet_quantity = _diecut_sheet_stage_quantity(
                code=code,
                stage_type=stage_type,
                fg_qty=float(fg_qty),
                required_sheets=int(sheets_required or 0),
                fg_per_sheet=int(fg_per_sheet),
                previous_output_qty=prev_output_qty,
            )

        # Heuristic quantity profiles. These are conservative and simply
        # mirror the quantity StageGraph sheet/FG quantities.

        if diecut_sheet_quantity:
            target_qty = float(diecut_sheet_quantity.get("target_qty") or 0.0)
            target_uom = diecut_sheet_quantity.get("target_uom") or None
            effective_kind = diecut_sheet_quantity.get("effective_kind") or effective_kind
            metrics_block.update(dict(diecut_sheet_quantity.get("metrics") or {}))
            tags.extend(list(diecut_sheet_quantity.get("tags") or []))

        # Explicit FG / terminal stage: per finished unit.
        elif qp_code == "per_fg_unit" and can_be_terminal and output_stock == "fg":
            target_qty = float(fg_qty)
            target_uom = "unit"
            if not effective_kind:
                effective_kind = "box"

        # Sheet/board chain for 2ply -> board -> ts_print.
        elif code in ("2ply", "board", "ts_print"):
            # A selected slitting layout changes the meaning of stage output:
            # the layout-driving stage consumes parent sheet runs but produces
            # slit sheets/blanks for every downstream material stage.
            target_qty = float(slit_sheets_produced or sheets_required)
            target_uom = "sheet"
            metrics_block["parent_sheets_required"] = float(sheets_required)
            metrics_block["slit_sheets_produced"] = float(slit_sheets_produced)
            if not layout_split_seen and layout_blanks_per_sheet > 1:
                tags.append("layout_driver")
                layout_split_seen = True
            if code == "2ply":
                # Clarification: treat 2ply output explicitly as board.
                effective_kind = "board"
                if sheet_area_m2 > 0.0 and sheets_required > 0:
                    metrics_block["board_area_total_m2"] = sheet_area_m2 * float(sheets_required)
            elif code == "board":
                if not effective_kind:
                    effective_kind = "board"
                metrics_block["required_sheets"] = float(sheets_required)
            elif code == "ts_print":
                if not effective_kind:
                    effective_kind = "sheet"
                tags.append("printed")
                if sheet_area_m2 > 0.0 and sheets_required > 0:
                    metrics_block["printed_area_total_m2"] = sheet_area_m2 * float(sheets_required)

        # Generic per-FG profile (non-terminal stages).
        elif qp_code == "per_fg_unit":
            target_qty = float(fg_qty)
            target_uom = "unit"

        # Generic sheet-based board profile.
        elif qp_code == "sheet_based_board":
            target_qty = float(slit_sheets_produced or sheets_required)
            target_uom = "sheet"
            metrics_block["parent_sheets_required"] = float(sheets_required)
            metrics_block["slit_sheets_produced"] = float(slit_sheets_produced)
            if not layout_split_seen and layout_blanks_per_sheet > 1:
                tags.append("layout_driver")
                layout_split_seen = True

        # Area-based printing profile.
        elif qp_code == "area_based_printing":
            target_qty = float(slit_sheets_produced or sheets_required)
            target_uom = "sheet"
            metrics_block["parent_sheets_required"] = float(sheets_required)
            metrics_block["slit_sheets_produced"] = float(slit_sheets_produced)
            if sheet_area_m2 > 0.0 and sheets_required > 0:
                metrics_block["printed_area_total_m2"] = sheet_area_m2 * float(sheets_required)

        input_from_prev = 0.0
        if diecut_sheet_quantity and "input_from_prev" in diecut_sheet_quantity:
            input_from_prev = float(diecut_sheet_quantity.get("input_from_prev") or 0.0)
        elif consumes_prev and prev_output_qty is not None:
            input_from_prev = float(prev_output_qty)

        output_block: Optional[Dict[str, Any]] = None
        if target_qty > 0.0 and target_uom:
            output_block = {
                "kind": effective_kind or "",
                "quantity": float(target_qty),
                "uom": target_uom,
                "fg_equivalent_qty": float(fg_qty),
                "dimensions": {},
                "tags": tags,
            }
            prev_output_qty = target_qty

        stages_out.append(
            {
                "index": desc.get("index"),
                "stage_code": code,
                "fg_demand_qty": fg_demand_qty,
                "target_output_qty": float(target_qty),
                "target_output_uom": target_uom,
                "input_qty_from_prev": float(input_from_prev),
                # RM quantities remain optional; kept empty for now and can be
                # populated in later phases for parity diagnostics.
                "input_qty_from_rm": {},
                "computed_metrics": metrics_block,
                "output": output_block,
            }
        )

    return {
        "order_qty": float(fg_qty),
        "parent_sheets_required": float(sheets_required),
        "slit_sheets_produced": float(slit_sheets_produced),
        "stages": stages_out,
        "warnings": warnings,
    }




