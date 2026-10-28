from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.corr_mrp_bridge.models.product_hook import ProductTemplateCorr


@tagged("post_install", "-at_install", "corr_mrp_bridge")
class TestBulkUploadStabilization(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ProductTemplate = cls.env["product.template"].sudo()
        cls.Product = cls.env["product.product"].sudo()
        cls.manager_group = cls.env.ref("corrugation_formula_engine_v2.group_corr_manager")
        cls.env.user.write({"groups_id": [(4, cls.manager_group.id)]})
        cls.env["ir.config_parameter"].sudo().set_param(
            "corr_engine_v2.allowed_paper_material_template_ids",
            "",
        )

    def _make_attr(self, name, values):
        Attr = self.env["product.attribute"].sudo()
        Value = self.env["product.attribute.value"].sudo()
        attr = Attr.search([("name", "=", name)], limit=1)
        if not attr:
            attr = Attr.create({"name": name})
        value_records = self.env["product.attribute.value"]
        for value in values:
            record = Value.search([("attribute_id", "=", attr.id), ("name", "=", str(value))], limit=1)
            if not record:
                record = Value.create({"attribute_id": attr.id, "name": str(value)})
            value_records |= record
        return attr, value_records

    def _make_material_template(self, code, name=None, values=None, role="raw_material"):
        values = values or {
            "GSM": ["150"],
            "BF": ["18"],
            "Color": ["Natural Shade(NS)"],
            "Deckle": ["79"],
        }
        lines = []
        for attr_name, raw_values in values.items():
            attr, value_records = self._make_attr(attr_name, raw_values)
            lines.append((0, 0, {"attribute_id": attr.id, "value_ids": [(6, 0, value_records.ids)]}))
        template = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": name or code,
                "default_code": False,
                "product_role": role,
                "attribute_line_ids": lines,
            }
        )
        template.product_variant_id.default_code = code
        return template

    def _enable_engine_v2_bulk_upload(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "corr_mrp_bridge.bulk_upload_use_engine_v2",
            "1",
        )

    def _load_engine_v2_product_rows(self, rows, *, skip_autobom=True):
        context = {"skip_corr_autobom": True} if skip_autobom else {}
        return self.ProductTemplate.with_context(**context).load(
            ["Product Code", "Product Name", "Style / FEFCO", "Width (mm)"],
            rows,
        )

    def _latest_bulk_line_for_code(self, code):
        return self.env["corr.bulk.upload.line"].sudo().search(
            [("product_code", "=", code)],
            order="id desc",
            limit=1,
        )

    def _make_bulk_upload_batch_with_statuses(self, statuses):
        batch = self.env["corr.bulk.upload.batch"].sudo().create({"name": "Summary Test Batch"})
        Line = self.env["corr.bulk.upload.line"].sudo()
        for index, status in enumerate(statuses, start=2):
            Line.create(
                {
                    "batch_id": batch.id,
                    "excel_row": index,
                    "product_code": "SUMMARY-%03d" % index,
                    "product_name": "Summary Product %03d" % index,
                    "status": status,
                }
            )
        batch.invalidate_recordset()
        return batch

    def test_no_duplicate_product_codes_import_allowed(self):
        result = self.ProductTemplate.with_context(skip_corr_autobom=True).load(
            ["Product Code", "Product Name"],
            [
                ["STAB-DUP-OK-001", "Stabilization Duplicate OK 1"],
                ["STAB-DUP-OK-002", "Stabilization Duplicate OK 2"],
            ],
        )

        self.assertIsInstance(result, dict)
        self.assertTrue(self.ProductTemplate.search([("default_code", "=", "STAB-DUP-OK-001")], limit=1))
        self.assertTrue(self.ProductTemplate.search([("default_code", "=", "STAB-DUP-OK-002")], limit=1))

    def test_one_duplicate_product_code_rejects_file_before_import(self):
        codes = ["STAB-DUP-001", "STAB-DUP-002"]
        before_count = self.ProductTemplate.search_count([("default_code", "in", codes)])

        with self.assertRaises(UserError) as raised:
            self.ProductTemplate.with_context(skip_corr_autobom=True).load(
                ["Product Code", "Product Name"],
                [
                    ["STAB-DUP-001", "Duplicate 1"],
                    ["STAB-DUP-002", "Duplicate 2"],
                    ["STAB-DUP-001", "Duplicate 1 Again"],
                ],
            )

        self.assertIn("STAB-DUP-001 - rows 2 and 4", raised.exception.args[0])
        self.assertEqual(self.ProductTemplate.search_count([("default_code", "in", codes)]), before_count)

    def test_multiple_duplicate_product_codes_reported_together(self):
        with self.assertRaises(UserError) as raised:
            self.ProductTemplate._corr_validate_import_duplicate_product_codes(
                ["default_code", "name"],
                [
                    ["STAB-DUP-MULTI-001", "Duplicate 1"],
                    ["STAB-DUP-MULTI-002", "Duplicate 2"],
                    ["STAB-DUP-MULTI-001", "Duplicate 1 Again"],
                    ["STAB-DUP-MULTI-002", "Duplicate 2 Again"],
                    ["STAB-DUP-MULTI-001", "Duplicate 1 Third"],
                ],
            )

        message = raised.exception.args[0]
        self.assertIn("STAB-DUP-MULTI-001 - rows 2, 4, 6", message)
        self.assertIn("STAB-DUP-MULTI-002 - rows 3 and 5", message)

    def test_duplicate_existing_product_code_still_rejects_file(self):
        self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Existing Stabilization Product",
                "default_code": "STAB-DUP-EXISTING",
            }
        )

        with self.assertRaises(UserError) as raised:
            self.ProductTemplate.with_context(skip_corr_autobom=True).load(
                ["Product Code", "Product Name"],
                [
                    ["STAB-DUP-EXISTING", "Updated Name 1"],
                    ["STAB-DUP-EXISTING", "Updated Name 2"],
                ],
            )

        self.assertIn("STAB-DUP-EXISTING - rows 2 and 3", raised.exception.args[0])

    def test_duplicate_code_with_different_names_rejects_file(self):
        with self.assertRaises(UserError) as raised:
            self.ProductTemplate._corr_validate_import_duplicate_product_codes(
                ["default_code", "name"],
                [
                    ["STAB-DUP-NAME", "First Name"],
                    ["STAB-DUP-NAME", "Second Name"],
                ],
            )

        self.assertIn("STAB-DUP-NAME - rows 2 and 3", raised.exception.args[0])

    def test_csv_duplicate_handling_uses_same_load_mechanism(self):
        mapped_fields, mapped_data, _warnings, _source_map = self.ProductTemplate._corr_apply_import_header_aliases(
            ["default_code", "name"],
            [
                ["STAB-DUP-CSV", "CSV Name 1"],
                ["STAB-DUP-CSV", "CSV Name 2"],
            ],
        )

        with self.assertRaises(UserError) as raised:
            self.ProductTemplate._corr_validate_import_duplicate_product_codes(mapped_fields, mapped_data)

        self.assertIn("STAB-DUP-CSV - rows 2 and 3", raised.exception.args[0])

    def test_bulk_upload_summary_all_success(self):
        batch = self._make_bulk_upload_batch_with_statuses(["success"] * 5)

        summary = self.ProductTemplate._corr_bulk_upload_summary_payload(batch)

        self.assertEqual(summary["title"], "Bulk Upload Completed Successfully")
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["success"], 5)
        self.assertEqual(summary["auto_fixed"], 0)
        self.assertEqual(summary["failed"], 0)
        self.assertIn("Success: 5", summary["message"])
        self.assertIn("Failed: 0", summary["message"])

    def test_bulk_upload_summary_partial_failure(self):
        batch = self._make_bulk_upload_batch_with_statuses(["success", "success", "success", "success", "failed"])

        summary = self.ProductTemplate._corr_bulk_upload_summary_payload(batch)

        self.assertEqual(summary["title"], "Bulk Upload Completed with Errors")
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["success"], 4)
        self.assertEqual(summary["auto_fixed"], 0)
        self.assertEqual(summary["failed"], 1)
        self.assertIn("Success: 4", summary["message"])
        self.assertIn("Failed: 1", summary["message"])

    def test_bulk_upload_summary_auto_fixed_counted_separately(self):
        batch = self._make_bulk_upload_batch_with_statuses(
            ["success", "success", "success", "auto_fixed", "auto_fixed"]
        )

        summary = self.ProductTemplate._corr_bulk_upload_summary_payload(batch)

        self.assertEqual(summary["title"], "Bulk Upload Completed Successfully")
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["success"], 3)
        self.assertEqual(summary["auto_fixed"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertIn("Success: 3", summary["message"])
        self.assertIn("Auto Fixed: 2", summary["message"])

    def test_bulk_upload_summary_view_action_opens_exact_batch(self):
        first_batch = self._make_bulk_upload_batch_with_statuses(["success"])
        second_batch = self._make_bulk_upload_batch_with_statuses(["failed"])

        summary = self.ProductTemplate._corr_bulk_upload_summary_payload(first_batch)

        self.assertNotEqual(first_batch.id, second_batch.id)
        self.assertEqual(summary["batch_id"], first_batch.id)
        self.assertEqual(summary["action"]["res_model"], "corr.bulk.upload.batch")
        self.assertEqual(summary["action"]["res_id"], first_batch.id)
        self.assertEqual(summary["action"]["view_mode"], "form")

    def test_existing_engine_v2_product_import_updates_same_product_spec_and_bom(self):
        self._enable_engine_v2_bulk_upload()
        product = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Existing Import Product",
                "default_code": "TEST-EXIST-001",
                "corr_style": "0201",
                "corr_width_mm": 300.0,
            }
        )
        spec = self.env["corr.engine.v2.spec"].sudo().create(
            {"name": "Existing Import Spec", "product_tmpl_id": product.id}
        )
        component = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {"name": "Existing Import Component", "default_code": "TEST-EXIST-COMP"}
        ).product_variant_id
        uom = self.env.ref("uom.product_uom_unit")
        bom = self.env["mrp.bom"].sudo().create(
            {
                "product_tmpl_id": product.id,
                "product_qty": 1.0,
                "product_uom_id": uom.id,
                "bom_line_ids": [
                    (0, 0, {"product_id": component.id, "product_qty": 1.0, "product_uom_id": uom.id})
                ],
            }
        )

        product_count = self.ProductTemplate.search_count([("default_code", "=", "TEST-EXIST-001")])
        spec_count = self.env["corr.engine.v2.spec"].sudo().search_count([("product_tmpl_id", "=", product.id)])
        bom_count = self.env["mrp.bom"].sudo().search_count([("product_tmpl_id", "=", product.id)])

        result = self._load_engine_v2_product_rows(
            [["TEST-EXIST-001", "Existing Import Product Updated", "0201", 420.0]]
        )

        product.invalidate_recordset()
        self.assertEqual(result["ids"], [product.id])
        self.assertEqual(self.ProductTemplate.search_count([("default_code", "=", "TEST-EXIST-001")]), product_count)
        self.assertEqual(product.name, "Existing Import Product Updated")
        self.assertEqual(product.default_code, "TEST-EXIST-001")
        self.assertEqual(self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", product.id)]), spec)
        self.assertEqual(self.env["mrp.bom"].sudo().search([("product_tmpl_id", "=", product.id)]), bom)
        self.assertEqual(self.env["corr.engine.v2.spec"].sudo().search_count([("product_tmpl_id", "=", product.id)]), spec_count)
        self.assertEqual(self.env["mrp.bom"].sudo().search_count([("product_tmpl_id", "=", product.id)]), bom_count)

    def test_existing_engine_v2_product_import_updates_supported_field_without_duplicate(self):
        self._enable_engine_v2_bulk_upload()
        product = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Existing Update Product",
                "default_code": "TEST-EXIST-002",
                "corr_style": "0201",
                "corr_width_mm": 300.0,
            }
        )

        self._load_engine_v2_product_rows(
            [["TEST-EXIST-002", "Existing Update Product", "0201", 555.0]]
        )

        product.invalidate_recordset()
        self.assertEqual(product.corr_width_mm, 555.0)
        self.assertEqual(
            self.ProductTemplate.search([("default_code", "=", "TEST-EXIST-002")]),
            product,
        )

    def test_new_engine_v2_product_import_still_creates_normally(self):
        self._enable_engine_v2_bulk_upload()
        self.assertFalse(self.ProductTemplate.search([("default_code", "=", "TEST-NEW-001")], limit=1))

        result = self._load_engine_v2_product_rows(
            [["TEST-NEW-001", "New Import Product", "0201", 300.0]]
        )

        product = self.ProductTemplate.search([("default_code", "=", "TEST-NEW-001")])
        self.assertEqual(len(product), 1)
        self.assertEqual(result["ids"], product.ids)

    def test_mixed_existing_and_new_engine_v2_import_updates_and_creates(self):
        self._enable_engine_v2_bulk_upload()
        existing = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Mixed Existing Product",
                "default_code": "TEST-MIXED-EXIST",
                "corr_style": "0201",
                "corr_width_mm": 300.0,
            }
        )

        result = self._load_engine_v2_product_rows(
            [
                ["TEST-MIXED-EXIST", "Mixed Existing Product Updated", "0201", 333.0],
                ["TEST-MIXED-NEW", "Mixed New Product", "0201", 444.0],
            ]
        )

        new_product = self.ProductTemplate.search([("default_code", "=", "TEST-MIXED-NEW")])
        existing.invalidate_recordset()
        self.assertEqual(len(new_product), 1)
        self.assertEqual(result["ids"], [existing.id, new_product.id])
        self.assertEqual(existing.name, "Mixed Existing Product Updated")
        self.assertEqual(
            self.ProductTemplate.search_count([("default_code", "=", "TEST-MIXED-EXIST")]),
            1,
        )

    def test_engine_v2_import_rejects_ambiguous_existing_database_product_codes(self):
        self._enable_engine_v2_bulk_upload()
        self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {"name": "Ambiguous Existing A", "default_code": "TEST-EXIST-DUP"}
        )
        self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {"name": "Ambiguous Existing B", "default_code": "TEST-EXIST-DUP"}
        )

        with self.assertRaises(UserError) as raised:
            self._load_engine_v2_product_rows(
                [["TEST-EXIST-DUP", "Ambiguous Existing Updated", "0201", 300.0]]
            )

        self.assertIn("database already contains duplicate Product Codes", raised.exception.args[0])
        self.assertIn("TEST-EXIST-DUP", raised.exception.args[0])

    def test_existing_engine_v2_product_with_existing_material_reports_success_not_auto_fixed(self):
        self._enable_engine_v2_bulk_upload()
        product = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Existing Material Reuse Product",
                "default_code": "TEST-EXIST-MAT-OK",
                "corr_style": "0201",
            }
        )

        def mark_success(record):
            record._corr_set_autobom_status(
                "success",
                "v2",
                "ok",
                error_payload={"spec_id": False, "bom_id": False},
                preview_status="success",
                bom_status="success",
            )
            return {"status": "success"}

        with patch.object(ProductTemplateCorr, "_corr_autobom_maybe", autospec=True, side_effect=mark_success):
            self._load_engine_v2_product_rows(
                [["TEST-EXIST-MAT-OK", "Existing Material Reuse Product Updated", "0201", 300.0]],
                skip_autobom=False,
            )

        line = self._latest_bulk_line_for_code("TEST-EXIST-MAT-OK")
        self.assertEqual(self.ProductTemplate.search([("default_code", "=", "TEST-EXIST-MAT-OK")]), product)
        self.assertEqual(line.product_tmpl_id, product)
        self.assertEqual(line.status, "success")

    def test_existing_engine_v2_product_with_missing_material_can_still_report_auto_fixed(self):
        self._enable_engine_v2_bulk_upload()
        product = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Existing Missing Material Product",
                "default_code": "TEST-EXIST-MAT-AUTO",
                "corr_style": "0201",
            }
        )
        created_material = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {"name": "Auto Created Test Material", "default_code": "TEST-AUTO-MAT", "product_role": "raw_material"}
        ).product_variant_id

        def mark_auto_fixed(record):
            record._corr_set_autobom_status(
                "success",
                "v2",
                "ok",
                error_payload={
                    "spec_id": False,
                    "bom_id": False,
                    "auto_created_material_variant_ids": [created_material.id],
                },
                preview_status="success",
                bom_status="success",
            )
            return {"status": "success", "auto_created_material_variant_ids": [created_material.id]}

        with patch.object(ProductTemplateCorr, "_corr_autobom_maybe", autospec=True, side_effect=mark_auto_fixed):
            self._load_engine_v2_product_rows(
                [["TEST-EXIST-MAT-AUTO", "Existing Missing Material Product Updated", "0201", 300.0]],
                skip_autobom=False,
            )

        line = self._latest_bulk_line_for_code("TEST-EXIST-MAT-AUTO")
        self.assertEqual(self.ProductTemplate.search([("default_code", "=", "TEST-EXIST-MAT-AUTO")]), product)
        self.assertEqual(line.product_tmpl_id, product)
        self.assertEqual(line.status, "auto_fixed")
        self.assertEqual(line.auto_created_material_variant_ids, created_material)

    def test_bulk_upload_result_actions_require_corr_manager_group(self):
        user = self.env["res.users"].sudo().create(
            {
                "name": "Stabilization Non Manager",
                "login": "stabilization_non_manager",
                "email": "stabilization_non_manager@example.com",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        batch = self.env["corr.bulk.upload.batch"].sudo().create({"name": "Security Batch"})

        with self.assertRaises(AccessError):
            batch.with_user(user).action_retry_failed_boms()
        with self.assertRaises(AccessError):
            batch.with_user(user).action_download_error_report()

    def test_material_attribute_values_are_created_only_when_autocreate_enabled(self):
        template = self._make_material_template(
            "STAB-KRAFT-AUTO",
            values={
                "GSM": ["150"],
                "BF": ["18"],
                "Color": ["White"],
                "Deckle": ["60"],
            },
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "corr_mrp_bridge.bulk_upload_auto_create_missing_materials",
            "0",
        )

        result = self.ProductTemplate._corr_resolve_v2_layer_material_values(
            material_template=template,
            gsm=180,
            bf=20,
            color="Natural Shade(NS)",
            deckle_cm=79,
            auto_create_missing_values=False,
        )
        self.assertFalse(result["gsm_value_id"])
        self.assertFalse(result["bf_value_id"])
        self.assertFalse(result["color_value_id"])

        self.env["ir.config_parameter"].sudo().set_param(
            "corr_mrp_bridge.bulk_upload_auto_create_missing_materials",
            "1",
        )
        result = self.ProductTemplate._corr_resolve_v2_layer_material_values(
            material_template=template,
            gsm=180,
            bf=20,
            color="Natural Shade(NS)",
            deckle_cm=79,
            auto_create_missing_values=True,
        )
        self.assertTrue(result["gsm_value_id"])
        self.assertTrue(result["bf_value_id"])
        self.assertTrue(result["color_value_id"])
        self.assertTrue(result["deckle_value_id"])

    def test_missing_material_variant_created_once_for_shared_requirement(self):
        template = self._make_material_template(
            "STAB-KRAFT-SHARED",
            values={
                "GSM": ["150"],
                "BF": ["18"],
                "Color": ["White"],
                "Deckle": ["60"],
            },
        )
        product = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {"name": "Shared Requirement FG", "default_code": "STAB-FG-SHARED"}
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "corr_mrp_bridge.bulk_upload_auto_create_missing_materials",
            "1",
        )
        payload = {
            "paper_resolution": [
                {
                    "status": "missing",
                    "role": "liner",
                    "source_layer_no": 1,
                    "material_template_id": template.id,
                    "gsm": 180,
                    "bf": 20,
                    "color": "Natural Shade(NS)",
                    "required_deckle_cm": 79,
                },
                {
                    "status": "missing",
                    "role": "liner",
                    "source_layer_no": 2,
                    "material_template_id": template.id,
                    "gsm": 180,
                    "bf": 20,
                    "color": "Natural Shade(NS)",
                    "required_deckle_cm": 79,
                },
            ]
        }
        before = len(template.product_variant_ids)
        created_ids = product._corr_autocreate_missing_material_variants_from_payload(payload)
        self.assertEqual(len(created_ids), 1)
        self.assertEqual(len(template.product_variant_ids), before + 1)

    def test_unresolved_autocreatable_layers_are_persisted_for_preview(self):
        template = self._make_material_template(
            "STAB-KRAFT-PERSIST",
            values={
                "GSM": ["150"],
                "BF": ["18"],
                "Color": ["Natural Shade(NS)"],
                "Deckle": ["60"],
            },
        )
        flute = self.env["corr.flute"].sudo().search([("code", "=", "B")], limit=1)
        if not flute:
            flute = self.env["corr.flute"].sudo().create({"name": "Flute B", "code": "B"})
        self.env["ir.config_parameter"].sudo().set_param(
            "corr_mrp_bridge.bulk_upload_auto_create_missing_materials",
            "1",
        )
        product = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Persist Missing Layer FG",
                "default_code": "STAB-FG-PERSIST",
                "corr_style": "0110",
                "corr_width_mm": 600,
                "corr_forced_ups": 1,
                "corr_layer1_role": "liner",
                "corr_layer1_gsm": 180,
                "corr_layer1_bf": 20,
                "corr_layer1_color": "Natural Shade(NS)",
                "corr_layer1_material_template": template.product_variant_id.default_code,
                "corr_layer2_role": "medium",
                "corr_layer2_gsm": 120,
                "corr_layer2_bf": 18,
                "corr_layer2_flute_code": flute.code,
                "corr_layer2_color": "Natural Shade(NS)",
                "corr_layer2_material_template": template.product_variant_id.default_code,
                "corr_layer3_role": "liner",
                "corr_layer3_gsm": 150,
                "corr_layer3_bf": 18,
                "corr_layer3_color": "Natural Shade(NS)",
                "corr_layer3_material_template": template.product_variant_id.default_code,
            }
        )

        layers = product._corr_build_v2_layer_source_data()

        self.assertEqual(len(layers), 3)
        self.assertEqual(layers[0]["role"], "inner_liner")
        self.assertEqual(layers[0]["material_template_id"], template.id)
        self.assertFalse(layers[0]["material_product_id"])
        self.assertTrue(layers[0]["gsm_value_id"])
        self.assertTrue(layers[0]["bf_value_id"])
        self.assertTrue(layers[0]["color_value_id"])

    def test_material_template_lookup_by_variant_code(self):
        template = self._make_material_template("E2E-KRAFT-CODE", name="Lookup Kraft Paper")
        found = self.ProductTemplate._corr_find_material_template("E2E-KRAFT-CODE")
        self.assertEqual(found, template)

    def test_material_template_lookup_by_missing_or_inappropriate_code_is_not_resolved(self):
        finished = self._make_material_template(
            "E2E-FG-CODE",
            name="Not Raw Material",
            role="finished_good",
        )
        self.assertFalse(self.ProductTemplate._corr_find_material_template("E2E-NOT-FOUND"))
        self.assertFalse(self.ProductTemplate._corr_find_material_template(finished.product_variant_id.default_code))

    def test_material_template_lookup_by_ambiguous_variant_code_fails(self):
        self._make_material_template("E2E-KRAFT-AMB", name="Ambiguous Kraft 1")
        self._make_material_template("E2E-KRAFT-AMB", name="Ambiguous Kraft 2")

        with self.assertRaises(UserError):
            self.ProductTemplate._corr_find_material_template("E2E-KRAFT-AMB")

    def test_batch_marks_all_products_using_batch_created_variant_auto_fixed(self):
        material = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {"name": "Batch Created Material", "default_code": "STAB-BATCH-RM", "product_role": "raw_material"}
        ).product_variant_id
        existing_material = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {"name": "Existing Material", "default_code": "STAB-BATCH-RM-OLD", "product_role": "raw_material"}
        ).product_variant_id
        fg_a = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {"name": "Auto Fixed A", "default_code": "STAB-BATCH-A", "product_role": "finished_good"}
        )
        fg_b = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {"name": "Auto Fixed B", "default_code": "STAB-BATCH-B", "product_role": "finished_good"}
        )
        fg_c = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {"name": "Success C", "default_code": "STAB-BATCH-C", "product_role": "finished_good"}
        )
        uom = self.env.ref("uom.product_uom_unit")
        Bom = self.env["mrp.bom"].sudo()
        bom_a = Bom.create(
            {
                "product_tmpl_id": fg_a.id,
                "product_qty": 1,
                "product_uom_id": uom.id,
                "bom_line_ids": [(0, 0, {"product_id": material.id, "product_qty": 1, "product_uom_id": uom.id})],
            }
        )
        bom_b = Bom.create(
            {
                "product_tmpl_id": fg_b.id,
                "product_qty": 1,
                "product_uom_id": uom.id,
                "bom_line_ids": [(0, 0, {"product_id": material.id, "product_qty": 1, "product_uom_id": uom.id})],
            }
        )
        bom_c = Bom.create(
            {
                "product_tmpl_id": fg_c.id,
                "product_qty": 1,
                "product_uom_id": uom.id,
                "bom_line_ids": [(0, 0, {"product_id": existing_material.id, "product_qty": 1, "product_uom_id": uom.id})],
            }
        )
        batch = self.env["corr.bulk.upload.batch"].sudo().create({"name": "Shared Auto Fixed Batch"})
        Line = self.env["corr.bulk.upload.line"].sudo()
        line_a = Line.create(
            {
                "batch_id": batch.id,
                "product_tmpl_id": fg_a.id,
                "status": "auto_fixed",
                "bom_id": bom_a.id,
                "auto_created_material_variant_ids": [(6, 0, [material.id])],
            }
        )
        line_b = Line.create(
            {"batch_id": batch.id, "product_tmpl_id": fg_b.id, "status": "success", "bom_id": bom_b.id}
        )
        line_c = Line.create(
            {"batch_id": batch.id, "product_tmpl_id": fg_c.id, "status": "success", "bom_id": bom_c.id}
        )

        batch._corr_apply_batch_autofix_status()

        self.assertEqual(line_a.status, "auto_fixed")
        self.assertEqual(line_b.status, "auto_fixed")
        self.assertEqual(line_b.auto_created_material_variant_ids, material)
        self.assertEqual(line_c.status, "success")

    def test_engine_v2_routing_profile_without_stages_fails_before_preview_contract(self):
        Style = self.env["corr.engine.style"].sudo()
        style = Style.search([("code", "=", "0201")], limit=1)
        if not style:
            style = Style.create({"name": "Regular Slotted Carton", "code": "0201", "active": True})
        Plant = self.env["corr.plant.param"].sudo()
        plant = Plant.search([("code", "=", "STAB-PLANT")], limit=1)
        if not plant:
            plant = Plant.create({"code": "STAB-PLANT"})
        route = self.env["corr.engine.routing.profile"].sudo().create(
            {"name": "STAB Empty Route", "active": True}
        )
        product = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Empty Route Contract Product",
                "default_code": "STAB-EMPTY-ROUTE",
                "corr_style": style.code,
                "corr_length_mm": 600,
                "corr_width_mm": 300,
                "corr_height_mm": 180,
                "corr_ply_count": 3,
                "corr_plant_code": plant.code,
                "corr_routing_profile_code": route.name,
            }
        )

        with self.assertRaises(UserError) as raised:
            product._corr_autobom_v2_validate_inputs()

        self.assertIn("has no stage lines configured", raised.exception.args[0])
