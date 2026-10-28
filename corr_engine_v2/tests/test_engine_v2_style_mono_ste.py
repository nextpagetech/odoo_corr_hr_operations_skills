import unittest

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.bom_adapter import build_bom_structure
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.spec_input import BoardLayerInput
from custom_addons.corr_engine_v2.engine_v2.styles import STYLE_REGISTRY, StyleResult
from custom_addons.corr_engine_v2.engine_v2.styles.contract import require_registered_style
from custom_addons.corr_engine_v2.engine_v2.styles import style_mono_ste
from custom_addons.corr_engine_v2.preview_adapter import build_preview_model


class TestEngineV2StyleMonoSte(unittest.TestCase):
    def _plant(self):
        return PlantProfile(
            stages=["printing", "diecut", "seaming"],
            code="MONO_STE_PLANT",
            name="Mono Carton Plant",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=1000.0,
            sheet_width_mm=700.0,
            default_glue_lap_mm=20.0,
            allow_blank_rotation=True,
        )

    def _spec(self, *, style_params=None):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=300.0,
            medium_gsm=0.0,
            style_code="MONO_STE",
            sfg_mode="staged",
            spec_id=301,
            spec_name="Mono STE Carton",
        )
        spec_input.box_length_mm = 120.0
        spec_input.box_width_mm = 60.0
        spec_input.box_height_mm = 180.0
        if style_params is not None:
            spec_input.style_params = dict(style_params)
        return spec_input

    def test_style_mono_ste_blank_formula_with_explicit_params(self):
        result = style_mono_ste.compute_blank(
            self._spec(
                style_params={
                    "glue_flap_mm": 18.0,
                    "top_tuck_flap_mm": 65.0,
                    "bottom_tuck_flap_mm": 70.0,
                    "trim_allowance_mm": 4.0,
                    "bleed_mm": 3.0,
                    "grain_direction": "height",
                }
            ),
            self._plant(),
        )

        self.assertIsInstance(result, StyleResult)
        self.assertAlmostEqual(result["blank_length_mm"], 382.0, places=6)
        self.assertAlmostEqual(result["blank_width_mm"], 319.0, places=6)
        self.assertAlmostEqual(result["seam_mm"], 18.0, places=6)
        self.assertAlmostEqual(result["flap_mm"], 70.0, places=6)
        self.assertEqual(result["metadata"]["style_code"], "MONO_STE")
        self.assertEqual(result["metadata"]["primary_material_role"], "carton_board")
        self.assertEqual(result["orientation_rules"]["preferred_grain_axis"], "height")
        self.assertTrue(result["allows_width_split"])
        self.assertFalse(result["allows_length_split"])
        self.assertTrue(result["allows_shared_upstream"])

    def test_style_mono_ste_defaults_tuck_flaps_to_carton_width(self):
        result = style_mono_ste.compute_blank(
            self._spec(
                style_params={
                    "glue_flap_mm": 20.0,
                    "top_tuck_flap_mm": 0.0,
                    "bottom_tuck_flap_mm": 0.0,
                    "trim_allowance_mm": 0.0,
                }
            ),
            self._plant(),
        )

        self.assertAlmostEqual(result["blank_length_mm"], 380.0, places=6)
        self.assertAlmostEqual(result["blank_width_mm"], 300.0, places=6)
        self.assertEqual(result["metadata"]["top_tuck_flap_mm"], 60.0)
        self.assertEqual(result["metadata"]["bottom_tuck_flap_mm"], 60.0)

    def test_style_mono_ste_missing_dimensions_returns_none(self):
        spec = self._spec(style_params={"glue_flap_mm": 20.0})
        spec.box_height_mm = 0.0

        self.assertIsNone(style_mono_ste.compute_blank(spec, self._plant()))

    def test_registry_resolves_mono_ste(self):
        handler = require_registered_style("mono_ste", STYLE_REGISTRY)
        self.assertIs(handler, style_mono_ste.compute_blank)

    def test_compute_exposes_mono_ste_metadata_and_capabilities(self):
        result = compute(
            self._spec(
                style_params={
                    "carton_board_gsm": 300.0,
                    "glue_flap_mm": 20.0,
                    "top_tuck_flap_mm": 60.0,
                    "bottom_tuck_flap_mm": 60.0,
                    "trim_allowance_mm": 0.0,
                    "bleed_mm": 3.0,
                    "grain_direction": "height",
                }
            ),
            self._plant(),
        )
        style_payload = result.metrics.extra.get("style") or {}
        capabilities = style_payload.get("capabilities") or {}
        metadata = style_payload.get("metadata") or {}

        self.assertEqual(result.metrics.extra["geometry"]["blank_length_mm"], 380.0)
        self.assertEqual(result.metrics.extra["geometry"]["blank_width_mm"], 300.0)
        self.assertEqual(metadata.get("style_code"), "MONO_STE")
        self.assertEqual(metadata.get("style_family"), "mono_carton")
        self.assertEqual(metadata.get("primary_material_role"), "carton_board")
        self.assertEqual([rm.role for rm in result.rm_requirements], ["carton_board"])
        self.assertEqual(result.rm_requirements[0].product_key, "rm_carton_board")
        self.assertTrue(capabilities.get("allows_width_split"))
        self.assertFalse(capabilities.get("allows_length_split"))
        self.assertTrue(capabilities.get("allows_shared_upstream"))
        self.assertTrue(style_payload.get("score_lines"))
        self.assertTrue(style_payload.get("fold_lines"))

    def test_mono_ste_suppresses_corrugated_adhesives(self):
        plant = self._plant()
        plant.enable_consumables = True
        plant.enable_adhesive = True
        plant.enable_corrugation_adhesive = True
        plant.enable_lamination_adhesive = True
        plant.adhesive_factor_corrugation_kg_per_m2 = 0.5
        plant.adhesive_factor_board_kg_per_m2 = 0.2

        result = compute(
            self._spec(
                style_params={
                    "carton_board_gsm": 300.0,
                    "glue_flap_mm": 20.0,
                    "top_tuck_flap_mm": 60.0,
                    "bottom_tuck_flap_mm": 60.0,
                }
            ),
            plant,
        )
        roles = [rm.role for rm in result.rm_requirements]

        self.assertIn("carton_board", roles)
        self.assertNotIn("adhesive_corrugation", roles)
        self.assertNotIn("adhesive_board", roles)

    def test_mono_ste_uses_selected_carton_board_source(self):
        spec = self._spec(style_params={"carton_board_gsm": 300.0, "glue_flap_mm": 20.0})
        spec.board_layers_v2 = [
            BoardLayerInput(
                sequence=10,
                role="carton_board",
                gsm=350.0,
                bf=0.0,
                color="White Back",
                material_code="CB350",
                material_product_id=901,
                material_product_name="Carton Board 350 GSM",
                material_product_uom_id=81,
                material_product_uom_name="kg",
                material_template_id=501,
                material_template_name="Carton Board",
            )
        ]

        result = compute(spec, self._plant())
        rm = result.rm_requirements[0]
        metadata = rm.metadata

        self.assertEqual(rm.role, "carton_board")
        self.assertAlmostEqual(metadata["gsm"], 350.0)
        self.assertEqual(metadata["product_id"], 901)
        self.assertEqual(metadata["resolution_product_name"], "Carton Board 350 GSM")
        self.assertEqual(metadata["writer_product_key"], "product.product:901")
        self.assertEqual(metadata["carton_board_source_mode"], "area_weight")
        self.assertEqual(rm.qty_uom, "kg")
        self.assertEqual(rm.consumption_type, "area")

        preview = build_preview_model(
            {"stage_contexts": [{"stage_code": "printing", "stage_name": "Printing"}]},
            spec_name="Mono STE Carton",
            style="MONO_STE",
            plant_name="Mono Carton Plant",
            order_qty=1.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )
        self.assertEqual(preview["materials"][0]["product"], "Carton Board 350 GSM")

    def test_mono_ste_unit_carton_board_uses_ups_units(self):
        spec = self._spec(
            style_params={
                "carton_board_gsm": 300.0,
                "carton_board_source_mode": "auto",
                "carton_board_ups": 2,
                "glue_flap_mm": 20.0,
            }
        )
        spec.board_layers_v2 = [
            BoardLayerInput(
                sequence=10,
                role="carton_board",
                gsm=300.0,
                material_product_id=902,
                material_product_name="Pre-cut Carton Board Sheet",
                material_product_uom_id=82,
                material_product_uom_name="Units",
            )
        ]

        result = compute(spec, self._plant())
        rm = result.rm_requirements[0]

        self.assertEqual(rm.role, "carton_board")
        self.assertEqual(rm.qty_uom, "Units")
        self.assertEqual(rm.consumption_type, "unit")
        self.assertAlmostEqual(rm.qty_per_fg, 0.5)
        self.assertEqual(rm.metadata["carton_board_source_mode"], "unit_sheet")
        self.assertEqual(rm.metadata["carton_board_ups"], 2)
        self.assertEqual(rm.metadata["order_qty_rounding"], "ceil_by_ups")

        bom = build_bom_structure(result, order_qty=5.0)
        self.assertEqual(bom["components"][0]["qty"], 3.0)
        self.assertEqual(bom["components"][0]["uom"], "Units")

        preview = build_preview_model(
            {"stage_contexts": [{"stage_code": "printing", "stage_name": "Printing"}]},
            spec_name="Mono STE Carton",
            style="MONO_STE",
            plant_name="Mono Carton Plant",
            order_qty=5.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )
        self.assertEqual(preview["materials"][0]["total_qty"], 3.0)
        self.assertEqual(preview["materials"][0]["uom"], "Units")

    def test_style_mono_ste_preview_and_bom_smoke(self):
        result = compute(
            self._spec(style_params={"carton_board_gsm": 300.0, "glue_flap_mm": 20.0}),
            self._plant(),
        )

        preview = build_preview_model(
            {
                "stage_contexts": [
                    {"stage_code": "printing", "stage_name": "Printing"},
                    {"stage_code": "diecut", "stage_name": "Die-cut"},
                    {"stage_code": "seaming", "stage_name": "Folding / Gluing"},
                ],
            },
            spec_name="Mono STE Carton",
            style="MONO_STE",
            plant_name="Mono Carton Plant",
            order_qty=10.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )
        bom = build_bom_structure(result, order_qty=10.0)

        self.assertEqual(preview["summary"]["style"], "MONO_STE")
        self.assertTrue(preview["materials"])
        self.assertTrue(bom["components"])
        self.assertEqual(bom["product_qty"], 10.0)


if __name__ == "__main__":
    unittest.main()
