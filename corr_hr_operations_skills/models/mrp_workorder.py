from odoo import api, fields, models


class MrpWorkorder(models.Model):
    _inherit = "mrp.workorder"

    # Restrict employee selection to those allowed for this work order.
    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        domain="[('id', 'in', operation_allowed_employee_ids)]",
    )

    operation_allowed_employee_ids = fields.Many2many(
        "hr.employee",
        compute="_compute_operation_allowed_employee_ids",
        string="Allowed Employees (Operations)",
        help="Employees allowed for this work order based on work center tags and Operations skills.",
    )

    @api.depends(
        "workcenter_id.allowed_employee_category_ids",
        "company_id",
        "corr_operation_type_id",
    )
    def _compute_operation_allowed_employee_ids(self):
        Employee = self.env["hr.employee"].sudo()
        SkillType = self.env["hr.skill.type"].sudo()
        EmployeeSkill = self.env["hr.employee.skill"].sudo()
        operations_type = SkillType.search([("name", "=", "Operations")], limit=1)

        for wo in self:
            domain = [("active", "=", True)]
            if wo.company_id:
                domain.append(
                    ("company_id", "in", [False, wo.company_id.id])
                )
            wc = wo.workcenter_id
            if wc and wc.allowed_employee_category_ids:
                domain.append(
                    ("category_ids", "in", wc.allowed_employee_category_ids.ids)
                )

            employees = Employee.search(domain)

            # If Operations skill type or operation is not configured, keep base filtering.
            if not operations_type or not wo.corr_operation_type_id:
                wo.operation_allowed_employee_ids = employees
                continue

            op_names = set()
            if wo.corr_operation_type_id.name:
                op_names.add(wo.corr_operation_type_id.name.strip())
            if wo.corr_operation_type_id.code:
                op_names.add(wo.corr_operation_type_id.code.strip())
            if not op_names:
                wo.operation_allowed_employee_ids = employees
                continue

            skills = EmployeeSkill.search(
                [
                    ("employee_id", "in", employees.ids),
                    ("skill_type_id", "=", operations_type.id),
                    ("skill_id.name", "in", list(op_names)),
                ]
            )
            skilled_ids = skills.mapped("employee_id").ids
            wo.operation_allowed_employee_ids = employees.filtered(
                lambda e: e.id in skilled_ids
            )

    def _ensure_employee_or_open_wizard(self, action_from="start"):
        action = super()._ensure_employee_or_open_wizard(action_from=action_from)
        if not action:
            return action
        ctx = dict(action.get("context") or {})
        # Flag this context so hr.employee name_search can apply skill-based ranking.
        ctx.setdefault("workorder_employee_for_operations", True)
        # Ensure the work order id is always available in context.
        ctx.setdefault("default_workorder_id", self.id)
        action["context"] = ctx
        return action
