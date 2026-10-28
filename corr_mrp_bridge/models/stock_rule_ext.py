from collections import defaultdict
import logging

from odoo import SUPERUSER_ID, api, models
from odoo.tools import float_compare


_logger = logging.getLogger(__name__)


class StockRule(models.Model):
    _inherit = "stock.rule"

    def _corr_keep_so_created_corr_mos_draft_enabled(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "corr_mrp_bridge.keep_so_created_corr_mos_draft",
            default="0",
        ) in ("1", "true", "True")

    def _corr_is_sale_created_corr_mo(self, production):
        bom = production.bom_id
        product_tmpl = production.product_id.product_tmpl_id
        return bool(
            (
                production.sale_line_id
                or production.procurement_group_id.sale_id
                or production.move_dest_ids.group_id.sale_id
            )
            and (
                (bom and (bom.corr_spec_id or bom.corr_stage or bom.corr_sfg_key))
                or getattr(product_tmpl, "corr_sfg_key", False)
                or getattr(product_tmpl, "corr_stage", False)
            )
        )

    def _corr_should_keep_procurement_mo_draft(self, production):
        if not self._corr_is_sale_created_corr_mo(production):
            return False
        sale = production.procurement_group_id.sale_id or production.move_dest_ids.group_id.sale_id[:1]
        if not sale:
            return False
        # Only keep the SO-root MO in draft. Downstream child MOs should
        # follow normal Odoo auto-confirm behavior after the user confirms
        # the root manually.
        return production.origin == sale.name

    def _get_matching_bom(self, product_id, company_id, values):
        if values.get("bom_id", False):
            return values["bom_id"]
        if values.get("orderpoint_id", False) and values["orderpoint_id"].bom_id:
            return values["orderpoint_id"].bom_id
        current_bom = self.env["mrp.bom"]._corr_current_engine_v2_bom_for_product(product_id, company_id)
        if current_bom:
            _logger.info(
                "Engine V2 current BoM selected for procurement MO: product_id=%s bom_id=%s version=%s company_id=%s",
                product_id.id,
                current_bom.id,
                current_bom.corr_engine_v2_version,
                company_id.id if company_id else None,
            )
            return current_bom
        return super()._get_matching_bom(product_id, company_id, values)

    @api.model
    def _run_manufacture(self, procurements):
        new_productions_values_by_company = defaultdict(lambda: defaultdict(list))
        for procurement, rule in procurements:
            if float_compare(procurement.product_qty, 0, precision_rounding=procurement.product_uom.rounding) <= 0:
                continue
            bom = rule._get_matching_bom(procurement.product_id, procurement.company_id, procurement.values)

            mo = self.env["mrp.production"]
            if procurement.origin != "MPS":
                domain = rule._make_mo_get_domain(procurement, bom)
                mo = self.env["mrp.production"].sudo().search(domain, limit=1)
            if not mo:
                procurement_qty = procurement.product_qty
                batch_size = procurement.values.get("batch_size", procurement_qty)
                if batch_size <= 0:
                    batch_size = procurement_qty
                vals = rule._prepare_mo_vals(*procurement, bom)
                while float_compare(procurement_qty, 0, precision_rounding=procurement.product_uom.rounding) > 0:
                    current_qty = min(procurement_qty, batch_size)
                    new_productions_values_by_company[procurement.company_id.id]["values"].append({
                        **vals,
                        "product_qty": procurement.product_uom._compute_quantity(current_qty, bom.product_uom_id) if bom else current_qty,
                    })
                    new_productions_values_by_company[procurement.company_id.id]["procurements"].append(procurement)
                    procurement_qty -= current_qty
            else:
                self.env["change.production.qty"].sudo().with_context(skip_activity=True).create({
                    "mo_id": mo.id,
                    "product_qty": mo.product_id.uom_id._compute_quantity(
                        mo.product_uom_qty + procurement.product_qty,
                        mo.product_uom_id,
                    ),
                }).change_prod_qty()

        keep_draft = self._corr_keep_so_created_corr_mos_draft_enabled()
        for company_id in new_productions_values_by_company:
            productions_vals_list = new_productions_values_by_company[company_id]["values"]
            productions = self.env["mrp.production"].with_user(SUPERUSER_ID).sudo().with_company(company_id).create(
                productions_vals_list
            )
            to_confirm = productions.filtered(self._should_auto_confirm_procurement_mo)
            if keep_draft:
                to_confirm = to_confirm.filtered(lambda p: not self._corr_should_keep_procurement_mo_draft(p))
            to_confirm.with_context(corr_auto_confirm_from_parent_mo=True).action_confirm()
            productions._post_run_manufacture(new_productions_values_by_company[company_id]["procurements"])
        return True
