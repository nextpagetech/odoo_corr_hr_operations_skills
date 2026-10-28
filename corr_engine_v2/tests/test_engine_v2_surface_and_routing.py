import unittest

from custom_addons.corr_engine_v2.engine_v2 import compute, EngineSpecInput
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.spec_input import (
    SurfaceProfileInput,
    RoutingProfileInput,
    RoutingStageInput,
)


class TestEngineV2SurfaceAndRouting(unittest.TestCase):
    def test_printed_and_laminated_job_rm_routing(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Printed + Laminated Job",
        )
        # Top-sheet GSM still drives mass; surface profile controls routing.
        spec_input.top_sheet_gsm = 150.0
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=3,
            print_coverage_percent=80.0,
            top_sheet_mode="unit_sheet",
            top_sheet_product_id=42,
            lamination_required=True,
            lamination_type_id=7,
            lamination_side="top",
        )

        # Routing profile defines the ordered material / operation stages.
        routing_profile = RoutingProfileInput(
            name="Printed + Laminated Route",
            stages=[
                RoutingStageInput(sequence=10, stage_code="2ply", stage_type="material", waste_percent=0.0),
                RoutingStageInput(sequence=20, stage_code="board", stage_type="material", waste_percent=0.0),
                RoutingStageInput(sequence=30, stage_code="lamination", stage_type="material", stage_semantic="lamination", waste_percent=0.0),
                RoutingStageInput(sequence=40, stage_code="diecut", stage_type="operation", waste_percent=0.0),
                RoutingStageInput(sequence=50, stage_code="fg", stage_type="material", stage_semantic="fg", output_kind="fg", is_fg_stage=True, waste_percent=0.0),
            ],
        )
        spec_input.routing_profile = routing_profile

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="SURF",
            name="Surface Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        rm_keys = {(rm.role, rm.product_key, rm.stage_key) for rm in result.rm_requirements}

        # StageGraph semantics: routing_profile drives the full ordered
        # route; lamination remains an explicit material stage for RM
        # routing.
        stage_keys = [s.key for s in result.stage_graph.stages]
        self.assertEqual(stage_keys, ["2ply", "board", "lamination", "diecut", "fg"])
        lam_stage = next(s for s in result.stage_graph.stages if s.key == "lamination")
        self.assertEqual(lam_stage.logical_role, "lamination")
        self.assertEqual(lam_stage.stage_type, "material")
        diecut_stage = next(s for s in result.stage_graph.stages if s.key == "diecut")
        self.assertEqual(diecut_stage.stage_type, "operation")

        # With top_sheet_mode=unit_sheet and printing_required=True, expect
        # an external top-sheet RM and a lamination film RM.
        self.assertIn(("top_sheet_external", "rm_top_sheet_external", "board"), rm_keys)
        self.assertIn(("lamination", "rm_lamination_film", "lamination"), rm_keys)

    def test_routing_profile_overrides_plant_stage_sequence(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=2,
            spec_name="Routing Profile Spec",
        )

        routing_profile = RoutingProfileInput(
            name="Custom Route",
            stages=[
                RoutingStageInput(sequence=10, stage_code="2ply", stage_type="material", stage_semantic="corrugation", waste_percent=0.0),
                RoutingStageInput(sequence=20, stage_code="board", stage_type="material", stage_semantic="conversion", waste_percent=0.0),
                RoutingStageInput(sequence=30, stage_code="diecut", stage_type="operation", stage_semantic="diecut", waste_percent=0.0),
                RoutingStageInput(sequence=40, stage_code="fg", stage_type="material", stage_semantic="fg", output_kind="fg", is_fg_stage=True, waste_percent=0.0),
            ],
        )
        spec_input.routing_profile = routing_profile

        # Plant stages differ from routing profile; routing should win.
        plant = PlantProfile(
            stages=["2ply", "ts_print", "board", "fg"],
            code="ROUTE_PLANT",
            name="Routing Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        stage_keys = [s.key for s in result.stage_graph.stages]

        # RoutingProfileInput defines the full ordered route; diecut is
        # represented explicitly as an operation stage and still
        # attached to the board material stage for RM semantics.
        self.assertEqual(stage_keys, ["2ply", "board", "diecut", "fg"])
        board_stage = next(s for s in result.stage_graph.stages if s.key == "board")
        # When a RoutingProfileInput is present, its stage_type field
        # controls material vs operation semantics: diecut is an
        # operation attached to the nearest material stage (board),
        # not a separate material node in the StageGraph.
        self.assertIn("diecut", board_stage.operations)

    def test_plain_carton_drops_printing_operations(self):
        """Plain carton: no printing operation even if plant has ts_print."""

        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=3,
            spec_name="Plain Carton",
        )
        # Surface profile explicitly disables printing.
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=False,
            top_sheet_mode="unit_sheet",
        )

        plant = PlantProfile(
            stages=["2ply", "board", "ts_print", "diecut", "fg"],
            code="PLAIN",
            name="Plain Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        stage_keys = [s.key for s in result.stage_graph.stages]
        # Default material chain only; printing is treated as an operation.
        self.assertEqual(stage_keys, ["2ply", "board", "fg"])

        board_stage = next(s for s in result.stage_graph.stages if s.key == "board")
        ops_norm = [o.lower() for o in board_stage.operations]
        self.assertNotIn("ts_print", ops_norm)

    def test_printed_carton_keeps_printing_operations(self):
        """Printed carton: printing operation is kept when required."""

        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=4,
            spec_name="Printed Carton",
        )
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            top_sheet_mode="unit_sheet",
        )

        plant = PlantProfile(
            stages=["2ply", "board", "ts_print", "diecut", "fg"],
            code="PRINTED",
            name="Printed Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        stage_keys = [s.key for s in result.stage_graph.stages]
        self.assertEqual(stage_keys, ["2ply", "board", "fg"])

        board_stage = next(s for s in result.stage_graph.stages if s.key == "board")
        ops_norm = [o.lower() for o in board_stage.operations]
        self.assertIn("ts_print", ops_norm)

    def test_routing_profile_without_2ply_merges_board_rms(self):
        """Routing profile with board/fg only: board RMs attach to board stage."""

        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=5,
            spec_name="No 2ply Route",
        )

        routing_profile = RoutingProfileInput(
            name="No 2ply",
            stages=[
                RoutingStageInput(sequence=10, stage_code="board", stage_type="material", waste_percent=0.0),
                RoutingStageInput(sequence=20, stage_code="diecut", stage_type="operation", waste_percent=0.0),
                RoutingStageInput(sequence=30, stage_code="fg", stage_type="material", waste_percent=0.0),
            ],
        )
        spec_input.routing_profile = routing_profile

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="NO2PLY",
            name="No 2ply Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        stage_keys = [s.key for s in result.stage_graph.stages]
        self.assertEqual(stage_keys, ["board", "diecut", "fg"])

        rm_stage_pairs = {(rm.role, rm.stage_key) for rm in result.rm_requirements}
        # Liner and medium board RMs must attach to the board material stage.
        self.assertIn(("liner", "board"), rm_stage_pairs)
        self.assertIn(("medium", "board"), rm_stage_pairs)

    def test_printing_first_route_attaches_operations_and_rms(self):
        """Printing-first routing: printing ops and surface RMs attach to first material stage."""

        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=6,
            spec_name="Printing First Route",
        )
        spec_input.top_sheet_gsm = 150.0
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=2,
            print_coverage_percent=60.0,
            top_sheet_mode="roll",
        )

        routing_profile = RoutingProfileInput(
            name="Printing First",
            stages=[
                RoutingStageInput(sequence=10, stage_code="ts_cut", stage_type="operation", waste_percent=0.0),
                RoutingStageInput(sequence=20, stage_code="ts_print", stage_type="operation", waste_percent=0.0),
                RoutingStageInput(sequence=30, stage_code="2ply", stage_type="material", stage_semantic="corrugation", waste_percent=0.0),
                RoutingStageInput(sequence=40, stage_code="board", stage_type="material", stage_semantic="conversion", waste_percent=0.0),
                RoutingStageInput(sequence=50, stage_code="diecut", stage_type="operation", waste_percent=0.0),
                RoutingStageInput(sequence=60, stage_code="fg", stage_type="material", stage_semantic="fg", output_kind="fg", is_fg_stage=True, waste_percent=0.0),
            ],
        )
        spec_input.routing_profile = routing_profile

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PRINT_FIRST",
            name="Printing First Plant",
            printing_ups=1,
            diecut_ups=1,
        )
        # Enable ink so we see both top_roll and ink RMs.
        plant.enable_consumables = True
        plant.enable_adhesive = False
        plant.enable_ink = True
        plant.ink_gm2_at_100pct_default = 5.0

        result = compute(spec_input, plant)

        stage_keys = [s.key for s in result.stage_graph.stages]
        self.assertEqual(stage_keys, ["ts_cut", "ts_print", "2ply", "board", "diecut", "fg"])

        stage_2ply = next(s for s in result.stage_graph.stages if s.key == "2ply")
        ops_norm_2ply = [o.lower() for o in stage_2ply.operations]
        self.assertIn("ts_cut", ops_norm_2ply)
        self.assertIn("ts_print", ops_norm_2ply)

        board_stage = next(s for s in result.stage_graph.stages if s.key == "board")
        ops_norm_board = [o.lower() for o in board_stage.operations]
        self.assertIn("diecut", ops_norm_board)

        rm_roles = {(rm.role, rm.stage_key) for rm in result.rm_requirements}
        # Stack-driven roll mode moves the printable top liner into
        # rm_top_roll; the remaining board RM stays on the first
        # material stage together with surface consumables.
        self.assertIn(("medium", "2ply"), rm_roles)
        self.assertIn(("top_roll", "2ply"), rm_roles)
        self.assertIn(("ink", "2ply"), rm_roles)
        # No RM should use a process-only ts_print stage key.
        self.assertNotIn(("top_roll", "ts_print"), rm_roles)

    def test_single_stage_fg_route_attaches_all_rms_to_fg(self):
        """Single-stage routing: all RMs attach to the FG material stage."""

        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=7,
            spec_name="Single Stage FG Route",
        )

        routing_profile = RoutingProfileInput(
            name="Single FG Stage",
            stages=[
                RoutingStageInput(sequence=10, stage_code="fg", stage_type="material", stage_semantic="fg", output_kind="fg", is_fg_stage=True, waste_percent=0.0),
            ],
        )
        spec_input.routing_profile = routing_profile

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="FG_ONLY",
            name="FG Only Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        stage_keys = [s.key for s in result.stage_graph.stages]
        self.assertEqual(stage_keys, ["fg"])

        rm_stage_keys = {rm.stage_key for rm in result.rm_requirements if rm.stage_key is not None}
        # All material RMs (liner/medium) must be routed to the single
        # FG material stage.
        self.assertEqual(rm_stage_keys, {"fg"})

    def test_cutting_printing_board_fg_route_drives_stage_flow_and_consumables(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=8,
            spec_name="Cutting Printing Board FG",
        )
        spec_input.top_sheet_gsm = 150.0
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=2,
            print_coverage_percent=70.0,
            top_sheet_mode="unit_sheet",
        )

        class LockingBoth:
            def __init__(self):
                self.method = "both"
                self.wire_stitches_per_cm = 1.2
                self.wire_g_per_stitch = 0.04

        spec_input.locking_type = LockingBoth()

        spec_input.routing_profile = RoutingProfileInput(
            name="Cutting Printing Board FG",
            stages=[
                RoutingStageInput(sequence=10, stage_code="cutting", stage_type="operation", stage_semantic="auxiliary", waste_percent=0.0),
                RoutingStageInput(sequence=20, stage_code="printing", stage_type="operation", stage_semantic="printing", waste_percent=0.0),
                RoutingStageInput(sequence=30, stage_code="board_stage_a", stage_type="material", stage_semantic="conversion", output_kind="board", waste_percent=0.0),
                RoutingStageInput(sequence=40, stage_code="fg_stage_a", stage_type="material", stage_semantic="fg", output_kind="box", is_fg_stage=True, waste_percent=0.0),
            ],
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="CUT_PRINT",
            name="Cut Print Plant",
            printing_ups=1,
            diecut_ups=1,
        )
        plant.enable_consumables = True
        plant.enable_adhesive = True
        plant.enable_ink = True
        plant.enable_wire = True
        plant.adhesive_factor_board_kg_per_m2 = 0.2
        plant.adhesive_factor_fg_kg_per_m2 = 0.1
        plant.ink_gm2_at_100pct_default = 5.0

        result = compute(spec_input, plant)

        self.assertEqual([s.key for s in result.stage_graph.stages], ["cutting", "printing", "board_stage_a", "fg_stage_a"])
        self.assertEqual(result.stage_graph.final_stage_key(), "fg_stage_a")

        board_stage = next(s for s in result.stage_graph.stages if s.key == "board_stage_a")
        self.assertEqual(board_stage.logical_role, "conversion")
        self.assertIn("cutting", board_stage.operations)
        self.assertIn("printing", board_stage.operations)

        rm_roles = {(rm.role, rm.stage_key) for rm in result.rm_requirements}
        self.assertIn(("top_sheet_external", "board_stage_a"), rm_roles)
        self.assertIn(("ink", "board_stage_a"), rm_roles)
        self.assertIn(("adhesive_board", "board_stage_a"), rm_roles)
        self.assertIn(("adhesive_fg", "fg_stage_a"), rm_roles)

    def test_custom_final_material_stage_is_used_as_terminal_stage(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=9,
            spec_name="Custom Final Route",
        )

        class LockingBoth:
            def __init__(self):
                self.method = "both"
                self.wire_stitches_per_cm = 1.0
                self.wire_g_per_stitch = 0.05

        spec_input.locking_type = LockingBoth()
        spec_input.routing_profile = RoutingProfileInput(
            name="Custom Final Route",
            stages=[
                RoutingStageInput(sequence=10, stage_code="corr_a", stage_type="material", stage_semantic="corrugation", output_kind="board", waste_percent=0.0),
                RoutingStageInput(sequence=20, stage_code="close_joint_a", stage_type="material", stage_semantic="conversion", output_kind="box", can_be_terminal=True, waste_percent=0.0),
            ],
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="CUSTOM_FINAL",
            name="Custom Final Plant",
            printing_ups=1,
            diecut_ups=1,
        )
        plant.enable_consumables = True
        plant.enable_adhesive = True
        plant.enable_wire = True
        plant.adhesive_factor_fg_kg_per_m2 = 0.1

        result = compute(spec_input, plant)

        self.assertEqual([s.key for s in result.stage_graph.stages], ["corr_a", "close_joint_a"])
        self.assertEqual(result.stage_graph.final_stage_key(), "close_joint_a")
        final_stage = next(s for s in result.stage_graph.stages if s.key == "close_joint_a")
        self.assertTrue(final_stage.is_terminal)

        final_edge = next(e for e in result.stage_graph.flows if e.to_stage == "close_joint_a")
        self.assertEqual(final_edge.product_key, "fg_box")

        product_keys = {p.key for p in result.products}
        self.assertIn("fg_box", product_keys)
        self.assertNotIn("sfg_close_joint_a", product_keys)

        rm_roles = {(rm.role, rm.stage_key) for rm in result.rm_requirements}
        self.assertIn(("adhesive_fg", "close_joint_a"), rm_roles)

    def test_trace_uses_full_routing_profile_stage_chain(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=10,
            spec_name="Trace Route Alignment",
        )
        spec_input.routing_profile = RoutingProfileInput(
            name="Semi Auto Trace",
            stages=[
                RoutingStageInput(sequence=10, stage_code="2ply", stage_type="material", stage_semantic="corrugation", waste_percent=0.0),
                RoutingStageInput(sequence=20, stage_code="board", stage_type="material", stage_semantic="conversion", output_kind="board", waste_percent=0.0),
                RoutingStageInput(sequence=30, stage_code="ts_print", stage_type="operation", stage_semantic="printing", waste_percent=0.0),
                RoutingStageInput(sequence=40, stage_code="seaming", stage_type="material", stage_semantic="conversion", output_kind="box", can_be_terminal=True, waste_percent=0.0),
            ],
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="TRACE",
            name="Trace Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        trace_map = {name: payload for name, payload in (result.trace or [])}

        self.assertEqual(
            trace_map["stage_chain_resolved"]["stages"],
            ["2ply", "board", "ts_print", "seaming"],
        )
        self.assertEqual(
            trace_map["stage_graph_created"]["stages"],
            ["2ply", "board", "ts_print", "seaming"],
        )
        self.assertEqual(
            trace_map["stage_chain_resolved"]["material_flow"],
            [
                ("2ply", "board", "sfg_board"),
                ("board", "ts_print", "sfg_board"),
                ("ts_print", "seaming", "fg_box"),
            ],
        )
        self.assertEqual(result.stage_graph.final_stage_key(), "seaming")




