import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class MrpProductionCorr(models.Model):
    _inherit = "mrp.production"

    @api.depends("product_id", "never_product_template_attribute_value_ids")
    def _compute_bom_id(self):
        super()._compute_bom_id()
        try:
            default_bom_id = int(self.env.context.get("default_bom_id") or 0)
        except Exception:
            default_bom_id = 0
        for mo in self:
            if mo.state and mo.state != "draft":
                continue
            if not mo.product_id:
                continue
            if default_bom_id and mo.bom_id and mo.bom_id.id == default_bom_id:
                continue
            current_bom = self.env["mrp.bom"]._corr_current_engine_v2_bom_for_product(
                mo.product_id,
                mo.company_id,
            )
            if current_bom and mo.bom_id != current_bom:
                mo.bom_id = current_bom.id
                _logger.info(
                    "Engine V2 current BoM selected for draft MO: mo_id=%s product_id=%s bom_id=%s version=%s company_id=%s",
                    mo.id or None,
                    mo.product_id.id,
                    current_bom.id,
                    current_bom.corr_engine_v2_version,
                    mo.company_id.id if mo.company_id else None,
                )
                self.env.add_to_compute(mo._fields["picking_type_id"], mo)

    def action_corr_compute_preview(self):
        self.ensure_one()
        product_tmpl = self.product_tmpl_id
        qty = self.product_qty or 1.0
        spec = self.env["corr.formula.engine"].map_product_to_spec(product_tmpl)
        return {
            "type": "ir.actions.act_window",
            "name": _("Compute Corr Requirements"),
            "res_model": "corr.compute.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_spec_id": spec.id,
                "default_qty": qty,
            },
        }

    def action_corr_open_deckle_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Deckle Planning"),
            "res_model": "corr.deckle.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_production_id": self.id},
        }

    def _corr_is_staged_chain_mo(self):
        self.ensure_one()
        bom = self.bom_id
        product_tmpl = self.product_id.product_tmpl_id
        return bool(
            (bom and (bom.corr_spec_id or bom.corr_stage or bom.corr_sfg_key))
            or getattr(product_tmpl, "corr_sfg_key", False)
            or getattr(product_tmpl, "corr_stage", False)
        )

    def _corr_check_manual_chain_confirm_allowed(self):
        if self.env.context.get("corr_auto_confirm_from_parent_mo"):
            return
        for mo in self.filtered(lambda production: production.state == "draft"):
            if not mo._corr_is_staged_chain_mo():
                continue
            draft_sources = mo._get_sources().filtered(lambda source: source.state == "draft")
            if draft_sources:
                raise UserError(_(
                    "Please confirm the parent Manufacturing Order first.\n\n"
                    "Child Manufacturing Orders are confirmed automatically from the parent MO.\n"
                    "Draft parent MO(s): %s"
                ) % ", ".join(draft_sources.mapped("name")))

    def action_confirm(self):
        self._corr_check_manual_chain_confirm_allowed()
        # Variance check (feature-flagged)
        icp = self.env["ir.config_parameter"].sudo()
        check = icp.get_param("corr_mrp_bridge.mo_variance_check", default="0") == "1"
        if check:
            for mo in self:
                try:
                    mo._corr_log_bom_variance()
                except Exception:
                    # Do not block confirmation on logging
                    pass
        res = super().action_confirm()
        if self.env.context.get("corr_reopen_so_mrp_after_confirm") and self.env.context.get("corr_sale_order_id"):
            sale = self.env["sale.order"].browse(int(self.env.context["corr_sale_order_id"])).exists()
            if sale:
                sale.invalidate_recordset(["mrp_production_ids", "mrp_production_count"])
                return sale._corr_action_view_mrp_production_list()
        return res

    def _corr_log_bom_variance(self):
        self.ensure_one()
        if not self.bom_id:
            return
        product_tmpl = self.product_tmpl_id
        # Skip staged SFG products; variance is only tracked for FG
        try:
            if (getattr(product_tmpl, "product_role", None) == "sfg") or getattr(product_tmpl, "corr_sfg_key", False):
                return
        except Exception:
            pass
        qty = self.product_qty or 1.0
        engine = self.env["corr.formula.engine"]
        spec = engine.map_product_to_spec(product_tmpl)
        result = engine.compute_requirements(spec, qty)
        rm_lines = result.get("rm_lines") or []
        # Build expected by product id (only roles we map)
        expected = {}
        metrics = result.get("metrics") or {}
        ply_detail = {int(p.get("idx")): p for p in (metrics.get("ply_detail") or [])}
        Map = self.env["corr.material.product.map"]
        for line in rm_lines:
            role = (line.get("role") or "").lower()
            if role not in ("liner", "medium", "adhesive", "ink", "lamination", "wire", "tape"):
                continue
            layer_no = line.get("layer_no")
            gsm = None
            flute = None
            if role in ("liner", "medium"):
                if layer_no:
                    p = ply_detail.get(int(layer_no))
                    if p:
                        gsm = float(p.get("gsm") or 0.0)
                        flute = p.get("flute") or None
                # Fallback: grouped rm_lines can carry gsm/flute directly
                if gsm in (None, 0.0):
                    try:
                        gsm_val = line.get("gsm")
                        if gsm_val is not None:
                            gsm = float(gsm_val)
                    except Exception:
                        pass
                if not flute:
                    flute = line.get("flute") or flute
            mapped = Map.find_match(role, gsm=gsm, flute=flute)
            if mapped:
                prod = mapped[0].product_id
                qty_val = float(line.get("qty") or 0.0)
                expected[prod.id] = expected.get(prod.id, 0.0) + qty_val
        # Compare with BoM scaled quantities
        variances = []
        for bl in self.bom_id.bom_line_ids:
            prod = bl.product_id
            if not prod:
                continue
            bom_qty = (bl.product_qty or 0.0) * qty
            exp_qty = expected.get(prod.id)
            if exp_qty is None:
                continue
            diff = exp_qty - bom_qty
            if abs(diff) > 1e-6:
                variances.append(f"{prod.display_name}: expected {exp_qty:.6f} {bl.product_uom_id.name or ''} vs BoM {bom_qty:.6f}")
        if variances:
            self.message_post(body=("Corrugation variance vs BoM\n" + "\n".join(variances)))
