from collections import Counter

from odoo import api, SUPERUSER_ID


DEFAULTS_BY_STAGE_CODE = {
    "corr_2ply": {
        "default_stage_type": "corrugation",
        "default_execution_type": "material",
        "capability": "corrugation",
        "default_output_kind": "board",
        "default_output_stock_behavior": "semi_finished",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "sheet_based_board",
    },
    "corr_3ply": {
        "default_stage_type": "corrugation",
        "default_execution_type": "material",
        "capability": "corrugation",
        "default_output_kind": "board",
        "default_output_stock_behavior": "semi_finished",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "sheet_based_board",
    },
    "corr_4ply": {
        "default_stage_type": "corrugation",
        "default_execution_type": "material",
        "capability": "corrugation",
        "default_output_kind": "board",
        "default_output_stock_behavior": "semi_finished",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "sheet_based_board",
    },
    "corr_board": {
        "default_stage_type": "corrugation",
        "default_execution_type": "material",
        "capability": "corrugation",
        "default_output_kind": "board",
        "default_output_stock_behavior": "semi_finished",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "sheet_based_board",
    },
    "top_sheet_cut": {
        "default_stage_type": "corrugation",
        "default_execution_type": "operation",
        "capability": "auxiliary",
        "default_output_kind": "sheet",
        "default_output_stock_behavior": "non_stock",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "per_fg_unit",
    },
    "sheet_print": {
        "default_stage_type": "printing",
        "default_execution_type": "operation",
        "capability": "printing",
        "default_output_kind": "sheet",
        "default_output_stock_behavior": "non_stock",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "area_based_printing",
    },
    "top_sheet_print": {
        "default_stage_type": "printing",
        "default_execution_type": "operation",
        "capability": "printing",
        "default_output_kind": "sheet",
        "default_output_stock_behavior": "non_stock",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "area_based_printing",
    },
    "top_sheet_film": {
        "default_stage_type": "lamination",
        "default_execution_type": "operation",
        "capability": "lamination",
        "default_output_kind": "sheet",
        "default_output_stock_behavior": "non_stock",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "area_based_printing",
    },
    "sheet_diecut": {
        "default_stage_type": "diecut",
        "default_execution_type": "operation",
        "capability": "diecut",
        "default_output_kind": "sheet",
        "default_output_stock_behavior": "non_stock",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "per_fg_unit",
    },
    "corr_die": {
        "default_stage_type": "diecut",
        "default_execution_type": "operation",
        "capability": "diecut",
        "default_output_kind": "sheet",
        "default_output_stock_behavior": "non_stock",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "per_fg_unit",
    },
    "peel_off": {
        "default_stage_type": "conversion",
        "default_execution_type": "operation",
        "capability": "conversion",
        "default_output_kind": "box",
        "default_output_stock_behavior": "non_stock",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "per_fg_unit",
    },
    "corr_peel": {
        "default_stage_type": "conversion",
        "default_execution_type": "operation",
        "capability": "conversion",
        "default_output_kind": "box",
        "default_output_stock_behavior": "non_stock",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "per_fg_unit",
    },
    "seaming": {
        "default_stage_type": "conversion",
        "default_execution_type": "material",
        "capability": "conversion",
        "default_output_kind": "box",
        "default_output_stock_behavior": "semi_finished",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "per_fg_unit",
    },
    "stitching": {
        "default_stage_type": "conversion",
        "default_execution_type": "material",
        "capability": "conversion",
        "default_output_kind": "box",
        "default_output_stock_behavior": "semi_finished",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "per_fg_unit",
    },
    "pasting": {
        "default_stage_type": "conversion",
        "default_execution_type": "material",
        "capability": "conversion",
        "default_output_kind": "box",
        "default_output_stock_behavior": "semi_finished",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "per_fg_unit",
    },
    "packing": {
        "default_stage_type": "conversion",
        "default_execution_type": "operation",
        "capability": "conversion",
        "default_output_kind": "box",
        "default_output_stock_behavior": "non_stock",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "per_fg_unit",
    },
    "fg": {
        "default_stage_type": "fg",
        "default_execution_type": "material",
        "capability": "fg",
        "default_output_kind": "fg",
        "default_output_stock_behavior": "fg",
        "default_bom_mapping_policy": "own_operation",
        "quantity_profile": "per_fg_unit",
    },
}

DEFAULT_FIELDS = [
    "default_stage_type",
    "default_execution_type",
    "default_output_kind",
    "default_output_stock_behavior",
    "default_bom_mapping_policy",
]

TEMPLATE_TO_MASTER_FIELDS = {
    "stage_type": "default_stage_type",
    "execution_type": "default_execution_type",
    "output_kind": "default_output_kind",
    "output_stock_behavior": "default_output_stock_behavior",
    "bom_mapping_policy": "default_bom_mapping_policy",
    "capability_id": "capability_id",
    "quantity_profile_id": "default_quantity_profile_id",
}


def _most_common(records, field_name):
    values = []
    for record in records:
        value = record[field_name]
        if not value:
            continue
        if hasattr(value, "id"):
            value = value.id
        values.append(value)
    if not values:
        return False
    return Counter(values).most_common(1)[0][0]


def _template_differs_from_master(template, master):
    for template_field, master_field in TEMPLATE_TO_MASTER_FIELDS.items():
        master_value = master[master_field]
        if not master_value:
            continue
        template_value = template[template_field]
        if hasattr(master_value, "id"):
            master_value = master_value.id
        if hasattr(template_value, "id"):
            template_value = template_value.id
        if template_value and template_value != master_value:
            return True
    return False


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    StageType = env["corr.stage.type"].sudo().with_context(active_test=False)
    StageTemplate = env["corr.engine.stage.template"].sudo().with_context(active_test=False)
    Capability = env["corr.engine.stage.capability"].sudo().with_context(active_test=False)
    QuantityProfile = env["corr.engine.quantity.profile"].sudo().with_context(active_test=False)

    capabilities = {cap.code: cap for cap in Capability.search([])}
    quantity_profiles = {profile.code: profile for profile in QuantityProfile.search([])}

    for stage_master in StageType.search([]):
        templates = StageTemplate.search([("stage_type_id", "=", stage_master.id)])
        explicit = DEFAULTS_BY_STAGE_CODE.get(stage_master.code or "", {})
        vals = {}

        for field_name in DEFAULT_FIELDS:
            if not stage_master[field_name]:
                value = _most_common(templates, field_name.replace("default_", "", 1))
                if value:
                    vals[field_name] = value
                elif explicit.get(field_name):
                    vals[field_name] = explicit[field_name]

        if not stage_master.capability_id:
            value = _most_common(templates, "capability_id")
            if value:
                vals["capability_id"] = value
            elif explicit.get("capability") and capabilities.get(explicit["capability"]):
                vals["capability_id"] = capabilities[explicit["capability"]].id

        if not stage_master.default_quantity_profile_id:
            value = _most_common(templates, "quantity_profile_id")
            if value:
                vals["default_quantity_profile_id"] = value
            elif explicit.get("quantity_profile") and quantity_profiles.get(explicit["quantity_profile"]):
                vals["default_quantity_profile_id"] = quantity_profiles[explicit["quantity_profile"]].id

        if vals:
            stage_master.write(vals)

    for template in StageTemplate.search([("stage_type_id", "!=", False)]):
        if _template_differs_from_master(template, template.stage_type_id) and not template.advanced_overrides:
            template.advanced_overrides = True
