import json
from unittest.mock import patch

from odoo.tests.common import Form, TransactionCase, tagged
from odoo.exceptions import AccessError, UserError

from odoo.addons.corr_mrp_bridge import bom_adapter as engine_v2_bom_adapter
from odoo.addons.corr_mrp_bridge.models.product_hook import ProductTemplateCorr


@tagged("corr_mrp_bridge")
class TestBulkUploadEngineFlag(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.icp = cls.env["ir.config_parameter"].sudo()
        cls.ProductTemplate = cls.env["product.template"].sudo()
        cls.Style = cls.env["corr.engine.style"].sudo()
        cls.Plant = cls.env["corr.plant.param"].sudo()
        cls.Route = cls.env["corr.engine.routing.profile"].sudo()
        cls.BoardRecipe = cls.env["corr.engine.board.recipe"].sudo()
        cls.Flute = cls.env["corr.flute"].sudo()
        cls.corr_manager_group = cls.env.ref("corrugation_formula_engine_v2.group_corr_manager")
        cls.env.user.write({"groups_id": [(4, cls.corr_manager_group.id)]})

        cls.style_0201 = cls.Style.search([("code", "=", "0201")], limit=1)
        if not cls.style_0201:
            cls.style_0201 = cls.Style.create(
                {"name": "Regular Slotted Container", "code": "0201", "family": "box", "active": True}
            )
        cls.style_diecut = cls.Style.search([("code", "=", "DIECUT_SHEET")], limit=1)
        if not cls.style_diecut:
            cls.style_diecut = cls.Style.create(
                {"name": "Custom Diecut Sheet", "code": "DIECUT_SHEET", "family": "diecut_sheet", "active": True}
            )

        cls.plant = cls.Plant.create(
            {
                "name": "Bulk Upload Plant",
                "code": "BULK_PLANT",
                "setup_waste_m2": 0.0,
                "process_waste_pct": 2.0,
                "default_glue_lap_mm": 20.0,
                "flap_basis": "fixed",
                "flap_fixed_mm": 20.0,
                "ink_gm2_at_100pct_default": 5.0,
                "lam_overlap_percent_default": 5.0,
                "default_reel_widths_mm": [1200, 1400],
                "humidity_factor": 1.0,
                "rounding_rules_json": {"kg": 3, "m": 3, "m2": 4},
                "gsm_tolerance_pct": 5.0,
                "default_ply_max": 5,
                "sheet_length_mm": 2000.0,
                "sheet_width_mm": 2000.0,
            }
        )
        cls.route = cls.Route.create({"name": "BULK_ROUTE", "active": True})
        cls.recipe = cls.BoardRecipe.create({"name": "Bulk Recipe", "code": "BULK_RECIPE", "active": True})
        cls.flute_b = cls.Flute.search([("code", "=", "B")], limit=1)
        if not cls.flute_b:
            cls.flute_b = cls.Flute.create({"name": "B", "code": "B", "caliper_mm": 2.5, "tuf": 0.05})
        cls.top_sheet_template = cls.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Bulk Top Sheet",
                "default_code": "TOP-SHEET-001",
            }
        )
        cls.top_sheet_product = cls.top_sheet_template.product_variant_id
        cls.attr_gsm = cls.env["product.attribute"].create({"name": "GSM"})
        cls.attr_bf = cls.env["product.attribute"].create({"name": "BF"})
        cls.attr_color = cls.env["product.attribute"].create({"name": "Color"})
        cls.attr_deckle = cls.env["product.attribute"].create({"name": "Deckle"})
        cls.attr_gsm_180 = cls.env["product.attribute.value"].create(
            {"name": "180", "attribute_id": cls.attr_gsm.id}
        )
        cls.attr_bf_18 = cls.env["product.attribute.value"].create(
            {"name": "18", "attribute_id": cls.attr_bf.id}
        )
        cls.attr_color_ns = cls.env["product.attribute.value"].create(
            {"name": "Natural Shade(NS)", "attribute_id": cls.attr_color.id}
        )
        cls.attr_deckle_79 = cls.env["product.attribute.value"].create(
            {"name": "79", "attribute_id": cls.attr_deckle.id}
        )
        cls.kraft_template = cls.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Kraft Paper",
                "default_code": "KRAFT-PAPER",
                "attribute_line_ids": [
                    (0, 0, {"attribute_id": cls.attr_gsm.id, "value_ids": [(6, 0, [cls.attr_gsm_180.id])]}),
                    (0, 0, {"attribute_id": cls.attr_bf.id, "value_ids": [(6, 0, [cls.attr_bf_18.id])]}),
                    (0, 0, {"attribute_id": cls.attr_color.id, "value_ids": [(6, 0, [cls.attr_color_ns.id])]}),
                    (0, 0, {"attribute_id": cls.attr_deckle.id, "value_ids": [(6, 0, [cls.attr_deckle_79.id])]}),
                ],
            }
        )
        cls.kraft_variant = cls.kraft_template.product_variant_id

    def _make_corr_product(self, **extra_vals):
        vals = {
            "name": "Bulk Upload Product",
            "corr_style": "0201",
            "corr_length_mm": 600.0,
            "corr_width_mm": 300.0,
            "corr_height_mm": 180.0,
            "corr_ply_count": 3,
        }
        vals.update(extra_vals)
        return self.ProductTemplate.with_context(skip_corr_autobom=True).create(vals)

    def _make_v2_ready_product(self, **extra_vals):
        vals = {
            "name": "V2 Validated Product",
            "corr_style": "0201",
            "corr_length_mm": 600.0,
            "corr_width_mm": 300.0,
            "corr_height_mm": 180.0,
            "corr_ply_count": 3,
            "corr_plant_code": "BULK_PLANT",
            "corr_routing_profile_code": "BULK_ROUTE",
            "corr_board_recipe_code": "BULK_RECIPE",
        }
        vals.update(extra_vals)
        return self._make_corr_product(**vals)

    def _preview_payload(self, spec, marker="ok", order_qty=1.0):
        return {
            "spec_id": spec.id,
            "plant_id": spec.plant_id.id if spec.plant_id else False,
            "style_code": spec.style_id.code if spec.style_id else False,
            "order_qty": float(order_qty),
            "bom_struct": {
                "product_qty": float(order_qty),
                "components": [],
                "operations": [],
            },
            "marker": marker,
        }

    def _store_mock_preview(self, spec, marker="ok", order_qty=1.0):
        payload = self._preview_payload(spec, marker=marker, order_qty=order_qty)
        raw_json = json.dumps(payload)
        spec.store_last_preview_snapshot(
            raw_json=raw_json,
            status="success",
            error=False,
        )
        return {
            "wizard_id": 999,
            "raw_json": raw_json,
            "preview_warning": False,
            "preview_backend_applied": "stagecontext",
        }

    def _mock_bom_result(self, bom_id=999):
        return {
            "bom_id": bom_id,
            "bom_created": True,
            "bom_reused": False,
            "bom_backend_applied": "stagecontext_shadow",
            "signature": "test-signature",
            "diagnostics": {},
        }

    def _make_fg_and_component(self, suffix):
        fg_tmpl = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Versioned FG %s" % suffix,
                "default_code": "VERSIONED-FG-%s" % suffix,
                "is_storable": True,
            }
        )
        comp_tmpl = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Versioned Component %s" % suffix,
                "default_code": "VERSIONED-COMP-%s" % suffix,
                "is_storable": True,
            }
        )
        return fg_tmpl.product_variant_id, comp_tmpl.product_variant_id

    def _versioning_bom_struct(self, component, qty=1.0):
        return {
            "product_qty": 1.0,
            "components": [
                {
                    "product": "rm_liner",
                    "qty": float(qty),
                    "metadata": {
                        "layer_no": 1,
                        "product_id": component.id,
                        "uom_id": component.uom_id.id,
                        "resolution_status": "exact",
                    },
                }
            ],
            "operations": [],
        }

    def _create_versioned_bom(self, fg_product, component, signature, qty=1.0):
        return engine_v2_bom_adapter._create_bom_from_struct(
            self.env,
            fg_product,
            self._versioning_bom_struct(component, qty=qty),
            signature=signature,
            order_qty=1.0,
        )

    def _line_snapshot(self, bom):
        return [
            (line.product_id.id, float(line.product_qty or 0.0))
            for line in bom.bom_line_ids.sorted(lambda line: line.id)
        ]

    def _create_mo_for_bom(self, product, bom, confirm=False):
        with Form(self.env["mrp.production"]) as mo_form:
            mo_form.product_id = product
            mo_form.bom_id = bom
            mo_form.product_qty = 1.0
        mo = mo_form.save()
        if confirm:
            mo.action_confirm()
        self.assertEqual(mo.bom_id, bom)
        return mo

    def test_engine_v2_same_signature_reuses_same_bom_id(self):
        self.icp.set_param("corr_mrp_bridge.engine_v2_current_bom_selection_enabled", "1")
        fg_product, component = self._make_fg_and_component("SAME")

        bom_1 = self._create_versioned_bom(fg_product, component, "sig-same", qty=1.0)
        bom_2 = self._create_versioned_bom(fg_product, component, "sig-same", qty=1.0)

        self.assertEqual(bom_2.id, bom_1.id)
        self.assertEqual(
            self.env["mrp.bom"].search_count(
                [("product_tmpl_id", "=", fg_product.product_tmpl_id.id), ("corr_engine_v2_signature", "=", "sig-same")]
            ),
            1,
        )
        self.assertEqual(fg_product.product_tmpl_id.corr_current_engine_v2_bom_id, bom_1)

    def test_engine_v2_changed_signature_reuses_unused_current_bom(self):
        self.icp.set_param("corr_mrp_bridge.engine_v2_current_bom_selection_enabled", "1")
        fg_product, component = self._make_fg_and_component("UNUSED")

        bom_1 = self._create_versioned_bom(fg_product, component, "sig-unused-old", qty=1.0)
        old_bom_id = bom_1.id
        bom_2 = self._create_versioned_bom(fg_product, component, "sig-unused-new", qty=2.0)

        self.assertEqual(bom_2.id, old_bom_id)
        self.assertEqual(bom_2.corr_engine_v2_signature, "sig-unused-new")
        self.assertEqual(self._line_snapshot(bom_2), [(component.id, 2.0)])
        self.assertEqual(fg_product.product_tmpl_id.corr_current_engine_v2_bom_id, bom_2)

    def test_engine_v2_changed_signature_creates_new_bom_when_old_bom_has_draft_mo(self):
        self.icp.set_param("corr_mrp_bridge.engine_v2_current_bom_selection_enabled", "1")
        fg_product, component = self._make_fg_and_component("DRAFT-MO")

        old_bom = self._create_versioned_bom(fg_product, component, "sig-draft-old", qty=1.0)
        old_snapshot = self._line_snapshot(old_bom)
        mo = self._create_mo_for_bom(fg_product, old_bom)
        new_bom = self._create_versioned_bom(fg_product, component, "sig-draft-new", qty=3.0)

        self.assertNotEqual(new_bom.id, old_bom.id)
        self.assertEqual(mo.bom_id, old_bom)
        self.assertEqual(old_bom.corr_engine_v2_signature, "sig-draft-old")
        self.assertEqual(self._line_snapshot(old_bom), old_snapshot)
        self.assertFalse(old_bom.corr_is_current_engine_v2_bom)
        self.assertEqual(old_bom.corr_superseded_by_bom_id, new_bom)
        self.assertTrue(new_bom.corr_is_current_engine_v2_bom)
        self.assertEqual(fg_product.product_tmpl_id.corr_current_engine_v2_bom_id, new_bom)

    def test_engine_v2_changed_signature_creates_new_bom_when_old_bom_has_confirmed_mo(self):
        self.icp.set_param("corr_mrp_bridge.engine_v2_current_bom_selection_enabled", "1")
        fg_product, component = self._make_fg_and_component("CONFIRMED-MO")

        old_bom = self._create_versioned_bom(fg_product, component, "sig-confirmed-old", qty=1.0)
        old_snapshot = self._line_snapshot(old_bom)
        mo = self._create_mo_for_bom(fg_product, old_bom, confirm=True)
        new_bom = self._create_versioned_bom(fg_product, component, "sig-confirmed-new", qty=4.0)

        self.assertNotEqual(new_bom.id, old_bom.id)
        self.assertEqual(mo.bom_id, old_bom)
        self.assertEqual(old_bom.corr_engine_v2_signature, "sig-confirmed-old")
        self.assertEqual(self._line_snapshot(old_bom), old_snapshot)
        self.assertEqual(old_bom.corr_superseded_by_bom_id, new_bom)
        self.assertEqual(fg_product.product_tmpl_id.corr_current_engine_v2_bom_id, new_bom)

    def test_engine_v2_future_manual_mo_selects_new_current_bom(self):
        self.icp.set_param("corr_mrp_bridge.engine_v2_current_bom_selection_enabled", "1")
        fg_product, component = self._make_fg_and_component("MANUAL-MO")

        old_bom = self._create_versioned_bom(fg_product, component, "sig-manual-old", qty=1.0)
        self._create_mo_for_bom(fg_product, old_bom)
        new_bom = self._create_versioned_bom(fg_product, component, "sig-manual-new", qty=2.0)

        with Form(self.env["mrp.production"]) as mo_form:
            mo_form.product_id = fg_product
            mo_form.product_qty = 1.0
        future_mo = mo_form.save()

        self.assertEqual(future_mo.bom_id, new_bom)

    def test_engine_v2_procurement_matching_prefers_new_current_bom(self):
        self.icp.set_param("corr_mrp_bridge.engine_v2_current_bom_selection_enabled", "1")
        fg_product, component = self._make_fg_and_component("PROCUREMENT")

        old_bom = self._create_versioned_bom(fg_product, component, "sig-proc-old", qty=1.0)
        self._create_mo_for_bom(fg_product, old_bom)
        new_bom = self._create_versioned_bom(fg_product, component, "sig-proc-new", qty=2.0)

        rule = self.env["stock.rule"].search([("action", "=", "manufacture")], limit=1)
        self.assertTrue(rule, "expected at least one manufacture stock rule")
        matched_bom = rule._get_matching_bom(fg_product, self.env.company, {})

        self.assertEqual(matched_bom, new_bom)

    def test_engine_v2_failed_new_bom_generation_keeps_old_current(self):
        self.icp.set_param("corr_mrp_bridge.engine_v2_current_bom_selection_enabled", "1")
        fg_product, component = self._make_fg_and_component("FAILED")

        old_bom = self._create_versioned_bom(fg_product, component, "sig-failed-old", qty=1.0)
        self._create_mo_for_bom(fg_product, old_bom)
        old_snapshot = self._line_snapshot(old_bom)

        with self.assertRaises(UserError):
            engine_v2_bom_adapter._create_bom_from_struct(
                self.env,
                fg_product,
                {
                    "product_qty": 1.0,
                    "components": [{"product": "rm_liner", "qty": 1.0, "metadata": {"layer_no": 1}}],
                    "operations": [],
                },
                signature="sig-failed-new",
                order_qty=1.0,
                paper_resolution_policy="strict_required",
            )

        self.assertEqual(fg_product.product_tmpl_id.corr_current_engine_v2_bom_id, old_bom)
        self.assertTrue(old_bom.corr_is_current_engine_v2_bom)
        self.assertEqual(old_bom.corr_engine_v2_signature, "sig-failed-old")
        self.assertEqual(self._line_snapshot(old_bom), old_snapshot)

    def test_engine_v2_repeated_same_upload_does_not_duplicate_boms(self):
        self.icp.set_param("corr_mrp_bridge.engine_v2_current_bom_selection_enabled", "1")
        fg_product, component = self._make_fg_and_component("REPEAT")

        for _index in range(3):
            self._create_versioned_bom(fg_product, component, "sig-repeat", qty=1.0)

        boms = self.env["mrp.bom"].search(
            [
                ("product_tmpl_id", "=", fg_product.product_tmpl_id.id),
                ("corr_engine_v2_signature", "=", "sig-repeat"),
            ]
        )
        self.assertEqual(len(boms), 1)

    def test_friendly_import_headers_map_to_technical_fields(self):
        mapped_fields, mapped_data, warnings, source_map = self.ProductTemplate._corr_apply_import_header_aliases(
            ["Product Code", "Product Name", "Style / FEFCO", "FG per Sheet", "Layer   1 - GSM", "corr_layer1_flute"],
            [["P-001", "Friendly Box", "DIECUT_SHEET", 2, 180, "B"]],
        )

        self.assertEqual(
            mapped_fields,
            [
                "default_code",
                "name",
                "corr_style",
                "corr_fg_per_sheet",
                "corr_layer1_gsm",
                "corr_layer1_flute_code",
            ],
        )
        self.assertEqual(mapped_data, [["P-001", "Friendly Box", "DIECUT_SHEET", 2, 180, "B"]])
        self.assertEqual(warnings, [])
        self.assertEqual(source_map["corr_style"], "Style / FEFCO")

    def test_duplicate_import_alias_columns_warn_and_keep_technical_field(self):
        mapped_fields, mapped_data, warnings, source_map = self.ProductTemplate._corr_apply_import_header_aliases(
            ["FG per Sheet", "corr_fg_per_sheet", "Layer 1 GSM"],
            [[1, 2, 180]],
        )

        self.assertEqual(mapped_fields, ["corr_fg_per_sheet", "corr_layer1_gsm"])
        self.assertEqual(mapped_data, [[2, 180]])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Duplicate import columns map to 'corr_fg_per_sheet'", warnings[0])
        self.assertEqual(source_map["corr_fg_per_sheet"], "corr_fg_per_sheet")

    def test_flag_off_dispatches_legacy_branch(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "0")
        product = self._make_corr_product(name="Legacy Branch Product")

        with patch.object(ProductTemplateCorr, "_corr_autobom_legacy_maybe", autospec=True, return_value="legacy") as legacy, \
             patch.object(ProductTemplateCorr, "_corr_autobom_v2_maybe", autospec=True, return_value="v2") as v2:
            result = product._corr_autobom_maybe()

        self.assertEqual(result, "legacy")
        legacy.assert_called_once_with(product)
        v2.assert_not_called()

    def test_flag_on_dispatches_v2_branch(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_corr_product(name="V2 Branch Product")

        with patch.object(ProductTemplateCorr, "_corr_autobom_legacy_maybe", autospec=True, return_value="legacy") as legacy, \
             patch.object(ProductTemplateCorr, "_corr_autobom_v2_maybe", autospec=True, return_value="v2") as v2:
            result = product._corr_autobom_maybe()

        self.assertEqual(result, "v2")
        v2.assert_called_once_with(product)
        legacy.assert_not_called()

    def test_spec_details_button_uses_active_engine_v2_without_legacy_fallback(self):
        self.icp.set_param("corr_mrp_bridge.active_engine", "v2")
        product = self._make_corr_product(name="V2 Spec Details Product")
        v2_spec = self.env["corr.engine.v2.spec"].sudo().create(
            {
                "name": "V2 Spec Details Spec",
                "product_tmpl_id": product.id,
            }
        )
        self.env["corr.spec.template"].sudo().with_context(skip_corr_stack_check=True).create(
            {
                "name": "Legacy Spec Should Not Open",
                "product_tmpl_id": product.id,
                "length_mm": 600.0,
                "width_mm": 300.0,
                "height_mm": 180.0,
                "ply_count": 3,
            }
        )

        action = product.action_corr_compute_preview()

        self.assertEqual(action["res_model"], "corr.engine.v2.spec")
        self.assertEqual(action["res_id"], v2_spec.id)

    def test_spec_details_button_uses_active_engine_old_without_v2_fallback(self):
        self.icp.set_param("corr_mrp_bridge.active_engine", "old")
        product = self._make_corr_product(name="Old Spec Details Product")
        legacy_spec = self.env["corr.spec.template"].sudo().with_context(skip_corr_stack_check=True).create(
            {
                "name": "Legacy Spec Details Spec",
                "product_tmpl_id": product.id,
                "length_mm": 600.0,
                "width_mm": 300.0,
                "height_mm": 180.0,
                "ply_count": 3,
            }
        )
        self.env["corr.engine.v2.spec"].sudo().create(
            {
                "name": "V2 Spec Should Not Open",
                "product_tmpl_id": product.id,
            }
        )

        action = product.action_corr_compute_preview()

        self.assertEqual(action["res_model"], "corr.spec.template")
        self.assertEqual(action["res_id"], legacy_spec.id)

    def test_import_upsert_reactivates_archived_product_when_matching_default_code(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        archived = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Archived Match",
                "default_code": "ARCH-UPSERT-001",
                "active": False,
            }
        )

        created = self.ProductTemplate.with_context(import_file=True, skip_corr_autobom=True).create(
            [
                {
                    "name": "Archived Match Updated",
                    "default_code": "ARCH-UPSERT-001",
                    "corr_style": "0201",
                    "corr_length_mm": 600.0,
                    "corr_width_mm": 300.0,
                    "corr_height_mm": 180.0,
                    "corr_ply_count": 3,
                }
            ]
        )

        self.assertEqual(created.id, archived.id)
        archived.invalidate_recordset()
        self.assertTrue(archived.active)
        self.assertEqual(archived.name, "Archived Match Updated")

    def test_valid_style_plant_routing_board_recipe_passes_validation(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product()

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: self._store_mock_preview(spec, marker="ok"),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
            return_value=self._mock_bom_result(5001),
        ):
            result = product._corr_autobom_maybe()
        product.invalidate_recordset()
        spec = self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1)

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["spec_id"])
        self.assertTrue(result["spec_created"])
        self.assertEqual(result["bom_id"], 5001)
        self.assertEqual(product.corr_autobom_status, "success")
        self.assertEqual(product.corr_autobom_backend, "v2")
        self.assertEqual(product.corr_autobom_message, "Engine V2 spec, preview, and BoM created/updated successfully.")
        self.assertEqual(product.corr_autobom_spec_id, spec.id)
        self.assertEqual(product.corr_autobom_preview_status, "success")
        self.assertEqual(product.corr_autobom_bom_status, "success")
        self.assertFalse(product.corr_autobom_error_stage)
        self.assertIn('"bom_id": 5001', product.corr_autobom_error_payload or "")
        self.assertFalse(self.env["corr.spec.template"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1))
        self.assertTrue(spec)
        self.assertEqual(spec.product_tmpl_id.id, product.id)
        self.assertEqual(spec.style_id.code, "0201")
        self.assertEqual(spec.plant_id.id, self.plant.id)
        self.assertEqual(spec.routing_profile_id.id, self.route.id)
        self.assertEqual(spec.board_recipe_id.id, self.recipe.id)
        self.assertEqual(spec.last_preview_status, "success")
        self.assertTrue(spec.last_preview_generated_at)
        self.assertIn('"marker": "ok"', spec.last_preview_raw_json or "")
        self.assertFalse(spec.last_preview_error)

    def test_invalid_style_fails(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(corr_style="BAD_STYLE")

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "validation_failed")
        self.assertEqual(product.corr_autobom_backend, "v2")
        self.assertEqual(product.corr_autobom_error_stage, "validation")
        self.assertEqual(product.corr_autobom_bom_status, "pending")
        self.assertIn("Invalid style code/name 'BAD_STYLE' from column 'corr_style'", product.corr_autobom_message)
        self.assertIn("Looked up by code, then unique name.", product.corr_autobom_message)
        self.assertIn("DIECUT_SHEET", product.corr_autobom_message)

    def test_style_lookup_accepts_case_insensitive_engine_code(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(corr_style="diecut_sheet", corr_height_mm=0.0, corr_fg_per_sheet=1)

        style = product._corr_find_v2_style()

        self.assertTrue(style)
        self.assertEqual(style.code, "DIECUT_SHEET")

    def test_invalid_style_message_uses_friendly_import_header(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(corr_style="ABC").with_context(
            corr_import_field_sources={"corr_style": "Style / FEFCO"}
        )

        with self.assertRaises(UserError) as ctx:
            product._corr_autobom_v2_validate_inputs()

        message = str(ctx.exception)
        self.assertIn("Invalid style code/name 'ABC' from column 'Style / FEFCO'", message)
        self.assertIn("Available style codes:", message)
        self.assertIn("DIECUT_SHEET", message)

    def test_missing_plant_fails(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(corr_plant_code=False)

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "validation_failed")
        self.assertEqual(product.corr_autobom_error_stage, "validation")
        self.assertEqual(product.corr_autobom_message, "Missing or invalid plant code.")

    def test_missing_routing_profile_fails(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(corr_routing_profile_code=False)

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "validation_failed")
        self.assertEqual(product.corr_autobom_message, "Missing or invalid routing profile code.")

    def test_missing_board_recipe_and_no_layer_lines_fails(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(corr_board_recipe_code=False)

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "validation_failed")
        self.assertEqual(product.corr_autobom_message, "Missing board recipe or board structure.")

    def test_board_recipe_missing_material_variant_blocks_before_preview(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        missing_template = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Kraft Paper Missing 160 Variant",
                "default_code": "KRAFT-MISSING-160",
            }
        )
        recipe = self.BoardRecipe.create(
            {
                "name": "Missing 160 Recipe",
                "code": "MISSING_160_RECIPE",
                "active": True,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": 10,
                            "role": "medium",
                            "gsm": 160.0,
                            "bf": 18.0,
                            "color": "Natural Shade(NS)",
                            "flute_type_id": self.flute_b.id,
                            "material_template_id": missing_template.id,
                        },
                    )
                ],
            }
        )
        product = self._make_v2_ready_product(corr_board_recipe_code=recipe.code)

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "material_resolution_failed")
        self.assertEqual(product.corr_autobom_error_stage, "material_resolution")
        self.assertIn("No material variant found for Layer 1", product.corr_autobom_message or "")
        self.assertFalse(self.env["mrp.bom"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1))

    def test_preview_payload_missing_paper_product_blocks_bom_creation(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(name="V2 Missing Preview Product")
        spec, _created = product._corr_upsert_v2_spec(product._corr_autobom_v2_validate_inputs())
        payload = self._preview_payload(spec, marker="missing-paper")
        payload["paper_resolution"] = [
            {
                "role": "medium",
                "layer_no": 1,
                "status": "missing",
                "product_id": None,
                "material_template_name": "Kraft Paper",
                "gsm": 160.0,
                "bf": 18.0,
                "color": "Natural Shade(NS)",
                "resolution_note": "Exact paper variant not found for selected layout",
            }
        ]
        spec.store_last_preview_snapshot(raw_json=json.dumps(payload), status="success", error=False)

        with self.assertRaises(UserError) as ctx:
            product._corr_create_v2_bom_from_persisted_preview(spec)

        self.assertIn("No material variant found for Layer 1", str(ctx.exception))

    def test_exact_board_recipe_variant_populates_material_product(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        recipe = self.BoardRecipe.create(
            {
                "name": "Exact 180 Recipe",
                "code": "EXACT_180_RECIPE",
                "active": True,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": 10,
                            "role": "inner_liner",
                            "gsm": 180.0,
                            "bf": 18.0,
                            "color": "Natural Shade(NS)",
                            "material_template_id": self.kraft_template.id,
                        },
                    )
                ],
            }
        )
        product = self._make_v2_ready_product(corr_board_recipe_code=recipe.code)
        spec, _created = product._corr_upsert_v2_spec(product._corr_autobom_v2_validate_inputs())

        product._corr_validate_v2_spec_material_resolution(spec)
        line = spec.stack_line_ids[:1]

        self.assertEqual(line.resolved_product_id.id, self.kraft_variant.id)
        self.assertEqual(line.material_product_id.id, self.kraft_variant.id)

    def test_diecut_sheet_missing_fg_per_sheet_fails(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(
            corr_style="DIECUT_SHEET",
            corr_height_mm=0.0,
            corr_fg_per_sheet=0,
        )

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "validation_failed")
        self.assertEqual(product.corr_autobom_message, "DIECUT_SHEET requires FG per Sheet as an integer >= 1.")

    def test_diecut_sheet_fg_per_sheet_two_passes_validation(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(
            corr_style="DIECUT_SHEET",
            corr_height_mm=0.0,
            corr_fg_per_sheet=2,
        )

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: self._store_mock_preview(spec, marker="diecut"),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
            return_value=self._mock_bom_result(5002),
        ):
            result = product._corr_autobom_maybe()
        product.invalidate_recordset()
        spec = self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["fg_per_sheet"], 2)
        self.assertEqual(product.corr_autobom_status, "success")
        self.assertTrue(spec)
        self.assertEqual(spec.fg_per_sheet, 2)

    def test_v2_branch_updates_existing_spec_without_duplicate(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(name="V2 Upsert Product")

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: self._store_mock_preview(spec, marker="update"),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
            return_value=self._mock_bom_result(5003),
        ):
            first = product._corr_autobom_maybe()
        spec = self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1)
        self.assertTrue(spec)
        first_spec_id = spec.id
        self.assertTrue(first["spec_created"])

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: self._store_mock_preview(spec, marker="updated-manual"),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
            return_value=self._mock_bom_result(5004),
        ):
            product.write(
                {
                    "corr_fg_per_sheet": 2,
                    "corr_board_recipe_code": False,
                    "corr_layer1_role": "medium",
                    "corr_layer1_gsm": 120.0,
                    "corr_layer1_flute_code": "B",
                    "corr_layer2_role": "liner",
                    "corr_layer2_gsm": 180.0,
                    "corr_layer2_color": "NS",
                }
            )
        product.invalidate_recordset()
        spec.invalidate_recordset()
        spec = self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1)

        self.assertEqual(spec.id, first_spec_id)
        self.assertEqual(spec.fg_per_sheet, 2)
        self.assertFalse(spec.board_recipe_id)

    def test_manual_layer_import_populates_value_ids_and_resolved_variant(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(
            name="V2 Manual Layer Value IDs",
            corr_board_recipe_code=False,
            corr_layer1_role="liner",
            corr_layer1_gsm=180.0,
            corr_layer1_bf=18.0,
            corr_layer1_color="Natural Shade(NS)",
            corr_layer1_material_template="KRAFT-PAPER",
        )

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: self._store_mock_preview(spec, marker="layer-values"),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
            return_value=self._mock_bom_result(5010),
        ):
            result = product._corr_autobom_maybe()
        spec = self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1)
        layer = spec.stack_line_ids[:1]

        self.assertEqual(result["status"], "success")
        self.assertTrue(layer)
        self.assertEqual(layer.material_template_id.id, self.kraft_template.id)
        self.assertEqual(layer.gsm_value_id.id, self.attr_gsm_180.id)
        self.assertEqual(layer.bf_value_id.id, self.attr_bf_18.id)
        self.assertEqual(layer.color_value_id.id, self.attr_color_ns.id)
        self.assertEqual(layer.material_product_id.id, self.kraft_variant.id)
        self.assertEqual(layer.resolved_product_id.id, self.kraft_variant.id)

    def test_diecut_sheet_validation_uses_deckle_to_resolve_exact_variant(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        attr_gsm_140 = self.env["product.attribute.value"].create(
            {"name": "140", "attribute_id": self.attr_gsm.id}
        )
        attr_deckle_100 = self.env["product.attribute.value"].create(
            {"name": "100", "attribute_id": self.attr_deckle.id}
        )
        deckle_template = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Deckle Kraft Paper",
                "default_code": "DECKLE-KRAFT",
                "attribute_line_ids": [
                    (0, 0, {"attribute_id": self.attr_gsm.id, "value_ids": [(6, 0, [attr_gsm_140.id])]}),
                    (0, 0, {"attribute_id": self.attr_bf.id, "value_ids": [(6, 0, [self.attr_bf_18.id])]}),
                    (0, 0, {"attribute_id": self.attr_color.id, "value_ids": [(6, 0, [self.attr_color_ns.id])]}),
                    (0, 0, {"attribute_id": self.attr_deckle.id, "value_ids": [(6, 0, [self.attr_deckle_79.id, attr_deckle_100.id])]}),
                ],
            }
        )
        target_variant = deckle_template.product_variant_ids.filtered(
            lambda p: "79" in p.product_template_attribute_value_ids.mapped("name")
        )[:1]
        self.assertTrue(target_variant)
        product = self._make_v2_ready_product(
            name="V2 Deckle Exact Variant",
            corr_style="DIECUT_SHEET",
            corr_height_mm=0.0,
            corr_width_mm=790.0,
            corr_fg_per_sheet=1,
            corr_forced_ups=1,
            corr_board_recipe_code=False,
            corr_layer1_role="liner",
            corr_layer1_gsm=140.0,
            corr_layer1_bf=18.0,
            corr_layer1_color="Natural Shade(NS)",
            corr_layer1_material_template=deckle_template.default_code,
        )

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: self._store_mock_preview(spec, marker="deckle-exact"),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
            return_value=self._mock_bom_result(5011),
        ):
            result = product._corr_autobom_maybe()
        spec = self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1)
        layer = spec.stack_line_ids[:1]

        self.assertEqual(result["status"], "success")
        self.assertTrue(layer)
        self.assertEqual(layer.material_product_id, target_variant)

    def test_manual_layer_import_blocks_when_template_attrs_do_not_resolve_unique_variant(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        attr_color_gs = self.env["product.attribute.value"].create(
            {"name": "Golden Shade(GS)", "attribute_id": self.attr_color.id}
        )
        ambiguous_template = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Ambiguous Kraft Paper",
                "default_code": "AMBIG-KRAFT",
                "attribute_line_ids": [
                    (0, 0, {"attribute_id": self.attr_gsm.id, "value_ids": [(6, 0, [self.attr_gsm_180.id])]}),
                    (0, 0, {"attribute_id": self.attr_bf.id, "value_ids": [(6, 0, [self.attr_bf_18.id])]}),
                    (0, 0, {"attribute_id": self.attr_color.id, "value_ids": [(6, 0, [self.attr_color_ns.id, attr_color_gs.id])]}),
                ],
            }
        )
        product = self._make_v2_ready_product(
            name="V2 Ambiguous Layer Variant",
            corr_board_recipe_code=False,
            corr_layer1_role="liner",
            corr_layer1_gsm=180.0,
            corr_layer1_bf=18.0,
            corr_layer1_material_template=ambiguous_template.default_code,
        )

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "validation_failed")
        self.assertIn("Multiple material variants matched corr_layer1", product.corr_autobom_message or "")
        self.assertIn("Material Template = Ambiguous Kraft Paper", product.corr_autobom_message or "")

    def test_diecut_sheet_validation_message_includes_derived_deckle(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        attr_color_gs = self.env["product.attribute.value"].create(
            {"name": "Golden Shade Stage2", "attribute_id": self.attr_color.id}
        )
        ambiguous_template = self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Ambiguous Diecut Kraft",
                "default_code": "AMBIG-DIECUT-KRAFT",
                "attribute_line_ids": [
                    (0, 0, {"attribute_id": self.attr_gsm.id, "value_ids": [(6, 0, [self.attr_gsm_180.id])]}),
                    (0, 0, {"attribute_id": self.attr_bf.id, "value_ids": [(6, 0, [self.attr_bf_18.id])]}),
                    (0, 0, {"attribute_id": self.attr_color.id, "value_ids": [(6, 0, [self.attr_color_ns.id, attr_color_gs.id])]}),
                ],
            }
        )
        product = self._make_v2_ready_product(
            name="V2 Ambiguous Diecut Variant",
            corr_style="DIECUT_SHEET",
            corr_height_mm=0.0,
            corr_width_mm=790.0,
            corr_fg_per_sheet=1,
            corr_forced_ups=1,
            corr_board_recipe_code=False,
            corr_layer1_role="liner",
            corr_layer1_gsm=180.0,
            corr_layer1_bf=18.0,
            corr_layer1_color="Natural Shade(NS)",
            corr_layer1_material_template=ambiguous_template.default_code,
        )

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "validation_failed")
        self.assertIn("Deckle = 79.0 cm", product.corr_autobom_message or "")
        self.assertIn("Multiple material variants matched corr_layer1", product.corr_autobom_message or "")

    def test_unit_sheet_top_sheet_code_maps_to_product(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(
            name="V2 Unit Sheet Top",
            corr_top_layer_mode="Unit Sheet",
            corr_top_sheet_code="TOP-SHEET-001",
        )

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: self._store_mock_preview(spec, marker="top-unit"),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
            return_value=self._mock_bom_result(5005),
        ):
            result = product._corr_autobom_maybe()
        spec = self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1)

        self.assertEqual(result["status"], "success")
        self.assertTrue(spec)
        self.assertEqual(spec.surface_top_sheet_mode, "unit_sheet")
        self.assertEqual(spec.surface_top_sheet_product_id.id, self.top_sheet_product.id)
        self.assertFalse(spec.surface_top_sheet_roll_code)

    def test_roll_top_sheet_code_maps_to_roll_code(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(
            name="V2 Roll Top",
            corr_top_layer_mode="Roll",
            corr_top_sheet_roll_code="ROLL-TOP-001",
        )

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: self._store_mock_preview(spec, marker="top-roll"),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
            return_value=self._mock_bom_result(5006),
        ):
            result = product._corr_autobom_maybe()
        spec = self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1)

        self.assertEqual(result["status"], "success")
        self.assertTrue(spec)
        self.assertEqual(spec.surface_top_sheet_mode, "roll")
        self.assertFalse(spec.surface_top_sheet_product_id)
        self.assertEqual(spec.surface_top_sheet_roll_code, "ROLL-TOP-001")

    def test_invalid_top_sheet_mode_fails(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(
            name="V2 Bad Top Mode",
            corr_top_layer_mode="Bad Mode",
        )

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "validation_failed")
        self.assertEqual(product.corr_autobom_message, "Invalid top sheet mode 'Bad Mode'. Use None, Roll, or Unit Sheet.")

    def test_unit_sheet_mode_missing_product_fails(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(
            name="V2 Unit Missing Product",
            corr_top_layer_mode="Unit",
        )

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "validation_failed")
        self.assertEqual(
            product.corr_autobom_message,
            "Top Sheet Mode 'Unit Sheet' requires a valid top sheet product or code.",
        )

    def test_roll_mode_missing_code_fails(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(
            name="V2 Roll Missing Code",
            corr_top_layer_mode="Roll",
        )

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "validation_failed")
        self.assertEqual(
            product.corr_autobom_message,
            "Top Sheet Mode 'Roll' requires a top sheet roll code.",
        )
        self.assertTrue(spec.is_board_customized)
        self.assertEqual(len(spec.stack_line_ids), 2)
        first_line = spec.stack_line_ids.sorted(lambda line: (line.sequence, line.id))[0]
        second_line = spec.stack_line_ids.sorted(lambda line: (line.sequence, line.id))[1]
        self.assertEqual(first_line.role, "medium")
        self.assertEqual(first_line.flute_type_id.code, "B")
        self.assertEqual(second_line.role, "inner_liner")
        self.assertEqual(second_line.color, "NS")
        self.assertEqual(spec.last_preview_status, "success")
        self.assertIn('"marker": "updated-manual"', spec.last_preview_raw_json or "")

    def test_import_reupload_same_default_code_updates_existing_product_and_spec(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        ProductTemplate = self.ProductTemplate.with_context(import_file=True)

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: self._store_mock_preview(spec, marker=rec.default_code),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
            return_value=self._mock_bom_result(5005),
        ):
            first = ProductTemplate.create(
                {
                    "name": "V2 Stage3 Test - 0201 Recipe",
                    "default_code": "V2-S3-0201-001",
                    "corr_style": "0201",
                    "corr_length_mm": 600.0,
                    "corr_width_mm": 300.0,
                    "corr_height_mm": 180.0,
                    "corr_ply_count": 3,
                    "corr_plant_code": "BULK_PLANT",
                    "corr_routing_profile_code": "BULK_ROUTE",
                    "corr_board_recipe_code": "BULK_RECIPE",
                }
            )
        first_spec = self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", first.id)], limit=1)
        self.assertTrue(first_spec)

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: self._store_mock_preview(spec, marker=rec.name),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
            return_value=self._mock_bom_result(5005),
        ):
            second = ProductTemplate.create(
                {
                    "name": "V2 Stage3 Test - 0201 Recipe Updated",
                    "default_code": "V2-S3-0201-001",
                    "corr_style": "0201",
                    "corr_length_mm": 610.0,
                    "corr_width_mm": 305.0,
                    "corr_height_mm": 185.0,
                    "corr_ply_count": 3,
                    "corr_plant_code": "BULK_PLANT",
                    "corr_routing_profile_code": "BULK_ROUTE",
                    "corr_board_recipe_code": "BULK_RECIPE",
                }
            )

        self.assertEqual(first.id, second.id)

        templates = self.ProductTemplate.search([("default_code", "=", "V2-S3-0201-001")])
        self.assertEqual(len(templates), 1)
        product = templates[:1]
        self.assertEqual(product.name, "V2 Stage3 Test - 0201 Recipe Updated")
        self.assertEqual(product.corr_length_mm, 610.0)

        specs = self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", product.id)])
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[:1].id, first_spec.id)
        self.assertEqual(specs[:1].box_length_mm, 610.0)
        self.assertEqual(specs[:1].box_width_mm, 305.0)
        self.assertEqual(specs[:1].last_preview_status, "success")
        self.assertIn('"marker": "V2 Stage3 Test - 0201 Recipe Updated"', specs[:1].last_preview_raw_json or "")

    def test_preview_failure_is_persisted_on_spec_and_product(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(name="V2 Preview Failure Product")

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: (
                spec.store_last_preview_snapshot(
                    raw_json=False,
                    status="failed",
                    error="Synthetic preview failure",
                )
                or (_ for _ in ()).throw(Exception("Synthetic preview failure"))
            ),
        ):
            result = product._corr_autobom_maybe()
        product.invalidate_recordset()
        spec = self.env["corr.engine.v2.spec"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1)

        self.assertFalse(result)
        self.assertTrue(spec)
        self.assertEqual(product.corr_autobom_status, "preview_failed")
        self.assertEqual(product.corr_autobom_backend, "v2")
        self.assertEqual(product.corr_autobom_error_stage, "preview")
        self.assertEqual(product.corr_autobom_spec_id, spec.id)
        self.assertEqual(product.corr_autobom_preview_status, "failed")
        self.assertIn("Synthetic preview failure", product.corr_autobom_message or "")
        self.assertEqual(spec.last_preview_status, "failed")
        self.assertEqual(spec.last_preview_error, "Synthetic preview failure")

    def test_unknown_flute_code_fails(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(
            corr_board_recipe_code=False,
            corr_layer1_role="medium",
            corr_layer1_gsm=120.0,
            corr_layer1_flute_code="ZZZ",
        )

        result = product._corr_autobom_maybe()
        product.invalidate_recordset()

        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "validation_failed")
        self.assertEqual(product.corr_autobom_error_stage, "validation")
        self.assertIn("Unknown flute code 'ZZZ' on corr_layer1.", product.corr_autobom_message or "")

    def test_preview_failure_does_not_attempt_bom_creation(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(name="V2 Preview Failure No BoM")

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=UserError("Preview failed: Synthetic preview failure"),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
        ) as bom_create:
            result = product._corr_autobom_maybe()

        self.assertFalse(result)
        bom_create.assert_not_called()
        product.invalidate_recordset()
        self.assertEqual(product.corr_autobom_status, "preview_failed")
        self.assertEqual(product.corr_autobom_error_stage, "preview")

    def test_spec_upsert_failure_sets_structured_stage(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(name="V2 Spec Upsert Failure")

        with patch.object(
            ProductTemplateCorr,
            "_corr_upsert_v2_spec",
            autospec=True,
            side_effect=Exception("Synthetic spec sync failure"),
        ):
            result = product._corr_autobom_maybe()

        product.invalidate_recordset()
        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "spec_upsert_failed")
        self.assertEqual(product.corr_autobom_error_stage, "spec_upsert")
        self.assertIn("Synthetic spec sync failure", product.corr_autobom_message or "")

    def test_material_resolution_failure_is_classified_from_preview_message(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(name="V2 Material Resolution Failure")

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=UserError("Preview failed: no exact deckle roll found for required width"),
        ):
            result = product._corr_autobom_maybe()

        product.invalidate_recordset()
        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "material_resolution_failed")
        self.assertEqual(product.corr_autobom_error_stage, "material_resolution")
        self.assertIn("no exact deckle roll found", product.corr_autobom_message or "")

    def test_bom_write_failure_sets_structured_stage(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(name="V2 BoM Write Failure")

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: self._store_mock_preview(spec, marker="bom-write"),
        ), patch.object(
            ProductTemplateCorr,
            "_corr_create_v2_bom_from_persisted_preview",
            autospec=True,
            side_effect=UserError("BoM write failed: unable to create/update BoM line"),
        ):
            result = product._corr_autobom_maybe()

        product.invalidate_recordset()
        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "bom_write_failed")
        self.assertEqual(product.corr_autobom_error_stage, "bom_write")
        self.assertEqual(product.corr_autobom_preview_status, "success")
        self.assertEqual(product.corr_autobom_bom_status, "failed")

    def test_missing_persisted_preview_payload_marks_product_failed(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(name="V2 Missing Persisted Preview")

        with patch.object(
            ProductTemplateCorr,
            "_corr_run_v2_preview_and_persist",
            autospec=True,
            side_effect=lambda rec, spec: (
                spec.store_last_preview_snapshot(raw_json=False, status="success", error=False)
                or {
                    "wizard_id": 1005,
                    "raw_json": False,
                    "preview_warning": False,
                    "preview_backend_applied": "stagecontext",
                }
            ),
        ):
            result = product._corr_autobom_maybe()

        product.invalidate_recordset()
        self.assertFalse(result)
        self.assertEqual(product.corr_autobom_status, "bom_materialization_failed")
        self.assertEqual(product.corr_autobom_backend, "v2")
        self.assertEqual(product.corr_autobom_error_stage, "bom_materialization")
        self.assertEqual(product.corr_autobom_preview_status, "success")
        self.assertEqual(product.corr_autobom_bom_status, "failed")
        self.assertEqual(product.corr_autobom_message, "Persisted preview payload is missing.")

    def test_create_v2_bom_uses_persisted_preview_payload(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(name="V2 Persisted Preview BoM")
        spec, _created = product._corr_upsert_v2_spec(product._corr_autobom_v2_validate_inputs())
        payload = self._preview_payload(spec, marker="persisted-preview")
        spec.store_last_preview_snapshot(
            raw_json=json.dumps(payload),
            status="success",
            error=False,
        )

        captured = {}

        def _fake_action(wizard):
            captured["payload"] = json.loads(wizard.raw_json or "{}")
            captured["fg_product_id"] = wizard.v2_spec_id.product_tmpl_id.product_variant_id.id
            captured["bom_backend"] = wizard.bom_backend
            bom = self.env["mrp.bom"].sudo().create(
                {
                    "product_tmpl_id": product.id,
                    "product_id": product.product_variant_id.id,
                    "product_qty": 1.0,
                    "type": "normal",
                }
            )
            return {
                "type": "ir.actions.act_window",
                "name": "Engine V2 Materialized BoM",
                "res_model": "mrp.bom",
                "view_mode": "form",
                "res_id": bom.id,
                "target": "current",
            }

        with patch(
            "odoo.addons.corr_engine_v2.wizards.engine_v2_debug_wizard.CorrEngineV2DebugWizard.action_create_bom_from_preview",
            autospec=True,
            side_effect=_fake_action,
        ):
            result = product._corr_create_v2_bom_from_persisted_preview(spec)

        self.assertEqual(captured["payload"]["marker"], "persisted-preview")
        self.assertEqual(captured["fg_product_id"], product.product_variant_id.id)
        self.assertEqual(captured["bom_backend"], "stagecontext_shadow")
        self.assertTrue(result["bom_id"])
        self.assertEqual(result["bom_backend_applied"], "stagecontext_shadow")
        self.assertFalse(self.env["corr.spec.template"].sudo().search([("product_tmpl_id", "=", product.id)], limit=1))

    def test_v2_spec_upsert_maps_corr_forced_ups_to_spec_forced_ups(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        product = self._make_v2_ready_product(
            name="V2 Forced UPS Product",
            corr_style="DIECUT_SHEET",
            corr_height_mm=0.0,
            corr_fg_per_sheet=1,
            corr_forced_ups=1,
        )

        spec, _created = product._corr_upsert_v2_spec(product._corr_autobom_v2_validate_inputs())

        self.assertEqual(spec.forced_ups, 1)

    def test_bulk_upload_result_batch_maps_rows_and_counts_statuses(self):
        success_product = self._make_v2_ready_product(
            name="Bulk Result Success",
            default_code="BULK-RESULT-001",
        )
        auto_fixed_product = self._make_v2_ready_product(
            name="Bulk Result Auto Fixed",
            default_code="BULK-RESULT-002",
        )
        failed_product = self._make_v2_ready_product(
            name="Bulk Result Failed",
            default_code="BULK-RESULT-003",
        )
        success_product._corr_set_autobom_status(
            "success",
            "v2",
            "ok",
            error_payload={"spec_id": 10, "bom_id": 20},
        )
        auto_fixed_product._corr_set_autobom_status(
            "success",
            "v2",
            "ok",
            error_payload={
                "spec_id": 11,
                "bom_id": 21,
                "auto_created_material_variant_ids": [self.kraft_variant.id],
            },
        )
        failed_product._corr_set_v2_failure(
            "material_resolution",
            "No material variant found for Layer 1",
            payload={"spec_id": 12},
        )

        batch = self.ProductTemplate._corr_create_import_result_batch(
            ["default_code", "name", "corr_style"],
            [
                ["BULK-RESULT-001", "Bulk Result Success", "0201"],
                ["BULK-RESULT-002", "Bulk Result Auto Fixed", "0201"],
                ["BULK-RESULT-003", "Bulk Result Failed", "0201"],
            ],
        )

        self.assertEqual(batch.total_rows, 3)
        self.assertEqual(batch.success_count, 1)
        self.assertEqual(batch.auto_fixed_count, 1)
        self.assertEqual(batch.failed_count, 1)
        self.assertEqual(batch.state, "partial")
        lines = batch.line_ids.sorted("excel_row")
        self.assertEqual(lines.mapped("excel_row"), [2, 3, 4])
        self.assertEqual(lines.mapped("status"), ["success", "auto_fixed", "failed"])
        self.assertEqual(lines[2].failure_stage, "material_resolution")
        self.assertIn("No material variant", lines[2].error_message)
        self.assertEqual(lines[1].auto_created_material_variant_ids, self.kraft_variant)

    def test_retry_failed_boms_continues_after_one_row_exception(self):
        self.icp.set_param("corr_mrp_bridge.bulk_upload_use_engine_v2", "1")
        bad_product = self._make_v2_ready_product(
            name="Bulk Retry Bad",
            default_code="BULK-RETRY-BAD",
        )
        good_product = self._make_v2_ready_product(
            name="Bulk Retry Good",
            default_code="BULK-RETRY-GOOD",
        )
        bad_product._corr_set_v2_failure("validation", "bad", payload={})
        good_product._corr_set_v2_failure("validation", "retry me", payload={})
        batch = self.env["corr.bulk.upload.batch"].sudo().create({"name": "Retry Batch"})
        bad_line = self.env["corr.bulk.upload.line"].sudo().create(
            {
                "batch_id": batch.id,
                "excel_row": 2,
                "product_tmpl_id": bad_product.id,
                "product_code": bad_product.default_code,
                "status": "failed",
            }
        )
        good_line = self.env["corr.bulk.upload.line"].sudo().create(
            {
                "batch_id": batch.id,
                "excel_row": 3,
                "product_tmpl_id": good_product.id,
                "product_code": good_product.default_code,
                "status": "failed",
            }
        )

        def _fake_autobom(product):
            if product == bad_product:
                raise RuntimeError("forced retry failure")
            product._corr_set_autobom_status(
                "success",
                "v2",
                "retry ok",
                error_payload={"spec_id": False, "bom_id": False},
            )
            return {"status": "success"}

        with patch.object(ProductTemplateCorr, "_corr_autobom_maybe", autospec=True, side_effect=_fake_autobom):
            batch.action_retry_failed_boms()

        bad_line.invalidate_recordset()
        good_line.invalidate_recordset()
        self.assertEqual(bad_line.status, "failed")
        self.assertIn("forced retry failure", bad_line.error_message)
        self.assertEqual(good_line.status, "success")

    def test_duplicate_product_code_validator_allows_unique_codes(self):
        result = self.ProductTemplate.with_context(skip_corr_autobom=True).load(
            ["Product Code", "Product Name"],
            [
                ["BULK-DUP-OK-001", "Duplicate OK 1"],
                ["BULK-DUP-OK-002", "Duplicate OK 2"],
            ],
        )

        self.assertIsInstance(result, dict)
        self.assertTrue(self.ProductTemplate.search([("default_code", "=", "BULK-DUP-OK-001")], limit=1))
        self.assertTrue(self.ProductTemplate.search([("default_code", "=", "BULK-DUP-OK-002")], limit=1))

    def test_duplicate_product_code_validator_rejects_one_duplicate_before_import(self):
        before_count = self.ProductTemplate.search_count([("default_code", "in", ["BULK-DUP-001", "BULK-DUP-002"])])

        with self.assertRaises(UserError) as raised:
            self.ProductTemplate.with_context(skip_corr_autobom=True).load(
                ["Product Code", "Product Name"],
                [
                    ["BULK-DUP-001", "Duplicate 1"],
                    ["BULK-DUP-002", "Duplicate 2"],
                    ["BULK-DUP-001", "Duplicate 1 Again"],
                ],
            )

        message = raised.exception.args[0]
        self.assertIn("Duplicate Product Code found in uploaded file", message)
        self.assertIn("BULK-DUP-001 - rows 2 and 4", message)
        after_count = self.ProductTemplate.search_count([("default_code", "in", ["BULK-DUP-001", "BULK-DUP-002"])])
        self.assertEqual(after_count, before_count)

    def test_duplicate_product_code_validator_reports_all_duplicates(self):
        with self.assertRaises(UserError) as raised:
            self.ProductTemplate._corr_validate_import_duplicate_product_codes(
                ["default_code", "name"],
                [
                    ["BULK-DUP-MULTI-001", "Duplicate 1"],
                    ["BULK-DUP-MULTI-002", "Duplicate 2"],
                    ["BULK-DUP-MULTI-001", "Duplicate 1 Again"],
                    ["BULK-DUP-MULTI-002", "Duplicate 2 Again"],
                    ["BULK-DUP-MULTI-001", "Duplicate 1 Third"],
                ],
            )

        message = raised.exception.args[0]
        self.assertIn("BULK-DUP-MULTI-001 - rows 2, 4, 6", message)
        self.assertIn("BULK-DUP-MULTI-002 - rows 3 and 5", message)

    def test_duplicate_product_code_validator_rejects_existing_product_duplicate_in_file(self):
        self.ProductTemplate.with_context(skip_corr_autobom=True).create(
            {
                "name": "Existing Duplicate Target",
                "default_code": "BULK-DUP-EXISTING",
            }
        )

        with self.assertRaises(UserError) as raised:
            self.ProductTemplate.with_context(skip_corr_autobom=True).load(
                ["Product Code", "Product Name"],
                [
                    ["BULK-DUP-EXISTING", "Existing Updated Name"],
                    ["BULK-DUP-EXISTING", "Existing Updated Name Again"],
                ],
            )

        self.assertIn("BULK-DUP-EXISTING - rows 2 and 3", raised.exception.args[0])

    def test_duplicate_product_code_validator_rejects_same_code_different_names(self):
        with self.assertRaises(UserError) as raised:
            self.ProductTemplate._corr_validate_import_duplicate_product_codes(
                ["default_code", "name"],
                [
                    ["BULK-DUP-NAME", "First Name"],
                    ["BULK-DUP-NAME", "Second Name"],
                ],
            )

        self.assertIn("BULK-DUP-NAME - rows 2 and 3", raised.exception.args[0])

    def test_csv_duplicate_handling_uses_same_load_mechanism(self):
        mapped_fields, mapped_data, _warnings, _source_map = self.ProductTemplate._corr_apply_import_header_aliases(
            ["default_code", "name"],
            [
                ["BULK-DUP-CSV", "CSV Name 1"],
                ["BULK-DUP-CSV", "CSV Name 2"],
            ],
        )

        with self.assertRaises(UserError) as raised:
            self.ProductTemplate._corr_validate_import_duplicate_product_codes(mapped_fields, mapped_data)

        self.assertIn("BULK-DUP-CSV - rows 2 and 3", raised.exception.args[0])

    def test_bulk_upload_result_actions_require_corr_manager_group(self):
        user = self.env["res.users"].sudo().create(
            {
                "name": "Bulk Upload Non Manager",
                "login": "bulk_upload_non_manager",
                "email": "bulk_upload_non_manager@example.com",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )
        batch = self.env["corr.bulk.upload.batch"].sudo().create({"name": "Security Batch"})

        with self.assertRaises(AccessError):
            batch.with_user(user).action_retry_failed_boms()
        with self.assertRaises(AccessError):
            batch.with_user(user).action_download_error_report()
