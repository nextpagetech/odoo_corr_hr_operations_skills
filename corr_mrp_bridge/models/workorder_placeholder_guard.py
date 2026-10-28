from odoo import models, _
from odoo.exceptions import UserError


class MrpWorkorderCorrPlaceholderGuard(models.Model):
    _inherit = "mrp.workorder"

    def button_start(self, raise_on_invalid_state=False):
        """Prevent starting work orders on placeholder corrugation work centers.

        Any work order whose work center is explicitly marked as a corrugation
        placeholder must be reassigned to a real work center before it can be
        started, regardless of whether the MO BoM is the FG or an SFG.
        """
        for wo in self:
            wc = wo.workcenter_id
            if (
                wc
                and getattr(wc, "corr_is_placeholder", False)
            ):
                raise UserError(
                    _(
                        "Work center '%(wc)s' is configured as a corrugation placeholder.\n"
                        "Please assign a real work center on the BoM operation before starting this work order.",
                        wc=wc.display_name,
                    )
                )
        return super().button_start(raise_on_invalid_state=raise_on_invalid_state)
