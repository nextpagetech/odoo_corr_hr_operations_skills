from odoo import api, fields, models
from odoo.tools import float_round


class MrpRoutingWorkcenterExt(models.Model):
    _inherit = "mrp.routing.workcenter"

    # Engine V2 can intentionally create operations without a selected
    # workcenter when corr_engine_v2.auto_assign_operation_workcenter is off.
    # Feasibility still uses workcenters internally; this only controls BoM
    # operation assignment.
    workcenter_id = fields.Many2one("mrp.workcenter", required=False)

    corr_engine_generated = fields.Boolean(
        string="Engine Generated",
        help="Operation line was created or last updated by the Engine V2 BoM adapter.",
    )
    time_cycle_manual_sec = fields.Float(
        string="Manual Dur. (sec)",
        compute="_compute_time_cycle_manual_sec",
        inverse="_inverse_time_cycle_manual_sec",
        readonly=False,
        help="Helper field for readability: minutes x 60. Editing this will update minutes.",
    )

    @api.depends("time_cycle_manual")
    def _compute_time_cycle_manual_sec(self):
        for rec in self:
            try:
                rec.time_cycle_manual_sec = float(rec.time_cycle_manual or 0.0) * 60.0
            except Exception:
                rec.time_cycle_manual_sec = 0.0

    def _inverse_time_cycle_manual_sec(self):
        for rec in self:
            try:
                sec = float(rec.time_cycle_manual_sec or 0.0)
            except Exception:
                sec = 0.0
            # Write back minutes from seconds
            rec.time_cycle_manual = sec / 60.0

    def _get_duration_expected(self, product, quantity, unit=False, workcenter=False):
        self.ensure_one()
        if self._skip_operation_line(product):
            return 0.0

        product = product or self.bom_id.product_tmpl_id
        unit = unit or product.uom_id
        quantity = self.bom_id.product_uom_id._compute_quantity(quantity, unit)
        workcenter = workcenter or self.workcenter_id
        if not workcenter:
            return float(self.time_cycle or 0.0)

        capacity = float(workcenter._get_capacity(product) or 0.0)
        if capacity <= 0.0:
            capacity = 1.0
        efficiency = float(workcenter.time_efficiency or 0.0)
        if efficiency <= 0.0:
            efficiency = 100.0
        cycle_number = float_round(quantity / capacity, precision_digits=0, rounding_method="UP")
        return workcenter._get_expected_duration(product) + cycle_number * self.time_cycle * 100.0 / efficiency
