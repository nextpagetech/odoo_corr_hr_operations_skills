import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.spec_input import EngineOverrides


class TestEngineV2UpsEnforcement(unittest.TestCase):
    def _make_spec_and_plant(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="UPS Enforcement Spec",
        )
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="UPS_PLANT",
            name="UPS Plant",
            printing_ups=8,
            diecut_ups=6,
        )
        return spec_input, plant

    def test_soft_forced_ups_clamped_to_plant_capacity(self):
        spec_input, plant = self._make_spec_and_plant()
        # Plant effective UPS = min(8, 6) = 6; soft forced_ups=10 should clamp to 6.
        spec_input.overrides = EngineOverrides(
            manual_sheet_length_mm=0.0,
            manual_sheet_width_mm=0.0,
            forced_ups=10,
            ups_enforcement="soft",
        )

        result = compute(spec_input, plant)
        metrics = result.metrics
        self.assertEqual(metrics.printing_ups, 8)
        self.assertEqual(metrics.diecut_ups, 6)
        self.assertEqual(metrics.effective_ups, 6)

    def test_hard_forced_ups_within_capacity(self):
        spec_input, plant = self._make_spec_and_plant()
        # Plant effective UPS = min(8, 6) = 6; hard forced_ups=4 is allowed.
        spec_input.overrides = EngineOverrides(
            manual_sheet_length_mm=0.0,
            manual_sheet_width_mm=0.0,
            forced_ups=4,
            ups_enforcement="hard",
        )

        result = compute(spec_input, plant)
        metrics = result.metrics
        self.assertEqual(metrics.effective_ups, 4)

    def test_hard_forced_ups_exceeds_capacity_raises(self):
        spec_input, plant = self._make_spec_and_plant()
        # Hard forced_ups greater than plant capacity should raise.
        spec_input.overrides = EngineOverrides(
            manual_sheet_length_mm=0.0,
            manual_sheet_width_mm=0.0,
            forced_ups=10,
            ups_enforcement="hard",
        )

        with self.assertRaises(ValueError):
            compute(spec_input, plant)





