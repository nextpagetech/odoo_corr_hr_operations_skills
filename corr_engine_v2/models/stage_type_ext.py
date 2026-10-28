from odoo import fields, models


STAGE_TYPE_SELECTION = [
    ("corrugation", "Corrugation"),
    ("printing", "Printing"),
    ("lamination", "Lamination"),
    ("diecut", "Die-cut"),
    ("conversion", "Conversion / Seaming"),
    ("fg", "Finished Goods"),
    ("auxiliary", "Auxiliary"),
]

EXECUTION_TYPE_SELECTION = [
    ("material", "Material"),
    ("operation", "Operation"),
]

OUTPUT_KIND_SELECTION = [
    ("fg", "Finished Goods"),
    ("box", "Box"),
    ("board", "Board"),
    ("sheet", "Sheet"),
    ("roll", "Roll"),
    ("generic", "Generic Intermediate"),
]

OUTPUT_STOCK_BEHAVIOR_SELECTION = [
    ("fg", "Finished Goods"),
    ("semi_finished", "Semi-finished"),
    ("non_stock", "Non-stock Logical"),
]

BOM_MAPPING_POLICY_SELECTION = [
    ("own_operation", "Own Operation"),
    ("merge_to_material_parent", "Merge Into Material Stage"),
    ("logical_only", "Logical Only"),
]


class CorrStageType(models.Model):
    _inherit = "corr.stage.type"

    category = fields.Selection(
        selection_add=[("operation", "Operation")],
        ondelete={"operation": "set default"},
    )
    capability_id = fields.Many2one(
        "corr.engine.stage.capability",
        string="Default Engine Capability",
        help=(
            "Default Engine V2 capability used when an Engine Stage Template "
            "selects this operation/stage type."
        ),
    )
    default_stage_type = fields.Selection(
        STAGE_TYPE_SELECTION,
        string="Default Technical Stage Type",
        help="Compatibility stage type copied to Engine Stage Templates.",
    )
    default_execution_type = fields.Selection(
        EXECUTION_TYPE_SELECTION,
        string="Default Execution Type",
        help="Default material/operation behavior for Engine Stage Templates.",
    )
    default_output_stock_behavior = fields.Selection(
        OUTPUT_STOCK_BEHAVIOR_SELECTION,
        string="Default Stock Behaviour",
    )
    default_output_kind = fields.Selection(
        OUTPUT_KIND_SELECTION,
        string="Default Output Kind",
    )
    default_bom_mapping_policy = fields.Selection(
        BOM_MAPPING_POLICY_SELECTION,
        string="Default BoM Mapping Policy",
    )
    default_quantity_profile_id = fields.Many2one(
        "corr.engine.quantity.profile",
        string="Default Quantity Profile",
    )


class CorrStageMappingLine(models.Model):
    _inherit = "corr.stage.mapping.line"

    category = fields.Selection(
        selection_add=[("operation", "Operation")],
        ondelete={"operation": "set default"},
    )
