from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class CorrEngineRoutingProfileStage(models.Model):
    _inherit = "corr.engine.routing.profile.stage"

    stage_template_id = fields.Many2one(
        "corr.engine.stage.template",
        string="Stage Template",
        required=True,
        ondelete="restrict",
        help="Stage template used as the source of truth for this routing step.",
    )

    def _map_template_stage_type(self, template) -> str:
        execution_type = str(getattr(template, "execution_type", "") or "").strip().lower()
        if execution_type in ("material", "operation"):
            return execution_type

        stype = str(getattr(template, "stage_type", "") or "").strip().lower()
        if stype in ("corrugation", "conversion", "fg"):
            return "material"
        return "operation"

    def _apply_template_to_line(self):
        for rec in self:
            tmpl = rec.stage_template_id
            if not tmpl:
                continue
            rec.stage_code = tmpl.code or ""
            rec.stage_type = self._map_template_stage_type(tmpl)

    @api.onchange("stage_template_id")
    def _onchange_stage_template_id(self):
        self._apply_template_to_line()

    @api.model
    def create(self, vals):
        vals = dict(vals or {})
        tmpl_id = vals.get("stage_template_id")
        if tmpl_id:
            tmpl = self.env["corr.engine.stage.template"].browse(tmpl_id)
            if tmpl:
                vals.setdefault("stage_code", tmpl.code or "")
                vals["stage_type"] = self._map_template_stage_type(tmpl)
        return super().create(vals)

    def write(self, vals):
        res = super().write(vals)
        if "stage_template_id" in vals:
            self._apply_template_to_line()
        return res

    @api.constrains("stage_template_id")
    def _check_stage_template_active(self):
        for rec in self:
            tmpl = rec.stage_template_id
            if tmpl and not tmpl.active:
                raise ValidationError(
                    _(
                        "Stage Template '%s' selected on routing profile '%s' is inactive."
                    )
                    % (tmpl.display_name, rec.routing_profile_id.display_name)
                )

    @api.constrains("routing_profile_id", "stage_template_id", "sequence")
    def _check_sequence_and_template(self):
        for rec in self:
            if not rec.routing_profile_id or not rec.stage_template_id:
                continue
            dup = self.search_count(
                [
                    ("id", "!=", rec.id),
                    ("routing_profile_id", "=", rec.routing_profile_id.id),
                    ("stage_template_id", "=", rec.stage_template_id.id),
                    ("sequence", "=", rec.sequence),
                ]
            )
            if dup:
                raise ValidationError(
                    _(
                        "Routing Profile '%s' has duplicate stage '%s' at sequence %s."
                    )
                    % (
                        rec.routing_profile_id.display_name,
                        rec.stage_template_id.display_name,
                        rec.sequence,
                    )
                )
