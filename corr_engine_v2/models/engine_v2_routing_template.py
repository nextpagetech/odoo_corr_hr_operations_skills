from odoo import fields, models


class CorrEngineRoutingTemplate(models.Model):
    _name = "corr.engine.routing.template"
    _description = "Engine Routing Template"

    name = fields.Char(required=True)
    code = fields.Char(string="Code")
    active = fields.Boolean(default=True)

    stage_ids = fields.One2many(
        "corr.engine.routing.template.stage",
        "routing_template_id",
        string="Stages",
    )

    is_system_default = fields.Boolean(
        string="System Default",
        default=False,
        readonly=True,
        help=(
            "Indicates that this routing template was created and is maintained by the "
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
            "corr_engine_routing_template_seed_key_unique",
            "unique(seed_key)",
            "Each Engine Routing Template seed key must be unique.",
        )
    ]


class CorrEngineRoutingTemplateStage(models.Model):
    _name = "corr.engine.routing.template.stage"
    _description = "Engine Routing Template Stage"
    _order = "routing_template_id, sequence, id"

    routing_template_id = fields.Many2one(
        "corr.engine.routing.template",
        string="Routing Template",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    stage_template_id = fields.Many2one(
        "corr.engine.stage.template",
        string="Stage Template",
        required=True,
        ondelete="restrict",
    )
    is_optional = fields.Boolean(
        string="Optional Stage",
        help="When enabled, this stage may be skipped for some products or plants.",
    )
    note = fields.Char(string="Note")



