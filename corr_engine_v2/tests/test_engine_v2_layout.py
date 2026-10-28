import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.layout import resolve_layout
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile


class TestEngineV2Layout(unittest.TestCase):
    def test_layout_without_rotation(self):
        # Sheet and blank sizes chosen so that rotation would change counts,
        # but the plant does not allow rotation.
        spec_input = EngineSpecInput(
            blank_length_mm=700.0,
            blank_width_mm=400.0,
            liner_gsm=0.0,
            medium_gsm=0.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="No Rotation",
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="NOROT",
            name="No Rotation Plant",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=False,
        )
        # Sheet dimensions (mm)
        plant.sheet_length_mm = 1200.0
        plant.sheet_width_mm = 700.0

        layout = resolve_layout(spec_input, plant)

        # Normal orientation: floor(1200/700)=1, floor(700/400)=1 -> 1 piece
        self.assertEqual(layout["orientation"], "normal")
        self.assertEqual(layout["blanks_per_row"], 1)
        self.assertEqual(layout["blanks_per_column"], 1)
        self.assertEqual(layout["blanks_per_sheet"], 1)

    def test_layout_with_rotation_flag_true_normal_best(self):
        # Same geometry as previous test, rotation allowed but still not better.
        spec_input = EngineSpecInput(
            blank_length_mm=700.0,
            blank_width_mm=400.0,
            liner_gsm=0.0,
            medium_gsm=0.0,
            style_code="",
            sfg_mode="staged",
            spec_id=2,
            spec_name="Rotation Allowed",
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="ROT",
            name="Rotation Plant",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=True,
        )
        plant.sheet_length_mm = 1200.0
        plant.sheet_width_mm = 700.0

        layout = resolve_layout(spec_input, plant)

        # Normal: 1x1 = 1, rotated: floor(1200/400)=3, floor(700/700)=1 -> 3
        # Here rotated is better, so we expect rotated orientation.
        self.assertEqual(layout["orientation"], "rotated")
        self.assertEqual(layout["blanks_per_row"], 3)
        self.assertEqual(layout["blanks_per_column"], 1)
        self.assertEqual(layout["blanks_per_sheet"], 3)

    def test_layout_rotated_selected_when_better(self):
        # Explicit test to confirm rotated orientation is chosen when it yields more pieces.
        spec_input = EngineSpecInput(
            blank_length_mm=700.0,
            blank_width_mm=400.0,
            liner_gsm=0.0,
            medium_gsm=0.0,
            style_code="",
            sfg_mode="staged",
            spec_id=3,
            spec_name="Rotated Best",
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="ROT2",
            name="Rotation Plant 2",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=True,
        )
        plant.sheet_length_mm = 1200.0
        plant.sheet_width_mm = 700.0

        layout = resolve_layout(spec_input, plant)

        self.assertEqual(layout["orientation"], "rotated")
        self.assertEqual(layout["blanks_per_sheet"], 3)

    def test_layout_waste_calculation(self):
        # Simple case with partial utilisation to validate waste metrics.
        spec_input = EngineSpecInput(
            blank_length_mm=200.0,
            blank_width_mm=200.0,
            liner_gsm=0.0,
            medium_gsm=0.0,
            style_code="",
            sfg_mode="staged",
            spec_id=4,
            spec_name="Waste Layout",
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="WASTE",
            name="Waste Plant",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=False,
        )
        plant.sheet_length_mm = 1000.0
        plant.sheet_width_mm = 900.0

        layout = resolve_layout(spec_input, plant)

        # Normal: floor(1000/200)=5, floor(900/200)=4 -> 20 blanks
        # Blank area = 40_000, sheet area = 900_000, usage = 800_000/900_000 = 0.888...
        expected_usage = (20 * 200.0 * 200.0) / (1000.0 * 900.0)
        expected_waste = 1.0 - expected_usage

        self.assertAlmostEqual(layout["sheet_usage_percent"], expected_usage, places=6)
        self.assertAlmostEqual(layout["waste_percent"], expected_waste, places=6)

    def test_layout_trace_and_metrics_integration(self):
        # Ensure compute() attaches layout info into metrics.extra and trace.
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=5,
            spec_name="Layout Integration",
        )

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="INT",
            name="Integration Plant",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=True,
        )
        plant.sheet_length_mm = 2000.0
        plant.sheet_width_mm = 1000.0

        result = compute(spec_input, plant)

        # Trace should contain a layout_resolved entry.
        step_names = [step for step, _payload in result.trace]
        self.assertIn("layout_resolved", step_names)

        layout = result.metrics.extra.get("layout")
        self.assertIsInstance(layout, dict)
        self.assertGreaterEqual(layout.get("blanks_per_sheet", 0), 1)





