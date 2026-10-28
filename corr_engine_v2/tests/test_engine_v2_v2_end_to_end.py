import unittest

from custom_addons.corr_engine_v2.engine_v2 import compute, EngineSpecInput
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.spec_input import (
    GeometrySpec,
    BoardLayerInput,
    SurfaceProfileInput,
    LayoutPolicyInput,
    RoutingProfileInput,
    RoutingStageInput,
    EngineOverrides,
)


class TestEngineV2FullV2Scenario(unittest.TestCase):
    def test_full_v2_spec_end_to_end(self):
        # Geometry-driven 0201 box with typed V2 inputs.
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0 + 200.0,
            medium_gsm=120.0,
            style_code="0201",
            sfg_mode="staged",
            spec_id=100,
            spec_name="V2 Full Scenario",
        )

        # GeometrySpec as primary source of box dimensions.
        spec_input.geometry = GeometrySpec(
            style_id=1,
            box_length_mm=300.0,
            box_width_mm=200.0,
            box_height_mm=150.0,
            glue_flap_override_mm=20.0,
            plant_code="V2PLANT",
            plant_id=None,
        )

        # V2 board layers with rich roles; liner_gsm / medium_gsm above
        # are consistent with these values.
        spec_input.board_layers_v2 = [
            BoardLayerInput(
                sequence=10,
                role="outer_liner",
                gsm=180.0,
                bf=30.0,
                color="Kraft",
                flute_type_code="B",
                material_code="L180",
                source_type="manual",
            ),
            BoardLayerInput(
                sequence=20,
                role="medium",
                gsm=120.0,
                bf=26.0,
                color="Kraft",
                flute_type_code="B",
                material_code="M120",
                source_type="manual",
            ),
            BoardLayerInput(
                sequence=30,
                role="inner_liner",
                gsm=200.0,
                bf=35.0,
                color="White",
                flute_type_code="B",
                material_code="L200",
                source_type="manual",
            ),
        ]

        # Surface profile: printed + laminated.
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=3,
            print_coverage_percent=75.0,
            top_sheet_mode="unit_sheet",
            top_sheet_product_id=42,
            lamination_required=True,
            lamination_type_id=7,
            lamination_side="top",
        )
        # Top-sheet GSM used for RM mass.
        spec_input.top_sheet_gsm = 150.0

        # Layout policy: plant-driven sheet, rotation allowed.
        spec_input.layout_policy = LayoutPolicyInput(
            allow_rotation=True,
            preferred_ups=4,
            max_ups=8,
            layout_mode="optimize",
            sheet_source="plant_default",
            trim_margin_mm=0.0,
            gripper_margin_mm=0.0,
        )

        # Routing profile: material stages 2ply -> board, with printing
        # and diecut as operations attached to board.
        spec_input.routing_profile = RoutingProfileInput(
            name="V2 Routing",
            stages=[
                RoutingStageInput(
                    sequence=10,
                    stage_code="2ply",
                    stage_type="material",
                    stage_semantic="corrugation",
                    waste_percent=0.0,
                ),
                RoutingStageInput(
                    sequence=20,
                    stage_code="board",
                    stage_type="material",
                    stage_semantic="conversion",
                    waste_percent=0.0,
                ),
                RoutingStageInput(
                    sequence=30,
                    stage_code="ts_print",
                    stage_type="operation",
                    stage_semantic="printing",
                    waste_percent=0.0,
                ),
                RoutingStageInput(
                    sequence=40,
                    stage_code="diecut",
                    stage_type="operation",
                    stage_semantic="diecut",
                    waste_percent=0.0,
                ),
                RoutingStageInput(
                    sequence=50,
                    stage_code="fg",
                    stage_type="material",
                    stage_semantic="fg",
                    output_kind="fg",
                    is_fg_stage=True,
                    waste_percent=0.0,
                ),
            ],
        )

        # Engine overrides: no forced UPS, but kept for completeness.
        spec_input.overrides = EngineOverrides(
            manual_sheet_length_mm=0.0,
            manual_sheet_width_mm=0.0,
            forced_ups=None,
            ups_enforcement="soft",
        )

        # Plant profile with sheet dimensions and V2 split settings.
        plant = PlantProfile(
            stages=["ts_cut", "ts_print", "lamination", "board", "diecut", "fg"],
            code="V2PLANT",
            name="V2 Plant",
            printing_ups=4,
            diecut_ups=4,
            allow_blank_rotation=True,
            sheet_length_mm=2000.0,
            sheet_width_mm=1200.0,
        )
        # V2 splitting: no split required as limit is wide.
        plant.max_machine_width_mm = 2000.0
        plant.split_mode = "none"

        result = compute(spec_input, plant)
        metrics = result.metrics
        extra = metrics.extra or {}

        # 1) Blank and geometry
        expected_blank_length = 2 * (300.0 + 200.0) + 20.0
        expected_blank_width = 200.0 + 150.0 + 20.0
        expected_m2_per_box = (expected_blank_length * expected_blank_width) / 1_000_000.0
        self.assertAlmostEqual(metrics.m2_per_box, expected_m2_per_box, places=6)

        # 2) Layout result (same geometry as 0201 small box snapshot).
        layout_blanks_per_sheet = int(extra.get("layout_blanks_per_sheet", 0) or 0)
        boxes_per_sheet = float(extra.get("boxes_per_sheet", 0.0) or 0.0)
        self.assertEqual(layout_blanks_per_sheet, 5)
        self.assertEqual(boxes_per_sheet, 5.0)

        # 3) Split result (no split in this scenario).
        pieces_per_box = int(extra.get("pieces_per_box", 1) or 1)
        self.assertEqual(pieces_per_box, 1)

        # 4) RM breakdown: liner, medium, top-sheet, lamination.
        rm_roles = {(rm.role, rm.product_key, rm.stage_key) for rm in result.rm_requirements}
        self.assertIn(("liner", "rm_liner", "2ply"), rm_roles)
        self.assertIn(("medium", "rm_medium", "2ply"), rm_roles)
        # Top-sheet and lamination RM from surface profile.
        self.assertIn(("top_sheet_external", "rm_top_sheet_external", "board"), rm_roles)
        # Lamination may attach to 'lamination' or 'board' depending on
        # stage configuration; assert presence without hard-coding stage.
        lamination_entries = [
            r for r in rm_roles if r[0] == "lamination" and r[1] == "rm_lamination_film"
        ]
        self.assertTrue(lamination_entries)

        # 5) Stage flow and operation attachment.
        stage_keys = [s.key for s in result.stage_graph.stages]
        self.assertEqual(stage_keys, ["2ply", "board", "ts_print", "diecut", "fg"])
        board_stage = next(s for s in result.stage_graph.stages if s.key == "board")
        # Operations from routing profile should attach to 'board'.
        self.assertIn("ts_print", board_stage.operations)
        self.assertIn("diecut", board_stage.operations)

    def test_full_v2_5ply_roll_and_unit_sheet(self):
        """Validate 5-ply roll vs unit-sheet top-layer handling for V2 specs."""

        # Common geometry and board stack (5-ply: outer, med, center, med, inner).
        def _make_spec_input(top_mode: str):
            spec_input = EngineSpecInput(
                blank_length_mm=0.0,
                blank_width_mm=0.0,
                liner_gsm=180.0 + 200.0 + 160.0,
                medium_gsm=120.0 + 120.0,
                style_code="0201",
                sfg_mode="staged",
                spec_id=200 if top_mode == "roll" else 201,
                spec_name="5-ply Roll" if top_mode == "roll" else "5-ply Unit Sheet",
            )
            spec_input.geometry = GeometrySpec(
                style_id=1,
                box_length_mm=300.0,
                box_width_mm=200.0,
                box_height_mm=150.0,
                glue_flap_override_mm=20.0,
                plant_code="V2PLANT",
                plant_id=None,
            )
            spec_input.board_layers_v2 = [
                BoardLayerInput(10, "outer_liner", 180.0, 30.0, "Kraft", "B", "L180", "manual"),
                BoardLayerInput(20, "medium", 120.0, 26.0, "Kraft", "B", "M120", "manual"),
                BoardLayerInput(30, "center_liner", 160.0, 32.0, "Kraft", "B", "L160", "manual"),
                BoardLayerInput(40, "medium", 120.0, 26.0, "Kraft", "B", "M120", "manual"),
                BoardLayerInput(50, "inner_liner", 200.0, 35.0, "White", "B", "L200", "manual"),
            ]
            if top_mode == "roll":
                spec_input.surface_profile = SurfaceProfileInput(
                    printing_required=True,
                    print_process="flexo",
                    print_colors=4,
                    print_coverage_percent=80.0,
                    top_sheet_mode="roll",
                    top_sheet_product_id=None,
                )
            else:
                spec_input.surface_profile = SurfaceProfileInput(
                    printing_required=True,
                    print_process="flexo",
                    print_colors=4,
                    print_coverage_percent=80.0,
                    top_sheet_mode="unit_sheet",
                    top_sheet_product_id=99,
                    top_sheet_gsm=150.0,
                )
            return spec_input

        plant = PlantProfile(
            stages=["ts_print", "board", "diecut", "fg"],
            code="V2PLANT",
            name="V2 Plant",
            printing_ups=4,
            diecut_ups=4,
            allow_blank_rotation=True,
            sheet_length_mm=2000.0,
            sheet_width_mm=1200.0,
        )

        # 5-ply roll: top liner becomes rm_top_roll, remaining 4 layers stay as board RMs.
        spec_roll = _make_spec_input("roll")
        result_roll = compute(spec_roll, plant)
        rm_roles_roll = {(rm.role, rm.product_key, rm.stage_key) for rm in result_roll.rm_requirements}
        # Roll-mode top sheet is routed via the printing material stage
        # (board) rather than a ts_print process stage.
        self.assertIn(("top_roll", "rm_top_roll", "board"), rm_roles_roll)
        # Board RMs stay on the corrugation-capable material stage. In
        # this plant-only route that stage resolves to the board stage.
        board_rms = [rm for rm in result_roll.rm_requirements if rm.stage_key == "board"]
        roles_board = {rm.role for rm in board_rms}
        self.assertIn("liner", roles_board)
        self.assertIn("medium", roles_board)

        # 5-ply unit_sheet: external top-sheet replaces top printable liner.
        spec_unit = _make_spec_input("unit_sheet")
        result_unit = compute(spec_unit, plant)
        rm_roles_unit = {(rm.role, rm.product_key, rm.stage_key) for rm in result_unit.rm_requirements}
        self.assertIn(("top_sheet_external", "rm_top_sheet_external", "board"), rm_roles_unit)
        self.assertNotIn(("top_roll", "rm_top_roll", "ts_print"), rm_roles_unit)

        # 6) Basic trace coverage: ensure key steps are present.
        trace_steps = [t[0] for t in (result_roll.trace or [])]
        self.assertIn("compute_started", trace_steps)
        self.assertIn("style_resolved", trace_steps)
        self.assertIn("layout_resolved", trace_steps)




