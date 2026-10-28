import unittest
from types import SimpleNamespace

from custom_addons.corr_engine_v2.engine_v2 import (
    EngineMetrics,
    EngineSpecInput,
    RmRequirement,
    compute,
    Stage as MaterialStage,
    StageGraph as MaterialStageGraph,
)
from custom_addons.corr_engine_v2.engine_v2.bom_adapter import build_bom_structure
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.consumables import effective_consumable_state
from custom_addons.corr_engine_v2.engine_v2.stage_graph import StageGraph, build_stage_graph
from custom_addons.corr_engine_v2.engine_v2.spec_input import SurfaceProfileInput
from custom_addons.corr_engine_v2.engine_v2.rm_rules import _resolve_wire_rms, _resolve_adhesive_rms, _resolve_ink_rms
from custom_addons.corr_engine_v2.plant_adapter import parse_consumable_bool


class TestEngineV2RmConsumption(unittest.TestCase):
    def test_consumable_system_parameter_bool_parser(self):
        false_values = ["0", "false", "False", "no", "off", "", None]
        true_values = ["1", "true", "True", "yes", "on"]

        for value in false_values:
            self.assertFalse(parse_consumable_bool(value), value)
        for value in true_values:
            self.assertTrue(parse_consumable_bool(value), value)

    def test_global_consumables_off_suppresses_all_derived_process_consumables(self):
        plant = PlantProfile(enable_consumables=False, enable_adhesive=True)
        spec = SimpleNamespace(
            joining_type="gluing_stitching",
            surface_printing_required=True,
            surface_lamination_required=True,
        )

        state = effective_consumable_state(plant_profile=plant, spec=spec)

        self.assertFalse(state["enable_consumables"])
        for key in (
            "enable_corrugation_adhesive",
            "enable_lamination_adhesive",
            "enable_printing_ink",
            "enable_lamination_film",
            "enable_stitching_wire",
            "enable_jointing_glue",
            "enable_jointing_tape",
        ):
            self.assertFalse(state[key], key)

    def test_joining_type_derives_joining_consumables(self):
        plant = PlantProfile(enable_consumables=True, enable_adhesive=True)
        expected = {
            "none": (False, False, False),
            "gluing": (True, False, False),
            "stitching": (False, True, False),
            "gluing_stitching": (True, True, False),
            "tape": (False, False, True),
        }

        for joining_type, flags in expected.items():
            spec = SimpleNamespace(joining_type=joining_type)
            state = effective_consumable_state(plant_profile=plant, spec=spec)
            self.assertEqual(
                (
                    state["enable_jointing_glue"],
                    state["enable_stitching_wire"],
                    state["enable_jointing_tape"],
                ),
                flags,
                joining_type,
            )

    def test_printing_and_lamination_process_fields_derive_consumables(self):
        plant = PlantProfile(enable_consumables=True, enable_adhesive=True)
        spec = SimpleNamespace(
            joining_type="none",
            surface_printing_required=False,
            surface_lamination_required=True,
        )

        state = effective_consumable_state(plant_profile=plant, spec=spec)

        self.assertFalse(state["enable_printing_ink"])
        self.assertTrue(state["enable_lamination_film"])

    def _make_spec_and_plant(self, length_mm: float) -> tuple:
        spec_input = EngineSpecInput(
            blank_length_mm=length_mm,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name=f"RM Consumption {length_mm}",
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
        return spec_input, plant

    def _make_material_stage_graph(self) -> MaterialStageGraph:
        """Minimal material StageGraph with 2ply/board/fg stages for adhesive tests."""
        return MaterialStageGraph(
            stages=[
                MaterialStage(key="2ply", name="2ply", stage_type="material"),
                MaterialStage(key="board", name="Board", stage_type="material"),
                MaterialStage(key="fg", name="FG", stage_type="material"),
            ],
            flows=[],
        )

    def test_area_based_liner_varies_with_dimensions(self):
        # Two specs with different blank length -> different m2_per_box
        # and different liner qty_per_fg, with consumption_type="area".
        spec1, plant = self._make_spec_and_plant(500.0)
        spec2, _ = self._make_spec_and_plant(600.0)

        res1 = compute(spec1, plant)
        res2 = compute(spec2, plant)

        liner1 = next(rm for rm in res1.rm_requirements if rm.role == "liner")
        liner2 = next(rm for rm in res2.rm_requirements if rm.role == "liner")

        self.assertEqual(liner1.consumption_type, "area")
        self.assertEqual(liner2.consumption_type, "area")

        m2_1 = res1.metrics.m2_per_box
        m2_2 = res2.metrics.m2_per_box

        # Base formula: qty_per_fg = gsm * m2_per_box / 1000
        expected_1 = (180.0 * m2_1) / 1000.0
        expected_2 = (180.0 * m2_2) / 1000.0

        self.assertAlmostEqual(liner1.qty_per_fg, expected_1, places=6)
        self.assertAlmostEqual(liner2.qty_per_fg, expected_2, places=6)
        # Different dimensions -> different area-based consumption.
        self.assertNotAlmostEqual(liner1.qty_per_fg, liner2.qty_per_fg, places=6)

    def test_medium_uses_flute_take_up_factor(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=180.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Medium TUF",
        )
        spec_input.board_layers = [
            {"type": "medium", "gsm": 180.0, "layer_no": 1, "flute_code": "B", "flute_tuf": 1.4},
            {"type": "liner", "gsm": 180.0, "layer_no": 2},
        ]
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT",
            name="Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        medium = next(rm for rm in result.rm_requirements if rm.role == "medium")
        liner = next(rm for rm in result.rm_requirements if rm.role == "liner")

        expected_liner = (180.0 * result.metrics.m2_per_box) / 1000.0
        expected_medium = expected_liner * 1.4
        self.assertAlmostEqual(liner.qty_per_fg, expected_liner, places=6)
        self.assertAlmostEqual(medium.qty_per_fg, expected_medium, places=6)
        self.assertAlmostEqual(medium.metadata.get("tuf_applied"), 1.4, places=6)
        self.assertEqual(medium.metadata.get("flute_code"), "B")

    def test_unit_based_rm_scales_linearly_with_order_qty(self):
        # Use a simple spec/plant and inject a unit-based RM requirement
        # with a fixed qty_per_fg that should scale linearly with order_qty.
        spec, plant = self._make_spec_and_plant(500.0)
        result = compute(spec, plant)

        # Add a synthetic unit-based RM: e.g. 2 labels per box.
        unit_rm = RmRequirement(
            product_key="rm_label",
            role="label",
            stage_key=None,
            layer_no=None,
            qty_per_fg=2.0,
            qty_uom="unit",
            consumption_type="unit",
            metadata={"example": True},
        )
        result.rm_requirements.append(unit_rm)

        order_qty_small = 10
        order_qty_large = 25

        bom_small = build_bom_structure(result, order_qty_small)
        bom_large = build_bom_structure(result, order_qty_large)

        comp_small = {c["product"]: c for c in bom_small["components"]}
        comp_large = {c["product"]: c for c in bom_large["components"]}

        self.assertIn("rm_label", comp_small)
        self.assertIn("rm_label", comp_large)

        qty_small = comp_small["rm_label"]["qty"]
        qty_large = comp_large["rm_label"]["qty"]

        # qty_per_fg is constant for unit-based RM; total scales with order_qty.
        self.assertAlmostEqual(qty_small, 2.0 * order_qty_small, places=6)
        self.assertAlmostEqual(qty_large, 2.0 * order_qty_large, places=6)
        # Linear scaling between orders.
        self.assertAlmostEqual(qty_large / qty_small, order_qty_large / order_qty_small, places=6)

    def test_unit_based_rm_attached_to_finished_goods_stage(self):
        spec, plant = self._make_spec_and_plant(500.0)
        result = compute(spec, plant)

        unit_rm = RmRequirement(
            product_key="rm_label",
            role="label",
            stage_key=None,
            layer_no=None,
            qty_per_fg=1.0,
            qty_uom="unit",
            consumption_type="unit",
            metadata={"example": True},
        )
        result.rm_requirements.append(unit_rm)

        qgraph = build_stage_graph(result, order_qty=10)
        self.assertIsInstance(qgraph, StageGraph)
        fg_node = qgraph.nodes.get("finished_goods")
        self.assertIsNotNone(fg_node)

        rm_unit = fg_node.meta.get("rm_unit") or []
        self.assertTrue(any(entry.get("product_key") == "rm_label" for entry in rm_unit))

    # ------------------------------------------------------------------
    # Wire consumable tests
    # ------------------------------------------------------------------

    def _make_spec_with_locking(self, length_mm: float):
        spec, plant = self._make_spec_and_plant(length_mm)
        # Attach a simple locking-like object with wire parameters.
        class LockingMock:
            def __init__(self):
                self.method = "wire"
                self.code = "WIRE_14MM"
                self.wire_type_id = 14
                self.wire_product_id = 901
                self.wire_product_name = "14mm Stitching Wire"
                self.wire_stitches_per_cm = 1.5
                self.wire_g_per_stitch = 0.05

        spec.locking_type = LockingMock()
        # Enable consumables + wire on the plant profile and provide
        # plant-level defaults that will be overridden by locking.
        plant.enable_consumables = True
        plant.enable_wire = True
        plant.wire_stitches_per_cm_default = 1.0
        plant.wire_g_per_stitch_default = 0.04
        return spec, plant

    def test_wire_rm_created_when_seam_and_params_present(self):
        spec, plant = self._make_spec_with_locking(500.0)

        # Minimal metrics with seam_mm defined in geometry extra.
        metrics = EngineMetrics(m2_per_box=1.0)
        metrics.extra["geometry"] = {"seam_mm": 350.0}

        # StageGraph is not used by _resolve_wire_rms but must be provided.
        stage_graph = StageGraph()

        wires = _resolve_wire_rms(spec, plant, stage_graph, metrics, m2_per_box=1.0)
        self.assertEqual(len(wires), 1)
        wire_rm = wires[0]
        self.assertEqual(wire_rm.qty_uom, "kg")
        self.assertEqual(wire_rm.consumption_type, "length")

        seam_mm = 350.0
        seam_cm = seam_mm / 10.0
        stitches_per_cm = 1.5
        g_per_stitch = 0.05
        stitches_per_box = seam_cm * stitches_per_cm
        wire_g_per_box = stitches_per_box * g_per_stitch
        expected_kg_per_box = wire_g_per_box / 1000.0

        self.assertAlmostEqual(wire_rm.qty_per_fg, expected_kg_per_box, places=6)
        # Metadata should expose core wire parameters for adapter usage.
        meta = wire_rm.metadata or {}
        self.assertEqual(meta.get("locking_method"), "wire")
        self.assertEqual(meta.get("wire_grade"), "WIRE_14MM")
        self.assertEqual(meta.get("wire_type_id"), 14)
        self.assertEqual(meta.get("wire_product_id"), 901)
        self.assertEqual(meta.get("product_id"), 901)
        self.assertEqual(meta.get("product_name"), "14mm Stitching Wire")
        self.assertEqual(meta.get("writer_product_key"), "product.product:901")
        self.assertEqual(meta.get("resolution_status"), "resolved")
        self.assertEqual((meta.get("resolution") or {}).get("product_id"), 901)
        self.assertAlmostEqual(float(meta.get("seam_length_mm", 0.0) or 0.0), seam_mm, places=6)
        self.assertAlmostEqual(float(meta.get("stitches_per_cm", 0.0) or 0.0), stitches_per_cm, places=6)
        self.assertAlmostEqual(float(meta.get("g_per_stitch", 0.0) or 0.0), g_per_stitch, places=6)
        self.assertAlmostEqual(float(meta.get("wire_g_per_box", 0.0) or 0.0), wire_g_per_box, places=6)

    def test_stitching_wire_uses_glue_flap_override_when_style_seam_is_zero(self):
        spec, plant = self._make_spec_and_plant(500.0)
        spec.style_code = "0100"
        spec.box_length_mm = 620.0
        spec.box_width_mm = 600.0
        spec.box_height_mm = 0.0
        spec.glue_flap_mm = 35.0
        spec.joining_type = "stitching"
        plant.enable_consumables = True
        plant.wire_stitches_per_cm_default = 1.0
        plant.wire_g_per_stitch_default = 0.1

        result = compute(spec, plant)
        geometry = (result.metrics.extra or {}).get("geometry") or {}
        wires = [rm for rm in result.rm_requirements if rm.product_key == "rm_wire"]

        self.assertEqual(geometry.get("seam_mm"), 35.0)
        self.assertEqual(geometry.get("seam_source"), "joining_glue_flap_override")
        self.assertEqual(len(wires), 1)
        self.assertGreater(wires[0].qty_per_fg, 0.0)

    def test_stitching_wire_uses_plant_seam_when_style_seam_and_override_are_zero(self):
        spec, plant = self._make_spec_and_plant(500.0)
        spec.style_code = "0100"
        spec.box_length_mm = 620.0
        spec.box_width_mm = 600.0
        spec.box_height_mm = 0.0
        spec.glue_flap_mm = 0.0
        spec.joining_type = "stitching"
        plant.enable_consumables = True
        plant.default_glue_lap_mm = 22.0
        plant.wire_stitches_per_cm_default = 1.0
        plant.wire_g_per_stitch_default = 0.1

        result = compute(spec, plant)
        geometry = (result.metrics.extra or {}).get("geometry") or {}
        wires = [rm for rm in result.rm_requirements if rm.product_key == "rm_wire"]

        self.assertEqual(geometry.get("seam_mm"), 22.0)
        self.assertEqual(geometry.get("seam_source"), "joining_plant_default_glue_lap")
        self.assertEqual(len(wires), 1)
        self.assertGreater(wires[0].qty_per_fg, 0.0)

    def test_route_selected_wire_falls_back_to_final_stage_without_joining_stage(self):
        spec, plant = self._make_spec_and_plant(500.0)
        spec.joining_type = "stitching"

        class RouteStage:
            def __init__(self, code):
                self.stage_code = code

        class RouteProfile:
            def __init__(self):
                self.stages = [RouteStage("2ply"), RouteStage("board")]

        spec.routing_profile = RouteProfile()
        plant.enable_consumables = True
        plant.wire_stitches_per_cm_default = 1.5
        plant.wire_g_per_stitch_default = 0.05

        metrics = EngineMetrics(m2_per_box=1.0)
        metrics.extra["geometry"] = {"seam_mm": 35.0}
        mat_graph = MaterialStageGraph(
            stages=[
                MaterialStage(key="2ply", name="2-Ply", stage_type="material"),
                MaterialStage(key="board", name="Board", stage_type="material", output_kind="board"),
            ],
            flows=[],
        )

        wires = _resolve_wire_rms(spec, plant, mat_graph, metrics, m2_per_box=1.0)

        self.assertEqual(len(wires), 1)
        self.assertEqual(wires[0].stage_key, "board")

    def test_wire_metadata_includes_wire_grade_from_locking_code(self):
        spec, plant = self._make_spec_and_plant(500.0)

        class LockingWithCode:
            def __init__(self):
                self.method = "wire"
                self.code = "HEAVY_SS304"
                self.wire_stitches_per_cm = 1.0
                self.wire_g_per_stitch = 0.1

        spec.locking_type = LockingWithCode()
        plant.enable_consumables = True
        plant.enable_wire = True
        # Locking provides parameters; plant defaults are irrelevant for this test.
        plant.wire_stitches_per_cm_default = 0.0
        plant.wire_g_per_stitch_default = 0.0

        metrics = EngineMetrics(m2_per_box=1.0)
        metrics.extra["geometry"] = {"seam_mm": 100.0}
        stage_graph = StageGraph()

        wires = _resolve_wire_rms(spec, plant, stage_graph, metrics, m2_per_box=1.0)
        self.assertEqual(len(wires), 1)
        meta = wires[0].metadata or {}
        # wire_grade should reflect the locking code verbatim (trimmed).
        self.assertEqual(meta.get("wire_grade"), "HEAVY_SS304")

    def test_no_wire_when_locking_not_wire(self):
        spec, plant = self._make_spec_and_plant(500.0)

        class LockingMock:
            def __init__(self):
                self.method = "glue"
                self.wire_stitches_per_cm = 1.5
                self.wire_g_per_stitch = 0.05

        spec.locking_type = LockingMock()
        plant.enable_consumables = True
        plant.enable_wire = True

        metrics = EngineMetrics(m2_per_box=1.0)
        metrics.extra["geometry"] = {"seam_mm": 350.0}
        stage_graph = StageGraph()

        wires = _resolve_wire_rms(spec, plant, stage_graph, metrics, m2_per_box=1.0)
        self.assertFalse(wires)

    def test_fg_adhesive_and_wire_when_locking_both(self):
        spec, plant = self._make_spec_and_plant(500.0)

        class LockingBoth:
            def __init__(self):
                self.method = "both"
                self.wire_stitches_per_cm = 1.5
                self.wire_g_per_stitch = 0.05

        spec.locking_type = LockingBoth()
        spec.joining_type = "gluing_stitching"

        plant.enable_consumables = True
        plant.enable_adhesive = True
        plant.enable_wire = True
        plant.adhesive_factor_fg_kg_per_m2 = 0.2

        metrics = EngineMetrics(m2_per_box=1.0)
        metrics.extra["geometry"] = {"seam_mm": 350.0}

        mat_graph = self._make_material_stage_graph()

        adh_list = _resolve_adhesive_rms(spec, plant, mat_graph, metrics, m2_per_box=1.0)
        fg_adhs = [rm for rm in adh_list if rm.role == "adhesive_fg"]
        self.assertEqual(len(fg_adhs), 1)
        fg_rm = fg_adhs[0]
        self.assertAlmostEqual(fg_rm.qty_per_fg, 0.2, places=6)

        wires = _resolve_wire_rms(spec, plant, mat_graph, metrics, m2_per_box=1.0)
        self.assertEqual(len(wires), 1)

    def test_adhesive_stage_allocation_with_full_stages(self):
        """Plant with 2ply/board/fg: each adhesive stays on its preferred stage."""

        spec, plant = self._make_spec_and_plant(500.0)
        spec.joining_type = "gluing"
        plant.enable_consumables = True
        plant.enable_adhesive = True
        plant.adhesive_factor_corrugation_kg_per_m2 = 0.5
        plant.adhesive_factor_board_kg_per_m2 = 0.2
        plant.adhesive_factor_fg_kg_per_m2 = 0.1

        metrics = EngineMetrics(m2_per_box=1.0)
        mat_graph = self._make_material_stage_graph()

        adh_list = _resolve_adhesive_rms(spec, plant, mat_graph, metrics, m2_per_box=1.0)
        by_role = {rm.role: rm for rm in adh_list}

        self.assertEqual(by_role["adhesive_corrugation"].stage_key, "2ply")
        self.assertEqual(by_role["adhesive_board"].stage_key, "board")
        self.assertEqual(by_role["adhesive_fg"].stage_key, "fg")

    def test_adhesive_stage_allocation_without_corrugation_stage(self):
        """Plant without 2ply: corrugation adhesive merges into board."""

        spec, plant = self._make_spec_and_plant(500.0)
        spec.joining_type = "gluing"
        plant.enable_consumables = True
        plant.enable_adhesive = True
        plant.adhesive_factor_corrugation_kg_per_m2 = 0.5
        plant.adhesive_factor_board_kg_per_m2 = 0.2
        plant.adhesive_factor_fg_kg_per_m2 = 0.1

        metrics = EngineMetrics(m2_per_box=1.0)
        # Material graph with board + fg only.
        mat_graph = MaterialStageGraph(
            stages=[
                MaterialStage(key="board", name="Board", stage_type="material"),
                MaterialStage(key="fg", name="FG", stage_type="material"),
            ],
            flows=[],
        )

        adh_list = _resolve_adhesive_rms(spec, plant, mat_graph, metrics, m2_per_box=1.0)
        by_role = {rm.role: rm for rm in adh_list}

        # Corrugation adhesive should fall back to board.
        self.assertEqual(by_role["adhesive_corrugation"].stage_key, "board")
        # Board adhesive remains on board.
        self.assertEqual(by_role["adhesive_board"].stage_key, "board")
        # FG adhesive remains on fg.
        self.assertEqual(by_role["adhesive_fg"].stage_key, "fg")

    def test_adhesive_stage_allocation_when_all_merge(self):
        """Plant with only FG stage: all adhesive factors merge into FG."""

        spec, plant = self._make_spec_and_plant(500.0)
        spec.joining_type = "gluing"
        plant.enable_consumables = True
        plant.enable_adhesive = True
        plant.adhesive_factor_corrugation_kg_per_m2 = 0.5
        plant.adhesive_factor_board_kg_per_m2 = 0.2
        plant.adhesive_factor_fg_kg_per_m2 = 0.1

        metrics = EngineMetrics(m2_per_box=1.0)
        mat_graph = MaterialStageGraph(
            stages=[MaterialStage(key="fg", name="FG", stage_type="material")],
            flows=[],
        )

        adh_list = _resolve_adhesive_rms(spec, plant, mat_graph, metrics, m2_per_box=1.0)
        by_role = {rm.role: rm for rm in adh_list}

        # All adhesives must be present and use the FG stage.
        self.assertEqual(len(adh_list), 3)
        self.assertEqual(by_role["adhesive_corrugation"].stage_key, "fg")
        self.assertEqual(by_role["adhesive_board"].stage_key, "fg")
        self.assertEqual(by_role["adhesive_fg"].stage_key, "fg")

    def test_route_selected_adhesive_and_wire_follow_custom_terminal_stage(self):
        spec, plant = self._make_spec_and_plant(500.0)

        class LockingBoth:
            def __init__(self):
                self.method = "both"
                self.wire_stitches_per_cm = 1.5
                self.wire_g_per_stitch = 0.05

        spec.locking_type = LockingBoth()
        spec.joining_type = "gluing_stitching"

        class RouteStage:
            def __init__(self, code):
                self.stage_code = code

        class RouteProfile:
            def __init__(self):
                self.stages = [RouteStage("corr_a"), RouteStage("close_joint_a")]

        spec.routing_profile = RouteProfile()

        plant.enable_consumables = True
        plant.enable_adhesive = True
        plant.enable_wire = True
        plant.adhesive_factor_corrugation_kg_per_m2 = 0.5
        plant.adhesive_factor_board_kg_per_m2 = 0.2
        plant.adhesive_factor_fg_kg_per_m2 = 0.1

        metrics = EngineMetrics(m2_per_box=1.0)
        metrics.extra["geometry"] = {"seam_mm": 350.0}
        mat_graph = MaterialStageGraph(
            stages=[
                MaterialStage(
                    key="corr_a",
                    name="Corrugation A",
                    stage_type="material",
                    logical_role="corrugation",
                    output_kind="board",
                ),
                MaterialStage(
                    key="close_joint_a",
                    name="Close Joint A",
                    stage_type="material",
                    logical_role="conversion",
                    output_kind="box",
                    is_terminal=True,
                ),
            ],
            flows=[],
        )

        adh_list = _resolve_adhesive_rms(spec, plant, mat_graph, metrics, m2_per_box=1.0)
        by_role = {rm.role: rm for rm in adh_list}
        self.assertEqual(by_role["adhesive_corrugation"].stage_key, "corr_a")
        self.assertEqual(by_role["adhesive_board"].stage_key, "close_joint_a")
        self.assertEqual(by_role["adhesive_fg"].stage_key, "close_joint_a")

        wires = _resolve_wire_rms(spec, plant, mat_graph, metrics, m2_per_box=1.0)
        self.assertEqual(len(wires), 1)
        self.assertEqual(wires[0].stage_key, "close_joint_a")

    def test_fg_adhesive_does_not_fallback_to_nonterminal_board_stage(self):
        spec, plant = self._make_spec_and_plant(500.0)

        class RouteStage:
            def __init__(self, code):
                self.stage_code = code

        class RouteProfile:
            def __init__(self):
                self.stages = [RouteStage("2ply"), RouteStage("board")]

        spec.routing_profile = RouteProfile()
        plant.enable_consumables = True
        plant.enable_adhesive = True
        plant.enable_jointing_glue = True
        plant.adhesive_factor_corrugation_kg_per_m2 = 0.093
        plant.adhesive_factor_board_kg_per_m2 = 0.037
        plant.adhesive_factor_fg_kg_per_m2 = 0.037

        mat_graph = MaterialStageGraph(
            stages=[
                MaterialStage(
                    key="2ply",
                    name="2-Ply Corrugation",
                    stage_type="material",
                    logical_role="corrugation",
                    output_kind="sheet",
                ),
                MaterialStage(
                    key="board",
                    name="Board Formation",
                    stage_type="material",
                    logical_role="conversion",
                    output_kind="sheet",
                    is_terminal=False,
                ),
            ],
            flows=[],
        )

        adh_list = _resolve_adhesive_rms(
            spec,
            plant,
            mat_graph,
            EngineMetrics(m2_per_box=1.0),
            m2_per_box=1.0,
        )
        by_role = {rm.role: rm for rm in adh_list}

        self.assertEqual(by_role["adhesive_corrugation"].stage_key, "2ply")
        self.assertEqual(by_role["adhesive_board"].stage_key, "board")
        self.assertNotIn("adhesive_fg", by_role)

    # ------------------------------------------------------------------
    # Ink consumable tests
    # ------------------------------------------------------------------

    def test_ink_rm_created_when_printing_required(self):
        spec, plant = self._make_spec_and_plant(500.0)
        # Enable consumables + ink on the plant profile with a simple
        # baseline for ink gm2 at 100% coverage.
        plant.enable_consumables = True
        plant.enable_adhesive = False
        plant.enable_ink = True
        plant.ink_gm2_at_100pct_default = 5.0

        # Attach a surface profile that requests printing.
        surface = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=2,
            print_coverage_percent=50.0,
            print_side="top",
            top_sheet_mode="none",
        )
        spec.surface_profile = surface

        result = compute(spec, plant)
        m2_per_box = result.metrics.m2_per_box

        # Ink RM must be present with role "ink" and a material
        # printing stage. With the default plant this is the board
        # material stage (printing operations attached to board).
        inks = [rm for rm in result.rm_requirements if rm.role == "ink"]
        self.assertEqual(len(inks), 1)
        ink_rm = inks[0]
        self.assertEqual(ink_rm.stage_key, "board")
        self.assertEqual(ink_rm.qty_uom, "kg")
        self.assertEqual(ink_rm.consumption_type, "area")

        # Expected formula:
        #   gm2_effective = base_gm2 * coverage_factor * color_factor
        #   ink_kg_per_box = gm2_effective * m2_per_box / 1000
        base_gm2 = 5.0
        coverage_factor = 0.5  # 50%
        color_factor = 2.0     # 2 colours -> factor 2
        gm2_effective = base_gm2 * coverage_factor * color_factor
        expected_kg_per_box = gm2_effective * m2_per_box / 1000.0

        self.assertAlmostEqual(ink_rm.qty_per_fg, expected_kg_per_box, places=6)
        # Diagnostics metric should expose the same per-box total.
        extra = result.metrics.extra or {}
        self.assertAlmostEqual(
            float(extra.get("ink_total_kg_per_box", 0.0) or 0.0),
            expected_kg_per_box,
            places=6,
        )

    def test_no_ink_when_printing_not_required(self):
        spec, plant = self._make_spec_and_plant(500.0)
        plant.enable_consumables = True
        plant.enable_ink = True
        plant.ink_gm2_at_100pct_default = 5.0

        # printing_required=False -> no ink RM.
        surface = SurfaceProfileInput(
            printing_required=False,
            print_process="flexo",
            print_colors=2,
            print_coverage_percent=80.0,
        )
        spec.surface_profile = surface

        result = compute(spec, plant)
        inks = [rm for rm in result.rm_requirements if rm.role == "ink"]
        self.assertFalse(inks)

    # Wire negative-path tests: no wire when prerequisites are not met.

    def test_no_wire_when_seam_missing(self):
        spec, plant = self._make_spec_with_locking(500.0)
        metrics = EngineMetrics(m2_per_box=1.0)
        # No geometry / seam_mm provided.
        stage_graph = StageGraph()

        wires = _resolve_wire_rms(spec, plant, stage_graph, metrics, m2_per_box=1.0)
        self.assertFalse(wires)

    def test_no_wire_when_parameters_missing(self):
        spec, plant = self._make_spec_and_plant(500.0)

        class LockingWireNoParams:
            def __init__(self):
                self.method = "wire"
                self.wire_stitches_per_cm = 0.0
                self.wire_g_per_stitch = 0.0

        spec.locking_type = LockingWireNoParams()
        plant.enable_consumables = True
        plant.enable_wire = True
        plant.wire_stitches_per_cm_default = 0.0
        plant.wire_g_per_stitch_default = 0.0

        metrics = EngineMetrics(m2_per_box=1.0)
        metrics.extra["geometry"] = {"seam_mm": 350.0}
        stage_graph = StageGraph()

        wires = _resolve_wire_rms(spec, plant, stage_graph, metrics, m2_per_box=1.0)
        self.assertFalse(wires)

    def test_no_ink_when_consumables_disabled(self):
        spec, plant = self._make_spec_and_plant(500.0)
        # Ink config present but master flag off -> no ink RM.
        plant.enable_consumables = False
        plant.enable_ink = True
        plant.ink_gm2_at_100pct_default = 5.0

        surface = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=3,
            print_coverage_percent=70.0,
        )
        spec.surface_profile = surface

        result = compute(spec, plant)
        inks = [rm for rm in result.rm_requirements if rm.role == "ink"]
        self.assertFalse(inks)

    def test_printing_required_derives_ink_even_when_legacy_ink_flag_false(self):
        spec, plant = self._make_spec_and_plant(500.0)
        plant.enable_consumables = True
        plant.enable_ink = False
        plant.ink_gm2_at_100pct_default = 5.0

        surface = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=1,
            print_coverage_percent=60.0,
        )
        spec.surface_profile = surface

        result = compute(spec, plant)
        inks = [rm for rm in result.rm_requirements if rm.role == "ink"]
        self.assertEqual(len(inks), 1)

    def test_route_printing_stage_owns_ink_even_when_cutting_is_first(self):
        spec, plant = self._make_spec_and_plant(500.0)
        plant.enable_consumables = True
        plant.ink_gm2_at_100pct_default = 5.0
        spec.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=1,
            print_coverage_percent=20.0,
        )
        spec.routing_profile = SimpleNamespace(stages=[SimpleNamespace(stage_code="ts_print")])
        stage_graph = MaterialStageGraph(
            stages=[
                MaterialStage(
                    key="cutting",
                    name="Cutting",
                    stage_type="material",
                    logical_role="auxiliary",
                    output_kind="sheet",
                ),
                MaterialStage(
                    key="ts_print",
                    name="Top Sheet Printing",
                    stage_type="operation",
                    logical_role="printing",
                    output_kind="sheet",
                ),
                MaterialStage(
                    key="board",
                    name="Board",
                    stage_type="material",
                    logical_role="corrugation",
                    output_kind="board",
                ),
            ],
            flows=[],
        )

        inks = _resolve_ink_rms(
            spec,
            plant,
            stage_graph,
            EngineMetrics(m2_per_box=1.0),
            m2_per_box=1.0,
        )

        self.assertEqual(len(inks), 1)
        self.assertEqual(inks[0].stage_key, "ts_print")

    def test_no_ink_when_base_factor_not_configured(self):
        spec, plant = self._make_spec_and_plant(500.0)
        plant.enable_consumables = True
        plant.enable_ink = True
        plant.ink_gm2_at_100pct_default = 0.0

        surface = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=4,
            print_coverage_percent=80.0,
        )
        spec.surface_profile = surface

        result = compute(spec, plant)
        inks = [rm for rm in result.rm_requirements if rm.role == "ink"]
        self.assertFalse(inks)

    def test_lamination_film_respects_global_consumable_switch(self):
        spec, plant = self._make_spec_and_plant(500.0)
        plant.enable_consumables = False
        plant.enable_lamination_film = True
        spec.surface_profile = SurfaceProfileInput(lamination_required=True)

        result = compute(spec, plant)

        self.assertFalse([rm for rm in result.rm_requirements if rm.product_key == "rm_lamination_film"])

    def test_lamination_required_derives_film_even_when_legacy_film_flag_false(self):
        spec, plant = self._make_spec_and_plant(500.0)
        plant.enable_consumables = True
        plant.enable_lamination_film = False
        spec.surface_profile = SurfaceProfileInput(lamination_required=True)

        result = compute(spec, plant)

        self.assertEqual(
            len([rm for rm in result.rm_requirements if rm.product_key == "rm_lamination_film"]),
            1,
        )

    def test_lamination_film_enabled_once_when_stage_active(self):
        spec, plant = self._make_spec_and_plant(500.0)
        plant.enable_consumables = True
        plant.enable_lamination_film = True
        spec.surface_profile = SurfaceProfileInput(lamination_required=True)

        result = compute(spec, plant)
        films = [rm for rm in result.rm_requirements if rm.product_key == "rm_lamination_film"]

        self.assertEqual(len(films), 1)
        self.assertEqual(films[0].role, "lamination")
        self.assertGreater(films[0].qty_per_fg, 0.0)

    def test_legacy_enable_ink_flag_maps_to_printing_ink(self):
        spec, plant = self._make_spec_and_plant(500.0)
        plant.enable_consumables = True
        plant.enable_ink = True
        plant.ink_gm2_at_100pct_default = 5.0
        spec.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_colors=1,
            print_coverage_percent=50.0,
        )

        result = compute(spec, plant)

        self.assertEqual(len([rm for rm in result.rm_requirements if rm.role == "ink"]), 1)




