import math
import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.spec_input import RoutingProfileInput, RoutingStageInput
from custom_addons.corr_engine_v2.engine_v2.stage_graph import (
    StageEdge,
    StageGraph,
    StageNode,
    build_stage_graph,
)


class TestEngineV2StageGraphQuantities(unittest.TestCase):
    def _make_engine_result(self, sheet_length_mm: float, sheet_width_mm: float):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="StageGraph Layout",
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT",
            name="Plant",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=True,
            sheet_length_mm=sheet_length_mm,
            sheet_width_mm=sheet_width_mm,
        )

        return compute(spec_input, plant)

    def test_different_layouts_produce_different_sheet_quantities(self):
        order_qty = 100

        # Layout 1: 4 boxes per sheet -> 25 sheets.
        result_4 = self._make_engine_result(sheet_length_mm=1200.0, sheet_width_mm=800.0)
        metrics_4 = result_4.metrics
        self.assertEqual(metrics_4.extra.get("layout_blanks_per_sheet"), 4)
        self.assertEqual(metrics_4.extra.get("boxes_per_sheet"), 4)

        graph_4 = build_stage_graph(result_4, order_qty)
        self.assertIsInstance(graph_4, StageGraph)
        self.assertIn("sheet", graph_4.nodes)
        self.assertEqual(graph_4.nodes["sheet"].qty, 25)
        self.assertIsInstance(graph_4.nodes["sheet"].qty, int)

        # Layout 2: 5 boxes per sheet -> 20 sheets.
        # Choose sheet size: floor(2500/500)=5, floor(400/400)=1 => 5 blanks.
        result_5 = self._make_engine_result(sheet_length_mm=2500.0, sheet_width_mm=400.0)
        metrics_5 = result_5.metrics
        self.assertEqual(metrics_5.extra.get("layout_blanks_per_sheet"), 5)
        self.assertEqual(metrics_5.extra.get("boxes_per_sheet"), 5)

        graph_5 = build_stage_graph(result_5, order_qty)
        self.assertEqual(graph_5.nodes["sheet"].qty, 20)

    def test_blanks_calculation_uses_layout_and_order_qty(self):
        # order_qty = 100, boxes_per_sheet = 4, layout_blanks_per_sheet = 4
        # sheets = 25, blanks = 100.
        order_qty = 100
        result = self._make_engine_result(sheet_length_mm=1200.0, sheet_width_mm=800.0)
        metrics = result.metrics

        self.assertEqual(metrics.extra.get("layout_blanks_per_sheet"), 4)
        self.assertEqual(metrics.extra.get("boxes_per_sheet"), 4)

        graph = build_stage_graph(result, order_qty)
        self.assertEqual(graph.nodes["sheet"].qty, 25)
        self.assertEqual(graph.nodes["blanks"].qty, 100)

        # Quantities for sheet, blanks, board, and FG are integers.
        for key in ("sheet", "blanks", "board", "finished_goods"):
            self.assertIsInstance(graph.nodes[key].qty, int)

    def test_fg_quantity_equals_order_quantity(self):
        order_qty = 123
        result = self._make_engine_result(sheet_length_mm=1200.0, sheet_width_mm=800.0)

        graph = build_stage_graph(result, order_qty)
        self.assertIn("finished_goods", graph.nodes)
        self.assertEqual(graph.nodes["finished_goods"].qty, order_qty)
        self.assertIsInstance(graph.nodes["finished_goods"].qty, int)

    def test_required_sheets_for_two_up_layout(self):
        """Required sheets must be ceil(boxes / boxes_per_sheet)."""

        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=10,
            spec_name="2-up Sheets Required",
        )

        # Plant configured for derived sheet behaviour with 2-up printing.
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="P2UP",
            name="2-up Plant",
            printing_ups=2,
            diecut_ups=2,
            allow_blank_rotation=True,
            default_sheet_behavior="derived",
        )

        result = compute(spec_input, plant)
        extra = result.metrics.extra
        self.assertEqual(extra.get("boxes_per_sheet"), 2)

        # 1000 boxes @ 2-up => 500 sheets.
        graph_1000 = build_stage_graph(result, order_qty=1000)
        self.assertEqual(graph_1000.nodes["sheet"].qty, 500)

        # 1001 boxes @ 2-up => 501 sheets.
        graph_1001 = build_stage_graph(result, order_qty=1001)
        self.assertEqual(graph_1001.nodes["sheet"].qty, 501)

    def test_stage_flow_correctness(self):
        order_qty = 50
        result = self._make_engine_result(sheet_length_mm=1200.0, sheet_width_mm=800.0)

        graph = build_stage_graph(result, order_qty)

        self.assertIn("raw_material", graph.nodes)
        self.assertIn("board", graph.nodes)
        self.assertIn("sheet", graph.nodes)
        self.assertIn("blanks", graph.nodes)
        self.assertIn("finished_goods", graph.nodes)

        self.assertEqual(graph.nodes["raw_material"].outputs, ["board"])
        self.assertEqual(graph.nodes["board"].inputs, ["raw_material"])
        self.assertEqual(graph.nodes["board"].outputs, ["sheet"])
        self.assertEqual(graph.nodes["sheet"].inputs, ["board"])
        self.assertEqual(graph.nodes["sheet"].outputs, ["blanks"])
        self.assertEqual(graph.nodes["blanks"].inputs, ["sheet"])
        self.assertEqual(graph.nodes["blanks"].outputs, ["finished_goods"])
        self.assertEqual(graph.nodes["finished_goods"].inputs, ["blanks"])

    def test_rm_alignment_propagates_from_raw_material(self):
        order_qty = 80
        result = self._make_engine_result(sheet_length_mm=1200.0, sheet_width_mm=800.0)

        graph = build_stage_graph(result, order_qty)

        # RM connects to board and propagates forward along the chain.
        self.assertIn("raw_material", graph.nodes)
        self.assertIn("board", graph.nodes)
        self.assertIn("finished_goods", graph.nodes)

        self.assertIn("board", graph.nodes["raw_material"].outputs)
        # Walk the chain to ensure a path exists from RM to FG.
        current = "raw_material"
        visited = set()
        while current and current not in visited and current != "finished_goods":
            visited.add(current)
            outputs = graph.nodes[current].outputs
            current = outputs[0] if outputs else None

        self.assertEqual(current, "finished_goods")

        # Raw-material quantity should be area-based, not just equal to sheet count.
        m2_per_box = (500.0 * 400.0) / 1_000_000.0
        boxes_per_sheet = result.metrics.extra.get("boxes_per_sheet")
        area_per_sheet = m2_per_box * boxes_per_sheet
        sheets_required = int(math.ceil(order_qty / boxes_per_sheet))
        expected_rm_total = ((180.0 + 120.0) * area_per_sheet * sheets_required) / 1000.0

        self.assertNotEqual(graph.nodes["raw_material"].qty, graph.nodes["sheet"].qty)
        self.assertAlmostEqual(graph.nodes["raw_material"].qty, expected_rm_total, places=6)

    def test_edges_have_expected_ratios_and_default_waste(self):
        order_qty = 100
        result = self._make_engine_result(sheet_length_mm=1200.0, sheet_width_mm=800.0)
        metrics = result.metrics

        blanks_per_sheet = metrics.extra.get("layout_blanks_per_sheet")
        self.assertEqual(blanks_per_sheet, 4)

        graph = build_stage_graph(result, order_qty)

        # We expect four edges in the fixed flow.
        self.assertEqual(len(graph.edges), 4)

        # Find the sheet -> blanks edge and verify its qty_ratio.
        sheet_to_blanks = [
            e for e in graph.edges if e.from_node == "sheet" and e.to_node == "blanks"
        ]
        self.assertEqual(len(sheet_to_blanks), 1)
        edge = sheet_to_blanks[0]
        self.assertIsInstance(edge, StageEdge)
        self.assertEqual(edge.qty_ratio, blanks_per_sheet)

        # All edges should default to zero waste_percent for now.
        for e in graph.edges:
            self.assertEqual(e.waste_percent, 0.0)

    def test_route_stage_quantities_are_attached_to_legacy_nodes(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=20,
            spec_name="Route Quantity Metadata",
            routing_profile=RoutingProfileInput(
                name="2ply-board-tsprint-seaming",
                stages=[
                    RoutingStageInput(
                        sequence=10,
                        stage_code="2ply",
                        stage_type="material",
                        stage_semantic="corrugation",
                        output_kind="board",
                    ),
                    RoutingStageInput(
                        sequence=20,
                        stage_code="board",
                        stage_type="material",
                        stage_semantic="conversion",
                        output_kind="board",
                    ),
                    RoutingStageInput(
                        sequence=30,
                        stage_code="ts_print",
                        stage_type="operation",
                        stage_semantic="printing",
                        output_kind="sheet",
                    ),
                    RoutingStageInput(
                        sequence=40,
                        stage_code="seaming",
                        stage_type="material",
                        stage_semantic="conversion",
                        output_kind="box",
                        can_be_terminal=True,
                    ),
                ],
            ),
        )
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="P_ROUTE_QTY",
            name="Route Qty Plant",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=True,
            sheet_length_mm=1200.0,
            sheet_width_mm=800.0,
        )

        result = compute(spec_input, plant)
        graph = build_stage_graph(result, order_qty=100)

        board_entries = graph.nodes["board"].meta.get("route_stage_quantities") or []
        sheet_entries = graph.nodes["sheet"].meta.get("route_stage_quantities") or []
        fg_entries = graph.nodes["finished_goods"].meta.get("route_stage_quantities") or []

        self.assertEqual([item["stage_code"] for item in board_entries], ["2ply", "board"])
        self.assertEqual([item["stage_code"] for item in sheet_entries], ["ts_print"])
        self.assertEqual([item["stage_code"] for item in fg_entries], ["seaming"])

        self.assertEqual(sheet_entries[0]["input_qty"], graph.nodes["board"].qty)
        self.assertEqual(sheet_entries[0]["output_qty"], graph.nodes["sheet"].qty)
        self.assertEqual(fg_entries[0]["input_qty"], graph.nodes["sheet"].qty)
        self.assertEqual(fg_entries[0]["output_qty"], graph.nodes["finished_goods"].qty)
        self.assertEqual(
            graph.nodes["raw_material"].meta.get("route_stage_sequence"),
            ["2ply", "board", "ts_print", "seaming"],
        )




