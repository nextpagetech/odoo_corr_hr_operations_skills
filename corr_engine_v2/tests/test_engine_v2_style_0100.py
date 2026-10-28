import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.bom_adapter import build_bom_structure
from custom_addons.corr_engine_v2.engine_v2.manufacturing_plan import (
    build_materialization_plan_from_manufacturing_plan,
    build_manufacturing_plan_from_stagecontext_preview_payload,
    build_manufacturing_plan_from_stagecontexts,
)
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.styles import STYLE_REGISTRY, StyleResult
from custom_addons.corr_engine_v2.engine_v2.styles.contract import (
    MissingStyleParameterError,
    require_registered_style,
)
from custom_addons.corr_engine_v2.engine_v2.styles import (
    style_0100,
    style_0110,
    style_0201,
    style_0202,
    style_0203,
)
from custom_addons.corr_engine_v2.preview_adapter import build_preview_model


class TestEngineV2Style0100(unittest.TestCase):
    def _plant(self):
        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="STYLE_0100",
            name="0100 Roll Plant",
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

    def _spec(self, *, style_code="0100", roll_length_m=100.0, roll_width_mm=1200.0):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code=style_code,
            sfg_mode="staged",
            spec_id=100,
            spec_name=f"{style_code} Roll",
        )
        if style_code == "0100":
            spec_input.box_length_mm = roll_length_m * 1000.0
            spec_input.box_width_mm = roll_width_mm
            spec_input.box_height_mm = 0.0
        elif style_code == "0110":
            spec_input.box_length_mm = roll_length_m * 1000.0
            spec_input.box_width_mm = roll_width_mm
            spec_input.box_height_mm = 0.0
        else:
            spec_input.box_length_mm = 600.0
            spec_input.box_width_mm = 300.0
            spec_input.box_height_mm = 180.0
            spec_input.glue_flap_mm = 30.0
        return spec_input

    def _roll_spec_with_layers(self, layers):
        spec = self._spec()
        spec.board_layers = layers
        spec.liner_gsm = sum(layer["gsm"] for layer in layers if layer["type"] == "liner")
        spec.medium_gsm = sum(layer["gsm"] for layer in layers if layer["type"] == "medium")
        return spec

    def test_style_0100_uses_main_geometry_length_width(self):
        result = style_0100.compute_blank(self._spec(), self._plant())

        self.assertIsInstance(result, StyleResult)
        self.assertAlmostEqual(result["blank_length_mm"], 100000.0, places=6)
        self.assertAlmostEqual(result["blank_width_mm"], 1200.0, places=6)
        self.assertAlmostEqual(result["seam_mm"], 0.0, places=6)
        self.assertAlmostEqual(result["flap_mm"], 0.0, places=6)
        self.assertEqual(result["metadata"]["style_code"], "0100")
        self.assertEqual(result["metadata"]["family"], "roll")
        self.assertEqual(result["metadata"]["uom_basis"], "roll")
        self.assertEqual(result["metadata"]["blank_formula"], "roll_length*roll_width")

    def test_style_0100_converts_internal_length_mm_to_roll_length_m(self):
        result = style_0100.compute_blank(self._spec(roll_length_m=100.0), self._plant())

        self.assertAlmostEqual(result["metadata"]["roll_length_m"], 100.0, places=6)
        self.assertAlmostEqual(result["metadata"]["roll_width_mm"], 1200.0, places=6)

    def test_style_0100_area_formula(self):
        result = style_0100.compute_blank(self._spec(), self._plant())

        self.assertAlmostEqual(result["metadata"]["area_m2_per_fg"], 120.0, places=6)

    def test_style_0100_missing_roll_geometry_fails_clearly(self):
        missing_length = self._spec(roll_length_m=0.0, roll_width_mm=1200.0)
        with self.assertRaisesRegex(MissingStyleParameterError, "Roll Length > 0"):
            style_0100.compute_blank(missing_length, self._plant())

        missing_width = self._spec(roll_length_m=100.0, roll_width_mm=0.0)
        with self.assertRaisesRegex(MissingStyleParameterError, "Roll Width > 0"):
            style_0100.compute_blank(missing_width, self._plant())

    def test_style_0100_does_not_require_height_or_style_params(self):
        spec = self._spec()
        self.assertEqual(spec.box_height_mm, 0.0)
        self.assertFalse(hasattr(spec, "style_params"))

        result = compute(spec, self._plant())

        self.assertEqual(result.metrics.extra["geometry"]["blank_length_mm"], 100000.0)
        self.assertEqual(result.metrics.extra["geometry"]["blank_width_mm"], 1200.0)
        self.assertAlmostEqual(result.metrics.m2_per_box, 120.0, places=6)

    def test_registry_resolves_0100(self):
        handler = require_registered_style("0100", STYLE_REGISTRY)
        self.assertIs(handler, style_0100.compute_blank)

    def test_style_0100_style_master_is_parameter_free(self):
        repo_root = Path(__file__).resolve().parents[3]
        view_path = repo_root / "custom_addons/corr_engine_v2/views/engine_style_views.xml"
        root = ET.parse(view_path).getroot()
        records = {
            record.attrib.get("id"): record
            for record in root.findall(".//record")
        }

        self.assertIn("corr_engine_style_0100", records)
        self.assertNotIn("corr_engine_style_0100_param_roll_width_mm", records)
        self.assertNotIn("corr_engine_style_0100_param_roll_length_m", records)

        style_record = records["corr_engine_style_0100"]
        style_fields = {
            field.attrib.get("name"): (field.text or "")
            for field in style_record.findall("field")
        }
        self.assertEqual(style_fields.get("code"), "0100")
        self.assertEqual(style_fields.get("family"), "roll")

    def test_style_0100_view_uses_roll_length_m_helper(self):
        repo_root = Path(__file__).resolve().parents[3]
        view_path = repo_root / "custom_addons/corr_mrp_bridge/views/engine_v2_spec_views.xml"
        text = view_path.read_text(encoding="utf-8")

        self.assertIn('name="is_roll_style"', text)
        self.assertIn('string="Roll Size"', text)
        self.assertIn('name="roll_length_m" string="Roll Length (m)"', text)
        self.assertIn('name="box_width_mm" string="Roll Width (mm)"', text)

    def test_compute_exposes_0100_geometry_metadata_and_capabilities(self):
        result = compute(self._spec(), self._plant())
        style_payload = result.metrics.extra.get("style") or {}
        capabilities = style_payload.get("capabilities") or {}
        metadata = style_payload.get("metadata") or {}

        self.assertEqual(result.metrics.extra["geometry"]["blank_length_mm"], 100000.0)
        self.assertEqual(result.metrics.extra["geometry"]["blank_width_mm"], 1200.0)
        self.assertAlmostEqual(result.metrics.m2_per_box, 120.0, places=6)
        self.assertEqual(metadata.get("style_code"), "0100")
        self.assertEqual(metadata.get("family"), "roll")
        self.assertEqual(metadata.get("uom_basis"), "roll")
        self.assertAlmostEqual(metadata.get("roll_length_m"), 100.0, places=6)
        self.assertAlmostEqual(metadata.get("area_m2_per_fg"), 120.0, places=6)
        self.assertFalse(capabilities.get("allows_width_split"))
        self.assertFalse(capabilities.get("allows_length_split"))
        self.assertTrue(capabilities.get("allows_shared_upstream"))

    def test_style_0100_rm_uses_roll_area(self):
        result = compute(self._spec(), self._plant())
        liner = next(rm for rm in result.rm_requirements if rm.role == "liner")
        medium = next(rm for rm in result.rm_requirements if rm.role == "medium")

        self.assertAlmostEqual(result.metrics.m2_per_box, 120.0, places=6)
        self.assertAlmostEqual(liner.qty_per_fg, 21.6, places=6)
        self.assertAlmostEqual(medium.qty_per_fg, 14.4, places=6)

    def test_style_0100_preview_and_bom_smoke(self):
        result = compute(self._spec(), self._plant())

        preview = build_preview_model(
            {
                "stage_contexts": [
                    {"stage_code": "2ply", "stage_name": "2-ply"},
                    {"stage_code": "board", "stage_name": "Board"},
                    {"stage_code": "fg", "stage_name": "Finished Roll"},
                ],
            },
            spec_name="0100 Roll",
            style="0100",
            plant_name="0100 Roll Plant",
            order_qty=1.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )
        bom = build_bom_structure(result, order_qty=1.0)

        self.assertEqual(preview["summary"]["style"], "0100")
        self.assertTrue(preview["materials"])
        self.assertTrue(bom["components"])
        self.assertEqual(bom["product_qty"], 1.0)

    def test_0100_allows_supported_stack_configurations(self):
        cases = [
            [
                {"type": "liner", "gsm": 180.0, "layer_no": 1},
                {"type": "medium", "gsm": 120.0, "layer_no": 2},
            ],
            [
                {"type": "liner", "gsm": 180.0, "layer_no": 1},
                {"type": "medium", "gsm": 120.0, "layer_no": 2},
                {"type": "liner", "gsm": 180.0, "layer_no": 3},
            ],
            [
                {"type": "liner", "gsm": 180.0, "layer_no": 1},
                {"type": "medium", "gsm": 120.0, "layer_no": 2},
                {"type": "liner", "gsm": 180.0, "layer_no": 3},
                {"type": "medium", "gsm": 120.0, "layer_no": 4},
                {"type": "liner", "gsm": 180.0, "layer_no": 5},
            ],
            [
                {"type": "liner", "gsm": 180.0, "layer_no": 1},
                {"type": "medium", "gsm": 120.0, "layer_no": 2},
                {"type": "liner", "gsm": 180.0, "layer_no": 3},
                {"type": "medium", "gsm": 120.0, "layer_no": 4},
                {"type": "liner", "gsm": 180.0, "layer_no": 5},
                {"type": "medium", "gsm": 120.0, "layer_no": 6},
                {"type": "liner", "gsm": 180.0, "layer_no": 7},
            ],
        ]

        for layers in cases:
            with self.subTest(ply_count=len(layers)):
                result = compute(self._roll_spec_with_layers(layers), self._plant())
                paper = [
                    rm
                    for rm in result.rm_requirements
                    if rm.role in {"liner", "medium"}
                ]
                self.assertEqual(len(paper), len(layers))

    def test_roll_family_promotes_last_material_output_to_fg(self):
        contexts = [
            {
                "stage_template_id": 1001,
                "stage_contract_code": "route.stage.2ply",
                "stage_code": "2ply",
                "stage_name": "2-Ply Corrugation",
                "stage_type": "corrugation",
                "sequence": 10,
                "index": 0,
                "execution_type": "material",
                "output_stock_behavior": "semi_finished",
                "target_output_qty": 1.0,
                "target_output_uom": "unit",
                "output_kind": "roll",
                "output_descriptor": {
                    "kind": "roll",
                    "quantity": 1.0,
                    "uom": "unit",
                    "fg_equivalent_qty": 1.0,
                },
            }
        ]

        plan = build_manufacturing_plan_from_stagecontexts(
            contexts,
            spec_id=100,
            order_qty=1.0,
            metadata={"style_family": "roll", "style_code": "0100"},
        )
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        self.assertEqual(plan.fg_segment_key, "contract:route.stage.2ply")
        self.assertEqual(plan.segments[0].output.output_role, "fg")
        self.assertEqual(materialized.products[0].product_role, "fg")
        self.assertIsNone(materialized.products[0].corr_sfg_key)
        self.assertEqual(materialized.boms[0].bom_role, "fg")

    def test_0100_preview_payload_uses_roll_family_metadata_for_terminal_fg(self):
        payload = {
            "spec_id": 100,
            "style_code": "0100",
            "order_qty": 1.0,
            "metrics": {
                "extra": {
                    "style": {
                        "metadata": {
                            "style_code": "0100",
                            "family": "roll",
                            "uom_basis": "roll",
                        }
                    }
                }
            },
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "stage_template_id": 1001,
                        "stage_contract_code": "route.stage.2ply",
                        "stage_code": "2ply",
                        "stage_name": "2-Ply Corrugation",
                        "stage_type": "corrugation",
                        "sequence": 10,
                        "index": 0,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                        "output_kind": "roll",
                        "output_descriptor": {
                            "kind": "roll",
                            "quantity": 1.0,
                            "uom": "unit",
                            "fg_equivalent_qty": 1.0,
                        },
                    }
                ]
            },
            "phase5_preview_model": {
                "materials": [],
                "operations": [],
                "routed_operations": [],
                "stages": [],
            },
        }

        plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)

        self.assertEqual(plan.metadata["style_family"], "roll")
        self.assertEqual(plan.fg_segment_key, "contract:route.stage.2ply")

    def test_0201_0202_0203_0110_regression_anchors(self):
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

        blank_0110 = style_0110.compute_blank(
            self._spec(style_code="0110", roll_length_m=1.2, roll_width_mm=1000.0),
            plant,
        )
        self.assertAlmostEqual(blank_0110["blank_length_mm"], 1200.0, places=6)
        self.assertAlmostEqual(blank_0110["blank_width_mm"], 1000.0, places=6)


if __name__ == "__main__":
    unittest.main()




