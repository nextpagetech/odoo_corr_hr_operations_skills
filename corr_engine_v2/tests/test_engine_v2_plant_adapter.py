import unittest

from custom_addons.corr_engine_v2.engine_v2 import compute, EngineSpecInput
from custom_addons.corr_engine_v2.plant_adapter import build_plant_profile


class TestEngineV2PlantAdapter(unittest.TestCase):
    def test_build_plant_profile_and_ups(self):
        # Mock plant configuration as it would exist in corr_mrp_bridge.
        class MockPlantConfig:
            def __init__(self):
                self.stages = ["2ply", "board", "printing", "diecut", "fg"]
                self.code = "TEST"
                self.name = "Test Plant"
                self.printing_ups = 3
                self.diecut_ups = 6

        plant_cfg = MockPlantConfig()

        # Build PlantProfile via the adapter (no engine_v2 imports here).
        plant_profile = build_plant_profile(plant_cfg)

        # Basic profile sanity checks.
        self.assertEqual(plant_profile.stages, ["2ply", "board", "printing", "diecut", "fg"])
        self.assertEqual(plant_profile.code, "TEST")
        self.assertEqual(plant_profile.name, "Test Plant")
        self.assertEqual(plant_profile.printing_ups, 3)
        self.assertEqual(plant_profile.diecut_ups, 6)

        # Prepare a simple spec input.
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Test Spec",
        )

        # Run the engine using the PlantProfile.
        result = compute(spec_input, plant_profile)

        # Metrics should reflect UPS from the plant profile.
        metrics = result.metrics
        self.assertEqual(metrics.printing_ups, 3)
        self.assertEqual(metrics.diecut_ups, 6)
        # effective_ups is defined as min(printing_ups, diecut_ups)
        self.assertEqual(metrics.effective_ups, 3)

        # Final edge into FG must use qty_per_fg derived from layout.
        # When the plant has no explicit sheet size configured, layout
        # falls back to a neutral 1:1 mapping (boxes_per_sheet = 1).
        fg_edges = [e for e in result.stage_graph.flows if e.to_stage == "fg"]
        self.assertTrue(fg_edges)
        self.assertAlmostEqual(fg_edges[0].qty_per_fg, 1.0, places=6)

    def test_split_constraints_mapped_from_plant(self):
        # Mock plant configuration carrying Engine V2 split constraints.
        class MockPlantConfig:
            def __init__(self):
                self.stages = ["2ply", "board", "fg"]
                self.code = "SPLIT"
                self.name = "Split Plant"
                self.printing_ups = 1
                self.diecut_ups = 1
                self.max_machine_width_mm = 2200.0
                self.split_mode = "uneven_allowed"
                self.min_piece_width_mm = 350.0
                self.max_piece_width_mm = 1200.0
                self.default_sheet_behavior = "derived"

        plant_cfg = MockPlantConfig()
        plant_profile = build_plant_profile(plant_cfg)

        self.assertAlmostEqual(plant_profile.max_machine_width_mm, 2200.0)
        self.assertEqual(plant_profile.split_mode, "uneven_allowed")
        self.assertAlmostEqual(plant_profile.min_piece_width_mm, 350.0)
        self.assertAlmostEqual(plant_profile.max_piece_width_mm, 1200.0)
        self.assertEqual(plant_profile.default_sheet_behavior, "derived")




