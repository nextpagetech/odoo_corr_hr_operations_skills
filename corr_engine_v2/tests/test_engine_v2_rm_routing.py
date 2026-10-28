import unittest

from custom_addons.corr_engine_v2.engine_v2 import compute, EngineSpecInput
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.spec_input import (
    RoutingProfileInput,
    RoutingStageInput,
    SurfaceProfileInput,
)


class TestEngineV2RmRouting(unittest.TestCase):
    def _spec_with_layers(self, name, layers, surface_profile=None):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=sum(layer["gsm"] for layer in layers if layer["type"] == "liner"),
            medium_gsm=sum(layer["gsm"] for layer in layers if layer["type"] == "medium"),
            style_code="",
            sfg_mode="staged",
            spec_id=100,
            spec_name=name,
        )
        spec_input.board_layers = layers
        spec_input.surface_profile = surface_profile
        return spec_input

    def _standard_plant(self):
        return PlantProfile(
            stages=["2ply", "board", "fg"],
            code="STD",
            name="Standard Plant",
            printing_ups=1,
            diecut_ups=1,
        )

    def _paper_layer_rms(self, result):
        return [
            rm
            for rm in result.rm_requirements
            if rm.role in ("liner", "medium", "top_roll", "top_sheet_external")
        ]

    def _assert_top_sheet_layer_model(self, result, *, top_role, ply_count, replaces_liner=True):
        paper = self._paper_layer_rms(result)
        roles = [rm.role for rm in paper]
        expected_medium_count = (ply_count - 1) // 2
        expected_remaining_liner_count = (ply_count - 1) // 2
        self.assertEqual(len(paper), ply_count)
        self.assertEqual(roles.count(top_role), 1)
        self.assertEqual(roles.count("medium"), expected_medium_count)
        self.assertEqual(roles.count("liner"), expected_remaining_liner_count)
        if replaces_liner:
            self.assertFalse(
                any(rm.role == "liner" and rm.layer_no == 1 for rm in paper),
                "top liner layer must be replaced, not kept",
            )
            expected_liner_layers = range(3, ply_count + 1, 2)
        else:
            expected_liner_layers = range(3, ply_count + 1, 2)
        for layer_no in expected_liner_layers:
            self.assertTrue(
                any(rm.role == "liner" and rm.layer_no == layer_no for rm in paper),
                "base/non-top liner layer %s must remain" % layer_no,
            )

    def test_corrugation_rm_routing_2ply(self):
        # Spec with 2-ply legacy fields
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="2ply Routing",
        )

        plant_profile = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="TEST",
            name="Test Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant_profile)
        rm_by_stage = {}
        for rm in result.rm_requirements:
            rm_by_stage.setdefault(rm.stage_key, []).append(rm)

        # In split corrugation/board routes, the top surface liner is assigned
        # to the board material stage while corrugation receives the medium.
        stage_2ply = rm_by_stage.get("2ply", [])
        roles = {rm.role for rm in stage_2ply}
        self.assertIn("medium", roles)
        self.assertIn("liner", {rm.role for rm in rm_by_stage.get("board", [])})

    def test_scalar_top_sheet_gsm_without_surface_profile_does_not_create_top_sheet(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Top Sheet Routing",
        )
        spec_input.top_sheet_gsm = 150.0

        plant = PlantProfile(
            stages=["2ply", "board", "diecut", "fg"],
            code="EXT",
            name="External Sheet Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        roles = {rm.role for rm in result.rm_requirements}
        products = {rm.product_key for rm in result.rm_requirements}
        self.assertIn("liner", roles)
        self.assertIn("medium", roles)
        self.assertNotIn("top_roll", roles)
        self.assertNotIn("top_sheet_external", roles)
        self.assertNotIn("rm_top_roll", products)
        self.assertNotIn("rm_top_sheet_external", products)

    def test_3ply_without_top_sheet_has_three_paper_layers(self):
        spec_input = self._spec_with_layers(
            "3ply No Top Sheet",
            [
                {"type": "liner", "gsm": 180.0, "layer_no": 1},
                {"type": "medium", "gsm": 150.0, "layer_no": 2},
                {"type": "liner", "gsm": 180.0, "layer_no": 3},
            ],
        )

        result = compute(spec_input, self._standard_plant())
        paper = self._paper_layer_rms(result)
        roles = [rm.role for rm in paper]
        self.assertEqual(len(paper), 3)
        self.assertEqual(roles.count("liner"), 2)
        self.assertEqual(roles.count("medium"), 1)
        self.assertNotIn("top_roll", roles)
        self.assertNotIn("top_sheet_external", roles)

    def test_3ply_roll_top_sheet_replaces_top_liner(self):
        spec_input = self._spec_with_layers(
            "3ply Roll Top Sheet",
            [
                {"type": "liner", "gsm": 180.0, "layer_no": 1},
                {"type": "medium", "gsm": 150.0, "layer_no": 2},
                {"type": "liner", "gsm": 180.0, "layer_no": 3},
            ],
            SurfaceProfileInput(
                printing_required=True,
                top_sheet_mode="roll",
                print_process="flexo",
                print_colors=2,
                print_coverage_percent=50.0,
            ),
        )

        result = compute(spec_input, self._standard_plant())
        paper = self._paper_layer_rms(result)
        roles = [rm.role for rm in paper]
        self.assertEqual(len(paper), 3)
        self.assertEqual(roles.count("top_roll"), 1)
        self.assertEqual(roles.count("liner"), 1)
        self.assertEqual(roles.count("medium"), 1)
        top = next(rm for rm in paper if rm.role == "top_roll")
        self.assertEqual(top.layer_no, 1)

    def test_3ply_unit_top_sheet_replaces_top_liner(self):
        spec_input = self._spec_with_layers(
            "3ply Unit Top Sheet",
            [
                {"type": "medium", "gsm": 150.0, "layer_no": 1},
                {"type": "liner", "gsm": 180.0, "layer_no": 2},
            ],
            SurfaceProfileInput(
                printing_required=True,
                top_sheet_mode="unit_sheet",
                top_sheet_product_id=42,
                top_sheet_gsm=200.0,
            ),
        )

        result = compute(spec_input, self._standard_plant())
        paper = self._paper_layer_rms(result)
        roles = [rm.role for rm in paper]
        self.assertEqual(len(paper), 3)
        self.assertEqual(roles.count("top_sheet_external"), 1)
        self.assertEqual(roles.count("liner"), 1)
        self.assertEqual(roles.count("medium"), 1)
        top = next(rm for rm in paper if rm.role == "top_sheet_external")
        self.assertEqual(top.layer_no, 1)
        self.assertTrue(any(rm.role == "medium" and rm.layer_no == 2 for rm in paper))
        self.assertTrue(any(rm.role == "liner" and rm.layer_no == 3 for rm in paper))

    def test_5ply_unit_top_sheet_total_layer_count_remains_five(self):
        spec_input = self._spec_with_layers(
            "5ply Unit Top Sheet",
            [
                {"type": "medium", "gsm": 150.0, "layer_no": 1},
                {"type": "liner", "gsm": 180.0, "layer_no": 2},
                {"type": "medium", "gsm": 150.0, "layer_no": 3},
                {"type": "liner", "gsm": 180.0, "layer_no": 4},
            ],
            SurfaceProfileInput(
                printing_required=True,
                top_sheet_mode="unit_sheet",
                top_sheet_product_id=42,
                top_sheet_gsm=200.0,
            ),
        )

        result = compute(spec_input, self._standard_plant())
        self._assert_top_sheet_layer_model(
            result,
            top_role="top_sheet_external",
            ply_count=5,
            replaces_liner=False,
        )

    def test_5ply_roll_top_sheet_total_layer_count_remains_five(self):
        spec_input = self._spec_with_layers(
            "5ply Roll Top Sheet",
            [
                {"type": "liner", "gsm": 180.0, "layer_no": 1},
                {"type": "medium", "gsm": 150.0, "layer_no": 2},
                {"type": "liner", "gsm": 180.0, "layer_no": 3},
                {"type": "medium", "gsm": 150.0, "layer_no": 4},
                {"type": "liner", "gsm": 180.0, "layer_no": 5},
            ],
            SurfaceProfileInput(
                printing_required=True,
                top_sheet_mode="roll",
                print_process="flexo",
                print_colors=2,
                print_coverage_percent=50.0,
            ),
        )

        result = compute(spec_input, self._standard_plant())
        self._assert_top_sheet_layer_model(result, top_role="top_roll", ply_count=5)

    def test_5ply_without_top_sheet_routes_first_liner_to_board_stage(self):
        spec_input = self._spec_with_layers(
            "5ply Board Surface Liner",
            [
                {"type": "liner", "gsm": 180.0, "layer_no": 1},
                {"type": "medium", "gsm": 140.0, "layer_no": 2},
                {"type": "liner", "gsm": 140.0, "layer_no": 3},
                {"type": "medium", "gsm": 140.0, "layer_no": 4},
                {"type": "liner", "gsm": 140.0, "layer_no": 5},
            ],
        )

        result = compute(spec_input, self._standard_plant())
        stage_layer_roles = sorted(
            (rm.stage_key, rm.role, rm.layer_no)
            for rm in result.rm_requirements
            if rm.role in ("liner", "medium")
        )

        self.assertIn(("board", "liner", 1), stage_layer_roles)
        self.assertIn(("2ply", "medium", 2), stage_layer_roles)
        self.assertIn(("2ply", "liner", 3), stage_layer_roles)
        self.assertIn(("2ply", "medium", 4), stage_layer_roles)
        self.assertIn(("2ply", "liner", 5), stage_layer_roles)
        self.assertNotIn(("2ply", "liner", 1), stage_layer_roles)

    def test_7ply_roll_top_sheet_total_layer_count_remains_seven(self):
        spec_input = self._spec_with_layers(
            "7ply Roll Top Sheet",
            [
                {"type": "liner", "gsm": 180.0, "layer_no": 1},
                {"type": "medium", "gsm": 150.0, "layer_no": 2},
                {"type": "liner", "gsm": 180.0, "layer_no": 3},
                {"type": "medium", "gsm": 150.0, "layer_no": 4},
                {"type": "liner", "gsm": 180.0, "layer_no": 5},
                {"type": "medium", "gsm": 150.0, "layer_no": 6},
                {"type": "liner", "gsm": 180.0, "layer_no": 7},
            ],
            SurfaceProfileInput(
                printing_required=True,
                top_sheet_mode="roll",
                print_process="flexo",
                print_colors=2,
                print_coverage_percent=50.0,
            ),
        )

        result = compute(spec_input, self._standard_plant())
        self._assert_top_sheet_layer_model(result, top_role="top_roll", ply_count=7)

    def test_7ply_unit_top_sheet_total_layer_count_remains_seven(self):
        spec_input = self._spec_with_layers(
            "7ply Unit Top Sheet",
            [
                {"type": "medium", "gsm": 150.0, "layer_no": 1},
                {"type": "liner", "gsm": 180.0, "layer_no": 2},
                {"type": "medium", "gsm": 150.0, "layer_no": 3},
                {"type": "liner", "gsm": 180.0, "layer_no": 4},
                {"type": "medium", "gsm": 150.0, "layer_no": 5},
                {"type": "liner", "gsm": 180.0, "layer_no": 6},
            ],
            SurfaceProfileInput(
                printing_required=True,
                top_sheet_mode="unit_sheet",
                top_sheet_product_id=42,
                top_sheet_gsm=200.0,
            ),
        )

        result = compute(spec_input, self._standard_plant())
        self._assert_top_sheet_layer_model(
            result,
            top_role="top_sheet_external",
            ply_count=7,
            replaces_liner=False,
        )

    def test_unit_sheet_with_product_uses_surface_gsm_and_metadata(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=2,
            spec_name="Unit Sheet Product-First",
        )
        # Legacy scalar fallback GSM.
        spec_input.top_sheet_gsm = 150.0
        # Surface profile carries product-derived GSM and selected product.
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=3,
            print_coverage_percent=80.0,
            top_sheet_mode="unit_sheet",
            top_sheet_product_id=42,
            top_sheet_gsm=200.0,
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="UNIT",
            name="Unit Sheet Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        top_rms = [
            rm
            for rm in result.rm_requirements
            if rm.role == "top_sheet_external" and rm.product_key == "rm_top_sheet_external"
        ]
        self.assertEqual(len(top_rms), 1)
        rm = top_rms[0]
        self.assertEqual(rm.stage_key, "board")
        self.assertGreater(rm.qty_per_fg, 0.0)
        # GSM must come from the surface/profile (product-derived) value.
        self.assertAlmostEqual(rm.metadata.get("gsm"), 200.0)
        self.assertEqual(rm.metadata.get("top_sheet_mode"), "unit_sheet")
        self.assertEqual(rm.metadata.get("top_sheet_product_id"), 42)

    def test_unit_sheet_with_product_emits_rm_when_printing_not_required(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=22,
            spec_name="Unit Sheet Without Printing",
        )
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=False,
            print_process="",
            print_colors=0,
            print_coverage_percent=0.0,
            top_sheet_mode="unit_sheet",
            top_sheet_product_id=42,
            top_sheet_gsm=0.0,
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="UNIT_NOPRINT",
            name="Unit Sheet No Printing Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        top_rms = [
            rm
            for rm in result.rm_requirements
            if rm.role == "top_sheet_external" and rm.product_key == "rm_top_sheet_external"
        ]
        self.assertEqual(len(top_rms), 1)
        rm = top_rms[0]
        self.assertEqual(rm.stage_key, "board")
        self.assertGreater(rm.qty_per_fg, 0.0)
        self.assertEqual(rm.qty_uom, "unit")
        self.assertEqual(rm.consumption_type, "unit")
        self.assertEqual(rm.metadata.get("top_sheet_mode"), "unit_sheet")
        self.assertEqual(rm.metadata.get("top_sheet_product_id"), 42)

    def test_roll_mode_routes_top_roll_to_cutting_when_printing_not_required(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0 + 140.0,
            medium_gsm=150.0,
            style_code="",
            sfg_mode="staged",
            spec_id=23,
            spec_name="Roll Sheet Without Printing",
        )
        spec_input.board_layers = [
            {"type": "liner", "gsm": 180.0, "layer_no": 1},
            {"type": "medium", "gsm": 150.0, "layer_no": 2},
            {"type": "liner", "gsm": 140.0, "layer_no": 3},
        ]
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=False,
            print_process="",
            print_colors=0,
            print_coverage_percent=0.0,
            top_sheet_mode="roll",
            top_sheet_product_id=None,
        )
        spec_input.routing_profile = RoutingProfileInput(
            name="Top Sheet Cut Board",
            stages=[
                RoutingStageInput(sequence=10, stage_code="top_sheet_cut", stage_type="material", stage_semantic="cutting", output_kind="sheet"),
                RoutingStageInput(sequence=20, stage_code="2ply", stage_type="material", stage_semantic="corrugation", output_kind="sheet"),
                RoutingStageInput(sequence=30, stage_code="board", stage_type="material", stage_semantic="conversion", output_kind="board"),
                RoutingStageInput(sequence=40, stage_code="seaming", stage_type="material", stage_semantic="fg", output_kind="fg", is_fg_stage=True),
            ],
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="ROLL_NOPRINT",
            name="Roll No Printing Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        rm_keys = {(rm.role, rm.product_key, rm.stage_key) for rm in result.rm_requirements}
        self.assertIn(("top_roll", "rm_top_roll", "top_sheet_cut"), rm_keys)
        self.assertNotIn(("top_roll", "rm_top_roll", "board"), rm_keys)
        self.assertFalse(
            any(rm.role == "ink" for rm in result.rm_requirements),
            "printing-disabled roll top sheet must not create ink RM",
        )

    def test_unit_sheet_without_product_uses_scalar_gsm(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=3,
            spec_name="Unit Sheet No Product",
        )
        # Only scalar GSM is available.
        spec_input.top_sheet_gsm = 150.0
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=2,
            print_coverage_percent=60.0,
            top_sheet_mode="unit_sheet",
            top_sheet_product_id=None,
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="UNIT_NOPROD",
            name="Unit Sheet No Product Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        top_rms = [
            rm
            for rm in result.rm_requirements
            if rm.role == "top_sheet_external" and rm.product_key == "rm_top_sheet_external"
        ]
        self.assertEqual(len(top_rms), 1)
        rm = top_rms[0]
        self.assertEqual(rm.stage_key, "board")
        self.assertGreater(rm.qty_per_fg, 0.0)
        # GSM falls back to the scalar top_sheet_gsm value.
        self.assertAlmostEqual(rm.metadata.get("gsm"), 150.0)
        self.assertIsNone(rm.metadata.get("top_sheet_product_id"))

    def test_unit_sheet_product_falls_back_to_scalar_gsm_when_surface_gsm_missing(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=4,
            spec_name="Unit Sheet Product GSM Fallback",
        )
        spec_input.top_sheet_gsm = 150.0
        # Surface profile has a selected product but no explicit GSM.
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=4,
            print_coverage_percent=90.0,
            top_sheet_mode="unit_sheet",
            top_sheet_product_id=99,
            top_sheet_gsm=0.0,
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="UNIT_FALLBACK",
            name="Unit Sheet Fallback Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        top_rms = [
            rm
            for rm in result.rm_requirements
            if rm.role == "top_sheet_external" and rm.product_key == "rm_top_sheet_external"
        ]
        self.assertEqual(len(top_rms), 1)
        rm = top_rms[0]
        # GSM should fall back to the scalar value when surface GSM is unusable.
        self.assertAlmostEqual(rm.metadata.get("gsm"), 150.0)
        self.assertEqual(rm.metadata.get("top_sheet_product_id"), 99)

    def test_roll_mode_with_surface_profile_matches_legacy_behaviour(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=5,
            spec_name="Roll Mode With Surface",
        )
        spec_input.top_sheet_gsm = 150.0
        # Surface profile explicitly selects roll mode; GSM should be
        # driven by the scalar top_sheet_gsm as in the legacy path.
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=2,
            print_coverage_percent=70.0,
            top_sheet_mode="roll",
            top_sheet_product_id=123,
            top_sheet_gsm=999.0,  # ignored for roll mode
        )

        plant = PlantProfile(
            stages=["ts_print", "board", "fg"],
            code="ROLL_SURF",
            name="Roll Surface Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        rm_keys = {(rm.role, rm.product_key, rm.stage_key) for rm in result.rm_requirements}
        # Roll-mode top sheet still resolves as rm_top_roll but is now
        # routed via the board material stage instead of a ts_print
        # process-only stage.
        self.assertIn(("top_roll", "rm_top_roll", "board"), rm_keys)

    def test_roll_mode_routes_top_roll_to_explicit_cutting_material_stage(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0 + 140.0 + 140.0,
            medium_gsm=140.0 + 140.0,
            style_code="",
            sfg_mode="staged",
            spec_id=51,
            spec_name="Roll Cutting Route",
        )
        spec_input.board_layers = [
            {"type": "liner", "gsm": 180.0, "layer_no": 1},
            {"type": "medium", "gsm": 140.0, "layer_no": 2},
            {"type": "liner", "gsm": 140.0, "layer_no": 3},
            {"type": "medium", "gsm": 140.0, "layer_no": 4},
            {"type": "liner", "gsm": 140.0, "layer_no": 5},
        ]
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=1,
            print_coverage_percent=0.0,
            top_sheet_mode="roll",
        )
        spec_input.routing_profile = RoutingProfileInput(
            name="Cutting 2ply Board Diecut Seaming",
            stages=[
                RoutingStageInput(sequence=10, stage_code="cutting", stage_type="material", stage_semantic="cutting", output_kind="sheet"),
                RoutingStageInput(sequence=20, stage_code="2ply", stage_type="material", stage_semantic="corrugation", output_kind="sheet"),
                RoutingStageInput(sequence=30, stage_code="board", stage_type="material", stage_semantic="conversion", output_kind="board"),
                RoutingStageInput(sequence=40, stage_code="diecut", stage_type="operation", stage_semantic="diecut", output_kind="sheet"),
                RoutingStageInput(sequence=50, stage_code="seaming", stage_type="material", stage_semantic="fg", output_kind="fg", is_fg_stage=True),
            ],
        )
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="ROLL_CUT",
            name="Roll Cutting Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)

        rm_keys = {(rm.role, rm.product_key, rm.stage_key) for rm in result.rm_requirements}
        self.assertIn(("top_roll", "rm_top_roll", "cutting"), rm_keys)
        self.assertNotIn(("top_roll", "rm_top_roll", "board"), rm_keys)
        self.assertFalse(
            any(rm.role in ("liner", "medium") and rm.stage_key == "cutting" for rm in result.rm_requirements),
            "base board paper layers must not be routed to the top-sheet cutting stage",
        )

    def test_stack_driven_top_roll_3ply(self):
        """3-ply roll mode: top liner becomes rm_top_roll, remaining layers stay as board RMs."""

        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0 + 200.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=6,
            spec_name="3ply Roll From Stack",
        )
        # Board structure: outer liner, medium, inner liner.
        class Layer:
            def __init__(self, t, gsm, layer_no):
                self.type = t
                self.gsm = gsm
                self.layer_no = layer_no
        spec_input.board_layers = [
            {"type": "liner", "gsm": 180.0, "layer_no": 1},
            {"type": "medium", "gsm": 120.0, "layer_no": 2},
            {"type": "liner", "gsm": 200.0, "layer_no": 3},
        ]
        # Surface profile selects roll mode with printing enabled.
        spec_input.surface_profile = SurfaceProfileInput(
            printing_required=True,
            print_process="flexo",
            print_colors=2,
            print_coverage_percent=50.0,
            top_sheet_mode="roll",
            top_sheet_product_id=None,
        )

        plant = PlantProfile(
            stages=["ts_print", "board", "fg"],
            code="ROLL3",
            name="3ply Roll Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        result = compute(spec_input, plant)
        rm_by_role = {}
        for rm in result.rm_requirements:
            rm_by_role.setdefault(rm.role, []).append(rm)

        # One top_roll RM feeding the printing material stage (board).
        top_roll_rms = rm_by_role.get("top_roll", [])
        self.assertEqual(len(top_roll_rms), 1)
        self.assertEqual(top_roll_rms[0].product_key, "rm_top_roll")
        self.assertEqual(top_roll_rms[0].stage_key, "board")

        # Board RMs should include one medium and one liner (inner), i.e. 2 layers.
        liner_rms = [rm for rm in rm_by_role.get("liner", []) if rm.product_key == "rm_liner"]
        medium_rms = [rm for rm in rm_by_role.get("medium", []) if rm.product_key == "rm_medium"]
        self.assertEqual(len(liner_rms), 1)
        self.assertEqual(len(medium_rms), 1)




