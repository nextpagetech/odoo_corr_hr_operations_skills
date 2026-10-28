import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.styles.registry import STYLE_REGISTRY
import custom_addons.corr_engine_v2.engine_v2.styles.style_0201 as style_0201


class TestEngineV2StyleRegistry(unittest.TestCase):
    def test_style_module_registers_itself(self):
        # STYLE_REGISTRY should contain an entry for 0201 pointing to
        # the compute_blank handler defined in style_0201.
        handler = STYLE_REGISTRY.get("0201")
        self.assertIsNotNone(handler)
        self.assertIs(handler, style_0201.compute_blank)

    def test_registry_lookup_used_by_engine(self):
        # Prepare a spec with box dimensions but zero blank size; the
        # 0201 style handler should populate blank_length/blank_width.
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="0201",
            sfg_mode="staged",
            spec_id=1,
            spec_name="0201 Registry Box",
        )
        spec_input.box_length_mm = 300.0
        spec_input.box_width_mm = 200.0
        spec_input.box_height_mm = 150.0
        spec_input.glue_flap_mm = 20.0

        class DummyPlant:
            stages = ["2ply", "board", "fg"]
            code = "TEST"
            name = "Test"

        plant = DummyPlant()

        result = compute(spec_input, plant)
        metrics = result.metrics

        expected_blank_length = 2 * (300.0 + 200.0) + 20.0
        expected_blank_width = 200.0 + 150.0 + 20.0
        expected_m2 = (expected_blank_length * expected_blank_width) / 1_000_000.0

        self.assertAlmostEqual(metrics.m2_per_box, expected_m2, places=6)




