import unittest

from custom_addons.corr_engine_v2.engine_v2.quantity_planner import run_quantity_planner


class _Metrics:
    m2_per_box = 0.649
    extra = {
        "boxes_per_sheet": 2.0,
        "layout_blanks_per_sheet": 2,
    }


class _Node:
    def __init__(self, qty):
        self.qty = qty


class _Graph:
    nodes = {
        "sheet": _Node(50),
        "blanks": _Node(100),
        "finished_goods": _Node(100),
    }


class TestEngineV2QuantityPlanner(unittest.TestCase):
    def test_slitting_layout_outputs_feed_downstream_sheet_stages(self):
        stage_chain = [
            {
                "index": 0,
                "stage_code": "2ply",
                "stage_type": "corrugation",
                "quantity_profile_code": "sheet_based_board",
                "output_kind": "board",
                "output_stock_behavior": "board",
                "consumes_previous_output": False,
            },
            {
                "index": 1,
                "stage_code": "board",
                "stage_type": "lamination",
                "quantity_profile_code": "sheet_based_board",
                "output_kind": "board",
                "output_stock_behavior": "board",
                "consumes_previous_output": True,
            },
            {
                "index": 2,
                "stage_code": "seaming",
                "stage_type": "conversion",
                "quantity_profile_code": "per_fg_unit",
                "output_kind": "box",
                "output_stock_behavior": "fg",
                "can_be_terminal": True,
                "consumes_previous_output": True,
            },
        ]

        report = run_quantity_planner(stage_chain, _Metrics(), _Graph(), 100)
        stages = {row["stage_code"]: row for row in report["stages"]}

        self.assertEqual(report["parent_sheets_required"], 50.0)
        self.assertEqual(report["slit_sheets_produced"], 100.0)
        self.assertEqual(stages["2ply"]["target_output_qty"], 100.0)
        self.assertEqual(stages["board"]["input_qty_from_prev"], 100.0)
        self.assertEqual(stages["board"]["target_output_qty"], 100.0)
        self.assertEqual(stages["seaming"]["input_qty_from_prev"], 100.0)
        self.assertEqual(stages["seaming"]["target_output_qty"], 100.0)


if __name__ == "__main__":
    unittest.main()




