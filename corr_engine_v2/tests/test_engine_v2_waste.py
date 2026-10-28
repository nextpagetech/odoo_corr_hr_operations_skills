import unittest

from custom_addons.corr_engine_v2.engine_v2 import compute, EngineSpecInput
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile


class TestEngineV2Waste(unittest.TestCase):
    def test_waste_optional_application(self):
        # Spec with known blank and GSM
        spec_input = EngineSpecInput(
            blank_length_mm=1000.0,
            blank_width_mm=500.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Waste Spec",
        )
        # Attach waste percentages on spec (10% total)
        spec_input.trim_waste_pct = 0.05
        spec_input.setup_waste_pct = 0.02
        spec_input.diecut_waste_pct = 0.03

        plant_profile = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="TEST",
            name="Test Plant",
            printing_ups=1,
            diecut_ups=1,
        )

        # 1) Run without including waste
        result_no_waste = compute(spec_input, plant_profile, options={"include_waste": False})
        metrics_no = result_no_waste.metrics
        base_kg = metrics_no.kg_per_box
        kg_with_no = metrics_no.extra.get("kg_per_box_with_waste")

        # 2) Run with waste included
        result_with_waste = compute(spec_input, plant_profile, options={"include_waste": True})
        metrics_yes = result_with_waste.metrics
        kg_with_yes = metrics_yes.extra.get("kg_per_box_with_waste")

        # Same base kg_per_box in both runs
        self.assertAlmostEqual(metrics_yes.kg_per_box, base_kg, places=6)

        # When waste is disabled, effective kg equals base
        self.assertAlmostEqual(kg_with_no, base_kg, places=6)

        # When waste is enabled, effective kg is higher
        self.assertGreater(kg_with_yes, base_kg)

        # Total waste pct should be present and ~0.10
        total_pct = metrics_yes.extra.get("total_waste_pct")
        self.assertAlmostEqual(total_pct, 0.10, places=6)




