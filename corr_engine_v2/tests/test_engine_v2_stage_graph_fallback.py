import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile


class TestEngineV2StageGraphFallback(unittest.TestCase):
    def test_stage_graph_qty_fallback_when_boxes_per_sheet_zero(self):
        # Spec with direct blank dimensions and simple GSM.
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Fallback Layout",
        )

        # Plant without sheet dimensions; layout will report zero blanks
        # per sheet and boxes_per_sheet will be 0.
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT_FALLBACK",
            name="Plant Fallback",
            printing_ups=1,
            diecut_ups=1,
            allow_blank_rotation=True,
            sheet_length_mm=0.0,
            sheet_width_mm=0.0,
        )

        result = compute(spec_input, plant)
        metrics = result.metrics
        extra = metrics.extra

        boxes_per_sheet = extra.get("boxes_per_sheet")
        self.assertIn(boxes_per_sheet, (0, None))

        # StageGraph should fall back to a neutral 1:1 mapping on the
        # final stage edge when boxes_per_sheet is missing or zero.
        fg_edges = [e for e in result.stage_graph.flows if e.to_stage == result.stage_graph.final_stage_key()]
        self.assertTrue(fg_edges)
        self.assertAlmostEqual(fg_edges[0].qty_per_fg, 1.0, places=6)





