from odoo import _, fields, models


class CorrEngineV2ContractSeedWizard(models.TransientModel):
    _name = "corr.engine.v2.contract.seed.wizard"
    _description = "Engine V2 Contract Seeding / Normalisation"

    seed_capabilities = fields.Boolean(
        string="Seed Stage Capabilities",
        default=True,
        help="Create or update standard Engine Stage Capabilities.",
    )
    seed_input_roles = fields.Boolean(
        string="Seed Input Roles",
        default=True,
        help="Create or update standard Engine Input Roles.",
    )
    normalise_stage_templates = fields.Boolean(
        string="Apply Contract Defaults to Routed Stage Templates",
        default=True,
        help=(
            "Apply non-destructive contract defaults to Engine Stage Templates that "
            "are used in active routing profiles. Only missing values are filled."
        ),
    )

    def action_run(self):
        self.ensure_one()
        # Use the current environment; this method is only exposed to
        # admins / corrugation managers via the UI. Avoid calling sudo()
        # here so that shell usage with a bare env also works.
        env = self.env

        if self.seed_capabilities:
            env["corr.engine.stage.capability"].action_seed_default_capabilities()

        if self.seed_input_roles:
            env["corr.engine.input.role"].action_seed_default_input_roles()

        if self.normalise_stage_templates:
            RoutingStage = env["corr.engine.routing.profile.stage"]
            StageTemplate = env["corr.engine.stage.template"]

            lines = RoutingStage.search(
                [
                    ("routing_profile_id.active", "=", True),
                    ("stage_template_id", "!=", False),
                ]
            )
            tmpl_ids = {l.stage_template_id.id for l in lines if l.stage_template_id}
            if tmpl_ids:
                StageTemplate.browse(list(tmpl_ids)).action_seed_contract_defaults()

        # Close the wizard and surface a small confirmation.
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Engine V2 contract seeding completed"),
                "message": _("Stage capabilities, input roles, and routed stage templates have been processed."),
                "sticky": False,
            },
        }



