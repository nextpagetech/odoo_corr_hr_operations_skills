from odoo import api, fields, models


class CorrEngineMaterialProfile(models.Model):
    _name = "corr.engine.material.profile"
    _description = "Engine Material Profile"
    _rec_name = "product_tmpl_id"
    _rec_names_search = ["product_tmpl_id", "role", "material_type"]

    product_tmpl_id = fields.Many2one(
        "product.template",
        string="Product Template",
        required=True,
        ondelete="cascade",
    )
    material_type = fields.Selection(
        [
            ("structural", "Structural"),
            ("consumable", "Consumable"),
            ("byproduct", "Byproduct"),
        ],
        string="Material Type",
        required=True,
        help=(
            "High-level classification of the material for the Engine V2 dynamic "
            "architecture. This does not change any current BoM or costing behaviour."
        ),
    )
    role = fields.Char(
        string="Role",
        help=(
            "Optional free-text role for this material (e.g. liner, flute, top_sheet, "
            "adhesive, ink, strap)."
        ),
    )
    default_bucket = fields.Selection(
        [
            ("raw_material", "Raw Material"),
            ("board", "Board"),
            ("sheet", "Sheet"),
            ("blanks", "Blanks"),
            ("finished_goods", "Finished Goods"),
        ],
        string="Default Quantity Bucket",
        help=(
            "Optional default quantity bucket used when aggregating this material in "
            "the dynamic architecture. This does not affect current Stage Costing."
        ),
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        related="product_tmpl_id.company_id",
        store=True,
        readonly=True,
    )
    active = fields.Boolean(default=True)

    is_system_default = fields.Boolean(
        string="System Default",
        default=False,
        readonly=True,
        help=(
            "Indicates that this material profile was created and is maintained by the "
            "Phase 8 seeding tools as part of the standard compatibility profile."
        ),
    )
    seed_key = fields.Char(
        string="Seed Key",
        readonly=True,
        index=True,
        help=(
            "Stable identifier used by the Phase 8 seeding tools to match and update "
            "this record in an idempotent way."
        ),
    )
    seed_version = fields.Char(
        string="Seed Version",
        readonly=True,
        help="Version label of the compatibility profile that last updated this record.",
    )

    _sql_constraints = [
        (
            "corr_engine_material_profile_product_unique",
            "unique(product_tmpl_id)",
            "Each product template can have at most one Engine Material Profile.",
        ),
        (
            "corr_engine_material_profile_seed_key_unique",
            "unique(seed_key)",
            "Each Engine Material Profile seed key must be unique.",
        )
    ]

    @api.depends("product_tmpl_id.display_name")
    def _compute_display_name(self):
        for profile in self:
            profile.display_name = (
                profile.product_tmpl_id.display_name
                or profile.seed_key
                or "Engine Material Profile"
            )


class CorrEngineMaterialAttachmentRule(models.Model):
    _name = "corr.engine.material.attachment.rule"
    _description = "Engine Material Attachment Rule"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    plant_id = fields.Many2one(
        "corr.plant.param",
        string="Plant",
        help="Optional plant binding for this rule. Leave empty to apply globally.",
    )
    material_profile_id = fields.Many2one(
        "corr.engine.material.profile",
        string="Material Profile",
        required=True,
        ondelete="cascade",
    )
    stage_template_ids = fields.Many2many(
        "corr.engine.stage.template",
        "corr_engine_material_rule_stage_rel",
        "rule_id",
        "stage_template_id",
        string="Stage Templates",
    )
    stage_type = fields.Selection(
        [
            ("corrugation", "Corrugation"),
            ("printing", "Printing"),
            ("lamination", "Lamination"),
            ("diecut", "Die-cut"),
            ("conversion", "Conversion / Seaming"),
            ("fg", "Finished Goods"),
            ("auxiliary", "Auxiliary"),
        ],
        string="Stage Type",
        help=(
            "Optional filter on stage type when using this rule. When both stage "
            "templates and stage type are set, both filters must match."
        ),
    )
    attach_quantity_basis = fields.Selection(
        [
            ("per_m2", "Per mÂ²"),
            ("per_box", "Per Box"),
            ("per_length", "Per Length"),
            ("per_time", "Per Time"),
            ("custom", "Custom"),
        ],
        string="Attach Quantity Basis",
        default="per_m2",
        help=(
            "Describes how the required quantity for this material is computed in the "
            "dynamic architecture. This is configuration only at this stage."
        ),
    )
    is_mandatory = fields.Boolean(
        string="Mandatory",
        help="When enabled, this material is expected to be present whenever the rule applies.",
    )
    active = fields.Boolean(default=True)

    is_system_default = fields.Boolean(
        string="System Default",
        default=False,
        readonly=True,
        help=(
            "Indicates that this material attachment rule was created and is maintained "
            "by the Phase 8 seeding tools as part of the standard compatibility profile."
        ),
    )
    seed_key = fields.Char(
        string="Seed Key",
        readonly=True,
        index=True,
        help=(
            "Stable identifier used by the Phase 8 seeding tools to match and update "
            "this record in an idempotent way."
        ),
    )
    seed_version = fields.Char(
        string="Seed Version",
        readonly=True,
        help="Version label of the compatibility profile that last updated this record.",
    )

    _sql_constraints = [
        (
            "corr_engine_material_attachment_rule_seed_key_unique",
            "unique(seed_key)",
            "Each Engine Material Attachment Rule seed key must be unique.",
        )
    ]



