import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.bom_adapter import build_bom_structure
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.styles import STYLE_REGISTRY, StyleResult
from custom_addons.corr_engine_v2.engine_v2.styles.contract import require_registered_style
from custom_addons.corr_engine_v2.engine_v2.styles import style_0201, style_0202, style_0203
from custom_addons.corr_engine_v2.preview_adapter import build_preview_model


class TestEngineV2Style0203(unittest.TestCase):
    def _plant(self):
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="STYLE_0203",
            name="0203 Style Plant",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=3000.0,
            sheet_width_mm=2000.0,
            default_glue_lap_mm=20.0,
            allow_blank_rotation=True,
            flap_basis="fixed",
            flap_fixed_mm=20.0,
            flap_percent=0.0,
            flap_percent_of="width",
        )
        plant.max_machine_width_mm = 1000.0
        plant.split_mode = "equal_only"
        return plant

    def _spec(self, *, style_code="0203", seam_mm=30.0):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code=style_code,
            sfg_mode="staged",
            spec_id=203,
            spec_name=f"{style_code} Style",
        )
        spec_input.box_length_mm = 600.0
        spec_input.box_width_mm = 300.0
        spec_input.box_height_mm = 180.0
        spec_input.glue_flap_mm = seam_mm
        return spec_input

    def test_style_0203_blank_formula(self):
        result = style_0203.compute_blank(self._spec(), self._plant())

        self.assertIsInstance(result, StyleResult)
        self.assertAlmostEqual(result["blank_length_mm"], 1830.0, places=6)
        self.assertAlmostEqual(result["blank_width_mm"], 780.0, places=6)
        self.assertAlmostEqual(result["seam_mm"], 30.0, places=6)
        self.assertAlmostEqual(result["flap_mm"], 300.0, places=6)
        self.assertEqual(result["metadata"]["style_code"], "0203")
        self.assertEqual(result["metadata"]["blank_formula"], "2*(L+W)+seam by H+2*W")
        self.assertAlmostEqual(result["metadata"]["top_flap_depth_mm"], 300.0, places=6)
        self.assertAlmostEqual(result["metadata"]["bottom_flap_depth_mm"], 300.0, places=6)
        self.assertTrue(result["allows_width_split"])
        self.assertFalse(result["allows_length_split"])
        self.assertTrue(result["allows_shared_upstream"])

    def test_style_0203_missing_dimensions_returns_none(self):
        spec_input = self._spec()
        spec_input.box_width_mm = 0.0

        self.assertIsNone(style_0203.compute_blank(spec_input, self._plant()))

    def test_registry_resolves_0203(self):
        handler = require_registered_style("0203", STYLE_REGISTRY)
        self.assertIs(handler, style_0203.compute_blank)

    def test_compute_exposes_0203_geometry_metadata_and_capabilities(self):
        result = compute(self._spec(), self._plant())
        style_payload = result.metrics.extra.get("style") or {}
        capabilities = style_payload.get("capabilities") or {}
        metadata = style_payload.get("metadata") or {}

        self.assertEqual(result.metrics.extra["geometry"]["blank_length_mm"], 1830.0)
        self.assertEqual(result.metrics.extra["geometry"]["blank_width_mm"], 780.0)
        self.assertEqual(metadata.get("style_code"), "0203")
        self.assertEqual(metadata.get("blank_formula"), "2*(L+W)+seam by H+2*W")
        self.assertEqual(metadata.get("top_flap_depth_mm"), 300.0)
        self.assertEqual(metadata.get("bottom_flap_depth_mm"), 300.0)
        self.assertTrue(capabilities.get("allows_width_split"))
        self.assertFalse(capabilities.get("allows_length_split"))
        self.assertTrue(capabilities.get("allows_shared_upstream"))
        self.assertTrue(style_payload.get("score_lines"))
        self.assertTrue(style_payload.get("fold_lines"))

    def test_style_0203_rm_uses_expanded_full_overlap_blank(self):
        result = compute(self._spec(), self._plant())
        expected_m2 = (1830.0 * 780.0) / 1_000_000.0
        liner = next(rm for rm in result.rm_requirements if rm.role == "liner")
        medium = next(rm for rm in result.rm_requirements if rm.role == "medium")

        self.assertAlmostEqual(result.metrics.m2_per_box, expected_m2, places=6)
        self.assertAlmostEqual(liner.qty_per_fg, (180.0 * expected_m2) / 1000.0, places=6)
        self.assertAlmostEqual(medium.qty_per_fg, (120.0 * expected_m2) / 1000.0, places=6)

    def test_style_0203_layout_preview_and_bom_smoke(self):
        result = compute(self._spec(), self._plant())
        extra = result.metrics.extra

        self.assertEqual(extra.get("pieces_per_box"), 1)
        self.assertEqual(extra.get("split_strategy"), "none")
        self.assertGreater(extra.get("layout_blanks_per_sheet"), 0)
        self.assertIn((extra.get("layout") or {}).get("orientation"), {"normal", "rotated"})

        preview = build_preview_model(
            {
                "stage_contexts": [
                    {"stage_code": "2ply", "stage_name": "2-ply"},
                    {"stage_code": "board", "stage_name": "Board"},
                    {"stage_code": "fg", "stage_name": "Finished Box"},
                ],
            },
            spec_name="0203 FOL",
            style="0203",
            plant_name="0203 Style Plant",
            order_qty=10.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )
        bom = build_bom_structure(result, order_qty=10.0)

        self.assertEqual(preview["summary"]["style"], "0203")
        self.assertTrue(preview["materials"])
        self.assertTrue(bom["components"])
        self.assertEqual(bom["product_qty"], 10.0)

    def test_0201_and_0202_regression_anchors(self):
        plant = self._plant()

        blank_0201 = style_0201.compute_blank(self._spec(style_code="0201"), plant)
        self.assertAlmostEqual(blank_0201["blank_length_mm"], 1830.0, places=6)
        self.assertAlmostEqual(blank_0201["blank_width_mm"], 500.0, places=6)

        spec_0202 = self._spec(style_code="0202")
        spec_0202.style_params = {"overlap_mm": 25.0}
        blank_0202 = style_0202.compute_blank(spec_0202, plant)
        self.assertAlmostEqual(blank_0202["blank_length_mm"], 1830.0, places=6)
        self.assertAlmostEqual(blank_0202["blank_width_mm"], 530.0, places=6)




