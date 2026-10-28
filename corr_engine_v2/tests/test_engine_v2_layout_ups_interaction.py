import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile


class TestEngineV2LayoutUpsInteraction(unittest.TestCase):
    def _make_spec(self):
        # Common spec with fixed blank size
        return EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Layout UPS Spec",
        )

    def test_layout_limits_boxes_per_sheet(self):
        spec_input = self._make_spec()

        # Plant with high UPS but a relatively small sheet so that
        # geometric layout is the limiting factor.
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="P_SMALL",
            name="Small Sheet Plant",
            printing_ups=10,
            diecut_ups=10,
            allow_blank_rotation=True,
            sheet_length_mm=1200.0,
            sheet_width_mm=800.0,
        )

        result = compute(spec_input, plant)
        metrics = result.metrics
        extra = metrics.extra

        layout = extra.get("layout") or {}
        layout_blanks = extra.get("layout_blanks_per_sheet")
        boxes_per_sheet = extra.get("boxes_per_sheet")

        # For the chosen sizes:
        # normal: floor(1200/500)=2, floor(800/400)=2 -> 4
        # rotated: floor(1200/400)=3, floor(800/500)=1 -> 3
        # so layout capacity is 4 blanks per sheet.
        self.assertEqual(layout.get("blanks_per_sheet"), 4)
        self.assertEqual(layout_blanks, 4)

        # Effective UPS from plant is 10, but boxes_per_sheet reflects
        # geometric layout capacity, not the UPS clamp.
        self.assertEqual(metrics.effective_ups, 10)
        self.assertEqual(extra.get("effective_ups"), 10)
        self.assertEqual(boxes_per_sheet, 4)
        self.assertEqual(boxes_per_sheet, layout_blanks)

    def test_boxes_per_sheet_changes_with_sheet_size(self):
        spec_input = self._make_spec()

        # Plant 1: smaller sheet
        plant_small = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="P1",
            name="Plant 1",
            printing_ups=10,
            diecut_ups=10,
            allow_blank_rotation=True,
            sheet_length_mm=1200.0,
            sheet_width_mm=800.0,
        )

        # Plant 2: larger sheet (geometric capacity higher than UPS)
        plant_large = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="P2",
            name="Plant 2",
            printing_ups=10,
            diecut_ups=10,
            allow_blank_rotation=True,
            sheet_length_mm=3000.0,
            sheet_width_mm=1600.0,
        )

        result_small = compute(spec_input, plant_small)
        result_large = compute(spec_input, plant_large)

        extra_small = result_small.metrics.extra
        extra_large = result_large.metrics.extra

        boxes_small = extra_small.get("boxes_per_sheet")
        boxes_large = extra_large.get("boxes_per_sheet")
        layout_small = extra_small.get("layout") or {}
        layout_large = extra_large.get("layout") or {}

        # With identical UPS but different sheet sizes, boxes_per_sheet
        # should differ and increase with larger sheet, and equal the
        # geometric blanks_per_sheet in each case.
        self.assertGreater(boxes_small, 0)
        self.assertGreater(boxes_large, 0)
        self.assertNotEqual(boxes_small, boxes_large)
        self.assertGreater(boxes_large, boxes_small)
        self.assertEqual(boxes_small, layout_small.get("blanks_per_sheet"))
        self.assertEqual(boxes_large, layout_large.get("blanks_per_sheet"))

    def test_derived_sheet_uses_printing_ups_as_capacity(self):
        """In derived mode, blanks_per_sheet matches printing_ups, not plant sheet size."""

        spec_input = self._make_spec()

        # Plant with large theoretical capacity but default_sheet_behavior='derived'.
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="P_DERIVED",
            name="Derived Sheet Plant",
            printing_ups=2,
            diecut_ups=2,
            allow_blank_rotation=True,
            # Large sheet values that should be ignored in derived mode.
            sheet_length_mm=3000.0,
            sheet_width_mm=2000.0,
            default_sheet_behavior="derived",
        )

        result = compute(spec_input, plant)
        extra = result.metrics.extra
        layout = extra.get("layout") or {}

        # Derived sheet sizing should honour printing_ups as the
        # preferred capacity regardless of plant sheet dimensions.
        self.assertEqual(extra.get("printing_ups"), result.metrics.printing_ups)
        self.assertEqual(result.metrics.printing_ups, 2)

        self.assertEqual(layout.get("blanks_per_sheet"), 2)
        self.assertEqual(extra.get("layout_blanks_per_sheet"), 2)
        self.assertEqual(extra.get("boxes_per_sheet"), 2)

    def test_derived_sheet_feasible_against_machine_limits_1up(self):
        """Derived 1-up sheet within plant sheet size is accepted."""

        spec_input = self._make_spec()

        # For 1-up, derived sheet is 500 x 400.
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="P_DERIVED_OK1",
            name="Derived Sheet OK 1-up",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=True,
            sheet_length_mm=2000.0,  # larger than 500
            sheet_width_mm=2000.0,   # larger than 400
            default_sheet_behavior="derived",
        )

        result = compute(spec_input, plant)
        extra = result.metrics.extra
        layout = extra.get("layout") or {}

        self.assertEqual(layout.get("blanks_per_sheet"), 1)
        self.assertEqual(extra.get("boxes_per_sheet"), 1)

    def test_derived_sheet_feasible_against_machine_limits_2up(self):
        """Derived 2-up sheet within plant sheet size is accepted."""

        spec_input = self._make_spec()

        # For 2-up, derived sheet is 500 x 800 (1 x 2).
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="P_DERIVED_OK2",
            name="Derived Sheet OK 2-up",
            printing_ups=2,
            diecut_ups=2,
            allow_blank_rotation=True,
            sheet_length_mm=2000.0,  # larger than 500
            sheet_width_mm=2000.0,   # larger than 800
            default_sheet_behavior="derived",
        )

        result = compute(spec_input, plant)
        extra = result.metrics.extra
        layout = extra.get("layout") or {}

        self.assertEqual(layout.get("blanks_per_sheet"), 2)
        self.assertEqual(extra.get("boxes_per_sheet"), 2)

    def test_derived_sheet_infeasible_due_to_width_limit(self):
        """Derived 2-up sheet wider than plant sheet_width_mm is rejected."""

        spec_input = self._make_spec()

        # Derived 2-up sheet is 500 x 800. Plant width limit is 700.
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="P_DERIVED_BADW",
            name="Derived Sheet Bad Width",
            printing_ups=2,
            diecut_ups=2,
            allow_blank_rotation=True,
            sheet_length_mm=2000.0,
            sheet_width_mm=700.0,  # less than derived 800mm width
            default_sheet_behavior="derived",
        )

        with self.assertRaises(ValueError) as ctx:
            compute(spec_input, plant)
        msg = str(ctx.exception)
        self.assertIn("Derived sheet", msg)
        self.assertIn("width", msg)

    def test_derived_sheet_infeasible_can_continue_for_preview_suggestions(self):
        """Debug preview can continue so auto split suggestions can be rendered."""

        spec_input = self._make_spec()
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="P_DERIVED_PREVIEW",
            name="Derived Sheet Preview",
            printing_ups=2,
            diecut_ups=2,
            allow_blank_rotation=True,
            sheet_length_mm=2000.0,
            sheet_width_mm=700.0,
            default_sheet_behavior="derived",
        )

        result = compute(
            spec_input,
            plant,
            options={"allow_infeasible_derived_sheet_preview": True},
        )

        feasibility = result.metrics.extra.get("derived_sheet_feasibility") or {}
        self.assertEqual(feasibility.get("status"), "infeasible")
        self.assertIn("Derived sheet", feasibility.get("message") or "")

    def test_derived_sheet_infeasible_due_to_length_limit(self):
        """Derived 2-up sheet longer than plant sheet_length_mm is rejected."""

        spec_input = self._make_spec()

        # Derived 2-up sheet is 500 x 800. Plant length limit is 400.
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="P_DERIVED_BADL",
            name="Derived Sheet Bad Length",
            printing_ups=2,
            diecut_ups=2,
            allow_blank_rotation=True,
            sheet_length_mm=400.0,  # less than derived 500mm length
            sheet_width_mm=2000.0,
            default_sheet_behavior="derived",
        )

        with self.assertRaises(ValueError) as ctx:
            compute(spec_input, plant)
        msg = str(ctx.exception)
        self.assertIn("Derived sheet", msg)
        self.assertIn("length", msg)




