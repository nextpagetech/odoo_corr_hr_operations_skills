import unittest
import importlib.util
import sys
from types import SimpleNamespace
from pathlib import Path


def _load_debug_wizard_class():
    """Load the wizard under the Odoo addon namespace for standalone tests.

    Importing ``custom_addons.corr_mrp_bridge.wizards`` runs the package
    ``__init__`` and loads Odoo models with a non-Odoo module prefix, which
    fails outside a real Odoo addon import. These tests only need pure helper
    methods from ``engine_v2_debug_wizard.py``, so load that file directly
    under an ``odoo.addons...`` name.
    """

    module_name = "odoo.addons.corr_mrp_bridge.wizards.engine_v2_debug_wizard"
    if module_name in sys.modules:
        return sys.modules[module_name].CorrEngineV2DebugWizard

    wizard_path = (
        Path(__file__).resolve().parents[1]
        / "wizards"
        / "engine_v2_debug_wizard.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, wizard_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.CorrEngineV2DebugWizard


CorrEngineV2DebugWizard = _load_debug_wizard_class()


class _FakeWizard:
    _piece_paper_product_matches_request = CorrEngineV2DebugWizard._piece_paper_product_matches_request
    _piece_paper_product_attr = CorrEngineV2DebugWizard._piece_paper_product_attr
    _piece_paper_product_attr_float = CorrEngineV2DebugWizard._piece_paper_product_attr_float
    _piece_paper_float = CorrEngineV2DebugWizard._piece_paper_float
    _piece_paper_normalize = CorrEngineV2DebugWizard._piece_paper_normalize
    _canonical_piece_key = CorrEngineV2DebugWizard._canonical_piece_key
    _render_preview_model_rm_breakdown_html = CorrEngineV2DebugWizard._render_preview_model_rm_breakdown_html
    _render_preview_model_bom_html = CorrEngineV2DebugWizard._render_preview_model_bom_html

    def _preview_formatter(self):
        def format_quantity(qty, uom="", digits=None, trim=True):
            amount = float(qty or 0.0)
            if digits is not None:
                rendered = f"{amount:.{digits}f}"
                return rendered.rstrip("0").rstrip(".") if trim else rendered
            return str(amount).rstrip("0").rstrip(".")

        return SimpleNamespace(format_quantity=format_quantity)

    def _active_spec_record(self):
        return SimpleNamespace()

    def _piece_override_preview_enabled(self, result, spec):
        return True


class TestEngineV2PieceSpecificPreview(unittest.TestCase):
    def test_rm_breakdown_renderer_uses_fixed_three_decimals_for_quantities(self):
        wizard = _FakeWizard()
        html = wizard._render_preview_model_rm_breakdown_html(
            {
                "materials": [
                    {
                        "product": "Kraft Paper",
                        "role": "liner",
                        "stage_code": "2ply",
                        "category": "rm",
                        "qty_per_fg": 0.05,
                        "total_qty": 0.1,
                        "uom": "kg",
                    }
                ]
            }
        )

        self.assertIn("0.050", html)
        self.assertIn("0.100", html)

    def test_bom_preview_renderer_prefers_routed_operations(self):
        wizard = _FakeWizard()
        html = wizard._render_preview_model_bom_html(
            {
                "materials": [],
                "operations": [
                    {"stage": "corrugator", "input_qty": 1.0, "output_qty": 1.0, "uom": "sheet"},
                    {"stage": "diecut", "input_qty": 1.0, "output_qty": 2.0, "uom": "unit"},
                    {"stage": "conversion", "input_qty": 2.0, "output_qty": 2.0, "uom": "unit"},
                ],
                "routed_operations": [
                    {
                        "stage": "corrugator",
                        "stage_code": "2ply",
                        "stage_name": "2-Ply Corrugation",
                        "input_qty": 1.0,
                        "output_qty": 1.0,
                        "uom": "sheet",
                        "output_kind": "sheet",
                    },
                    {
                        "stage": "board",
                        "stage_code": "board",
                        "stage_name": "Board Formation",
                        "input_qty": 1.0,
                        "output_qty": 1.0,
                        "uom": "sheet",
                        "output_kind": "board",
                    },
                    {
                        "stage": "diecut",
                        "stage_code": "diecut",
                        "stage_name": "Die-cut",
                        "input_qty": 1.0,
                        "output_qty": 2.0,
                        "uom": "unit",
                        "yield_output_label": "gross blanks",
                    },
                    {
                        "stage": "peeloff",
                        "stage_code": "peeloff",
                        "stage_name": "Peel-off / Waste Stripping",
                        "input_qty": 2.0,
                        "output_qty": 2.0,
                        "uom": "unit",
                        "yield_output_label": "usable blanks",
                    },
                    {
                        "stage": "conversion",
                        "stage_code": "seaming",
                        "stage_name": "Seaming / Jointing",
                        "input_qty": 2.0,
                        "output_qty": 2.0,
                        "uom": "unit",
                        "output_kind": "box",
                    },
                ],
            }
        )

        self.assertIn("Board Formation", html)
        self.assertIn("Peel-off / Waste Stripping", html)
        self.assertIn("gross blanks", html)
        self.assertIn("usable blanks", html)
        self.assertLess(html.index("2-Ply Corrugation"), html.index("Board Formation"))
        self.assertLess(html.index("Board Formation"), html.index("Die-cut"))
        self.assertLess(html.index("Die-cut"), html.index("Peel-off / Waste Stripping"))
        self.assertLess(html.index("Peel-off / Waste Stripping"), html.index("Seaming / Jointing"))

    def test_piece_paper_matcher_rejects_wrong_gsm_variant(self):
        wizard = _FakeWizard()

        def attr(name, value):
            return SimpleNamespace(attribute_id=SimpleNamespace(name=name), name=value)

        product = SimpleNamespace(
            id=10,
            product_template_attribute_value_ids=[
                attr("GSM", "160"),
                attr("Deckle", "56"),
                attr("Color", "Natural Shade(NS)"),
                attr("BF", "18"),
            ],
            exists=lambda: True,
        )

        self.assertFalse(
            CorrEngineV2DebugWizard._piece_paper_product_matches_request(
                wizard,
                product,
                gsm=180,
                bf=18,
                color="Natural Shade",
                deckle_cm=56,
            )
        )
        self.assertTrue(
            CorrEngineV2DebugWizard._piece_paper_product_matches_request(
                wizard,
                product,
                gsm=160,
                bf=18,
                color="Natural Shade",
                deckle_cm=56,
            )
        )

    def test_build_piece_specific_rm_breakdown_payload_groups_piece_rows_and_rollup(self):
        wizard = _FakeWizard()
        result = SimpleNamespace(
            rm_requirements=[
                SimpleNamespace(
                    role="liner",
                    stage_key="2ply__piece_a",
                    layer_no=1,
                    qty_per_fg=0.3,
                    qty_uom="kg",
                    metadata={
                        "piece_key": "A",
                        "piece_index": 1,
                        "piece_name": "Piece 1",
                        "stack_source": "custom",
                        "recipe_signature": "sig-a",
                        "source_layer_no": 1,
                        "gsm": 180.0,
                        "bf": 18.0,
                        "color": "Natural Shade",
                        "flute_code": "",
                        "product_name": "[KP-180-56-NS-18] Kraft Paper",
                        "resolution_note": "Exact paper variant resolved",
                    },
                    product_key="rm_liner",
                ),
                SimpleNamespace(
                    role="medium",
                    stage_key="2ply__piece_b",
                    layer_no=2,
                    qty_per_fg=0.42,
                    qty_uom="kg",
                    metadata={
                        "piece_key": "B",
                        "piece_index": 2,
                        "piece_name": "Piece 2",
                        "stack_source": "inherited",
                        "recipe_signature": "sig-b",
                        "source_layer_no": 2,
                        "gsm": 180.0,
                        "bf": 18.0,
                        "color": "Natural Shade",
                        "flute_code": "B",
                        "product_name": "[KP-180-56-NS-18] Kraft Paper",
                        "resolution_note": "Exact paper variant resolved",
                    },
                    product_key="rm_medium",
                ),
            ]
        )

        payload = CorrEngineV2DebugWizard._build_piece_specific_rm_breakdown_payload(
            wizard, result, 10.0
        )

        self.assertTrue(payload["enabled"])
        self.assertEqual([row["piece_key"] for row in payload["rows"]], ["A", "B"])
        self.assertEqual(payload["rows"][0]["stack_source"], "custom")
        self.assertEqual(payload["rows"][1]["stack_source"], "inherited")
        self.assertEqual(payload["rows"][0]["recipe_signature"], "sig-a")
        self.assertEqual(payload["rows"][1]["recipe_signature"], "sig-b")
        self.assertEqual(payload["fg_rollup_rows"][0]["total_qty"], 3.0)
        self.assertEqual(payload["fg_rollup_rows"][1]["total_qty"], 4.2)

    def test_build_piece_specific_costing_payload_groups_piece_components(self):
        wizard = _FakeWizard()
        costing = {
            "total_cost_per_fg": 12.5,
            "total_cost_for_order": 125.0,
            "components": [
                {
                    "piece_key": "A",
                    "piece_index": 1,
                    "piece_name": "Piece 1",
                    "stack_source": "custom",
                    "recipe_signature": "sig-a",
                    "cost_per_fg": 5.0,
                    "cost_for_order": 50.0,
                },
                {
                    "piece_key": "B",
                    "piece_index": 2,
                    "piece_name": "Piece 2",
                    "stack_source": "inherited",
                    "recipe_signature": "sig-b",
                    "cost_per_fg": 7.5,
                    "cost_for_order": 75.0,
                },
                {
                    "piece_key": "",
                    "cost_per_fg": 99.0,
                    "cost_for_order": 990.0,
                },
            ],
        }

        payload = CorrEngineV2DebugWizard._build_piece_specific_costing_payload(
            wizard, costing
        )

        self.assertTrue(payload["enabled"])
        self.assertEqual([row["piece_key"] for row in payload["pieces"]], ["A", "B"])
        self.assertEqual(payload["pieces"][0]["material_cost_for_order"], 50.0)
        self.assertEqual(payload["pieces"][1]["material_cost_for_order"], 75.0)
        self.assertEqual(payload["fg_rollup"]["material_cost_per_fg"], 12.5)
        self.assertEqual(payload["fg_rollup"]["material_cost_for_order"], 125.0)




