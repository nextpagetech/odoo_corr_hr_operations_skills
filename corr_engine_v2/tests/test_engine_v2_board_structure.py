import unittest

from custom_addons.corr_engine_v2.engine_v2.board_structure import (
    BoardLayer,
    BoardStructure,
    resolve_board_structure,
)
from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput


class TestEngineV2BoardStructure(unittest.TestCase):
    def test_explicit_board_layers_used(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=0.0,
            medium_gsm=0.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Multi-ply",
        )

        # Define an explicit 3-ply structure: liner -> medium -> liner
        spec_input.board_layers = [
            {"type": "liner", "gsm": 180.0, "layer_no": 1},
            {"type": "medium", "gsm": 120.0, "layer_no": 2},
            {"type": "liner", "gsm": 200.0, "layer_no": 3},
        ]

        bs = resolve_board_structure(spec_input)
        self.assertIsInstance(bs, BoardStructure)
        self.assertEqual(len(bs.layers), 3)

        self.assertEqual(bs.layers[0].type, "liner")
        self.assertAlmostEqual(bs.layers[0].gsm, 180.0)
        self.assertEqual(bs.layers[0].layer_no, 1)

        self.assertEqual(bs.layers[1].type, "medium")
        self.assertAlmostEqual(bs.layers[1].gsm, 120.0)
        self.assertEqual(bs.layers[1].layer_no, 2)

        self.assertEqual(bs.layers[2].type, "liner")
        self.assertAlmostEqual(bs.layers[2].gsm, 200.0)
        self.assertEqual(bs.layers[2].layer_no, 3)

    def test_fallback_to_legacy_liner_medium(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Legacy 2ply",
        )

        bs = resolve_board_structure(spec_input)
        self.assertEqual(len(bs.layers), 2)

        self.assertEqual(bs.layers[0].type, "liner")
        self.assertAlmostEqual(bs.layers[0].gsm, 180.0)
        self.assertEqual(bs.layers[0].layer_no, 1)

        self.assertEqual(bs.layers[1].type, "medium")
        self.assertAlmostEqual(bs.layers[1].gsm, 120.0)
        self.assertEqual(bs.layers[1].layer_no, 2)




