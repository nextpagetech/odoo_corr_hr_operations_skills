from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["corr.engine.quantity.profile"].sudo().action_seed_default_quantity_profiles()
