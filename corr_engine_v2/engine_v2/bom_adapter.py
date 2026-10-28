from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import math

from .stage_graph import StageEdge, StageGraph, build_stage_graph


@dataclass
class BomComponent:
    """Lightweight BOM component line for Engine V2."""

    product: str
    qty: float
    uom: str
    stage: str


@dataclass
class BomOperation:
    """Lightweight operation derived from the quantity StageGraph."""

    stage: str
    input_qty: float
    output_qty: float


def _node_stage_label(node_name: str) -> str:
    """Map StageGraph node names to high-level process stage labels."""

    key = (node_name or "").strip().lower()
    if key in ("raw_material", "board"):
        return "corrugator"
    if key == "sheet":
        return "printing"
    if key == "blanks":
        return "diecut"
    return node_name or ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def _style_yield_metadata(engine_result: Any) -> Dict[str, Any]:
    try:
        extra = getattr(getattr(engine_result, "metrics", None), "extra", {}) or {}
        style = dict(extra.get("style") or {})
        return dict(style.get("metadata") or {})
    except Exception:
        return {}


def _apply_fg_per_sheet_operation_yield(
    operations: List[Dict[str, Any]],
    engine_result: Any,
    order_qty: float,
) -> List[Dict[str, Any]]:
    metadata = _style_yield_metadata(engine_result)
    if metadata.get("yield_basis") != "fg_per_sheet":
        return operations
    fg_per_sheet = _safe_int(metadata.get("fg_per_sheet") or metadata.get("diecut_ups"), 0)
    if fg_per_sheet <= 0:
        return operations

    order_qty_f = float(order_qty or 0.0)
    if order_qty_f <= 1.0:
        upstream_qty = 1.0
        yield_output_qty = float(fg_per_sheet)
        downstream_qty = float(fg_per_sheet)
        quantity_basis = "whole_sheet_yield_relation"
    else:
        upstream_qty = float(math.ceil(order_qty_f / float(fg_per_sheet)))
        yield_output_qty = order_qty_f
        downstream_qty = order_qty_f
        quantity_basis = "order_required_sheets"

    yield_stage_key = str(metadata.get("yield_stage_key") or "").strip().lower()
    yield_stage_kind = str(metadata.get("yield_stage_kind") or "").strip().lower()
    downstream_stage_key = str(metadata.get("yield_downstream_stage_key") or "").strip().lower()
    downstream_output_label = metadata.get("yield_downstream_output_label")
    yield_index = None
    downstream_index = None
    for index, operation in enumerate(operations or []):
        stage = str(operation.get("stage") or "").strip().lower()
        stage_code = str(operation.get("stage_code") or "").strip().lower()
        if downstream_stage_key and downstream_stage_key in {stage, stage_code}:
            downstream_index = index
        if yield_stage_key and yield_stage_key in {stage, stage_code}:
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
        else:
            row["input_qty"] = downstream_qty
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


def _resolve_rm_node(qgraph: StageGraph, stage_key: Optional[str], rm: Optional[Any] = None) -> str:
    """Resolve the StageGraph node a raw material feeds into.

    When an ``rm`` object is provided, its role is used to refine the
    mapping so that surface consumables (top sheet, ink, lamination)
    are grouped under the printing node while keeping material-stage
    stage_keys for RM breakdown.
    """

    key = (stage_key or "").strip().lower()
    role = ""
    if rm is not None:
        try:
            if isinstance(rm, dict):
                role = (rm.get("engine_role") or rm.get("role") or "").strip().lower()
            else:
                role = (getattr(rm, "role", "") or "").strip().lower()
        except Exception:
            role = ""

    target: str = ""

    # Role-aware overrides for common consumables.
    if role in ("top_roll", "top_sheet_external", "ink"):
        # Surface / printing consumables conceptually feed the sheet
        # node when it exists.
        if "sheet" in qgraph.nodes:
            target = "sheet"
    elif role.startswith("adhesive"):
        # Corrugation/board adhesive -> board node, FG adhesive -> FG.
        if role in ("adhesive_fg", "fg_adhesive") or key in ("fg", "finished_goods"):
            if "finished_goods" in qgraph.nodes:
                target = "finished_goods"
        else:
            if "board" in qgraph.nodes:
                target = "board"
    elif role == "lamination":
        # Lamination film is treated as a sheet-level consumable.
        if "sheet" in qgraph.nodes:
            target = "sheet"
    elif role == "wire":
        # Wire is attached to the finished-goods node when present.
        if "finished_goods" in qgraph.nodes:
            target = "finished_goods"

    # If role-based mapping did not choose a target, fall back to
    # stage_key-based semantics for backward compatibility.
    if not target:
        # Corrugation RMs (liner/medium) -> board node.
        if key in ("2ply", "board"):
            target = "board"
        # Top-sheet printing and related pre-board surface steps -> sheet.
        elif key in ("ts_print", "ts_cut", "lamination"):
            target = "sheet"
        # Diecut / peel-off RMs -> blanks node.
        elif key in ("diecut", "peeloff"):
            target = "blanks"
        # Finished-good / seaming consumables -> finished_goods node.
        elif key in ("fg", "finished_goods", "seaming", "final"):
            target = "finished_goods"
        else:
            # Fallback to board when present, otherwise first available node.
            if "board" in qgraph.nodes:
                target = "board"
            elif qgraph.nodes:
                target = sorted(qgraph.nodes.keys())[0]
            else:
                target = ""

    if target and target not in qgraph.nodes and qgraph.nodes:
        target = sorted(qgraph.nodes.keys())[0]

    return target


def _build_components(engine_result: Any, order_qty: float, qgraph: StageGraph) -> List[Dict[str, Any]]:
    """Build BOM component lines from rm_requirements."""

    rm_reqs = getattr(engine_result, "rm_requirements", []) or []
    try:
        order_qty_f = float(order_qty or 0.0)
    except Exception:
        order_qty_f = 0.0
    if order_qty_f < 0.0:
        order_qty_f = 0.0

    components: List[Dict[str, Any]] = []
    for rm in rm_reqs:
        product_key = getattr(rm, "product_key", "") or ""
        qty_per_fg = getattr(rm, "qty_per_fg", 0.0)
        uom = getattr(rm, "qty_uom", "") or ""
        consumption_type = (getattr(rm, "consumption_type", "area") or "area").lower()
        metadata = getattr(rm, "metadata", {}) or {}
        # Keep consumption_type available inside metadata for debugging
        # and downstream analysis without affecting display units.
        if "consumption_type" not in metadata:
            metadata = dict(metadata)
            metadata["consumption_type"] = consumption_type

        try:
            qty_per_fg_f = float(qty_per_fg or 0.0)
        except Exception:
            qty_per_fg_f = 0.0
        if qty_per_fg_f <= 0.0 or order_qty_f <= 0.0:
            continue

        total_qty = qty_per_fg_f * order_qty_f

        # For unit-sheet materials, apply order-level rounding so that
        # preview components reflect an integer sheet requirement.
        role = str(getattr(rm, "role", "") or "").strip().lower()
        order_rounding = str(metadata.get("order_qty_rounding") or "").strip().lower()
        if (
            role == "top_sheet_external"
            or order_rounding == "ceil_by_ups"
            or (role == "carton_board" and consumption_type == "unit")
        ) and consumption_type == "unit":
            try:
                boxes_per_sheet = float(
                    getattr(engine_result.metrics, "extra", {}).get("boxes_per_sheet", 0.0) or 0.0
                )
            except Exception:
                boxes_per_sheet = 0.0
            if order_rounding == "ceil_by_ups":
                try:
                    printing_ups_val = int(float(metadata.get("carton_board_ups") or 0))
                except Exception:
                    printing_ups_val = 0
            else:
                try:
                    printing_ups_val = int(
                        getattr(engine_result.metrics, "extra", {}).get("printing_ups", 0) or 0
                    )
                except Exception:
                    printing_ups_val = 0
                if printing_ups_val <= 0:
                    try:
                        printing_ups_val = int(getattr(engine_result.metrics, "printing_ups", 0) or 0)
                    except Exception:
                        printing_ups_val = 0
            if printing_ups_val <= 0:
                printing_ups_val = 1

            if boxes_per_sheet > 0.0 and order_rounding != "ceil_by_ups":
                sheets_total = math.ceil(order_qty_f / boxes_per_sheet)
            else:
                sheets_total = math.ceil(order_qty_f / float(printing_ups_val or 1))
            total_qty = float(sheets_total)

        node_name = _resolve_rm_node(qgraph, getattr(rm, "stage_key", None), rm=rm)
        stage = _node_stage_label(node_name)

        components.append(
            {
                "product": product_key,
                "qty": total_qty,
                "uom": uom,
                "stage": stage,
                "metadata": metadata,
            }
        )

    return components


def _stage_attr_text(stage: Any, attr: str) -> str:
    try:
        return str(getattr(stage, attr, "") or "").strip().lower()
    except Exception:
        return ""


def _stage_operation_codes(stage: Any) -> List[str]:
    try:
        raw_ops = list(getattr(stage, "operations", []) or [])
    except Exception:
        raw_ops = []
    return [str(item or "").strip().lower() for item in raw_ops if str(item or "").strip()]


def _is_route_driven_stage_graph(stage_graph: Optional[Any]) -> bool:
    stages = list(getattr(stage_graph, "stages", []) or []) if stage_graph is not None else []
    if not stages:
        return False

    legacy_material_keys = {"2ply", "board", "fg"}
    for stage in stages:
        if _stage_attr_text(stage, "stage_type") == "operation":
            return True
        if _stage_attr_text(stage, "output_kind"):
            return True
        key = _stage_attr_text(stage, "key")
        if key and key not in legacy_material_keys:
            return True
    return False


def _stage_has_corrugation_signal(stage: Any) -> bool:
    role = _stage_attr_text(stage, "logical_role")
    key = _stage_attr_text(stage, "key")
    output_kind = _stage_attr_text(stage, "output_kind")
    return role == "corrugation" or key in ("2ply", "3ply", "singleface") or output_kind == "board"


def _stage_has_printing_signal(stage: Any) -> bool:
    role = _stage_attr_text(stage, "logical_role")
    key = _stage_attr_text(stage, "key")
    output_kind = _stage_attr_text(stage, "output_kind")
    ops = _stage_operation_codes(stage)
    if role in ("printing", "lamination"):
        return True
    if key in ("ts_print", "printing", "lamination"):
        return True
    if output_kind == "sheet":
        return True
    return any(op in ("ts_print", "printing", "lamination") for op in ops)


def _stage_has_diecut_signal(stage: Any) -> bool:
    role = _stage_attr_text(stage, "logical_role")
    key = _stage_attr_text(stage, "key")
    output_kind = _stage_attr_text(stage, "output_kind")
    ops = _stage_operation_codes(stage)
    if role == "diecut":
        return True
    if key in ("diecut", "peeloff", "peel-off", "slot/die"):
        return True
    if output_kind == "blank":
        return True
    return any(op in ("diecut", "peeloff", "peel-off", "slot/die") for op in ops)


def _stage_has_conversion_signal(stage: Any) -> bool:
    role = _stage_attr_text(stage, "logical_role")
    key = _stage_attr_text(stage, "key")
    output_kind = _stage_attr_text(stage, "output_kind")
    is_terminal = bool(getattr(stage, "is_terminal", False))
    ops = _stage_operation_codes(stage)
    if output_kind == "box":
        return True
    if role == "conversion" and (is_terminal or key in ("seaming", "conversion", "final")):
        return True
    if key in ("seaming", "conversion", "final"):
        return True
    return any(op in ("conversion", "seaming") for op in ops)


def _build_operation_from_edge(
    qgraph: StageGraph,
    stage_key: str,
    src: str,
    dst: str,
    *,
    output_from_node: bool = False,
) -> Optional[Dict[str, Any]]:
    nodes = getattr(qgraph, "nodes", {}) or {}
    edges = list(getattr(qgraph, "edges", []) or [])

    edge: Optional[StageEdge] = None
    for item in edges:
        if item.from_node == src and item.to_node == dst:
            edge = item
            break
    if edge is None or src not in nodes or dst not in nodes:
        return None

    input_qty = float(getattr(nodes[src], "qty", 0.0) or 0.0)
    if output_from_node:
        output_qty = float(getattr(nodes[dst], "qty", 0.0) or 0.0)
    else:
        ratio = float(getattr(edge, "qty_ratio", 0.0) or 0.0)
        waste = float(getattr(edge, "waste_percent", 0.0) or 0.0)
        output_qty = input_qty * ratio * (1.0 - waste)
    if input_qty <= 0.0 or output_qty <= 0.0:
        return None
    return {
        "stage": stage_key,
        "input_qty": input_qty,
        "output_qty": output_qty,
    }


def _build_operations_from_quantity_graph(qgraph: StageGraph) -> List[Dict[str, Any]]:
    """Build high-level operations from the quantity StageGraph.

    Current mapping:
      - raw_material -> board : corrugator
      - sheet -> blanks       : printing
      - blanks -> finished_goods : diecut
    """

    nodes = qgraph.nodes
    edges = qgraph.edges

    def _find_edge(src: str, dst: str) -> Optional[StageEdge]:
        for e in edges:
            if e.from_node == src and e.to_node == dst:
                return e
        return None

    ops: List[Dict[str, Any]] = []

    # Corrugator: raw_material -> board
    if "raw_material" in nodes and "board" in nodes:
        e = _find_edge("raw_material", "board")
        if e:
            input_qty = float(nodes["raw_material"].qty or 0.0)
            # For corrugation, we currently keep a 1:1 view between
            # RM input (kg) and board output (sheets) at the operation
            # level without applying qty_ratio as a unit converter.
            output_qty = float(nodes["board"].qty or 0.0)
            ops.append(
                {
                    "stage": "corrugator",
                    "input_qty": input_qty,
                    "output_qty": output_qty,
                }
            )

    # Printing: sheet -> blanks
    if "sheet" in nodes and "blanks" in nodes:
        e = _find_edge("sheet", "blanks")
        if e:
            input_qty = float(nodes["sheet"].qty or 0.0)
            ratio = float(e.qty_ratio or 0.0)
            waste = float(e.waste_percent or 0.0)
            effective_output = input_qty * ratio * (1.0 - waste)
            ops.append(
                {
                    "stage": "printing",
                    "input_qty": input_qty,
                    "output_qty": effective_output,
                }
            )

    # Diecut: blanks -> finished_goods
    if "blanks" in nodes and "finished_goods" in nodes:
        e = _find_edge("blanks", "finished_goods")
        if e:
            input_qty = float(nodes["blanks"].qty or 0.0)
            ratio = float(e.qty_ratio or 0.0)
            waste = float(e.waste_percent or 0.0)
            effective_output = input_qty * ratio * (1.0 - waste)
            ops.append(
                {
                    "stage": "diecut",
                    "input_qty": input_qty,
                    "output_qty": effective_output,
                }
            )

    return ops


def _build_operations_from_route_and_quantity_graph(
    engine_result: Any, qgraph: StageGraph
) -> List[Dict[str, Any]]:
    stage_graph = getattr(engine_result, "stage_graph", None)
    stages = list(getattr(stage_graph, "stages", []) or []) if stage_graph is not None else []
    if not stages:
        return _build_operations_from_quantity_graph(qgraph)

    ops: List[Dict[str, Any]] = []
    if any(_stage_has_corrugation_signal(stage) for stage in stages):
        corr = _build_operation_from_edge(
            qgraph, "corrugator", "raw_material", "board", output_from_node=True
        )
        if corr:
            ops.append(corr)

    if any(_stage_has_printing_signal(stage) for stage in stages):
        printing = _build_operation_from_edge(qgraph, "printing", "sheet", "blanks")
        if printing:
            ops.append(printing)

    terminal_stage_key = ""
    for stage in stages:
        if _stage_has_conversion_signal(stage):
            terminal_stage_key = "conversion"
        elif _stage_has_diecut_signal(stage) and terminal_stage_key != "conversion":
            terminal_stage_key = "diecut"

    if terminal_stage_key:
        terminal = _build_operation_from_edge(
            qgraph, terminal_stage_key, "blanks", "finished_goods"
        )
        if terminal:
            ops.append(terminal)

    return ops


def build_bom_structure(engine_result: Any, order_qty: float) -> Dict[str, Any]:
    """Render a pure-Python BOM-like structure from an EngineResult.

    The structure matches:
        {
          "product_qty": order_qty,
          "components": [...],
          "operations": [...],
        }
    """

    try:
        order_qty_f = float(order_qty or 0.0)
    except Exception:
        order_qty_f = 0.0
    if order_qty_f < 0.0:
        order_qty_f = 0.0

    # Prefer an attached quantity_stage_graph when it matches the order
    # quantity; otherwise build a fresh one.
    qgraph: Optional[StageGraph]
    try:
        existing = getattr(engine_result, "quantity_stage_graph", None)
    except Exception:
        existing = None
    if isinstance(existing, StageGraph) and "finished_goods" in existing.nodes:
        try:
            fg_q = float(existing.nodes["finished_goods"].qty or 0.0)
        except Exception:
            fg_q = -1.0
        if abs(fg_q - order_qty_f) < 1e-6:
            qgraph = existing
        else:
            qgraph = build_stage_graph(engine_result, order_qty_f)
    else:
        qgraph = build_stage_graph(engine_result, order_qty_f)

    components = _build_components(engine_result, order_qty_f, qgraph)
    if _is_route_driven_stage_graph(getattr(engine_result, "stage_graph", None)):
        operations = _build_operations_from_route_and_quantity_graph(engine_result, qgraph)
    else:
        operations = _build_operations_from_quantity_graph(qgraph)
    operations = _apply_fg_per_sheet_operation_yield(
        operations,
        engine_result,
        order_qty_f,
    )

    return {
        "product_qty": order_qty_f,
        "components": components,
        "operations": operations,
    }



