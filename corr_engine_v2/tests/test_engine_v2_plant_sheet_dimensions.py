import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.layout import resolve_layout
from custom_addons.corr_engine_v2.plant_adapter import build_plant_profile


class TestEngineV2PlantSheetDimensions(unittest.TestCase):
    def test_plant_profile_receives_sheet_dimensions(self):
        class MockPlantConfig:
            def __init__(self, length_mm, width_mm):
                self.stages = ["2ply", "board", "fg"]
                self.code = "PLANT1"
                self.name = "Plant 1"
                self.printing_ups = 1
                self.diecut_ups = 1
                self.sheet_length_mm = length_mm
                self.sheet_width_mm = width_mm

        plant_cfg = MockPlantConfig(2000.0, 1000.0)
        plant_profile = build_plant_profile(plant_cfg)

        self.assertAlmostEqual(plant_profile.sheet_length_mm or 0.0, 2000.0)
        self.assertAlmostEqual(plant_profile.sheet_width_mm or 0.0, 1000.0)

    def test_layout_uses_plant_sheet_dimensions(self):
        class MockPlantConfig:
            def __init__(self, length_mm, width_mm):
                self.stages = ["2ply", "board", "fg"]
                self.code = "PLANT"
                self.name = "Plant"
                self.printing_ups = 1
                self.diecut_ups = 1
                self.sheet_length_mm = length_mm
                self.sheet_width_mm = width_mm

        # Spec with fixed blank size
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=0.0,
            medium_gsm=0.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Sheet Layout Spec",
        )

        # Plant 1: larger sheet
        plant_profile_1 = build_plant_profile(MockPlantConfig(2000.0, 1000.0))
        layout_1 = resolve_layout(spec_input, plant_profile_1)

        # Plant 2: smaller sheet
        plant_profile_2 = build_plant_profile(MockPlantConfig(1000.0, 1000.0))
        layout_2 = resolve_layout(spec_input, plant_profile_2)

        # With the chosen sizes, Plant 1 should yield more blanks per sheet.
        self.assertGreater(layout_1["blanks_per_sheet"], 0)
        self.assertGreater(layout_2["blanks_per_sheet"], 0)
        self.assertNotEqual(layout_1["blanks_per_sheet"], layout_2["blanks_per_sheet"])

    def test_compute_integration_uses_plant_sheet_dimensions(self):
        class MockPlantConfig:
            def __init__(self, length_mm, width_mm):
                self.stages = ["2ply", "board", "fg"]
                self.code = "PLANTC"
                self.name = "Plant Compute"
                self.printing_ups = 1
                self.diecut_ups = 1
                self.sheet_length_mm = length_mm
                self.sheet_width_mm = width_mm

        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=2,
            spec_name="Compute Layout Spec",
        )

        plant_profile_1 = build_plant_profile(MockPlantConfig(2000.0, 1000.0))
        plant_profile_2 = build_plant_profile(MockPlantConfig(1000.0, 1000.0))

        result_1 = compute(spec_input, plant_profile_1)
        result_2 = compute(spec_input, plant_profile_2)

        layout_1 = result_1.metrics.extra.get("layout")
        layout_2 = result_2.metrics.extra.get("layout")

        self.assertIsInstance(layout_1, dict)
        self.assertIsInstance(layout_2, dict)
        self.assertGreater(layout_1.get("blanks_per_sheet", 0), 0)
        self.assertGreater(layout_2.get("blanks_per_sheet", 0), 0)
        self.assertNotEqual(layout_1["blanks_per_sheet"], layout_2["blanks_per_sheet"])





