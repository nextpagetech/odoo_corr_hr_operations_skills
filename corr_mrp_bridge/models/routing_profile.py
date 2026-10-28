from odoo import fields, models


class CorrEngineRoutingProfile(models.Model):
    _name = "corr.engine.routing.profile"
    _description = "Engine Routing Profile"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)

    stage_ids = fields.One2many(
        "corr.engine.routing.profile.stage",
        "routing_profile_id",
        string="Stages",
    )

    def action_backfill_stage_templates(self):
        """Best-effort backfill for existing routing profile lines.

        For legacy records that only have a free-text stage_code, this
        helper attempts to match them to corr.engine.stage.template
        records by code and populate stage_template_id accordingly.

        When no matching template is found, the line is left unchanged
        so that admins can fix it explicitly; no silent failures.
        """

        StageLine = self.env["corr.engine.routing.profile.stage"].sudo()
        if (
            "corr.engine.stage.template" not in self.env.registry
            or "stage_template_id" not in StageLine._fields
        ):
            return True

        StageTemplate = self.env["corr.engine.stage.template"].sudo()
        for profile in self:
            lines = StageLine.search(
                [("routing_profile_id", "=", profile.id)]
            )
            for line in lines:
                if line.stage_template_id or not line.stage_code:
                    continue
                tmpl = StageTemplate.search(
                    [("code", "=", line.stage_code.strip())], limit=1
                )
                if not tmpl:
                    # No matching template; surface clearly in logs so
                    # admins can diagnose and fix.
                    self.env["ir.logging"].sudo().create(
                        {
                            "name": "corr_mrp_bridge.routing_profile.backfill",
                            "type": "server",
                            "level": "WARNING",
                            "dbname": self.env.cr.dbname,
                            "message": (
                                "Routing Profile %s has stage line '%s' with code '%s' "
                                "that could not be matched to any Engine Stage Template."
                            )
                            % (
                                profile.display_name,
                                line.display_name,
                                line.stage_code,
                            ),
                            "path": __name__,
                            "func": "action_backfill_stage_templates",
                            "line": 0,
                        }
                    )
                    continue
                line.stage_template_id = tmpl.id


class CorrEngineRoutingProfileStage(models.Model):
    _name = "corr.engine.routing.profile.stage"
    _description = "Engine Routing Profile Stage"
    _order = "routing_profile_id, sequence, id"

    routing_profile_id = fields.Many2one(
        "corr.engine.routing.profile",
        string="Routing Profile",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    stage_code = fields.Char(
        string="Stage Code",
        help=(
            "Technical code of the stage. Engine V2 can bind this to an "
            "Engine Stage Template when the engine module is installed."
        ),
    )
    stage_type = fields.Selection(
        [
            ("material", "Material"),
            ("operation", "Operation"),
        ],
        string="Stage Type",
        required=True,
        default="operation",
        help="Material vs operation classification derived from the selected stage template.",
    )
    waste_percent = fields.Float(string="Waste %", digits=(16, 3))

