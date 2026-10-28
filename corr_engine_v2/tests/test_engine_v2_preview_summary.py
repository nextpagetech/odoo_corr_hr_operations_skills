import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


def _load_debug_wizard_module():
    module_name = "odoo.addons.corr_engine_v2.wizards.engine_v2_debug_wizard"
    if module_name in sys.modules:
        return sys.modules[module_name]

    wizard_path = (
        Path(__file__).resolve().parents[1]
        / "wizards"
        / "engine_v2_debug_wizard.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, wizard_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_wizard_module = _load_debug_wizard_module()
CorrEngineV2DebugWizard = _wizard_module.CorrEngineV2DebugWizard
_build_business_summary_sections = _wizard_module._build_business_summary_sections


class _FakeWizard:
    _render_preview_model_summary_html = CorrEngineV2DebugWizard._render_preview_model_summary_html
    _build_summary_render_context = CorrEngineV2DebugWizard._build_summary_render_context
    _build_summary_render_payload = CorrEngineV2DebugWizard._build_summary_render_payload
    _summary_route_stage_codes = CorrEngineV2DebugWizard._summary_route_stage_codes
    _summary_style_display = CorrEngineV2DebugWizard._summary_style_display
    _summary_required_sheets = CorrEngineV2DebugWizard._summary_required_sheets
    _get_cost_source = CorrEngineV2DebugWizard._get_cost_source

    def __init__(self, spec):
        self._spec = spec
        self.plant_id = getattr(spec, "plant_id", None)
        self.cost_source = "standard_price"

    def _active_spec_record(self):
        return self._spec

    def ensure_one(self):
        return self

    def _preview_formatter(self):
        return CorrEngineV2DebugWizard._preview_formatter(self)


class TestEngineV2PreviewSummary(unittest.TestCase):
    def test_box_summary_uses_fg_labels(self):
        payload = {
            "blank_length_mm_display": "1200.00",
            "blank_width_mm_display": "800.00",
            "area_per_fg_m2_display": "0.960000",
            "weight_per_fg_kg_display": "0.420000",
            "required_sheets_display": "100",
            "material_cost_per_unit_display": "$ 3.00",
            "total_production_cost_for_order_display": "$ 8.00",
        }
        context = {
            "spec_name": "0201 Test",
            "finished_product": "0201 Box",
            "style_display": "Regular Slotted Container (0201)",
            "plant_name": "DEFAULT",
            "routing_profile_name": "RSC Route",
            "order_qty_display": "100",
            "cost_source_label": "Standard Cost",
            "operation_flow_display": "2ply -> board -> diecut",
        }

        sections = _build_business_summary_sections(payload, context)
        geometry_rows = dict(sections[1][1])
        production_rows = dict(sections[2][1])

        self.assertEqual(geometry_rows["Blank Length"], "1200.00 mm")
        self.assertEqual(geometry_rows["Blank Width"], "800.00 mm")
        self.assertEqual(geometry_rows["Area per FG"], "0.960000 m2")
        self.assertEqual(geometry_rows["Weight per FG"], "0.420000 kg")
        self.assertEqual(production_rows["Required Sheets"], "100 sheets")
        self.assertEqual(production_rows["Cost per Unit"], "$ 3.00")
        self.assertEqual(production_rows["Total Cost for Order"], "$ 8.00")
        self.assertNotIn("Blanks Produced", production_rows)
        self.assertNotIn("Sheet Length", geometry_rows)

    def test_sheet_summary_uses_sheet_labels_only(self):
        payload = {
            "sheet_length_mm_display": "2020.00",
            "sheet_width_mm_display": "500.00",
            "area_per_sheet_m2_display": "1.010000",
            "required_sheets_display": "1",
            "material_cost_per_unit_display": "$ 3.00",
            "total_production_cost_for_order_display": "$ 8.00",
        }
        context = {
            "is_sheet_style": True,
            "style_display": "Corrugated Sheet (0110)",
            "order_qty_display": "1",
        }

        sections = _build_business_summary_sections(payload, context)
        geometry_rows = dict(sections[1][1])

        self.assertEqual(geometry_rows["Sheet Length"], "2020.00 mm")
        self.assertEqual(geometry_rows["Sheet Width"], "500.00 mm")
        self.assertEqual(geometry_rows["Area per Sheet"], "1.010000 m2")
        production_rows = dict(sections[2][1])
        self.assertEqual(production_rows["Cost per Unit"], "$ 3.00")
        self.assertNotIn("Blank Length", geometry_rows)

    def test_roll_summary_uses_roll_labels_only(self):
        payload = {
            "roll_length_m_display": "120.000",
            "roll_width_mm_display": "1500.00",
            "area_per_roll_m2_display": "180.000000",
            "material_cost_per_unit_display": "$ 3.00",
            "total_production_cost_for_order_display": "$ 8.00",
        }
        context = {
            "is_roll_style": True,
            "style_display": "Roll Product (0100)",
            "order_qty_display": "1",
        }

        sections = _build_business_summary_sections(payload, context)
        geometry_rows = dict(sections[1][1])

        self.assertEqual(geometry_rows["Roll Length"], "120.000 m")
        self.assertEqual(geometry_rows["Roll Width"], "1500.00 mm")
        self.assertEqual(geometry_rows["Area per Roll"], "180.000000 m2")
        production_rows = dict(sections[2][1])
        self.assertEqual(production_rows["Total Cost for Order"], "$ 8.00")
        self.assertNotIn("Blank Length", geometry_rows)

    def test_diecut_sheet_summary_uses_yield_labels(self):
        payload = {
            "sheet_length_mm_display": "1000.00",
            "sheet_width_mm_display": "800.00",
            "fg_per_sheet_display": "2",
            "area_per_sheet_m2_display": "0.800000",
            "area_per_fg_m2_display": "0.400000",
            "required_sheets_display": "1",
            "gross_blanks_produced_display": "2",
            "material_cost_per_unit_display": "$ 3.00",
            "total_production_cost_for_order_display": "$ 8.00",
        }
        context = {
            "is_diecut_sheet_style": True,
            "style_display": "Custom Diecut Sheet",
            "order_qty_display": "1",
        }

        sections = _build_business_summary_sections(payload, context)
        geometry_rows = dict(sections[1][1])
        production_rows = dict(sections[2][1])

        self.assertEqual(geometry_rows["FG per Sheet"], "2 fg/sheet")
        self.assertEqual(geometry_rows["Required Sheets"], "1 sheets")
        self.assertEqual(production_rows["Required Sheets"], "1 sheets")
        self.assertEqual(production_rows["Gross Blanks Produced"], "2 blanks")
        self.assertEqual(production_rows["Cost per Unit"], "$ 3.00")
        self.assertNotIn("FG Output Qty", production_rows)

    def test_stagecontext_summary_omits_route_section(self):
        spec = SimpleNamespace(
            name="CS Duplicate",
            product_tmpl_id=SimpleNamespace(display_name="Custom Sheet FG", name="Custom Sheet FG"),
            style_id=SimpleNamespace(display_name="Custom Diecut Sheet", name="Custom Diecut Sheet", code="DIECUT_SHEET"),
            plant_id=SimpleNamespace(name="DEFAULT"),
            routing_profile_id=SimpleNamespace(display_name="Main Route", name="Main Route"),
            is_sheet_style=False,
            is_roll_style=False,
            is_diecut_sheet_style=True,
        )
        wizard = _FakeWizard(spec)
        preview_model = {
            "warnings": [],
            "stages": [
                {"stage_code": "2ply"},
                {"stage_code": "board"},
                {"stage_code": "diecut"},
                {"stage_code": "peeloff"},
                {"stage_code": "seaming"},
            ],
            "summary": {
                "order_qty": 1.0,
                "routing_stage_count": 5,
                "context": {
                    "style_display": "Custom Diecut Sheet",
                    "plant_name": "DEFAULT",
                    "finished_product": "Custom Sheet FG",
                    "routing_profile_name": "Main Route",
                    "order_qty_display": "1",
                    "cost_source_label": "Standard Cost",
                    "is_diecut_sheet_style": True,
                    "sheet_length_mm_display": "1000.00",
                    "sheet_width_mm_display": "800.00",
                    "fg_per_sheet_display": "2",
                    "required_sheets_display": "1",
                    "gross_blanks_produced_display": "2",
                    "material_cost_per_unit_display": "$ 3.00",
                    "total_production_cost_for_order_display": "$ 8.00",
                },
            },
        }

        html = wizard._render_preview_model_summary_html(preview_model)

        self.assertIn("Quick Manufacturing Summary", html)
        self.assertIn("Cost per Unit", html)
        self.assertNotIn("Quick Route", html)
        self.assertNotIn("Routing Profile", html)


if __name__ == "__main__":
    unittest.main()
