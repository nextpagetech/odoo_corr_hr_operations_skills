from odoo import api, fields, models


class MrpWorkcenterCorrExt(models.Model):
    _inherit = "mrp.workcenter"

    # Machine capability hints for corrugation routing
    corr_flutes_supported = fields.Char(
        string="Corr: Flutes Supported",
        help="Comma-separated flute codes this machine supports (e.g., 'B,F').",
    )
    corr_deckle_min_cm = fields.Float(string="Corr: Deckle Min (cm)")
    corr_deckle_max_cm = fields.Float(string="Corr: Deckle Max (cm)")
    corr_length_min_mm = fields.Float(
        string="Corr: Length Min (mm)",
        help="Minimum sheet/blank length supported by this workcenter for option feasibility.",
    )
    corr_length_max_mm = fields.Float(
        string="Corr: Length Max (mm)",
        help="Maximum sheet/blank length supported by this workcenter for option feasibility.",
    )
    corr_length_min_cm = fields.Float(
        string="Corr: Length Min (cm)",
        compute="_compute_corr_length_cm",
        inverse="_inverse_corr_length_min_cm",
        help="Minimum sheet/blank length supported by this workcenter for option feasibility.",
    )
    corr_length_max_cm = fields.Float(
        string="Corr: Length Max (cm)",
        compute="_compute_corr_length_cm",
        inverse="_inverse_corr_length_max_cm",
        help="Maximum sheet/blank length supported by this workcenter for option feasibility.",
    )
    max_slitting_ups = fields.Integer(
        string="Max Slitting UPS",
        help=(
            "Practical maximum slitting/UPS count this workcenter can support for "
            "layout option generation."
        ),
    )
    corr_lamination_top_only = fields.Boolean(
        string="Corr: Top-sheet Lamination Only",
        help="If enabled, this workcenter is intended for top-sheet lamination stage only.",
    )
    corr_is_placeholder = fields.Boolean(
        string="Corr: Placeholder Work Center",
        help=(
            "Enable this flag to mark the work center as a placeholder for corrugation operations "
            "that still need manual work center assignment. Auto-BoM may use this work center "
            "when no specific mapping is found; planners must reassign a real work center before "
            "starting the work order."
        ),
        default=False,
    )

    @api.depends("corr_length_min_mm", "corr_length_max_mm")
    def _compute_corr_length_cm(self):
        for workcenter in self:
            workcenter.corr_length_min_cm = float(workcenter.corr_length_min_mm or 0.0) / 10.0
            workcenter.corr_length_max_cm = float(workcenter.corr_length_max_mm or 0.0) / 10.0

    def _inverse_corr_length_min_cm(self):
        for workcenter in self:
            workcenter.corr_length_min_mm = float(workcenter.corr_length_min_cm or 0.0) * 10.0

    def _inverse_corr_length_max_cm(self):
        for workcenter in self:
            workcenter.corr_length_max_mm = float(workcenter.corr_length_max_cm or 0.0) * 10.0

    def get_corr_deckle_min_mm(self):
        self.ensure_one()
        return float(self.corr_deckle_min_cm or 0.0) * 10.0

    def get_corr_deckle_max_mm(self):
        self.ensure_one()
        return float(self.corr_deckle_max_cm or 0.0) * 10.0

    def get_corr_length_min_mm(self):
        self.ensure_one()
        return float(self.corr_length_min_mm or 0.0)

    def get_corr_length_max_mm(self):
        self.ensure_one()
        return float(self.corr_length_max_mm or 0.0)
