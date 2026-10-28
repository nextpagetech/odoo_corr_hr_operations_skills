from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class CorrStageType(models.Model):
    _name = "corr.stage.type"
    _description = "Corrugation Stage Type"
    _order = "sequence, name, id"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    category = fields.Selection(
        selection=[
            ("sfg", "SFG"),
            ("fg", "Finished Goods"),
        ],
        string="Category",
        default="sfg",
        required=True,
    )
    default_operation_type_id = fields.Many2one(
        "corr.operation.type",
        string="Default Operation Type",
        help="Optional operation type to prefill on stage mapping lines.",
    )
    default_representation = fields.Selection(
        [
            ("sfg_and_operation", "SFG + Operation"),
            ("operation_only", "Operation Only"),
            ("hidden_but_compute", "Hidden (compute only)"),
        ],
        default="sfg_and_operation",
        required=True,
    )
    note = fields.Text()

    _sql_constraints = [
        (
            "corr_stage_type_code_unique",
            "unique(code)",
            "Each stage type code must be unique.",
        ),
    ]

    def name_get(self):
        result = []
        for rec in self:
            label = rec.name or rec.code
            if rec.code:
                label = "%s [%s]" % (label, rec.code)
            result.append((rec.id, label))
        return result

    @api.model
    def _corr_stage_code_candidates(self, code):
        code = (code or "").strip()
        if not code:
            return []
        aliases = {
            "2-ply": "corr_2ply",
            "3-ply": "corr_3ply",
            "4-ply": "corr_4ply",
            "board": "corr_board",
            "top_sheet_printed": "top_sheet_print",
            "die-cut": "corr_die",
            "peeling": "corr_peel",
        }
        candidates = [code]
        alias = aliases.get(code.lower())
        if alias and alias not in candidates:
            candidates.append(alias)
        return candidates

    @api.model
    def find_from_code(self, code):
        candidates = self._corr_stage_code_candidates(code)
        if not candidates:
            return self.browse()
        return self.with_context(active_test=False).search(
            [("code", "in", candidates)],
            limit=1,
        )

    @api.model
    def sync_legacy_stage_links(self):
        for model_name in ("product.template", "mrp.bom"):
            Model = self.env[model_name].sudo().with_context(active_test=False)
            if "corr_stage" not in Model._fields or "stage_type_id" not in Model._fields:
                continue
            records = Model.search(
                [
                    ("corr_stage", "!=", False),
                    ("stage_type_id", "=", False),
                ]
            )
            stages = {
                (record.corr_stage or "").strip()
                for record in records
                if (record.corr_stage or "").strip()
            }
            for stage in stages:
                stage_type = self.find_from_code(stage)
                if not stage_type:
                    continue
                records.filtered(
                    lambda record, s=stage: (record.corr_stage or "").strip() == s
                ).write({"stage_type_id": stage_type.id})
        return True


class CorrStageMapping(models.Model):
    _name = "corr.stage.mapping"
    _description = "Corrugation Stage Mapping"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char(string="Internal Code")
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        index=True,
        help="Leave empty to share this mapping across companies.",
    )
    active = fields.Boolean(default=True)
    note = fields.Text()
    line_ids = fields.One2many(
        "corr.stage.mapping.line",
        "mapping_id",
        string="Stage Lines",
    )

    _sql_constraints = [
        (
            "corr_stage_mapping_name_company_unique",
            "unique(name, company_id)",
            "Each Stage Mapping name must be unique per company.",
        ),
    ]


class CorrStageMappingLine(models.Model):
    _name = "corr.stage.mapping.line"
    _description = "Corrugation Stage Mapping Line"
    _order = "sequence, id"

    mapping_id = fields.Many2one(
        "corr.stage.mapping",
        required=True,
        ondelete="cascade",
        string="Stage Mapping",
    )
    sequence = fields.Integer(default=10)
    operation_type_id = fields.Many2one(
        "corr.operation.type",
        string="Operation Type",
        help="Operation type this stage represents (e.g. Corrugation, Printing, Slot/Die, Peeling).",
    )
    stage_type_id = fields.Many2one(
        "corr.stage.type",
        string="Stage Type",
        help="Configurable stage master record.",
    )
    stage_code = fields.Selection(
        selection="_selection_stage_code",
        string="Legacy Stage Code",
    )
    stage_name = fields.Char(string="Stage Name")
    category = fields.Selection(
        selection=[
            ("sfg", "SFG"),
            ("fg", "Finished Goods"),
        ],
        string="Category",
        default="sfg",
        required=True,
    )
    enabled = fields.Boolean(default=True)
    create_sfg = fields.Boolean(
        string="Create SFG",
        help="Compatibility marker for mappings that create an intermediate SFG.",
    )
    is_final_stage = fields.Boolean(
        string="Is Final Stage",
        help="Compatibility marker for the final manufacturing stage.",
    )
    label_override = fields.Char()
    merge_into_operation_type_id = fields.Many2one(
        "corr.operation.type",
        string="Merge Into Operation",
        help="When skip policy is Merge, components and time for this operation will be merged into the selected target operation.",
    )
    skip_policy = fields.Selection(
        [
            ("merge", "Merge into another operation"),
            ("ignore", "Ignore (drop as stage)"),
            ("fallback", "Fallback (keep as-is when no match)"),
        ],
        default="fallback",
        required=True,
    )
    representation = fields.Selection(
        [
            ("sfg_and_operation", "SFG + Operation"),
            ("operation_only", "Operation Only"),
            ("hidden_but_compute", "Hidden (compute only)"),
        ],
        default="sfg_and_operation",
        required=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="mapping_id.company_id",
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        (
            "corr_stage_mapping_line_unique_op",
            "unique(mapping_id, operation_type_id)",
            "Each operation type can appear only once per Stage Mapping.",
        ),
    ]

    @api.model
    def _selection_stage_code(self):
        try:
            StageType = self.env["corr.stage.type"].sudo().with_context(active_test=False)
            self.env.cr.execute("SELECT to_regclass(%s)", (StageType._table,))
            exists = self.env.cr.fetchone()
            if not exists or not exists[0]:
                return []
            return [
                (stage.code, stage.name or stage.code)
                for stage in StageType.search([], order="sequence, name, id")
                if stage.code
            ]
        except Exception:
            return []

    def name_get(self):
        res = []
        for rec in self:
            label = rec.stage_type_id.name or rec.stage_name or rec.stage_code
            if not label and rec.operation_type_id:
                label = rec.operation_type_id.display_name
            res.append((rec.id, label or _("Stage Line")))
        return res

    @api.model
    def _corr_stage_type_defaults(self, vals):
        vals = dict(vals or {})
        StageType = self.env["corr.stage.type"]
        stage_type = StageType.browse()

        if vals.get("stage_type_id"):
            stage_type = StageType.browse(vals["stage_type_id"])
        elif vals.get("stage_code"):
            stage_type = StageType.search([("code", "=", vals["stage_code"])], limit=1)
            if stage_type:
                vals["stage_type_id"] = stage_type.id

        if stage_type:
            vals.setdefault("stage_code", stage_type.code)
            vals.setdefault("stage_name", stage_type.name)
            vals.setdefault("category", stage_type.category)
            vals.setdefault("representation", stage_type.default_representation)
            if stage_type.default_operation_type_id:
                vals.setdefault(
                    "operation_type_id",
                    stage_type.default_operation_type_id.id,
                )
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._corr_stage_type_defaults(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if "stage_type_id" in vals or "stage_code" in vals:
            vals = self._corr_stage_type_defaults(vals)
        return super().write(vals)

    @api.onchange("stage_type_id")
    def _onchange_stage_type_id(self):
        for rec in self:
            stage_type = rec.stage_type_id
            if not stage_type:
                continue
            rec.stage_code = stage_type.code
            rec.stage_name = rec.stage_name or stage_type.name
            rec.category = stage_type.category
            rec.representation = stage_type.default_representation
            if stage_type.default_operation_type_id and not rec.operation_type_id:
                rec.operation_type_id = stage_type.default_operation_type_id

    @api.onchange("stage_code")
    def _onchange_stage_code_set_stage_type(self):
        StageType = self.env["corr.stage.type"]
        for rec in self:
            if not rec.stage_code or rec.stage_type_id:
                continue
            stage_type = StageType.search([("code", "=", rec.stage_code)], limit=1)
            if stage_type:
                rec.stage_type_id = stage_type
                rec.stage_name = rec.stage_name or stage_type.name
                rec.category = stage_type.category

    @api.constrains("operation_type_id", "mapping_id")
    def _check_unique_operation_per_mapping(self):
        for rec in self:
            if not rec.mapping_id or not rec.operation_type_id:
                continue
            dup = self.search_count(
                [
                    ("id", "!=", rec.id),
                    ("mapping_id", "=", rec.mapping_id.id),
                    ("operation_type_id", "=", rec.operation_type_id.id),
                ]
            )
            if dup:
                raise ValidationError(
                    _("Operation '%s' is already used in this Stage Mapping.")
                    % (rec.operation_type_id.display_name,)
                )

    @api.constrains("merge_into_operation_type_id", "operation_type_id")
    def _check_merge_target_not_self(self):
        for line in self:
            if (
                line.merge_into_operation_type_id
                and line.merge_into_operation_type_id == line.operation_type_id
            ):
                raise ValidationError(
                    _("Stage Mapping line for operation '%s' cannot merge into itself.")
                    % (line.operation_type_id.display_name,)
                )

    @api.constrains("skip_policy", "merge_into_operation_type_id", "enabled")
    def _check_merge_policy_requirements(self):
        for line in self:
            if not line.enabled or line.skip_policy != "merge":
                continue
            if not line.merge_into_operation_type_id:
                raise ValidationError(
                    _(
                        "Stage Mapping line for operation '%s' uses skip policy "
                        "'merge' but no merge target is configured."
                    )
                    % (line.operation_type_id.display_name or _("Unknown"),)
                )
            if not line.merge_into_operation_type_id.active:
                raise ValidationError(
                    _("Merge target operation '%s' must be active.")
                    % (line.merge_into_operation_type_id.display_name,)
                )

    @api.constrains("merge_into_operation_type_id", "mapping_id")
    def _check_merge_cycles(self):
        for line in self:
            mapping = line.mapping_id
            if not mapping:
                continue
            graph = {}
            for candidate in mapping.line_ids:
                if candidate.skip_policy == "merge" and candidate.merge_into_operation_type_id:
                    src = candidate.operation_type_id.id
                    tgt = candidate.merge_into_operation_type_id.id
                    if src and tgt:
                        graph.setdefault(src, set()).add(tgt)

            def _dfs(start, current, visited):
                if current in visited:
                    return current == start
                visited.add(current)
                for nxt in graph.get(current, ()):
                    if _dfs(start, nxt, visited):
                        return True
                return False

            for src in graph:
                if _dfs(src, src, set()):
                    raise ValidationError(
                        _(
                            "Stage Mapping '%s' has a merge cycle involving operation type ID %s. "
                            "Please break the cycle."
                        )
                        % (mapping.display_name, src)
                    )
