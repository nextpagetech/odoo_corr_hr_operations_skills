STAGE_TYPE_XMLIDS = {
    "corr_stage_type_2ply": "corr_2ply",
    "corr_stage_type_3ply": "corr_3ply",
    "corr_stage_type_4ply": "corr_4ply",
    "corr_stage_type_board": "corr_board",
    "corr_stage_type_sheet_print": "sheet_print",
    "corr_stage_type_sheet_diecut": "sheet_diecut",
    "corr_stage_type_peel_off": "peel_off",
    "corr_stage_type_finished_goods": "fg",
    "corr_stage_type_top_sheet_cut": "top_sheet_cut",
    "corr_stage_type_top_sheet_print": "top_sheet_print",
    "corr_stage_type_top_sheet_film": "top_sheet_film",
    "corr_stage_type_corr_die": "corr_die",
    "corr_stage_type_corr_peel": "corr_peel",
}


def migrate(cr, version):
    if not version:
        return

    for xmlid_name, code in STAGE_TYPE_XMLIDS.items():
        cr.execute(
            """
            SELECT id
              FROM corr_stage_type
             WHERE code = %s
             ORDER BY id
             LIMIT 1
            """,
            [code],
        )
        row = cr.fetchone()
        if not row:
            continue
        stage_type_id = row[0]

        cr.execute(
            """
            UPDATE ir_model_data
               SET model = 'corr.stage.type',
                   res_id = %s,
                   noupdate = true
             WHERE module = 'corr_mrp_bridge'
               AND name = %s
            """,
            [stage_type_id, xmlid_name],
        )
        if cr.rowcount:
            continue

        cr.execute(
            """
            INSERT INTO ir_model_data
                (module, name, model, res_id, noupdate)
            VALUES
                ('corr_mrp_bridge', %s, 'corr.stage.type', %s, true)
            """,
            [xmlid_name, stage_type_id],
        )
