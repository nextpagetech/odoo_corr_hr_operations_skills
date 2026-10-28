from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CorrSpecTemplateBridge(models.Model):
    _inherit = "corr.spec.template"

    plant_param_id = fields.Many2one(
        "corr.plant.param",
        string="Plant Parameters",
        help="Optional plant parameter set to use when computing corrugation requirements.",
    )
    plant_setup_waste_m2 = fields.Float(
        string="Setup Waste (m2)",
        related="plant_param_id.setup_waste_m2",
        readonly=False,
    )
    plant_process_waste_pct = fields.Float(
        string="Process Waste %",
        related="plant_param_id.process_waste_pct",
        readonly=False,
    )
    plant_default_glue_lap_mm = fields.Float(
        string="Default Glue Lap (mm)",
        related="plant_param_id.default_glue_lap_mm",
        readonly=False,
    )
    plant_flap_basis = fields.Selection(
        related="plant_param_id.flap_basis",
        readonly=False,
    )
    plant_flap_fixed_mm = fields.Float(
        string="Flap Fixed (mm)",
        related="plant_param_id.flap_fixed_mm",
        readonly=False,
    )
    plant_flap_percent = fields.Float(
        string="Flap Percent",
        related="plant_param_id.flap_percent",
        readonly=False,
    )
    plant_flap_percent_of = fields.Selection(
        related="plant_param_id.flap_percent_of",
        readonly=False,
    )
    plant_ink_gm2_at_100pct_default = fields.Float(
        string="Ink gm2 @100%",
        related="plant_param_id.ink_gm2_at_100pct_default",
        readonly=False,
    )
    plant_lam_overlap_percent_default = fields.Float(
        string="Lamination Overlap %",
        related="plant_param_id.lam_overlap_percent_default",
        readonly=False,
    )
    plant_humidity_factor = fields.Float(
        string="Humidity Factor",
        related="plant_param_id.humidity_factor",
        readonly=False,
    )
    plant_gsm_tolerance_pct = fields.Float(
        string="GSM Tolerance %",
        related="plant_param_id.gsm_tolerance_pct",
        readonly=False,
    )
    plant_default_ply_max = fields.Integer(
        string="Default Ply Max",
        related="plant_param_id.default_ply_max",
        readonly=False,
    )

    # UI field to drive finishing operations after Die-cut (Peeling)
    finishing_line_ids = fields.One2many(
        "corr.spec.finishing.line",
        "spec_id",
        string="Finishing Operations",
        help="Ordered list of finishing operations after Peeling. If set, overrides default locking behavior.")

    corr_sfg_mode = fields.Selection(
        [
            ("inherit", "Inherit Global/Product SFG Mode"),
            ("none", "No SFG (Flat FG BoM)"),
            ("flat_single_wo", "Flat FG (Single Work Order)"),
            ("phantom", "SFG as Phantom"),
            ("staged", "Staged Stockable SFGs"),
        ],
        string="Corr SFG Mode",
        help=(
            "Optional override for this spec's corrugation BoM structure.\n"
            "Inherit: use product/global SFG Mode.\n"
            "No SFG: generate a flat FG BoM without staged SFG products.\n"
            "Flat FG (Single Work Order): generate a flat FG BoM and collapse corrugation/finishing into a single work order.\n"
            "SFG as Phantom: generate SFG structure but mark SFG BoMs as phantom.\n"
            "Staged Stockable SFGs: generate stockable 2-ply/4-ply/board/die-cut SFGs."
        ),
        default="inherit",
    )

    corr_corr_stage_mode = fields.Selection(
        [
            ("inherit", "Default Corrugation Staging"),
            ("full", "2/4/Board Staged SFGs"),
            ("board_only", "Board-only (no 4-ply SFG)"),
        ],
        string="Corr Stage Mode",
        help=(
            "Optional override for corrugated SFG staging for this spec.\n"
            "Default: keep current behavior (build 4-ply when applicable).\n"
            "2/4/Board: always build 2-ply, 4-ply and board SFGs when applicable.\n"
            "Board-only: skip explicit 4-ply SFG; board SFG consumes 2-ply SFGs directly."
        ),
        default="inherit",
    )

    top_sheet_roll_code = fields.Char(
        string="Top Sheet Roll Code",
        help="Code used in SFG names when Top Layer Mode is Roll.",
    )

    surface_profile_id = fields.Many2one(
        "corr.engine.surface.profile",
        string="Surface Profile",
        help=(
            "Optional: default surface/printing configuration. When set, core "
            "printing/top-sheet/lamination fields on the spec are pre-filled "
            "from this profile, but the spec holds the final editable values."
        ),
    )

    def _build_vals_from_surface_profile(self, profile):
        """Return a vals dict populated from a surface profile.

        The spec remains the single source of truth; this helper is used
        by create/write/onchange to copy values from the selected profile
        into the spec fields without introducing any runtime dependency
        on the profile for engine inputs.
        """

        vals = {}
        if not profile:
            return vals

        try:
            vals["printing_required"] = bool(profile.printing_required)
        except Exception:
            pass
        try:
            vals["print_colors"] = int(profile.print_colors or 0)
        except Exception:
            pass
        try:
            vals["coverage_pct"] = float(profile.print_coverage_percent or 0.0)
        except Exception:
            pass

        # Map top-sheet mode onto the existing BMC UI helper so that
        # extra_params_json remains consistent with the spec fields.
        try:
            mode_val = (profile.top_sheet_mode or "").strip().lower()
        except Exception:
            mode_val = ""
        if mode_val == "roll":
            vals["top_layer_mode_ui"] = "Roll"
        elif mode_val == "unit_sheet":
            vals["top_layer_mode_ui"] = "Unit"

        # Top-sheet product is copied directly to the spec; this keeps
        # unit-sheet behaviour product-first while allowing manual edits.
        try:
            ts_prod = profile.top_sheet_product_id
            vals["top_sheet_product_id"] = ts_prod.id if ts_prod and ts_prod.id else False
        except Exception:
            pass

        # Lamination: when the profile explicitly enables lamination and
        # provides a type, propagate it onto the spec.
        try:
            if profile.lamination_required and profile.lamination_type_id:
                vals["lamination_type_id"] = profile.lamination_type_id.id
        except Exception:
            pass

        return vals

    @api.onchange("surface_profile_id")
    def _onchange_surface_profile_id(self):
        for rec in self:
            if not rec.surface_profile_id:
                continue
            vals = rec._build_vals_from_surface_profile(rec.surface_profile_id)
            for field_name, value in vals.items():
                try:
                    setattr(rec, field_name, value)
                except Exception:
                    # Onchange must never break the form; users can still edit manually.
                    continue

    def action_open_bom_wizard(self):
        self.ensure_one()
        # Read defaults from system parameters so the wizard opens with the same
        # options that auto-BoM uses during product import.
        icp = self.env["ir.config_parameter"].sudo()
        include_ops = icp.get_param("corr_mrp_bridge.default_include_ops", default="1") == "1"
        include_scrap = icp.get_param("corr_mrp_bridge.default_include_scrap", default="1") == "1"
        include_sfg = icp.get_param("corr_mrp_bridge.default_include_sfg", default="1") == "1"
        # Default to staged SFGs when not explicitly configured
        sfg_mode = icp.get_param("corr_mrp_bridge.sfg_mode") or "staged"
        # Per-spec override when configured
        try:
            mode_spec = (self.corr_sfg_mode or "inherit").strip()
            if mode_spec and mode_spec != "inherit":
                sfg_mode = mode_spec
        except Exception:
            sfg_mode = icp.get_param("corr_mrp_bridge.sfg_mode") or "staged"
        # Per-product override when spec is inheriting
        try:
            tmpl = self.product_tmpl_id
            if tmpl and sfg_mode in ("staged", "phantom", "none", "flat_single_wo"):
                mode_pt = (getattr(tmpl, "corr_sfg_mode", "") or "inherit").strip()
                if mode_pt and mode_pt != "inherit":
                    sfg_mode = mode_pt
        except Exception:
            sfg_mode = icp.get_param("corr_mrp_bridge.sfg_mode") or "staged"
        return {
            "type": "ir.actions.act_window",
            "name": _("Create/Update BoM"),
            "res_model": "corr.bom.generate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_spec_id": self.id,
                "default_target_product_tmpl_id": self.product_tmpl_id.id if self.product_tmpl_id else False,
                # Pre-check toggles for convenience
                "default_include_operations": include_ops,
                "default_include_scrap_byproduct": include_scrap,
                "default_include_sfg_phantom": (sfg_mode == "phantom"),
                "default_sfg_mode": sfg_mode,
            },
        }

    def action_open_compute_wizard(self):
        action = super().action_open_compute_wizard()
        if self.plant_param_id:
            context = dict(action.get("context") or {})
            context["corr_plant_param_id"] = self.plant_param_id.id
            action["context"] = context
        return action

    # Enforce Roll mode auto-pick: when Top Layer Mode is 'roll' and no explicit
    # top-sheet override is provided, keep spec fields empty so the wizard can
    # auto-pick a roll. Also scrub any stale product/code keys from the JSON.
    def write(self, vals):
        if self.env.context.get("skip_ts_autoclean"):
            return super().write(vals)

        vals = dict(vals)  # avoid mutating caller's dict

        # When a surface profile is selected via the UI or RPC, copy its
        # surface/printing/top-sheet settings into the spec so that the
        # spec remains the single source of truth for engine inputs.
        sp_id = vals.get("surface_profile_id")
        if sp_id:
            try:
                profile = self.env["corr.engine.surface.profile"].browse(sp_id)
                profile_vals = self._build_vals_from_surface_profile(profile)
                # Spec-provided values in vals take precedence; only fill
                # where the caller did not specify a value.
                for key, value in profile_vals.items():
                    vals.setdefault(key, value)
            except Exception:
                # Profile copy is best-effort; failures must not break writes.
                pass

        res = super().write(vals)
        try:
            # Sync finishing ops into extra_params_json unless explicitly skipped
            if not self.env.context.get("skip_finishing_sync"):
                for rec in self:
                    xp_now = getattr(rec, "extra_params_json", None) or {}
                    xp2 = dict(xp_now) if isinstance(xp_now, dict) else {}
                    # Finishing ops (ordered)
                    if ("finishing_line_ids" in vals) or rec.finishing_line_ids:
                        ops = []
                        for line in rec.finishing_line_ids.sorted(lambda l: l.sequence or 0):
                            code = (line.op_code or "").strip()
                            if not code:
                                continue
                            if code == "gluing":
                                ops.append("gluing")
                            elif code == "stitching":
                                ops.append("stitching")
                            elif code == "folding_auto":
                                ops.append("folding:auto")
                            elif code == "folding_manual":
                                ops.append("folding:manual")
                            elif code == "flap_pasting":
                                ops.append("flap_pasting")
                            elif code == "bottom_lock":
                                ops.append("bottom_lock")
                        xp2["finishing_ops"] = ops
                    # Persist JSON
                    if xp2 != xp_now:
                        rec.with_context(skip_finishing_sync=True, skip_ts_autoclean=True).write({"extra_params_json": xp2})

            def _is_roll(rec_local, vals_local):
                try:
                    # 1) New JSON in this write
                    if "extra_params_json" in vals_local and isinstance(vals_local.get("extra_params_json"), dict):
                        raw = vals_local["extra_params_json"].get("top_layer_mode")
                        if raw is not None and "roll" in str(raw).strip().lower():
                            return True
                    # 2) UI helper in this write (top_layer_mode_ui)
                    if "top_layer_mode_ui" in vals_local and vals_local.get("top_layer_mode_ui"):
                        if "roll" in str(vals_local.get("top_layer_mode_ui")).strip().lower():
                            return True
                    # 3) Current UI field on record
                    ui = getattr(rec_local, "top_layer_mode_ui", False)
                    if ui and "roll" in str(ui).strip().lower():
                        return True
                    # 4) Existing JSON on record
                    xp_now = getattr(rec_local, "extra_params_json", None) or {}
                    raw2 = xp_now.get("top_layer_mode")
                    if raw2 is not None and "roll" in str(raw2).strip().lower():
                        return True
                except Exception:
                    return False
                return False

            for rec in self:
                if not _is_roll(rec, vals):
                    continue
                # Determine if an explicit override is provided in this write
                # (or currently set). If neither product nor code is set, clean.
                explicit_pid = None
                explicit_code = None
                try:
                    if "top_sheet_product_id" in vals:
                        explicit_pid = vals.get("top_sheet_product_id")
                    else:
                        explicit_pid = bool(rec.top_sheet_product_id)
                except Exception:
                    explicit_pid = None
                try:
                    if "top_sheet_code" in vals:
                        explicit_code = vals.get("top_sheet_code")
                    else:
                        explicit_code = bool(rec.top_sheet_code)
                except Exception:
                    explicit_code = None
                if explicit_pid or explicit_code:
                    continue
                # Scrub: clear fields and remove JSON bindings
                xp_now = getattr(rec, "extra_params_json", None) or {}
                xp2 = dict(xp_now) if isinstance(xp_now, dict) else {}
                xp2.pop("top_sheet_product_id", None)
                xp2.pop("top_sheet_code", None)
                rec.with_context(skip_ts_autoclean=True).write({
                    "top_sheet_product_id": False,
                    "top_sheet_code": False,
                    "extra_params_json": xp2,
                })
        except Exception:
            pass
        return res
