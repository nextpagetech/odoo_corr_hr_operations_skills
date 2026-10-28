import math
import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.bom_adapter import build_bom_structure, _resolve_rm_node
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.stage_graph import StageEdge, StageGraph, build_stage_graph
from custom_addons.corr_engine_v2.engine_v2.spec_input import (
    RoutingProfileInput,
    RoutingStageInput,
    SurfaceProfileInput,
)


class TestEngineV2BomAdapter(unittest.TestCase):
    def _make_engine_result(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="BOM Adapter",
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT",
            name="Plant",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=True,
            sheet_length_mm=1200.0,
            sheet_width_mm=800.0,
        )

        result = compute(spec_input, plant)
        return result

    def _make_routed_result(self, routing_profile, *, printing_required=False):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=11,
            spec_name="BOM Adapter Routed",
            routing_profile=routing_profile,
        )
        if printing_required:
            spec_input.surface_profile = SurfaceProfileInput(
                printing_required=True,
                print_process="flexo",
                print_colors=2,
                print_coverage_percent=50.0,
                top_sheet_mode="none",
            )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="ROUTED",
            name="Routed Plant",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=True,
            sheet_length_mm=1200.0,
            sheet_width_mm=800.0,
        )
        return compute(spec_input, plant)

    def test_bom_components_and_fg_qty(self):
        order_qty = 100
        result = self._make_engine_result()

        bom = build_bom_structure(result, order_qty)

        # Finished good quantity should match order_qty.
        self.assertEqual(bom["product_qty"], float(order_qty))

        components = bom["components"]
        self.assertTrue(components)

        # Expected per-box RM quantities based on geometry and GSM.
        expected_m2 = (500.0 * 400.0) / 1_000_000.0
        expected_liner_kg_per_fg = (180.0 * expected_m2) / 1000.0
        expected_medium_kg_per_fg = (120.0 * expected_m2) / 1000.0

        # Total for the order.
        expected_liner_total = expected_liner_kg_per_fg * order_qty
        expected_medium_total = expected_medium_kg_per_fg * order_qty

        by_product = {c["product"]: c for c in components}

        self.assertIn("rm_liner", by_product)
        self.assertIn("rm_medium", by_product)

        liner = by_product["rm_liner"]
        medium = by_product["rm_medium"]

        self.assertAlmostEqual(liner["qty"], expected_liner_total, places=6)
        self.assertAlmostEqual(medium["qty"], expected_medium_total, places=6)

        # Components should have corrugator stage for liner/medium, derived
        # from the StageGraph node mapping (board/raw_material).
        self.assertEqual(liner["stage"], "corrugator")
        self.assertEqual(medium["stage"], "corrugator")

        # Consumption type should be preserved inside metadata for debugging.
        self.assertEqual(liner["metadata"].get("consumption_type"), "area")
        self.assertIn("gsm", liner["metadata"])

    def test_operations_use_stage_graph_quantities(self):
        order_qty = 100
        result = self._make_engine_result()

        # Build a quantity StageGraph explicitly for this order.
        qgraph = build_stage_graph(result, order_qty)

        # Sanity: layout yields 4 boxes per sheet and 4 blanks per sheet.
        metrics = result.metrics
        self.assertEqual(metrics.extra.get("layout_blanks_per_sheet"), 4)
        self.assertEqual(metrics.extra.get("boxes_per_sheet"), 4)

        # From previous tests: sheets_required = 25, blanks = 100.
        self.assertEqual(qgraph.nodes["sheet"].qty, 25)
        self.assertEqual(qgraph.nodes["blanks"].qty, 100)
        self.assertEqual(qgraph.nodes["finished_goods"].qty, order_qty)

        bom = build_bom_structure(result, order_qty)
        operations = bom["operations"]
        self.assertTrue(operations)

        by_stage = {op["stage"]: op for op in operations}

        # Corrugator: raw_material -> board.
        corr = by_stage.get("corrugator")
        self.assertIsNotNone(corr)
        self.assertAlmostEqual(corr["input_qty"], qgraph.nodes["raw_material"].qty, places=6)
        self.assertAlmostEqual(corr["output_qty"], qgraph.nodes["board"].qty, places=6)

        # Printing: sheet -> blanks uses qty_ratio and matches node quantities.
        printing = by_stage.get("printing")
        self.assertIsNotNone(printing)
        sheet_to_blanks = [
            e for e in qgraph.edges if e.from_node == "sheet" and e.to_node == "blanks"
        ][0]
        expected_blanks = qgraph.nodes["sheet"].qty * sheet_to_blanks.qty_ratio
        self.assertEqual(printing["input_qty"], qgraph.nodes["sheet"].qty)
        self.assertEqual(printing["output_qty"], expected_blanks)
        self.assertEqual(expected_blanks, qgraph.nodes["blanks"].qty)

        # Diecut: blanks -> finished_goods uses qty_ratio and matches node quantities.
        diecut = by_stage.get("diecut")
        self.assertIsNotNone(diecut)
        blanks_to_fg = [
            e for e in qgraph.edges if e.from_node == "blanks" and e.to_node == "finished_goods"
        ][0]
        expected_fg = qgraph.nodes["blanks"].qty * blanks_to_fg.qty_ratio
        self.assertEqual(diecut["input_qty"], qgraph.nodes["blanks"].qty)
        self.assertAlmostEqual(diecut["output_qty"], expected_fg, places=6)
        self.assertAlmostEqual(expected_fg, qgraph.nodes["finished_goods"].qty, places=6)

    def test_waste_percent_affects_operation_output(self):
        order_qty = 100
        result = self._make_engine_result()

        # Build a quantity StageGraph and attach it so the BOM adapter reuses it.
        qgraph = build_stage_graph(result, order_qty)
        result.quantity_stage_graph = qgraph

        # Inject a small waste on the sheet -> blanks edge and update the
        # blanks node qty to remain consistent.
        edges = [e for e in qgraph.edges if e.from_node == "sheet" and e.to_node == "blanks"]
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertIsInstance(edge, StageEdge)

        sheet_qty = qgraph.nodes["sheet"].qty
        original_blanks = qgraph.nodes["blanks"].qty
        self.assertEqual(original_blanks, sheet_qty * edge.qty_ratio)

        edge.waste_percent = 0.05  # 5% trim waste
        new_blanks = sheet_qty * edge.qty_ratio * (1.0 - edge.waste_percent)
        qgraph.nodes["blanks"].qty = new_blanks

        bom = build_bom_structure(result, order_qty)
        printing_op = {op["stage"]: op for op in bom["operations"]}["printing"]

        self.assertEqual(printing_op["input_qty"], sheet_qty)
        self.assertAlmostEqual(printing_op["output_qty"], new_blanks, places=6)

    def test_surface_rms_grouped_under_printing_stage(self):
        """Top-roll and ink RMs should appear under the printing stage in the BOM."""

        order_qty = 100
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=4,
            spec_name="BOM Surface RMs",
        )
        spec_input.top_sheet_gsm = 150.0
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=2,
            print_coverage_percent=50.0,
            top_sheet_mode="roll",
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="SURF_BOM",
            name="Surface BOM Plant",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=True,
            sheet_length_mm=1200.0,
            sheet_width_mm=800.0,
        )
        plant.enable_consumables = True
        plant.enable_adhesive = False
        plant.enable_ink = True
        plant.ink_gm2_at_100pct_default = 5.0

        result = compute(spec_input, plant)
        bom = build_bom_structure(result, order_qty)
        components = bom["components"]
        by_product = {c["product"]: c for c in components}

        self.assertIn("rm_top_roll", by_product)
        self.assertIn("rm_ink", by_product)
        self.assertEqual(by_product["rm_top_roll"]["stage"], "printing")
        self.assertEqual(by_product["rm_ink"]["stage"], "printing")

    def test_resolve_rm_node_keeps_role_aware_mapping_for_costing_components(self):
        """Costing component dicts use the same role-aware routing as RM objects."""

        result = self._make_engine_result()
        qgraph = build_stage_graph(result, order_qty=100)

        self.assertEqual(
            _resolve_rm_node(qgraph, "2ply", rm={"role": "top_roll"}),
            "sheet",
        )
        self.assertEqual(
            _resolve_rm_node(qgraph, "2ply", rm={"role": "ink"}),
            "sheet",
        )
        self.assertEqual(
            _resolve_rm_node(qgraph, "2ply", rm={"role": "adhesive_fg", "engine_role": "adhesive_fg"}),
            "finished_goods",
        )

    def test_route_driven_bom_operations_follow_selected_route(self):
        order_qty = 100
        routing_profile = RoutingProfileInput(
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
        )

        result = self._make_routed_result(routing_profile, printing_required=True)
        bom = build_bom_structure(result, order_qty)
        by_stage = {op["stage"]: op for op in bom["operations"]}

        self.assertIn("corrugator", by_stage)
        self.assertIn("printing", by_stage)
        self.assertIn("conversion", by_stage)
        self.assertNotIn("diecut", by_stage)

        qgraph = build_stage_graph(result, order_qty)
        self.assertEqual(by_stage["printing"]["input_qty"], qgraph.nodes["sheet"].qty)
        self.assertEqual(by_stage["conversion"]["input_qty"], qgraph.nodes["blanks"].qty)
        self.assertEqual(by_stage["conversion"]["output_qty"], qgraph.nodes["finished_goods"].qty)

    def test_route_driven_bom_operations_do_not_inject_printing_when_route_has_none(self):
        order_qty = 100
        routing_profile = RoutingProfileInput(
            name="board-fg",
            stages=[
                RoutingStageInput(
                    sequence=10,
                    stage_code="board_stage_a",
                    stage_type="material",
                    stage_semantic="conversion",
                    output_kind="board",
                ),
                RoutingStageInput(
                    sequence=20,
                    stage_code="fg_stage_a",
                    stage_type="material",
                    stage_semantic="fg",
                    output_kind="fg",
                    can_be_terminal=True,
                    is_fg_stage=True,
                ),
            ],
        )

        result = self._make_routed_result(routing_profile, printing_required=False)
        bom = build_bom_structure(result, order_qty)
        by_stage = {op["stage"]: op for op in bom["operations"]}

        self.assertIn("corrugator", by_stage)
        self.assertNotIn("printing", by_stage)
        self.assertNotIn("diecut", by_stage)
        self.assertNotIn("conversion", by_stage)




