from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CorrEngineV2WireType(models.Model):
    _name = "corr.engine.v2.wire.type"
    _description = "Engine V2 Stitching Wire Type"
    _order = "sequence, name, id"

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    code = fields.Char(required=True)
    active = fields.Boolean(default=True)
    product_id = fields.Many2one(
        "product.product",
        string="Wire Product",
        required=True,
        domain=[("product_role", "=", "raw_material")],
        help="Raw material variant consumed by Engine V2 BoMs for this wire type.",
    )
    stitches_per_cm = fields.Float(
        string="Stitches per cm",
        digits=(16, 3),
        required=True,
        default=1.0,
    )
    g_per_stitch = fields.Float(
        string="g per Stitch",
        digits=(16, 6),
        required=True,
        default=0.05,
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
    )
    notes = fields.Text()

    _sql_constraints = [
        (
            "code_company_unique",
            "unique(code, company_id)",
            "Wire type code must be unique per company.",
        ),
    ]

    @api.constrains("stitches_per_cm", "g_per_stitch")
    def _check_positive_factors(self):
        for rec in self:
            if rec.stitches_per_cm <= 0.0:
                raise ValidationError(_("Stitches per cm must be greater than zero."))
            if rec.g_per_stitch <= 0.0:
                raise ValidationError(_("g per Stitch must be greater than zero."))
