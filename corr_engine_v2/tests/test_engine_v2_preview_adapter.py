import unittest

from custom_addons.corr_engine_v2.preview_adapter import (
    _apply_multi_piece_fg_rollup_from_legacy_requirements,
    _dedupe_material_rows_prefer_resolved,
    _suppress_unit_top_sheet_legacy_paper_fallbacks,
    build_preview_model,
)


class TestEngineV2PreviewAdapter(unittest.TestCase):
    def test_global_consumables_off_suppresses_generic_shadow_pending_consumable(self):
        shadow_runtime = {
            "consumable_state": {
                "enable_consumables": False,
                "enable_lamination_film": False,
            },
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "top_sheet_film",
                    "stage_name": "Top-sheet Film",
                    "stage_type": "auxiliary",
                    "execution_type": "material",
                    "target_output_qty": 1.0,
                    "target_output_uom": "sheet",
                    "output_stock_behavior": "semi_finished",
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    "rm_attachments": [],
                    "consumable_attachments": [
                        {
                            "role_code": "COMP_GENERIC",
                            "semantic_role": "comp_generic",
                            "material_type": "consumable",
                            "product_tmpl_id": 99,
                            "source": "material_profile_rule",
                        }
                    ],
                    "intermediate_attachments": [],
                }
            ],
        }

        preview = build_preview_model(shadow_runtime, order_qty=1.0)

        self.assertFalse(preview["materials"])
        trace = preview["material_row_trace"]
        self.assertTrue(
            any(item["action"] == "suppressed_disabled_consumable_attachment" for item in trace)
        )

    def test_unmatched_consumable_like_rm_attachment_does_not_render_shadow_pending_row(self):
        shadow_runtime = {
            "consumable_state": {
                "enable_consumables": True,
                "enable_stitching_wire": True,
            },
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "seaming",
                    "stage_name": "Seaming",
                    "stage_type": "conversion",
                    "execution_type": "material",
                    "target_output_qty": 1.0,
                    "target_output_uom": "unit",
                    "output_stock_behavior": "semi_finished",
                    "output_descriptor": {"kind": "box", "uom": "unit"},
                    "rm_attachments": [
                        {
                            "role_code": "COMP_GENERIC",
                            "semantic_role": "wire",
                            "material_type": "consumable",
                            "product_tmpl_id": 99,
                            "source": "material_profile_rule",
                        }
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                }
            ],
        }

        preview = build_preview_model(shadow_runtime, order_qty=1.0)

        self.assertFalse(preview["materials"])
        self.assertTrue(
            any(
                item["action"] == "suppressed_shadow_pending_consumable_attachment"
                for item in preview["material_row_trace"]
            )
        )

    def test_top_surface_liner_moves_to_board_when_2ply_and_board_stages_exist(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 1.0,
                    "target_output_uom": "sheet",
                    "output_stock_behavior": "semi_finished",
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_code": "board",
                    "stage_name": "Board Making",
                    "stage_type": "board",
                    "execution_type": "material",
                    "target_output_qty": 1.0,
                    "target_output_uom": "sheet",
                    "output_stock_behavior": "semi_finished",
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                },
            ],
        }
        rm_requirements = [
            {
                "product_key": "liner_1",
                "role": "liner",
                "stage_key": "2ply",
                "layer_no": 1,
                "qty_per_fg": 0.17,
                "qty_uom": "kg",
                "metadata": {"layer_no": 1, "resolution_product_name": "Liner 1"},
            },
            {
                "product_key": "medium_2",
                "role": "medium",
                "stage_key": "2ply",
                "layer_no": 2,
                "qty_per_fg": 0.24,
                "qty_uom": "kg",
                "metadata": {"layer_no": 2, "resolution_product_name": "Medium 2"},
            },
            {
                "product_key": "liner_3",
                "role": "liner",
                "stage_key": "2ply",
                "layer_no": 3,
                "qty_per_fg": 0.17,
                "qty_uom": "kg",
                "metadata": {"layer_no": 3, "resolution_product_name": "Liner 3"},
            },
        ]

        preview = build_preview_model(
            shadow_runtime,
            order_qty=1.0,
            rm_requirements=rm_requirements,
        )

        self.assertEqual(
            [(row["stage_code"], row["role"], row.get("layer_no")) for row in preview["materials"]],
            [("board", "liner", 1), ("2ply", "medium", 2), ("2ply", "liner", 3)],
        )
        board_row = preview["materials"][0]
        self.assertEqual(board_row["metadata"]["assigned_route_stage"], "board")
        self.assertEqual(board_row["metadata"]["fallback_reason"], "top_surface_liner_board_stage")
        self.assertIn("assigned to board stage as top surface liner", board_row["notes"])
        self.assertIn(
            "assigned_top_surface_liner_to_board_stage",
            [entry["action"] for entry in preview["material_row_trace"]],
        )

    def test_explicit_top_sheet_route_keeps_3ply_liner_on_2ply(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "top_sheet_cut",
                    "stage_name": "Top Sheet Cut",
                    "stage_type": "auxiliary",
                    "capability_code": "cutting",
                    "execution_type": "material",
                    "target_output_qty": 1.0,
                    "target_output_uom": "sheet",
                    "output_stock_behavior": "semi_finished",
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 1.0,
                    "target_output_uom": "sheet",
                    "output_stock_behavior": "semi_finished",
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                },
                {
                    "index": 2,
                    "sequence": 30,
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 1.0,
                    "target_output_uom": "sheet",
                    "output_stock_behavior": "semi_finished",
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                },
            ],
        }
        rm_requirements = [
            {
                "product_key": "top_sheet",
                "role": "top_roll",
                "stage_key": "top_sheet_cut",
                "layer_no": 1,
                "qty_per_fg": 0.040,
                "qty_uom": "kg",
                "metadata": {"layer_no": 1, "resolution_product_name": "Top Sheet"},
            },
            {
                "product_key": "medium_2",
                "role": "medium",
                "stage_key": "2ply",
                "layer_no": 2,
                "qty_per_fg": 0.044,
                "qty_uom": "kg",
                "metadata": {"layer_no": 2, "resolution_product_name": "Medium"},
            },
            {
                "product_key": "liner_3",
                "role": "liner",
                "stage_key": "2ply",
                "layer_no": 3,
                "qty_per_fg": 0.040,
                "qty_uom": "kg",
                "metadata": {"layer_no": 3, "resolution_product_name": "Liner"},
            },
        ]

        preview = build_preview_model(
            shadow_runtime,
            order_qty=1.0,
            rm_requirements=rm_requirements,
        )

        self.assertEqual(
            [(row["stage_code"], row["role"], row.get("layer_no")) for row in preview["materials"]],
            [("top_sheet_cut", "top_roll", 1), ("2ply", "medium", 2), ("2ply", "liner", 3)],
        )
        self.assertFalse(
            any(row["stage_code"] == "board" and row["role"] == "liner" for row in preview["materials"])
        )
        self.assertNotIn(
            "assigned_top_surface_liner_to_board_stage",
            [entry["action"] for entry in preview["material_row_trace"]],
        )

    def test_preview_model_carries_summary_context(self):
        preview = build_preview_model(
            {
                "route_source": {"selection_source": "routing_profile"},
                "routing": {"selection_source": "routing_profile", "warnings": [], "stages": []},
                "material_requirements": [],
                "stage_contexts": [],
            },
            order_qty=1.0,
            summary_context={
                "style_display": "Custom Diecut Sheet",
                "routing_profile_name": "Main Route",
                "sheet_length_mm_display": "1000.00",
            },
        )

        self.assertEqual(
            preview["summary"]["context"]["style_display"],
            "Custom Diecut Sheet",
        )
        self.assertEqual(
            preview["summary"]["context"]["sheet_length_mm_display"],
            "1000.00",
        )

    def test_material_row_dedupe_rolls_up_multi_piece_quantities_to_fg_level(self):
        row_trace = []
        rows = [
            {
                "stage_code": "2ply__piece_A",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-56-NS-18] Kraft Paper",
                "layer_no": 2,
                "qty_per_fg": 0.30,
                "total_qty": 3.0,
                "uom": "kg",
                "quantity_source": "legacy_rm_requirement",
                "metadata": {"multi_piece": True, "piece_key": "A"},
                "notes": [],
            },
            {
                "stage_code": "2ply__piece_B",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-56-NS-18] Kraft Paper",
                "layer_no": 2,
                "qty_per_fg": 0.30,
                "total_qty": 3.0,
                "uom": "kg",
                "quantity_source": "legacy_rm_requirement",
                "metadata": {"multi_piece": True, "piece_key": "B"},
                "notes": [],
            },
        ]

        deduped = _dedupe_material_rows_prefer_resolved(rows, row_trace)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["qty_per_fg"], 0.60)
        self.assertEqual(deduped[0]["total_qty"], 6.0)
        self.assertEqual(deduped[0]["metadata"]["rollup_piece_keys"], ["A", "B"])
        self.assertTrue(deduped[0]["metadata"]["fg_level_rollup"])
        self.assertIn("multi-piece FG-level quantity rollup", deduped[0]["notes"])
        self.assertIn("merged_multi_piece_material_row", [entry["action"] for entry in row_trace])

    def test_material_row_rollup_updates_paper_resolution_rows_from_piece_requirements(self):
        row_trace = []
        rows = [
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "medium",
                "product": "[KP-180-56-NS-18] Kraft Paper",
                "layer_no": 1,
                "qty_per_fg": 0.42,
                "total_qty": 0.42,
                "uom": "kg",
                "quantity_source": "paper_resolution",
                "metadata": {"resolution_product_name": "[KP-180-56-NS-18] Kraft Paper"},
                "notes": [],
            },
        ]
        legacy = [
            {
                "stage_key": "2ply__piece_A",
                "category": "rm",
                "role": "medium",
                "product_key": "medium_a",
                "layer_no": 1,
                "qty_per_fg": 0.42,
                "qty_uom": "kg",
                "metadata": {
                    "multi_piece": True,
                    "piece_key": "A",
                    "product_id": 3038,
                },
            },
            {
                "stage_key": "2ply__piece_B",
                "category": "rm",
                "role": "medium",
                "product_key": "medium_b",
                "layer_no": 1,
                "qty_per_fg": 0.42,
                "qty_uom": "kg",
                "metadata": {
                    "multi_piece": True,
                    "piece_key": "B",
                    "product_id": 3038,
                },
            },
        ]

        rolled = _apply_multi_piece_fg_rollup_from_legacy_requirements(rows, legacy, row_trace, 1.0)

        self.assertEqual(rolled[0]["qty_per_fg"], 0.84)
        self.assertEqual(rolled[0]["total_qty"], 0.84)
        self.assertEqual(rolled[0]["metadata"]["rollup_piece_keys"], ["A", "B"])
        self.assertEqual(rolled[0]["metadata"]["fg_level_rollup_source"], "legacy_rm_requirements")
        self.assertIn("applied_multi_piece_fg_rollup_quantity", [entry["action"] for entry in row_trace])

    def test_material_row_rollup_recovers_sparse_paper_display_attributes(self):
        row_trace = []
        rows = [
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-56-NS-18] Kraft Paper (180, 56, Natural Shade(NS), 18)",
                "layer_no": 4,
                "qty_per_fg": 0.61,
                "total_qty": 0.61,
                "uom": "kg",
                "quantity_source": "legacy_rm_requirement",
                "metadata": {"multi_piece": True, "piece_key": "A"},
                "notes": [],
            },
        ]
        legacy = [
            {
                "stage_key": "2ply__piece_A",
                "category": "rm",
                "role": "liner",
                "product_key": "liner_a",
                "layer_no": 4,
                "qty_per_fg": 0.305,
                "qty_uom": "kg",
                "metadata": {"multi_piece": True, "piece_key": "A", "product_id": 3038},
            },
            {
                "stage_key": "2ply__piece_B",
                "category": "rm",
                "role": "liner",
                "product_key": "liner_b",
                "layer_no": 4,
                "qty_per_fg": 0.305,
                "qty_uom": "kg",
                "metadata": {"multi_piece": True, "piece_key": "B", "product_id": 3038},
            },
        ]

        rolled = _apply_multi_piece_fg_rollup_from_legacy_requirements(rows, legacy, row_trace, 1.0)
        metadata = rolled[0]["metadata"]

        self.assertEqual(metadata["color"], "Natural Shade(NS)")
        self.assertEqual(metadata["bf"], "18")
        self.assertEqual(metadata["gsm"], "180")

    def test_material_row_dedupe_prefers_resolved_paper_row_over_legacy_duplicate(self):
        row_trace = []
        rows = [
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-112-NS-18] Kraft Paper",
                "layer_no": 2,
                "qty_per_fg": 0.61,
                "uom": "kg",
                "quantity_source": "legacy_rm_requirement",
                "metadata": {},
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-112-NS-18] Kraft Paper",
                "layer_no": 2,
                "qty_per_fg": 0.61,
                "uom": "kg",
                "quantity_source": "paper_resolution",
                "metadata": {"resolution_product_name": "[KP-180-112-NS-18] Kraft Paper"},
            },
        ]

        deduped = _dedupe_material_rows_prefer_resolved(rows, row_trace)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["quantity_source"], "paper_resolution")
        self.assertEqual(row_trace[0]["action"], "suppressed_duplicate_legacy_material_row")

    def test_material_row_dedupe_handles_unit_top_sheet_shifted_liner_duplicate(self):
        row_trace = []
        rows = [
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-112-NS-18] Kraft Paper",
                "layer_no": 1,
                "qty_per_fg": 0.61,
                "uom": "kg",
                "quantity_source": "legacy_rm_requirement",
                "metadata": {},
            },
            {
                "stage_code": "board",
                "category": "rm",
                "role": "top_sheet_external",
                "product": "[TOP:10-897-751-00363] Top Sheets",
                "layer_no": 1,
                "qty_per_fg": 1.0,
                "uom": "unit",
                "quantity_source": "legacy_rm_requirement",
                "metadata": {"top_sheet_product_id": 104},
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-112-NS-18] Kraft Paper",
                "layer_no": 2,
                "qty_per_fg": 0.61,
                "uom": "kg",
                "quantity_source": "paper_resolution",
                "metadata": {"resolution_product_name": "[KP-180-112-NS-18] Kraft Paper"},
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "medium",
                "product": "[KP-180-112-NS-18] Kraft Paper",
                "layer_no": 2,
                "qty_per_fg": 0.84,
                "uom": "kg",
                "quantity_source": "legacy_rm_requirement",
                "metadata": {},
            },
        ]

        deduped = _dedupe_material_rows_prefer_resolved(rows, row_trace)

        self.assertEqual(
            [(row["role"], row.get("quantity_source")) for row in deduped],
            [
                ("liner", "legacy_rm_requirement"),
                ("top_sheet_external", "legacy_rm_requirement"),
                ("liner", "paper_resolution"),
                ("medium", "legacy_rm_requirement"),
            ],
        )
        self.assertEqual(len([row for row in deduped if row["role"] == "liner"]), 2)

    def test_material_row_dedupe_suppresses_unit_top_sheet_legacy_role_fallback_duplicate(self):
        row_trace = []
        rows = [
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-56-NS-18] Kraft Paper",
                "layer_no": 2,
                "qty_per_fg": 0.3,
                "uom": "kg",
                "quantity_source": "paper_resolution",
                "metadata": {"resolution_product_name": "[KP-180-56-NS-18] Kraft Paper"},
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-56-NS-18] Kraft Paper",
                "layer_no": 3,
                "qty_per_fg": 0.30004,
                "uom": "kg",
                "quantity_source": "legacy_role_fallback",
                "metadata": {"resolution_product_name": "[KP-180-56-NS-18] Kraft Paper"},
            },
            {
                "stage_code": "board",
                "category": "rm",
                "role": "top_sheet_external",
                "product": "[TOP:10-897-751-00363] Top Sheets",
                "layer_no": 1,
                "qty_per_fg": 1.0,
                "uom": "unit",
                "quantity_source": "legacy_role_fallback",
                "metadata": {"top_sheet_product_id": 104},
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "medium",
                "product": "[KP-180-56-NS-18] Kraft Paper",
                "layer_no": 3,
                "qty_per_fg": 0.42,
                "uom": "kg",
                "quantity_source": "paper_resolution",
                "metadata": {"resolution_product_name": "[KP-180-56-NS-18] Kraft Paper"},
            },
        ]

        deduped = _dedupe_material_rows_prefer_resolved(rows, row_trace)

        liners = [row for row in deduped if row["role"] == "liner"]
        self.assertEqual([(row["layer_no"], row["quantity_source"]) for row in liners], [
            (2, "paper_resolution"),
            (3, "legacy_role_fallback"),
        ])
        self.assertFalse(
            any(
                trace.get("action") == "suppressed_duplicate_material_row"
                and trace.get("qty_source") == "legacy_role_fallback"
                and trace.get("layer_no") == 3
                for trace in row_trace
            )
        )

    def test_final_unit_top_sheet_filter_suppresses_legacy_paper_fallback(self):
        row_trace = []
        rows = [
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-56-NS-18] Kraft Paper",
                "layer_no": 2,
                "qty_per_fg": 0.3,
                "uom": "kg",
                "quantity_source": "paper_resolution",
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-56-NS-18] Kraft Paper",
                "layer_no": 3,
                "qty_per_fg": 0.30004,
                "uom": "kg",
                "quantity_source": "legacy_role_fallback",
            },
            {
                "stage_code": "board",
                "category": "rm",
                "role": "top_sheet_external",
                "product": "[TOP:10-897-751-00363] Top Sheets",
                "layer_no": 1,
                "qty_per_fg": 1.0,
                "uom": "unit",
                "quantity_source": "legacy_role_fallback",
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "medium",
                "product": "[KP-180-56-NS-18] Kraft Paper",
                "layer_no": 3,
                "qty_per_fg": 0.42,
                "uom": "kg",
                "quantity_source": "paper_resolution",
            },
        ]

        filtered = _suppress_unit_top_sheet_legacy_paper_fallbacks(rows, row_trace)

        liners = [row for row in filtered if row["role"] == "liner"]
        self.assertEqual([(row["layer_no"], row["quantity_source"]) for row in liners], [
            (2, "paper_resolution"),
            (3, "legacy_role_fallback"),
        ])
        self.assertFalse(
            any(
                trace.get("action") == "suppressed_unit_top_sheet_legacy_paper_fallback"
                and trace.get("qty_source") == "legacy_role_fallback"
                and trace.get("layer_no") == 3
                for trace in row_trace
            )
        )

    def test_final_unit_top_sheet_filter_suppresses_same_layer_legacy_paper_fallback(self):
        row_trace = []
        rows = [
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-56-NS-18] Kraft Paper",
                "layer_no": 2,
                "qty_per_fg": 0.3,
                "uom": "kg",
                "quantity_source": "paper_resolution",
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-56-NS-18] Kraft Paper",
                "layer_no": 2,
                "qty_per_fg": 0.30004,
                "uom": "kg",
                "quantity_source": "legacy_role_fallback",
            },
            {
                "stage_code": "board",
                "category": "rm",
                "role": "top_sheet_external",
                "product": "[TOP:10-897-751-00363] Top Sheets",
                "layer_no": 1,
                "qty_per_fg": 1.0,
                "uom": "unit",
                "quantity_source": "legacy_role_fallback",
            },
        ]

        filtered = _suppress_unit_top_sheet_legacy_paper_fallbacks(rows, row_trace)

        self.assertEqual(len([row for row in filtered if row["role"] == "liner"]), 1)
        self.assertEqual(
            next(row for row in filtered if row["role"] == "liner")["quantity_source"],
            "paper_resolution",
        )
        self.assertTrue(
            any(
                trace.get("action") == "suppressed_unit_top_sheet_legacy_paper_fallback"
                and trace.get("qty_source") == "legacy_role_fallback"
                for trace in row_trace
            )
        )

    def test_material_row_dedupe_keeps_repeated_physical_layers_for_unit_top_sheet(self):
        row_trace = []
        rows = [
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-112-NS-18] Kraft Paper",
                "layer_no": 1,
                "qty_per_fg": 0.61,
                "uom": "kg",
                "quantity_source": "legacy_rm_requirement",
                "metadata": {},
            },
            {
                "stage_code": "board",
                "category": "rm",
                "role": "top_sheet_external",
                "product": "[TOP:10-897-751-00363] Top Sheets",
                "layer_no": 1,
                "qty_per_fg": 1.0,
                "uom": "unit",
                "quantity_source": "legacy_rm_requirement",
                "metadata": {"top_sheet_product_id": 104},
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-112-NS-18] Kraft Paper",
                "layer_no": 2,
                "qty_per_fg": 0.61,
                "uom": "kg",
                "quantity_source": "paper_resolution",
                "metadata": {"resolution_product_name": "[KP-180-112-NS-18] Kraft Paper"},
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "medium",
                "product": "[KP-180-112-NS-18] Kraft Paper",
                "layer_no": 3,
                "qty_per_fg": 0.84,
                "uom": "kg",
                "quantity_source": "paper_resolution",
                "metadata": {"resolution_product_name": "[KP-180-112-NS-18] Kraft Paper"},
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "liner",
                "product": "[KP-180-112-NS-18] Kraft Paper",
                "layer_no": 4,
                "qty_per_fg": 0.61,
                "uom": "kg",
                "quantity_source": "paper_resolution",
                "metadata": {"resolution_product_name": "[KP-180-112-NS-18] Kraft Paper"},
            },
            {
                "stage_code": "2ply",
                "category": "rm",
                "role": "medium",
                "product": "[KP-180-112-NS-18] Kraft Paper",
                "layer_no": 5,
                "qty_per_fg": 0.84,
                "uom": "kg",
                "quantity_source": "paper_resolution",
                "metadata": {"resolution_product_name": "[KP-180-112-NS-18] Kraft Paper"},
            },
        ]

        deduped = _dedupe_material_rows_prefer_resolved(rows, row_trace)

        self.assertEqual(
            [(row["role"], row.get("layer_no")) for row in deduped],
            [
                ("liner", 1),
                ("top_sheet_external", 1),
                ("liner", 2),
                ("medium", 3),
                ("liner", 4),
                ("medium", 5),
            ],
        )
        self.assertEqual(len([row for row in deduped if row["role"] in {"liner", "medium"}]), 5)

    def test_stagecontext_preview_model_surfaces_stage_totals_and_consumables(self):
        shadow_runtime = {
            "route_source": {"selection_source": "plant"},
            "routing": {"selection_source": "plant", "warnings": [], "stages": [{}, {}, {}]},
            "material_requirements": [
                {
                    "stage_code": "2ply",
                    "role": "liner",
                    "product_tmpl_id": 10,
                    "material_type": "rm",
                    "attach_quantity_basis": "per_m2",
                },
                {
                    "stage_code": "seaming",
                    "role": "adhesive",
                    "product_tmpl_id": 20,
                    "material_type": "consumable",
                    "attach_quantity_basis": "per_box",
                },
            ],
            "quantity_report": {
                "order_qty": 1234.0,
                "warnings": ["quantity_planner:boxes_per_sheet_missing_or_zero"],
            },
            "parity_report": {
                "warnings": ["1 material keys present only in Phase 8 shadow material_requirements."],
            },
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 206.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 1234.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [{"role_code": "liner", "product_tmpl_id": 10, "material_type": "rm"}],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_code": "seaming",
                    "stage_name": "Seaming",
                    "stage_type": "conversion",
                    "execution_type": "material",
                    "target_output_qty": 1234.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 206.0,
                    "fg_demand_qty": 1234.0,
                    "output_stock_behavior": "wip",
                    "rm_attachments": [],
                    "consumable_attachments": [{"role_code": "adhesive", "product_tmpl_id": 20, "material_type": "consumable"}],
                    "intermediate_attachments": [],
                    "warnings": ["required_input_role_missing:adhesive"],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "joint", "uom": "unit"},
                },
                {
                    "index": 2,
                    "sequence": 30,
                    "stage_code": "fg",
                    "stage_name": "Finished Goods",
                    "stage_type": "fg",
                    "execution_type": "material",
                    "target_output_qty": 1234.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 1234.0,
                    "fg_demand_qty": 1234.0,
                    "output_stock_behavior": "fg",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "box", "uom": "unit"},
                },
            ],
        }
        rm_requirements = [
            {
                "product_key": "rm_liner",
                "role": "liner",
                "stage_key": "2ply",
                "qty_per_fg": 0.25,
                "qty_uom": "kg",
                "consumption_type": "area",
                "metadata": {},
            }
        ]
        qgraph = type(
            "Graph",
            (),
            {
                "nodes": {
                    "raw_material": type("Node", (), {"qty": 0.5661})(),
                    "board": type("Node", (), {"qty": 206.0})(),
                    "sheet": type("Node", (), {"qty": 206.0})(),
                    "blanks": type("Node", (), {"qty": 1234.0})(),
                    "finished_goods": type("Node", (), {"qty": 1234.0})(),
                },
                "edges": [
                    type("Edge", (), {"from_node": "raw_material", "to_node": "board"})(),
                    type("Edge", (), {"from_node": "sheet", "to_node": "blanks"})(),
                    type("Edge", (), {"from_node": "blanks", "to_node": "finished_goods"})(),
                ],
            },
        )()

        preview = build_preview_model(
            shadow_runtime,
            spec_name="Spec A",
            style="0201",
            plant_name="Plant A",
            order_qty=1234.0,
            metrics=type("Metrics", (), {"m2_per_box": 0.5, "kg_per_box": 0.2, "extra": {"boxes_per_sheet": 6.0, "layout_blanks_per_sheet": 12, "orientation": "std"}})(),
            costing=None,
            rm_requirements=rm_requirements,
            product_templates={10: "Kraft Liner", 20: "FG Adhesive"},
            qgraph=qgraph,
        )

        self.assertEqual(preview["source"], "stage_context_runtime")
        self.assertEqual(preview["summary"]["sheet_stage_total_qty"], 206.0)
        self.assertEqual(preview["summary"]["fg_stage_total_qty"], 1234.0)
        self.assertEqual(preview["summary"]["sheet_stage_codes"], ["2ply"])
        self.assertEqual(preview["summary"]["fg_stage_codes"], ["fg"])

        materials = preview["materials"]
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0]["quantity_source"], "legacy_rm_requirement")
        self.assertEqual(materials[0]["product"], "Kraft Liner")

        self.assertIn("quantity_planner:boxes_per_sheet_missing_or_zero", preview["warnings"])
        self.assertIn("seaming:required_input_role_missing:adhesive", preview["warnings"])
        self.assertIn("route_source:plant", preview["notes"])
        ops = {item["stage"]: item for item in preview["operations"]}
        self.assertEqual(ops["corrugator"]["input_qty"], 0.5661)
        self.assertEqual(ops["corrugator"]["output_qty"], 206.0)
        self.assertIn("conversion", ops)

    def test_role_alias_match_is_recorded(self):
        shadow_runtime = {
            "route_source": {"selection_source": "plant"},
            "routing": {"selection_source": "plant", "warnings": [], "stages": [{}]},
            "material_requirements": [
                {
                    "stage_code": "ts_print",
                    "role": "ink",
                    "product_tmpl_id": 30,
                    "material_type": "consumable",
                    "attach_quantity_basis": "per_m2",
                }
            ],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "ts_print",
                    "stage_name": "Top Sheet Printing",
                    "stage_type": "printing",
                    "execution_type": "operation",
                    "target_output_qty": 50.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 50.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "sheet",
                    "rm_attachments": [],
                    "consumable_attachments": [{"role_code": "ink", "product_tmpl_id": 30, "material_type": "consumable"}],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                }
            ],
        }
        rm_requirements = [
            {
                "product_key": "rm_ink",
                "role": "ink_total",
                "stage_key": "ts_print",
                "qty_per_fg": 0.03,
                "qty_uom": "kg",
                "consumption_type": "area",
                "metadata": {},
            }
        ]

        preview = build_preview_model(
            shadow_runtime,
            spec_name="Spec B",
            style="0201",
            plant_name="Plant B",
            order_qty=100.0,
            rm_requirements=rm_requirements,
            product_templates={30: "Printing Ink"},
        )

        self.assertEqual(len(preview["materials"]), 1)
        self.assertEqual(preview["materials"][0]["quantity_source"], "legacy_rm_requirement")
        self.assertNotIn("quantity matched by stage fallback", preview["materials"][0]["notes"])

    def test_consumable_stage_fallback_uses_profile_product_uom_not_area_basis(self):
        shadow_runtime = {
            "consumable_state": {
                "enable_consumables": True,
                "enable_lamination_adhesive": True,
            },
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [
                {
                    "stage_code": "board_formation",
                    "stage_template_id": 40,
                    "role": "adhesive_board",
                    "product_tmpl_id": 40,
                    "material_type": "consumable",
                    "attach_quantity_basis": "per_m2",
                    "legacy_role_aliases": ["adhesive"],
                }
            ],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "board_formation",
                    "stage_template_id": 40,
                    "stage_name": "Board Formation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 1.0,
                    "target_output_uom": "sheet",
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [],
                    "consumable_attachments": [
                        {
                            "role_code": "adhesive",
                            "stage_template_id": 40,
                            "product_tmpl_id": 40,
                            "material_type": "consumable",
                        }
                    ],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                }
            ],
        }
        rm_requirements = [
            {
                "product_key": "rm_adhesive_board",
                "role": "adhesive_board",
                "stage_key": "board_formation",
                "qty_per_fg": 0.271,
                "qty_uom": "kg",
                "consumption_type": "area",
                "metadata": {"kind": "adhesive_board"},
            }
        ]

        preview = build_preview_model(
            shadow_runtime,
            order_qty=1.0,
            rm_requirements=rm_requirements,
            consumable_profile_products={
                "adhesive_board": {
                    "product_tmpl_id": 40,
                    "product_label": "[ADH-001] Adhesive",
                    "uom_id": 7,
                    "uom_name": "kg",
                }
            },
        )

        self.assertEqual(len(preview["materials"]), 1)
        row = preview["materials"][0]
        self.assertEqual(row["quantity_source"], "legacy_stage_fallback")
        self.assertEqual(row["quantity_basis"], "per_m2")
        self.assertEqual(row["uom"], "kg")
        self.assertEqual(row["product"], "[ADH-001] Adhesive")
        self.assertEqual(row["metadata"]["uom_name"], "kg")

    def test_unmatched_legacy_print_consumable_uses_profile_product_label(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "ts_print",
                    "stage_name": "Top Sheet Printing",
                    "stage_type": "printing",
                    "execution_type": "operation",
                    "target_output_qty": 50.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 50.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "sheet",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                }
            ],
        }
        rm_requirements = [
            {
                "product_key": "rm_print",
                "role": "printing",
                "stage_key": "ts_print",
                "qty_per_fg": 0.03,
                "qty_uom": "kg",
                "consumption_type": "area",
                "metadata": {"kind": "printing_ink"},
            }
        ]

        preview = build_preview_model(
            shadow_runtime,
            spec_name="Spec B",
            style="0201",
            plant_name="Plant B",
            order_qty=100.0,
            rm_requirements=rm_requirements,
            consumable_profile_products={
                "ink": {
                    "product_tmpl_id": 30,
                    "product_label": "[INK-001] Ink",
                }
            },
        )

        self.assertEqual(len(preview["materials"]), 1)
        self.assertEqual(preview["materials"][0]["product"], "[INK-001] Ink")
        self.assertEqual(preview["materials"][0]["product_tmpl_id"], 30)
        self.assertEqual(preview["materials"][0]["metadata"]["resolution_source"], "engine_material_profile_role")

    def test_unmatched_legacy_rm_stage_falls_forward_to_active_board_stage(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "board",
                    "stage_name": "Board",
                    "stage_type": "board",
                    "execution_type": "material",
                    "target_output_qty": 1.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 1.0,
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_code": "diecut",
                    "stage_name": "Slot/Die",
                    "stage_type": "diecut",
                    "execution_type": "material",
                    "target_output_qty": 1.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 1.0,
                    "fg_demand_qty": 1.0,
                    "output_stock_behavior": "fg",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "box", "uom": "unit"},
                },
            ],
        }
        rm_requirements = [
            {
                "product_key": "",
                "role": "liner",
                "stage_key": "2ply",
                "qty_per_fg": 0.07,
                "qty_uom": "kg",
                "metadata": {"resolution_status": "missing"},
            },
            {
                "product_key": "",
                "role": "medium",
                "stage_key": "2ply",
                "qty_per_fg": 0.07,
                "qty_uom": "kg",
                "metadata": {"resolution_status": "missing"},
            },
        ]

        preview = build_preview_model(shadow_runtime, order_qty=1.0, rm_requirements=rm_requirements)

        self.assertEqual([row["stage_code"] for row in preview["materials"]], ["board", "board"])
        self.assertEqual(preview["materials"][0]["metadata"]["original_semantic_stage"], "2ply")
        self.assertEqual(preview["materials"][0]["metadata"]["assigned_route_stage"], "board")
        self.assertEqual(preview["materials"][0]["metadata"]["fallback_reason"], "missing_stage")

    def test_medium_shadow_pending_uses_exact_paper_resolution(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {
                            "role_code": "medium",
                            "product_tmpl_id": 88,
                            "material_type": "rm",
                            "metadata": {"layer_no": 2},
                        }
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                }
            ],
        }

        preview = build_preview_model(
            shadow_runtime,
            order_qty=100.0,
            rm_requirements=[],
            product_templates={88: "Phase5 Medium"},
            paper_resolution=[
                {
                    "role": "medium",
                    "layer_no": 2,
                    "status": "exact",
                    "product_name": "Kraft Paper 150 GSM 112 Deckle",
                    "qty_per_fg": 0.286,
                    "qty_uom": "kg",
                    "required_deckle_cm": 112.0,
                }
            ],
        )

        self.assertEqual(len(preview["materials"]), 1)
        row = preview["materials"][0]
        self.assertEqual(row["role"], "medium")
        self.assertEqual(row["product"], "Kraft Paper 150 GSM 112 Deckle")
        self.assertEqual(row["quantity_source"], "paper_resolution")
        self.assertAlmostEqual(row["qty_per_fg"], 0.286)
        self.assertAlmostEqual(row["total_qty"], 28.6)
        self.assertNotEqual(row["product"], "Phase5 Medium")
        self.assertNotIn("quantity pending in shadow preview adapter", row["notes"])

    def test_medium_shadow_pending_missing_resolution_stays_unresolved(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {
                            "role_code": "medium",
                            "product_tmpl_id": 88,
                            "material_type": "rm",
                            "metadata": {"layer_no": 2},
                        }
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                }
            ],
        }

        preview = build_preview_model(
            shadow_runtime,
            order_qty=100.0,
            rm_requirements=[],
            product_templates={88: "Phase5 Medium"},
            paper_resolution=[
                {
                    "role": "medium",
                    "layer_no": 2,
                    "status": "missing",
                    "product_name": "Missing / Unresolved",
                    "qty_per_fg": 0.286,
                    "qty_uom": "kg",
                    "required_deckle_cm": 224.0,
                    "resolution_note": "Exact paper variant not found for required deckle 224.0 cm",
                }
            ],
        )

        self.assertEqual(len(preview["materials"]), 1)
        row = preview["materials"][0]
        self.assertEqual(row["role"], "medium")
        self.assertEqual(row["product"], "Missing / Unresolved")
        self.assertEqual(row["quantity_source"], "paper_resolution")
        self.assertNotEqual(row["product"], "Phase5 Medium")
        self.assertIn("Exact paper variant not found", " ".join(row["notes"]))

    def test_unmatched_top_sheet_template_is_not_rendered_without_requirement(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "lamination",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {
                            "role_code": "top_sheet_external",
                            "product_tmpl_id": 99,
                            "material_type": "rm",
                            "metadata": {},
                        }
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                }
            ],
        }

        preview = build_preview_model(
            shadow_runtime,
            order_qty=100.0,
            rm_requirements=[],
            product_templates={99: "Phase5 Top Sheet"},
            paper_resolution=[],
        )

        self.assertEqual(preview["materials"], [])

    def test_unit_top_sheet_replaces_liner_without_zero_placeholder(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "lamination",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {
                            "role_code": "medium",
                            "product_tmpl_id": 11,
                            "material_type": "rm",
                            "metadata": {"layer_no": 2},
                        },
                        {
                            "role_code": "liner",
                            "product_tmpl_id": 10,
                            "material_type": "rm",
                            "metadata": {"layer_no": 3},
                        },
                        {
                            "role_code": "top_sheet_external",
                            "product_tmpl_id": 12,
                            "material_type": "rm",
                            "metadata": {"layer_no": 1},
                        },
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                }
            ],
        }
        rm_requirements = [
            {
                "product_key": "unit_top_sheet",
                "role": "top_sheet_external",
                "stage_key": "board",
                "layer_no": 1,
                "qty_per_fg": 1.0,
                "qty_uom": "unit",
                "consumption_type": "unit",
                "metadata": {"layer_no": 1, "resolution_product_name": "Unit Top Sheet"},
            },
            {
                "product_key": "medium",
                "role": "medium",
                "stage_key": "board",
                "layer_no": 2,
                "qty_per_fg": 0.2,
                "qty_uom": "kg",
                "metadata": {"layer_no": 2, "resolution_product_name": "Medium"},
            },
            {
                "product_key": "liner",
                "role": "liner",
                "stage_key": "board",
                "layer_no": 3,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 3, "resolution_product_name": "Inner Liner"},
            },
        ]

        preview = build_preview_model(
            shadow_runtime,
            order_qty=100.0,
            rm_requirements=rm_requirements,
            paper_resolution=[],
        )

        materials = preview["materials"]
        self.assertEqual([(row["role"], row.get("layer_no")) for row in materials], [
            ("top_sheet_external", 1),
            ("medium", 2),
            ("liner", 3),
        ])
        self.assertNotIn("Missing / Unresolved", [row["product"] for row in materials])
        self.assertFalse(any(row["quantity_source"] == "shadow_pending" for row in materials))
        top_row = next(row for row in materials if row["role"] == "top_sheet_external")
        self.assertEqual(top_row["metadata"].get("consumption_type"), "unit")

    def test_roll_top_sheet_renders_top_roll_not_top_sheet_external(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "lamination",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {
                            "role_code": "liner",
                            "product_tmpl_id": 10,
                            "material_type": "rm",
                            "metadata": {"layer_no": 1},
                        },
                        {
                            "role_code": "medium",
                            "product_tmpl_id": 11,
                            "material_type": "rm",
                            "metadata": {"layer_no": 2},
                        },
                        {
                            "role_code": "liner",
                            "product_tmpl_id": 10,
                            "material_type": "rm",
                            "metadata": {"layer_no": 3},
                        },
                        {
                            "role_code": "top_sheet_external",
                            "product_tmpl_id": 12,
                            "material_type": "rm",
                            "metadata": {"layer_no": 1},
                            "legacy_role_aliases": ["top_sheet_external", "top_roll"],
                        },
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                }
            ],
        }
        rm_requirements = [
            {
                "product_key": "top_roll",
                "role": "top_roll",
                "stage_key": "board",
                "layer_no": 1,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 1, "resolution_product_name": "Top Roll"},
            },
            {
                "product_key": "medium",
                "role": "medium",
                "stage_key": "board",
                "layer_no": 2,
                "qty_per_fg": 0.2,
                "qty_uom": "kg",
                "metadata": {"layer_no": 2, "resolution_product_name": "Medium"},
            },
            {
                "product_key": "liner",
                "role": "liner",
                "stage_key": "board",
                "layer_no": 3,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 3, "resolution_product_name": "Inner Liner"},
            },
        ]

        preview = build_preview_model(
            shadow_runtime,
            order_qty=100.0,
            rm_requirements=rm_requirements,
            paper_resolution=[],
        )

        materials = preview["materials"]
        self.assertEqual([(row["role"], row.get("layer_no")) for row in materials], [
            ("top_roll", 1),
            ("medium", 2),
            ("liner", 3),
        ])
        self.assertNotIn("top_sheet_external", [row["role"] for row in materials])
        self.assertEqual(materials[0]["product"], "Top Roll")

    def test_live_style_roll_top_sheet_suppresses_legacy_replaced_liner(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "lamination",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {
                            "role_code": "liner",
                            "product_tmpl_id": 10,
                            "material_type": "rm",
                        },
                        {
                            "role_code": "medium",
                            "product_tmpl_id": 11,
                            "material_type": "rm",
                        },
                        {
                            "role_code": "liner",
                            "product_tmpl_id": 10,
                            "material_type": "rm",
                        },
                        {
                            "role_code": "top_sheet_external",
                            "product_tmpl_id": 12,
                            "material_type": "rm",
                            "legacy_role_aliases": ["top_sheet_external", "top_roll"],
                        },
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                }
            ],
        }
        rm_requirements = [
            # Live raw_json can still contain a legacy liner for the top layer.
            # It must not survive preview when top_roll replaces layer 1.
            {
                "product_key": "legacy_top_liner",
                "role": "liner",
                "stage_key": "board",
                "layer_no": 1,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 1, "resolution_product_name": "Legacy Top Liner"},
            },
            {
                "product_key": "medium",
                "role": "medium",
                "stage_key": "board",
                "layer_no": 2,
                "qty_per_fg": 0.2,
                "qty_uom": "kg",
                "metadata": {"layer_no": 2, "resolution_product_name": "Medium"},
            },
            {
                "product_key": "liner",
                "role": "liner",
                "stage_key": "board",
                "layer_no": 3,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 3, "resolution_product_name": "Inner Liner"},
            },
            {
                "product_key": "top_roll",
                "role": "top_roll",
                "stage_key": "board",
                "layer_no": 1,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 1, "resolution_product_name": "Top Roll"},
            },
        ]

        preview = build_preview_model(
            shadow_runtime,
            order_qty=100.0,
            rm_requirements=rm_requirements,
            paper_resolution=[],
        )

        materials = preview["materials"]
        self.assertEqual([(row["role"], row.get("layer_no")) for row in materials], [
            ("top_roll", 1),
            ("medium", 2),
            ("liner", 3),
        ])
        self.assertNotIn("Legacy Top Liner", [row["product"] for row in materials])
        self.assertEqual(len(materials), 3)

    def test_live_trace_order_does_not_match_liner_attachment_to_medium_fallback(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {"role_code": "liner", "product_tmpl_id": 10, "material_type": "rm", "source": "material_profile_rule"},
                        {"role_code": "liner", "product_tmpl_id": 10, "material_type": "rm", "source": "material_profile_rule"},
                        {"role_code": "medium", "product_tmpl_id": 11, "material_type": "rm", "source": "material_profile_rule"},
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "lamination",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 10.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {
                            "role_code": "top_sheet_external",
                            "product_tmpl_id": 12,
                            "material_type": "rm",
                            "source": "material_profile_rule",
                            "legacy_role_aliases": ["top_sheet_external", "top_roll"],
                        }
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
            ],
        }
        rm_requirements = [
            {
                "product_key": "rm_liner",
                "role": "liner",
                "stage_key": "2ply",
                "layer_no": 3,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 3, "resolution_status": "missing"},
            },
            {
                "product_key": "rm_medium",
                "role": "medium",
                "stage_key": "2ply",
                "layer_no": 2,
                "qty_per_fg": 0.2,
                "qty_uom": "kg",
                "metadata": {"layer_no": 2, "resolution_status": "missing"},
            },
            {
                "product_key": "rm_top_roll",
                "role": "top_roll",
                "stage_key": "board",
                "layer_no": 1,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 1, "resolution_status": "missing"},
            },
        ]

        preview = build_preview_model(
            shadow_runtime,
            order_qty=100.0,
            rm_requirements=rm_requirements,
            paper_resolution=[],
        )

        materials = preview["materials"]
        self.assertEqual([(row["role"], row.get("layer_no")) for row in materials], [
            ("top_roll", 1),
            ("medium", 2),
            ("liner", 3),
        ])
        self.assertFalse(
            any(
                trace.get("attachment_role") == "liner"
                and trace.get("matched_role") == "medium"
                for trace in preview["material_row_trace"]
            )
        )

    def test_live_trace_does_not_duplicate_liner_from_legacy_and_paper_resolution(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {"role_code": "liner", "product_tmpl_id": 10, "material_type": "rm", "source": "material_profile_rule"},
                        {"role_code": "liner", "product_tmpl_id": 10, "material_type": "rm", "source": "material_profile_rule"},
                        {"role_code": "medium", "product_tmpl_id": 11, "material_type": "rm", "source": "material_profile_rule"},
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "lamination",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 10.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {
                            "role_code": "top_sheet_external",
                            "product_tmpl_id": 12,
                            "material_type": "rm",
                            "source": "material_profile_rule",
                            "legacy_role_aliases": ["top_sheet_external", "top_roll"],
                        }
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
            ],
        }
        rm_requirements = [
            {
                "product_key": "rm_liner",
                "role": "liner",
                "stage_key": "2ply",
                "layer_no": 3,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 3, "resolution_product_name": "Liner 110"},
            },
            {
                "product_key": "rm_medium",
                "role": "medium",
                "stage_key": "2ply",
                "layer_no": 2,
                "qty_per_fg": 0.2,
                "qty_uom": "kg",
                "metadata": {"layer_no": 2, "resolution_product_name": "Medium 110"},
            },
            {
                "product_key": "rm_top_roll",
                "role": "top_roll",
                "stage_key": "board",
                "layer_no": 1,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 1, "resolution_product_name": "Top Roll 110"},
            },
        ]

        preview = build_preview_model(
            shadow_runtime,
            order_qty=100.0,
            rm_requirements=rm_requirements,
            paper_resolution=[
                {
                    "role": "liner",
                    "layer_no": 3,
                    "status": "exact",
                    "product_name": "Liner 110",
                    "qty_per_fg": 0.3,
                    "qty_uom": "kg",
                },
                {
                    "role": "medium",
                    "layer_no": 2,
                    "status": "exact",
                    "product_name": "Medium 110",
                    "qty_per_fg": 0.2,
                    "qty_uom": "kg",
                },
                {
                    "role": "top_roll",
                    "layer_no": 1,
                    "status": "exact",
                    "product_name": "Top Roll 110",
                    "qty_per_fg": 0.3,
                    "qty_uom": "kg",
                },
            ],
        )

        materials = preview["materials"]
        self.assertEqual([(row["role"], row.get("layer_no")) for row in materials], [
            ("top_roll", 1),
            ("medium", 2),
            ("liner", 3),
        ])
        self.assertEqual(
            len([row for row in materials if row["role"] == "liner" and row.get("layer_no") == 3]),
            1,
        )
        self.assertFalse(
            any(
                trace.get("role") == "liner"
                and trace.get("layer_no") == 3
                and trace.get("path") == "paper_resolution"
                for trace in preview["material_row_trace"]
            )
        )

    def test_liner_paper_resolution_for_replaced_top_layer_is_suppressed(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {"role_code": "liner", "product_tmpl_id": 10, "material_type": "rm", "source": "material_profile_rule"},
                        {"role_code": "liner", "product_tmpl_id": 10, "material_type": "rm", "source": "material_profile_rule"},
                        {"role_code": "medium", "product_tmpl_id": 11, "material_type": "rm", "source": "material_profile_rule"},
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "lamination",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 10.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {
                            "role_code": "top_sheet_external",
                            "product_tmpl_id": 12,
                            "material_type": "rm",
                            "source": "material_profile_rule",
                            "legacy_role_aliases": ["top_sheet_external", "top_roll"],
                        }
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
            ],
        }
        rm_requirements = [
            {
                "product_key": "rm_liner",
                "role": "liner",
                "stage_key": "2ply",
                "layer_no": 3,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 3, "resolution_product_name": "Liner 3"},
            },
            {
                "product_key": "rm_medium",
                "role": "medium",
                "stage_key": "2ply",
                "layer_no": 2,
                "qty_per_fg": 0.2,
                "qty_uom": "kg",
                "metadata": {"layer_no": 2, "resolution_product_name": "Medium 2"},
            },
            {
                "product_key": "rm_top_roll",
                "role": "top_roll",
                "stage_key": "board",
                "layer_no": 1,
                "qty_per_fg": 0.3,
                "qty_uom": "kg",
                "metadata": {"layer_no": 1, "resolution_product_name": "Top Roll 1"},
            },
        ]

        preview = build_preview_model(
            shadow_runtime,
            order_qty=100.0,
            rm_requirements=rm_requirements,
            paper_resolution=[
                {
                    "role": "liner",
                    "layer_no": 1,
                    "status": "exact",
                    "product_name": "Replaced Liner 1",
                    "qty_per_fg": 0.3,
                    "qty_uom": "kg",
                },
                {
                    "role": "liner",
                    "layer_no": 3,
                    "status": "exact",
                    "product_name": "Liner 3",
                    "qty_per_fg": 0.3,
                    "qty_uom": "kg",
                },
                {
                    "role": "medium",
                    "layer_no": 2,
                    "status": "exact",
                    "product_name": "Medium 2",
                    "qty_per_fg": 0.2,
                    "qty_uom": "kg",
                },
                {
                    "role": "top_roll",
                    "layer_no": 1,
                    "status": "exact",
                    "product_name": "Top Roll 1",
                    "qty_per_fg": 0.3,
                    "qty_uom": "kg",
                },
            ],
        )

        materials = preview["materials"]
        self.assertEqual([(row["role"], row.get("layer_no")) for row in materials], [
            ("top_roll", 1),
            ("medium", 2),
            ("liner", 3),
        ])
        self.assertNotIn("Replaced Liner 1", [row["product"] for row in materials])
        self.assertTrue(
            any(
                trace.get("action") == "suppressed_replaced_liner_from_paper_resolution"
                and trace.get("layer_no") == 1
                for trace in preview["material_row_trace"]
            )
        )

    def test_unit_top_sheet_single_liner_attachment_keeps_base_liner_from_paper_resolution(self):
        shadow_runtime = {
            "route_source": {"selection_source": "routing_profile"},
            "routing": {"selection_source": "routing_profile", "warnings": [], "stages": [{}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {"role_code": "liner", "product_tmpl_id": 10, "material_type": "rm", "source": "material_profile_rule"},
                        {"role_code": "medium", "product_tmpl_id": 11, "material_type": "rm", "source": "material_profile_rule"},
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "lamination",
                    "execution_type": "material",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 10.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [
                        {
                            "role_code": "top_sheet_external",
                            "product_tmpl_id": 12,
                            "material_type": "rm",
                            "source": "material_profile_rule",
                            "legacy_role_aliases": ["top_sheet_external", "top_roll"],
                        }
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
            ],
        }
        rm_requirements = [
            {
                "product_key": "rm_medium",
                "role": "medium",
                "stage_key": "2ply",
                "layer_no": 2,
                "qty_per_fg": 0.2,
                "qty_uom": "kg",
                "metadata": {"layer_no": 2, "resolution_product_name": "Medium 2"},
            },
            {
                "product_key": "unit_top",
                "role": "top_sheet_external",
                "stage_key": "board",
                "layer_no": 1,
                "qty_per_fg": 1.0,
                "qty_uom": "unit",
                "metadata": {"layer_no": 1, "resolution_product_name": "Unit Top 1"},
            },
        ]

        preview = build_preview_model(
            shadow_runtime,
            order_qty=100.0,
            rm_requirements=rm_requirements,
            paper_resolution=[
                {
                    "role": "liner",
                    "layer_no": 3,
                    "status": "exact",
                    "product_name": "Base Liner 3",
                    "qty_per_fg": 0.3,
                    "qty_uom": "kg",
                },
                {
                    "role": "medium",
                    "layer_no": 2,
                    "status": "exact",
                    "product_name": "Medium 2",
                    "qty_per_fg": 0.2,
                    "qty_uom": "kg",
                },
            ],
        )

        materials = preview["materials"]
        self.assertEqual([(row["role"], row.get("layer_no")) for row in materials], [
            ("top_sheet_external", 1),
            ("medium", 2),
            ("liner", 3),
        ])
        self.assertIn("Base Liner 3", [row["product"] for row in materials])

    def test_qgraph_overlay_drives_legacy_equivalent_operation_quantities(self):
        shadow_runtime = {
            "route_source": {"selection_source": "global_default"},
            "routing": {"selection_source": "global_default", "warnings": [], "stages": [{}, {}, {}]},
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 25.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "board",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_code": "ts_print",
                    "stage_name": "Top Sheet Printing",
                    "stage_type": "printing",
                    "execution_type": "material",
                    "target_output_qty": 25.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "sheet",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                },
                {
                    "index": 2,
                    "sequence": 30,
                    "stage_code": "diecut",
                    "stage_name": "Die-cut",
                    "stage_type": "diecut",
                    "execution_type": "material",
                    "target_output_qty": 100.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "non_stock",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "blank", "uom": "unit"},
                },
                {
                    "index": 3,
                    "sequence": 40,
                    "stage_code": "fg",
                    "stage_name": "Finished Goods",
                    "stage_type": "fg",
                    "execution_type": "material",
                    "target_output_qty": 100.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 100.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "fg",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "box", "uom": "unit"},
                },
            ],
        }
        qgraph = type(
            "Graph",
            (),
            {
                "nodes": {
                    "raw_material": type("Node", (), {"qty": 35.0})(),
                    "board": type("Node", (), {"qty": 25.0})(),
                    "sheet": type("Node", (), {"qty": 25.0})(),
                    "blanks": type("Node", (), {"qty": 100.0})(),
                    "finished_goods": type("Node", (), {"qty": 100.0})(),
                },
                "edges": [
                    type("Edge", (), {"from_node": "raw_material", "to_node": "board"})(),
                    type("Edge", (), {"from_node": "sheet", "to_node": "blanks"})(),
                    type("Edge", (), {"from_node": "blanks", "to_node": "finished_goods"})(),
                ],
            },
        )()

        preview = build_preview_model(
            shadow_runtime,
            spec_name="Spec C",
            style="0201",
            plant_name="Plant C",
            order_qty=100.0,
            qgraph=qgraph,
        )

        ops = {item["stage"]: item for item in preview["operations"]}
        self.assertEqual(ops["corrugator"]["input_qty"], 35.0)
        self.assertEqual(ops["corrugator"]["output_qty"], 25.0)
        self.assertEqual(ops["printing"]["input_qty"], 25.0)
        self.assertEqual(ops["printing"]["output_qty"], 100.0)
        self.assertEqual(ops["diecut"]["input_qty"], 100.0)
        self.assertEqual(ops["diecut"]["output_qty"], 100.0)
        self.assertIn("route_source:global_default", preview["notes"])

    def test_stage_type_and_capability_drive_mapping_without_legacy_stage_codes(self):
        shadow_runtime = {
            "route_source": {"selection_source": "spec"},
            "routing": {"selection_source": "spec", "warnings": [], "stages": [{}, {}, {}, {}]},
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "entry_corr_a",
                    "stage_name": "Entry Corrugation A",
                    "stage_type": "corrugation",
                    "capability_code": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 25.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_code": "surface_pass_a",
                    "stage_name": "Surface Pass A",
                    "stage_type": "printing",
                    "capability_code": "printing",
                    "execution_type": "operation",
                    "target_output_qty": 25.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 25.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "non_stock",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                },
                {
                    "index": 2,
                    "sequence": 30,
                    "stage_code": "blank_form_a",
                    "stage_name": "Blank Form A",
                    "stage_type": "diecut",
                    "capability_code": "diecut",
                    "execution_type": "operation",
                    "target_output_qty": 100.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 25.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "non_stock",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "blank", "uom": "unit"},
                },
                {
                    "index": 3,
                    "sequence": 40,
                    "stage_code": "close_joint_a",
                    "stage_name": "Close Joint A",
                    "stage_type": "conversion",
                    "capability_code": "conversion",
                    "execution_type": "material",
                    "target_output_qty": 100.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 100.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "box", "uom": "unit"},
                },
                {
                    "index": 4,
                    "sequence": 50,
                    "stage_code": "dispatch_out_a",
                    "stage_name": "Dispatch Out A",
                    "stage_type": "fg",
                    "capability_code": "fg",
                    "execution_type": "material",
                    "target_output_qty": 100.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 100.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "fg",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "fg", "uom": "unit"},
                },
            ],
        }
        qgraph = type(
            "Graph",
            (),
            {
                "nodes": {
                    "raw_material": type("Node", (), {"qty": 40.0})(),
                    "board": type("Node", (), {"qty": 25.0})(),
                    "sheet": type("Node", (), {"qty": 25.0})(),
                    "blanks": type("Node", (), {"qty": 100.0})(),
                    "finished_goods": type("Node", (), {"qty": 100.0})(),
                },
                "edges": [
                    type("Edge", (), {"from_node": "raw_material", "to_node": "board"})(),
                    type("Edge", (), {"from_node": "sheet", "to_node": "blanks"})(),
                    type("Edge", (), {"from_node": "blanks", "to_node": "finished_goods"})(),
                ],
            },
        )()

        preview = build_preview_model(
            shadow_runtime,
            spec_name="Spec D",
            style="0427",
            plant_name="Plant D",
            order_qty=100.0,
            qgraph=qgraph,
        )

        ops = {item["stage"]: item for item in preview["operations"]}
        self.assertEqual(sorted(ops.keys()), ["conversion", "corrugator", "diecut", "fg", "printing"])
        self.assertEqual(ops["corrugator"]["stage_codes"], ["entry_corr_a"])
        self.assertEqual(ops["printing"]["stage_codes"], ["surface_pass_a"])
        self.assertEqual(ops["diecut"]["stage_codes"], ["blank_form_a"])
        self.assertEqual(ops["conversion"]["stage_codes"], ["close_joint_a"])
        self.assertEqual(ops["fg"]["stage_codes"], ["dispatch_out_a"])
        buckets = {item["bucket"]: item for item in preview["flow_buckets"]}
        self.assertEqual(buckets["board"]["stages"], ["entry_corr_a"])
        self.assertEqual(buckets["sheet"]["stages"], ["surface_pass_a"])
        self.assertEqual(buckets["blanks"]["stages"], ["blank_form_a"])
        self.assertEqual(buckets["finished_goods"]["stages"], ["close_joint_a", "dispatch_out_a"])

    def test_qgraph_route_stage_overlay_updates_stage_level_quantities(self):
        shadow_runtime = {
            "route_source": {"selection_source": "spec"},
            "routing": {"selection_source": "spec", "warnings": [], "stages": [{}, {}, {}, {}]},
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_code": "entry_corr_a",
                    "stage_name": "Entry Corrugation A",
                    "stage_type": "corrugation",
                    "capability_code": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 25.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_code": "surface_pass_a",
                    "stage_name": "Surface Pass A",
                    "stage_type": "printing",
                    "capability_code": "printing",
                    "execution_type": "operation",
                    "target_output_qty": 25.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "non_stock",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                },
                {
                    "index": 2,
                    "sequence": 30,
                    "stage_code": "close_joint_a",
                    "stage_name": "Close Joint A",
                    "stage_type": "conversion",
                    "capability_code": "conversion",
                    "execution_type": "material",
                    "target_output_qty": 100.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 100.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "box", "uom": "unit"},
                },
                {
                    "index": 3,
                    "sequence": 40,
                    "stage_code": "dispatch_out_a",
                    "stage_name": "Dispatch Out A",
                    "stage_type": "fg",
                    "capability_code": "fg",
                    "execution_type": "material",
                    "target_output_qty": 100.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 100.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "fg",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "fg", "uom": "unit"},
                },
            ],
        }
        qgraph = type(
            "Graph",
            (),
            {
                "nodes": {
                    "raw_material": type("Node", (), {"qty": 40.0, "meta": {}})(),
                    "board": type(
                        "Node",
                        (),
                        {
                            "qty": 25.0,
                            "meta": {
                                "route_stage_quantities": [
                                    {
                                        "stage_code": "entry_corr_a",
                                        "bucket_node": "board",
                                        "operation_key": "corrugator",
                                        "input_qty": 40.0,
                                        "output_qty": 25.0,
                                        "output_uom": "sheet",
                                    }
                                ]
                            },
                        },
                    )(),
                    "sheet": type(
                        "Node",
                        (),
                        {
                            "qty": 25.0,
                            "meta": {
                                "route_stage_quantities": [
                                    {
                                        "stage_code": "surface_pass_a",
                                        "bucket_node": "sheet",
                                        "operation_key": "printing",
                                        "input_qty": 25.0,
                                        "output_qty": 25.0,
                                        "output_uom": "sheet",
                                    }
                                ]
                            },
                        },
                    )(),
                    "blanks": type("Node", (), {"qty": 100.0, "meta": {}})(),
                    "finished_goods": type(
                        "Node",
                        (),
                        {
                            "qty": 100.0,
                            "meta": {
                                "route_stage_quantities": [
                                    {
                                        "stage_code": "close_joint_a",
                                        "bucket_node": "finished_goods",
                                        "operation_key": "conversion",
                                        "input_qty": 25.0,
                                        "output_qty": 100.0,
                                        "output_uom": "unit",
                                    },
                                    {
                                        "stage_code": "dispatch_out_a",
                                        "bucket_node": "finished_goods",
                                        "operation_key": "fg",
                                        "input_qty": 100.0,
                                        "output_qty": 100.0,
                                        "output_uom": "unit",
                                    },
                                ]
                            },
                        },
                    )(),
                },
                "edges": [
                    type("Edge", (), {"from_node": "raw_material", "to_node": "board"})(),
                    type("Edge", (), {"from_node": "sheet", "to_node": "blanks"})(),
                    type("Edge", (), {"from_node": "blanks", "to_node": "finished_goods"})(),
                ],
            },
        )()

        preview = build_preview_model(
            shadow_runtime,
            spec_name="Spec Overlay",
            style="0427",
            plant_name="Plant Overlay",
            order_qty=100.0,
            qgraph=qgraph,
        )

        stages = {item["stage_code"]: item for item in preview["stages"]}
        self.assertEqual(stages["surface_pass_a"]["input_qty"], 25.0)
        self.assertEqual(stages["surface_pass_a"]["output_qty"], 25.0)
        self.assertEqual(stages["close_joint_a"]["input_qty"], 25.0)
        self.assertEqual(stages["close_joint_a"]["output_qty"], 100.0)
        self.assertEqual(
            (stages["close_joint_a"].get("computed_metrics") or {}).get("quantity_overlay_source"),
            "qgraph_route_stage_propagation",
        )

        ops = {item["stage"]: item for item in preview["operations"]}
        self.assertEqual(ops["conversion"]["input_qty"], 25.0)
        self.assertEqual(ops["conversion"]["output_qty"], 100.0)
        self.assertEqual(ops["fg"]["input_qty"], 100.0)
        self.assertEqual(ops["fg"]["output_qty"], 100.0)

    def test_grouped_operations_preserve_per_stage_identity_details(self):
        shadow_runtime = {
            "route_source": {"selection_source": "spec"},
            "routing": {"selection_source": "spec", "warnings": [], "stages": [{}, {}]},
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_template_id": 101,
                    "stage_contract_code": "route.stage.print_a",
                    "stage_code": "print_a",
                    "stage_name": "Print A",
                    "stage_type": "printing",
                    "capability_code": "printing",
                    "execution_type": "operation",
                    "target_output_qty": 10.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 10.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "non_stock",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_template_id": 102,
                    "stage_contract_code": "route.stage.print_b",
                    "stage_code": "print_b",
                    "stage_name": "Print B",
                    "stage_type": "printing",
                    "capability_code": "printing",
                    "execution_type": "operation",
                    "target_output_qty": 20.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 10.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "non_stock",
                    "rm_attachments": [],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                },
            ],
        }

        preview = build_preview_model(
            shadow_runtime,
            spec_name="Spec Stage Identity",
            style="0427",
            plant_name="Plant Stage Identity",
            order_qty=100.0,
        )

        ops = {item["stage"]: item for item in preview["operations"]}
        printing = ops["printing"]
        self.assertEqual(printing["stage_codes"], ["print_a", "print_b"])
        details = printing.get("stage_details") or []
        self.assertEqual([item["stage_code"] for item in details], ["print_a", "print_b"])
        self.assertEqual(details[0]["input_qty"], 10.0)
        self.assertEqual(details[0]["output_qty"], 10.0)
        self.assertEqual(details[1]["input_qty"], 10.0)
        self.assertEqual(details[1]["output_qty"], 20.0)
        routed = preview.get("routed_operations") or []
        self.assertEqual([item["stage_code"] for item in routed], ["print_a", "print_b"])
        self.assertEqual([item["stage"] for item in routed], ["printing", "printing"])
        self.assertEqual(routed[0]["input_qty"], 10.0)
        self.assertEqual(routed[0]["output_qty"], 10.0)
        self.assertEqual(routed[1]["input_qty"], 10.0)
        self.assertEqual(routed[1]["output_qty"], 20.0)

    def test_routed_operations_include_first_output_stage_when_input_is_zero(self):
        shadow_runtime = {
            "route_source": {"selection_source": "spec"},
            "routing": {"selection_source": "spec", "warnings": [], "stages": [{}, {}, {}]},
            "material_requirements": [],
            "quantity_report": {},
            "parity_report": {},
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_template_id": 7,
                    "stage_contract_code": "v2_compat.stage.2ply",
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "capability_code": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 17.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [{"role_code": "liner", "product_tmpl_id": 10, "material_type": "rm"}],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_template_id": 8,
                    "stage_contract_code": "v2_compat.stage.board",
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "corrugation",
                    "capability_code": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 17.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 17.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [{"role_code": "top_sheet", "product_tmpl_id": 11, "material_type": "rm"}],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
                {
                    "index": 2,
                    "sequence": 30,
                    "stage_template_id": 13,
                    "stage_contract_code": "v2_compat.stage.seaming",
                    "stage_code": "seaming",
                    "stage_name": "Seaming / Jointing",
                    "stage_type": "conversion",
                    "capability_code": "conversion",
                    "execution_type": "material",
                    "target_output_qty": 100.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 17.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "fg",
                    "rm_attachments": [],
                    "consumable_attachments": [{"role_code": "adhesive", "product_tmpl_id": 20, "material_type": "consumable"}],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "box", "uom": "unit"},
                },
            ],
        }
        qgraph = type(
            "Graph",
            (),
            {
                "nodes": {
                    "raw_material": type("Node", (), {"qty": 99.04608})(),
                    "board": type("Node", (), {"qty": 17.0})(),
                    "sheet": type("Node", (), {"qty": 17.0})(),
                    "blanks": type("Node", (), {"qty": 100.0})(),
                    "finished_goods": type("Node", (), {"qty": 100.0})(),
                },
                "edges": [
                    type("Edge", (), {"from_node": "raw_material", "to_node": "board"})(),
                    type("Edge", (), {"from_node": "sheet", "to_node": "blanks"})(),
                    type("Edge", (), {"from_node": "blanks", "to_node": "finished_goods"})(),
                ],
            },
        )()

        preview = build_preview_model(
            shadow_runtime,
            spec_name="Spec First Output",
            style="0427",
            plant_name="Plant First Output",
            order_qty=100.0,
            qgraph=qgraph,
        )

        routed = preview.get("routed_operations") or []
        self.assertEqual([item["stage_code"] for item in routed], ["2ply", "board", "seaming"])
        self.assertEqual(routed[0]["input_qty"], 0.0)
        self.assertEqual(routed[0]["output_qty"], 17.0)

        ops = {item["stage"]: item for item in preview["operations"]}
        self.assertEqual(ops["corrugator"]["input_qty"], 99.04608)
        self.assertEqual(ops["corrugator"]["output_qty"], 17.0)
        self.assertEqual([item["stage_code"] for item in (ops["corrugator"].get("stage_details") or [])], ["2ply", "board"])

    def test_contract_identity_drives_rm_match_when_stage_names_differ(self):
        shadow_runtime = {
            "route_source": {"selection_source": "plant"},
            "routing": {"selection_source": "plant", "warnings": [], "stages": [{}]},
            "material_requirements": [
                {
                    "stage_template_id": 901,
                    "stage_contract_code": "v2_compat.stage.runtime_corr_a",
                    "stage_code": "runtime_corr_a",
                    "role": "liner",
                    "semantic_role": "liner",
                    "legacy_role_aliases": ["liner"],
                    "product_tmpl_id": 10,
                    "material_type": "rm",
                    "attach_quantity_basis": "per_m2",
                }
            ],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_template_id": 901,
                    "stage_contract_code": "v2_compat.stage.runtime_corr_a",
                    "stage_code": "runtime_corr_a",
                    "stage_name": "Runtime Corrugation A",
                    "stage_type": "corrugation",
                    "capability_code": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 25.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [
                        {
                            "stage_template_id": 901,
                            "stage_contract_code": "v2_compat.stage.runtime_corr_a",
                            "semantic_role": "liner",
                            "legacy_role_aliases": ["liner"],
                            "role_code": "RM_PAPER_LINER",
                            "product_tmpl_id": 10,
                            "material_type": "rm",
                        }
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                }
            ],
        }
        rm_requirements = [
            {
                "product_key": "rm_liner",
                "role": "liner",
                "stage_key": "legacy_corr_name",
                "stage_template_id": 901,
                "stage_contract_code": "v2_compat.stage.runtime_corr_a",
                "qty_per_fg": 0.25,
                "qty_uom": "kg",
                "consumption_type": "area",
                "metadata": {},
            }
        ]

        preview = build_preview_model(
            shadow_runtime,
            spec_name="Spec E",
            style="0201",
            plant_name="Plant E",
            order_qty=100.0,
            rm_requirements=rm_requirements,
            product_templates={10: "Kraft Liner"},
        )

        self.assertEqual(len(preview["materials"]), 1)
        self.assertEqual(preview["materials"][0]["quantity_source"], "legacy_rm_requirement")
        self.assertEqual(preview["materials"][0]["qty_per_fg"], 0.25)
        self.assertEqual(preview["materials"][0]["product"], "Kraft Liner")

    def test_runtime_adhesive_semantics_remove_preview_side_override(self):
        shadow_runtime = {
            "route_source": {"selection_source": "plant"},
            "routing": {"selection_source": "plant", "warnings": [], "stages": [{}]},
            "material_requirements": [
                {
                    "stage_template_id": 990,
                    "stage_contract_code": "v2_compat.stage.close_joint_a",
                    "stage_code": "close_joint_a",
                    "role": "adhesive",
                    "semantic_role": "adhesive_fg",
                    "legacy_role_aliases": ["adhesive_fg", "adhesive"],
                    "product_tmpl_id": 20,
                    "material_type": "consumable",
                    "attach_quantity_basis": "per_box",
                }
            ],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_template_id": 990,
                    "stage_contract_code": "v2_compat.stage.close_joint_a",
                    "stage_code": "close_joint_a",
                    "stage_name": "Close Joint A",
                    "stage_type": "conversion",
                    "capability_code": "conversion",
                    "execution_type": "material",
                    "target_output_qty": 100.0,
                    "target_output_uom": "unit",
                    "input_qty_from_prev": 100.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [],
                    "consumable_attachments": [
                        {
                            "stage_template_id": 990,
                            "stage_contract_code": "v2_compat.stage.close_joint_a",
                            "role_code": "CONSUMABLE_ADHESIVE_FG",
                            "semantic_role": "adhesive_fg",
                            "legacy_role_aliases": ["adhesive_fg", "adhesive"],
                            "product_tmpl_id": 20,
                            "material_type": "consumable",
                        }
                    ],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "box", "uom": "unit"},
                }
            ],
        }
        rm_requirements = [
            {
                "product_key": "rm_glue_fg",
                "role": "adhesive_fg",
                "stage_key": "legacy_fg_glue",
                "stage_template_id": 990,
                "stage_contract_code": "v2_compat.stage.close_joint_a",
                "qty_per_fg": 0.03,
                "qty_uom": "kg",
                "consumption_type": "area",
                "metadata": {},
            }
        ]

        preview = build_preview_model(
            shadow_runtime,
            spec_name="Spec F",
            style="0201",
            plant_name="Plant F",
            order_qty=100.0,
            rm_requirements=rm_requirements,
            product_templates={20: "FG Adhesive"},
        )

        self.assertEqual(len(preview["materials"]), 1)
        self.assertEqual(preview["materials"][0]["role"], "adhesive_fg")
        self.assertEqual(preview["materials"][0]["quantity_source"], "legacy_rm_requirement")
        self.assertEqual(preview["materials"][0]["product"], "FG Adhesive")

    def test_unmatched_legacy_rm_rows_are_emitted_for_repeated_5ply_layers(self):
        shadow_runtime = {
            "route_source": {"selection_source": "spec"},
            "routing": {"selection_source": "spec", "warnings": [], "stages": [{}, {}]},
            "material_requirements": [],
            "stage_contexts": [
                {
                    "index": 0,
                    "sequence": 10,
                    "stage_template_id": 701,
                    "stage_contract_code": "v2_compat.stage.2ply",
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "capability_code": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 17.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 0.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [
                        {
                            "stage_template_id": 701,
                            "stage_contract_code": "v2_compat.stage.2ply",
                            "semantic_role": "liner",
                            "legacy_role_aliases": ["liner"],
                            "role_code": "RM_PAPER_LINER",
                            "product_tmpl_id": 10,
                            "material_type": "rm",
                        },
                        {
                            "stage_template_id": 701,
                            "stage_contract_code": "v2_compat.stage.2ply",
                            "semantic_role": "liner",
                            "legacy_role_aliases": ["liner"],
                            "role_code": "RM_PAPER_LINER",
                            "product_tmpl_id": 10,
                            "material_type": "rm",
                        },
                        {
                            "stage_template_id": 701,
                            "stage_contract_code": "v2_compat.stage.2ply",
                            "semantic_role": "medium",
                            "legacy_role_aliases": ["medium"],
                            "role_code": "RM_PAPER_MEDIUM",
                            "product_tmpl_id": 11,
                            "material_type": "rm",
                        },
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
                {
                    "index": 1,
                    "sequence": 20,
                    "stage_template_id": 702,
                    "stage_contract_code": "v2_compat.stage.board",
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "corrugation",
                    "capability_code": "corrugation",
                    "execution_type": "material",
                    "target_output_qty": 17.0,
                    "target_output_uom": "sheet",
                    "input_qty_from_prev": 17.0,
                    "fg_demand_qty": 100.0,
                    "output_stock_behavior": "semi_finished",
                    "rm_attachments": [
                        {
                            "stage_template_id": 702,
                            "stage_contract_code": "v2_compat.stage.board",
                            "semantic_role": "top_sheet_external",
                            "legacy_role_aliases": ["top_sheet_external", "top_roll"],
                            "role_code": "RM_PAPER_TOP",
                            "product_tmpl_id": 12,
                            "material_type": "rm",
                        }
                    ],
                    "consumable_attachments": [],
                    "intermediate_attachments": [],
                    "warnings": [],
                    "computed_metrics": {},
                    "output_descriptor": {"kind": "board", "uom": "sheet"},
                },
            ],
        }
        rm_requirements = [
            {
                "product_key": "rm_liner",
                "role": "liner",
                "stage_key": "2ply",
                "stage_template_id": 701,
                "stage_contract_code": "v2_compat.stage.2ply",
                "layer_no": 1,
                "qty_per_fg": 0.34272,
                "qty_uom": "kg",
                "consumption_type": "area",
                "metadata": {"layer_no": 1, "resolution_product_name": "Liner 1"},
            },
            {
                "product_key": "rm_medium",
                "role": "medium",
                "stage_key": "2ply",
                "stage_template_id": 701,
                "stage_contract_code": "v2_compat.stage.2ply",
                "layer_no": 2,
                "qty_per_fg": 0.2856,
                "qty_uom": "kg",
                "consumption_type": "area",
                "metadata": {"layer_no": 2, "resolution_product_name": "Medium 2"},
            },
            {
                "product_key": "rm_liner",
                "role": "liner",
                "stage_key": "2ply",
                "stage_template_id": 701,
                "stage_contract_code": "v2_compat.stage.2ply",
                "layer_no": 3,
                "qty_per_fg": 0.34272,
                "qty_uom": "kg",
                "consumption_type": "area",
                "metadata": {"layer_no": 3, "resolution_product_name": "Liner 3"},
            },
            {
                "product_key": "rm_medium",
                "role": "medium",
                "stage_key": "2ply",
                "stage_template_id": 701,
                "stage_contract_code": "v2_compat.stage.2ply",
                "layer_no": 4,
                "qty_per_fg": 0.2856,
                "qty_uom": "kg",
                "consumption_type": "area",
                "metadata": {"layer_no": 4, "resolution_product_name": "Medium 4"},
            },
            {
                "product_key": "rm_top_sheet_external",
                "role": "top_sheet_external",
                "stage_key": "board",
                "stage_template_id": 702,
                "stage_contract_code": "v2_compat.stage.board",
                "qty_per_fg": 0.34272,
                "qty_uom": "kg",
                "consumption_type": "area",
                "metadata": {"resolution_product_name": "Top Sheet"},
            },
        ]

        preview = build_preview_model(
            shadow_runtime,
            spec_name="Spec 5ply",
            style="0201",
            plant_name="Plant 5ply",
            order_qty=100.0,
            rm_requirements=rm_requirements,
        )

        materials = preview["materials"]
        self.assertEqual(len(materials), 5)
        self.assertEqual(
            [(item["stage_code"], item["role"], item.get("layer_no")) for item in materials],
            [
                ("2ply", "liner", 1),
                ("2ply", "medium", 2),
                ("2ply", "liner", 3),
                ("2ply", "medium", 4),
                ("board", "top_sheet_external", None),
            ],
        )
        self.assertEqual(materials[3]["quantity_source"], "legacy_rm_requirement")
        self.assertIn("emitted from unmatched legacy rm requirement", materials[3]["notes"])
        self.assertEqual(materials[3]["product"], "Medium 4")
        self.assertEqual(preview["material_stats"]["legacy_unmatched_rows"], 0)




