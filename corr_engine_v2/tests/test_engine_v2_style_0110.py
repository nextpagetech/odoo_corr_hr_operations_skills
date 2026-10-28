import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.bom_adapter import build_bom_structure
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.styles import STYLE_REGISTRY, StyleResult
from custom_addons.corr_engine_v2.engine_v2.styles.contract import (
    MissingStyleParameterError,
    require_registered_style,
)
from custom_addons.corr_engine_v2.engine_v2.styles import (
    style_0110,
    style_0201,
    style_0202,
    style_0203,
)
from custom_addons.corr_engine_v2.preview_adapter import build_preview_model


class TestEngineV2Style0110(unittest.TestCase):
    def _plant(self):
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="STYLE_0110",
            name="0110 Sheet Plant",
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
        plant.max_machine_width_mm = 2000.0
        plant.split_mode = "equal_only"
        return plant

    def _spec(self, *, style_code="0110", sheet_length=1200.0, sheet_width=1000.0):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code=style_code,
            sfg_mode="staged",
            spec_id=110,
            spec_name=f"{style_code} Sheet",
        )
        if style_code == "0110":
            spec_input.box_length_mm = sheet_length
            spec_input.box_width_mm = sheet_width
            spec_input.box_height_mm = 0.0
        else:
            spec_input.box_length_mm = 600.0
            spec_input.box_width_mm = 300.0
            spec_input.box_height_mm = 180.0
            spec_input.glue_flap_mm = 30.0
        return spec_input

    def test_style_0110_blank_formula(self):
        result = style_0110.compute_blank(self._spec(), self._plant())

        self.assertIsInstance(result, StyleResult)
        self.assertAlmostEqual(result["blank_length_mm"], 1200.0, places=6)
        self.assertAlmostEqual(result["blank_width_mm"], 1000.0, places=6)
        self.assertAlmostEqual(result["seam_mm"], 0.0, places=6)
        self.assertAlmostEqual(result["flap_mm"], 0.0, places=6)
        self.assertEqual(result["metadata"]["style_code"], "0110")
        self.assertEqual(result["metadata"]["family"], "sheet")
        self.assertEqual(result["metadata"]["uom_basis"], "sheet")
        self.assertEqual(result["metadata"]["blank_formula"], "sheet_length*sheet_width")
        self.assertAlmostEqual(result["metadata"]["area_m2_per_fg"], 1.2, places=6)
        self.assertFalse(result["allows_width_split"])
        self.assertFalse(result["allows_length_split"])
        self.assertTrue(result["allows_shared_upstream"])

    def test_style_0110_missing_sheet_geometry_fails_clearly(self):
        missing_length = self._spec(sheet_length=0.0, sheet_width=1000.0)
        with self.assertRaisesRegex(MissingStyleParameterError, "Sheet Length > 0"):
            style_0110.compute_blank(missing_length, self._plant())

        missing_width = self._spec(sheet_length=1200.0, sheet_width=0.0)
        with self.assertRaisesRegex(MissingStyleParameterError, "Sheet Width > 0"):
            style_0110.compute_blank(missing_width, self._plant())

    def test_style_0110_does_not_require_height_or_style_params(self):
        spec = self._spec()
        self.assertEqual(spec.box_height_mm, 0.0)
        self.assertFalse(hasattr(spec, "style_params"))

        result = compute(spec, self._plant())

        self.assertEqual(result.metrics.extra["geometry"]["blank_length_mm"], 1200.0)
        self.assertEqual(result.metrics.extra["geometry"]["blank_width_mm"], 1000.0)
        self.assertAlmostEqual(result.metrics.m2_per_box, 1.2, places=6)

    def test_registry_resolves_0110(self):
        handler = require_registered_style("0110", STYLE_REGISTRY)
        self.assertIs(handler, style_0110.compute_blank)

    def test_style_0110_style_master_is_parameter_free(self):
        repo_root = Path(__file__).resolve().parents[3]
        view_path = repo_root / "custom_addons/corr_engine_v2/views/engine_style_views.xml"
        root = ET.parse(view_path).getroot()
        records = {
            record.attrib.get("id"): record
            for record in root.findall(".//record")
        }

        self.assertIn("corr_engine_style_0110", records)
        self.assertNotIn("corr_engine_style_0110_param_sheet_length_mm", records)
        self.assertNotIn("corr_engine_style_0110_param_sheet_width_mm", records)

        style_record = records["corr_engine_style_0110"]
        style_fields = {
            field.attrib.get("name"): (field.text or "")
            for field in style_record.findall("field")
        }
        self.assertEqual(style_fields.get("code"), "0110")
        self.assertEqual(style_fields.get("family"), "sheet")

    def test_compute_exposes_0110_geometry_metadata_and_capabilities(self):
        result = compute(self._spec(), self._plant())
        style_payload = result.metrics.extra.get("style") or {}
        capabilities = style_payload.get("capabilities") or {}
        metadata = style_payload.get("metadata") or {}

        self.assertEqual(result.metrics.extra["geometry"]["blank_length_mm"], 1200.0)
        self.assertEqual(result.metrics.extra["geometry"]["blank_width_mm"], 1000.0)
        self.assertAlmostEqual(result.metrics.m2_per_box, 1.2, places=6)
        self.assertEqual(metadata.get("style_code"), "0110")
        self.assertEqual(metadata.get("family"), "sheet")
        self.assertEqual(metadata.get("uom_basis"), "sheet")
        self.assertAlmostEqual(metadata.get("area_m2_per_fg"), 1.2, places=6)
        self.assertFalse(capabilities.get("allows_width_split"))
        self.assertFalse(capabilities.get("allows_length_split"))
        self.assertTrue(capabilities.get("allows_shared_upstream"))

    def test_style_0110_rm_uses_sheet_area(self):
        result = compute(self._spec(), self._plant())
        liner = next(rm for rm in result.rm_requirements if rm.role == "liner")
        medium = next(rm for rm in result.rm_requirements if rm.role == "medium")

        self.assertAlmostEqual(result.metrics.m2_per_box, 1.2, places=6)
        self.assertAlmostEqual(liner.qty_per_fg, 0.216, places=6)
        self.assertAlmostEqual(medium.qty_per_fg, 0.144, places=6)

    def test_style_0110_preview_and_bom_smoke(self):
        result = compute(self._spec(), self._plant())

        preview = build_preview_model(
            {
                "stage_contexts": [
                    {"stage_code": "2ply", "stage_name": "2-ply"},
                    {"stage_code": "board", "stage_name": "Board"},
                    {"stage_code": "fg", "stage_name": "Finished Sheet"},
                ],
            },
            spec_name="0110 Sheet",
            style="0110",
            plant_name="0110 Sheet Plant",
            order_qty=1.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )
        bom = build_bom_structure(result, order_qty=1.0)

        self.assertEqual(preview["summary"]["style"], "0110")
        self.assertTrue(preview["materials"])
        self.assertTrue(bom["components"])
        self.assertEqual(bom["product_qty"], 1.0)

    def test_0201_0202_0203_regression_anchors(self):
        plant = self._plant()

        blank_0201 = style_0201.compute_blank(self._spec(style_code="0201"), plant)
        self.assertAlmostEqual(blank_0201["blank_length_mm"], 1830.0, places=6)
        self.assertAlmostEqual(blank_0201["blank_width_mm"], 500.0, places=6)

        spec_0202 = self._spec(style_code="0202")
        spec_0202.style_params = {"overlap_mm": 25.0}
        blank_0202 = style_0202.compute_blank(spec_0202, plant)
        self.assertAlmostEqual(blank_0202["blank_length_mm"], 1830.0, places=6)
        self.assertAlmostEqual(blank_0202["blank_width_mm"], 530.0, places=6)

        blank_0203 = style_0203.compute_blank(self._spec(style_code="0203"), plant)
        self.assertAlmostEqual(blank_0203["blank_length_mm"], 1830.0, places=6)
        self.assertAlmostEqual(blank_0203["blank_width_mm"], 780.0, places=6)




