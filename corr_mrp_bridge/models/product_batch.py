from odoo import api, fields, models, _


class ProductTemplateBatchCorr(models.Model):
    _inherit = "product.template"

    def action_corr_generate_boms(self):
        """Regenerate/Update BoMs for selected products using corrugation specs.

        - Creates/updates the spec from corr_* fields if missing
        - Applies system parameter defaults for operations/SFG/scrap
        - Posts a note on each product with the outcome
        """
        for tmpl in self.sudo():
            try:
                tmpl._corr_autobom_maybe()
            except Exception as e:
                try:
                    tmpl.message_post(body=("Batch Auto BoM failed: %s" % (str(e))))
                except Exception:
                    # Keep silent if chatter not available
                    pass
        return True

