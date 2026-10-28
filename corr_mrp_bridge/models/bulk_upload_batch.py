import base64
import csv
import io
import json

from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class CorrBulkUploadBatch(models.Model):
    _name = "corr.bulk.upload.batch"
    _description = "Corrugation Bulk Upload Result"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, default=lambda self: _("Bulk Upload"))
    imported_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    line_ids = fields.One2many("corr.bulk.upload.line", "batch_id", string="Rows")
    total_rows = fields.Integer(compute="_compute_counts", store=True)
    success_count = fields.Integer(compute="_compute_counts", store=True)
    auto_fixed_count = fields.Integer(compute="_compute_counts", store=True)
    failed_count = fields.Integer(compute="_compute_counts", store=True)
    state = fields.Selection(
        [
            ("success", "Success"),
            ("partial", "Partial"),
            ("failed", "Failed"),
        ],
        compute="_compute_counts",
        store=True,
        default="success",
    )

    @api.depends("line_ids.status")
    def _compute_counts(self):
        for batch in self:
            lines = batch.line_ids
            batch.total_rows = len(lines)
            batch.success_count = len(lines.filtered(lambda line: line.status == "success"))
            batch.auto_fixed_count = len(lines.filtered(lambda line: line.status == "auto_fixed"))
            batch.failed_count = len(lines.filtered(lambda line: line.status == "failed"))
            if batch.failed_count and batch.failed_count == batch.total_rows:
                batch.state = "failed"
            elif batch.failed_count:
                batch.state = "partial"
            else:
                batch.state = "success"

    def _check_bulk_upload_manager_access(self):
        if not self.env.user.has_group("corrugation_formula_engine_v2.group_corr_manager"):
            raise AccessError(_("You are not allowed to manage corrugation bulk upload results."))

    def action_retry_failed_boms(self):
        self._check_bulk_upload_manager_access()
        Line = self.env["corr.bulk.upload.line"].sudo()
        for batch in self.sudo():
            for line in batch.line_ids.filtered(lambda row: row.status == "failed" and row.product_tmpl_id):
                try:
                    with self.env.cr.savepoint():
                        line.product_tmpl_id._corr_autobom_maybe()
                except Exception as exc:
                    line.write(
                        {
                            "status": "failed",
                            "error_message": _("Retry failed before diagnostics could be updated: %s") % exc,
                            "action_required": _("Review the server log, correct the master data/configuration, then retry."),
                        }
                    )
                    continue
                Line._corr_update_from_product(line)
            batch._corr_apply_batch_autofix_status()
        return True

    def action_download_error_report(self):
        self._check_bulk_upload_manager_access()
        self.ensure_one()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Excel Row",
                "Product Code",
                "Product Name",
                "Status",
                "Failure Stage",
                "Error Message",
                "Action Required",
            ]
        )
        for line in self.line_ids.filtered(lambda row: row.status == "failed"):
            writer.writerow(
                [
                    line.excel_row or "",
                    line.product_code or "",
                    line.product_name or "",
                    line.status or "",
                    line.failure_stage or "",
                    line.error_message or "",
                    line.action_required or "",
                ]
            )
        data = base64.b64encode(output.getvalue().encode("utf-8"))
        attachment = self.env["ir.attachment"].sudo().create(
            {
                "name": "%s-error-report.csv" % (self.name or "bulk-upload"),
                "type": "binary",
                "datas": data,
                "mimetype": "text/csv",
                "res_model": self._name,
                "res_id": self.id,
            }
        )
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }

    def _corr_apply_batch_autofix_status(self):
        Product = self.env["product.product"].sudo()
        Bom = self.env["mrp.bom"].sudo()
        for batch in self.sudo():
            auto_material_ids = set(batch.line_ids.mapped("auto_created_material_variant_ids").ids)
            auto_materials = Product.browse(list(auto_material_ids)).exists()
            if not auto_materials:
                continue
            auto_material_ids = set(auto_materials.ids)
            for line in batch.line_ids.filtered(lambda row: row.status == "success" and row.product_tmpl_id):
                bom = line.bom_id
                if not bom and line.product_tmpl_id:
                    bom = Bom.search(
                        [("product_tmpl_id", "=", line.product_tmpl_id.id)],
                        order="id desc",
                        limit=1,
                    )
                used_ids = set((bom.bom_line_ids.product_id & auto_materials).ids) if bom else set()
                if not used_ids:
                    continue
                line.write(
                    {
                        "status": "auto_fixed",
                        "auto_created_material_variant_ids": [(6, 0, sorted(used_ids))],
                    }
                )


class CorrBulkUploadLine(models.Model):
    _name = "corr.bulk.upload.line"
    _description = "Corrugation Bulk Upload Result Line"
    _order = "batch_id desc, excel_row asc, id asc"

    batch_id = fields.Many2one("corr.bulk.upload.batch", required=True, ondelete="cascade")
    excel_row = fields.Integer(string="Excel Row")
    product_tmpl_id = fields.Many2one("product.template", string="Product")
    product_code = fields.Char()
    product_name = fields.Char()
    status = fields.Selection(
        [
            ("success", "Success"),
            ("auto_fixed", "Auto Fixed"),
            ("failed", "Failed"),
        ],
        required=True,
        default="failed",
    )
    failure_stage = fields.Selection(
        [
            ("validation", "Validation"),
            ("spec_upsert", "Spec Upsert"),
            ("material_resolution", "Material Resolution"),
            ("preview", "Preview"),
            ("bom", "BoM"),
            ("bom_materialization", "BoM Materialization"),
            ("bom_write", "BoM Write"),
        ],
    )
    error_message = fields.Text()
    action_required = fields.Text()
    auto_created_material_variant_ids = fields.Many2many(
        "product.product",
        "corr_bulk_upload_line_auto_material_rel",
        "line_id",
        "product_id",
        string="Auto-created Material Variants",
    )
    auto_created_product_ids = fields.Many2many(
        "product.product",
        "corr_bulk_upload_line_auto_product_rel",
        "line_id",
        "product_id",
        string="Auto-created Products",
    )
    spec_id = fields.Many2one("corr.engine.v2.spec", string="Engine V2 Spec")
    bom_id = fields.Many2one("mrp.bom", string="BoM")

    @api.model
    def _corr_payload_from_product(self, product):
        try:
            return json.loads(product.corr_autobom_error_payload or "{}")
        except Exception:
            return {}

    @api.model
    def _corr_update_from_product(self, line):
        product = line.product_tmpl_id
        payload = self._corr_payload_from_product(product) if product else {}
        auto_material_ids = payload.get("auto_created_material_variant_ids") or []
        auto_product_ids = payload.get("auto_created_product_ids") or []
        bom_id = payload.get("bom_id") or False
        spec_id = payload.get("spec_id") or product.corr_autobom_spec_id or False
        status = "success" if product.corr_autobom_status == "success" else "failed"
        if status == "success" and (auto_material_ids or auto_product_ids):
            status = "auto_fixed"
        vals = {
            "status": status,
            "failure_stage": False if status != "failed" else product.corr_autobom_error_stage,
            "error_message": False if status != "failed" else product.corr_autobom_message,
            "action_required": self._corr_action_required(product) if status == "failed" else False,
            "spec_id": spec_id,
            "bom_id": bom_id,
            "auto_created_material_variant_ids": [(6, 0, auto_material_ids)],
            "auto_created_product_ids": [(6, 0, auto_product_ids)],
        }
        line.write(vals)

    @api.model
    def _corr_action_required(self, product):
        stage = product.corr_autobom_error_stage
        message = product.corr_autobom_message or ""
        if stage == "material_resolution":
            return _("Create the missing material variant or enable safe auto-create and retry.")
        if "top sheet" in message.lower():
            return _("Create or correct the top sheet product master, then retry failed BoMs.")
        if stage == "validation":
            return _("Correct required upload columns such as style, dimensions, routing, recipe, or layer details.")
        if stage in ("bom", "bom_materialization", "bom_write"):
            return _("Review the persisted Engine V2 preview and BoM write diagnostics, then retry.")
        return _("Correct the product upload data and retry.")

