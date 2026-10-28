from odoo import _, api, fields, models


class MrpWorkorderOperationType(models.Model):
    _inherit = "mrp.workorder"

    # Corrugation Operation master (dropdown instead of free-text)
    corr_operation_type_id = fields.Many2one(
        "corr.operation.type",
        string="Operation",
        domain="['&', ('active', '=', True), ('use_in_workorder', '=', True), '|', "
        "('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    # Dynamic helper used to restrict the Work Center selection
    allowed_corr_workcenter_ids = fields.Many2many(
        "mrp.workcenter",
        compute="_compute_allowed_corr_workcenters",
        string="Allowed Work Centers (Corr)",
    )
    # Enforce that Work Center is chosen only from the mapped set,
    # so both the dropdown and "Search More..." respect the mapping.
    workcenter_id = fields.Many2one(
        "mrp.workcenter",
        string="Work Center",
        required=True,
        group_expand="_read_group_workcenter_id",
        check_company=True,
        domain="[('id', 'in', allowed_corr_workcenter_ids)]",
    )

    @api.depends("corr_operation_type_id", "company_id")
    def _compute_allowed_corr_workcenters(self):
        Map = self.env["corr.operation.map"].sudo()
        Workcenter = self.env["mrp.workcenter"]
        allowed_company_ids = self.env.companies.ids
        for wo in self:
            workcenters = Workcenter.browse()
            if wo.corr_operation_type_id:
                code = (wo.corr_operation_type_id.code or "").strip()
                if code:
                    domain = [("active", "=", True), ("op_name", "=", code)]
                    if wo.company_id:
                        domain.append(("workcenter_id.company_id", "=", wo.company_id.id))
                    mappings = Map.search(domain)
                    workcenters = mappings.mapped("workcenter_id")
                    if allowed_company_ids:
                        workcenters = workcenters.filtered(
                            lambda wc: not wc.company_id
                            or wc.company_id.id in allowed_company_ids
                        )
            wo.allowed_corr_workcenter_ids = workcenters

    @api.onchange("corr_operation_type_id")
    def _onchange_corr_operation_type_id(self):
        """Sync name, clear invalid WCs, and dynamically restrict WC domain.

        Domain behaviour:
        - If an operation is selected and mappings exist, limit Work Center to
          those mapped work centers.
        - If an operation is selected but has no mappings, clear Work Center,
          show a warning, and set an empty domain (no options).
        - If no operation is selected, clear Work Center and use an empty
          domain so users must pick an operation first.
        """
        if not self:
            return {}

        wo = self[0]
        domain = [("id", "=", False)]
        warning = None

        if wo.corr_operation_type_id:
            wo.name = wo.corr_operation_type_id.name
            allowed = wo.allowed_corr_workcenter_ids
            if allowed:
                domain = [("id", "in", allowed.ids)]
            # Clear invalid workcenter when operation changes
            if wo.workcenter_id and allowed and wo.workcenter_id not in allowed:
                wo.workcenter_id = False
            # Warn when there is no mapping for the chosen operation
            if not allowed:
                wo.workcenter_id = False
                warning = {
                    "title": _("No mapped work centers"),
                    "message": _(
                        "No Work Centers are mapped for this Operation. "
                        "Please configure the corrugation Operation Mapping first."
                    ),
                }
        else:
            # No operation selected: force WC blank, no choices
            wo.workcenter_id = False

        result = {"domain": {"workcenter_id": domain}}
        if warning:
            result["warning"] = warning
        return result

    @api.model
    def create(self, vals):
        """Ensure operation type and name stay in sync on creation.

        - When a corr_operation_type_id is provided but name is empty, copy the
          operation type name onto the work order.
        - When name is provided but corr_operation_type_id is missing, try to
          infer the operation type from the label.
        """
        OpType = self.env["corr.operation.type"]
        company = None
        company_id = vals.get("company_id")
        if company_id:
            company = self.env["res.company"].browse(company_id)

        if vals.get("corr_operation_type_id") and not vals.get("name"):
            op_type = OpType.browse(vals["corr_operation_type_id"])
            vals["name"] = op_type.name
        elif not vals.get("corr_operation_type_id") and vals.get("name"):
            op_type = OpType.find_from_label(vals.get("name"), company=company)
            if op_type:
                vals["corr_operation_type_id"] = op_type.id

        return super().create(vals)

    def write(self, vals):
        """Keep the work order name aligned with the selected operation type."""
        res = super().write(vals)
        if "corr_operation_type_id" in vals:
            for wo in self:
                if wo.corr_operation_type_id:
                    wo.name = wo.corr_operation_type_id.name
        return res

    @api.model
    def fields_view_get(self, view_id=None, view_type="form", toolbar=False, submenu=False):
        """Inject corr_operation_type_id into Work Orders tree (list) view.

        This replaces the free-text Operation column with a controlled
        corr_operation_type_id dropdown while keeping the underlying name field
        for compatibility with existing logic.
        """
        res = super().fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)
        if not res.get("arch"):
            return res

        try:
            from lxml import etree
        except Exception:
            return res

        arch = res["arch"]
        try:
            doc = etree.fromstring(arch.encode("utf-8"))
        except Exception:
            return res

        # Only adjust list/tree views (Odoo 18 uses <list>, older code sometimes uses <tree>)
        if doc.tag not in ("tree", "list"):
            res["arch"] = etree.tostring(doc, encoding="unicode")
            return res

        # If the view already contains corr_operation_type_id, assume XML did the job.
        if doc.xpath("//field[@name='corr_operation_type_id']"):
            res["arch"] = etree.tostring(doc, encoding="unicode")
            return res

        # Find the first name column (Operation) in the tree
        name_field = None
        for field in doc.xpath("//field[@name='name']"):
            name_field = field
            break
        if name_field is None:
            res["arch"] = etree.tostring(doc, encoding="unicode")
            return res

        parent = name_field.getparent()
        if parent is None:
            res["arch"] = etree.tostring(doc, encoding="unicode")
            return res

        # Insert corr_operation_type_id before name column
        op_field = etree.Element("field", name="corr_operation_type_id")
        op_field.set("string", "Operation")
        if name_field.get("readonly"):
            op_field.set("readonly", name_field.get("readonly"))
        # No quick-create / no edit / no open: enforce selection from the master list only.
        op_field.set(
            "options",
            "{'no_create': True, 'no_create_edit': True, 'no_open': True}",
        )
        parent.insert(parent.index(name_field), op_field)

        # Hide the raw name column in the tree
        name_field.set("column_invisible", "True")

        res["arch"] = etree.tostring(doc, encoding="unicode")
        return res
