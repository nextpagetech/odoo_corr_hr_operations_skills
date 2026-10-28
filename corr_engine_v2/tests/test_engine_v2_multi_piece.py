import unittest

from custom_addons.corr_engine_v2.engine_v2 import (
    EngineMetrics,
    EngineProduct,
    EngineResult,
    EngineSpecInput,
    FlowEdge,
    Stage,
    StageGraph,
    compute,
)
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.spec_input import (
    BoardLayerInput,
    RoutingProfileInput,
    RoutingStageInput,
)


class TestEngineV2MultiPiece(unittest.TestCase):
    def _spec(self):
        return EngineSpecInput(
            blank_length_mm=1200.0,
            blank_width_mm=900.0,
            liner_gsm=180.0,
            medium_gsm=150.0,
            style_code="",
            sfg_mode="staged",
            spec_id=1,
            spec_name="Multi Piece Box",
            board_layers_v2=[
                BoardLayerInput(sequence=1, role="outer_liner", gsm=180.0),
                BoardLayerInput(sequence=2, role="medium", gsm=150.0),
                BoardLayerInput(sequence=3, role="inner_liner", gsm=180.0),
            ],
            routing_profile=RoutingProfileInput(
                name="Multi Piece Route",
                stages=[
                    RoutingStageInput(
                        sequence=10,
                        stage_code="2ply",
                        stage_type="material",
                        stage_semantic="corrugation",
                    ),
                    RoutingStageInput(
                        sequence=20,
                        stage_code="board",
                        stage_type="material",
                        stage_semantic="conversion",
                    ),
                    RoutingStageInput(
                        sequence=30,
                        stage_code="diecut",
                        stage_type="material",
                        stage_semantic="conversion",
                    ),
                    RoutingStageInput(
                        sequence=40,
                        stage_code="seaming",
                        stage_type="material",
                        stage_semantic="fg",
                        output_kind="box",
                    ),
                    RoutingStageInput(
                        sequence=50,
                        stage_code="fg",
                        stage_type="material",
                        stage_semantic="fg",
                        output_kind="fg",
                        is_fg_stage=True,
                    ),
                ],
            ),
        )

    def _plant(self):
        return PlantProfile(
            stages=["2ply", "board", "diecut", "seaming", "fg"],
            code="MP",
            name="Multi Piece Plant",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=2400.0,
            sheet_width_mm=1800.0,
        )

    def test_equal_width_split_branches_and_merges_at_seaming(self):
        result = compute(
            self._spec(),
            self._plant(),
            options={
                "multi_piece": {
                    "enabled": True,
                    "split_axis": "width",
                    "split_mode": "equal",
                    "piece_count": 2,
                    "merge_stage_key": "seaming",
                }
            },
        )

        stage_keys = [stage.key for stage in result.stage_graph.stages]
        self.assertIn("2ply__piece_a", stage_keys)
        self.assertIn("diecut__piece_b", stage_keys)
        self.assertIn("seaming", stage_keys)
        self.assertIn("fg", stage_keys)

        merge_inputs = [edge for edge in result.stage_graph.flows if edge.to_stage == "seaming"]
        self.assertEqual({edge.piece_key for edge in merge_inputs}, {"piece_a", "piece_b"})
        self.assertEqual(
            {edge.product_key for edge in merge_inputs},
            {"sfg_diecut__piece_a", "sfg_diecut__piece_b"},
        )

        plan = result.metrics.extra["multi_piece"]
        self.assertEqual(plan["split_axis"], "width")
        self.assertEqual([piece["piece_width_mm"] for piece in plan["pieces"]], [450.0, 450.0])
        self.assertEqual([piece["piece_length_mm"] for piece in plan["pieces"]], [1200.0, 1200.0])

    def test_zero_source_options_fall_back_to_current_blank_geometry(self):
        spec = self._spec()
        spec.blank_length_mm = 0.0
        spec.blank_width_mm = 0.0
        spec.box_length_mm = 1200.0
        spec.box_width_mm = 900.0

        result = compute(
            spec,
            self._plant(),
            options={
                "multi_piece": {
                    "enabled": True,
                    "split_axis": "width",
                    "split_mode": "equal",
                    "piece_count": 2,
                    "source_length_mm": 0.0,
                    "source_width_mm": 0.0,
                    "merge_stage_key": "seaming",
                }
            },
        )

        plan = result.metrics.extra["multi_piece"]
        self.assertEqual(plan["source_length_mm"], 1200.0)
        self.assertEqual(plan["source_width_mm"], 900.0)
        self.assertEqual([piece["piece_width_mm"] for piece in plan["pieces"]], [450.0, 450.0])

    def test_uneven_width_split_splits_rm_by_piece_area(self):
        result = compute(
            self._spec(),
            self._plant(),
            options={
                "multi_piece": {
                    "enabled": True,
                    "split_axis": "width",
                    "split_mode": "uneven",
                    "piece_count": 2,
                    "merge_stage_key": "seaming",
                    "pieces": [
                        {"piece_key": "piece_a", "piece_name": "A", "piece_width_mm": 500.0},
                        {"piece_key": "piece_b", "piece_name": "B", "piece_width_mm": 400.0},
                    ],
                }
            },
        )

        liner_by_piece = {}
        for rm in result.rm_requirements:
            if rm.role == "liner" and rm.layer_no == 1:
                liner_by_piece[rm.metadata.get("piece_key")] = rm.qty_per_fg

        self.assertAlmostEqual(liner_by_piece["piece_a"], 0.108, places=6)
        self.assertAlmostEqual(liner_by_piece["piece_b"], 0.0864, places=6)
        self.assertIn("sfg_board__piece_a", result.product_index())
        self.assertIn("sfg_board__piece_b", result.product_index())

    def test_multi_piece_route_with_operations_registers_flow_sfg_products(self):
        spec = self._spec()
        spec.routing_profile = RoutingProfileInput(
            name="Multi Piece Route With Operation",
            stages=[
                RoutingStageInput(
                    sequence=10,
                    stage_code="2ply",
                    stage_type="material",
                    stage_semantic="corrugation",
                ),
                RoutingStageInput(
                    sequence=20,
                    stage_code="printing",
                    stage_type="operation",
                    stage_semantic="printing",
                ),
                RoutingStageInput(
                    sequence=30,
                    stage_code="board",
                    stage_type="material",
                    stage_semantic="conversion",
                ),
                RoutingStageInput(
                    sequence=40,
                    stage_code="diecut",
                    stage_type="material",
                    stage_semantic="conversion",
                ),
                RoutingStageInput(
                    sequence=50,
                    stage_code="seaming",
                    stage_type="material",
                    stage_semantic="fg",
                    output_kind="box",
                ),
                RoutingStageInput(
                    sequence=60,
                    stage_code="fg",
                    stage_type="material",
                    stage_semantic="fg",
                    output_kind="fg",
                    is_fg_stage=True,
                ),
            ],
        )

        result = compute(
            spec,
            self._plant(),
            options={
                "multi_piece": {
                    "enabled": True,
                    "split_axis": "width",
                    "split_mode": "equal",
                    "piece_count": 2,
                    "merge_stage_key": "seaming",
                }
            },
        )

        product_keys = set(result.product_index())
        flow_sfg_keys = {
            edge.product_key
            for edge in result.stage_graph.flows
            if str(edge.product_key or "").startswith("sfg_")
        }
        self.assertTrue(flow_sfg_keys)
        self.assertTrue(flow_sfg_keys.issubset(product_keys))

    def test_validation_registers_missing_sfg_flow_product(self):
        result = EngineResult(
            spec_id=1,
            style_code="0201",
            sfg_mode="staged",
            products=[EngineProduct(key="fg_box", role="fg", description="FG", uom="unit")],
            stage_graph=StageGraph(
                stages=[
                    Stage(key="board__piece_a", name="Board A", stage_type="material"),
                    Stage(key="seaming", name="Seaming", stage_type="material"),
                    Stage(key="fg", name="FG", stage_type="material", is_terminal=True),
                ],
                flows=[
                    FlowEdge(
                        from_stage="board__piece_a",
                        to_stage="seaming",
                        product_key="sfg_board__piece_a",
                        qty_per_fg=1.0,
                    ),
                    FlowEdge(
                        from_stage="seaming",
                        to_stage="fg",
                        product_key="fg_box",
                        qty_per_fg=1.0,
                    ),
                ],
            ),
            rm_requirements=[],
            metrics=EngineMetrics(),
        )

        result.validate()

        self.assertIn("sfg_board__piece_a", result.product_index())

    def test_equal_length_split_derives_piece_lengths(self):
        result = compute(
            self._spec(),
            self._plant(),
            options={
                "multi_piece": {
                    "enabled": True,
                    "split_axis": "length",
                    "split_mode": "equal",
                    "piece_count": 2,
                    "merge_stage_key": "seaming",
                }
            },
        )

        pieces = result.metrics.extra["multi_piece"]["pieces"]
        self.assertEqual([piece["piece_length_mm"] for piece in pieces], [600.0, 600.0])
        self.assertEqual([piece["piece_width_mm"] for piece in pieces], [900.0, 900.0])

    def test_ratio_width_split_derives_piece_widths_after_style_geometry(self):
        result = compute(
            self._spec(),
            self._plant(),
            options={
                "multi_piece": {
                    "enabled": True,
                    "split_axis": "width",
                    "split_mode": "uneven",
                    "split_method": "ratio",
                    "ratio_a": 60.0,
                    "ratio_b": 40.0,
                    "piece_count": 2,
                    "merge_stage_key": "seaming",
                }
            },
        )

        pieces = result.metrics.extra["multi_piece"]["pieces"]
        self.assertEqual([piece["piece_width_mm"] for piece in pieces], [540.0, 360.0])
        self.assertEqual([piece["piece_length_mm"] for piece in pieces], [1200.0, 1200.0])

    def test_primary_length_split_derives_other_piece_length(self):
        result = compute(
            self._spec(),
            self._plant(),
            options={
                "multi_piece": {
                    "enabled": True,
                    "split_axis": "length",
                    "split_mode": "uneven",
                    "split_method": "primary",
                    "primary_piece_key": "piece_a",
                    "primary_size_mm": 700.0,
                    "piece_count": 2,
                    "merge_stage_key": "seaming",
                }
            },
        )

        pieces = result.metrics.extra["multi_piece"]["pieces"]
        self.assertEqual([piece["piece_length_mm"] for piece in pieces], [700.0, 500.0])
        self.assertEqual([piece["piece_width_mm"] for piece in pieces], [900.0, 900.0])

    def test_invalid_uneven_width_split_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Piece widths must add up"):
            compute(
                self._spec(),
                self._plant(),
                options={
                    "multi_piece": {
                        "enabled": True,
                        "split_axis": "width",
                        "split_mode": "uneven",
                        "piece_count": 2,
                        "pieces": [
                            {"piece_key": "piece_a", "piece_width_mm": 500.0},
                            {"piece_key": "piece_b", "piece_width_mm": 300.0},
                        ],
                    }
                },
            )


if __name__ == "__main__":
    unittest.main()




