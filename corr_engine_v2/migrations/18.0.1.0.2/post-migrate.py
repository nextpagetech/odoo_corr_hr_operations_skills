from odoo import api, SUPERUSER_ID


STAGE_TYPE_SEEDS = {
    "seaming": ("Seaming / Jointing", "sfg", "operation_only"),
    "stitching": ("Stitching", "sfg", "operation_only"),
    "pasting": ("Pasting", "sfg", "operation_only"),
    "packing": ("Packing", "sfg", "operation_only"),
}

CAPABILITY_BY_STAGE_TYPE_CODE = {
    "corr_2ply": "corrugation",
    "corr_3ply": "corrugation",
    "corr_4ply": "corrugation",
    "corr_board": "corrugation",
    "top_sheet_cut": "corrugation",
    "sheet_print": "printing",
    "top_sheet_print": "printing",
    "top_sheet_film": "lamination",
    "sheet_diecut": "diecut",
    "corr_die": "diecut",
    "peel_off": "conversion",
    "corr_peel": "conversion",
    "seaming": "conversion",
    "stitching": "conversion",
    "pasting": "conversion",
    "packing": "conversion",
    "fg": "fg",
}

STAGE_TEMPLATE_CODE_MAP = {
    "2ply": "corr_2ply",
    "2-ply": "corr_2ply",
    "corr_2ply": "corr_2ply",
    "3ply": "corr_3ply",
    "3-ply": "corr_3ply",
    "corr_3ply": "corr_3ply",
    "4ply": "corr_4ply",
    "4-ply": "corr_4ply",
    "corr_4ply": "corr_4ply",
    "board": "corr_board",
    "corr_board": "corr_board",
    "ts_cut": "top_sheet_cut",
    "top_sheet_cut": "top_sheet_cut",
    "printing": "sheet_print",
    "print": "sheet_print",
    "sheet_print": "sheet_print",
    "ts_print": "top_sheet_print",
    "top_sheet_print": "top_sheet_print",
    "lamination": "top_sheet_film",
    "film": "top_sheet_film",
    "top_sheet_film": "top_sheet_film",
    "diecut": "corr_die",
    "die-cut": "corr_die",
    "corr_die": "corr_die",
    "sheet_diecut": "sheet_diecut",
    "slot_die": "sheet_diecut",
    "slot_diecut": "sheet_diecut",
    "peeloff": "corr_peel",
    "peel-off": "corr_peel",
    "peeling": "corr_peel",
    "corr_peel": "corr_peel",
    "peel_off": "peel_off",
    "seaming": "seaming",
    "jointing": "seaming",
    "stitching": "stitching",
    "pasting": "pasting",
    "packing": "packing",
    "fg": "fg",
    "finished_goods": "fg",
}

TECHNICAL_STAGE_TYPE_BY_STAGE_TYPE_CODE = {
    "corr_2ply": "corrugation",
    "corr_3ply": "corrugation",
    "corr_4ply": "corrugation",
    "corr_board": "corrugation",
    "top_sheet_cut": "corrugation",
    "sheet_print": "printing",
    "top_sheet_print": "printing",
    "top_sheet_film": "lamination",
    "sheet_diecut": "diecut",
    "corr_die": "diecut",
    "peel_off": "conversion",
    "corr_peel": "conversion",
    "seaming": "conversion",
    "stitching": "conversion",
    "pasting": "conversion",
    "packing": "conversion",
    "fg": "fg",
}


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = %s
           AND column_name = %s
         LIMIT 1
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    StageType = env["corr.stage.type"].sudo().with_context(active_test=False)
    Capability = env["corr.engine.stage.capability"].sudo().with_context(active_test=False)
    StageTemplate = env["corr.engine.stage.template"].sudo().with_context(active_test=False)

    for code, (name, category, representation) in STAGE_TYPE_SEEDS.items():
        if StageType.search([("code", "=", code)], limit=1):
            continue
        StageType.create(
            {
                "code": code,
                "name": name,
                "category": category,
                "default_representation": representation,
                "sequence": 80,
                "active": True,
            }
        )

    for stage_type in StageType.search([("code", "in", list(CAPABILITY_BY_STAGE_TYPE_CODE))]):
        cap_code = CAPABILITY_BY_STAGE_TYPE_CODE.get(stage_type.code)
        capability = Capability.search([("code", "=", cap_code)], limit=1)
        if capability and not stage_type.capability_id:
            stage_type.capability_id = capability

    for template in StageTemplate.search([]):
        key = (template.code or "").strip().lower()
        stage_type_code = STAGE_TEMPLATE_CODE_MAP.get(key)
        if not stage_type_code:
            stage_type_code = STAGE_TEMPLATE_CODE_MAP.get((template.stage_type or "").strip().lower())
        if not stage_type_code:
            continue
        stage_type = StageType.search([("code", "=", stage_type_code)], limit=1)
        if not stage_type:
            continue
        vals = {}
        if not template.stage_type_id:
            vals["stage_type_id"] = stage_type.id
        if not template.stage_type:
            vals["stage_type"] = TECHNICAL_STAGE_TYPE_BY_STAGE_TYPE_CODE.get(stage_type.code, "auxiliary")
        if not template.capability_id and stage_type.capability_id:
            vals["capability_id"] = stage_type.capability_id.id
        if vals:
            template.write(vals)

    # Keep compatibility strings populated even if a record was linked by a
    # manual import before this migration ran.
    if _column_exists(cr, "corr_engine_stage_template", "stage_type_id"):
        for stage_type_code, technical_type in TECHNICAL_STAGE_TYPE_BY_STAGE_TYPE_CODE.items():
            cr.execute(
                """
                UPDATE corr_engine_stage_template tmpl
                   SET stage_type = %s
                  FROM corr_stage_type st
                 WHERE tmpl.stage_type_id = st.id
                   AND st.code = %s
                   AND (tmpl.stage_type IS NULL OR tmpl.stage_type = '')
                """,
                (technical_type, stage_type_code),
            )
