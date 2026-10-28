import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.bom_adapter import build_bom_structure
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.styles import STYLE_REGISTRY, StyleResult
from custom_addons.corr_engine_v2.engine_v2.styles.contract import (
    MissingStyleParameterError,
    require_registered_style,
)
from custom_addons.corr_engine_v2.engine_v2.styles import style_0202
from custom_addons.corr_engine_v2.preview_adapter import build_preview_model


class TestEngineV2Style0202(unittest.TestCase):
    def _plant(self):
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="STYLE_0202",
            name="0202 Style Plant",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=3000.0,
            sheet_width_mm=1600.0,
            default_glue_lap_mm=20.0,
            allow_blank_rotation=True,
        )
        plant.max_machine_width_mm = 500.0
        plant.split_mode = "equal_only"
        return plant

    def _spec(self, *, overlap_mm=30.0):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="0202",
            sfg_mode="staged",
            spec_id=202,
            spec_name="0202 OSC",
        )
        spec_input.box_length_mm = 300.0
        spec_input.box_width_mm = 200.0
        spec_input.box_height_mm = 150.0
        spec_input.glue_flap_mm = 20.0
        if overlap_mm is not None:
            spec_input.style_params = {"overlap_mm": overlap_mm}
        return spec_input

    def test_style_0202_blank_formula(self):
        result = style_0202.compute_blank(self._spec(overlap_mm=30.0), self._plant())

        self.assertIsInstance(result, StyleResult)
        self.assertAlmostEqual(result["blank_length_mm"], 1020.0, places=6)
        self.assertAlmostEqual(result["blank_width_mm"], 410.0, places=6)
        self.assertAlmostEqual(result["seam_mm"], 20.0, places=6)
        self.assertAlmostEqual(result["flap_mm"], 130.0, places=6)
        self.assertEqual(result["metadata"]["style_code"], "0202")
        self.assertEqual(result["metadata"]["overlap_mm"], 30.0)
        self.assertTrue(result["allows_width_split"])
        self.assertFalse(result["allows_length_split"])
        self.assertTrue(result["allows_shared_upstream"])

    def test_style_0202_missing_overlap_fails_clearly(self):
        with self.assertRaisesRegex(
            MissingStyleParameterError, "FEFCO 0202 requires style parameter overlap_mm"
        ):
            style_0202.compute_blank(self._spec(overlap_mm=None), self._plant())

    def test_registry_resolves_0202(self):
        handler = require_registered_style("0202", STYLE_REGISTRY)
        self.assertIs(handler, style_0202.compute_blank)

    def test_compute_exposes_0202_metadata_and_capabilities(self):
        result = compute(self._spec(overlap_mm=30.0), self._plant())
        style_payload = result.metrics.extra.get("style") or {}
        capabilities = style_payload.get("capabilities") or {}
        metadata = style_payload.get("metadata") or {}

        self.assertEqual(result.metrics.extra["geometry"]["blank_length_mm"], 1020.0)
        self.assertEqual(result.metrics.extra["geometry"]["blank_width_mm"], 410.0)
        self.assertEqual(metadata.get("style_code"), "0202")
        self.assertEqual(metadata.get("overlap_mm"), 30.0)
        self.assertTrue(capabilities.get("allows_width_split"))
        self.assertFalse(capabilities.get("allows_length_split"))
        self.assertTrue(capabilities.get("allows_shared_upstream"))
        self.assertTrue(style_payload.get("score_lines"))
        self.assertTrue(style_payload.get("fold_lines"))

    def test_style_0202_layout_and_splitting_compatibility(self):
        result = compute(self._spec(overlap_mm=30.0), self._plant())
        extra = result.metrics.extra

        self.assertEqual(extra.get("pieces_per_box"), 1)
        self.assertEqual(extra.get("split_strategy"), "none")
        self.assertGreater(extra.get("layout_blanks_per_sheet"), 0)
        self.assertIn((extra.get("layout") or {}).get("orientation"), {"normal", "rotated"})

    def test_style_0202_preview_and_bom_smoke(self):
        result = compute(self._spec(overlap_mm=30.0), self._plant())

        preview = build_preview_model(
            {
                "stage_contexts": [
                    {"stage_code": "2ply", "stage_name": "2-ply"},
                    {"stage_code": "board", "stage_name": "Board"},
                    {"stage_code": "fg", "stage_name": "Finished Box"},
                ],
            },
            spec_name="0202 OSC",
            style="0202",
            plant_name="0202 Style Plant",
            order_qty=10.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )
        bom = build_bom_structure(result, order_qty=10.0)

        self.assertEqual(preview["summary"]["style"], "0202")
        self.assertTrue(preview["materials"])
        self.assertTrue(bom["components"])
        self.assertEqual(bom["product_qty"], 10.0)




