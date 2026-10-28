from odoo import fields, models


class CorrPlantParam(models.Model):
    _inherit = "corr.plant.param"

    sheet_length_mm = fields.Float(string="Sheet Length (mm)")
    sheet_width_mm = fields.Float(string="Sheet Width (mm)")
    use_dynamic_stage_architecture = fields.Boolean(
        string="Use Dynamic Stage Architecture",
        help=(
            "When enabled, this plant can opt into the Engine V2 dynamic, stage-first "
            "architecture. This flag is reserved for future phases; it does not change "
            "current Engine V2 behaviour yet."
        ),
    )
    phase8_routing_template_id = fields.Many2one(
        "corr.engine.routing.template",
        string="Phase 8 Routing Template",
        help=(
            "Optional default Engine V2 routing template used by the Phase 8 dynamic "
            "architecture in shadow mode. This does not affect current Engine V2 "
            "routing or BoM behaviour."
        ),
    )
    engine_v2_operation_cost_ids = fields.One2many(
        "corr.engine.v2.operation.cost",
        "plant_id",
        string="Engine V2 Operation Costs",
    )
    enable_stitching_wire = fields.Boolean(
        string="Enable Stitching Wire",
        help="Default Engine V2 consumable switch for stitching wire at this plant.",
    )
    wire_stitches_per_cm_default = fields.Float(
        string="Wire Stitches per cm",
        help="Default stitch density used to calculate Engine V2 stitching wire consumption.",
    )
    wire_g_per_stitch_default = fields.Float(
        string="Wire g per Stitch",
        help="Default wire weight per stitch used to calculate Engine V2 stitching wire consumption.",
    )



