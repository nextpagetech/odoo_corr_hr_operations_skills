import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile


class TestEngineV2BoardFeasibility(unittest.TestCase):
    def _make_basic_plant(self):
        return PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT_BOARD",
            name="Plant Board",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=2000.0,
            sheet_width_mm=1200.0,
        )

    def test_preview_succeeds_with_valid_board_from_scalar_gsm(self):
        """Engine accepts a simple 2-ply board resolved from liner/medium GSM."""

        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Board OK",
        )
        plant = self._make_basic_plant()

        result = compute(spec_input, plant)
        self.assertTrue(result.rm_requirements)
        # Ensure at least one corrugation RM was produced.
        roles = {rm.role for rm in result.rm_requirements}
        self.assertIn("liner", roles)
        self.assertIn("medium", roles)

    def test_preview_fails_when_no_board_layers_and_no_scalar_gsm(self):
        """Engine raises a clear error when no board layers can be resolved."""

        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=0.0,
            medium_gsm=0.0,
            style_code="",
            sfg_mode="staged",
            spec_id=2,
            spec_name="Board Missing",
        )
        plant = self._make_basic_plant()

        with self.assertRaises(ValueError) as ctx:
            compute(spec_input, plant)
        msg = str(ctx.exception)
        self.assertIn("board/material resolution failed", msg)

    def test_preview_fails_when_only_liner_or_only_medium_resolved(self):
        """Engine fails when resolved board is missing a required liner or medium layer."""

        plant = self._make_basic_plant()

        # Only liner GSM present.
        spec_input_liner_only = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=0.0,
            style_code="",
            sfg_mode="staged",
            spec_id=3,
            spec_name="Board Missing Medium",
        )
        with self.assertRaises(ValueError) as ctx1:
            compute(spec_input_liner_only, plant)
        self.assertIn("must contain at least one liner layer and one medium layer", str(ctx1.exception))

        # Only medium GSM present.
        spec_input_medium_only = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=0.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=4,
            spec_name="Board Missing Liner",
        )
        with self.assertRaises(ValueError) as ctx2:
            compute(spec_input_medium_only, plant)
        self.assertIn("must contain at least one liner layer and one medium layer", str(ctx2.exception))




