import json
import os
import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile


FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__),
    "fixtures",
    "engine_v2",
)


def _serialize_engine_result(result):
    metrics = result.metrics
    extra = metrics.extra or {}

    layout_blanks_per_sheet = int(extra.get("layout_blanks_per_sheet", 0) or 0)
    boxes_per_sheet = float(extra.get("boxes_per_sheet", 0.0) or 0.0)
    pieces_per_box = int(extra.get("pieces_per_box", 1) or 1)

    rm_reqs = []
    for rm in result.rm_requirements:
        rm_reqs.append(
            {
                "product_key": rm.product_key,
                "role": rm.role,
                "stage_key": rm.stage_key,
                "layer_no": rm.layer_no,
                "qty_per_fg": round(float(rm.qty_per_fg or 0.0), 6),
                "qty_uom": rm.qty_uom,
            }
        )
    rm_reqs.sort(
        key=lambda r: (
            r.get("role") or "",
            int(r.get("layer_no") or 0),
            r.get("product_key") or "",
        )
    )

    stages = []
    for s in result.stage_graph.stages:
        stages.append(
            {
                "key": s.key,
                "logical_role": s.logical_role,
                "stage_type": s.stage_type,
                "operations": list(s.operations or []),
            }
        )

    return {
        "layout_blanks_per_sheet": layout_blanks_per_sheet,
        "boxes_per_sheet": round(boxes_per_sheet, 6),
        "pieces_per_box": pieces_per_box,
        "rm_requirements": rm_reqs,
        "stage_graph_stages": stages,
    }


class TestEngineV2Snapshots(unittest.TestCase):
    def test_snapshot_0201_small_box(self):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="0201",
            sfg_mode="staged",
            spec_id=1,
            spec_name="0201 small",
        )
        # Box dimensions for 0201 style
        spec_input.box_length_mm = 300.0
        spec_input.box_width_mm = 200.0
        spec_input.box_height_mm = 150.0
        spec_input.glue_flap_mm = 20.0

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT1",
            name="Plant 1",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=2000.0,
            sheet_width_mm=1200.0,
        )

        result = compute(spec_input, plant)
        actual = _serialize_engine_result(result)

        with open(os.path.join(FIXTURES_DIR, "0201_small_box.json"), encoding="utf-8") as f:
            expected = json.load(f)

        self.assertEqual(actual, expected)

    def test_snapshot_0201_large_box_split(self):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="0201",
            sfg_mode="staged",
            spec_id=2,
            spec_name="0201 split",
        )
        # Larger box requiring blank splitting by machine limits
        spec_input.box_length_mm = 600.0
        spec_input.box_width_mm = 500.0
        spec_input.box_height_mm = 200.0
        spec_input.glue_flap_mm = 20.0

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT2",
            name="Plant 2",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=3600.0,
            sheet_width_mm=1800.0,
        )
        # Machine width limits force splitting: effective_limit = 800 mm
        plant.max_corrugator_width_mm = 900.0
        plant.max_printer_width_mm = 800.0
        plant.max_diecut_width_mm = 1000.0

        result = compute(spec_input, plant)
        actual = _serialize_engine_result(result)

        with open(os.path.join(FIXTURES_DIR, "0201_large_box_split.json"), encoding="utf-8") as f:
            expected = json.load(f)

        self.assertEqual(actual, expected)

    def test_snapshot_0201_layout_gt_ups(self):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="0201",
            sfg_mode="staged",
            spec_id=3,
            spec_name="0201 layout vs UPS",
        )
        spec_input.box_length_mm = 300.0
        spec_input.box_width_mm = 200.0
        spec_input.box_height_mm = 150.0
        spec_input.glue_flap_mm = 20.0

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PLANT3",
            name="Plant 3",
            printing_ups=2,
            diecut_ups=2,
            sheet_length_mm=2000.0,
            sheet_width_mm=2000.0,
        )

        result = compute(spec_input, plant)
        actual = _serialize_engine_result(result)

        with open(os.path.join(FIXTURES_DIR, "0201_layout_gt_ups.json"), encoding="utf-8") as f:
            expected = json.load(f)

        self.assertEqual(actual, expected)




