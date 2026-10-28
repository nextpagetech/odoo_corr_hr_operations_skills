import unittest

from custom_addons.corr_engine_v2.engine_v2 import compute, EngineSpecInput
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.spec_input import GeometrySpec
from custom_addons.corr_engine_v2.engine_v2.styles import style_0201


class TestEngineV2Style0201(unittest.TestCase):
    def test_style_0201_blank_and_metrics(self):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="0201",
            sfg_mode="staged",
            spec_id=1,
            spec_name="0201 Box",
        )
        spec_input.box_length_mm = 300.0
        spec_input.box_width_mm = 200.0
        spec_input.box_height_mm = 150.0
        spec_input.glue_flap_mm = 20.0

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="TEST",
            name="Test Plant",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=2000.0,
            sheet_width_mm=1200.0,
            default_glue_lap_mm=20.0,
            flap_basis="fixed",
            flap_fixed_mm=20.0,
            flap_percent=0.0,
            flap_percent_of="width",
        )

        blank = style_0201.compute_blank(spec_input, plant)
        self.assertIsNotNone(blank)

        expected_blank_length = 2 * (300.0 + 200.0) + 20.0
        expected_blank_width = 200.0 + 150.0 + 20.0
        self.assertAlmostEqual(blank["blank_length_mm"], expected_blank_length, places=6)
        self.assertAlmostEqual(blank["blank_width_mm"], expected_blank_width, places=6)

        result = compute(spec_input, plant)
        metrics = result.metrics

        expected_m2_per_box = (expected_blank_length * expected_blank_width) / 1_000_000.0
        self.assertAlmostEqual(metrics.m2_per_box, expected_m2_per_box, places=6)

    def test_style_0201_geometry_spec_driven_blank(self):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="0201",
            sfg_mode="staged",
            spec_id=2,
            spec_name="0201 Box GeometrySpec",
        )
        spec_input.geometry = GeometrySpec(
            style_id=1,
            box_length_mm=300.0,
            box_width_mm=200.0,
            box_height_mm=150.0,
            glue_flap_override_mm=20.0,
            plant_code="PLANTG",
            plant_id=None,
        )

        plant_profile = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANTG",
            name="Geometry Plant",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=2000.0,
            sheet_width_mm=1200.0,
            default_glue_lap_mm=20.0,
            flap_basis="fixed",
            flap_fixed_mm=20.0,
            flap_percent=0.0,
            flap_percent_of="width",
        )

        result = compute(spec_input, plant_profile)
        metrics = result.metrics

        expected_blank_length = 2 * (300.0 + 200.0) + 20.0
        expected_blank_width = 200.0 + 150.0 + 20.0
        expected_m2_per_box = (expected_blank_length * expected_blank_width) / 1_000_000.0

        self.assertAlmostEqual(metrics.m2_per_box, expected_m2_per_box, places=6)

    def test_style_0201_legacy_geometry_large_box(self):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="0201",
            sfg_mode="staged",
            spec_id=3,
            spec_name="0201 large box legacy geometry",
        )
        spec_input.box_length_mm = 710.0
        spec_input.box_width_mm = 495.0
        spec_input.box_height_mm = 300.0
        spec_input.glue_flap_mm = 20.0

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT_LEGACY",
            name="Legacy Plant",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=3000.0,
            sheet_width_mm=2000.0,
            default_glue_lap_mm=20.0,
            flap_basis="fixed",
            flap_fixed_mm=20.0,
            flap_percent=0.0,
            flap_percent_of="width",
        )

        blank = style_0201.compute_blank(spec_input, plant)
        self.assertIsNotNone(blank)

        expected_blank_length = 2 * (710.0 + 495.0) + 20.0
        expected_blank_width = 495.0 + 300.0 + 20.0
        self.assertAlmostEqual(blank["blank_length_mm"], expected_blank_length, places=6)
        self.assertAlmostEqual(blank["blank_width_mm"], expected_blank_width, places=6)




