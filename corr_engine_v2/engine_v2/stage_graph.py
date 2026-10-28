from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class StageNode:
    """Single node in the quantity StageGraph.

    Attributes:
        name: Logical stage name (e.g. "raw_material", "sheet").
        qty: Quantity at this stage for the given order.
        inputs: Names of upstream stages feeding this node.
        outputs: Names of downstream stages fed by this node.
        meta: Optional metadata (e.g. RM breakdown).
    """

    name: str
    qty: float = 0.0
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StageEdge:
    """Directed edge between two stage nodes.

    Attributes:
        from_node: Name of the source node.
        to_node: Name of the destination node.
        qty_ratio: Output quantity per unit of input on this edge.
        waste_percent: Optional waste percentage applied on this edge.
    """

    from_node: str
    to_node: str
    qty_ratio: float = 1.0
    waste_percent: float = 0.0


@dataclass
class StageGraph:
    """Quantity StageGraph for RM â†’ board â†’ sheet â†’ blanks â†’ FG.

    This graph is concerned purely with *order-level quantities* and
    layout interactions. It does not model routing or material SFG
    semantics; those live in the material StageGraph defined in
    :mod:`engine_v2.__init__`.

    Nodes represent logical quantity checkpoints (raw material, board,
    sheet, blanks, finished_goods) and edges carry transformation
    ratios and waste percentages.
    """

    nodes: Dict[str, StageNode] = field(default_factory=dict)
    edges: List[StageEdge] = field(default_factory=list)

    def add_node(self, name: str, qty: float = 0.0) -> StageNode:
        """Create or update a node by name."""

        node = self.nodes.get(name)
        if node is None:
            node = StageNode(name=name, qty=qty)
            self.nodes[name] = node
        else:
            node.qty = qty
        return node

    def connect(
        self,
        from_node: StageNode,
        to_node: StageNode,
        qty_ratio: float = 1.0,
        waste_percent: float = 0.0,
    ) -> StageEdge:
        """Connect two nodes and record the transformation ratio."""

        if from_node is None or to_node is None:
            return StageEdge(from_node="", to_node="", qty_ratio=1.0, waste_percent=0.0)

        if from_node.name not in self.nodes:
            self.nodes[from_node.name] = from_node
        if to_node.name not in self.nodes:
            self.nodes[to_node.name] = to_node

        if to_node.name not in from_node.outputs:
            from_node.outputs.append(to_node.name)
        if from_node.name not in to_node.inputs:
            to_node.inputs.append(from_node.name)

        edge = StageEdge(
            from_node=from_node.name,
            to_node=to_node.name,
            qty_ratio=float(qty_ratio or 0.0) if qty_ratio is not None else 0.0,
            waste_percent=float(waste_percent or 0.0) if waste_percent is not None else 0.0,
        )
        self.edges.append(edge)
        return edge


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _stage_text(stage: Any, attr: str) -> str:
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


def _is_route_driven_material_graph(stage_graph: Optional[Any]) -> bool:
    stages = list(getattr(stage_graph, "stages", []) or []) if stage_graph is not None else []
    if not stages:
        return False

    legacy_material_keys = {"2ply", "board", "fg"}
    for stage in stages:
        if _stage_text(stage, "stage_type") == "operation":
            return True
        if _stage_text(stage, "output_kind"):
            return True
        key = _stage_text(stage, "key")
        if key and key not in legacy_material_keys:
            return True
    return False


def _quantity_node_uom(node_name: str) -> str:
    key = str(node_name or "").strip().lower()
    return {
        "raw_material": "kg",
        "board": "sheet",
        "sheet": "sheet",
        "blanks": "unit",
        "finished_goods": "unit",
    }.get(key, "")


def _classify_route_stage(stage: Any) -> Tuple[str, str]:
    role = _stage_text(stage, "logical_role")
    key = _stage_text(stage, "key")
    output_kind = _stage_text(stage, "output_kind")
    ops = _stage_operation_codes(stage)

    if output_kind == "board" or role == "corrugation" or key in ("2ply", "3ply", "singleface"):
        return "board", "corrugator"
    if (
        output_kind == "sheet"
        or role in ("printing", "lamination")
        or key in ("ts_print", "printing", "lamination")
        or any(op in ("ts_print", "printing", "lamination") for op in ops)
    ):
        return "sheet", "printing"
    if (
        output_kind == "blank"
        or role == "diecut"
        or key in ("diecut", "peeloff", "peel-off", "slot/die")
        or any(op in ("diecut", "peeloff", "peel-off", "slot/die") for op in ops)
    ):
        return "blanks", "diecut"
    if (
        output_kind == "box"
        or key in ("seaming", "conversion", "final")
        or (role == "conversion" and output_kind != "board")
        or any(op in ("conversion", "seaming") for op in ops)
    ):
        return "finished_goods", "conversion"
    if output_kind == "fg" or role == "fg":
        return "finished_goods", "fg"
    return "", ""


def _attach_route_stage_quantities(graph: StageGraph, engine_result: Any) -> None:
    material_graph = getattr(engine_result, "stage_graph", None)
    if not _is_route_driven_material_graph(material_graph):
        return

    stages = list(getattr(material_graph, "stages", []) or [])
    if not stages:
        return

    nodes = getattr(graph, "nodes", {}) or {}
    raw_node = nodes.get("raw_material")
    raw_qty = _safe_float(getattr(raw_node, "qty", 0.0), 0.0)
    prev_output_qty: Optional[float] = None
    prev_output_uom = ""
    route_sequence: List[str] = []

    for stage in stages:
        bucket_node_name, operation_key = _classify_route_stage(stage)
        if not bucket_node_name:
            continue
        bucket_node = nodes.get(bucket_node_name)
        if bucket_node is None:
            continue

        stage_code = _stage_text(stage, "key")
        if not stage_code:
            continue

        output_qty = _safe_float(getattr(bucket_node, "qty", 0.0), 0.0)
        output_uom = _quantity_node_uom(bucket_node_name)

        if prev_output_qty is None:
            if operation_key == "corrugator":
                input_qty = raw_qty
                input_uom = _quantity_node_uom("raw_material")
            else:
                input_qty = output_qty
                input_uom = output_uom
        else:
            input_qty = prev_output_qty
            input_uom = prev_output_uom

        entry = {
            "stage_code": stage_code,
            "stage_type": _stage_text(stage, "stage_type"),
            "logical_role": _stage_text(stage, "logical_role"),
            "output_kind": _stage_text(stage, "output_kind"),
            "bucket_node": bucket_node_name,
            "operation_key": operation_key,
            "input_qty": input_qty,
            "input_uom": input_uom,
            "output_qty": output_qty,
            "output_uom": output_uom,
            "source": "route_stage_propagation",
        }
        bucket_node.meta.setdefault("route_stage_quantities", []).append(entry)
        route_sequence.append(stage_code)
        prev_output_qty = output_qty
        prev_output_uom = output_uom

    if raw_node is not None and route_sequence:
        raw_node.meta["route_stage_sequence"] = route_sequence


def build_stage_graph(engine_result: Any, order_qty: float) -> StageGraph:
    """Build a quantity StageGraph for a given order quantity.

    Fixed flow:
        raw_material -> board -> sheet -> blanks -> finished_goods

    Quantities:
        boxes_per_sheet          = engine_result.metrics.extra["boxes_per_sheet"]
        layout_blanks_per_sheet  = engine_result.metrics.extra["layout_blanks_per_sheet"]
        m2_per_box               = engine_result.metrics.m2_per_box

        sheets_required = ceil(order_qty / boxes_per_sheet)          (when boxes_per_sheet > 0)
        blanks_required = sheets_required * layout_blanks_per_sheet

        FG qty     = order_qty
        sheet qty  = sheets_required
        blanks qty = blanks_required
        board qty  = sheets_required

    Raw-material quantity uses area-based consumption:

        area_per_sheet = m2_per_box * boxes_per_sheet

        For each RM layer (derived from rm_requirements):
            rm_qty_layer = gsm * area_per_sheet * sheets_required / 1000
        raw_material qty = sum(rm_qty_layer) over all layers
    """

    metrics = getattr(engine_result, "metrics", None)
    extra = getattr(metrics, "extra", {}) if metrics is not None else {}

    boxes_per_sheet = _safe_float(extra.get("boxes_per_sheet"), default=0.0)
    layout_blanks_per_sheet = _safe_float(extra.get("layout_blanks_per_sheet"), default=0.0)
    m2_per_box = _safe_float(getattr(metrics, "m2_per_box", 0.0) if metrics is not None else 0.0, 0.0)

    # Finished goods are discrete boxes; normalise to a non-negative integer.
    fg_raw = _safe_float(order_qty, default=0.0)
    if fg_raw < 0.0:
        fg_raw = 0.0
    fg_qty = int(round(fg_raw))

    if boxes_per_sheet > 0.0 and fg_qty > 0:
        sheets_required = int(math.ceil(fg_qty / boxes_per_sheet))
    else:
        sheets_required = 0

    blanks_per_sheet = int(layout_blanks_per_sheet) if layout_blanks_per_sheet > 0.0 else 0
    if blanks_per_sheet > 0 and sheets_required > 0:
        blanks_required = sheets_required * blanks_per_sheet
    else:
        blanks_required = 0

    board_qty = sheets_required

    # Area-based RM consumption
    if m2_per_box > 0.0 and boxes_per_sheet > 0.0 and sheets_required > 0:
        area_per_sheet = m2_per_box * boxes_per_sheet
    else:
        area_per_sheet = 0.0

    rm_total_kg = 0.0
    rm_by_role: Dict[str, float] = {}
    rm_layers: List[Dict[str, Any]] = []

    rm_reqs = getattr(engine_result, "rm_requirements", []) or []

    if area_per_sheet > 0.0 and sheets_required > 0 and m2_per_box > 0.0:
        for rm in rm_reqs:
            qty_per_fg = _safe_float(getattr(rm, "qty_per_fg", 0.0), default=0.0)
            if qty_per_fg <= 0.0:
                continue

            # Only area-based RMs participate in the area-per-sheet
            # aggregation here; unit/length-based consumptions are
            # handled elsewhere (e.g. BOM adapter or node metadata).
            ctype = (getattr(rm, "consumption_type", "area") or "area").lower()
            if ctype != "area":
                continue

            # Derive GSM from per-box consumption to stay aligned with rm_rules.
            gsm = (qty_per_fg * 1000.0) / m2_per_box
            rm_qty = (gsm * area_per_sheet * sheets_required) / 1000.0

            role = getattr(rm, "role", "") or ""
            product_key = getattr(rm, "product_key", "") or ""
            layer_no = getattr(rm, "layer_no", None)

            rm_total_kg += rm_qty
            if role:
                rm_by_role[role] = rm_by_role.get(role, 0.0) + rm_qty
            rm_layers.append(
                {
                    "role": role,
                    "product_key": product_key,
                    "layer_no": layer_no,
                    "gsm": gsm,
                    "qty_kg": rm_qty,
                }
            )

    raw_material_qty = rm_total_kg

    graph = StageGraph()

    # Create nodes with computed quantities.
    raw_node = graph.add_node("raw_material", qty=float(raw_material_qty))
    board_node = graph.add_node("board", qty=int(board_qty))
    sheet_node = graph.add_node("sheet", qty=int(sheets_required))
    blanks_node = graph.add_node("blanks", qty=int(blanks_required))
    fg_node = graph.add_node("finished_goods", qty=int(fg_qty))

    # Enrich raw_material node for future RM expansion.
    raw_node.meta["area_per_sheet_m2"] = area_per_sheet
    raw_node.meta["sheets_required"] = sheets_required
    raw_node.meta["rm_total_kg"] = rm_total_kg
    raw_node.meta["rm_by_role"] = rm_by_role
    raw_node.meta["rm_layers"] = rm_layers

    # Attach unit- and length-based RM requirements to relevant nodes
    # for visibility, without mixing them into area calculations.
    if rm_reqs:
        unit_list = raw_node.meta.setdefault("rm_unit", [])
        length_targets = {
            "sheet": sheet_node,
            "blanks": blanks_node,
            "board": board_node,
        }
        for rm in rm_reqs:
            ctype = (getattr(rm, "consumption_type", "area") or "area").lower()
            if ctype not in ("unit", "length"):
                continue

            info = {
                "product_key": getattr(rm, "product_key", "") or "",
                "role": getattr(rm, "role", "") or "",
                "qty_per_fg": _safe_float(getattr(rm, "qty_per_fg", 0.0), 0.0),
                "qty_uom": getattr(rm, "qty_uom", "") or "",
                "consumption_type": ctype,
                "metadata": getattr(rm, "metadata", {}) or {},
            }

            if ctype == "unit":
                # Unit-based consumptions conceptually attach to the
                # finished goods stage.
                fg_unit_list = fg_node.meta.setdefault("rm_unit", [])
                fg_unit_list.append(info)
            elif ctype == "length":
                # Length-based consumptions are attached to the most
                # relevant stage based on rm.stage_key.
                stage_key = (getattr(rm, "stage_key", None) or "").strip().lower()
                if stage_key == "ts_print":
                    target_node = length_targets.get("sheet")
                elif stage_key == "diecut":
                    target_node = length_targets.get("blanks")
                else:
                    target_node = length_targets.get("board")
                if target_node is not None:
                    lst = target_node.meta.setdefault("rm_length", [])
                    lst.append(info)

    # Wire the fixed flow with transformation ratios.
    # raw_material -> board: represents RM feeding board; ratio kept at 1 for now.
    graph.connect(raw_node, board_node, qty_ratio=1.0, waste_percent=0.0)
    # board -> sheet: simple 1:1 materialisation of sheets from board.
    graph.connect(board_node, sheet_node, qty_ratio=1.0, waste_percent=0.0)
    # sheet -> blanks: ratio driven by layout blanks per sheet.
    graph.connect(sheet_node, blanks_node, qty_ratio=float(blanks_per_sheet or 0), waste_percent=0.0)
    # blanks -> finished_goods: ratio expresses finished goods per blank.
    if blanks_required > 0:
        fg_ratio = float(fg_qty) / float(blanks_required)
    else:
        fg_ratio = 0.0
    graph.connect(blanks_node, fg_node, qty_ratio=fg_ratio, waste_percent=0.0)

    _attach_route_stage_quantities(graph, engine_result)

    return graph



