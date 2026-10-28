import logging
import re
from typing import List

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CorrEngineStageTemplate(models.Model):
    _name = "corr.engine.stage.template"
    _description = "Engine Stage Template"
    _order = "sequence, id"

    name = fields.Char(required=True)
    code = fields.Char(
        string="Stage Code",
        required=True,
        help="Unique technical code for this logical engine stage (e.g. 2ply, ts_print, lamination, diecut, seaming, fg).",
    )
    sequence = fields.Integer(default=10)

    execution_type = fields.Selection(
        [
            ("material", "Material"),
            ("operation", "Operation"),
        ],
        string="Execution Type",
        default="material",
        help=(
            "Execution class of this stage in the generic engine. "
            "Material stages form the backbone; operation stages attach to material stages."
        ),
    )
    capability_id = fields.Many2one(
        "corr.engine.stage.capability",
        string="Capability",
        help="Business capability / role for this stage (cutting, printing, board, FG, etc.).",
    )
    stage_type_id = fields.Many2one(
        "corr.stage.type",
        string="Stage Master",
        help=(
            "User-facing operation/stage type master. The technical Stage Type "
            "selection remains populated for Engine V2 compatibility."
        ),
    )
    consumes_previous_output = fields.Boolean(
        string="Consumes Previous Output",
        help=(
            "When enabled, this stage expects the previous stage's output as its main input."
        ),
    )
    previous_output_required = fields.Boolean(
        string="Previous Output Required",
        help=(
            "When enabled together with 'Consumes Previous Output', this stage is "
            "invalid without a previous output in the routing chain."
        ),
    )
    output_kind = fields.Selection(
        [
            ("fg", "Finished Goods"),
            ("box", "Box"),
            ("board", "Board"),
            ("sheet", "Sheet"),
            ("roll", "Roll"),
            ("generic", "Generic Intermediate"),
        ],
        string="Output Kind",
        help="High-level kind of output this stage produces for the generic engine.",
    )
    output_stock_behavior = fields.Selection(
        [
            ("fg", "Finished Goods"),
            ("semi_finished", "Semi-finished"),
            ("non_stock", "Non-stock Logical"),
        ],
        string="Stock Behaviour",
        default="non_stock",
        help=(
            "How the generic engine should treat this stage's output in terms of stock: "
            "finished goods, semi-finished product, or non-stock logical output."
        ),
    )
    can_be_terminal = fields.Boolean(
        string="Can Be Terminal",
        help="When enabled, this stage is allowed to be the final FG-producing stage.",
    )
    bom_mapping_policy = fields.Selection(
        [
            ("own_operation", "Own Operation"),
            ("merge_to_material_parent", "Merge Into Material Stage"),
            ("logical_only", "Logical Only"),
        ],
        string="BoM Mapping Policy",
        default="own_operation",
        help=(
            "Guides how this stage maps into BoM/workorder operations in the generic engine."
        ),
    )
    quantity_profile_id = fields.Many2one(
        "corr.engine.quantity.profile",
        string="Quantity Profile",
        help="Quantity profile used by the generic engine for this stage.",
    )
    advanced_overrides = fields.Boolean(
        string="Advanced Overrides",
        help=(
            "Enabled when this template keeps technical/default values that differ "
            "from the selected Stage Master. Runtime fields remain unchanged."
        ),
    )
    consider_slitting_options = fields.Boolean(
        string="Consider Slitting Options",
        help=(
            "Enable when this stage can generate/show slitting/layout options. "
            "Disable when it only consumes the upstream selected sheet/layout."
        ),
    )
    drives_layout_selection = fields.Boolean(
        string="Drives Layout Selection",
        help=(
            "Enable when this stage should be treated as the primary user-facing "
            "layout/slitting decision point for routed preview flows."
        ),
    )
    consider_slitting_options_configured = fields.Boolean(
        string="Slitting Option Eligibility Configured",
        default=False,
        readonly=True,
        help=(
            "Technical flag used to distinguish explicit slitting-option configuration "
            "from compatibility fallback defaults."
        ),
    )
    input_rule_ids = fields.One2many(
        "corr.engine.stage.input.rule",
        "stage_template_id",
        string="Input Roles",
        help="Allowed input roles for this stage in the generic engine.",
    )

    stage_type = fields.Selection(
        [
            ("corrugation", "Corrugation"),
            ("printing", "Printing"),
            ("lamination", "Lamination"),
            ("diecut", "Die-cut"),
            ("conversion", "Conversion / Seaming"),
            ("fg", "Finished Goods"),
            ("auxiliary", "Auxiliary"),
        ],
        string="Stage Type",
        default="auxiliary",
        required=True,
        help="High-level classification of this logical stage.",
    )
    default_quantity_basis = fields.Selection(
        [
            ("raw_material", "Raw Material"),
            ("board", "Board"),
            ("sheet", "Sheet"),
            ("blanks", "Blanks"),
            ("finished_goods", "Finished Goods"),
        ],
        string="Default Quantity Basis",
        help=(
            "Default quantity node used by the Engine quantity graph for this stage. "
            "This does not change the existing quantity engine; it is configuration "
            "for the future dynamic architecture."
        ),
    )
    default_cost_basis = fields.Selection(
        [
            ("per_unit", "Per Unit"),
            ("per_hour", "Per Hour"),
            ("per_area", "Per Area"),
            ("per_weight", "Per Weight"),
        ],
        string="Default Cost Basis",
        default="per_unit",
        help=(
            "Default costing basis for this stage in the dynamic architecture. "
            "Current Engine V2 costing continues to use per-unit configuration only."
        ),
    )

    is_entry_stage = fields.Boolean(
        string="Entry Stage",
        help="Optional marker for the first logical stage in a routing template.",
    )
    is_fg_stage = fields.Boolean(
        string="Finished Goods Stage",
        help="Optional marker for the logical stage that produces finished goods.",
    )
    is_optional = fields.Boolean(
        string="Optional Stage",
        help="When enabled, this stage may be skipped in some routings for a plant.",
    )
    merge_group_key = fields.Char(
        string="Merge Group Key",
        help=(
            "Optional logical merge key used to indicate that multiple stages may be "
            "merged into a single physical operation for some plants."
        ),
    )

    io_template_ids = fields.One2many(
        "corr.engine.stage.io.template",
        "stage_template_id",
        string="I/O Definitions",
    )

    is_system_default = fields.Boolean(
        string="System Default",
        default=False,
        readonly=True,
        help=(
            "Indicates that this record was created and is maintained by the Phase 8 "
            "seeding tools as part of the standard compatibility profile."
        ),
    )
    seed_key = fields.Char(
        string="Seed Key",
        readonly=True,
        index=True,
        help=(
            "Stable identifier used by the Phase 8 seeding tools to match and update "
            "this record in an idempotent way."
        ),
    )
    seed_version = fields.Char(
        string="Seed Version",
        readonly=True,
        help="Version label of the compatibility profile that last updated this record.",
    )

    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "corr_engine_stage_template_code_unique",
            "unique(code)",
            "Each Engine Stage Template code must be unique.",
        ),
        (
            "corr_engine_stage_template_seed_key_unique",
            "unique(seed_key)",
            "Each Engine Stage Template seed key must be unique.",
        )
    ]

    @staticmethod
    def _default_consider_slitting_options(stage_type, code):
        # Backward-compatibility fallback only.
        # Long-term eligibility ownership should come from explicit
        # `consider_slitting_options` configuration on the stage template.
        stage_type = str(stage_type or "").strip().lower()
        code = str(code or "").strip().lower()
        if code in ("2ply", "board", "cutting", "slitter", "slitting", "ts_cut"):
            return True
        if stage_type == "corrugation":
            return True
        return False

    @staticmethod
    def _normalize_stage_code(value):
        value = str(value or "").strip().lower()
        value = re.sub(r"[^a-z0-9]+", "_", value)
        value = re.sub(r"_+", "_", value).strip("_")
        return value

    @api.model
    def _technical_stage_type_from_stage_type(self, stage_type):
        code = str(stage_type.code or "").strip().lower() if stage_type else ""
        category = str(stage_type.category or "").strip().lower() if stage_type else ""
        if code in {
            "corr_2ply",
            "corr_3ply",
            "corr_4ply",
            "corr_board",
            "top_sheet_cut",
        }:
            return "corrugation"
        if code in {"sheet_print", "top_sheet_print"}:
            return "printing"
        if code in {"top_sheet_film"}:
            return "lamination"
        if code in {"sheet_diecut", "corr_die"}:
            return "diecut"
        if code in {"peel_off", "corr_peel", "seaming", "stitching", "pasting", "packing"}:
            return "conversion"
        if code == "fg" or category == "fg":
            return "fg"
        return "auxiliary"

    @api.model
    def _sync_stage_type_master_vals(self, vals, template=False):
        vals = dict(vals)
        advanced_overrides = vals.get(
            "advanced_overrides",
            bool(getattr(template, "advanced_overrides", False)) if template else False,
        )
        if advanced_overrides:
            return vals

        stage_type_id = vals.get("stage_type_id") or getattr(
            getattr(template, "stage_type_id", False), "id", False
        )
        if not stage_type_id:
            return vals
        stage_type = self.env["corr.stage.type"].sudo().browse(stage_type_id).exists()
        if not stage_type:
            return vals
        vals["stage_type"] = stage_type.default_stage_type or self._technical_stage_type_from_stage_type(stage_type)
        capability = getattr(stage_type, "capability_id", False)
        if capability:
            vals["capability_id"] = capability.id
        if stage_type.default_execution_type:
            vals["execution_type"] = stage_type.default_execution_type
        if stage_type.default_output_stock_behavior:
            vals["output_stock_behavior"] = stage_type.default_output_stock_behavior
        if stage_type.default_output_kind:
            vals["output_kind"] = stage_type.default_output_kind
        if stage_type.default_bom_mapping_policy:
            vals["bom_mapping_policy"] = stage_type.default_bom_mapping_policy
        if stage_type.default_quantity_profile_id:
            vals["quantity_profile_id"] = stage_type.default_quantity_profile_id.id
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("code") and vals.get("name"):
                vals["code"] = self._normalize_stage_code(vals.get("name"))
            elif vals.get("code"):
                vals["code"] = self._normalize_stage_code(vals.get("code"))
        vals_list = [self._sync_stage_type_master_vals(vals) for vals in vals_list]
        for vals in vals_list:
            if "consider_slitting_options" in vals and "consider_slitting_options_configured" not in vals:
                vals["consider_slitting_options_configured"] = True
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get("corr_skip_stage_template_sync"):
            return super().write(vals)
        if vals.get("code"):
            vals = dict(vals)
            vals["code"] = self._normalize_stage_code(vals.get("code"))
        if "consider_slitting_options" in vals and "consider_slitting_options_configured" not in vals:
            vals = dict(vals)
            vals["consider_slitting_options_configured"] = True
        for tmpl in self:
            tmpl.with_context(corr_skip_stage_template_sync=True).write(
                self._sync_stage_type_master_vals(vals, template=tmpl)
            )
        return True

    @api.onchange("name")
    def _onchange_name_generate_code(self):
        for rec in self:
            if rec.code or not rec.name:
                continue
            rec.code = rec._normalize_stage_code(rec.name)

    @api.onchange("code")
    def _onchange_code_normalize(self):
        for rec in self:
            if rec.code:
                rec.code = rec._normalize_stage_code(rec.code)

    @api.onchange("stage_type_id")
    def _onchange_stage_type_id(self):
        for rec in self:
            stage_type = rec.stage_type_id
            if not stage_type:
                continue
            rec.stage_type = stage_type.default_stage_type or rec._technical_stage_type_from_stage_type(stage_type)
            capability = stage_type.sudo().capability_id
            if capability:
                rec.capability_id = capability.id
            if stage_type.default_execution_type:
                rec.execution_type = stage_type.default_execution_type
            if stage_type.default_output_stock_behavior:
                rec.output_stock_behavior = stage_type.default_output_stock_behavior
            if stage_type.default_output_kind:
                rec.output_kind = stage_type.default_output_kind
            if stage_type.default_bom_mapping_policy:
                rec.bom_mapping_policy = stage_type.default_bom_mapping_policy
            if stage_type.default_quantity_profile_id:
                rec.quantity_profile_id = stage_type.default_quantity_profile_id

    def action_seed_contract_defaults(self):
        """Apply recommended generic stage contract defaults on selected templates.

        This mirrors the non-breaking defaults used during post-init backfill and
        only fills fields that are currently unset. It does not alter any existing
        Engine V2 runtime behaviour and is intended as an admin helper.
        """
        StageCapability = self.env["corr.engine.stage.capability"].sudo()
        QuantityProfile = self.env["corr.engine.quantity.profile"].sudo()

        capability_by_code = {c.code: c for c in StageCapability.search([])}
        quantity_profile_by_code = {q.code: q for q in QuantityProfile.search([])}

        for tmpl in self:
            vals = {}

            stage_type = tmpl.stage_type or "auxiliary"

            # Execution type: material for corrugation, conversion, fg; operation otherwise.
            if not tmpl.execution_type:
                if stage_type in ("corrugation", "conversion", "fg"):
                    vals["execution_type"] = "material"
                else:
                    vals["execution_type"] = "operation"

            # Capability: map from stage_type when not set.
            if not tmpl.capability_id:
                cap = capability_by_code.get(stage_type)
                if cap:
                    vals["capability_id"] = cap.id

            # Output kind and stock behaviour: best-effort based on stage_type flags.
            if not tmpl.output_kind:
                if stage_type == "fg":
                    vals["output_kind"] = "fg"
                elif stage_type in ("conversion",):
                    vals["output_kind"] = "box"
                elif stage_type == "corrugation":
                    vals["output_kind"] = "board"
                elif stage_type in ("printing", "lamination", "diecut"):
                    vals["output_kind"] = "sheet"
                else:
                    vals["output_kind"] = "generic"

            if not tmpl.output_stock_behavior:
                if stage_type == "fg" or tmpl.is_fg_stage:
                    vals["output_stock_behavior"] = "fg"
                elif stage_type in ("corrugation", "conversion"):
                    vals["output_stock_behavior"] = "semi_finished"
                else:
                    vals["output_stock_behavior"] = "non_stock"

            # Terminal flag: honour existing is_fg_stage when present.
            if tmpl.can_be_terminal in (False, None):
                vals.setdefault(
                    "can_be_terminal",
                    bool(tmpl.is_fg_stage or stage_type == "fg"),
                )

            # BoM mapping policy: default own_operation.
            if not tmpl.bom_mapping_policy:
                vals["bom_mapping_policy"] = "own_operation"

            # Quantity profile: map from stage_type where sensible.
            if not tmpl.quantity_profile_id:
                qp_code = None
                if stage_type == "corrugation":
                    qp_code = "sheet_based_board"
                elif stage_type in ("printing", "lamination"):
                    qp_code = "area_based_printing"
                elif stage_type in ("conversion", "fg", "diecut"):
                    qp_code = "per_fg_unit"
                else:
                    qp_code = "per_fg_unit"
                qp = quantity_profile_by_code.get(qp_code)
                if qp:
                    vals["quantity_profile_id"] = qp.id

            if not tmpl.consider_slitting_options_configured:
                vals["consider_slitting_options"] = self._default_consider_slitting_options(
                    stage_type,
                    tmpl.code,
                )
                vals["consider_slitting_options_configured"] = True

            # Previous output flags: conservative defaults for now. Treat non-entry,
            # non-corrugation stages as consuming previous output.
            if not tmpl.consumes_previous_output and stage_type not in ("corrugation",):
                if not tmpl.is_entry_stage:
                    vals.setdefault("consumes_previous_output", True)
            if (
                not tmpl.previous_output_required
                and (vals.get("consumes_previous_output") or tmpl.consumes_previous_output)
            ):
                vals.setdefault("previous_output_required", True)

            if vals:
                tmpl.write(vals)


class CorrEngineStageIOTemplate(models.Model):
    _name = "corr.engine.stage.io.template"
    _description = "Engine Stage I/O Template"
    _order = "stage_template_id, id"

    name = fields.Char(
        help="Optional label for this I/O mapping; can be left empty.",
    )
    stage_template_id = fields.Many2one(
        "corr.engine.stage.template",
        string="Stage Template",
        required=True,
        ondelete="cascade",
    )
    input_quantity_node_key = fields.Selection(
        [
            ("raw_material", "Raw Material"),
            ("board", "Board"),
            ("sheet", "Sheet"),
            ("blanks", "Blanks"),
            ("finished_goods", "Finished Goods"),
        ],
        string="Input Quantity Node",
        help=(
            "Quantity node consumed by this stage in the Engine quantity graph. "
            "This is configuration only; it does not alter the current StageGraph behaviour."
        ),
    )
    output_quantity_node_key = fields.Selection(
        [
            ("raw_material", "Raw Material"),
            ("board", "Board"),
            ("sheet", "Sheet"),
            ("blanks", "Blanks"),
            ("finished_goods", "Finished Goods"),
        ],
        string="Output Quantity Node",
        help=(
            "Quantity node produced by this stage in the Engine quantity graph. "
            "This is configuration only; it does not alter the current StageGraph behaviour."
        ),
    )
    yield_factor = fields.Float(
        string="Yield Factor",
        default=1.0,
        help=(
            "Optional yield factor from input to output for this stage in the dynamic "
            "architecture. The current quantity engine is not affected by this value yet."
        ),
    )
    loss_type = fields.Selection(
        [
            ("none", "None"),
            ("trim", "Trim"),
            ("scrap", "Scrap"),
            ("waste", "Waste"),
        ],
        string="Loss Type",
        default="none",
        help="Optional classification of losses at this stage.",
    )
    active = fields.Boolean(default=True)

    is_system_default = fields.Boolean(
        string="System Default",
        default=False,
        readonly=True,
        help=(
            "Indicates that this record was created and is maintained by the Phase 8 "
            "seeding tools as part of the standard compatibility profile."
        ),
    )
    seed_key = fields.Char(
        string="Seed Key",
        readonly=True,
        index=True,
        help=(
            "Stable identifier used by the Phase 8 seeding tools to match and update "
            "this record in an idempotent way."
        ),
    )
    seed_version = fields.Char(
        string="Seed Version",
        readonly=True,
        help="Version label of the compatibility profile that last updated this record.",
    )

    _sql_constraints = [
        (
            "corr_engine_stage_io_template_seed_key_unique",
            "unique(seed_key)",
            "Each Engine Stage I/O Template seed key must be unique.",
        )
    ]


_logger = logging.getLogger(__name__)


class CorrEngineStageTemplate(models.Model):
    _inherit = "corr.engine.stage.template"

    def action_seed_contract_defaults(self):
        """Apply generic stage contract defaults on selected templates.

        Only fills missing configuration; it does not overwrite non-empty values.
        This helper is intended for admin use and does not change Engine V2
        runtime behaviour directly.
        """
        StageCapability = self.env["corr.engine.stage.capability"].sudo()
        QuantityProfile = self.env["corr.engine.quantity.profile"].sudo()

        capability_by_code = {c.code: c for c in StageCapability.search([])}
        quantity_profile_by_code = {q.code: q for q in QuantityProfile.search([])}

        for tmpl in self:
            vals = {}

            stage_type = tmpl.stage_type or "auxiliary"

            # Execution type: material for corrugation, conversion, fg; operation otherwise.
            if not tmpl.execution_type:
                if stage_type in ("corrugation", "conversion", "fg"):
                    vals["execution_type"] = "material"
                else:
                    vals["execution_type"] = "operation"

            # Capability: map from stage_type when not set.
            if not tmpl.capability_id:
                cap = capability_by_code.get(stage_type)
                if cap:
                    vals["capability_id"] = cap.id

            # Output kind and stock behaviour: best-effort based on stage_type flags.
            if not tmpl.output_kind:
                if stage_type == "fg":
                    vals["output_kind"] = "fg"
                elif stage_type in ("conversion",):
                    vals["output_kind"] = "box"
                elif stage_type == "corrugation":
                    vals["output_kind"] = "board"
                elif stage_type in ("printing", "lamination", "diecut"):
                    vals["output_kind"] = "sheet"
                else:
                    vals["output_kind"] = "generic"

            if not tmpl.output_stock_behavior:
                if stage_type == "fg" or tmpl.is_fg_stage:
                    vals["output_stock_behavior"] = "fg"
                elif stage_type in ("corrugation", "conversion"):
                    vals["output_stock_behavior"] = "semi_finished"
                else:
                    vals["output_stock_behavior"] = "non_stock"

            # BoM mapping policy: default own_operation.
            if not tmpl.bom_mapping_policy:
                vals["bom_mapping_policy"] = "own_operation"

            # Quantity profile: map from stage_type where sensible.
            if not tmpl.quantity_profile_id:
                qp_code = None
                if stage_type == "corrugation":
                    qp_code = "sheet_based_board"
                elif stage_type in ("printing", "lamination"):
                    qp_code = "area_based_printing"
                elif stage_type in ("conversion", "fg", "diecut"):
                    qp_code = "per_fg_unit"
                else:
                    qp_code = "per_fg_unit"
                qp = quantity_profile_by_code.get(qp_code)
                if qp:
                    vals["quantity_profile_id"] = qp.id

            # Previous output and terminal flags are left unchanged here; the
            # contract validator highlights inconsistent values instead of
            # overwriting them.

            if vals:
                tmpl.write(vals)

    def action_validate_contract(self):
        """Validate stage contracts for templates used in active routing.

        Checks for missing or inconsistent configuration and raises a summary
        UserError when issues are detected. Intended for admin use.
        """

        RoutingStage = self.env["corr.engine.routing.profile.stage"].sudo()
        StageTemplate = self.env["corr.engine.stage.template"].sudo()

        lines = RoutingStage.search(
            [
                ("routing_profile_id.active", "=", True),
                ("stage_template_id", "!=", False),
            ]
        )
        tmpl_ids = {l.stage_template_id.id for l in lines if l.stage_template_id}
        if not tmpl_ids:
            raise UserError(
                _("No Stage Templates are used in active routing profiles.")
            )

        templates = StageTemplate.browse(list(tmpl_ids))

        # Build route context per profile for positional checks.
        profiles_by_id = {}
        for line in lines:
            rp = line.routing_profile_id
            if not rp or not rp.active:
                continue
            profiles_by_id.setdefault(rp.id, []).append(line)

        for rp_id, rlines in profiles_by_id.items():
            rlines.sort(key=lambda l: (l.sequence, l.id))
            profiles_by_id[rp_id] = rlines

        messages: List[str] = []

        def _add(level: str, msg: str):
            text = f"[{level}] {msg}"
            messages.append(text)
            if level == "ERROR":
                _logger.error("%s", text)
            else:
                _logger.warning("%s", text)

        # Field-level checks.
        for tmpl in templates:
            prefix = f"StageTemplate '{tmpl.display_name}'"

            if not tmpl.execution_type:
                _add("ERROR", f"{prefix} is missing execution_type.")
            else:
                if tmpl.stage_type in ("corrugation", "conversion", "fg") and tmpl.execution_type != "material":
                    _add(
                        "WARNING",
                        f"{prefix} has stage_type='{tmpl.stage_type}' but execution_type='{tmpl.execution_type}' (expected 'material').",
                    )
                if tmpl.stage_type in ("printing", "lamination", "diecut", "auxiliary") and tmpl.execution_type != "operation":
                    _add(
                        "WARNING",
                        f"{prefix} has stage_type='{tmpl.stage_type}' but execution_type='{tmpl.execution_type}' (expected 'operation').",
                    )

            if not tmpl.capability_id:
                _add("ERROR", f"{prefix} is missing capability_id.")
            if not tmpl.output_kind:
                _add("ERROR", f"{prefix} is missing output_kind.")
            if not tmpl.output_stock_behavior:
                _add("ERROR", f"{prefix} is missing output_stock_behavior.")
            if not tmpl.bom_mapping_policy:
                _add("ERROR", f"{prefix} is missing bom_mapping_policy.")
            if not tmpl.quantity_profile_id:
                _add("ERROR", f"{prefix} is missing quantity_profile_id.")

            # FG semantics.
            if tmpl.is_fg_stage or tmpl.stage_type == "fg":
                if tmpl.output_stock_behavior != "fg":
                    _add(
                        "WARNING",
                        f"{prefix} is FG-like but output_stock_behavior='{tmpl.output_stock_behavior}' (expected 'fg').",
                    )
                if not tmpl.can_be_terminal:
                    _add(
                        "WARNING",
                        f"{prefix} is FG-like but can_be_terminal is False.",
                    )

            # Consumes previous output sanity.
            if tmpl.previous_output_required and not tmpl.consumes_previous_output:
                _add(
                    "ERROR",
                    f"{prefix} has previous_output_required=True but consumes_previous_output=False.",
                )

        # Route-position checks.
        for rp_id, rlines in profiles_by_id.items():
            routing = rlines[0].routing_profile_id
            if not routing:
                continue
            name = routing.display_name

            # First stage checks.
            first = rlines[0]
            t_first = first.stage_template_id
            if t_first and t_first.consumes_previous_output and t_first.previous_output_required:
                _add(
                    "WARNING",
                    f"Routing Profile '{name}' first stage '{t_first.display_name}' requires previous output but has no preceding stage.",
                )

            # Terminal stage checks.
            last = rlines[-1]
            t_last = last.stage_template_id
            if t_last and not t_last.can_be_terminal:
                _add(
                    "WARNING",
                    f"Routing Profile '{name}' terminal stage '{t_last.display_name}' has can_be_terminal=False.",
                )

            # Operation without preceding material.
            seen_material = False
            for line in rlines:
                tmpl = line.stage_template_id
                if not tmpl:
                    continue
                is_material = tmpl.execution_type == "material"
                if is_material:
                    seen_material = True
                    continue
                # Operation stage
                if not seen_material:
                    _add(
                        "WARNING",
                        f"Routing Profile '{name}' stage '{tmpl.display_name}' is an operation with no preceding material stage.",
                    )

        if not messages:
            raise UserError(
                _("Stage contract validation: no issues found for templates used in active routing profiles.")
            )

        # Surface a concise summary to the admin; full details are in the logs.
        head = "\n".join(messages[:30])
        if len(messages) > 30:
            head += _("\n... (%s more messages, see logs)") % (len(messages) - 30)
        raise UserError(
            _("Stage contract validation found issues:\n%s") % head
        )



