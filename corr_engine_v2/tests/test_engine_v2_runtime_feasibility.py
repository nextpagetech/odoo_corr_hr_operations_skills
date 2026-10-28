import unittest
from types import SimpleNamespace
from unittest.mock import patch

from custom_addons.corr_engine_v2 import engine_v2_runtime


class _FakeStageTemplateModel:
    def __init__(self, records=None):
        self._records = records or {}

    def sudo(self):
        return self

    def browse(self, record_id):
        return self._records.get(record_id)


class _FakeConfigParameterModel:
    def __init__(self, values=None):
        self._values = values or {}

    def sudo(self):
        return self

    def get_param(self, key, default=None):
        return self._values.get(key, default)


class _FakeInputRoleModel:
    def __init__(self, records=None):
        self._records = records or []

    def sudo(self):
        return self

    def search(self, domain):
        return self._records


class _FakeEnv:
    def __init__(self, *, stage_templates=None, config_params=None, input_roles=None):
        self._models = {
            "corr.engine.stage.template": _FakeStageTemplateModel(stage_templates),
            "ir.config_parameter": _FakeConfigParameterModel(config_params),
            "corr.engine.input.role": _FakeInputRoleModel(input_roles),
        }

    def __getitem__(self, model_name):
        if model_name not in self._models:
            raise KeyError(model_name)
        return self._models[model_name]


class TestEngineV2RuntimeFeasibility(unittest.TestCase):
    def _make_stage(self, **overrides):
        values = {
            "stage_template_id": 7,
            "stage_contract_code": "v2_compat.stage.2ply",
            "stage_code": "2ply",
            "stage_name": "2-Ply Corrugation",
            "stage_type": "corrugation",
            "capability_code": "corrugation",
            "sequence": 10,
            "is_optional": False,
            "input_quantity_node_key": "raw_material",
            "output_quantity_node_key": "board",
            "plant_binding_id": None,
            "workcenter_id": None,
            "routing_workcenter_id": None,
        }
        values.update(overrides)
        return engine_v2_runtime.RoutingStageRun(**values)

    def _make_context(self, **overrides):
        values = {
            "stage_template_id": 7,
            "stage_contract_code": "v2_compat.stage.2ply",
            "stage_code": "2ply",
            "stage_name": "2-Ply Corrugation",
            "stage_type": "corrugation",
            "sequence": 10,
            "index": 0,
            "is_optional": False,
            "execution_type": "material",
            "capability_code": "corrugation",
            "capability_name": "Corrugation",
            "input_quantity_node_key": "raw_material",
            "output_quantity_node_key": "board",
            "consumes_previous_output": False,
            "previous_output_required": False,
            "output_kind": "board",
            "output_stock_behavior": "board",
            "can_be_terminal": False,
            "bom_mapping_policy": None,
            "quantity_profile_code": None,
            "quantity_profile_id": None,
        }
        values.update(overrides)
        return engine_v2_runtime.StageContext(**values)

    def _make_engine_result(self, **metric_overrides):
        metrics = SimpleNamespace(
            extra={
                "geometry": {
                    "box_length_mm": 700.0,
                    "box_width_mm": 800.0,
                    "box_height_mm": 300.0,
                    "blank_length_mm": 3020.0,
                    "blank_width_mm": 1120.0,
                },
                "layout": {
                    "sheet_width_mm": 1000.0,
                    "sheet_length_mm": 1500.0,
                    "sheet_source": "plant_default",
                },
                "effective_ups_machine": 1,
                "effective_ups": 1,
            },
            effective_ups=1,
            printing_ups=1,
            diecut_ups=1,
        )
        for key, value in metric_overrides.items():
            setattr(metrics, key, value)
        return SimpleNamespace(metrics=metrics)

    def test_engine_lamination_rm_requirement_attaches_as_consumable(self):
        context = self._make_context(
            stage_template_id=33,
            stage_contract_code="v2_compat.stage.top_sheet_film",
            stage_code="top_sheet_film",
            stage_name="Top Sheet Film",
            stage_type="lamination",
            capability_code="lamination",
            capability_name="Lamination",
            input_quantity_node_key="sheet",
            output_quantity_node_key="sheet",
            output_kind="sheet",
        )
        env = _FakeEnv(
            input_roles=[
                SimpleNamespace(code="CONSUMABLE_VARNISH", category="consumable"),
            ]
        )
        engine_result = SimpleNamespace(
            rm_requirements=[
                SimpleNamespace(
                    role="lamination",
                    product_key="rm_lamination_film",
                    stage_key="top_sheet_film",
                    metadata={},
                )
            ]
        )

        engine_v2_runtime._attach_engine_rm_requirements_to_stage_contexts(
            env,
            [context],
            engine_result,
        )

        self.assertEqual(len(context.rm_attachments), 0)
        self.assertEqual(len(context.consumable_attachments), 1)
        self.assertEqual(context.consumable_attachments[0].semantic_role, "lamination")

    def test_sheet_fits_workcenter_directly(self):
        stage = self._make_stage()
        context = self._make_context()
        plant = SimpleNamespace(
            max_machine_width_mm=1200.0,
            sheet_width_mm=0.0,
            sheet_length_mm=2000.0,
            printing_ups=2,
            diecut_ups=2,
            max_corrugator_width_mm=0.0,
            max_printer_width_mm=0.0,
            max_diecut_width_mm=0.0,
        )
        engine_result = self._make_engine_result()
        candidates = [
            {
                "source": "operation_map",
                "operation_name": "Corrugation",
                "workcenter_id": 10,
                "workcenter_name": "WC OK",
                "deckle_min_cm": 50.0,
                "deckle_max_cm": 120.0,
                "routing_bound": False,
            },
            {
                "source": "operation_map",
                "operation_name": "Corrugation",
                "workcenter_id": 11,
                "workcenter_name": "WC Narrow",
                "deckle_min_cm": 0.0,
                "deckle_max_cm": 90.0,
                "routing_bound": False,
            },
        ]

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_candidate_workcenters",
            return_value=candidates,
        ):
            report = engine_v2_runtime._build_stage_feasibility(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                plant_param=plant,
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        self.assertEqual(report["summary"]["stage_count"], 1)
        entry = report["stages"][0]
        self.assertEqual(entry["stage_code"], "2ply")
        self.assertEqual(entry["status"], "feasible")
        self.assertEqual(entry["current_dimensions"]["width_mm"], 1000.0)
        self.assertEqual(entry["current_dimensions"]["length_mm"], 1500.0)
        self.assertEqual(entry["current_dimensions"]["basis"], "sheet")
        self.assertEqual(entry["current_dimensions"]["basis_reason"], "resolved sheet dimensions are available from layout")
        self.assertEqual(entry["current_dimensions"]["sheet_source"], "plant_default")
        self.assertEqual(entry["current_dimensions"]["box_length_mm"], 700.0)
        self.assertEqual(entry["current_dimensions"]["box_width_mm"], 800.0)
        self.assertEqual(entry["current_dimensions"]["box_height_mm"], 300.0)
        self.assertEqual(entry["current_dimensions"]["blank_length_mm"], 3020.0)
        self.assertEqual(entry["current_dimensions"]["blank_width_mm"], 1120.0)
        self.assertFalse(entry["ups_rejection_applied"])
        self.assertEqual(entry["candidates"][0]["status"], "feasible")
        self.assertEqual(entry["candidates"][1]["status"], "rejected")
        self.assertEqual(entry["candidates"][0]["used_orientation"], "direct")
        self.assertTrue(
            any(
                "width" in reason
                for reason in entry["candidates"][1]["rejection_reasons"]
            )
        )

    def test_sheet_fails_direct_orientation_but_passes_rotated_orientation(self):
        stage = self._make_stage()
        context = self._make_context()
        plant = SimpleNamespace(
            max_machine_width_mm=0.0,
            sheet_width_mm=0.0,
            sheet_length_mm=0.0,
            printing_ups=1,
            diecut_ups=1,
            max_corrugator_width_mm=0.0,
            max_printer_width_mm=0.0,
            max_diecut_width_mm=0.0,
        )
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 1300.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 900.0
        candidates = [
            {
                "source": "operation_map",
                "operation_name": "Corrugation",
                "workcenter_id": 12,
                "workcenter_name": "WC Rotate",
                "deckle_min_cm": 0.0,
                "deckle_max_cm": 100.0,
                "deckle_max_source": "operation_map",
                "routing_bound": False,
            }
        ]

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_candidate_workcenters",
            return_value=candidates,
        ):
            report = engine_v2_runtime._build_stage_feasibility(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                plant_param=plant,
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        candidate = report["stages"][0]["candidates"][0]
        self.assertEqual(candidate["status"], "feasible")
        self.assertEqual(candidate["used_orientation"], "rotated")
        self.assertTrue(candidate["rotation_used"])
        self.assertEqual(
            [item["status"] for item in candidate["dimension_checks"]],
            ["rejected", "feasible"],
        )

    def test_sheet_fails_all_orientations(self):
        stage = self._make_stage()
        context = self._make_context()
        plant = SimpleNamespace(
            max_machine_width_mm=0.0,
            sheet_width_mm=0.0,
            sheet_length_mm=0.0,
            printing_ups=1,
            diecut_ups=1,
            max_corrugator_width_mm=0.0,
            max_printer_width_mm=0.0,
            max_diecut_width_mm=0.0,
        )
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 1300.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 1100.0
        candidates = [
            {
                "source": "operation_map",
                "operation_name": "Corrugation",
                "workcenter_id": 13,
                "workcenter_name": "WC Too Small",
                "deckle_min_cm": 0.0,
                "deckle_max_cm": 100.0,
                "deckle_max_source": "operation_map",
                "routing_bound": False,
            }
        ]

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_candidate_workcenters",
            return_value=candidates,
        ):
            report = engine_v2_runtime._build_stage_feasibility(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                plant_param=plant,
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        candidate = entry["candidates"][0]
        self.assertEqual(candidate["status"], "rejected")
        self.assertIsNone(candidate["used_orientation"])
        self.assertEqual(entry["status"], "rejected")
        self.assertTrue(any("width" in reason for reason in candidate["rejection_reasons"]))

    def test_no_direct_ups_rejection(self):
        stage = self._make_stage()
        context = self._make_context()
        plant = SimpleNamespace(
            max_machine_width_mm=0.0,
            sheet_width_mm=0.0,
            sheet_length_mm=0.0,
            printing_ups=1,
            diecut_ups=1,
            max_corrugator_width_mm=0.0,
            max_printer_width_mm=0.0,
            max_diecut_width_mm=0.0,
        )
        engine_result = self._make_engine_result(printing_ups=9, diecut_ups=9, effective_ups=9)
        engine_result.metrics.extra["effective_ups_machine"] = 9
        engine_result.metrics.extra["effective_ups"] = 9
        candidates = [
            {
                "source": "operation_map",
                "operation_name": "Corrugation",
                "workcenter_id": 14,
                "workcenter_name": "WC No UPS Reject",
                "deckle_min_cm": 0.0,
                "deckle_max_cm": 120.0,
                "deckle_max_source": "operation_map",
                "routing_bound": False,
            }
        ]

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_candidate_workcenters",
            return_value=candidates,
        ):
            report = engine_v2_runtime._build_stage_feasibility(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                plant_param=plant,
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        candidate = entry["candidates"][0]
        self.assertEqual(entry["current_ups"], 9)
        self.assertEqual(entry["ups_limit"], None)
        self.assertFalse(entry["ups_rejection_applied"])
        self.assertEqual(candidate["status"], "feasible")
        self.assertFalse(any("UPS" in reason for reason in (candidate["rejection_reasons"] or [])))

    def test_plant_fallback_only_applies_when_candidate_limits_absent(self):
        stage = self._make_stage(stage_code="board", stage_name="Board Formation", stage_type="lamination")
        context = self._make_context(
            stage_code="board",
            stage_name="Board Formation",
            stage_type="lamination",
            capability_code="lamination",
            capability_name="Lamination",
            output_kind="board",
            output_stock_behavior="board",
        )
        plant = SimpleNamespace(
            max_machine_width_mm=1200.0,
            sheet_width_mm=0.0,
            sheet_length_mm=2000.0,
            printing_ups=2,
            diecut_ups=2,
            max_corrugator_width_mm=0.0,
            max_printer_width_mm=0.0,
            max_diecut_width_mm=0.0,
        )
        engine_result = self._make_engine_result()

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_candidate_workcenters",
            return_value=[
                {
                    "source": "operation_map",
                    "operation_name": "Lamination",
                    "workcenter_id": 15,
                    "workcenter_name": "WC Plant Fallback",
                    "deckle_min_cm": None,
                    "deckle_max_cm": None,
                    "deckle_min_source": None,
                    "deckle_max_source": None,
                    "routing_bound": False,
                }
            ],
        ):
            report = engine_v2_runtime._build_stage_feasibility(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                plant_param=plant,
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        candidate = report["stages"][0]["candidates"][0]
        self.assertEqual(candidate["status"], "feasible")
        self.assertEqual(candidate["resolved_constraints"]["width_limit_source"], "plant_fallback")
        self.assertEqual(candidate["resolved_constraints"]["length_limit_source"], "plant_fallback")

    def test_stage_dimension_payload_uses_computed_blank_geometry_for_debug(self):
        stage = self._make_stage()
        context = self._make_context()
        plant = SimpleNamespace(
            max_machine_width_mm=1200.0,
            sheet_width_mm=0.0,
            sheet_length_mm=2000.0,
            printing_ups=1,
            diecut_ups=1,
            max_corrugator_width_mm=0.0,
            max_printer_width_mm=0.0,
            max_diecut_width_mm=0.0,
        )
        engine_result = self._make_engine_result()
        spec = SimpleNamespace(
            box_length_mm=700.0,
            box_width_mm=800.0,
            box_height_mm=300.0,
            blank_length_mm=0.0,
            blank_width_mm=0.0,
        )

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_candidate_workcenters",
            return_value=[],
        ):
            report = engine_v2_runtime._build_stage_feasibility(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                plant_param=plant,
                spec=spec,
                v2_spec=None,
                engine_result=engine_result,
            )

        dims = report["stages"][0]["current_dimensions"]
        self.assertEqual(dims["box_length_mm"], 700.0)
        self.assertEqual(dims["box_width_mm"], 800.0)
        self.assertEqual(dims["box_height_mm"], 300.0)
        self.assertEqual(dims["blank_length_mm"], 3020.0)
        self.assertEqual(dims["blank_width_mm"], 1120.0)
        self.assertEqual(dims["layout_blank_length_mm"], 0.0)
        self.assertEqual(dims["layout_blank_width_mm"], 0.0)

    def test_stage_feasibility_rejects_when_no_candidates_exist(self):
        stage = self._make_stage(stage_code="board", stage_name="Board Formation", stage_type="lamination")
        context = self._make_context(
            stage_code="board",
            stage_name="Board Formation",
            stage_type="lamination",
            capability_code="lamination",
            capability_name="Lamination",
            output_kind="board",
            output_stock_behavior="board",
        )
        plant = SimpleNamespace(
            max_machine_width_mm=1200.0,
            sheet_width_mm=0.0,
            sheet_length_mm=2000.0,
            printing_ups=2,
            diecut_ups=2,
            max_corrugator_width_mm=0.0,
            max_printer_width_mm=0.0,
            max_diecut_width_mm=0.0,
        )
        engine_result = self._make_engine_result()

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_candidate_workcenters",
            return_value=[],
        ):
            report = engine_v2_runtime._build_stage_feasibility(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                plant_param=plant,
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        self.assertEqual(entry["status"], "rejected")
        self.assertEqual(entry["rejection_reasons"], ["no_candidate_workcenters"])

    def test_slitting_options_generate_widthwise_machine_limited_options(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 21,
                    "workcenter_name": "WC Wide",
                    "width_min_cm": 0.0,
                    "width_max_cm": 90.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 2000.0,
                    "constraint_source": "workcenter",
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        self.assertEqual(entry["blank_dimensions"]["blank_basis"], "computed_blank")
        self.assertEqual([item["up_count"] for item in entry["options"]], [1, 2, 3])
        self.assertEqual([item["feasible"] for item in entry["options"]], [True, True, False])
        self.assertEqual(entry["iteration_stop_reason"], "all_candidate_workcenters_rejected")
        self.assertEqual(entry["iteration_stop_up_count"], 3)
        self.assertEqual(report["summary"]["feasible_option_count"], 2)
        self.assertEqual(report["summary"]["rejected_option_count"], 1)
        self.assertEqual([item["up_count"] for item in entry["feasible_options"]], [2, 1])
        self.assertEqual([item["rank"] for item in entry["feasible_options"]], [1, 2])
        self.assertEqual(entry["preferred_option"]["up_count"], 2)
        self.assertEqual(entry["rejected_options"][0]["up_count"], 3)

    def test_slitting_options_publish_single_layout_driving_stage(self):
        stages = [
            self._make_stage(
                stage_template_id=7,
                stage_contract_code="v2_compat.stage.2ply",
                stage_code="2ply",
                stage_name="2-Ply Corrugation",
                sequence=10,
            ),
            self._make_stage(
                stage_template_id=8,
                stage_contract_code="v2_compat.stage.board",
                stage_code="board",
                stage_name="Board Formation",
                sequence=20,
            ),
        ]
        contexts = [
            self._make_context(
                stage_template_id=7,
                stage_contract_code="v2_compat.stage.2ply",
                stage_code="2ply",
                stage_name="2-Ply Corrugation",
                sequence=10,
            ),
            self._make_context(
                stage_template_id=8,
                stage_contract_code="v2_compat.stage.board",
                stage_code="board",
                stage_name="Board Formation",
                sequence=20,
            ),
        ]
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 21,
                    "workcenter_name": "WC Wide",
                    "width_min_cm": 0.0,
                    "width_max_cm": 90.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 2000.0,
                    "constraint_source": "workcenter",
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=_FakeEnv(),
                stages=stages,
                stage_contexts=contexts,
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        self.assertEqual([item["stage_code"] for item in report["stages"]], ["2ply", "board"])
        self.assertEqual((report.get("layout_driving_stage") or {}).get("stage_code"), "2ply")
        self.assertEqual(
            (report.get("layout_driving_stage") or {}).get("selection_source"),
            "first_eligible_layout_stage_fallback",
        )
        driver_flags = {item["stage_code"]: item.get("is_layout_driving_stage") for item in report["stages"]}
        self.assertEqual(driver_flags, {"2ply": True, "board": False})

    def test_slitting_options_blank_fits_only_1_up(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 31,
                    "workcenter_name": "WC One Up",
                    "width_min_cm": 0.0,
                    "width_max_cm": 75.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 2000.0,
                    "constraint_source": "workcenter",
                    "constraints_applied": {
                        "width_min_cm": 0.0,
                        "width_max_cm": 75.0,
                        "length_min_mm": 0.0,
                        "length_max_mm": 2000.0,
                    },
                    "constraint_status": {
                        "width_constraints_defined": True,
                        "length_constraints_defined": True,
                    },
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        options = report["stages"][0]["options"]
        self.assertEqual([item["up_count"] for item in options], [1, 2])
        self.assertEqual([item["feasible"] for item in options], [True, False])
        self.assertEqual(report["stages"][0]["iteration_stop_reason"], "all_candidate_workcenters_rejected")

    def test_slitting_options_blank_fits_1_up_and_2_up(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 32,
                    "workcenter_name": "WC Two Up",
                    "width_min_cm": 0.0,
                    "width_max_cm": 90.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 2000.0,
                    "constraint_source": "workcenter",
                    "constraints_applied": {
                        "width_min_cm": 0.0,
                        "width_max_cm": 90.0,
                        "length_min_mm": 0.0,
                        "length_max_mm": 2000.0,
                    },
                    "constraint_status": {
                        "width_constraints_defined": True,
                        "length_constraints_defined": True,
                    },
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        options = report["stages"][0]["options"]
        self.assertEqual([item["up_count"] for item in options], [1, 2, 3])
        self.assertEqual([item["feasible"] for item in options], [True, True, False])
        self.assertEqual(report["stages"][0]["iteration_stop_reason"], "all_candidate_workcenters_rejected")
        self.assertEqual([item["up_count"] for item in report["stages"][0]["feasible_options"]], [2, 1])
        self.assertEqual(report["stages"][0]["preferred_option"]["up_count"], 2)

    def test_slitting_options_can_use_rotated_orientation(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1300.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 500.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 22,
                    "workcenter_name": "WC Rotate",
                    "width_min_cm": 0.0,
                    "width_max_cm": 140.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 1000.0,
                    "constraint_source": "workcenter",
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        option = report["stages"][0]["options"][0]
        self.assertTrue(option["feasible"])
        self.assertEqual(option["orientation_used"], "rotated")
        self.assertTrue(option["rotated_used"])
        self.assertEqual(option["matched_workcenter"]["workcenter_id"], 22)

    def test_slitting_options_presentation_prefers_direct_over_rotated_on_same_up_count(self):
        presentation = engine_v2_runtime._build_slitting_option_presentation(
            [
                {
                    "up_count": 2,
                    "candidate_length_mm": 1000.0,
                    "candidate_width_mm": 800.0,
                    "orientation_used": "rotated",
                    "rotated_used": True,
                    "matched_workcenter": {"workcenter_id": 41, "workcenter_name": "WC Rotated"},
                    "feasible": True,
                    "rejection_reasons": [],
                },
                {
                    "up_count": 2,
                    "candidate_length_mm": 1000.0,
                    "candidate_width_mm": 800.0,
                    "orientation_used": "direct",
                    "rotated_used": False,
                    "matched_workcenter": {"workcenter_id": 42, "workcenter_name": "WC Direct"},
                    "feasible": True,
                    "rejection_reasons": [],
                },
            ]
        )

        self.assertEqual(
            [item["matched_workcenter"]["workcenter_id"] for item in presentation["feasible_options"]],
            [42, 41],
        )
        self.assertEqual(presentation["preferred_option"]["matched_workcenter"]["workcenter_id"], 42)

    def test_slitting_options_presentation_prefers_requested_up_count_when_available(self):
        presentation = engine_v2_runtime._build_slitting_option_presentation(
            [
                {
                    "up_count": 2,
                    "candidate_length_mm": 1000.0,
                    "candidate_width_mm": 800.0,
                    "orientation_used": "direct",
                    "rotated_used": False,
                    "matched_workcenter": {"workcenter_id": 42, "workcenter_name": "WC Direct"},
                    "feasible": True,
                    "rejection_reasons": [],
                },
                {
                    "up_count": 1,
                    "candidate_length_mm": 1000.0,
                    "candidate_width_mm": 400.0,
                    "orientation_used": "direct",
                    "rotated_used": False,
                    "matched_workcenter": {"workcenter_id": 40, "workcenter_name": "WC Single"},
                    "feasible": True,
                    "rejection_reasons": [],
                },
            ],
            requested_up_count=1,
        )

        self.assertEqual(presentation["preferred_option_source"], "requested_up_count")
        self.assertEqual(presentation["preferred_option"]["up_count"], 1)
        self.assertEqual(
            [item["up_count"] for item in presentation["feasible_options"]],
            [1, 2],
        )

    def test_build_slitting_options_prefers_v2_spec_forced_ups_when_feasible(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        v2_spec = SimpleNamespace(forced_ups=1)

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 11,
                    "workcenter_name": "WC Primary",
                    "width_min_cm": 0.0,
                    "width_max_cm": 100.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 2000.0,
                    "constraint_source": "workcenter",
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=v2_spec,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        self.assertEqual(entry["requested_up_count"], 1)
        self.assertEqual(entry["preferred_option"]["up_count"], 1)
        self.assertEqual(entry["presentation"]["preferred_option_source"], "requested_up_count")

    def test_slitting_options_blank_fits_only_when_rotated(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1300.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 500.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 33,
                    "workcenter_name": "WC Rotated Only",
                    "width_min_cm": 0.0,
                    "width_max_cm": 140.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 1000.0,
                    "constraint_source": "workcenter",
                    "constraints_applied": {
                        "width_min_cm": 0.0,
                        "width_max_cm": 140.0,
                        "length_min_mm": 0.0,
                        "length_max_mm": 1000.0,
                    },
                    "constraint_status": {
                        "width_constraints_defined": True,
                        "length_constraints_defined": True,
                    },
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        option = report["stages"][0]["options"][0]
        self.assertTrue(option["feasible"])
        self.assertEqual(option["orientation_used"], "rotated")
        self.assertTrue(option["rotated_used"])
        self.assertEqual(report["stages"][0]["preferred_option"]["orientation_used"], "rotated")

    def test_slitting_options_skip_ineligible_stage(self):
        stage = self._make_stage(
            stage_template_id=17,
            stage_contract_code="v2_compat.stage.seaming",
            stage_code="seaming",
            stage_name="Seaming / Jointing",
            stage_type="conversion",
            capability_code="conversion",
        )
        context = self._make_context(
            stage_template_id=17,
            stage_contract_code="v2_compat.stage.seaming",
            stage_code="seaming",
            stage_name="Seaming / Jointing",
            stage_type="conversion",
            capability_code="conversion",
            capability_name="Conversion",
            output_kind="box",
        )
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 90,
                    "workcenter_name": "Should Never Be Used",
                    "width_min_cm": 0.0,
                    "width_max_cm": 200.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 5000.0,
                    "constraint_source": "workcenter",
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        self.assertEqual(report["stages"], [])
        self.assertEqual(report["summary"]["stage_count"], 0)
        self.assertEqual(report["summary"]["option_count"], 0)

    def test_slitting_options_use_explicit_configured_false_over_legacy_fallback(self):
        stage = self._make_stage(stage_template_id=7, stage_code="2ply", stage_type="corrugation")
        configured_template = SimpleNamespace(
            consider_slitting_options_configured=True,
            consider_slitting_options=False,
        )
        env = _FakeEnv(stage_templates={7: configured_template})

        report = engine_v2_runtime._build_slitting_options(
            env=env,
            stages=[stage],
            stage_contexts=[self._make_context()],
            spec=None,
            v2_spec=None,
            engine_result=self._make_engine_result(),
        )

        self.assertEqual(report["stages"], [])
        self.assertEqual(report["summary"]["stage_count"], 0)

    def test_slitting_options_use_explicit_configured_true_over_legacy_fallback(self):
        stage = self._make_stage(
            stage_template_id=13,
            stage_contract_code="v2_compat.stage.seaming",
            stage_code="seaming",
            stage_name="Seaming / Jointing",
            stage_type="conversion",
            capability_code="conversion",
        )
        context = self._make_context(
            stage_template_id=13,
            stage_contract_code="v2_compat.stage.seaming",
            stage_code="seaming",
            stage_name="Seaming / Jointing",
            stage_type="conversion",
            capability_code="conversion",
            capability_name="Conversion",
            output_kind="box",
        )
        configured_template = SimpleNamespace(
            consider_slitting_options_configured=True,
            consider_slitting_options=True,
        )
        env = _FakeEnv(stage_templates={13: configured_template})

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 91,
                    "workcenter_name": "Configured WC",
                    "width_min_cm": 0.0,
                    "width_max_cm": 200.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 5000.0,
                    "constraint_source": "workcenter",
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=env,
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=self._make_engine_result(),
            )

        self.assertEqual(report["summary"]["stage_count"], 1)
        self.assertEqual(report["stages"][0]["stage_code"], "seaming")

    def test_slitting_options_diecut_is_ineligible(self):
        stage = self._make_stage(
            stage_template_id=11,
            stage_contract_code="v2_compat.stage.diecut",
            stage_code="diecut",
            stage_name="Die-cut",
            stage_type="diecut",
            capability_code="diecut",
        )
        context = self._make_context(
            stage_template_id=11,
            stage_contract_code="v2_compat.stage.diecut",
            stage_code="diecut",
            stage_name="Die-cut",
            stage_type="diecut",
            capability_code="diecut",
            capability_name="Die-cut",
            output_kind="sheet",
        )
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        report = engine_v2_runtime._build_slitting_options(
            env=object(),
            stages=[stage],
            stage_contexts=[context],
            spec=None,
            v2_spec=None,
            engine_result=engine_result,
        )

        self.assertEqual(report["stages"], [])
        self.assertEqual(report["summary"]["stage_count"], 0)

    def test_slitting_options_use_workcenter_max_ups_when_configured(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 23,
                    "workcenter_name": "WC Explicit Max",
                    "width_min_cm": None,
                    "width_max_cm": 500.0,
                    "length_min_mm": None,
                    "length_max_mm": 5000.0,
                    "max_slitting_ups": 2,
                    "max_slitting_ups_source": "workcenter",
                    "constraint_source": "workcenter",
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        self.assertEqual(entry["explicit_max_ups"], 2)
        self.assertEqual(entry["explicit_max_ups_source"], "workcenter.max_slitting_ups")
        self.assertEqual(entry["iteration_stop_reason"], "explicit_max_ups_reached")
        self.assertEqual([item["up_count"] for item in entry["options"]], [1, 2])
        self.assertEqual([item["up_count"] for item in entry["feasible_options"]], [2, 1])

    def test_slitting_options_use_global_max_ups_when_workcenter_limit_missing(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0
        env = _FakeEnv(config_params={"corr_engine_v2.max_slitting_ups": "3"})

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 24,
                    "workcenter_name": "WC Global Max",
                    "width_min_cm": None,
                    "width_max_cm": 500.0,
                    "length_min_mm": None,
                    "length_max_mm": 5000.0,
                    "constraint_source": "workcenter",
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=env,
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        self.assertEqual(entry["explicit_max_ups"], 3)
        self.assertEqual(entry["explicit_max_ups_source"], "corr_engine_v2.max_slitting_ups")
        self.assertEqual(entry["iteration_stop_reason"], "explicit_max_ups_reached")
        self.assertEqual([item["up_count"] for item in entry["options"]], [1, 2, 3])

    def test_slitting_options_workcenter_max_ups_overrides_global(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0
        env = _FakeEnv(config_params={"corr_engine_v2.max_slitting_ups": "5"})

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 25,
                    "workcenter_name": "WC Override Global",
                    "width_min_cm": None,
                    "width_max_cm": 500.0,
                    "length_min_mm": None,
                    "length_max_mm": 5000.0,
                    "max_slitting_ups": 2,
                    "max_slitting_ups_source": "workcenter",
                    "constraint_source": "workcenter",
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=env,
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        self.assertEqual(entry["explicit_max_ups"], 2)
        self.assertEqual(entry["explicit_max_ups_source"], "workcenter.max_slitting_ups")
        self.assertEqual([item["up_count"] for item in entry["options"]], [1, 2])

    def test_layout_driving_stage_falls_back_to_first_eligible_stage(self):
        entries = [
            {
                "stage_template_id": 7,
                "stage_contract_code": "v2_compat.stage.2ply",
                "stage_code": "2ply",
                "stage_name": "2-Ply Corrugation",
                "stage_type": "corrugation",
                "sequence": 10,
            },
            {
                "stage_template_id": 8,
                "stage_contract_code": "v2_compat.stage.board",
                "stage_code": "board",
                "stage_name": "Board Formation",
                "stage_type": "corrugation",
                "sequence": 20,
            },
        ]

        selected, source = engine_v2_runtime._resolve_layout_driving_stage_entry(
            env=_FakeEnv(),
            stage_entries=entries,
        )

        self.assertEqual(selected["stage_code"], "2ply")
        self.assertEqual(source, "first_eligible_layout_stage_fallback")

    def test_layout_driving_stage_prefers_explicit_primary_stage(self):
        entries = [
            {
                "stage_template_id": 7,
                "stage_contract_code": "v2_compat.stage.2ply",
                "stage_code": "2ply",
                "stage_name": "2-Ply Corrugation",
                "stage_type": "corrugation",
                "sequence": 10,
            },
            {
                "stage_template_id": 8,
                "stage_contract_code": "v2_compat.stage.board",
                "stage_code": "board",
                "stage_name": "Board Formation",
                "stage_type": "corrugation",
                "sequence": 20,
            },
        ]
        env = _FakeEnv(
            stage_templates={
                8: SimpleNamespace(drives_layout_selection=True),
            }
        )

        selected, source = engine_v2_runtime._resolve_layout_driving_stage_entry(
            env=env,
            stage_entries=entries,
        )

        self.assertEqual(selected["stage_code"], "board")
        self.assertEqual(source, "explicit_primary_layout_stage")

    def test_slitting_options_missing_limits_use_safe_default_not_safety_cap(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 800.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 24,
                    "workcenter_name": "WC No Limits",
                    "width_min_cm": None,
                    "width_max_cm": None,
                    "length_min_mm": None,
                    "length_max_mm": None,
                    "constraint_source": "workcenter",
                    "constraints_applied": {
                        "width_min_cm": None,
                        "width_max_cm": None,
                        "length_min_mm": None,
                        "length_max_mm": None,
                    },
                    "constraint_status": {
                        "width_constraints_defined": False,
                        "length_constraints_defined": False,
                    },
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=SimpleNamespace(),
                v2_spec=SimpleNamespace(),
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        self.assertEqual(entry["explicit_max_ups"], engine_v2_runtime.SLITTING_OPTION_DEFAULT_MAX_UPS)
        self.assertEqual(entry["explicit_max_ups_source"], "engine_default.max_slitting_ups")
        self.assertEqual(entry["iteration_stop_reason"], "explicit_max_ups_reached")
        self.assertEqual(entry["iteration_limit"], engine_v2_runtime.SLITTING_OPTION_DEFAULT_MAX_UPS)
        self.assertEqual(len(entry["options"]), engine_v2_runtime.SLITTING_OPTION_DEFAULT_MAX_UPS)
        self.assertTrue(all(item["feasible"] for item in entry["options"]))
        self.assertFalse(entry["candidate_workcenters"][0]["constraint_status"]["length_constraints_defined"])
        self.assertEqual(entry["preferred_option"]["up_count"], engine_v2_runtime.SLITTING_OPTION_DEFAULT_MAX_UPS)

    def test_slitting_options_workcenter_zero_max_uses_global_limit(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0
        env = _FakeEnv(config_params={"corr_engine_v2.max_slitting_ups": "3"})

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 39,
                    "workcenter_name": "WC Zero Max Uses Global",
                    "width_min_cm": 0.0,
                    "width_max_cm": 500.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 5000.0,
                    "max_slitting_ups": 0,
                    "constraint_source": "workcenter",
                    "supported_flutes": ["B"],
                }
            ],
        ), patch.object(engine_v2_runtime, "_required_corrugation_flutes_from_spec", return_value=["B"]):
            report = engine_v2_runtime._build_slitting_options(
                env=env,
                stages=[stage],
                stage_contexts=[context],
                spec=SimpleNamespace(),
                v2_spec=None,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        self.assertEqual(entry["explicit_max_ups"], 3)
        self.assertEqual(entry["explicit_max_ups_source"], "corr_engine_v2.max_slitting_ups")
        self.assertEqual([item["up_count"] for item in entry["options"]], [1, 2, 3])
        self.assertEqual(entry["preferred_option"]["up_count"], 3)

    def test_diecut_sheet_full_blank_uses_physical_sheet_not_fg_per_sheet_as_slitting_ups(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 800.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 1000.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 800.0
        engine_result.metrics.extra["layout"]["sheet_source"] = "diecut_sheet_manual_size"
        spec = SimpleNamespace(
            style_code="DIECUT_SHEET",
            fg_per_sheet=2,
            style_id=SimpleNamespace(code="DIECUT_SHEET", family="diecut_sheet"),
        )
        env = _FakeEnv(config_params={"corr_engine_v2.max_slitting_ups": "3"})

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 39,
                    "workcenter_name": "DEFAULT Corrugator",
                    "width_min_cm": 10.0,
                    "width_max_cm": 160.0,
                    "length_min_mm": 50.0,
                    "length_max_mm": 100000000.0,
                    "max_slitting_ups": 0,
                    "constraint_source": "workcenter",
                    "supported_flutes": ["B"],
                }
            ],
        ), patch.object(engine_v2_runtime, "_required_corrugation_flutes_from_spec", return_value=["B"]):
            report = engine_v2_runtime._build_slitting_options(
                env=env,
                stages=[stage],
                stage_contexts=[context],
                spec=spec,
                v2_spec=spec,
                engine_result=engine_result,
                full_blank_only=True,
            )

        entry = report["stages"][0]
        self.assertTrue(entry["full_blank_manufacturing_sheet_one_up"])
        self.assertTrue(entry["diecut_sheet_full_blank_one_up"])
        self.assertEqual(entry["blank_basis_used"], "manufacturing_sheet")
        self.assertEqual(entry["iteration_stop_reason"], "full_blank_manufacturing_sheet_checked")
        self.assertEqual([item["up_count"] for item in entry["options"]], [1])
        self.assertEqual(entry["options"][0]["candidate_length_mm"], 1000.0)
        self.assertEqual(entry["options"][0]["candidate_width_mm"], 800.0)
        self.assertTrue(entry["options"][0]["feasible"])
        self.assertEqual(entry["preferred_option"]["up_count"], 1)
        self.assertEqual(report["summary"]["feasible_option_count"], 1)

    def test_full_blank_all_styles_use_resolved_manufacturing_sheet_as_one_up(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 3020.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 1120.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 1000.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 800.0
        spec = SimpleNamespace(
            style_code="0201",
            style_id=SimpleNamespace(code="0201", family="box"),
        )

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 42,
                    "workcenter_name": "DEFAULT Corrugator",
                    "width_min_cm": 10.0,
                    "width_max_cm": 160.0,
                    "length_min_mm": 50.0,
                    "length_max_mm": 100000000.0,
                    "max_slitting_ups": 0,
                    "constraint_source": "workcenter",
                    "supported_flutes": ["B"],
                }
            ],
        ), patch.object(engine_v2_runtime, "_required_corrugation_flutes_from_spec", return_value=["B"]):
            report = engine_v2_runtime._build_slitting_options(
                env=_FakeEnv(config_params={"corr_engine_v2.max_slitting_ups": "3"}),
                stages=[stage],
                stage_contexts=[context],
                spec=spec,
                v2_spec=spec,
                engine_result=engine_result,
                full_blank_only=True,
            )

        entry = report["stages"][0]
        self.assertTrue(entry["full_blank_manufacturing_sheet_one_up"])
        self.assertFalse(entry["diecut_sheet_full_blank_one_up"])
        self.assertEqual(entry["blank_basis_used"], "manufacturing_sheet")
        self.assertEqual(entry["iteration_stop_reason"], "full_blank_manufacturing_sheet_checked")
        self.assertEqual([item["up_count"] for item in entry["options"]], [1])
        self.assertEqual(entry["options"][0]["candidate_length_mm"], 1000.0)
        self.assertEqual(entry["options"][0]["candidate_width_mm"], 800.0)
        self.assertTrue(entry["options"][0]["feasible"])

    def test_slitting_options_ignores_incompatible_workcenter_max_before_ranking(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0
        env = _FakeEnv(config_params={"corr_engine_v2.max_slitting_ups": "3"})

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 40,
                    "workcenter_name": "B Corrugator",
                    "width_min_cm": 0.0,
                    "width_max_cm": 500.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 5000.0,
                    "max_slitting_ups": 0,
                    "constraint_source": "workcenter",
                    "supported_flutes": ["B"],
                },
                {
                    "workcenter_id": 41,
                    "workcenter_name": "F Corrugator Huge Max",
                    "width_min_cm": 0.0,
                    "width_max_cm": 5000.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 50000.0,
                    "max_slitting_ups": 50,
                    "constraint_source": "workcenter",
                    "supported_flutes": ["F"],
                },
            ],
        ), patch.object(engine_v2_runtime, "_required_corrugation_flutes_from_spec", return_value=["B"]):
            report = engine_v2_runtime._build_slitting_options(
                env=env,
                stages=[stage],
                stage_contexts=[context],
                spec=SimpleNamespace(),
                v2_spec=None,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        self.assertEqual(entry["explicit_max_ups"], 3)
        self.assertEqual([item["up_count"] for item in entry["feasible_options"]], [3, 2, 1])
        self.assertEqual(entry["preferred_option"]["up_count"], 3)
        self.assertEqual((entry["preferred_option"]["matched_workcenter"] or {}).get("workcenter_id"), 40)

    def test_slitting_options_machine_with_missing_length_config(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 34,
                    "workcenter_name": "WC Missing Length",
                    "width_min_cm": 0.0,
                    "width_max_cm": 75.0,
                    "length_min_mm": None,
                    "length_max_mm": None,
                    "constraint_source": "workcenter",
                    "constraints_applied": {
                        "width_min_cm": 0.0,
                        "width_max_cm": 75.0,
                        "length_min_mm": None,
                        "length_max_mm": None,
                    },
                    "constraint_status": {
                        "width_constraints_defined": True,
                        "length_constraints_defined": False,
                    },
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        self.assertEqual([item["feasible"] for item in entry["options"]], [True, False])
        self.assertFalse(entry["candidate_workcenters"][0]["constraint_status"]["length_constraints_defined"])
        self.assertEqual(entry["iteration_stop_reason"], "all_candidate_workcenters_rejected")
        self.assertEqual([item["up_count"] for item in entry["feasible_options"]], [1])
        self.assertEqual([item["up_count"] for item in entry["rejected_options"]], [2])

    def test_slitting_options_report_length_constraints_as_first_class(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 25,
                    "workcenter_name": "WC Length Limited",
                    "width_min_cm": 0.0,
                    "width_max_cm": 200.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 900.0,
                    "constraint_source": "workcenter",
                    "constraints_applied": {
                        "width_min_cm": 0.0,
                        "width_max_cm": 200.0,
                        "length_min_mm": 0.0,
                        "length_max_mm": 900.0,
                    },
                    "constraint_status": {
                        "width_constraints_defined": True,
                        "length_constraints_defined": True,
                    },
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        entry = report["stages"][0]
        option = entry["options"][0]
        self.assertTrue(option["feasible"])
        self.assertEqual(option["orientation_used"], "rotated")
        self.assertTrue(
            any(
                "length 1000.0 mm exceeds workcenter length limit 900.0 mm"
                in reason
                for reason in option["candidate_results"][0]["dimension_checks"][0]["rejection_reasons"]
            )
        )
        self.assertTrue(option["candidate_results"][0]["resolved_constraints"]["length_check_applied"])

    def test_slitting_options_presentation_shows_ui_facing_fields(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 43,
                    "workcenter_name": "WC UI",
                    "width_min_cm": 0.0,
                    "width_max_cm": 90.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 2000.0,
                    "constraint_source": "workcenter",
                    "constraints_applied": {
                        "width_min_cm": 0.0,
                        "width_max_cm": 90.0,
                        "length_min_mm": 0.0,
                        "length_max_mm": 2000.0,
                    },
                    "constraint_status": {
                        "width_constraints_defined": True,
                        "length_constraints_defined": True,
                    },
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        preferred = report["stages"][0]["preferred_option"]
        self.assertEqual(
            set(preferred.keys()),
            {
                "rank",
                "up_count",
                "final_sheet_length_mm",
                "final_sheet_width_mm",
                "matched_workcenter",
                "orientation_used",
                "rotated_used",
                "feasible",
                "rejection_reasons",
                "preference_model",
            },
        )
        self.assertEqual(preferred["up_count"], 2)
        self.assertEqual(preferred["final_sheet_length_mm"], 1000.0)
        self.assertEqual(preferred["final_sheet_width_mm"], 800.0)
        self.assertEqual(preferred["matched_workcenter"]["workcenter_id"], 43)
        self.assertEqual(preferred["rank"], 1)

    def test_slitting_options_blank_rejected_by_length(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 35,
                    "workcenter_name": "WC Reject Length",
                    "width_min_cm": 0.0,
                    "width_max_cm": 200.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 300.0,
                    "constraint_source": "workcenter",
                    "constraints_applied": {
                        "width_min_cm": 0.0,
                        "width_max_cm": 200.0,
                        "length_min_mm": 0.0,
                        "length_max_mm": 300.0,
                    },
                    "constraint_status": {
                        "width_constraints_defined": True,
                        "length_constraints_defined": True,
                    },
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        option = report["stages"][0]["options"][0]
        self.assertFalse(option["feasible"])
        self.assertTrue(any("length" in reason for reason in option["rejection_reasons"]))
        self.assertEqual(report["stages"][0]["iteration_stop_reason"], "all_candidate_workcenters_rejected")
        self.assertIsNone(report["stages"][0]["preferred_option"])
        self.assertEqual(report["stages"][0]["feasible_options"], [])

    def test_slitting_options_blank_rejected_by_width(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 36,
                    "workcenter_name": "WC Reject Width",
                    "width_min_cm": 0.0,
                    "width_max_cm": 30.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 5000.0,
                    "constraint_source": "workcenter",
                    "constraints_applied": {
                        "width_min_cm": 0.0,
                        "width_max_cm": 30.0,
                        "length_min_mm": 0.0,
                        "length_max_mm": 5000.0,
                    },
                    "constraint_status": {
                        "width_constraints_defined": True,
                        "length_constraints_defined": True,
                    },
                }
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        option = report["stages"][0]["options"][0]
        self.assertFalse(option["feasible"])
        self.assertTrue(any("width" in reason for reason in option["rejection_reasons"]))
        self.assertEqual(report["stages"][0]["iteration_stop_reason"], "all_candidate_workcenters_rejected")
        self.assertEqual([item["up_count"] for item in report["stages"][0]["rejected_options"]], [1])

    def test_slitting_options_multiple_workcenters_with_different_capacities(self):
        stage = self._make_stage()
        context = self._make_context()
        engine_result = self._make_engine_result()
        engine_result.metrics.extra["geometry"]["blank_length_mm"] = 1000.0
        engine_result.metrics.extra["geometry"]["blank_width_mm"] = 400.0
        engine_result.metrics.extra["layout"]["sheet_width_mm"] = 0.0
        engine_result.metrics.extra["layout"]["sheet_length_mm"] = 0.0

        with patch.object(
            engine_v2_runtime,
            "_collect_stage_option_workcenters",
            return_value=[
                {
                    "workcenter_id": 37,
                    "workcenter_name": "WC Narrow",
                    "width_min_cm": 0.0,
                    "width_max_cm": 60.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 2000.0,
                    "constraint_source": "workcenter",
                    "constraints_applied": {
                        "width_min_cm": 0.0,
                        "width_max_cm": 60.0,
                        "length_min_mm": 0.0,
                        "length_max_mm": 2000.0,
                    },
                    "constraint_status": {
                        "width_constraints_defined": True,
                        "length_constraints_defined": True,
                    },
                },
                {
                    "workcenter_id": 38,
                    "workcenter_name": "WC Wide",
                    "width_min_cm": 0.0,
                    "width_max_cm": 90.0,
                    "length_min_mm": 0.0,
                    "length_max_mm": 2000.0,
                    "constraint_source": "workcenter",
                    "constraints_applied": {
                        "width_min_cm": 0.0,
                        "width_max_cm": 90.0,
                        "length_min_mm": 0.0,
                        "length_max_mm": 2000.0,
                    },
                    "constraint_status": {
                        "width_constraints_defined": True,
                        "length_constraints_defined": True,
                    },
                },
            ],
        ):
            report = engine_v2_runtime._build_slitting_options(
                env=object(),
                stages=[stage],
                stage_contexts=[context],
                spec=None,
                v2_spec=None,
                engine_result=engine_result,
            )

        options = report["stages"][0]["options"]
        self.assertEqual([item["up_count"] for item in options], [1, 2, 3])
        self.assertTrue(options[0]["feasible"])
        self.assertTrue(options[1]["feasible"])
        self.assertEqual(options[1]["matched_workcenter"]["workcenter_id"], 38)
        self.assertFalse(options[2]["feasible"])
        feasible_options = report["stages"][0]["feasible_options"]
        self.assertEqual([item["up_count"] for item in feasible_options], [2, 1])
        self.assertEqual(feasible_options[0]["matched_workcenter"]["workcenter_id"], 38)

    def test_slitting_options_summary_publishes_preference_model(self):
        presentation = engine_v2_runtime._build_slitting_option_presentation(
            [
                {
                    "up_count": 1,
                    "candidate_length_mm": 1000.0,
                    "candidate_width_mm": 400.0,
                    "orientation_used": "direct",
                    "rotated_used": False,
                    "matched_workcenter": {"workcenter_id": 44, "workcenter_name": "WC A"},
                    "feasible": True,
                    "rejection_reasons": [],
                }
            ]
        )
        self.assertEqual(presentation["preference_model"], "higher_up_then_direct_then_smaller_sheet")
        self.assertEqual(presentation["preferred_option"]["rank"], 1)

    def test_slitting_option_candidate_rejects_incompatible_flute(self):
        result = engine_v2_runtime._evaluate_slitting_option_candidate(
            {
                "workcenter_id": 45,
                "workcenter_name": "F-only Corrugator",
                "width_min_cm": 1.0,
                "width_max_cm": 500.0,
                "length_max_mm": 5000.0,
                "required_flutes": ["B"],
                "supported_flutes": ["F"],
            },
            up_count=1,
            candidate_length_mm=1000.0,
            candidate_width_mm=1000.0,
            allow_rotation=True,
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("required flute" in reason for reason in result["rejection_reasons"]))
        self.assertEqual((result["resolved_constraints"] or {}).get("required_flutes"), ["B"])
        self.assertEqual((result["resolved_constraints"] or {}).get("supported_flutes"), ["F"])

    def test_slitting_option_candidate_accepts_matching_flute(self):
        result = engine_v2_runtime._evaluate_slitting_option_candidate(
            {
                "workcenter_id": 46,
                "workcenter_name": "B Corrugator",
                "width_min_cm": 1.0,
                "width_max_cm": 500.0,
                "length_max_mm": 5000.0,
                "required_flutes": ["B"],
                "supported_flutes": ["B", "C"],
            },
            up_count=1,
            candidate_length_mm=1000.0,
            candidate_width_mm=1000.0,
            allow_rotation=True,
        )

        self.assertEqual(result["status"], "feasible")
        self.assertEqual(result["rejection_reasons"], [])

    def test_required_corrugation_flutes_reads_engine_v2_flute_type(self):
        spec = SimpleNamespace(
            stack_line_ids=[
                SimpleNamespace(
                    role="medium",
                    flute_type_id=SimpleNamespace(code="B"),
                )
            ]
        )

        self.assertEqual(engine_v2_runtime._required_corrugation_flutes_from_spec(spec), ["B"])

    def test_build_shadow_runtime_publishes_stage_feasibility(self):
        routing = SimpleNamespace(
            stages=[self._make_stage()],
            selection_source="routing_profile",
            selection_details={"route_id": 5},
        )
        stage_contexts = [self._make_context()]
        stage_feasibility = {"stages": [{"stage_code": "2ply", "status": "feasible"}], "summary": {"stage_count": 1}}
        slitting_options = {"stages": [{"stage_code": "2ply", "status": "feasible"}], "summary": {"stage_count": 1}}

        with patch.object(engine_v2_runtime, "_build_routing_instance", return_value=routing), patch.object(
            engine_v2_runtime, "_build_material_requirements", return_value=[]
        ), patch.object(engine_v2_runtime, "_build_stage_cost_lines", return_value=[]), patch.object(
            engine_v2_runtime, "_build_stage_contexts", return_value=stage_contexts
        ), patch.object(
            engine_v2_runtime, "_attach_materials_to_stage_contexts", return_value=None
        ), patch.object(
            engine_v2_runtime, "_run_quantity_planner", return_value={"ok": True}
        ), patch.object(
            engine_v2_runtime, "_build_stage_feasibility", return_value=stage_feasibility
        ), patch.object(
            engine_v2_runtime, "_build_slitting_options", return_value=slitting_options
        ), patch.object(
            engine_v2_runtime, "_serialize_routing_instance", return_value={"stages": [{"stage_code": "2ply"}]}
        ):
            payload = engine_v2_runtime.build_shadow_runtime(
                env=object(),
                spec=object(),
                plant_param=object(),
                qgraph=object(),
                order_qty=100.0,
                v2_spec=None,
                engine_result=object(),
            )

        self.assertEqual(payload["stage_feasibility"], stage_feasibility)
        self.assertEqual(payload["slitting_options"], slitting_options)
        self.assertEqual(payload["quantity_report"], {"ok": True})

    def test_build_shadow_runtime_keeps_slitting_options_for_inherited_multi_piece_spec(self):
        routing = SimpleNamespace(
            stages=[self._make_stage()],
            selection_source="routing_profile",
            selection_details={"route_id": 5},
        )
        stage_contexts = [self._make_context()]
        slitting_options = {
            "layout_driving_stage": {"stage_code": "2ply"},
            "stages": [
                {
                    "stage_code": "2ply",
                    "status": "feasible",
                    "feasible_options": [
                        {
                            "rank": 1,
                            "up_count": 1,
                            "final_sheet_length_mm": 3020.0,
                            "final_sheet_width_mm": 1120.0,
                            "orientation_used": "direct",
                        }
                    ],
                }
            ],
            "summary": {"stage_count": 1, "feasible_option_count": 1},
        }
        spec = SimpleNamespace(
            multi_piece_enabled=True,
            multi_piece_strategy="independent_routed_piece",
            multi_piece_line_ids=[
                SimpleNamespace(
                    stack_source="inherited",
                    surface_source="inherited",
                    top_sheet_source="inherited",
                )
            ],
        )

        with patch.object(engine_v2_runtime, "_build_routing_instance", return_value=routing), patch.object(
            engine_v2_runtime, "_build_material_requirements", return_value=[]
        ), patch.object(engine_v2_runtime, "_build_stage_cost_lines", return_value=[]), patch.object(
            engine_v2_runtime, "_build_stage_contexts", return_value=stage_contexts
        ), patch.object(
            engine_v2_runtime, "_attach_materials_to_stage_contexts", return_value=None
        ), patch.object(
            engine_v2_runtime, "_run_quantity_planner", return_value={"ok": True}
        ), patch.object(
            engine_v2_runtime, "_build_stage_feasibility", return_value={"stages": []}
        ), patch.object(
            engine_v2_runtime, "_build_slitting_options", return_value=slitting_options
        ), patch.object(
            engine_v2_runtime, "_serialize_routing_instance", return_value={"stages": [{"stage_code": "2ply"}]}
        ):
            payload = engine_v2_runtime.build_shadow_runtime(
                env=object(),
                spec=spec,
                plant_param=object(),
                qgraph=object(),
                order_qty=100.0,
                v2_spec=None,
                engine_result=object(),
            )

        self.assertEqual(payload["slitting_options"], slitting_options)
        self.assertEqual(payload["slitting_options"]["summary"]["feasible_option_count"], 1)




