import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, EngineMetrics
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.slitting import resolve_slitting


class TestEngineV2Slitting(unittest.TestCase):
    def test_basic_slitting_pieces(self):
        # Spec with known blank dimensions
        spec_input = EngineSpecInput(
            blank_length_mm=1000.0,
            blank_width_mm=500.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Slitting Spec",
        )

        # Plant profile with a sheet width that allows 4 blanks across
        plant_profile = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="TEST",
            name="Test Plant",
            printing_ups=1,
            diecut_ups=1,
        )
        plant_profile.sheet_width_mm = 2000.0

        metrics = EngineMetrics(
            kg_per_box=0.0,
            m2_per_box=0.0,
            waste_pct=0.0,
            ups=1.0,
        )

        pieces = resolve_slitting(spec_input, plant_profile, metrics)

        self.assertEqual(len(pieces), 4)
        for p in pieces:
            self.assertAlmostEqual(p.ratio, 0.25, places=6)
            self.assertEqual(p.width_mm, 500.0)
            self.assertEqual(p.length_mm, 1000.0)




