from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CorrDeckleWizard(models.TransientModel):
    _name = "corr.deckle.wizard"
    _description = "Deckle Planning"

    production_id = fields.Many2one("mrp.production", required=True)
    result_text = fields.Text(readonly=True)

    def action_plan(self):
        self.ensure_one()
        mo = self.production_id
        engine = self.env["corr.formula.engine"]
        spec = engine.map_product_to_spec(mo.product_tmpl_id)
        plant = self.env["corr.plant.param"].sudo().search([], limit=1)
        reels = (plant.default_reel_widths_mm or [])
        if not reels:
            raise UserError(_("No reel widths configured in Plant Parameters."))
        res = engine.plan_deckle(spec, reels)
        text = (f"Target deckle: {res['target_deckle']} mm\n"
                f"Proposed reel: {res['reel_combo']}\n"
                f"Trim %: {res['trim_pct']}")
        mo.message_post(body=("Deckle Plan\n" + text))
        self.result_text = text
        return {
            "type": "ir.actions.act_window",
            "res_model": "corr.deckle.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }
