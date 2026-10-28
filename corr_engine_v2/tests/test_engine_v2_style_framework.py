import unittest
from unittest.mock import patch

import custom_addons.corr_engine_v2.engine_v2 as engine_v2_module
from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.styles import STYLE_REGISTRY, StyleResult
from custom_addons.corr_engine_v2.engine_v2.styles.contract import (
    UnsupportedStyleError,
    require_registered_style,
)
from custom_addons.corr_engine_v2.engine_v2.styles import style_0201


class TestEngineV2StyleFramework(unittest.TestCase):
    def _plant(self):
        return PlantProfile(
            stages=["2ply", "board", "fg"],
            code="STYLE_TEST",
            name="Style Test Plant",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=3000.0,
            sheet_width_mm=1600.0,
            default_glue_lap_mm=20.0,
            flap_basis="fixed",
            flap_fixed_mm=20.0,
            flap_percent=0.0,
            flap_percent_of="width",
        )

    def _0201_spec(self):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="0201",
            sfg_mode="staged",
            spec_id=201,
            spec_name="0201 Style Framework",
        )
        spec_input.box_length_mm = 300.0
        spec_input.box_width_mm = 200.0
        spec_input.box_height_mm = 150.0
        spec_input.glue_flap_mm = 20.0
        return spec_input

    def test_0201_handler_returns_style_contract_with_unchanged_blank_dimensions(self):
        result = style_0201.compute_blank(self._0201_spec(), self._plant())

        self.assertIsInstance(result, StyleResult)
        self.assertAlmostEqual(result["blank_length_mm"], 1020.0, places=6)
        self.assertAlmostEqual(result["blank_width_mm"], 370.0, places=6)
        self.assertAlmostEqual(result["seam_mm"], 20.0, places=6)
        self.assertAlmostEqual(result["flap_mm"], 20.0, places=6)
        self.assertTrue(result["score_lines"])
        self.assertTrue(result["fold_lines"])
        self.assertTrue(result["allows_width_split"])
        self.assertFalse(result["allows_length_split"])
        self.assertTrue(result["allows_shared_upstream"])
        self.assertEqual(result["metadata"]["style_code"], "0201")

    def test_registry_resolves_0201(self):
        handler = require_registered_style("0201", STYLE_REGISTRY)
        self.assertIs(handler, style_0201.compute_blank)

    def test_unsupported_style_raises_clear_error(self):
        with self.assertRaisesRegex(UnsupportedStyleError, "Unsupported style '9999'"):
            require_registered_style("9999", STYLE_REGISTRY)

    def test_compute_exposes_style_capabilities_to_downstream_sections(self):
        result = compute(self._0201_spec(), self._plant())
        style_payload = result.metrics.extra.get("style") or {}
        capabilities = style_payload.get("capabilities") or {}

        self.assertEqual(result.metrics.extra["geometry"]["blank_length_mm"], 1020.0)
        self.assertEqual(result.metrics.extra["geometry"]["blank_width_mm"], 370.0)
        self.assertTrue(capabilities.get("allows_width_split"))
        self.assertFalse(capabilities.get("allows_length_split"))
        self.assertTrue(capabilities.get("allows_shared_upstream"))
        self.assertTrue(style_payload.get("score_lines"))
        self.assertTrue(style_payload.get("fold_lines"))

    def test_compute_rejects_non_empty_unsupported_style(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="9999",
            sfg_mode="staged",
            spec_id=9999,
            spec_name="Unsupported Style",
        )

        with self.assertRaisesRegex(UnsupportedStyleError, "Unsupported style '9999'"):
            compute(spec_input, self._plant())

    def test_unsupported_style_fails_before_downstream_resolution(self):
        spec_input = EngineSpecInput(
            blank_length_mm=500.0,
            blank_width_mm=400.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code="9999",
            sfg_mode="staged",
            spec_id=10000,
            spec_name="Unsupported Style Boundary",
        )

        with patch.object(engine_v2_module, "build_stage_graph") as stage_graph, patch.object(
            engine_v2_module, "resolve_rm_inputs"
        ) as rm_inputs, patch.object(
            engine_v2_module, "resolve_splitting"
        ) as splitting, self.assertRaisesRegex(
            UnsupportedStyleError, "Unsupported style '9999'"
        ):
            compute(spec_input, self._plant())

        stage_graph.assert_not_called()
        rm_inputs.assert_not_called()
        splitting.assert_not_called()

    def test_no_style_specific_logic_in_downstream_materialization_modules(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        forbidden_files = [
            repo_root / "custom_addons/corr_engine_v2/engine_v2/multi_piece_materialization.py",
            repo_root / "custom_addons/corr_engine_v2/engine_v2/materialization_writer.py",
            repo_root / "custom_addons/corr_engine_v2/engine_v2/stage_graph.py",
            repo_root / "custom_addons/corr_engine_v2/engine_v2/rm_rules.py",
        ]
        forbidden_tokens = (
            "STYLE_REGISTRY",
            "style_0110",
            "style_0201",
            "style_0202",
            "style_0203",
            '"0110"',
            "'0110'",
            '"0201"',
            "'0201'",
            '"0202"',
            "'0202'",
            '"0203"',
            "'0203'",
        )

        for file_path in forbidden_files:
            text = file_path.read_text(encoding="utf-8")
            for token in forbidden_tokens:
                self.assertNotIn(token, text, msg=f"{token} leaked into {file_path}")




