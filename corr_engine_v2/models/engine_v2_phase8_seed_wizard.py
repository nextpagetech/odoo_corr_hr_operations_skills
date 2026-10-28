from odoo import api, fields, models, _


PHASE8_SEED_VERSION = "8.0.0"


DEFAULT_STAGE_TEMPLATES = [
    {
        "seed_key": "v2_compat.stage.2ply",
        "code": "2ply",
        "name": "2-Ply Corrugation",
        "stage_type": "corrugation",
        "default_quantity_basis": "board",
        "default_cost_basis": "per_unit",
        "consider_slitting_options": True,
        "is_entry_stage": True,
        "is_fg_stage": False,
        "is_optional": False,
        "sequence": 10,
    },
    {
        "seed_key": "v2_compat.stage.board",
        "code": "board",
        "name": "Board Formation",
        "stage_type": "corrugation",
        "default_quantity_basis": "board",
        "default_cost_basis": "per_unit",
        "consider_slitting_options": True,
        "is_entry_stage": False,
        "is_fg_stage": False,
        "is_optional": False,
        "sequence": 20,
    },
    {
        "seed_key": "v2_compat.stage.ts_print",
        "code": "ts_print",
        "name": "Top Sheet Printing",
        "stage_type": "printing",
        "default_quantity_basis": "sheet",
        "default_cost_basis": "per_unit",
        "consider_slitting_options": False,
        "is_entry_stage": False,
        "is_fg_stage": False,
        "is_optional": True,
        "sequence": 30,
    },
    {
        "seed_key": "v2_compat.stage.lamination",
        "code": "lamination",
        "name": "Lamination",
        "stage_type": "lamination",
        "default_quantity_basis": "sheet",
        "default_cost_basis": "per_unit",
        "consider_slitting_options": False,
        "is_entry_stage": False,
        "is_fg_stage": False,
        "is_optional": True,
        "sequence": 40,
    },
    {
        "seed_key": "v2_compat.stage.diecut",
        "code": "diecut",
        "name": "Die-cut",
        "stage_type": "diecut",
        "default_quantity_basis": "blanks",
        "default_cost_basis": "per_unit",
        "consider_slitting_options": False,
        "is_entry_stage": False,
        "is_fg_stage": False,
        "is_optional": False,
        "sequence": 50,
    },
    {
        "seed_key": "v2_compat.stage.peeloff",
        "code": "peeloff",
        "name": "Peel-off / Waste Stripping",
        "stage_type": "diecut",
        "default_quantity_basis": "blanks",
        "default_cost_basis": "per_unit",
        "consider_slitting_options": False,
        "is_entry_stage": False,
        "is_fg_stage": False,
        "is_optional": True,
        "sequence": 55,
    },
    {
        "seed_key": "v2_compat.stage.seaming",
        "code": "seaming",
        "name": "Seaming / Jointing",
        "stage_type": "conversion",
        "default_quantity_basis": "finished_goods",
        "default_cost_basis": "per_unit",
        "consider_slitting_options": False,
        "is_entry_stage": False,
        "is_fg_stage": False,
        "is_optional": False,
        "sequence": 60,
    },
    {
        "seed_key": "v2_compat.stage.fg",
        "code": "fg",
        "name": "Finished Goods",
        "stage_type": "fg",
        "default_quantity_basis": "finished_goods",
        "default_cost_basis": "per_unit",
        "consider_slitting_options": False,
        "is_entry_stage": False,
        "is_fg_stage": True,
        "is_optional": False,
        "sequence": 70,
    },
]


DEFAULT_STAGE_IO_TEMPLATES = [
    {
        "seed_key": "v2_compat.stage_io.2ply",
        "stage_seed_key": "v2_compat.stage.2ply",
        "input_quantity_node_key": "raw_material",
        "output_quantity_node_key": "board",
        "yield_factor": 1.0,
        "loss_type": "none",
    },
    {
        "seed_key": "v2_compat.stage_io.board",
        "stage_seed_key": "v2_compat.stage.board",
        "input_quantity_node_key": "board",
        "output_quantity_node_key": "board",
        "yield_factor": 1.0,
        "loss_type": "none",
    },
    {
        "seed_key": "v2_compat.stage_io.ts_print",
        "stage_seed_key": "v2_compat.stage.ts_print",
        "input_quantity_node_key": "sheet",
        "output_quantity_node_key": "sheet",
        "yield_factor": 1.0,
        "loss_type": "none",
    },
    {
        "seed_key": "v2_compat.stage_io.lamination",
        "stage_seed_key": "v2_compat.stage.lamination",
        "input_quantity_node_key": "sheet",
        "output_quantity_node_key": "sheet",
        "yield_factor": 1.0,
        "loss_type": "none",
    },
    {
        "seed_key": "v2_compat.stage_io.diecut",
        "stage_seed_key": "v2_compat.stage.diecut",
        "input_quantity_node_key": "sheet",
        "output_quantity_node_key": "blanks",
        "yield_factor": 1.0,
        "loss_type": "trim",
    },
    {
        "seed_key": "v2_compat.stage_io.peeloff",
        "stage_seed_key": "v2_compat.stage.peeloff",
        "input_quantity_node_key": "blanks",
        "output_quantity_node_key": "blanks",
        "yield_factor": 1.0,
        "loss_type": "waste",
    },
    {
        "seed_key": "v2_compat.stage_io.seaming",
        "stage_seed_key": "v2_compat.stage.seaming",
        "input_quantity_node_key": "blanks",
        "output_quantity_node_key": "finished_goods",
        "yield_factor": 1.0,
        "loss_type": "none",
    },
    {
        "seed_key": "v2_compat.stage_io.fg",
        "stage_seed_key": "v2_compat.stage.fg",
        "input_quantity_node_key": "finished_goods",
        "output_quantity_node_key": "finished_goods",
        "yield_factor": 1.0,
        "loss_type": "none",
    },
]


DEFAULT_ROUTING_TEMPLATES = [
    {
        "seed_key": "v2_compat.routing.standard_2ply_board_fg",
        "name": "V2 Compat: Standard 2-Ply Board to FG",
        "code": "V2_COMPAT_2PLY_BOARD_FG",
        "active": True,
        "stages": [
            {"stage_seed_key": "v2_compat.stage.2ply", "sequence": 10, "is_optional": False},
            {"stage_seed_key": "v2_compat.stage.board", "sequence": 20, "is_optional": False},
            {"stage_seed_key": "v2_compat.stage.ts_print", "sequence": 30, "is_optional": True},
            {"stage_seed_key": "v2_compat.stage.lamination", "sequence": 40, "is_optional": True},
            {"stage_seed_key": "v2_compat.stage.diecut", "sequence": 50, "is_optional": False},
            {"stage_seed_key": "v2_compat.stage.peeloff", "sequence": 55, "is_optional": True},
            {"stage_seed_key": "v2_compat.stage.seaming", "sequence": 60, "is_optional": False},
            {"stage_seed_key": "v2_compat.stage.fg", "sequence": 70, "is_optional": False},
        ],
    },
]


DEFAULT_BOM_TRANSLATION_RULES = [
    {
        "seed_key": "v2_compat.bom_rule.corrugation_board",
        "name": "V2 Compat: Corrugation Stages to Board Bucket",
        "sequence": 10,
        "stage_types": ["corrugation"],
        "operation_behavior": "single_operation",
        "operation_ref_type": "none",
        "component_behavior": "component_line",
        "bucket_mapping": "board",
    },
    {
        "seed_key": "v2_compat.bom_rule.printing_sheet",
        "name": "V2 Compat: Printing / Lamination to Sheet Bucket",
        "sequence": 20,
        "stage_types": ["printing", "lamination"],
        "operation_behavior": "single_operation",
        "operation_ref_type": "none",
        "component_behavior": "component_line",
        "bucket_mapping": "sheet",
    },
    {
        "seed_key": "v2_compat.bom_rule.diecut_blanks",
        "name": "V2 Compat: Die-cut Stages to Blanks Bucket",
        "sequence": 30,
        "stage_types": ["diecut"],
        "operation_behavior": "single_operation",
        "operation_ref_type": "none",
        "component_behavior": "component_line",
        "bucket_mapping": "blanks",
    },
    {
        "seed_key": "v2_compat.bom_rule.conversion_fg",
        "name": "V2 Compat: Conversion / FG Stages to Finished Goods Bucket",
        "sequence": 40,
        "stage_types": ["conversion", "fg"],
        "operation_behavior": "single_operation",
        "operation_ref_type": "none",
        "component_behavior": "component_line",
        "bucket_mapping": "finished_goods",
    },
]


class CorrEnginePhase8SeedWizard(models.TransientModel):
    _name = "corr.engine.phase8.seed.wizard"
    _description = "Engine Phase 8 Compatibility Seeding Wizard"

    seed_global_defaults = fields.Boolean(
        string="Seed Global Defaults",
        default=True,
        help=(
            "When enabled, seed or update the standard Engine V2 compatibility profile "
            "for logical stages, routing templates, material profiles, material "
            "attachment rules and BoM translation rules."
        ),
    )
    seed_plant_bindings = fields.Boolean(
        string="Seed Plant Stage Bindings",
        default=False,
        help=(
            "When enabled, seed standard Engine V2 stage bindings for the selected plant "
            "based on the compatibility stage templates."
        ),
    )
    plant_id = fields.Many2one(
        "corr.plant.param",
        string="Plant",
        help="Plant for which standard Engine V2 stage bindings should be seeded.",
    )
    allow_default_updates = fields.Boolean(
        string="Allow Updates of System Defaults",
        default=False,
        help=(
            "When enabled, existing system-default records (created by this wizard) may "
            "be updated to match the latest compatibility profile definition. "
            "Customer-owned records are never updated."
        ),
    )
    dry_run = fields.Boolean(
        string="Dry Run (Preview Only)",
        default=False,
        help=(
            "When enabled, the wizard will simulate seeding and show a summary without "
            "creating or updating any records."
        ),
    )
    log_message = fields.Text(
        string="Log",
        readonly=True,
        help="Summary of seeding operations performed by this wizard run.",
    )

    @api.constrains("seed_plant_bindings", "plant_id")
    def _check_plant_required(self):
        for rec in self:
            if rec.seed_plant_bindings and not rec.plant_id:
                raise models.ValidationError(
                    _("Please select a plant when seeding plant stage bindings.")
                )

    def action_run_seed(self):
        self.ensure_one()
        summary = {}

        def _init_model(model_name):
            if model_name not in summary:
                summary[model_name] = {"created": 0, "updated": 0, "skipped": 0}

        def _bump(model_name, key):
            _init_model(model_name)
            summary[model_name][key] += 1

        if self.seed_global_defaults:
            self._seed_stage_templates(summary, _bump)
            self._seed_stage_io_templates(summary, _bump)
            self._seed_routing_templates(summary, _bump)
            self._seed_material_profiles(summary, _bump)
            self._seed_material_attachment_rules(summary, _bump)
            self._seed_bom_translation_rules(summary, _bump)

        if self.seed_plant_bindings and self.plant_id:
            self._seed_plant_stage_bindings(summary, _bump)

        self.log_message = self._build_log_message(summary)

        return {
            "type": "ir.actions.act_window",
            "res_model": "corr.engine.phase8.seed.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }

    # -------------------------------------------------------------------------
    # Helpers: global defaults
    # -------------------------------------------------------------------------

    def _seed_stage_templates(self, summary, bump):
        Stage = self.env["corr.engine.stage.template"].sudo()
        for spec in DEFAULT_STAGE_TEMPLATES:
            seed_key = spec["seed_key"]
            existing = Stage.search([("seed_key", "=", seed_key)], limit=1)
            if not existing:
                if not self.dry_run:
                    vals = {
                        "code": spec["code"],
                        "name": spec["name"],
                        "sequence": spec.get("sequence", 10),
                        "stage_type": spec["stage_type"],
                        "default_quantity_basis": spec.get("default_quantity_basis"),
                        "default_cost_basis": spec.get("default_cost_basis", "per_unit"),
                        "consider_slitting_options": spec.get("consider_slitting_options", False),
                        "consider_slitting_options_configured": True,
                        "is_entry_stage": spec.get("is_entry_stage", False),
                        "is_fg_stage": spec.get("is_fg_stage", False),
                        "is_optional": spec.get("is_optional", False),
                        "merge_group_key": spec.get("merge_group_key"),
                        "is_system_default": True,
                        "seed_key": seed_key,
                        "seed_version": PHASE8_SEED_VERSION,
                    }
                    Stage.create(vals)
                bump("corr.engine.stage.template", "created")
                continue

            # Existing record
            if not existing.is_system_default:
                bump("corr.engine.stage.template", "skipped")
                continue
            if not self.allow_default_updates:
                bump("corr.engine.stage.template", "skipped")
                continue

            update_vals = {
                "name": spec["name"],
                "sequence": spec.get("sequence", existing.sequence),
                "stage_type": spec["stage_type"],
                "default_quantity_basis": spec.get(
                    "default_quantity_basis", existing.default_quantity_basis
                ),
                "default_cost_basis": spec.get(
                    "default_cost_basis", existing.default_cost_basis
                ),
                "consider_slitting_options": spec.get(
                    "consider_slitting_options", existing.consider_slitting_options
                ),
                "consider_slitting_options_configured": True,
                "is_entry_stage": spec.get(
                    "is_entry_stage", existing.is_entry_stage
                ),
                "is_fg_stage": spec.get("is_fg_stage", existing.is_fg_stage),
                "is_optional": spec.get(
                    "is_optional", existing.is_optional
                ),
                "merge_group_key": spec.get(
                    "merge_group_key", existing.merge_group_key
                ),
                "seed_version": PHASE8_SEED_VERSION,
            }
            if not self.dry_run:
                existing.write(update_vals)
            bump("corr.engine.stage.template", "updated")

    def _seed_stage_io_templates(self, summary, bump):
        Stage = self.env["corr.engine.stage.template"].sudo()
        StageIO = self.env["corr.engine.stage.io.template"].sudo()

        for spec in DEFAULT_STAGE_IO_TEMPLATES:
            seed_key = spec["seed_key"]
            stage_seed_key = spec["stage_seed_key"]
            stage = Stage.search([("seed_key", "=", stage_seed_key)], limit=1)
            if not stage:
                # No stage yet; skip I/O template for now
                bump("corr.engine.stage.io.template", "skipped")
                continue

            existing = StageIO.search([("seed_key", "=", seed_key)], limit=1)
            if not existing:
                if not self.dry_run:
                    vals = {
                        "stage_template_id": stage.id,
                        "input_quantity_node_key": spec["input_quantity_node_key"],
                        "output_quantity_node_key": spec["output_quantity_node_key"],
                        "yield_factor": spec.get("yield_factor", 1.0),
                        "loss_type": spec.get("loss_type", "none"),
                        "is_system_default": True,
                        "seed_key": seed_key,
                        "seed_version": PHASE8_SEED_VERSION,
                    }
                    StageIO.create(vals)
                bump("corr.engine.stage.io.template", "created")
                continue

            if not existing.is_system_default:
                bump("corr.engine.stage.io.template", "skipped")
                continue
            if not self.allow_default_updates:
                bump("corr.engine.stage.io.template", "skipped")
                continue

            update_vals = {
                "stage_template_id": stage.id,
                "input_quantity_node_key": spec["input_quantity_node_key"],
                "output_quantity_node_key": spec["output_quantity_node_key"],
                "yield_factor": spec.get("yield_factor", existing.yield_factor),
                "loss_type": spec.get("loss_type", existing.loss_type),
                "seed_version": PHASE8_SEED_VERSION,
            }
            if not self.dry_run:
                existing.write(update_vals)
            bump("corr.engine.stage.io.template", "updated")

    def _seed_routing_templates(self, summary, bump):
        Routing = self.env["corr.engine.routing.template"].sudo()
        RoutingStage = self.env["corr.engine.routing.template.stage"].sudo()
        Stage = self.env["corr.engine.stage.template"].sudo()

        for spec in DEFAULT_ROUTING_TEMPLATES:
            seed_key = spec["seed_key"]
            existing = Routing.search([("seed_key", "=", seed_key)], limit=1)
            if not existing:
                if not self.dry_run:
                    vals = {
                        "name": spec["name"],
                        "code": spec.get("code"),
                        "active": spec.get("active", True),
                        "is_system_default": True,
                        "seed_key": seed_key,
                        "seed_version": PHASE8_SEED_VERSION,
                    }
                    routing = Routing.create(vals)
                    for line in spec.get("stages", []):
                        stage = Stage.search(
                            [("seed_key", "=", line["stage_seed_key"])], limit=1
                        )
                        if not stage:
                            continue
                        RoutingStage.create(
                            {
                                "routing_template_id": routing.id,
                                "sequence": line.get("sequence", 10),
                                "stage_template_id": stage.id,
                                "is_optional": line.get("is_optional", False),
                            }
                        )
                bump("corr.engine.routing.template", "created")
                continue

            if not existing.is_system_default:
                bump("corr.engine.routing.template", "skipped")
                continue
            if not self.allow_default_updates:
                bump("corr.engine.routing.template", "skipped")
                continue

            update_vals = {
                "name": spec["name"],
                "code": spec.get("code", existing.code),
                "seed_version": PHASE8_SEED_VERSION,
            }
            if not self.dry_run:
                existing.write(update_vals)
            bump("corr.engine.routing.template", "updated")

    def _seed_material_profiles(self, summary, bump):
        MaterialProfile = self.env["corr.engine.material.profile"].sudo()
        Map = self.env["corr.material.product.map"].sudo()

        # Seed material profiles based on existing corr.material.product.map entries.
        seen_products = set()
        for rec in Map.search([("active", "=", True)]):
            product = rec.product_id
            if not product:
                continue
            product_tmpl = product.product_tmpl_id
            if not product_tmpl or product_tmpl.id in seen_products:
                continue
            seen_products.add(product_tmpl.id)

            material_type, default_bucket = self._material_profile_defaults_for_kind(
                rec.kind
            )
            if not material_type:
                # Unknown kind; skip
                bump("corr.engine.material.profile", "skipped")
                continue

            existing = MaterialProfile.search(
                [("product_tmpl_id", "=", product_tmpl.id)], limit=1
            )
            seed_key = "v2_compat.material_profile.%s" % product_tmpl.id
            if not existing:
                if not self.dry_run:
                    vals = {
                        "product_tmpl_id": product_tmpl.id,
                        "material_type": material_type,
                        "role": rec.kind,
                        "default_bucket": default_bucket,
                        "is_system_default": True,
                        "seed_key": seed_key,
                        "seed_version": PHASE8_SEED_VERSION,
                    }
                    MaterialProfile.create(vals)
                bump("corr.engine.material.profile", "created")
                continue

            if not existing.is_system_default:
                bump("corr.engine.material.profile", "skipped")
                continue
            if not self.allow_default_updates:
                bump("corr.engine.material.profile", "skipped")
                continue

            update_vals = {
                "material_type": material_type,
                "role": rec.kind,
                "default_bucket": default_bucket,
                "seed_version": PHASE8_SEED_VERSION,
            }
            if not self.dry_run:
                existing.write(update_vals)
            bump("corr.engine.material.profile", "updated")

    def _material_profile_defaults_for_kind(self, kind):
        """Return (material_type, default_bucket) for a corr.material.product.map kind."""
        if kind in ("liner", "medium"):
            return "structural", "board"
        if kind in ("adhesive",):
            return "consumable", "board"
        if kind in ("ink",):
            return "consumable", "sheet"
        if kind in ("lamination",):
            return "consumable", "sheet"
        if kind in ("wire", "tape"):
            return "consumable", "finished_goods"
        return None, None

    def _seed_material_attachment_rules(self, summary, bump):
        MaterialProfile = self.env["corr.engine.material.profile"].sudo()
        AttachmentRule = self.env["corr.engine.material.attachment.rule"].sudo()

        for profile in MaterialProfile.search(
            [("is_system_default", "=", True)]
        ):
            kind = (profile.role or "").strip() or None
            stage_type, basis = self._attachment_defaults_for_kind(kind)
            if not stage_type or not basis:
                bump("corr.engine.material.attachment.rule", "skipped")
                continue

            seed_key = "v2_compat.material_rule.%s" % profile.id
            existing = AttachmentRule.search([("seed_key", "=", seed_key)], limit=1)
            name = "V2 Compat: Attach %s" % (profile.product_tmpl_id.display_name,)

            if not existing:
                if not self.dry_run:
                    vals = {
                        "name": name,
                        "sequence": 10,
                        "plant_id": False,
                        "material_profile_id": profile.id,
                        "stage_type": stage_type,
                        "attach_quantity_basis": basis,
                        "is_mandatory": False,
                        "is_system_default": True,
                        "seed_key": seed_key,
                        "seed_version": PHASE8_SEED_VERSION,
                    }
                    AttachmentRule.create(vals)
                bump("corr.engine.material.attachment.rule", "created")
                continue

            if not existing.is_system_default:
                bump("corr.engine.material.attachment.rule", "skipped")
                continue
            if not self.allow_default_updates:
                bump("corr.engine.material.attachment.rule", "skipped")
                continue

            update_vals = {
                "name": name,
                "stage_type": stage_type,
                "attach_quantity_basis": basis,
                "seed_version": PHASE8_SEED_VERSION,
            }
            if not self.dry_run:
                existing.write(update_vals)
            bump("corr.engine.material.attachment.rule", "updated")

    def _attachment_defaults_for_kind(self, kind):
        """Return (stage_type, attach_quantity_basis) for a given material role/kind."""
        if kind in ("liner", "medium"):
            return "corrugation", "per_m2"
        if kind in ("adhesive",):
            return "conversion", "per_box"
        if kind in ("ink",):
            return "printing", "per_m2"
        if kind in ("lamination",):
            return "lamination", "per_m2"
        if kind in ("wire", "tape"):
            return "fg", "per_box"
        return None, None

    def _seed_bom_translation_rules(self, summary, bump):
        BomRule = self.env["corr.engine.bom.translation.rule"].sudo()
        Stage = self.env["corr.engine.stage.template"].sudo()

        for spec in DEFAULT_BOM_TRANSLATION_RULES:
            seed_key = spec["seed_key"]
            existing = BomRule.search([("seed_key", "=", seed_key)], limit=1)
            if not existing:
                if not self.dry_run:
                    stage_ids = Stage.search(
                        [
                            ("stage_type", "in", spec.get("stage_types", [])),
                            ("is_system_default", "=", True),
                        ]
                    ).ids
                    vals = {
                        "name": spec["name"],
                        "sequence": spec.get("sequence", 10),
                        "plant_id": False,
                        "operation_behavior": spec["operation_behavior"],
                        "operation_ref_type": spec["operation_ref_type"],
                        "component_behavior": spec["component_behavior"],
                        "bucket_mapping": spec.get("bucket_mapping"),
                        "is_system_default": True,
                        "seed_key": seed_key,
                        "seed_version": PHASE8_SEED_VERSION,
                    }
                    rule = BomRule.create(vals)
                    if stage_ids:
                        rule.stage_template_ids = [(6, 0, stage_ids)]
                bump("corr.engine.bom.translation.rule", "created")
                continue

            if not existing.is_system_default:
                bump("corr.engine.bom.translation.rule", "skipped")
                continue
            if not self.allow_default_updates:
                bump("corr.engine.bom.translation.rule", "skipped")
                continue

            update_vals = {
                "name": spec["name"],
                "sequence": spec.get("sequence", existing.sequence),
                "operation_behavior": spec["operation_behavior"],
                "operation_ref_type": spec["operation_ref_type"],
                "component_behavior": spec["component_behavior"],
                "bucket_mapping": spec.get("bucket_mapping", existing.bucket_mapping),
                "seed_version": PHASE8_SEED_VERSION,
            }
            if not self.dry_run:
                existing.write(update_vals)
            bump("corr.engine.bom.translation.rule", "updated")

    # -------------------------------------------------------------------------
    # Helpers: plant-specific bindings
    # -------------------------------------------------------------------------

    def _seed_plant_stage_bindings(self, summary, bump):
        if not self.plant_id:
            return
        Binding = self.env["corr.engine.plant.stage.binding"].sudo()
        Stage = self.env["corr.engine.stage.template"].sudo()
        plant = self.plant_id

        stages = Stage.search([("is_system_default", "=", True)])
        for stage in stages:
            existing = Binding.search(
                [
                    ("plant_id", "=", plant.id),
                    ("stage_template_id", "=", stage.id),
                ],
                limit=1,
            )
            seed_key = "v2_compat.binding.%s.%s" % (plant.id, stage.seed_key or stage.code)

            if not existing:
                if not self.dry_run:
                    vals = {
                        "plant_id": plant.id,
                        "stage_template_id": stage.id,
                        "sequence": stage.sequence,
                        "workcenter_id": False,
                        "routing_workcenter_id": False,
                        "merge_with_stage_template_id": False,
                        "is_system_default": True,
                        "seed_key": seed_key,
                        "seed_version": PHASE8_SEED_VERSION,
                    }
                    Binding.create(vals)
                bump("corr.engine.plant.stage.binding", "created")
                continue

            if not existing.is_system_default:
                bump("corr.engine.plant.stage.binding", "skipped")
                continue
            if not self.allow_default_updates:
                bump("corr.engine.plant.stage.binding", "skipped")
                continue

            update_vals = {
                "sequence": stage.sequence,
                "seed_version": PHASE8_SEED_VERSION,
            }
            if not self.dry_run:
                existing.write(update_vals)
            bump("corr.engine.plant.stage.binding", "updated")

    # -------------------------------------------------------------------------
    # Helpers: log rendering
    # -------------------------------------------------------------------------

    def _build_log_message(self, summary):
        lines = []
        lines.append(
            _("Phase 8 compatibility seeding completed (dry_run=%s).")
            % ("yes" if self.dry_run else "no")
        )
        if not summary:
            lines.append(_("No models were processed."))
            return "\n".join(lines)

        lines.append("")
        for model_name, stats in sorted(summary.items()):
            label = model_name
            lines.append(
                "%s: created=%s, updated=%s, skipped=%s"
                % (
                    label,
                    stats.get("created", 0),
                    stats.get("updated", 0),
                    stats.get("skipped", 0),
                )
            )
        return "\n".join(lines)



