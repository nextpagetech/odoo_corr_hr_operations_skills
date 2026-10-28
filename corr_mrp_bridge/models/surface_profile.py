from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CorrEngineSurfaceProfile(models.Model):
    _name = "corr.engine.surface.profile"
    _description = "Engine Surface Profile"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    plant_id = fields.Many2one("corr.plant.param", string="Plant")
    surface_category = fields.Selection(
        [
            ("default", "Default"),
            ("printed", "Printed"),
            ("plain", "Plain"),
            ("laminated", "Laminated"),
        ],
        string="Surface Category",
        default="default",
        required=True,
    )
    is_primary = fields.Boolean(
        string="Primary",
        help="Used as the default Engine V2 surface profile for this company/plant/category.",
    )

    # Printing
    printing_required = fields.Boolean(string="Printing Required")
    print_process = fields.Selection(
        [
            ("flexo", "Flexo"),
            ("offset", "Offset"),
            ("digital", "Digital"),
            ("screen", "Screen"),
            ("other", "Other"),
        ],
        string="Print Process",
    )
    print_colors = fields.Integer(string="Print Colors")
    print_coverage_percent = fields.Float(string="Print Coverage %", digits=(16, 2))

    # Top sheet
    top_sheet_mode = fields.Selection(
        [
            ("none", "None"),
            ("roll", "Top from Roll"),
            ("unit_sheet", "Unit Printed Sheet"),
        ],
        string="Top Sheet Mode",
        default="none",
    )
    top_sheet_product_id = fields.Many2one(
        "product.product",
        string="Top Sheet Product",
        domain="[('corr_top_sheet_selectable', '=', True)]",
        help="Select the Top Sheet SKU (variant). The selected product is used as the authoritative RM in unit-sheet mode.",
    )
    top_sheet_roll_code = fields.Char(
        string="Top Sheet Roll Code",
        help="Roll code used in SFG naming when Top Sheet Mode is Top from Roll.",
    )

    # Lamination
    lamination_required = fields.Boolean(string="Lamination Required")
    lamination_type_id = fields.Many2one("corr.lamination.type", string="Lamination Type")
    lamination_side = fields.Selection(
        [
            ("top", "Top"),
            ("bottom", "Bottom"),
            ("both", "Both"),
        ],
        string="Lamination Side",
    )

    @api.constrains("active", "company_id", "plant_id", "surface_category", "is_primary")
    def _check_single_primary_profile(self):
        for profile in self:
            if not profile.active or not profile.is_primary:
                continue
            domain = [
                ("id", "!=", profile.id),
                ("active", "=", True),
                ("is_primary", "=", True),
                ("surface_category", "=", profile.surface_category or "default"),
                ("company_id", "=", profile.company_id.id if profile.company_id else False),
                ("plant_id", "=", profile.plant_id.id if profile.plant_id else False),
            ]
            if self.search_count(domain):
                raise ValidationError(
                    _(
                        "Only one primary surface profile is allowed per company, plant, and surface category."
                    )
                )
