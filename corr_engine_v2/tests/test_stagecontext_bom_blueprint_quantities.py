import importlib
import sys
import types
import unittest


def _install_fake_odoo():
    if "odoo" in sys.modules and "odoo.exceptions" in sys.modules:
        return

    odoo_mod = types.ModuleType("odoo")
    odoo_mod.__dict__["_"] = lambda text, *args, **kwargs: text
    odoo_mod.api = object()
    odoo_mod.fields = object()
    odoo_mod.models = object()

    exceptions_mod = types.ModuleType("odoo.exceptions")

    class UserError(Exception):
        pass

    exceptions_mod.UserError = UserError
    odoo_mod.exceptions = exceptions_mod

    sys.modules["odoo"] = odoo_mod
    sys.modules["odoo.exceptions"] = exceptions_mod


_install_fake_odoo()
bom_adapter = importlib.import_module("custom_addons.corr_mrp_bridge.bom_adapter")
manufacturing_plan = importlib.import_module(
    "custom_addons.corr_engine_v2.engine_v2.manufacturing_plan"
)


class _FakeRuleModel:
    def sudo(self):
        return self

    def search(self, *args, **kwargs):
        return []


class _FakeRecord:
    def __init__(self, **values):
        self.__dict__.update(values)

    def exists(self):
        return self if getattr(self, "id", None) else False

    def __bool__(self):
        return bool(getattr(self, "id", None))


class _FakeProductTemplateModel:
    def __init__(self, records):
        self.records = dict(records or {})

    def sudo(self):
        return self

    def browse(self, record_id):
        return self.records.get(record_id) or _FakeRecord()


class _FakeEnv(dict):
    def __init__(self, **models):
        super().__init__(models)

    def __getitem__(self, key):
        if key == "corr.engine.bom.translation.rule":
            return _FakeRuleModel()
        if key in self:
            return dict.__getitem__(self, key)
        raise KeyError(key)


class TestStageContextBomBlueprintQuantities(unittest.TestCase):
    def test_top_sheet_mode_detection_is_metadata_aware(self):
        self.assertEqual(
            bom_adapter._top_sheet_mode_for_component(
                "rm_top_sheet_external",
                {"kind": "top_roll"},
            ),
            "roll",
        )
        self.assertEqual(
            bom_adapter._top_sheet_mode_for_component(
                "rm_top_sheet_external",
                {"kind": "top_sheet_external", "top_sheet_mode": "unit_sheet"},
            ),
            "unit_sheet",
        )

    def test_top_sheet_roll_resolution_entry_prefers_paper_resolution(self):
        entry = bom_adapter._top_sheet_roll_resolution_entry(
            {"kind": "top_roll", "layer_no": 3, "role": "liner"},
            {
                ("liner", 3): {
                    "status": "exact",
                    "product_id": 42,
                    "uom_id": 7,
                }
            },
        )
        self.assertEqual(entry["product_id"], 42)
        self.assertEqual(entry["uom_id"], 7)

    def test_top_sheet_roll_resolution_entry_falls_back_to_metadata_resolution(self):
        entry = bom_adapter._top_sheet_roll_resolution_entry(
            {
                "kind": "top_roll",
                "layer_no": 3,
                "role": "liner",
                "resolution": {
                    "status": "usable",
                    "product_id": 77,
                    "uom_id": 9,
                },
            },
            {},
        )
        self.assertEqual(entry["product_id"], 77)
        self.assertEqual(entry["uom_id"], 9)

    def test_paper_resolution_map_ignores_trace_rows(self):
        payload = {
            "paper_resolution": [
                {
                    "role": "medium",
                    "layer_no": 2,
                    "status": "trace",
                    "debug": {"trace_action": "paper_recovery_skipped_existing_key"},
                },
                {
                    "role": "medium",
                    "layer_no": 2,
                    "status": "exact",
                    "product_id": 42,
                },
                {
                    "role": "liner",
                    "layer_no": 3,
                    "status": "exact",
                    "product_id": 43,
                },
            ]
        }

        result = bom_adapter._normalize_paper_resolution_map(payload)

        self.assertEqual(result[("medium", 2)]["product_id"], 42)
        self.assertEqual(result[("liner", 3)]["product_id"], 43)
        self.assertNotEqual(result[("medium", 2)]["status"], "trace")

    def test_preferred_bom_operation_rows_use_routed_operations_when_available(self):
        bom_struct = {
            "operations": [
                {"stage": "corrugator", "input_qty": 99.04608, "output_qty": 17.0},
                {"stage": "conversion", "input_qty": 17.0, "output_qty": 100.0},
            ],
            "routed_operations": [
                {
                    "stage": "corrugator",
                    "operation_key": "corrugator",
                    "stage_code": "2ply",
                    "stage_contract_code": "v2_compat.stage.2ply",
                    "stage_template_id": 7,
                    "stage_name": "2-Ply Corrugation",
                    "input_qty": 0.0,
                    "output_qty": 17.0,
                    "sequence": 10,
                },
                {
                    "stage": "corrugator",
                    "operation_key": "corrugator",
                    "stage_code": "board",
                    "stage_contract_code": "v2_compat.stage.board",
                    "stage_template_id": 8,
                    "stage_name": "Board Formation",
                    "input_qty": 17.0,
                    "output_qty": 17.0,
                    "sequence": 20,
                },
                {
                    "stage": "conversion",
                    "operation_key": "conversion",
                    "stage_code": "seaming",
                    "stage_contract_code": "v2_compat.stage.seaming",
                    "stage_template_id": 13,
                    "stage_name": "Seaming / Jointing",
                    "input_qty": 17.0,
                    "output_qty": 100.0,
                    "sequence": 30,
                },
            ],
        }

        rows, source = bom_adapter._preferred_bom_operation_rows(bom_struct)

        self.assertEqual(source, "routed_operations")
        self.assertEqual([item["stage_code"] for item in rows], ["2ply", "board", "seaming"])
        self.assertEqual(
            [item["operation_name"] for item in rows],
            ["2-Ply Corrugation", "Board Formation", "Seaming / Jointing"],
        )
        self.assertEqual(
            [item["operation_family_name"] for item in rows],
            ["Corrugation", "Corrugation", "Conversion"],
        )
        self.assertEqual(rows[0]["input_qty"], 0.0)
        self.assertEqual(rows[0]["output_qty"], 17.0)

    def test_board_formation_lamination_stage_uses_lamination_family(self):
        bom_struct = {
            "routed_operations": [
                {
                    "stage": "corrugator",
                    "operation_key": "corrugator",
                    "stage_code": "2ply",
                    "stage_name": "2-Ply Corrugation",
                    "stage_type": "corrugation",
                    "capability_code": "corrugation",
                    "output_kind": "board",
                    "output_qty": 1.0,
                    "sequence": 10,
                },
                {
                    "stage": "corrugator",
                    "operation_key": "corrugator",
                    "stage_code": "board",
                    "stage_name": "Board Formation",
                    "stage_type": "lamination",
                    "capability_code": "Pasting",
                    "output_kind": "board",
                    "output_qty": 1.0,
                    "sequence": 20,
                },
            ]
        }

        rows, source = bom_adapter._preferred_bom_operation_rows(bom_struct)

        self.assertEqual(source, "routed_operations")
        self.assertEqual([row["operation_name"] for row in rows], ["2-Ply Corrugation", "Board Formation"])
        self.assertEqual([row["operation_family_name"] for row in rows], ["Corrugation", "Lamination"])

    def test_preferred_bom_operation_rows_fall_back_to_grouped_operations(self):
        bom_struct = {
            "operations": [
                {"stage": "corrugator", "input_qty": 99.04608, "output_qty": 17.0},
                {"stage": "conversion", "input_qty": 17.0, "output_qty": 100.0},
            ]
        }

        rows, source = bom_adapter._preferred_bom_operation_rows(bom_struct)

        self.assertEqual(source, "operations")
        self.assertEqual([item["operation_name"] for item in rows], ["Corrugation", "Conversion"])
        self.assertEqual([item["operation_family_name"] for item in rows], ["Corrugation", "Conversion"])
        self.assertEqual([item["sequence"] for item in rows], [10, 20])

    def test_operation_display_name_from_row_prefers_stage_name(self):
        operation_row = {
            "stage_name": "Board Formation",
            "stage_template_name": "Ignored Template Name",
            "name": "Ignored Name",
        }

        display_name = bom_adapter._operation_display_name_from_row(
            operation_row,
            fallback_name="Corrugation",
        )

        self.assertEqual(display_name, "Board Formation")

    def test_stagecontext_signature_prefers_writer_payload_when_routed_operations_exist(self):
        writer_payload = {
            "operations": [{"stage": "corrugator", "input_qty": 99.04608, "output_qty": 17.0}],
            "routed_operations": [
                {
                    "stage": "corrugator",
                    "operation_key": "corrugator",
                    "stage_code": "2ply",
                    "input_qty": 0.0,
                    "output_qty": 17.0,
                    "sequence": 10,
                }
            ],
        }
        legacy_struct = {
            "operations": [{"stage": "corrugator", "input_qty": 99.04608, "output_qty": 17.0}],
        }

        preferred, source = bom_adapter._preferred_stagecontext_signature_struct(writer_payload, legacy_struct)

        self.assertEqual(source, "stagecontext_writer_payload")
        self.assertIn("routed_operations", preferred)

    def test_signature_normalization_uses_routed_operation_identity(self):
        left = {
            "components": [],
            "operations": [{"stage": "corrugator", "input_qty": 99.04608, "output_qty": 17.0}],
            "routed_operations": [
                {
                    "stage": "corrugator",
                    "operation_key": "corrugator",
                    "stage_code": "2ply",
                    "stage_contract_code": "v2_compat.stage.2ply",
                    "stage_template_id": 7,
                    "input_qty": 0.0,
                    "output_qty": 17.0,
                    "sequence": 10,
                },
                {
                    "stage": "corrugator",
                    "operation_key": "corrugator",
                    "stage_code": "board",
                    "stage_contract_code": "v2_compat.stage.board",
                    "stage_template_id": 8,
                    "input_qty": 17.0,
                    "output_qty": 17.0,
                    "sequence": 20,
                },
            ],
        }
        right = {
            "components": [],
            "operations": [{"stage": "corrugator", "input_qty": 99.04608, "output_qty": 17.0}],
            "routed_operations": [
                {
                    "stage": "corrugator",
                    "operation_key": "corrugator",
                    "stage_code": "board",
                    "stage_contract_code": "v2_compat.stage.board",
                    "stage_template_id": 8,
                    "input_qty": 17.0,
                    "output_qty": 17.0,
                    "sequence": 20,
                }
            ],
        }

        left_norm = bom_adapter._normalise_bom_struct_for_signature(left, 100.0)
        right_norm = bom_adapter._normalise_bom_struct_for_signature(right, 100.0)

        self.assertEqual(left_norm["operation_source"], "routed_operations")
        self.assertEqual(right_norm["operation_source"], "routed_operations")
        self.assertNotEqual(left_norm["operations"], right_norm["operations"])

    def test_component_operation_binding_prefers_metadata_identity(self):
        component = {
            "stage": "finished_goods",
            "metadata": {
                "stage_code": "seaming",
                "stage_contract_code": "v2_compat.stage.seaming",
                "stage_template_id": 13,
                "operation_key": "conversion",
                "operation_name": "Conversion",
            },
        }

        binding = bom_adapter._component_operation_binding(component)

        self.assertEqual(binding["operation_name"], "Conversion")
        self.assertEqual(binding["stage_code"], "seaming")
        self.assertEqual(binding["stage_contract_code"], "v2_compat.stage.seaming")
        self.assertEqual(binding["stage_template_id"], 13)

    def test_component_operation_binding_falls_back_to_stage_label(self):
        component = {
            "stage": "corrugator",
            "metadata": {},
        }

        binding = bom_adapter._component_operation_binding(component)

        self.assertEqual(binding["operation_name"], "Corrugation")
        self.assertIsNone(binding["stage_code"])
        self.assertIsNone(binding["stage_contract_code"])
        self.assertIsNone(binding["stage_template_id"])

    def test_lookup_operation_id_for_component_prefers_stage_identity_then_name(self):
        operation_by_identity = {
            ("contract", "v2_compat.stage.seaming"): 42,
        }
        operation_by_name = {
            "Conversion": 99,
        }

        component_with_identity = {
            "stage": "finished_goods",
            "metadata": {
                "stage_contract_code": "v2_compat.stage.seaming",
                "operation_name": "Conversion",
            },
        }
        component_name_only = {
            "stage": "corrugator",
            "metadata": {},
        }

        self.assertEqual(
            bom_adapter._lookup_operation_id_for_component(
                component_with_identity,
                operation_by_identity,
                operation_by_name,
            ),
            42,
        )
        self.assertEqual(
            bom_adapter._lookup_operation_id_for_component(
                component_name_only,
                {},
                {"Corrugation": 11},
            ),
            11,
        )

    def test_component_operation_binding_supports_writer_helper_fields(self):
        component = {
            "component_stage": "corrugator",
            "component_operation_name": "",
            "component_stage_contract_code": "v2_compat.stage.board",
            "component_stage_template_id": 8,
            "component_stage_code": "board",
            "metadata": {},
        }

        binding = bom_adapter._component_operation_binding(component)

        self.assertEqual(binding["operation_name"], "Corrugation")
        self.assertEqual(binding["stage_contract_code"], "v2_compat.stage.board")
        self.assertEqual(binding["stage_template_id"], 8)
        self.assertEqual(binding["stage_code"], "board")

    def test_draft_line_component_key_uses_stage_role_layer_and_product(self):
        key = bom_adapter._draft_line_component_key(
            {
                "component_stage_code": "board",
                "corr_role": "liner",
                "corr_layer_no": 3,
                "product_id": 77,
            }
        )
        self.assertEqual(key, "engine_v2:board:liner:3:77")

    def test_fallback_component_stage_entry_rebinds_paper_rows_to_board_when_2ply_missing(self):
        stages = [
            {
                "stage_template_id": 8,
                "stage_contract_code": "v2_compat.stage.board",
                "stage_code": "board",
                "stage_name": "Board Formation",
                "stage_type": "lamination",
                "capability_code": "lamination",
                "execution_type": "material",
                "output_kind": "board",
                "output_stock_behavior": "semi_finished",
            },
            {
                "stage_template_id": 13,
                "stage_contract_code": "v2_compat.stage.seaming",
                "stage_code": "seaming",
                "stage_name": "Seaming / Jointing",
                "stage_type": "conversion",
                "capability_code": "conversion",
                "execution_type": "material",
                "output_kind": "box",
                "output_stock_behavior": "fg",
            },
        ]

        liner_stage = bom_adapter._fallback_component_stage_entry(
            {"semantic_role": "liner", "stage_code": "2ply"},
            {},
            stages,
        )
        medium_stage = bom_adapter._fallback_component_stage_entry(
            {"semantic_role": "medium", "stage_code": "2ply"},
            {},
            stages,
        )
        top_stage = bom_adapter._fallback_component_stage_entry(
            {"semantic_role": "top_sheet_external", "stage_code": "2ply"},
            {},
            stages,
        )

        self.assertEqual(liner_stage["stage_code"], "board")
        self.assertEqual(medium_stage["stage_code"], "board")
        self.assertEqual(top_stage["stage_code"], "board")

    def test_fallback_component_stage_entry_rebinds_paper_rows_to_only_material_stage(self):
        stages = [
            {
                "stage_template_id": 13,
                "stage_contract_code": "v2_compat.stage.seaming",
                "stage_code": "seaming",
                "stage_name": "Seaming / Jointing",
                "stage_type": "conversion",
                "capability_code": "conversion",
                "execution_type": "material",
                "output_kind": "box",
                "output_stock_behavior": "fg",
            },
        ]

        liner_stage = bom_adapter._fallback_component_stage_entry(
            {"semantic_role": "liner", "stage_code": "2ply"},
            {},
            stages,
        )
        medium_stage = bom_adapter._fallback_component_stage_entry(
            {"semantic_role": "medium", "stage_code": "2ply"},
            {},
            stages,
        )
        top_stage = bom_adapter._fallback_component_stage_entry(
            {"semantic_role": "top_sheet_external", "stage_code": "board"},
            {},
            stages,
        )

        self.assertEqual(liner_stage["stage_code"], "seaming")
        self.assertEqual(medium_stage["stage_code"], "seaming")
        self.assertEqual(top_stage["stage_code"], "seaming")

    def test_stagecontext_blueprint_rebinds_paper_components_to_board_when_2ply_missing(self):
        payload = {
            "plant_id": 1,
            "order_qty": 100.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "index": 0,
                        "sequence": 10,
                        "stage_template_id": 8,
                        "stage_contract_code": "v2_compat.stage.board",
                        "stage_code": "board",
                        "stage_name": "Board Formation",
                        "stage_type": "lamination",
                        "capability_code": "lamination",
                        "execution_type": "material",
                        "input_qty_from_prev": 17.0,
                        "target_output_qty": 17.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "semi_finished",
                        "output_descriptor": {"kind": "board", "uom": "sheet"},
                    },
                    {
                        "index": 1,
                        "sequence": 20,
                        "stage_template_id": 13,
                        "stage_contract_code": "v2_compat.stage.seaming",
                        "stage_code": "seaming",
                        "stage_name": "Seaming / Jointing",
                        "stage_type": "conversion",
                        "capability_code": "conversion",
                        "execution_type": "material",
                        "input_qty_from_prev": 17.0,
                        "target_output_qty": 100.0,
                        "target_output_uom": "unit",
                        "output_stock_behavior": "fg",
                        "output_descriptor": {"kind": "box", "uom": "unit"},
                    },
                ]
            },
            "phase5_preview_model": {
                "stages": [],
                "materials": [
                    {
                        "stage_code": "2ply",
                        "stage_contract_code": "v2_compat.stage.2ply",
                        "stage_template_id": 7,
                        "stage_name": "2-Ply Corrugation",
                        "semantic_role": "liner",
                        "legacy_role_aliases": ["liner"],
                        "material_type": "rm",
                        "product_tmpl_id": 10,
                        "product": "Liner",
                        "qty_per_fg": 0.34,
                        "total_qty": 34.0,
                        "uom": "kg",
                        "quantity_basis": "per_m2",
                        "quantity_source": "legacy_rm_requirement",
                        "metadata": {"layer_no": 1, "consumption_type": "area"},
                    },
                    {
                        "stage_code": "2ply",
                        "stage_contract_code": "v2_compat.stage.2ply",
                        "stage_template_id": 7,
                        "stage_name": "2-Ply Corrugation",
                        "semantic_role": "medium",
                        "legacy_role_aliases": ["medium"],
                        "material_type": "rm",
                        "product_tmpl_id": 11,
                        "product": "Medium",
                        "qty_per_fg": 0.28,
                        "total_qty": 28.0,
                        "uom": "kg",
                        "quantity_basis": "per_m2",
                        "quantity_source": "legacy_rm_requirement",
                        "metadata": {"layer_no": 2, "consumption_type": "area"},
                    },
                    {
                        "stage_code": "2ply",
                        "stage_contract_code": "v2_compat.stage.2ply",
                        "stage_template_id": 7,
                        "stage_name": "2-Ply Corrugation",
                        "semantic_role": "top_sheet_external",
                        "legacy_role_aliases": ["top_sheet_external", "top_roll"],
                        "material_type": "rm",
                        "product_tmpl_id": 12,
                        "product": "Top Sheet",
                        "qty_per_fg": 0.22,
                        "total_qty": 22.0,
                        "uom": "kg",
                        "quantity_basis": "per_m2",
                        "quantity_source": "legacy_rm_requirement",
                        "metadata": {"kind": "top_roll", "consumption_type": "area"},
                    },
                ],
                "routed_operations": [
                    {
                        "stage": "printing",
                        "operation_key": "printing",
                        "stage_code": "board",
                        "stage_contract_code": "v2_compat.stage.board",
                        "stage_template_id": 8,
                        "stage_name": "Board Formation",
                        "input_qty": 17.0,
                        "output_qty": 17.0,
                        "uom": "sheet",
                        "sequence": 10,
                    },
                    {
                        "stage": "conversion",
                        "operation_key": "conversion",
                        "stage_code": "seaming",
                        "stage_contract_code": "v2_compat.stage.seaming",
                        "stage_template_id": 13,
                        "stage_name": "Seaming / Jointing",
                        "input_qty": 17.0,
                        "output_qty": 100.0,
                        "uom": "unit",
                        "sequence": 20,
                    },
                ],
                "operations": [
                    {
                        "stage": "printing",
                        "input_qty": 17.0,
                        "output_qty": 17.0,
                        "uom": "sheet",
                    },
                    {
                        "stage": "conversion",
                        "input_qty": 17.0,
                        "output_qty": 100.0,
                        "uom": "unit",
                    },
                ],
            },
            "rm_requirements": [],
        }

        blueprint = bom_adapter.build_bom_blueprint_from_stagecontext_preview_payload(
            _FakeEnv(),
            payload,
        )

        components = blueprint["components"]
        self.assertEqual([item["stage_code"] for item in components], ["board", "board", "board"])
        self.assertEqual([item["stage_name"] for item in components], ["Board Formation", "Board Formation", "Board Formation"])

        writer_components = blueprint["writer_payload"]["components"]
        self.assertEqual(
            [item["metadata"].get("stage_code") for item in writer_components],
            ["board", "board", "board"],
        )
        self.assertEqual(
            [item["metadata"].get("stage_contract_code") for item in writer_components],
            ["v2_compat.stage.board", "v2_compat.stage.board", "v2_compat.stage.board"],
        )

    def test_stagecontext_blueprint_rebinds_paper_components_to_only_material_stage(self):
        payload = {
            "plant_id": 1,
            "order_qty": 100.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "index": 0,
                        "sequence": 10,
                        "stage_template_id": 13,
                        "stage_contract_code": "v2_compat.stage.seaming",
                        "stage_code": "seaming",
                        "stage_name": "Seaming / Jointing",
                        "stage_type": "conversion",
                        "capability_code": "conversion",
                        "execution_type": "material",
                        "input_qty_from_prev": 17.0,
                        "target_output_qty": 100.0,
                        "target_output_uom": "unit",
                        "output_stock_behavior": "fg",
                        "output_descriptor": {"kind": "box", "uom": "unit"},
                    },
                ]
            },
            "phase5_preview_model": {
                "stages": [],
                "materials": [
                    {
                        "stage_code": "2ply",
                        "stage_contract_code": "v2_compat.stage.2ply",
                        "stage_template_id": 7,
                        "stage_name": "2-Ply Corrugation",
                        "semantic_role": "liner",
                        "legacy_role_aliases": ["liner"],
                        "material_type": "rm",
                        "product_tmpl_id": 10,
                        "product": "Liner",
                        "qty_per_fg": 0.34,
                        "total_qty": 34.0,
                        "uom": "kg",
                        "quantity_basis": "per_m2",
                        "quantity_source": "legacy_rm_requirement",
                        "metadata": {"layer_no": 1, "consumption_type": "area"},
                    },
                    {
                        "stage_code": "2ply",
                        "stage_contract_code": "v2_compat.stage.2ply",
                        "stage_template_id": 7,
                        "stage_name": "2-Ply Corrugation",
                        "semantic_role": "medium",
                        "legacy_role_aliases": ["medium"],
                        "material_type": "rm",
                        "product_tmpl_id": 11,
                        "product": "Medium",
                        "qty_per_fg": 0.28,
                        "total_qty": 28.0,
                        "uom": "kg",
                        "quantity_basis": "per_m2",
                        "quantity_source": "legacy_rm_requirement",
                        "metadata": {"layer_no": 2, "consumption_type": "area"},
                    },
                    {
                        "stage_code": "board",
                        "stage_contract_code": "v2_compat.stage.board",
                        "stage_template_id": 8,
                        "stage_name": "Board Formation",
                        "semantic_role": "top_sheet_external",
                        "legacy_role_aliases": ["top_sheet_external", "top_roll"],
                        "material_type": "rm",
                        "product_tmpl_id": 12,
                        "product": "Top Sheet",
                        "qty_per_fg": 0.22,
                        "total_qty": 22.0,
                        "uom": "kg",
                        "quantity_basis": "per_m2",
                        "quantity_source": "legacy_rm_requirement",
                        "metadata": {"kind": "top_roll", "consumption_type": "area"},
                    },
                ],
                "routed_operations": [
                    {
                        "stage": "conversion",
                        "operation_key": "conversion",
                        "stage_code": "seaming",
                        "stage_contract_code": "v2_compat.stage.seaming",
                        "stage_template_id": 13,
                        "stage_name": "Seaming / Jointing",
                        "input_qty": 17.0,
                        "output_qty": 100.0,
                        "uom": "unit",
                        "sequence": 10,
                    },
                ],
                "operations": [
                    {
                        "stage": "conversion",
                        "input_qty": 17.0,
                        "output_qty": 100.0,
                        "uom": "unit",
                    },
                ],
            },
            "rm_requirements": [],
        }

        blueprint = bom_adapter.build_bom_blueprint_from_stagecontext_preview_payload(
            _FakeEnv(),
            payload,
        )

        components = blueprint["components"]
        self.assertEqual([item["stage_code"] for item in components], ["seaming", "seaming", "seaming"])
        self.assertEqual(
            [item["stage_name"] for item in components],
            ["Seaming / Jointing", "Seaming / Jointing", "Seaming / Jointing"],
        )

        writer_components = blueprint["writer_payload"]["components"]
        self.assertEqual(
            [item["metadata"].get("stage_code") for item in writer_components],
            ["seaming", "seaming", "seaming"],
        )
        self.assertEqual(
            [item["metadata"].get("stage_contract_code") for item in writer_components],
            ["v2_compat.stage.seaming", "v2_compat.stage.seaming", "v2_compat.stage.seaming"],
        )

    def test_stagecontext_blueprint_prefers_exact_preview_row_product_over_stale_paper_resolution(self):
        payload = {
            "plant_id": 1,
            "order_qty": 1.0,
            "paper_resolution": [
                {
                    "role": "liner",
                    "layer_no": 1,
                    "status": "exact",
                    "product_id": 14079,
                    "product_name": "[KP-140-79-NS-18] Kraft Paper",
                    "uom_id": 1,
                    "uom_name": "kg",
                }
            ],
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "index": 0,
                        "sequence": 10,
                        "stage_template_id": 8,
                        "stage_contract_code": "v2_compat.stage.board",
                        "stage_code": "board",
                        "stage_name": "Board Formation",
                        "stage_type": "lamination",
                        "capability_code": "lamination",
                        "execution_type": "material",
                        "input_qty_from_prev": 1.0,
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "semi_finished",
                        "output_descriptor": {"kind": "board", "uom": "sheet"},
                    }
                ]
            },
            "phase5_preview_model": {
                "stages": [],
                "materials": [
                    {
                        "stage_code": "board",
                        "stage_contract_code": "v2_compat.stage.board",
                        "stage_template_id": 8,
                        "stage_name": "Board Formation",
                        "semantic_role": "liner",
                        "legacy_role_aliases": ["liner"],
                        "material_type": "rm",
                        "product_tmpl_id": 10,
                        "product": "[KP-180-79-NS-18] Kraft Paper",
                        "qty_per_fg": 0.088,
                        "total_qty": 0.088,
                        "uom": "kg",
                        "quantity_basis": "per_m2",
                        "quantity_source": "paper_resolution",
                        "metadata": {
                            "layer_no": 1,
                            "gsm": 180.0,
                            "bf": 18.0,
                            "color": "Natural Shade(NS)",
                            "consumption_type": "area",
                            "product_id": 18079,
                            "product_name": "[KP-180-79-NS-18] Kraft Paper",
                            "uom_id": 1,
                            "uom_name": "kg",
                            "resolution_status": "exact",
                            "resolution": {
                                "status": "exact",
                                "product_id": 18079,
                                "product_name": "[KP-180-79-NS-18] Kraft Paper",
                                "uom_id": 1,
                                "uom_name": "kg",
                            },
                        },
                    }
                ],
                "routed_operations": [],
                "operations": [],
            },
            "rm_requirements": [],
        }

        blueprint = bom_adapter.build_bom_blueprint_from_stagecontext_preview_payload(
            _FakeEnv(),
            payload,
        )
        writer_component = blueprint["writer_payload"]["components"][0]
        metadata = dict(writer_component.get("metadata") or {})

        self.assertEqual(metadata.get("product_id"), 18079)
        self.assertEqual(metadata.get("writer_product_key"), "product.product:18079")
        self.assertEqual(metadata.get("resolution_status"), "exact")
        self.assertEqual((metadata.get("resolution") or {}).get("product_id"), 18079)

    def test_stagecontext_blueprint_preserves_top_roll_identity_when_semantic_role_is_degraded(self):
        payload = {
            "plant_id": 1,
            "order_qty": 1.0,
            "paper_resolution": [
                {
                    "role": "top_roll",
                    "layer_no": 1,
                    "status": "exact",
                    "product_id": 29,
                    "product_name": "KP-180-79-NS-18",
                    "uom_id": 1,
                    "uom_name": "kg",
                }
            ],
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "index": 0,
                        "sequence": 10,
                        "stage_template_id": 8,
                        "stage_contract_code": "v2_compat.stage.board",
                        "stage_code": "board",
                        "stage_name": "Board Formation",
                        "stage_type": "conversion",
                        "capability_code": "conversion",
                        "execution_type": "material",
                        "input_qty_from_prev": 1.0,
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "stock",
                        "output_descriptor": {"kind": "board", "uom": "sheet"},
                    },
                ]
            },
            "phase5_preview_model": {
                "stages": [],
                "materials": [
                    {
                        "stage_code": "board",
                        "stage_contract_code": "v2_compat.stage.board",
                        "stage_template_id": 8,
                        "stage_name": "Board Formation",
                        "role": "top_roll",
                        "semantic_role": "top_sheet_external",
                        "legacy_role_aliases": ["top_sheet_external", "top_roll"],
                        "material_type": "rm",
                        "product_tmpl_id": 10,
                        "product": "KP-180-79-NS-18",
                        "qty_per_fg": 0.088164,
                        "total_qty": 0.088164,
                        "uom": "kg",
                        "quantity_basis": "per_m2",
                        "quantity_source": "legacy_role_fallback",
                        "metadata": {
                            "kind": "top_roll",
                            "layer_no": 1,
                            "product_id": 29,
                            "uom_id": 1,
                            "uom_name": "kg",
                            "writer_product_key": "product.product:29",
                            "resolution": {
                                "product_id": 29,
                                "product_name": "KP-180-79-NS-18",
                                "uom_id": 1,
                                "uom_name": "kg",
                            },
                        },
                    },
                ],
                "routed_operations": [],
                "operations": [],
            },
            "rm_requirements": [],
        }

        blueprint = bom_adapter.build_bom_blueprint_from_stagecontext_preview_payload(
            _FakeEnv(),
            payload,
        )

        component = blueprint["components"][0]
        self.assertEqual(component["semantic_role"], "top_roll")
        self.assertEqual(component["product_key"], "rm_top_roll")
        self.assertEqual(component["product_id"], 29)

        writer_component = blueprint["writer_payload"]["components"][0]
        self.assertEqual(writer_component["product"], "rm_top_roll")
        self.assertEqual(writer_component["metadata"].get("semantic_role"), "top_roll")
        self.assertEqual(writer_component["metadata"].get("writer_product_key"), "product.product:29")

    def test_stagecontext_blueprint_prefers_stage_level_preview_quantities(self):
        payload = {
            "plant_id": 1,
            "order_qty": 100.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "index": 0,
                        "sequence": 10,
                        "stage_template_id": 101,
                        "stage_contract_code": "route.stage.print_a",
                        "stage_code": "print_a",
                        "stage_name": "Printing A",
                        "stage_type": "printing",
                        "capability_code": "printing",
                        "execution_type": "operation",
                        "input_qty_from_prev": 999.0,
                        "target_output_qty": 999.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "non_stock",
                        "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    },
                    {
                        "index": 1,
                        "sequence": 20,
                        "stage_template_id": 102,
                        "stage_contract_code": "route.stage.print_b",
                        "stage_code": "print_b",
                        "stage_name": "Printing B",
                        "stage_type": "printing",
                        "capability_code": "printing",
                        "execution_type": "operation",
                        "input_qty_from_prev": 999.0,
                        "target_output_qty": 999.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "non_stock",
                        "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    },
                ]
            },
            "phase5_preview_model": {
                "stages": [
                    {
                        "stage_template_id": 101,
                        "stage_contract_code": "route.stage.print_a",
                        "stage_code": "print_a",
                        "input_qty": 10.0,
                        "output_qty": 10.0,
                        "output_uom": "sheet",
                    },
                    {
                        "stage_template_id": 102,
                        "stage_contract_code": "route.stage.print_b",
                        "stage_code": "print_b",
                        "input_qty": 10.0,
                        "output_qty": 20.0,
                        "output_uom": "sheet",
                    },
                ],
                "operations": [
                    {
                        "stage": "printing",
                        "input_qty": 999.0,
                        "output_qty": 999.0,
                        "uom": "sheet",
                    }
                ],
                "materials": [],
            },
            "rm_requirements": [],
        }

        blueprint = bom_adapter.build_bom_blueprint_from_stagecontext_preview_payload(
            _FakeEnv(),
            payload,
        )

        stages = {item["stage_code"]: item for item in blueprint["stages"]}
        self.assertEqual(stages["print_a"]["operation_metrics"]["input_qty"], 10.0)
        self.assertEqual(stages["print_a"]["operation_metrics"]["output_qty"], 10.0)
        self.assertEqual(stages["print_a"]["operation_metrics"]["source"], "preview_stage_overlay")
        self.assertEqual(stages["print_b"]["operation_metrics"]["input_qty"], 10.0)
        self.assertEqual(stages["print_b"]["operation_metrics"]["output_qty"], 20.0)
        self.assertEqual(stages["print_b"]["operation_metrics"]["source"], "preview_stage_overlay")
        self.assertEqual(
            stages["print_b"]["authority"]["operation_quantities"],
            "preview_model_stage_overlay",
        )

        writer_ops = {item["stage"]: item for item in blueprint["writer_payload"]["operations"]}
        self.assertEqual(writer_ops["printing"]["input_qty"], 10.0)
        self.assertEqual(writer_ops["printing"]["output_qty"], 20.0)

    def test_stagecontext_blueprint_can_use_grouped_operation_stage_details_as_fallback(self):
        payload = {
            "plant_id": 1,
            "order_qty": 100.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "index": 0,
                        "sequence": 10,
                        "stage_template_id": 201,
                        "stage_contract_code": "route.stage.print_a",
                        "stage_code": "print_a",
                        "stage_name": "Printing A",
                        "stage_type": "printing",
                        "capability_code": "printing",
                        "execution_type": "operation",
                        "input_qty_from_prev": 999.0,
                        "target_output_qty": 999.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "non_stock",
                        "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    },
                    {
                        "index": 1,
                        "sequence": 20,
                        "stage_template_id": 202,
                        "stage_contract_code": "route.stage.print_b",
                        "stage_code": "print_b",
                        "stage_name": "Printing B",
                        "stage_type": "printing",
                        "capability_code": "printing",
                        "execution_type": "operation",
                        "input_qty_from_prev": 999.0,
                        "target_output_qty": 999.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "non_stock",
                        "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    },
                ]
            },
            "phase5_preview_model": {
                "stages": [],
                "operations": [
                    {
                        "stage": "printing",
                        "input_qty": 999.0,
                        "output_qty": 999.0,
                        "uom": "sheet",
                        "stage_details": [
                            {
                                "stage_code": "print_a",
                                "stage_contract_code": "route.stage.print_a",
                                "stage_template_id": 201,
                                "input_qty": 10.0,
                                "output_qty": 10.0,
                                "uom": "sheet",
                                "sequence": 10,
                            },
                            {
                                "stage_code": "print_b",
                                "stage_contract_code": "route.stage.print_b",
                                "stage_template_id": 202,
                                "input_qty": 10.0,
                                "output_qty": 20.0,
                                "uom": "sheet",
                                "sequence": 20,
                            },
                        ],
                    }
                ],
                "materials": [],
            },
            "rm_requirements": [],
        }

        blueprint = bom_adapter.build_bom_blueprint_from_stagecontext_preview_payload(
            _FakeEnv(),
            payload,
        )

        stages = {item["stage_code"]: item for item in blueprint["stages"]}
        self.assertEqual(stages["print_a"]["operation_metrics"]["input_qty"], 10.0)
        self.assertEqual(stages["print_a"]["operation_metrics"]["output_qty"], 10.0)
        self.assertEqual(stages["print_a"]["operation_metrics"]["source"], "preview_operation_stage_overlay")
        self.assertEqual(stages["print_b"]["operation_metrics"]["input_qty"], 10.0)
        self.assertEqual(stages["print_b"]["operation_metrics"]["output_qty"], 20.0)
        self.assertEqual(
            stages["print_b"]["authority"]["operation_quantities"],
            "preview_model_operation_stage_overlay",
        )

        writer_ops = {item["stage"]: item for item in blueprint["writer_payload"]["operations"]}
        details = writer_ops["printing"].get("stage_details") or []
        self.assertEqual([item["stage_code"] for item in details], ["print_a", "print_b"])
        self.assertEqual(details[0]["output_qty"], 10.0)
        self.assertEqual(details[1]["output_qty"], 20.0)

    def test_stagecontext_blueprint_prefers_distinct_routed_operations_when_available(self):
        payload = {
            "plant_id": 1,
            "order_qty": 100.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "index": 0,
                        "sequence": 10,
                        "stage_template_id": 201,
                        "stage_contract_code": "route.stage.print_a",
                        "stage_code": "print_a",
                        "stage_name": "Printing A",
                        "stage_type": "printing",
                        "capability_code": "printing",
                        "execution_type": "operation",
                        "input_qty_from_prev": 999.0,
                        "target_output_qty": 999.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "non_stock",
                        "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    },
                    {
                        "index": 1,
                        "sequence": 20,
                        "stage_template_id": 202,
                        "stage_contract_code": "route.stage.print_b",
                        "stage_code": "print_b",
                        "stage_name": "Printing B",
                        "stage_type": "printing",
                        "capability_code": "printing",
                        "execution_type": "operation",
                        "input_qty_from_prev": 999.0,
                        "target_output_qty": 999.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "non_stock",
                        "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    },
                ]
            },
            "phase5_preview_model": {
                "stages": [],
                "routed_operations": [
                    {
                        "stage": "printing",
                        "operation_key": "printing",
                        "stage_code": "print_a",
                        "stage_contract_code": "route.stage.print_a",
                        "stage_template_id": 201,
                        "input_qty": 10.0,
                        "output_qty": 10.0,
                        "uom": "sheet",
                        "sequence": 10,
                    },
                    {
                        "stage": "printing",
                        "operation_key": "printing",
                        "stage_code": "print_b",
                        "stage_contract_code": "route.stage.print_b",
                        "stage_template_id": 202,
                        "input_qty": 10.0,
                        "output_qty": 20.0,
                        "uom": "sheet",
                        "sequence": 20,
                    },
                ],
                "operations": [
                    {
                        "stage": "printing",
                        "input_qty": 999.0,
                        "output_qty": 999.0,
                        "uom": "sheet",
                    }
                ],
                "materials": [],
            },
            "rm_requirements": [],
        }

        blueprint = bom_adapter.build_bom_blueprint_from_stagecontext_preview_payload(
            _FakeEnv(),
            payload,
        )

        stages = {item["stage_code"]: item for item in blueprint["stages"]}
        self.assertEqual(stages["print_a"]["operation_metrics"]["input_qty"], 10.0)
        self.assertEqual(stages["print_a"]["operation_metrics"]["output_qty"], 10.0)
        self.assertEqual(stages["print_a"]["operation_metrics"]["source"], "preview_routed_operation_overlay")
        self.assertEqual(stages["print_b"]["operation_metrics"]["input_qty"], 10.0)
        self.assertEqual(stages["print_b"]["operation_metrics"]["output_qty"], 20.0)
        self.assertEqual(
            stages["print_b"]["authority"]["operation_quantities"],
            "preview_model_routed_operation_overlay",
        )

        writer_routed = blueprint["writer_payload"].get("routed_operations") or []
        self.assertEqual([item["stage_code"] for item in writer_routed], ["print_a", "print_b"])
        self.assertEqual([item["stage"] for item in writer_routed], ["printing", "printing"])

        writer_ops = {item["stage"]: item for item in blueprint["writer_payload"]["operations"]}
        self.assertEqual(writer_ops["printing"]["input_qty"], 10.0)
        self.assertEqual(writer_ops["printing"]["output_qty"], 20.0)

    def test_stagecontext_writer_routed_operations_include_first_output_stage(self):
        payload = {
            "plant_id": 1,
            "order_qty": 100.0,
            "phase8_shadow_runtime": {
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
                        "input_qty_from_prev": 0.0,
                        "target_output_qty": 17.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "semi_finished",
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
                        "input_qty_from_prev": 17.0,
                        "target_output_qty": 17.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "semi_finished",
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
                        "input_qty_from_prev": 17.0,
                        "target_output_qty": 100.0,
                        "target_output_uom": "unit",
                        "output_stock_behavior": "fg",
                        "output_descriptor": {"kind": "box", "uom": "unit"},
                    },
                ]
            },
            "phase5_preview_model": {
                "stages": [],
                "routed_operations": [
                    {
                        "stage": "corrugator",
                        "operation_key": "corrugator",
                        "stage_code": "2ply",
                        "stage_contract_code": "v2_compat.stage.2ply",
                        "stage_template_id": 7,
                        "input_qty": 0.0,
                        "output_qty": 17.0,
                        "uom": "sheet",
                        "sequence": 10,
                    },
                    {
                        "stage": "corrugator",
                        "operation_key": "corrugator",
                        "stage_code": "board",
                        "stage_contract_code": "v2_compat.stage.board",
                        "stage_template_id": 8,
                        "input_qty": 17.0,
                        "output_qty": 17.0,
                        "uom": "sheet",
                        "sequence": 20,
                    },
                    {
                        "stage": "conversion",
                        "operation_key": "conversion",
                        "stage_code": "seaming",
                        "stage_contract_code": "v2_compat.stage.seaming",
                        "stage_template_id": 13,
                        "input_qty": 17.0,
                        "output_qty": 100.0,
                        "uom": "unit",
                        "sequence": 30,
                    },
                ],
                "operations": [
                    {
                        "stage": "corrugator",
                        "input_qty": 99.04608,
                        "output_qty": 17.0,
                        "uom": "sheet",
                    },
                    {
                        "stage": "conversion",
                        "input_qty": 17.0,
                        "output_qty": 100.0,
                        "uom": "unit",
                    },
                ],
                "materials": [],
            },
            "rm_requirements": [],
        }

        blueprint = bom_adapter.build_bom_blueprint_from_stagecontext_preview_payload(
            _FakeEnv(),
            payload,
        )

        stages = {item["stage_code"]: item for item in blueprint["stages"]}
        self.assertEqual(stages["2ply"]["operation_metrics"]["input_qty"], 0.0)
        self.assertEqual(stages["2ply"]["operation_metrics"]["output_qty"], 17.0)
        self.assertEqual(stages["2ply"]["operation_metrics"]["source"], "preview_routed_operation_overlay")

        writer_routed = blueprint["writer_payload"].get("routed_operations") or []
        self.assertEqual([item["stage_code"] for item in writer_routed], ["2ply", "board", "seaming"])
        self.assertEqual(writer_routed[0]["input_qty"], 0.0)
        self.assertEqual(writer_routed[0]["output_qty"], 17.0)

        writer_ops = {item["stage"]: item for item in blueprint["writer_payload"]["operations"]}
        self.assertEqual(writer_ops["corrugator"]["input_qty"], 17.0)
        self.assertEqual(writer_ops["corrugator"]["output_qty"], 17.0)
        self.assertEqual(writer_ops["conversion"]["input_qty"], 17.0)
        self.assertEqual(writer_ops["conversion"]["output_qty"], 100.0)

    def test_manufacturing_plan_flattening_matches_stagecontext_writer_payload(self):
        payload = {
            "spec_id": 88,
            "plant_id": 3,
            "order_qty": 50.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "index": 0,
                        "sequence": 10,
                        "stage_template_id": 301,
                        "stage_contract_code": "route.stage.print_material",
                        "stage_code": "print_material",
                        "stage_name": "Print Material",
                        "stage_type": "printing",
                        "capability_code": "printing",
                        "execution_type": "material",
                        "input_qty_from_prev": 12.0,
                        "target_output_qty": 12.0,
                        "target_output_uom": "sheet",
                        "input_quantity_node_key": "sheet",
                        "output_quantity_node_key": "sheet",
                        "output_stock_behavior": "semi_finished",
                        "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    },
                    {
                        "index": 1,
                        "sequence": 20,
                        "stage_template_id": 302,
                        "stage_contract_code": "route.stage.close_material",
                        "stage_code": "close_material",
                        "stage_name": "Close Material",
                        "stage_type": "conversion",
                        "capability_code": "conversion",
                        "execution_type": "material",
                        "input_qty_from_prev": 12.0,
                        "target_output_qty": 50.0,
                        "target_output_uom": "unit",
                        "input_quantity_node_key": "finished_goods",
                        "output_quantity_node_key": "finished_goods",
                        "output_stock_behavior": "fg",
                        "output_descriptor": {"kind": "box", "uom": "unit"},
                    },
                ]
            },
            "phase5_preview_model": {
                "materials": [
                    {
                        "stage_code": "print_material",
                        "stage_contract_code": "route.stage.print_material",
                        "stage_template_id": 301,
                        "stage_name": "Print Material",
                        "semantic_role": "ink",
                        "legacy_role_aliases": ["ink"],
                        "material_type": "rm",
                        "product_key": "rm_ink",
                        "product_tmpl_id": 501,
                        "product": "Ink",
                        "qty_per_fg": 0.05,
                        "total_qty": 2.5,
                        "uom": "kg",
                        "quantity_basis": "per_m2",
                        "quantity_source": "legacy_rm_requirement",
                        "metadata": {"consumption_type": "area"},
                    }
                ],
                "routed_operations": [
                    {
                        "stage": "printing",
                        "operation_key": "printing",
                        "stage_code": "print_material",
                        "stage_contract_code": "route.stage.print_material",
                        "stage_template_id": 301,
                        "stage_name": "Print Material",
                        "input_qty": 12.0,
                        "output_qty": 12.0,
                        "uom": "sheet",
                        "sequence": 10,
                    },
                    {
                        "stage": "conversion",
                        "operation_key": "conversion",
                        "stage_code": "close_material",
                        "stage_contract_code": "route.stage.close_material",
                        "stage_template_id": 302,
                        "stage_name": "Close Material",
                        "input_qty": 12.0,
                        "output_qty": 50.0,
                        "uom": "unit",
                        "sequence": 20,
                    },
                ],
                "operations": [
                    {"stage": "printing", "input_qty": 12.0, "output_qty": 12.0, "uom": "sheet"},
                    {"stage": "conversion", "input_qty": 12.0, "output_qty": 50.0, "uom": "unit"},
                ],
            },
            "rm_requirements": [],
        }

        stagecontext_blueprint = bom_adapter.build_bom_blueprint_from_stagecontext_preview_payload(
            _FakeEnv(),
            payload,
        )
        plan = manufacturing_plan.build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        flattened = manufacturing_plan.flatten_manufacturing_plan_to_single_bom_writer_payload(plan)

        self.assertEqual(flattened, stagecontext_blueprint["writer_payload"])
        self.assertNotIn("sfg", {item.get("product") for item in flattened["components"]})

    def test_manufacturing_plan_flattening_keeps_consumable_edges_as_components(self):
        payload = {
            "order_qty": 10.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "index": 0,
                        "sequence": 10,
                        "stage_template_id": 401,
                        "stage_contract_code": "route.stage.apply_finish",
                        "stage_code": "apply_finish",
                        "stage_name": "Apply Finish",
                        "stage_type": "printing",
                        "capability_code": "printing",
                        "execution_type": "operation",
                        "input_qty_from_prev": 10.0,
                        "target_output_qty": 10.0,
                        "target_output_uom": "sheet",
                        "input_quantity_node_key": "sheet",
                        "output_quantity_node_key": "sheet",
                        "output_stock_behavior": "non_stock",
                        "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    }
                ]
            },
            "phase5_preview_model": {
                "materials": [
                    {
                        "stage_code": "apply_finish",
                        "stage_contract_code": "route.stage.apply_finish",
                        "stage_template_id": 401,
                        "semantic_role": "varnish",
                        "legacy_role_aliases": ["varnish"],
                        "category": "consumable",
                        "material_type": "consumable",
                        "product_key": "rm_varnish",
                        "product_tmpl_id": 601,
                        "total_qty": 1.25,
                        "uom": "kg",
                        "quantity_source": "legacy_rm_requirement",
                        "metadata": {"consumption_type": "area"},
                    }
                ],
                "routed_operations": [
                    {
                        "stage": "printing",
                        "operation_key": "printing",
                        "stage_code": "apply_finish",
                        "stage_contract_code": "route.stage.apply_finish",
                        "stage_template_id": 401,
                        "input_qty": 10.0,
                        "output_qty": 10.0,
                        "uom": "sheet",
                        "sequence": 10,
                    }
                ],
                "operations": [
                    {"stage": "printing", "input_qty": 10.0, "output_qty": 10.0, "uom": "sheet"},
                ],
            },
            "rm_requirements": [],
        }

        plan = manufacturing_plan.build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        flattened = manufacturing_plan.flatten_manufacturing_plan_to_single_bom_writer_payload(plan)

        self.assertEqual(len(plan.segments[0].consumable_inputs), 1)
        self.assertEqual(flattened["components"][0]["product"], "rm_varnish")
        self.assertEqual(flattened["components"][0]["qty"], 1.25)

    def test_stagecontext_blueprint_uses_attached_consumable_profile_product(self):
        kg = _FakeRecord(id=7, name="kg", display_name="kg")
        adhesive_variant = _FakeRecord(
            id=901,
            name="ADH-001 Adhesive",
            display_name="[ADH-001] Adhesive",
            uom_id=kg,
        )
        adhesive_template = _FakeRecord(
            id=601,
            name="Adhesive",
            display_name="Adhesive",
            product_variant_id=adhesive_variant,
        )
        env = _FakeEnv(
            **{
                "product.template": _FakeProductTemplateModel({601: adhesive_template}),
            }
        )
        payload = {
            "order_qty": 1.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "index": 0,
                        "sequence": 10,
                        "stage_template_id": 401,
                        "stage_contract_code": "route.stage.2ply",
                        "stage_code": "2ply",
                        "stage_name": "2-Ply Corrugation",
                        "stage_type": "corrugation",
                        "capability_code": "corrugation",
                        "execution_type": "material",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_stock_behavior": "semi_finished",
                        "output_descriptor": {"kind": "sheet", "uom": "sheet"},
                    }
                ]
            },
            "phase5_preview_model": {
                "materials": [
                    {
                        "stage_code": "2ply",
                        "stage_contract_code": "route.stage.2ply",
                        "stage_template_id": 401,
                        "semantic_role": "adhesive_corrugation",
                        "category": "consumable",
                        "material_type": "consumable",
                        "product_key": "rm_adhesive_corrugation",
                        "product_tmpl_id": 601,
                        "total_qty": 0.093,
                        "qty_per_fg": 0.093,
                        "uom": "kg",
                        "quantity_source": "legacy_rm_requirement",
                        "metadata": {"kind": "adhesive_corrugation"},
                    }
                ],
                "operations": [],
                "routed_operations": [],
                "stages": [],
            },
            "rm_requirements": [],
        }

        blueprint = bom_adapter.build_bom_blueprint_from_stagecontext_preview_payload(env, payload)
        component = blueprint["writer_payload"]["components"][0]

        self.assertEqual(component["product"], "rm_adhesive_corrugation")
        self.assertEqual(component["metadata"]["product_id"], 901)
        self.assertEqual(component["metadata"]["writer_product_key"], "product.product:901")
        self.assertEqual(component["metadata"]["resolution_source"], "material_profile_rule_product")


