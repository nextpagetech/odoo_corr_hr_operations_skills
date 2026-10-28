from odoo import fields, models


class CorrSpecTemplate(models.Model):
    _inherit = "corr.spec.template"

    stage_mapping_id = fields.Many2one("corr.stage.mapping", string="Stage Mapping")

    corr_extra_finishing_operation_ids = fields.Many2many(
        "corr.operation.type",
        "corr_spec_finishing_op_rel",
        "spec_id",
        "operation_type_id",
        string="Extra Finishing Operations",
        domain=[("is_finishing", "=", True)],
        help="Optional extra finishing operations to append to the FG BoM "
        "and work orders, after the core engine-driven steps.",
    )

    def action_clear_stage_mapping(self):
        for rec in self:
            rec.stage_mapping_id = False
        return True
