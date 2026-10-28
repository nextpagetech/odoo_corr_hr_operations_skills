from odoo import fields, models


class CorrLockingTypeExt(models.Model):
    _inherit = "corr.locking.type"

    # Extend locking method to support plant case where both gluing and stitching are used
    # Odoo 17+: required selection fields need an ondelete policy for added options.
    # Use 'set default' so uninstalling this module resets to the base default ('glue').
    method = fields.Selection(
        selection_add=[("both", "Both (Gluing + Stitching)")],
        ondelete={"both": "set default"},
    )
