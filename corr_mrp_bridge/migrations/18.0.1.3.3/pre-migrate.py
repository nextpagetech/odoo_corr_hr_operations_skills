FIXED_PLANT_SELECTION_ARCH = """<data>
  <xpath expr="//group[.//field[@name='finishing_line_ids']]" position="after">
    <group string="Plant Selection">
      <field name="plant_param_id" required="1"/>
      <field name="plant_process_waste_pct"/>
      <field name="plant_flap_basis"/>
      <field name="plant_flap_fixed_mm"/>
      <field name="plant_flap_percent" invisible="plant_flap_basis == 'fixed'"/>
      <field name="plant_flap_percent_of" invisible="plant_flap_basis == 'fixed'"/>
    </group>
  </xpath>
</data>"""


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        UPDATE ir_ui_view
           SET arch_db = jsonb_build_object('en_US', %s),
               active = true
         WHERE name::text ILIKE '%%plant.selection.bridge%%'
            OR (
                arch_db::text ILIKE '%%//page%%'
                AND arch_db::text ILIKE '%%finishing_line_ids%%'
            )
        """,
        [FIXED_PLANT_SELECTION_ARCH],
    )

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
