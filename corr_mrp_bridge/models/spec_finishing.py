from odoo import fields, models


class CorrSpecFinishingLine(models.Model):
    _name = "corr.spec.finishing.line"
    _description = "Corr Spec Finishing Operation"
    _order = "sequence, id"

    spec_id = fields.Many2one("corr.spec.template", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    op_code = fields.Selection([
        ("gluing", "Gluing"),
        ("stitching", "Stitching"),
        ("folding_auto", "Folding (Auto)"),
        ("folding_manual", "Folding (Manual)"),
        ("flap_pasting", "Flap Pasting"),
        ("bottom_lock", "Bottom Lock"),
    ], required=True, string="Operation")

