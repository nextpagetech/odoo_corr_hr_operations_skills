from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CorrEngineV2OperationCost(models.Model):
    _name = "corr.engine.v2.operation.cost"
    _description = "Engine V2 Operation Cost per Stage"
    _order = "plant_id, sequence, id"

    plant_id = fields.Many2one(
        "corr.plant.param",
        string="Plant",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    stage_key = fields.Char(
        string="Stage Key",
        required=True,
        help=(
            "Engine stage identifier used for operation costing. This can be either "
            "a dynamic process stage (e.g. 2ply, ts_print, diecut, peeloff, seaming, fg) "
            "or a quantity bucket name (raw_material, board, sheet, blanks, finished_goods). "
            "Dynamic keys are mapped to the appropriate quantity bucket for costing."
        ),
    )

    machine_cost_method = fields.Selection(
        [
            ("per_unit", "Per unit"),
            ("per_hour", "Per hour"),
        ],
        string="Machine Cost Method",
        default="per_unit",
        required=True,
        help="Costing basis for machine cost. Currently only 'Per unit' is used in Engine V2 preview.",
    )
    machine_rate = fields.Float(
        string="Machine Rate",
        default=0.0,
        help="Machine cost rate in company currency, interpreted per unit for the 'Per unit' method.",
    )
    machine_setup_time_min = fields.Float(
        string="Machine Setup Time (min)",
        default=0.0,
        help=(
            "Optional machine setup time in minutes for future Engine V2 dynamic "
            "architecture. Not used by current preview costing."
        ),
    )
    machine_run_speed_units_per_hour = fields.Float(
        string="Machine Run Speed (units/hour)",
        default=0.0,
        help=(
            "Optional nominal machine run speed for future per-hour costing in the "
            "dynamic architecture. Not used by current preview costing."
        ),
    )

    labor_cost_method = fields.Selection(
        [
            ("per_unit", "Per unit"),
            ("per_hour", "Per hour"),
        ],
        string="Labor Cost Method",
        default="per_unit",
        required=True,
        help="Costing basis for labor cost. Currently only 'Per unit' is used in Engine V2 preview.",
    )
    labor_rate = fields.Float(
        string="Labor Rate",
        default=0.0,
        help="Labor cost rate in company currency, interpreted per unit for the 'Per unit' method.",
    )
    labor_setup_time_min = fields.Float(
        string="Labor Setup Time (min)",
        default=0.0,
        help=(
            "Optional labor setup time in minutes for future Engine V2 dynamic "
            "architecture. Not used by current preview costing."
        ),
    )
    labor_run_speed_units_per_hour = fields.Float(
        string="Labor Run Speed (units/hour)",
        default=0.0,
        help=(
            "Optional nominal labor run speed for future per-hour costing in the "
            "dynamic architecture. Not used by current preview costing."
        ),
    )

    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "corr_engine_v2_operation_cost_plant_stage_unique",
            "unique(plant_id, stage_key)",
            "Each Stage Key must be unique per plant for Engine V2 operation costing.",
        )
    ]

    @api.constrains("machine_cost_method", "labor_cost_method")
    def _check_supported_cost_methods(self):
        for rec in self:
            unsupported = []
            if rec.machine_cost_method == "per_hour":
                unsupported.append("Machine cost method 'Per hour'")
            if rec.labor_cost_method == "per_hour":
                unsupported.append("Labor cost method 'Per hour'")
            if unsupported:
                raise ValidationError(
                    "Engine V2 operation costing currently supports only 'Per unit' methods "
                    "in preview. Unsupported configuration: %s." % ", ".join(unsupported)
                )

    @api.constrains("stage_key")
    def _check_stage_key(self):
        for rec in self:
            key = (rec.stage_key or "").strip()
            if not key:
                raise ValidationError("Engine V2 operation costing stage_key cannot be empty.")



