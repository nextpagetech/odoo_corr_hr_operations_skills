from odoo import api, fields, models


class WorkorderEmployeeWizard(models.TransientModel):
    _inherit = "workorder.employee.wizard"

    override_show_all = fields.Boolean(
        string="Show all employees (override)",
        help="Show all allowed employees, even if they do not have an Operations skill for this operation.",
    )
    no_skilled_employees = fields.Boolean(
        string="No skilled employees",
        compute="_compute_no_skilled_employees",
        help="Technical helper to display a hint when no skilled employees are found.",
    )

    @api.depends(
        "workorder_id.workcenter_id.allowed_employee_category_ids",
        "workorder_id.corr_operation_type_id",
        "override_show_all",
    )
    def _compute_allowed_employee_ids(self):
        """Start from base filtering, then restrict to skilled employees for the operation unless overridden."""
        super()._compute_allowed_employee_ids()

        SkillType = self.env["hr.skill.type"].sudo()
        EmployeeSkill = self.env["hr.employee.skill"].sudo()
        operations_type = SkillType.search(
            [("name", "=", "Operations")], limit=1
        )
        if not operations_type:
            # Operations skill type is not configured; keep base behaviour.
            return

        for wiz in self:
            employees = wiz.allowed_employee_ids
            wo = wiz.workorder_id

            if not employees or not wo or not wo.corr_operation_type_id:
                continue

            # When override is enabled, keep all base-filtered employees.
            if wiz.override_show_all:
                continue

            op_type = wo.corr_operation_type_id
            op_names = set()
            if op_type.name:
                op_names.add(op_type.name.strip())
            if op_type.code:
                op_names.add(op_type.code.strip())
            if not op_names:
                continue

            skills = EmployeeSkill.search(
                [
                    ("employee_id", "in", employees.ids),
                    ("skill_type_id", "=", operations_type.id),
                    ("skill_id.name", "in", list(op_names)),
                ]
            )
            skilled_emp_ids = skills.mapped("employee_id").ids
            wiz.allowed_employee_ids = employees.filtered(
                lambda e: e.id in skilled_emp_ids
            )

    @api.depends(
        "allowed_employee_ids",
        "workorder_id",
        "workorder_id.corr_operation_type_id",
        "override_show_all",
    )
    def _compute_no_skilled_employees(self):
        for wiz in self:
            wo = wiz.workorder_id
            wiz.no_skilled_employees = bool(
                wo
                and wo.corr_operation_type_id
                and not wiz.override_show_all
                and not wiz.allowed_employee_ids
            )

    @api.onchange("workorder_id", "override_show_all")
    def _onchange_workorder_id(self):
        """Ensure employee domain is refreshed when override is toggled."""
        res = super()._onchange_workorder_id()
        if self.workorder_id:
            domain = {"employee_id": [("id", "in", self.allowed_employee_ids.ids)]}
            if res:
                res_domain = res.get("domain") or {}
                res_domain.update(domain)
                res["domain"] = res_domain
                return res
            return {"domain": domain}
        return res or {}

