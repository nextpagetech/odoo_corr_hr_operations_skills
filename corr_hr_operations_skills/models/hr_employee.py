from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    def _get_operation_performance_scores(self, employee_ids, op_type, days=30):
        """Return a mapping {employee_id: (ratio, count)} for the given operation.

        ratio < 1 means faster than expected on average.
        """
        scores = {}
        if not employee_ids or not op_type:
            return scores

        Workorder = self.env["mrp.workorder"].sudo()
        try:
            date_from = fields.Datetime.now() - relativedelta(days=days)
        except Exception:
            date_from = False

        domain = [
            ("employee_id", "in", employee_ids),
            ("corr_operation_type_id", "=", op_type.id),
            ("state", "=", "done"),
        ]
        if date_from:
            domain.append(("date_finished", ">=", date_from))

        try:
            data = Workorder.read_group(
                domain,
                ["duration_expected:avg", "duration:avg", "id:count"],
                ["employee_id"],
            )
        except Exception:
            return {}

        for row in data:
            employee = row.get("employee_id")
            if not employee:
                continue
            emp_id = employee[0]
            avg_expected = row.get("duration_expected_avg") or 0.0
            avg_actual = row.get("duration_avg") or 0.0
            count = row.get("employee_id_count") or row.get("__count", 0) or 0
            if avg_expected and avg_actual:
                ratio = avg_actual / avg_expected
            else:
                ratio = 1.0
            scores[emp_id] = (ratio, count)
        return scores

    @api.model
    def name_search(self, name="", args=None, operator="ilike", limit=80):
        """Apply skill-based ranking (and optional performance) in the workorder employee picker."""
        ctx = self.env.context or {}
        if not ctx.get("workorder_employee_for_operations"):
            return super().name_search(name=name, args=args, operator=operator, limit=limit)

        # Let the base implementation determine which employees match the search/domain.
        base_res = super().name_search(name=name, args=args, operator=operator, limit=limit)
        if not base_res:
            return base_res

        ids = [emp_id for emp_id, _label in base_res]
        employees = self.browse(ids)

        # Resolve the related work order and operation.
        wo_id = ctx.get("default_workorder_id") or ctx.get("workorder_id")
        if not wo_id and ctx.get("active_model") == "mrp.workorder":
            wo_id = ctx.get("active_id")
        if not wo_id:
            return base_res
        wo = self.env["mrp.workorder"].browse(wo_id)
        if not wo or not wo.exists() or not wo.corr_operation_type_id:
            return base_res
        op_type = wo.corr_operation_type_id

        SkillType = self.env["hr.skill.type"].sudo()
        EmployeeSkill = self.env["hr.employee.skill"].sudo()
        operations_type = SkillType.search([("name", "=", "Operations")], limit=1)
        if not operations_type:
            return base_res

        op_names = set()
        if op_type.name:
            op_names.add(op_type.name.strip())
        if op_type.code:
            op_names.add(op_type.code.strip())
        if not op_names:
            return base_res

        # Fetch skills for these employees and this operation.
        skills = EmployeeSkill.search(
            [
                ("employee_id", "in", ids),
                ("skill_type_id", "=", operations_type.id),
                ("skill_id.name", "in", list(op_names)),
            ]
        )

        # Precompute best skill per employee.
        skill_by_emp = {}
        for sk in skills:
            emp_id = sk.employee_id.id
            level_progress = sk.skill_level_id.level_progress or 0
            # level_progress on hr.employee.skill is related to skill_level_id.level_progress,
            # but we keep it for clarity.
            employee_progress = sk.level_progress or level_progress
            existing = skill_by_emp.get(emp_id)
            if not existing or (
                level_progress,
                employee_progress,
            ) > (
                existing["level_progress"],
                existing["employee_progress"],
            ):
                skill_by_emp[emp_id] = {
                    "skill": sk,
                    "level_progress": level_progress,
                    "employee_progress": employee_progress,
                }

        # Optional: refine ranking using recent performance per operation.
        performance_scores = {}
        if skill_by_emp:
            try:
                performance_scores = self._get_operation_performance_scores(
                    list(skill_by_emp.keys()), op_type, days=30
                )
            except Exception:
                performance_scores = {}

        # Build a score per employee for sorting.
        score_by_emp = {}
        for emp in employees:
            info = skill_by_emp.get(emp.id)
            if not info:
                # Unskilled employees: rank after skilled ones.
                score_by_emp[emp.id] = {
                    "has_skill": False,
                    "level_progress": 0,
                    "employee_progress": 0,
                    "perf_score": 0.0,
                }
                continue

            level_progress = info["level_progress"]
            employee_progress = info["employee_progress"]
            ratio, count = performance_scores.get(emp.id, (1.0, 0))
            # Lower ratio and higher count are better; convert to a single score.
            perf_score = -ratio + min(count, 50) * 0.01
            score_by_emp[emp.id] = {
                "has_skill": True,
                "level_progress": level_progress,
                "employee_progress": employee_progress,
                "perf_score": perf_score,
                "skill": info["skill"],
            }

        if not score_by_emp:
            # No skills matched; keep the base order/labels.
            return base_res

        base_labels = {emp_id: label for emp_id, label in base_res}

        def sort_key(emp):
            data = score_by_emp.get(emp.id)
            if not data:
                # Unskilled: after skilled employees.
                return (1, 0, 0, 0.0, emp.name or "")
            return (
                0 if data["has_skill"] else 1,
                -data["level_progress"],
                -data["employee_progress"],
                -data["perf_score"],
                emp.name or "",
            )

        sorted_emps = sorted(employees, key=sort_key)
        if limit:
            sorted_emps = sorted_emps[:limit]

        # Build display labels: "Name — Level (XX%)" when a skill is available.
        result = []
        for emp in sorted_emps:
            data = score_by_emp.get(emp.id)
            if data and data.get("skill"):
                skill = data["skill"]
                level_name = skill.skill_level_id.name or ""
                progress = int(data["employee_progress"] or 0)
                label = f"{emp.name} — {level_name} ({progress}%)"
            else:
                label = base_labels.get(emp.id) or emp.name
            result.append((emp.id, label))

        return result
