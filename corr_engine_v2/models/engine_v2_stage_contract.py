from odoo import api, fields, models


class CorrEngineStageCapability(models.Model):
    _name = "corr.engine.stage.capability"
    _description = "Engine Stage Capability"
    _order = "name, id"

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    execution_type = fields.Selection(
        [("material", "Material"), ("operation", "Operation")],
        string="Default Execution Type",
        default="material",
        required=True,
        help="Default execution type for stages using this capability.",
    )
    default_output_kind = fields.Selection(
        [
            ("fg", "Finished Goods"),
            ("box", "Box"),
            ("board", "Board"),
            ("sheet", "Sheet"),
            ("roll", "Roll"),
            ("generic", "Generic Intermediate"),
        ],
        string="Default Output Kind",
        help="Optional default output kind for stages using this capability.",
    )
    default_output_stock_behavior = fields.Selection(
        [
            ("fg", "Finished Goods"),
            ("semi_finished", "Semi-finished"),
            ("non_stock", "Non-stock Logical"),
        ],
        string="Default Stock Behaviour",
        default="non_stock",
        help="Optional default stock behaviour for outputs of this capability.",
    )
    default_bom_mapping_policy = fields.Selection(
        [
            ("own_operation", "Own Operation"),
            ("merge_to_material_parent", "Merge Into Material Stage"),
            ("logical_only", "Logical Only"),
        ],
        string="Default BoM Mapping Policy",
        default="own_operation",
        help="Default policy for mapping stages of this capability to BoM operations.",
    )
    default_quantity_profile_id = fields.Many2one(
        "corr.engine.quantity.profile",
        string="Default Quantity Profile",
        help="Optional default quantity profile for stages using this capability.",
    )

    active = fields.Boolean(default=True)

    is_system_default = fields.Boolean(
        string="System Default",
        default=False,
        readonly=True,
        help=(
            "Indicates that this capability was created by seeding tools as part of "
            "the standard compatibility profile."
        ),
    )
    seed_key = fields.Char(
        string="Seed Key",
        readonly=True,
        index=True,
        help=(
            "Stable identifier used by seeding tools to match and update this record "
            "in an idempotent way."
        ),
    )
    seed_version = fields.Char(
        string="Seed Version",
        readonly=True,
        help="Version label of the compatibility profile that last updated this record.",
    )

    _sql_constraints = [
        (
            "corr_engine_stage_capability_code_unique",
            "unique(code)",
            "Each Engine Stage Capability code must be unique.",
        ),
        (
            "corr_engine_stage_capability_seed_key_unique",
            "unique(seed_key)",
            "Each Engine Stage Capability seed key must be unique.",
        ),
    ]

    def action_seed_default_capabilities(self):
        """Seed or update a minimal set of standard stage capabilities.

        This helper is safe to run multiple times. It only creates missing
        capabilities or fills empty fields on existing ones and does not change
        any Engine V2 runtime behaviour.
        """
        capability_defs = [
            # code,        name,         execution_type, default_output_kind
            ("corrugation", "corrugation", "material", "board"),
            ("printing", "printing", "operation", "sheet"),
            ("lamination", "lamination", "operation", "sheet"),
            ("diecut", "diecut", "operation", "sheet"),
            ("conversion", "conversion / seaming", "material", "box"),
            ("fg", "finished goods", "material", "fg"),
            ("auxiliary", "auxiliary", "operation", "generic"),
        ]

        Capability = self.env["corr.engine.stage.capability"].sudo()
        existing_by_code = {c.code: c for c in Capability.search([])}

        for code, name, exec_type, output_kind in capability_defs:
            cap = existing_by_code.get(code)
            if cap:
                updates = {}
                if not cap.execution_type:
                    updates["execution_type"] = exec_type
                if not cap.default_output_kind:
                    updates["default_output_kind"] = output_kind
                if updates:
                    cap.write(updates)
                continue

            Capability.create(
                {
                    "name": name,
                    "code": code,
                    "execution_type": exec_type,
                    "default_output_kind": output_kind,
                    "default_output_stock_behavior": "non_stock",
                    "default_bom_mapping_policy": "own_operation",
                    "is_system_default": True,
                    "seed_key": f"stage_capability_{code}",
                    "seed_version": "v1",
                }
            )


class CorrEngineInputRole(models.Model):
    _name = "corr.engine.input.role"
    _description = "Engine Input Role"
    _order = "category, code, id"

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    category = fields.Selection(
        [
            ("raw", "Raw Material"),
            ("consumable", "Consumable"),
            ("intermediate", "Intermediate Output"),
            ("generic_component", "Generic Component"),
        ],
        string="Category",
        required=True,
    )
    shape = fields.Selection(
        [
            ("sheet", "Sheet"),
            ("roll", "Roll"),
            ("liquid", "Liquid"),
            ("piece", "Piece"),
            ("area", "Area"),
            ("length", "Length"),
        ],
        string="Shape",
        help="Optional physical shape / quantity basis for this role.",
    )
    description = fields.Char()
    active = fields.Boolean(default=True)

    is_system_default = fields.Boolean(
        string="System Default",
        default=False,
        readonly=True,
        help=(
            "Indicates that this input role was created by seeding tools as part of "
            "the standard compatibility profile."
        ),
    )
    seed_key = fields.Char(
        string="Seed Key",
        readonly=True,
        index=True,
        help=(
            "Stable identifier used by seeding tools to match and update this record "
            "in an idempotent way."
        ),
    )
    seed_version = fields.Char(
        string="Seed Version",
        readonly=True,
        help="Version label of the compatibility profile that last updated this record.",
    )

    _sql_constraints = [
        (
            "corr_engine_input_role_code_unique",
            "unique(code)",
            "Each Engine Input Role code must be unique.",
        ),
        (
            "corr_engine_input_role_seed_key_unique",
            "unique(seed_key)",
            "Each Engine Input Role seed key must be unique.",
        ),
    ]

    def action_seed_default_input_roles(self):
        """Seed or update a minimal set of standard input roles.

        Safe to run multiple times; only creates missing roles or fills empty
        fields on existing ones. Does not affect current Engine V2 runtime
        behaviour and is intended as an admin/helper tool.
        """
        role_defs = [
            # code,                          name,                       category,           shape
            ("RM_PAPER_LINER",               "Paper Liner",              "raw",              "sheet"),
            ("RM_PAPER_MEDIUM",              "Paper Medium",             "raw",              "sheet"),
            ("RM_PAPER_TOP",                 "Top Paper",                "raw",              "sheet"),
            ("RM_PAPER_GENERIC",             "Generic Paper",            "raw",              "sheet"),
            ("RM_COMPONENT_GENERIC",         "Generic Component",        "raw",              "piece"),
            ("CONSUMABLE_ADHESIVE_CORRUGATION", "Adhesive (Corrugation)", "consumable",      "liquid"),
            ("CONSUMABLE_ADHESIVE_BOARD",    "Adhesive (Board)",         "consumable",       "liquid"),
            ("CONSUMABLE_ADHESIVE_FG",       "Adhesive (FG)",            "consumable",       "liquid"),
            ("CONSUMABLE_INK",               "Ink",                      "consumable",       "liquid"),
            ("CONSUMABLE_VARNISH",           "Varnish / Coating",        "consumable",       "liquid"),
            ("INT_SHEET",                    "Intermediate Sheet",       "intermediate",     "sheet"),
            ("INT_BOARD",                    "Intermediate Board",       "intermediate",     "sheet"),
            ("INT_2PLY_BOARD",               "2-ply Board",              "intermediate",     "sheet"),
            ("INT_BOX_SEAMED",               "Seamed Box",               "intermediate",     "piece"),
            ("COMP_GENERIC",                 "Generic Component",        "generic_component","piece"),
        ]

        InputRole = self.env["corr.engine.input.role"].sudo()
        existing_by_code = {r.code: r for r in InputRole.search([])}

        for code, name, category, shape in role_defs:
            role = existing_by_code.get(code)
            if role:
                updates = {}
                if not role.category:
                    updates["category"] = category
                if not role.shape:
                    updates["shape"] = shape
                if not role.name:
                    updates["name"] = name
                if updates:
                    role.write(updates)
                continue

            InputRole.create(
                {
                    "name": name,
                    "code": code,
                    "category": category,
                    "shape": shape,
                    "is_system_default": True,
                    "seed_key": f"input_role_{code}",
                    "seed_version": "v1",
                }
            )


class CorrEngineQuantityProfile(models.Model):
    _name = "corr.engine.quantity.profile"
    _description = "Engine Quantity Profile"
    _order = "code, id"

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    description = fields.Char()
    active = fields.Boolean(default=True)

    is_system_default = fields.Boolean(
        string="System Default",
        default=False,
        readonly=True,
        help=(
            "Indicates that this quantity profile was created by seeding tools as part "
            "of the standard compatibility profile."
        ),
    )
    seed_key = fields.Char(
        string="Seed Key",
        readonly=True,
        index=True,
        help=(
            "Stable identifier used by seeding tools to match and update this record "
            "in an idempotent way."
        ),
    )
    seed_version = fields.Char(
        string="Seed Version",
        readonly=True,
        help="Version label of the compatibility profile that last updated this record.",
    )

    _sql_constraints = [
        (
            "corr_engine_quantity_profile_code_unique",
            "unique(code)",
            "Each Engine Quantity Profile code must be unique.",
        ),
        (
            "corr_engine_quantity_profile_seed_key_unique",
            "unique(seed_key)",
            "Each Engine Quantity Profile seed key must be unique.",
        ),
    ]

    def name_get(self):
        result = []
        for rec in self:
            label = rec.name or rec.code
            if rec.code and rec.name:
                label = "%s [%s]" % (rec.name, rec.code)
            result.append((rec.id, label))
        return result

    @api.model
    def name_search(self, name="", args=None, operator="ilike", limit=100):
        args = list(args or [])
        if name:
            records = self.search(
                [
                    "|",
                    ("code", operator, name),
                    ("name", operator, name),
                ]
                + args,
                limit=limit,
            )
            if records:
                return [(record.id, record.display_name) for record in records.sudo()]
        return super().name_search(name=name, args=args, operator=operator, limit=limit)

    def action_seed_default_quantity_profiles(self):
        profile_defs = [
            ("per_fg_unit", "Per Finished Unit"),
            ("sheet_based_board", "Board from Sheets"),
            ("roll_to_sheet_cutting", "Cutting Roll to Sheets"),
            ("area_based_printing", "Printing by Area"),
        ]

        Profile = self.env["corr.engine.quantity.profile"].sudo()
        existing_by_code = {profile.code: profile for profile in Profile.search([])}

        for code, name in profile_defs:
            profile = existing_by_code.get(code)
            if profile:
                updates = {}
                if not profile.name:
                    updates["name"] = name
                if updates:
                    profile.write(updates)
                continue
            Profile.create(
                {
                    "name": name,
                    "code": code,
                    "is_system_default": True,
                    "seed_key": f"quantity_profile_{code}",
                    "seed_version": "v1",
                }
            )


class CorrEngineStageInputRule(models.Model):
    _name = "corr.engine.stage.input.rule"
    _description = "Engine Stage Input Rule"
    _order = "stage_template_id, sequence, id"

    stage_template_id = fields.Many2one(
        "corr.engine.stage.template",
        string="Stage Template",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    role_id = fields.Many2one(
        "corr.engine.input.role",
        string="Role",
        required=True,
        ondelete="restrict",
    )
    requirement = fields.Selection(
        [
            ("required", "Required"),
            ("optional", "Optional"),
            ("forbidden", "Forbidden"),
        ],
        string="Requirement",
        default="optional",
        required=True,
    )
    source = fields.Selection(
        [
            ("any", "Any"),
            ("rm", "Raw Material"),
            ("consumable", "Consumable"),
            ("previous_output", "Previous Output"),
            ("intermediate_output", "Intermediate Output"),
        ],
        string="Source",
        default="any",
        required=True,
        help="Expected source of this role for this stage.",
    )
    priority = fields.Integer(
        string="Priority",
        default=10,
        help="Higher priority rules are applied first when attaching inputs.",
    )
    note = fields.Char(string="Note")



