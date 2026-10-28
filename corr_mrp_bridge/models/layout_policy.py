from odoo import fields, models


class CorrEngineLayoutPolicy(models.Model):
    _name = "corr.engine.layout.policy"
    _description = "Engine Layout Policy"

    name = fields.Char(required=True)

    allow_rotation = fields.Boolean(string="Allow Rotation", default=True)
    preferred_ups = fields.Integer(string="Preferred UPS")
    max_ups = fields.Integer(string="Max UPS")
    layout_mode = fields.Selection(
        [
            ("strict", "Strict"),
            ("optimize", "Optimize"),
            ("prefer_target", "Prefer Target"),
        ],
        string="Layout Mode",
        default="optimize",
    )
    sheet_source = fields.Selection(
        [
            ("manual", "Manual"),
            ("plant_default", "Plant Default"),
            ("derived", "Derived"),
        ],
        string="Sheet Source",
        default="plant_default",
    )
    trim_margin_mm = fields.Float(string="Trim Margin (mm)")
    gripper_margin_mm = fields.Float(string="Gripper Margin (mm)")

