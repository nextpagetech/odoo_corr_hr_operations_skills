from odoo import _, fields, models
from odoo.exceptions import UserError


class MrpProductionAssignResponsibleWizard(models.TransientModel):
    _name = "mrp.production.assign.responsible.wizard"
    _description = "Assign Responsible to Manufacturing Orders"

    user_id = fields.Many2one(
        "res.users",
        string="Responsible",
        required=True,
        domain=lambda self: [("groups_id", "in", self.env.ref("mrp.group_mrp_user").id)],
    )

    def action_assign(self):
        self.ensure_one()
        productions = self.env["mrp.production"].browse(self.env.context.get("active_ids", []))
        eligible_productions = productions.filtered(lambda p: p.state not in ("cancel", "done"))

        if not eligible_productions:
            raise UserError(_("Only Manufacturing Orders that are not cancelled or done can be updated."))

        eligible_productions.write({"user_id": self.user_id.id})
        return {"type": "ir.actions.act_window_close"}
