from odoo import api, models


class HrSkillOperationsSync(models.Model):
    _inherit = "hr.skill"

    @api.model
    def _get_operations_skill_type(self):
        """Return the Operations hr.skill.type record, or False if missing."""
        return (
            self.env["hr.skill.type"]
            .sudo()
            .search([("name", "=", "Operations")], limit=1)
        )

    @api.model
    def _get_engine_operation_names(self):
        """Return the canonical list of corrugation operation names.

        Preferred source is corr.operation.map selection via corr.operation.type
        helper; fallback is the corr.operation.type records themselves.
        """
        OperationType = self.env["corr.operation.type"].sudo()

        names = []

        # Preferred: use the canonical selection on corr.operation.map
        try:
            if hasattr(OperationType, "_corr_selection_source"):
                selection = OperationType._corr_selection_source()
                for code, label in selection or []:
                    name = label or code
                    if name and name not in names:
                        names.append(name)
        except Exception:
            selection = []

        if names:
            return names

        # Fallback: use active corr.operation.type names (global types only)
        try:
            operation_types = OperationType.search(
                [("active", "=", True), ("company_id", "=", False)]
            )
            for op in operation_types:
                if op.name and op.name not in names:
                    names.append(op.name)
        except Exception:
            return []

        return names

    @api.model
    def _sync_operations_skills(self):
        """Ensure hr.skill records exist for each corrugation operation.

        Idempotent and safe to call multiple times.
        """
        operations_type = self._get_operations_skill_type()
        if not operations_type:
            # Operations skill type or levels may not exist yet (e.g. install order);
            # nothing to do in that case.
            return

        operation_names = self._get_engine_operation_names()
        if not operation_names:
            return

        existing_skills = {
            skill.name
            for skill in self.sudo().search(
                [("skill_type_id", "=", operations_type.id)]
            )
        }

        create_vals = []
        for name in operation_names:
            if name in existing_skills:
                continue
            create_vals.append(
                {
                    "name": name,
                    "skill_type_id": operations_type.id,
                }
            )

        if create_vals:
            self.sudo().create(create_vals)

