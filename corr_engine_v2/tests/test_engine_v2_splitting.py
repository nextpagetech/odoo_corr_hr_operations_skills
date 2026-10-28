import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, EngineMetrics, compute
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.splitting import resolve_splitting


class TestEngineV2Splitting(unittest.TestCase):
    def test_no_split_when_within_limits(self):
        spec_input = EngineSpecInput(
            blank_length_mm=800.0,
            blank_width_mm=600.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="No Split",
        )
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT",
            name="Plant",
            printing_ups=1,
            diecut_ups=1,
        )
        # Machine limits higher than blank width -> no split.
        plant.max_corrugator_width_mm = 1000.0
        plant.max_printer_width_mm = 900.0
        plant.max_diecut_width_mm = 800.0

        metrics = EngineMetrics()
        pieces_per_box, eff_width = resolve_splitting(spec_input, plant, metrics)

        self.assertEqual(pieces_per_box, 1)
        self.assertAlmostEqual(eff_width, 600.0, places=6)
        self.assertEqual(metrics.extra.get("pieces_per_box"), 1)

    def test_split_two_pieces(self):
        spec_input = EngineSpecInput(
            blank_length_mm=1000.0,
            blank_width_mm=1600.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=2,
            spec_name="Split Two Pieces",
        )
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT2",
            name="Plant 2",
            printing_ups=1,
            diecut_ups=1,
        )
        # Effective width limit = 800 -> 1600/800 => 2 pieces.
        plant.max_corrugator_width_mm = 900.0
        plant.max_printer_width_mm = 800.0
        plant.max_diecut_width_mm = 1000.0

        metrics = EngineMetrics()
        pieces_per_box, eff_width = resolve_splitting(spec_input, plant, metrics)

        self.assertEqual(pieces_per_box, 2)
        self.assertAlmostEqual(eff_width, 800.0, places=6)
        self.assertEqual(metrics.extra.get("pieces_per_box"), 2)

    def test_split_three_pieces(self):
        spec_input = EngineSpecInput(
            blank_length_mm=1000.0,
            blank_width_mm=2000.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=3,
            spec_name="Split Three Pieces",
        )
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT3",
            name="Plant 3",
            printing_ups=1,
            diecut_ups=1,
        )
        # Effective width limit = 700 -> ceil(2000/700) = 3 pieces.
        plant.max_corrugator_width_mm = 900.0
        plant.max_printer_width_mm = 750.0
        plant.max_diecut_width_mm = 700.0

        metrics = EngineMetrics()
        pieces_per_box, eff_width = resolve_splitting(spec_input, plant, metrics)

        self.assertEqual(pieces_per_box, 3)
        self.assertAlmostEqual(eff_width, 2000.0 / 3.0, places=6)
        self.assertEqual(metrics.extra.get("pieces_per_box"), 3)

    def test_stage_graph_qty_with_split(self):
        # Integration test: splitting + layout + StageGraph quantities.
        spec_input = EngineSpecInput(
            blank_length_mm=1000.0,
            blank_width_mm=1600.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=4,
            spec_name="Split Graph",
        )
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT4",
            name="Plant 4",
            printing_ups=1,
            diecut_ups=1,
        )
        plant.max_corrugator_width_mm = 900.0
        plant.max_printer_width_mm = 800.0
        plant.max_diecut_width_mm = 1000.0
        # Sheet size chosen so that after splitting width to 800, layout
        # yields 6 blanks per sheet: floor(3000/1000)=3, floor(1600/800)=2.
        plant.sheet_length_mm = 3000.0
        plant.sheet_width_mm = 1600.0

        result = compute(spec_input, plant)
        metrics = result.metrics
        extra = metrics.extra

        self.assertEqual(extra.get("pieces_per_box"), 2)
        self.assertEqual(extra.get("layout_blanks_per_sheet"), 6)

        # Final edge quantity should be pieces_per_box / blanks_per_sheet.
        fg_edges = [
            e for e in result.stage_graph.flows if e.to_stage == result.stage_graph.final_stage_key()
        ]
        self.assertTrue(fg_edges)
        self.assertAlmostEqual(fg_edges[0].qty_per_fg, 2.0 / 6.0, places=6)

    def test_equal_split_with_v2_plant_constraints(self):
        # V2-style split using max_machine_width_mm and split_mode.
        spec_input = EngineSpecInput(
            blank_length_mm=1000.0,
            blank_width_mm=1600.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=5,
            spec_name="V2 Split Equal",
        )
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT_V2",
            name="Plant V2",
            printing_ups=1,
            diecut_ups=1,
        )
        plant.max_machine_width_mm = 800.0
        plant.split_mode = "equal_only"

        metrics = EngineMetrics()
        pieces_per_box, eff_width = resolve_splitting(spec_input, plant, metrics)

        self.assertEqual(pieces_per_box, 2)
        self.assertAlmostEqual(eff_width, 800.0, places=6)
        self.assertEqual(metrics.extra.get("pieces_per_box"), 2)
        self.assertEqual(metrics.extra.get("split_strategy"), "equal")

    def test_split_disabled_when_split_mode_none_and_v2_limits(self):
        # When split_mode is explicitly 'none' with V2 limits, no split
        # should occur even if the blank width exceeds the machine limit.
        spec_input = EngineSpecInput(
            blank_length_mm=1000.0,
            blank_width_mm=1600.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="",
            sfg_mode="staged",
            spec_id=6,
            spec_name="V2 No Split",
        )
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT_V2_NONE",
            name="Plant V2 None",
            printing_ups=1,
            diecut_ups=1,
        )
        plant.max_machine_width_mm = 800.0
        plant.split_mode = "none"

        metrics = EngineMetrics()
        pieces_per_box, eff_width = resolve_splitting(spec_input, plant, metrics)

        self.assertEqual(pieces_per_box, 1)
        self.assertAlmostEqual(eff_width, 1600.0, places=6)
        self.assertEqual(metrics.extra.get("pieces_per_box"), 1)
        self.assertEqual(metrics.extra.get("split_strategy"), "none")




