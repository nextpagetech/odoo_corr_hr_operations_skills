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


class _FakeWizard:
    _render_piece_layout_planning_html = CorrEngineV2DebugWizard._render_piece_layout_planning_html
    _normalize_piece_layout_option = CorrEngineV2DebugWizard._normalize_piece_layout_option
    _piece_layout_option_matches = CorrEngineV2DebugWizard._piece_layout_option_matches

    def ensure_one(self):
        return self

    def _preview_formatter(self):
        return SimpleNamespace(
            format_integer=lambda value: str(int(round(float(value or 0.0)))),
            format_quantity=lambda value, uom="", digits=None, trim=True: str(float(value or 0.0)).rstrip("0").rstrip("."),
        )


class TestEngineV2PieceLayoutPlanningSummary(unittest.TestCase):
    def test_renderer_shows_business_summary_for_accepted_split(self):
        wizard = _FakeWizard()
        planning = {
            "enabled": True,
            "source": "accepted_auto_split",
            "layout_mode": "split_multi_piece",
            "split_axis": "width",
            "split_mode": "equal",
            "source_length_mm": 1000.0,
            "source_width_mm": 900.0,
            "is_stale": False,
            "pieces": [
                {
                    "piece_key": "piece_a",
                    "piece_name": "Piece A",
                    "piece_qty_per_fg": 1.0,
                    "blank_length_mm": 1000.0,
                    "blank_width_mm": 450.0,
                    "selected_option": {
                        "rank": 1,
                        "up_count": 1,
                        "final_sheet_length_mm": 1000.0,
                        "final_sheet_width_mm": 450.0,
                        "orientation_used": "direct",
                    },
                    "stages": [
                        {
                            "feasible_options": [
                                {
                                    "rank": 1,
                                    "up_count": 1,
                                    "final_sheet_length_mm": 1000.0,
                                    "final_sheet_width_mm": 450.0,
                                    "orientation_used": "direct",
                                    "feasible": True,
                                }
                            ]
                        }
                    ],
                },
                {
                    "piece_key": "piece_b",
                    "piece_name": "Piece B",
                    "piece_qty_per_fg": 1.0,
                    "blank_length_mm": 1000.0,
                    "blank_width_mm": 450.0,
                    "selected_option": {
                        "rank": 1,
                        "up_count": 1,
                        "final_sheet_length_mm": 1000.0,
                        "final_sheet_width_mm": 450.0,
                        "orientation_used": "direct",
                    },
                    "stages": [
                        {
                            "feasible_options": [
                                {
                                    "rank": 1,
                                    "up_count": 1,
                                    "final_sheet_length_mm": 1000.0,
                                    "final_sheet_width_mm": 450.0,
                                    "orientation_used": "direct",
                                    "feasible": True,
                                }
                            ]
                        }
                    ],
                },
            ],
        }

        html = wizard._render_piece_layout_planning_html(planning)

        self.assertIn("Current Layout Decision", html)
        self.assertIn("Split / Multi-Piece", html)
        self.assertIn("1000 x 900 mm", html)
        self.assertIn("Selected Pieces", html)
        self.assertIn("Piece A", html)
        self.assertIn("450 mm", html)
        self.assertNotIn("Planning only. No piece SFGs", html)
        self.assertNotIn("Materialization remains disabled", html)
        self.assertIn("To change or clear this split, go back to Spec -> Layout Planning.", html)


if __name__ == "__main__":
    unittest.main()
