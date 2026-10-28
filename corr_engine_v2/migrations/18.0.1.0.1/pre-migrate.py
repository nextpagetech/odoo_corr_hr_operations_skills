def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        UPDATE ir_ui_view
           SET active = false
         WHERE name::text = 'corr.engine.routing.profile.form.engine.v2'
            OR id IN (
                SELECT res_id
                  FROM ir_model_data
                 WHERE module = 'corr_engine_v2'
                   AND name = 'view_corr_engine_routing_profile_form_engine_v2'
                   AND model = 'ir.ui.view'
            )
        """
    )
