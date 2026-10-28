from odoo import fields, models


class CorrEnginePlantStageBinding(models.Model):
    _name = "corr.engine.plant.stage.binding"
    _description = "Engine Plant Stage Binding"
    _order = "plant_id, sequence, id"

    plant_id = fields.Many2one(
        "corr.plant.param",
        string="Plant",
        required=True,
        ondelete="cascade",
    )
    stage_template_id = fields.Many2one(
        "corr.engine.stage.template",
        string="Stage Template",
        required=True,
        ondelete="restrict",
    )
    sequence = fields.Integer(
        string="Sequence",
        default=10,
        help="Optional sequence to control display and evaluation order per plant.",
    )
    workcenter_id = fields.Many2one(
        "mrp.workcenter",
        string="Work Center",
        help="Physical work center typically used for this logical stage in this plant.",
    )
    routing_workcenter_id = fields.Many2one(
        "mrp.routing.workcenter",
        string="Routing Operation",
        help="MRP routing operation used to represent this logical stage.",
    )
    merge_with_stage_template_id = fields.Many2one(
        "corr.engine.stage.template",
        string="Merge With Stage",
        help=(
            "Optional reference to another stage template when this stage is merged "
            "into a single physical operation for this plant."
        ),
    )
    active = fields.Boolean(default=True)

    is_system_default = fields.Boolean(
        string="System Default",
        default=False,
        readonly=True,
        help=(
            "Indicates that this binding was created and is maintained by the Phase 8 "
            "seeding tools as part of the standard compatibility profile."
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
            "corr_engine_plant_stage_binding_unique",
            "unique(plant_id, stage_template_id)",
            "Each Stage Template must be bound at most once per plant.",
        ),
        (
            "corr_engine_plant_stage_binding_seed_key_unique",
            "unique(seed_key)",
            "Each Engine Plant Stage Binding seed key must be unique.",
        )
    ]



