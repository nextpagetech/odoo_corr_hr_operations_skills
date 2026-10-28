from odoo import fields, models


class CorrEngineBomTranslationRule(models.Model):
    _name = "corr.engine.bom.translation.rule"
    _description = "Engine BoM Translation Rule"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    plant_id = fields.Many2one(
        "corr.plant.param",
        string="Plant",
        help="Optional plant binding for this rule. Leave empty to apply globally.",
    )
    stage_template_ids = fields.Many2many(
        "corr.engine.stage.template",
        "corr_engine_bom_rule_stage_rel",
        "rule_id",
        "stage_template_id",
        string="Stage Templates",
    )
    operation_behavior = fields.Selection(
        [
            ("single_operation", "Single Operation"),
            ("merged_operation", "Merged Operation"),
            ("no_operation", "No Operation"),
        ],
        string="Operation Behaviour",
        default="single_operation",
        required=True,
        help=(
            "Describes how the matched stages should be represented as MRP operations "
            "when generating BoMs in the dynamic architecture."
        ),
    )
    operation_ref_type = fields.Selection(
        [
            ("workcenter", "Work Center"),
            ("routing_workcenter", "Routing Operation"),
            ("none", "None"),
        ],
        string="Operation Reference Type",
        default="routing_workcenter",
        required=True,
        help=(
            "Type of MRP object used to represent the operation for matched stages "
            "in the dynamic architecture."
        ),
    )
    workcenter_id = fields.Many2one(
        "mrp.workcenter",
        string="Work Center",
        help="Target work center for BoM operations when using the dynamic architecture.",
    )
    routing_workcenter_id = fields.Many2one(
        "mrp.routing.workcenter",
        string="Routing Operation",
        help="Target routing operation for BoM operations when using the dynamic architecture.",
    )
    component_behavior = fields.Selection(
        [
            ("component_line", "Component Line"),
            ("operation_consumable", "Operation Consumable"),
            ("ignore", "Ignore"),
        ],
        string="Component Behaviour",
        default="component_line",
        required=True,
        help=(
            "Describes how materials attached to the matched stages should be "
            "represented on the BoM in the dynamic architecture."
        ),
    )
    bucket_mapping = fields.Selection(
        [
            ("raw_material", "Raw Material"),
            ("board", "Board"),
            ("sheet", "Sheet"),
            ("blanks", "Blanks"),
            ("finished_goods", "Finished Goods"),
        ],
        string="Quantity Bucket Mapping",
        help=(
            "Optional explicit quantity bucket to use when aggregating costs and "
            "components for this rule. This does not affect current Stage Costing."
        ),
    )
    active = fields.Boolean(default=True)

    is_system_default = fields.Boolean(
        string="System Default",
        default=False,
        readonly=True,
        help=(
            "Indicates that this BoM translation rule was created and is maintained by "
            "the Phase 8 seeding tools as part of the standard compatibility profile."
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
            "corr_engine_bom_translation_rule_seed_key_unique",
            "unique(seed_key)",
            "Each Engine BoM Translation Rule seed key must be unique.",
        )
    ]



