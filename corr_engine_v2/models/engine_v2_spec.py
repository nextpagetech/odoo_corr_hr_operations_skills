import hashlib
import json
import logging
import re
from types import SimpleNamespace

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


LAYOUT_PLANNING_CACHE_VERSION = "full_blank_one_up_v4"
_logger = logging.getLogger(__name__)


class CorrEngineV2Spec(models.Model):
    _name = "corr.engine.v2.spec"
    _description = "Engine V2 Spec"

    name = fields.Char(
        string="Spec Name / Code",
        required=True,
        help="Human-readable manufacturing definition name or internal spec code.",
    )

    # Core product fields
    product_tmpl_id = fields.Many2one(
        "product.template",
        string="Finished Product",
        domain=[("product_role", "in", ["finished_good", "other"])],
        help=(
            "Finished product this manufacturing definition creates. "
            "Used by Engine V2 preview BoM creation; quantities remain on the "
            "preview/MO flow, not on the spec."
        ),
    )
    style_id = fields.Many2one("corr.engine.style", string="Style / FEFCO")
    plant_id = fields.Many2one(
        "corr.plant.param",
        string="Plant",
        default=lambda self: self._default_plant_id(),
    )
    style_param_value_ids = fields.One2many(
        "corr.engine.v2.spec.style.param.value",
        "spec_id",
        string="Style Parameters",
    )
    fg_per_sheet = fields.Integer(
        string="FG per Sheet",
        default=1,
        help="Finished goods produced from one diecut sheet. Used by DIECUT_SHEET only.",
    )
    has_style_params = fields.Boolean(
        string="Has Style Parameters",
        compute="_compute_has_style_params",
    )
    is_sheet_style = fields.Boolean(
        string="Sheet Style",
        compute="_compute_style_ui_labels",
    )
    is_roll_style = fields.Boolean(
        string="Roll Style",
        compute="_compute_style_ui_labels",
    )
    is_diecut_sheet_style = fields.Boolean(
        string="Diecut Sheet Style",
        compute="_compute_style_ui_labels",
    )
    geometry_group_label = fields.Char(
        string="Geometry Label",
        compute="_compute_style_ui_labels",
    )
    length_field_label = fields.Char(
        string="Length Label",
        compute="_compute_style_ui_labels",
    )
    width_field_label = fields.Char(
        string="Width Label",
        compute="_compute_style_ui_labels",
    )

    # Box dimensions (finished good)
    box_length_mm = fields.Float(string="Box Length (mm)")
    box_width_mm = fields.Float(string="Box Width (mm)")
    box_height_mm = fields.Float(string="Box Height (mm)")
    roll_length_m = fields.Float(
        string="Roll Length (m)",
        compute="_compute_roll_length_m",
        inverse="_inverse_roll_length_m",
    )

    # Glue flap override
    glue_flap_override_mm = fields.Float(string="Glue Flap Override (mm)")

    # Profile references
    board_recipe_id = fields.Many2one(
        "corr.engine.board.recipe",
        string="Board Recipe",
        help="Reusable default stack. Apply it to copy standard board layers into this spec.",
    )
    surface_profile_id = fields.Many2one(
        "corr.engine.surface.profile",
        string="Surface Profile",
        default=lambda self: self._default_surface_profile_id(),
    )
    surface_printing_required = fields.Boolean(
        string="Printing Required",
    )
    surface_print_process = fields.Selection(
        [
            ("flexo", "Flexo"),
            ("offset", "Offset"),
            ("digital", "Digital"),
            ("screen", "Screen"),
            ("other", "Other"),
        ],
        string="Print Method",
    )
    surface_print_colors = fields.Integer(
        string="Print Colors",
    )
    surface_print_coverage_percent = fields.Float(
        string="Print Coverage %",
        digits=(16, 2),
    )
    surface_top_sheet_mode = fields.Selection(
        [
            ("none", "None"),
            ("roll", "Top from Roll"),
            ("unit_sheet", "Unit Printed Sheet"),
        ],
        string="Top Sheet Mode",
    )
    surface_top_sheet_product_id = fields.Many2one(
        "product.product",
        string="Top Sheet Product",
        domain="[('corr_top_sheet_selectable', '=', True)]",
    )
    surface_top_sheet_roll_code = fields.Char(
        string="Top Sheet Roll Code",
    )
    surface_lamination_required = fields.Boolean(
        string="Lamination Enabled",
    )
    surface_lamination_type_id = fields.Many2one(
        "corr.lamination.type",
        string="Film Type",
    )
    surface_lamination_side = fields.Selection(
        [
            ("top", "Top"),
            ("bottom", "Bottom"),
            ("both", "Both"),
        ],
        string="Lamination Side",
    )
    joining_type = fields.Selection(
        [
            ("none", "None"),
            ("gluing", "Gluing"),
            ("stitching", "Stitching"),
            ("gluing_stitching", "Gluing + Stitching"),
            ("tape", "Tape"),
        ],
        string="Joining Type",
        default="none",
        required=True,
        help="Business joining method used to derive jointing glue, stitching wire, or tape consumables.",
    )
    stitching_wire_type_id = fields.Many2one(
        "corr.engine.v2.wire.type",
        string="Stitching Wire Type",
        domain="[('active', '=', True)]",
        help="Wire product and consumption factors used when the joining type consumes stitching wire.",
    )
    use_plant_consumable_defaults = fields.Boolean(
        string="Use Plant Consumable Defaults",
        default=True,
        help="When enabled, Engine V2 uses the plant/system consumable settings for this spec.",
    )
    enable_consumables = fields.Boolean(
        string="Enable Consumables",
        help="Allow Engine V2 to include consumable materials for this spec when plant defaults are not used.",
    )
    enable_corrugation_adhesive = fields.Boolean(string="Corrugation Adhesive")
    enable_lamination_adhesive = fields.Boolean(string="Lamination Adhesive")
    enable_printing_ink = fields.Boolean(string="Printing Ink")
    enable_lamination_film = fields.Boolean(string="Lamination Film")
    enable_stitching_wire = fields.Boolean(string="Stitching Wire")
    enable_jointing_glue = fields.Boolean(string="Jointing Glue")
    enable_jointing_tape = fields.Boolean(string="Jointing Tape")
    layout_policy_id = fields.Many2one("corr.engine.layout.policy", string="Layout Policy")
    routing_profile_id = fields.Many2one(
        "corr.engine.routing.profile",
        string="Routing Profile",
        help="Main Engine V2 route source used for preview and BoM materialization.",
    )
    surface_profile_summary_html = fields.Html(
        string="Surface Profile Summary",
        compute="_compute_profile_summary_html",
        sanitize=False,
    )
    layout_policy_summary_html = fields.Html(
        string="Layout Policy Summary",
        compute="_compute_profile_summary_html",
        sanitize=False,
    )
    routing_profile_summary_html = fields.Html(
        string="Routing Profile Summary",
        compute="_compute_profile_summary_html",
        sanitize=False,
    )
    routing_stage_summary_html = fields.Html(
        string="Routing Stages",
        compute="_compute_routing_stage_summary_html",
        sanitize=False,
    )
    last_preview_raw_json = fields.Text(
        string="Last Preview Raw JSON",
        readonly=True,
        copy=False,
    )
    last_preview_generated_at = fields.Datetime(
        string="Last Preview Generated At",
        readonly=True,
        copy=False,
    )
    last_preview_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("success", "Success"),
            ("failed", "Failed"),
        ],
        string="Last Preview Status",
        readonly=True,
        copy=False,
    )
    last_preview_error = fields.Text(
        string="Last Preview Error",
        readonly=True,
        copy=False,
    )
    phase8_routing_template_id = fields.Many2one(
        "corr.engine.routing.template",
        string="Phase 8 Routing Template",
        help=(
            "Technical fallback only. Routing Profile is the main Engine V2 route source."
        ),
    )

    # Convenience related field to drive UI behaviour (manual sheet mode).
    sheet_source_mode = fields.Selection(
        related="layout_policy_id.sheet_source",
        string="Sheet Source Mode",
        readonly=True,
    )

    # Optional overrides
    manual_sheet_length_mm = fields.Float(string="Manual Sheet Length (mm)")
    manual_sheet_width_mm = fields.Float(string="Manual Sheet Width (mm)")
    forced_ups = fields.Integer(
        string="Required Slitting UPS",
        help="Optional preferred slitting/layout UPS. When provided, a feasible layout option with this UPS is preferred automatically.",
    )
    ups_enforcement = fields.Selection(
        [
            ("soft", "Soft"),
            ("hard", "Hard"),
        ],
        string="UPS Enforcement",
    )

    # Flag indicating that board structure has been customized beyond the recipe.
    is_board_customized = fields.Boolean(
        string="Board Customized",
        default=True,
        help=(
            "Unchecked for recipe-controlled stacks. Checked for manual/custom "
            "board specs and when recipe layers are customized."
        ),
    )

    # Spec-specific board structure (rich roles)
    stack_line_ids = fields.One2many(
        "corr.engine.v2.spec.layer",
        "spec_id",
        string="Board Layers",
    )

    # Persistent preview-only multi-piece definition. Phase D stores the
    # accepted split on the spec, but BoM materialization remains blocked.
    multi_piece_enabled = fields.Boolean(string="Enable Multi-Piece Manufacturing")
    multi_piece_source = fields.Selection(
        [
            ("accepted_auto_split", "Accepted Auto Split"),
            ("explicit_multi_piece_design", "Explicit Multi-Piece Design"),
        ],
        string="Multi-Piece Source",
        default="explicit_multi_piece_design",
    )
    multi_piece_strategy = fields.Selection(
        [
            ("independent_routed_piece", "Independent Piece Manufacturing"),
            ("shared_upstream_split", "Shared Upstream Split"),
        ],
        string="Multi-Piece Strategy",
        default="independent_routed_piece",
        required=True,
    )
    multi_piece_split_axis = fields.Selection(
        [("width", "Width"), ("length", "Length")],
        string="Split Axis",
        default="width",
    )
    multi_piece_split_mode = fields.Selection(
        [("equal", "Equal"), ("uneven", "Uneven")],
        string="Split Mode",
        default="equal",
    )
    multi_piece_source_length_mm = fields.Float(string="Source Blank Length (mm)")
    multi_piece_source_width_mm = fields.Float(string="Source Blank Width (mm)")
    accepted_split_signature = fields.Char(string="Accepted Split Signature")
    multi_piece_merge_stage_key = fields.Char(string="Merge Stage", default="seaming")
    multi_piece_split_after_stage_key = fields.Char(string="Split After Stage")
    multi_piece_line_ids = fields.One2many(
        "corr.engine.v2.spec.piece",
        "spec_id",
        string="Multi-Piece Lines",
    )
    multi_piece_split_suggestion_ids = fields.One2many(
        "corr.engine.v2.spec.split.suggestion",
        "spec_id",
        string="Suggested Split Layouts",
    )
    full_blank_layout_option_ids = fields.One2many(
        "corr.engine.v2.spec.layout.option",
        "spec_id",
        string="Full Blank Layout Options",
    )
    selected_layout_stage_identity = fields.Char(string="Selected Layout Stage")
    selected_layout_option_json = fields.Text(string="Selected Layout Option")
    selected_layout_selection_mode = fields.Selection(
        [
            ("manual", "Manual"),
            ("required_ups", "Required UPS"),
            ("preferred", "Preferred"),
        ],
        string="Selected Layout Mode",
        readonly=True,
    )
    multi_piece_split_suggestion_message = fields.Text(
        string="Split Suggestion Message",
        readonly=True,
    )
    layout_planning_message = fields.Text(
        string="Layout Planning Message",
        readonly=True,
    )
    layout_planning_diagnostics_html = fields.Html(
        string="Layout Planning Diagnostics",
        readonly=True,
        sanitize=False,
    )
    layout_planning_hint = fields.Char(
        string="Layout Planning Hint",
        compute="_compute_layout_planning_state",
    )
    layout_planning_inputs_ready = fields.Boolean(
        string="Layout Planning Inputs Ready",
        compute="_compute_layout_planning_state",
    )
    layout_planning_calculated = fields.Boolean(
        string="Layout Planning Calculated",
        default=False,
    )
    layout_planning_cache_version = fields.Char(
        string="Layout Planning Cache Version",
        default=LAYOUT_PLANNING_CACHE_VERSION,
        readonly=True,
        copy=False,
    )
    has_full_blank_layout_options = fields.Boolean(
        string="Has Full Blank Layout Options",
        compute="_compute_has_full_blank_layout_options",
    )
    split_suggestions_calculated = fields.Boolean(
        string="Split Suggestions Calculated",
        default=False,
    )
    has_split_suggestions = fields.Boolean(
        string="Has Split Suggestions",
        compute="_compute_has_split_suggestions",
    )
    show_split_alternatives = fields.Boolean(string="Show Split Alternatives")
    multi_piece_split_stale = fields.Boolean(
        string="Split Layout Stale",
        default=False,
    )
    multi_piece_split_warning = fields.Char(
        string="Split Warning",
        compute="_compute_multi_piece_split_warning",
    )

    @api.depends("multi_piece_split_stale")
    def _compute_multi_piece_split_warning(self):
        for spec in self:
            spec.multi_piece_split_warning = (
                _("Planning inputs changed. Refresh layout options and reapply.")
                if spec.multi_piece_split_stale
                else ""
            )

    @api.depends("full_blank_layout_option_ids")
    def _compute_has_full_blank_layout_options(self):
        for spec in self:
            spec.has_full_blank_layout_options = bool(spec.full_blank_layout_option_ids)

    @api.depends("multi_piece_split_suggestion_ids")
    def _compute_has_split_suggestions(self):
        for spec in self:
            spec.has_split_suggestions = bool(spec.multi_piece_split_suggestion_ids)

    @api.depends(
        "style_id",
        "plant_id",
        "board_recipe_id",
        "stack_line_ids",
        "box_length_mm",
        "box_width_mm",
        "box_height_mm",
        "layout_planning_calculated",
        "layout_planning_message",
    )
    def _compute_layout_planning_state(self):
        for spec in self:
            ready = spec._layout_planning_inputs_ready()
            spec.layout_planning_inputs_ready = ready
            if not ready:
                spec.layout_planning_hint = _(
                    "Complete the spec details to calculate layout options."
                )
            elif not spec.layout_planning_calculated:
                spec.layout_planning_hint = _(
                    "Click Refresh Layout Options to calculate feasible layouts."
                )
            else:
                spec.layout_planning_hint = spec.layout_planning_message or ""

    def _layout_planning_inputs_ready(self):
        self.ensure_one()
        if not self.style_id or not self.plant_id:
            return False

        code = (self.style_id.code or "").strip() if self.style_id else ""
        family = (self.style_id.family or "").strip() if self.style_id else ""
        mono_carton = bool(family == "mono_carton" or code.upper().startswith("MONO_"))
        if not mono_carton and not self.board_recipe_id and not self.stack_line_ids:
            return False

        length = float(self.box_length_mm or 0.0)
        width = float(self.box_width_mm or 0.0)
        height = float(self.box_height_mm or 0.0)
        if family in ("sheet", "diecut_sheet", "roll") or code in ("0110", "0100", "DIECUT_SHEET"):
            return length > 0.0 and width > 0.0
        return length > 0.0 and width > 0.0 and height > 0.0

    @api.depends("style_id", "style_id.param_ids", "style_id.param_ids.active", "style_param_value_ids")
    def _compute_has_style_params(self):
        for spec in self:
            spec.has_style_params = bool(
                spec.style_param_value_ids
                or (spec.style_id and spec.style_id.param_ids.filtered(lambda param: param.active))
            )

    @api.depends("style_id", "style_id.family", "style_id.code")
    def _compute_style_ui_labels(self):
        for spec in self:
            code = (spec.style_id.code or "").strip() if spec.style_id else ""
            family = (spec.style_id.family or "").strip() if spec.style_id else ""
            is_sheet = bool(family in ("sheet", "diecut_sheet")) or code == "0110"
            is_roll = bool(family == "roll") or code == "0100"
            spec.is_sheet_style = is_sheet
            spec.is_roll_style = is_roll
            spec.is_diecut_sheet_style = bool(code == "DIECUT_SHEET" or family == "diecut_sheet")
            if is_roll:
                spec.geometry_group_label = _("Roll Size")
                spec.length_field_label = _("Roll Length (m)")
                spec.width_field_label = _("Roll Width (mm)")
            elif is_sheet:
                spec.geometry_group_label = _("Sheet Size")
                spec.length_field_label = _("Sheet Length (mm)")
                spec.width_field_label = _("Sheet Width (mm)")
            else:
                spec.geometry_group_label = _("Finished Box Size")
                spec.length_field_label = _("Box Length (mm)")
                spec.width_field_label = _("Box Width (mm)")

    @api.depends("box_length_mm")
    def _compute_roll_length_m(self):
        for spec in self:
            spec.roll_length_m = float(spec.box_length_mm or 0.0) / 1000.0

    def _inverse_roll_length_m(self):
        for spec in self:
            spec.box_length_mm = float(spec.roll_length_m or 0.0) * 1000.0

    def _is_diecut_sheet_style(self):
        self.ensure_one()
        code = (self.style_id.code or "").strip() if self.style_id else ""
        family = (self.style_id.family or "").strip() if self.style_id else ""
        return code == "DIECUT_SHEET" or family == "diecut_sheet"

    @api.model
    def _context_record_id(self, *keys):
        for key in keys:
            value = self.env.context.get(key)
            try:
                value = int(value or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                return value
        return False

    @api.model
    def _domain_if_field(self, model, field_name, operator, value):
        return [(field_name, operator, value)] if field_name in model._fields else []

    @api.model
    def _default_plant_id(self):
        context_plant_id = self._context_record_id(
            "default_plant_id",
            "plant_id",
            "corr_plant_param_id",
        )
        if context_plant_id:
            return context_plant_id

        Plant = self.env["corr.plant.param"].sudo()
        domain = self._domain_if_field(Plant, "active", "=", True)
        company_id = self.env.company.id
        if "company_id" in Plant._fields:
            domain += ["|", ("company_id", "=", False), ("company_id", "=", company_id)]

        plants = Plant.search(domain, order="id asc")
        if not plants:
            return False

        for default_field in ("is_default", "default", "is_primary"):
            if default_field in Plant._fields:
                default_plant = plants.filtered(lambda plant: bool(plant[default_field]))[:1]
                if default_plant:
                    return default_plant.id
        if len(plants) == 1:
            return plants.id
        return plants[:1].id

    @api.model
    def _default_surface_profile_id(self, plant_id=False):
        context_profile_id = self._context_record_id(
            "default_surface_profile_id",
            "surface_profile_id",
            "corr_surface_profile_id",
        )
        if context_profile_id:
            return context_profile_id

        Profile = self.env["corr.engine.surface.profile"].sudo()
        domain = self._domain_if_field(Profile, "active", "=", True)
        company_id = self.env.company.id
        if "company_id" in Profile._fields:
            domain += ["|", ("company_id", "=", False), ("company_id", "=", company_id)]
        plant_id = plant_id or self._context_record_id("default_plant_id", "plant_id", "corr_plant_param_id")
        if plant_id and "plant_id" in Profile._fields:
            domain += ["|", ("plant_id", "=", False), ("plant_id", "=", plant_id)]

        profiles = Profile.search(domain, order="is_primary desc, id asc" if "is_primary" in Profile._fields else "id asc")
        if not profiles:
            return False
        primary = profiles.filtered(lambda profile: bool(getattr(profile, "is_primary", False)))[:1]
        return (primary or profiles[:1]).id

    def _surface_profile_copy_vals(self, profile):
        if not profile:
            return {
                "surface_printing_required": False,
                "surface_print_process": False,
                "surface_print_colors": 0,
                "surface_print_coverage_percent": 0.0,
                "surface_top_sheet_mode": "none",
                "surface_top_sheet_product_id": False,
                "surface_top_sheet_roll_code": False,
                "surface_lamination_required": False,
                "surface_lamination_type_id": False,
                "surface_lamination_side": False,
            }
        return {
            "surface_printing_required": bool(profile.printing_required),
            "surface_print_process": profile.print_process or False,
            "surface_print_colors": int(profile.print_colors or 0),
            "surface_print_coverage_percent": float(profile.print_coverage_percent or 0.0),
            "surface_top_sheet_mode": profile.top_sheet_mode or "none",
            "surface_top_sheet_product_id": profile.top_sheet_product_id.id or False,
            "surface_top_sheet_roll_code": profile.top_sheet_roll_code or False,
            "surface_lamination_required": bool(profile.lamination_required),
            "surface_lamination_type_id": profile.lamination_type_id.id or False,
            "surface_lamination_side": profile.lamination_side or False,
        }

    @api.onchange("surface_profile_id")
    def _onchange_surface_profile_id_copy_values(self):
        for spec in self:
            vals = spec._surface_profile_copy_vals(spec.surface_profile_id)
            for field_name, value in vals.items():
                setattr(spec, field_name, value)

    def action_reload_surface_profile(self):
        for spec in self:
            if spec.surface_profile_id:
                spec.write(spec._surface_profile_copy_vals(spec.surface_profile_id))
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_refresh_multi_piece_split_suggestions(self):
        for spec in self:
            target = spec.with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            )
            target.show_split_alternatives = True
            target._refresh_multi_piece_split_suggestions(message_mode="manual")
        return False

    def action_show_split_alternatives(self):
        for spec in self:
            target = spec.with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            )
            target.show_split_alternatives = True
            if spec._layout_planning_inputs_ready() and (
                not spec.layout_planning_calculated
                or not spec.full_blank_layout_option_ids
            ):
                target._refresh_full_blank_layout_options()
            target._refresh_multi_piece_split_suggestions(message_mode="manual")
        return False

    def action_refresh_layout_planning_options(self):
        for spec in self:
            spec._validate_board_structure_lines()
            layout_spec = spec.with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            )
            if not spec._layout_planning_inputs_ready():
                layout_spec.write(
                    {
                        "layout_planning_calculated": False,
                        "split_suggestions_calculated": False,
                        "layout_planning_message": _(
                            "Enter style, dimensions, plant, and board recipe to calculate layout options."
                        ),
                        "layout_planning_diagnostics_html": False,
                        "multi_piece_split_suggestion_message": False,
                        "layout_planning_cache_version": LAYOUT_PLANNING_CACHE_VERSION,
                        "full_blank_layout_option_ids": [(5, 0, 0)],
                        "multi_piece_split_suggestion_ids": [(5, 0, 0)],
                    }
                )
                continue
            layout_spec._refresh_full_blank_layout_options()
            if spec.full_blank_layout_option_ids and not spec.multi_piece_enabled:
                spec.multi_piece_split_suggestion_ids.unlink()
                layout_spec.write(
                    {
                        "split_suggestions_calculated": False,
                        "multi_piece_split_suggestion_message": False,
                        "show_split_alternatives": False,
                    }
                )
            elif spec.multi_piece_enabled or not spec.full_blank_layout_option_ids:
                layout_spec._refresh_multi_piece_split_suggestions(
                    message_mode="no_full_blank"
                    if not spec.full_blank_layout_option_ids
                    else "manual"
                )
            layout_spec.write(
                {"layout_planning_cache_version": LAYOUT_PLANNING_CACHE_VERSION}
            )
        return False

    def _refresh_full_blank_layout_options(self):
        self.ensure_one()
        spec = self.with_context(
            skip_board_recipe_snapshot=True,
            skip_layout_auto_refresh=True,
        )
        spec.full_blank_layout_option_ids.unlink()
        spec.layout_planning_calculated = False
        if not spec.style_id:
            spec.layout_planning_message = _(
                "Enter style and box/sheet dimensions to calculate layout options."
            )
            return False

        plant = spec.plant_id or self.env["corr.plant.param"].sudo().search([], limit=1, order="id asc")
        if not plant:
            spec.layout_planning_message = _("No plant is available.")
            return False

        wizard = self.env["corr.engine_v2.debug.wizard"].create(
            {
                "v2_spec_id": spec.id,
                "plant_id": plant.id,
                "order_qty": 1.0,
                "selected_slitting_options_json": spec._selected_layout_map_json(),
            }
        )
        try:
            wizard.with_context(
                allow_stale_accepted_split_for_planning=True,
                skip_v2_spec_multi_piece_sync=True,
            ).action_run_debug()
        except Exception as exc:
            spec.layout_planning_message = _("Layout options could not be calculated: %s") % exc
            return False

        commands = [(5, 0, 0)]
        for line in wizard.slitting_option_line_ids.sorted(
            key=lambda item: (item.stage_sequence, item.option_rank or 9999, item.id)
        ):
            commands.append(
                (
                    0,
                    0,
                    {
                        "stage_sequence": line.stage_sequence,
                        "stage_code": line.stage_code,
                        "stage_name": line.stage_name,
                        "stage_identity": line.stage_identity,
                        "option_key": line.option_key,
                        "option_rank": line.option_rank,
                        "up_count": line.up_count,
                        "sheet_length_mm": line.final_sheet_length_mm,
                        "sheet_width_mm": line.final_sheet_width_mm,
                        "orientation": line.orientation_used,
                        "is_preferred": line.is_preferred,
                        "is_selected": line.is_selected,
                        "reason": line.matched_workcenter_name or "",
                        "option_payload_json": line.option_payload_json,
                    },
                )
            )
        if len(commands) == 1 and not spec.routing_profile_id:
            commands.extend(spec._preliminary_full_blank_layout_option_commands())
        spec.full_blank_layout_option_ids = commands
        spec.layout_planning_message = (
            spec._layout_feasible_message()
            if len(commands) > 1
            else spec._layout_block_message_from_wizard(wizard)
        )
        spec.layout_planning_diagnostics_html = spec._layout_diagnostics_html_from_wizard(wizard)
        spec.layout_planning_calculated = True
        spec.layout_planning_cache_version = LAYOUT_PLANNING_CACHE_VERSION
        selection_warning = spec._sync_selected_layout_from_option_lines(
            force_required_ups=True
        )
        if selection_warning:
            base_message = spec.layout_planning_message or ""
            spec.layout_planning_message = (
                ("%s\n%s" % (base_message, selection_warning)).strip()
                if base_message
                else selection_warning
            )
        return True

    def _preliminary_full_blank_layout_option_commands(self):
        self.ensure_one()
        length_mm, width_mm = self._resolved_preliminary_sheet_dimensions()
        if length_mm <= 0.0 or width_mm <= 0.0:
            return []
        payload = {
            "rank": 1,
            "up_count": 1,
            "final_sheet_length_mm": length_mm,
            "final_sheet_width_mm": width_mm,
            "orientation_used": "direct",
            "rotated_used": False,
            "feasible": True,
            "planning_mode": "preliminary_full_blank_without_route",
            "rejection_reasons": [],
        }
        return [
            (
                0,
                0,
                {
                    "stage_sequence": 10,
                    "stage_code": "preliminary",
                    "stage_name": _("Preliminary Layout"),
                    "stage_identity": "preliminary_layout_without_route",
                    "option_key": "preliminary:1:%s:%s" % (length_mm, width_mm),
                    "option_rank": 1,
                    "up_count": 1,
                    "sheet_length_mm": length_mm,
                    "sheet_width_mm": width_mm,
                    "orientation": "direct",
                    "is_preferred": True,
                    "is_selected": not bool(self.selected_layout_option_json),
                    "reason": _("No routing profile selected"),
                    "option_payload_json": json.dumps(payload, sort_keys=True),
                },
            )
        ]

    def _resolved_preliminary_sheet_dimensions(self):
        self.ensure_one()
        try:
            from odoo.addons.corr_engine_v2.engine_v2 import compute as engine_v2_compute
            from odoo.addons.corr_engine_v2.plant_adapter import build_plant_profile
            from odoo.addons.corr_engine_v2.spec_adapter import build_engine_spec_input
        except Exception:
            engine_v2_compute = None
            build_plant_profile = None
            build_engine_spec_input = None

        if engine_v2_compute and build_plant_profile and build_engine_spec_input and self.plant_id:
            try:
                result = engine_v2_compute(
                    build_engine_spec_input(self, sfg_mode="staged"),
                    build_plant_profile(self.plant_id),
                    options={
                        "order_qty": 1.0,
                        "allow_infeasible_derived_sheet_preview": True,
                    },
                )
                extra = dict(getattr(getattr(result, "metrics", None), "extra", {}) or {})
                layout = dict(extra.get("layout") or {})
                geometry = dict(extra.get("geometry") or {})
                length_mm = float(
                    layout.get("sheet_length_mm")
                    or geometry.get("blank_length_mm")
                    or 0.0
                )
                width_mm = float(
                    layout.get("sheet_width_mm")
                    or geometry.get("blank_width_mm")
                    or 0.0
                )
                if length_mm > 0.0 and width_mm > 0.0:
                    return length_mm, width_mm
            except Exception:
                pass

        code = (self.style_id.code or "").strip() if self.style_id else ""
        family = (self.style_id.family or "").strip() if self.style_id else ""
        if code in ("0110", "DIECUT_SHEET") or family in ("sheet", "diecut_sheet"):
            return float(self.box_length_mm or 0.0), float(self.box_width_mm or 0.0)
        if code == "0100" or family == "roll":
            return float(self.box_length_mm or 0.0), float(self.box_width_mm or 0.0)
        return float(self.manual_sheet_length_mm or 0.0), float(self.manual_sheet_width_mm or 0.0)

    def _layout_feasible_message(self):
        self.ensure_one()
        if not self.routing_profile_id:
            return _(
                "Preliminary layout only. Final feasibility depends on selected route/workcenters."
            )
        return _("Full blank layout is feasible.")

    def _layout_diagnostics_html_from_wizard(self, wizard):
        try:
            diagnostics = json.loads(wizard.layout_options_diagnostics_json or "{}")
        except Exception:
            diagnostics = {}
        stages = list(
            diagnostics.get("displayed_stage_details")
            or diagnostics.get("all_stage_details")
            or []
        )
        if not stages:
            return False

        def _fmt(value):
            try:
                amount = float(value or 0.0)
            except Exception:
                amount = 0.0
            if abs(amount - round(amount)) <= 1e-6:
                return str(int(round(amount)))
            return "%.1f" % amount

        rows = []
        for stage in stages:
            stage_label = stage.get("stage_name") or stage.get("stage_code") or "-"
            checks = list(stage.get("option_checks") or [])
            if not checks:
                rows.append(
                    "<tr><td>%s</td><td>-</td><td>%s x %s mm</td><td>-</td><td>%s</td></tr>"
                    % (
                        escape(stage_label),
                        escape(_fmt(stage.get("blank_length_mm"))),
                        escape(_fmt(stage.get("blank_width_mm"))),
                        escape("; ".join(stage.get("rejection_reasons") or []) or stage.get("status") or "-"),
                    )
                )
                continue
            for check in checks:
                dim = (check.get("dimension_checks") or [{}])[0]
                reasons = "; ".join(check.get("rejection_reasons") or [])
                result = _("Pass") if check.get("status") == "feasible" else _("Fail")
                if reasons:
                    result = "%s: %s" % (result, reasons)
                width_limits = "%s-%s mm (%s-%s cm)" % (
                    _fmt(check.get("width_min_mm")) if check.get("width_min_mm") else "-",
                    _fmt(check.get("width_max_mm")) if check.get("width_max_mm") else "-",
                    _fmt(check.get("width_min_cm")) if check.get("width_min_cm") else "-",
                    _fmt(check.get("width_max_cm")) if check.get("width_max_cm") else "-",
                )
                length_limits = "%s-%s mm" % (
                    _fmt(check.get("length_min_mm")) if check.get("length_min_mm") else "-",
                    _fmt(check.get("length_max_mm")) if check.get("length_max_mm") else "-",
                )
                rows.append(
                    "<tr><td>%s</td><td>%s</td><td>%s x %s mm</td><td>%s<br/>%s</td><td>%s</td></tr>"
                    % (
                        escape(stage_label),
                        escape(check.get("workcenter_name") or "-"),
                        escape(_fmt(dim.get("length_mm") or stage.get("blank_length_mm"))),
                        escape(_fmt(dim.get("width_mm") or stage.get("blank_width_mm"))),
                        escape(width_limits),
                        escape(length_limits),
                        escape(result),
                    )
                )
        return Markup(
            "<div class='mt-2'><strong>%s</strong>"
            "<table class='table table-sm table-striped mt-1'>"
            "<thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr></thead>"
            "<tbody>%s</tbody></table></div>"
        ) % (
            escape(_("Stage-wise Full Blank Feasibility")),
            escape(_("Stage")),
            escape(_("Workcenter")),
            escape(_("Blank Checked")),
            escape(_("Limits")),
            escape(_("Result")),
            Markup("".join(rows)),
        )

    def _layout_block_message_from_wizard(self, wizard):
        self.ensure_one()
        fallback = _(
            "No feasible full blank layout option was found. Use split / multi-piece manufacturing if required."
        )
        try:
            diagnostics = json.loads(wizard.layout_options_diagnostics_json or "{}")
        except Exception:
            diagnostics = {}
        stages = list(
            diagnostics.get("displayed_stage_details")
            or diagnostics.get("all_stage_details")
            or []
        )
        for stage in stages:
            checks = list(stage.get("option_checks") or [])
            failed = next(
                (
                    check
                    for check in checks
                    if (check.get("status") or "").strip().lower() != "feasible"
                ),
                None,
            )
            if not failed:
                continue
            reasons = "; ".join(failed.get("rejection_reasons") or [])
            if not reasons:
                reasons = "; ".join(stage.get("rejection_reasons") or [])
            stage_name = stage.get("stage_name") or stage.get("stage_code") or _("selected route")
            workcenter = failed.get("workcenter_name") or _("selected workcenter")
            if reasons:
                return _(
                    "Blocked at %(stage)s (%(workcenter)s): %(reason)s"
                ) % {
                    "stage": stage_name,
                    "workcenter": workcenter,
                    "reason": reasons,
                }
            return _(
                "Full blank layout is not feasible at %(stage)s (%(workcenter)s). Split options are available below."
            ) % {"stage": stage_name, "workcenter": workcenter}
        return fallback

    def _selected_layout_map_json(self):
        self.ensure_one()
        if not self.selected_layout_stage_identity or not self.selected_layout_option_json:
            return ""
        try:
            option = json.loads(self.selected_layout_option_json or "{}")
        except Exception:
            option = {}
        if not option:
            return ""
        return json.dumps({self.selected_layout_stage_identity: option}, indent=2, sort_keys=True)

    def _apply_full_blank_layout_option(self, option):
        self.ensure_one()
        target = self.with_context(
            skip_board_recipe_snapshot=True,
            skip_layout_auto_refresh=True,
        )
        payload = option.option_payload_json or "{}"
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = {}
        if not parsed:
            raise UserError(_("Selected layout option is missing its planner payload. Refresh layout options first."))
        target.write(
            {
                "selected_layout_stage_identity": option.stage_identity or option.stage_code or "",
                "selected_layout_option_json": json.dumps(parsed, indent=2, sort_keys=True),
                "selected_layout_selection_mode": "manual",
                "multi_piece_split_stale": False,
            }
        )
        self.full_blank_layout_option_ids.write({"is_selected": False})
        option.is_selected = True
        target.layout_planning_message = _("Full blank layout is feasible.")
        target.layout_planning_calculated = True
        return True

    def action_clear_selected_layout_option(self):
        for spec in self:
            target = spec.with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            )
            target.write(
                {
                    "selected_layout_stage_identity": False,
                    "selected_layout_option_json": False,
                    "selected_layout_selection_mode": False,
                    "multi_piece_split_stale": False,
                }
            )
            spec.full_blank_layout_option_ids.write({"is_selected": False})
            warning = spec._sync_selected_layout_from_option_lines(
                force_required_ups=True
            )
            base_message = (
                spec._layout_feasible_message()
                if spec.full_blank_layout_option_ids
                else spec.layout_planning_message
            )
            target.layout_planning_message = (
                ("%s\n%s" % (base_message, warning)).strip()
                if warning and base_message
                else warning or base_message
            )
            target.layout_planning_calculated = bool(spec.full_blank_layout_option_ids)
        return False

    def _layout_options_need_refresh(self):
        self.ensure_one()
        if not self._layout_planning_inputs_ready():
            return False
        if not self.layout_planning_calculated:
            return True
        if not self.full_blank_layout_option_ids:
            return True
        return (self.layout_planning_cache_version or "") != LAYOUT_PLANNING_CACHE_VERSION

    def _sync_selected_layout_from_option_lines(self, force_required_ups=False):
        for spec in self:
            target = spec.with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            )
            options = spec.full_blank_layout_option_ids.sorted(
                lambda item: (item.option_rank or 9999, item.id or 0)
            )
            warning = False
            selected = self.env["corr.engine.v2.spec.layout.option"]
            manual_selected = self.env["corr.engine.v2.spec.layout.option"]
            requested_ups = int(spec.forced_ups or 0)
            if (
                not force_required_ups
                and
                spec.selected_layout_selection_mode == "manual"
                and spec.selected_layout_stage_identity
                and spec.selected_layout_option_json
            ):
                manual_selected = options.filtered(
                    lambda item: (
                        (item.stage_identity or item.stage_code or "")
                        == (spec.selected_layout_stage_identity or "")
                        and item._payload_signature(spec.selected_layout_option_json)
                        == item._payload_signature(item.option_payload_json)
                    )
                )[:1]
            if manual_selected:
                selected = manual_selected
            else:
                if requested_ups > 0:
                    requested_match = options.filtered(
                        lambda item: int(item.up_count or 0) == requested_ups
                    )[:1]
                    if requested_match:
                        selected = requested_match
                    else:
                        preferred_fallback = options.filtered("is_preferred")[:1]
                        fallback_ups = int(preferred_fallback.up_count or 0) if preferred_fallback else 0
                        warning = _(
                            "Required Slitting UPS %(required)s is not feasible. Using preferred feasible layout UPS %(fallback)s."
                        ) % {
                            "required": requested_ups,
                            "fallback": fallback_ups or 0,
                        }
                if not selected:
                    selected = options.filtered("is_preferred")[:1]

            options.write({"is_selected": False})
            if selected:
                selected.is_selected = True

            if not selected:
                target.write(
                    {
                        "selected_layout_stage_identity": False,
                        "selected_layout_option_json": False,
                        "selected_layout_selection_mode": False,
                        "multi_piece_split_stale": False,
                    }
                )
                return warning
            payload = selected.option_payload_json or "{}"
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = {}
            if not parsed:
                target.write(
                    {
                        "selected_layout_stage_identity": False,
                        "selected_layout_option_json": False,
                        "selected_layout_selection_mode": False,
                        "multi_piece_split_stale": False,
                    }
                )
                return warning
            selection_mode = "preferred"
            if manual_selected and selected.id == manual_selected.id:
                selection_mode = "manual"
            elif int(spec.forced_ups or 0) > 0 and int(selected.up_count or 0) == int(spec.forced_ups or 0):
                selection_mode = "required_ups"
            target.write(
                {
                    "selected_layout_stage_identity": selected.stage_identity
                    or selected.stage_code
                    or "",
                    "selected_layout_option_json": json.dumps(
                        parsed, indent=2, sort_keys=True
                    ),
                    "selected_layout_selection_mode": selection_mode,
                    "multi_piece_split_stale": False,
                }
            )
            return warning
        return False

    def _refresh_multi_piece_split_suggestions(self, message_mode=None):
        self.ensure_one()
        target = self.with_context(
            skip_board_recipe_snapshot=True,
            skip_layout_auto_refresh=True,
        )
        target.multi_piece_split_suggestion_ids.unlink()
        target.split_suggestions_calculated = False
        if not self.style_id:
            target.multi_piece_split_suggestion_message = _(
                "Enter style and box/sheet dimensions to calculate split suggestions."
            )
            return False

        plant = self.plant_id or self.env["corr.plant.param"].sudo().search([], limit=1, order="id asc")
        if not plant:
            target.multi_piece_split_suggestion_message = _("No plant is available.")
            return False

        try:
            from odoo.addons.corr_engine_v2.engine_v2 import compute as engine_v2_compute
            from odoo.addons.corr_engine_v2.engine_v2.stage_graph import (
                build_stage_graph as build_quantity_stage_graph,
            )
            from odoo.addons.corr_engine_v2.plant_adapter import build_plant_profile
            from odoo.addons.corr_engine_v2.spec_adapter import build_engine_spec_input
        except Exception as exc:
            raise UserError(_("Engine V2 is not available: %s") % exc) from exc

        try:
            spec_input = build_engine_spec_input(self, sfg_mode="staged")
            result = engine_v2_compute(
                spec_input,
                build_plant_profile(plant),
                options={
                    "order_qty": 1.0,
                    "allow_infeasible_derived_sheet_preview": True,
                },
            )
        except Exception:
            target.multi_piece_split_suggestion_message = _(
                "Enter style and box/sheet dimensions to calculate split suggestions."
            )
            return False

        blank_length, blank_width = self._blank_dimensions_from_engine_result(result)
        if blank_length <= 0.0 or blank_width <= 0.0:
            target.multi_piece_split_suggestion_message = _(
                "Enter style and box/sheet dimensions to calculate split suggestions."
            )
            return False

        source_vals = {}
        if (
            float(self.multi_piece_source_length_mm or 0.0) <= 0.0
            or abs(float(self.multi_piece_source_length_mm or 0.0) - blank_length) > 0.0001
        ):
            source_vals["multi_piece_source_length_mm"] = blank_length
        if (
            float(self.multi_piece_source_width_mm or 0.0) <= 0.0
            or abs(float(self.multi_piece_source_width_mm or 0.0) - blank_width) > 0.0001
        ):
            source_vals["multi_piece_source_width_mm"] = blank_width
        if source_vals:
            _logger.info(
                "Engine V2 layout refresh syncing multi-piece source spec_id=%s "
                "old_length=%s old_width=%s new_length=%s new_width=%s",
                self.id,
                self.multi_piece_source_length_mm,
                self.multi_piece_source_width_mm,
                blank_length,
                blank_width,
            )
            target.write(source_vals)
            self.invalidate_recordset(
                ["multi_piece_source_length_mm", "multi_piece_source_width_mm"]
            )
        target._sync_current_equal_multi_piece_layout_from_source(
            blank_length=blank_length,
            blank_width=blank_width,
        )

        wizard = self.env["corr.engine_v2.debug.wizard"].create(
            {"v2_spec_id": self.id, "plant_id": plant.id, "order_qty": 1.0}
        )
        wizard._load_equal_split_suggestion_lines(
            blank_length=blank_length,
            blank_width=blank_width,
            selected_axis=self.multi_piece_split_axis
            if self.multi_piece_source == "accepted_auto_split"
            else None,
            result=result,
            qgraph=build_quantity_stage_graph(result, 1.0),
            spec=self,
            plant_param=plant,
            v2_spec=self,
        )

        commands = [(5, 0, 0)]
        block_reason = ""
        if message_mode == "no_full_blank":
            block_reason = self.layout_planning_message or ""
        for line in wizard.auto_split_suggestion_line_ids.sorted(
            key=lambda item: (item.option_rank, item.id)
        ):
            split_signature = self._split_signature(
                line.split_axis,
                line.split_method or "equal",
                line.source_length_mm,
                line.source_width_mm,
                line.piece_a_size_mm,
                line.piece_b_size_mm,
            )
            split_axis = (line.split_axis or "width").strip().lower()
            if split_axis == "length":
                reduction_text = _(
                    "Split reduces length from %(source).0f mm to %(piece_a).0f/%(piece_b).0f mm."
                ) % {
                    "source": float(line.source_length_mm or 0.0),
                    "piece_a": float(line.piece_a_size_mm or 0.0),
                    "piece_b": float(line.piece_b_size_mm or 0.0),
                }
            else:
                reduction_text = _(
                    "Split reduces width from %(source).0f mm to %(piece_a).0f/%(piece_b).0f mm."
                ) % {
                    "source": float(line.source_width_mm or 0.0),
                    "piece_a": float(line.piece_a_size_mm or 0.0),
                    "piece_b": float(line.piece_b_size_mm or 0.0),
                }
            reason = line.reason or ""
            if block_reason:
                reason = "%s %s" % (block_reason, reduction_text)
            elif reduction_text:
                reason = "%s %s" % (reason, reduction_text) if reason else reduction_text
            commands.append(
                (
                    0,
                    0,
                    {
                        "option_rank": line.option_rank,
                        "split_axis": line.split_axis,
                        "split_mode": line.split_method or "equal",
                        "piece_a_size_mm": line.piece_a_size_mm,
                        "piece_b_size_mm": line.piece_b_size_mm,
                        "source_length_mm": line.source_length_mm,
                        "source_width_mm": line.source_width_mm,
                        "reason": reason,
                        "split_signature": split_signature,
                        "is_applied": bool(
                            self.multi_piece_enabled
                            and self.multi_piece_source == "accepted_auto_split"
                            and not self.multi_piece_split_stale
                            and self.accepted_split_signature
                            and self.accepted_split_signature == split_signature
                        ),
                    },
                )
            )
        target.multi_piece_split_suggestion_ids = commands
        if len(commands) > 1:
            if message_mode == "no_full_blank":
                target.multi_piece_split_suggestion_message = _(
                    "No feasible full blank layout was found. Split options are available below."
                )
            else:
                target.multi_piece_split_suggestion_message = _(
                    "Split options calculated from current blank geometry."
                )
        else:
            target.multi_piece_split_suggestion_message = _(
                "No feasible split suggestions were found for the current geometry."
            )
        target.split_suggestions_calculated = True
        return True

    def _sync_current_equal_multi_piece_layout_from_source(self, blank_length=None, blank_width=None):
        for spec in self:
            if not (
                spec.multi_piece_enabled
                and spec.multi_piece_source == "accepted_auto_split"
                and (spec.multi_piece_split_mode or "equal") == "equal"
            ):
                continue
            axis = (spec.multi_piece_split_axis or "width").strip().lower()
            pieces = spec.multi_piece_line_ids.sorted(lambda item: (item.sequence or 0, item.id or 0))
            if not pieces:
                continue
            piece_count = len(pieces)
            if piece_count <= 0:
                continue
            source_length = float(blank_length or spec.multi_piece_source_length_mm or 0.0)
            source_width = float(blank_width or spec.multi_piece_source_width_mm or 0.0)
            if source_length <= 0.0 or source_width <= 0.0:
                continue
            old_widths = [float(piece.piece_width_mm or 0.0) for piece in pieces]
            old_lengths = [float(piece.piece_length_mm or 0.0) for piece in pieces]
            if axis == "length":
                piece_length = source_length / float(piece_count)
                piece_width = source_width
            else:
                piece_length = source_length
                piece_width = source_width / float(piece_count)
            changed = False
            for piece in pieces:
                if (
                    abs(float(piece.piece_length_mm or 0.0) - piece_length) > 0.0001
                    or abs(float(piece.piece_width_mm or 0.0) - piece_width) > 0.0001
                ):
                    changed = True
                    piece.with_context(
                        skip_piece_change_mark=True,
                        skip_piece_sequence_normalize=True,
                    ).write(
                        {
                            "piece_length_mm": piece_length,
                            "piece_width_mm": piece_width,
                        }
                    )
            if not changed:
                continue
            signature = spec._split_signature(
                axis,
                spec.multi_piece_split_mode or "equal",
                source_length,
                source_width,
                piece_length if axis == "length" else piece_width,
                piece_length if axis == "length" else piece_width,
            )
            spec.with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            ).write(
                {
                    "accepted_split_signature": signature,
                    "multi_piece_split_stale": False,
                }
            )
            _logger.info(
                "Engine V2 layout refresh rebuilt equal multi-piece split spec_id=%s "
                "axis=%s source_length=%s source_width=%s old_lengths=%s old_widths=%s "
                "new_piece_length=%s new_piece_width=%s piece_count=%s",
                spec.id,
                axis,
                source_length,
                source_width,
                old_lengths,
                old_widths,
                piece_length,
                piece_width,
                piece_count,
            )
        return True

    def _blank_dimensions_from_engine_result(self, result):
        try:
            geometry = dict(
                (getattr(getattr(result, "metrics", None), "extra", {}) or {}).get("geometry") or {}
            )
        except Exception:
            geometry = {}
        return (
            float(geometry.get("blank_length_mm") or 0.0),
            float(geometry.get("blank_width_mm") or 0.0),
        )

    def _split_signature(self, axis, mode, source_length, source_width, piece_a_size, piece_b_size):
        payload = {
            "axis": (axis or "width").strip().lower(),
            "mode": (mode or "equal").strip().lower(),
            "source_length_mm": round(float(source_length or 0.0), 4),
            "source_width_mm": round(float(source_width or 0.0), 4),
            "piece_a_size_mm": round(float(piece_a_size or 0.0), 4),
            "piece_b_size_mm": round(float(piece_b_size or 0.0), 4),
        }
        return hashlib.sha1(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _apply_multi_piece_split_suggestion(self, suggestion):
        self.ensure_one()
        axis = (suggestion.split_axis or "width").strip().lower()
        source_length = float(suggestion.source_length_mm or 0.0)
        source_width = float(suggestion.source_width_mm or 0.0)
        piece_a_size = float(suggestion.piece_a_size_mm or 0.0)
        piece_b_size = float(suggestion.piece_b_size_mm or 0.0)
        if source_length <= 0.0 or source_width <= 0.0 or piece_a_size <= 0.0 or piece_b_size <= 0.0:
            raise UserError(_("Selected split suggestion is missing dimensions. Refresh suggestions first."))

        if axis == "length":
            piece_values = [
                self._multi_piece_piece_values(10, "A", "Piece A", piece_a_size, source_width),
                self._multi_piece_piece_values(20, "B", "Piece B", piece_b_size, source_width),
            ]
        else:
            piece_values = [
                self._multi_piece_piece_values(10, "A", "Piece A", source_length, piece_a_size),
                self._multi_piece_piece_values(20, "B", "Piece B", source_length, piece_b_size),
            ]

        self.write(
            {
                "multi_piece_enabled": True,
                "multi_piece_source": "accepted_auto_split",
                "multi_piece_strategy": "independent_routed_piece",
                "multi_piece_split_axis": axis,
                "multi_piece_split_mode": suggestion.split_mode or "equal",
                "multi_piece_source_length_mm": source_length,
                "multi_piece_source_width_mm": source_width,
                "accepted_split_signature": suggestion.split_signature
                or self._split_signature(
                    axis,
                    suggestion.split_mode or "equal",
                    source_length,
                    source_width,
                    piece_a_size,
                    piece_b_size,
                ),
                "multi_piece_merge_stage_key": self.multi_piece_merge_stage_key or "seaming",
                "multi_piece_split_after_stage_key": False,
                "multi_piece_split_stale": False,
                "multi_piece_line_ids": self._multi_piece_piece_commands(piece_values),
            }
        )
        self.multi_piece_split_suggestion_ids.write({"is_applied": False})
        suggestion.is_applied = True
        self.multi_piece_split_suggestion_message = _("Split suggestion applied to the spec.")
        return True

    def action_clear_accepted_split(self):
        for spec in self:
            spec.write(
                {
                    "multi_piece_enabled": False,
                    "multi_piece_source": "explicit_multi_piece_design",
                    "multi_piece_split_axis": "width",
                    "multi_piece_split_mode": "equal",
                    "multi_piece_source_length_mm": 0.0,
                    "multi_piece_source_width_mm": 0.0,
                    "accepted_split_signature": False,
                    "multi_piece_split_stale": False,
                    "multi_piece_line_ids": [(2, line.id, 0) for line in spec.multi_piece_line_ids.filtered(
                        lambda line: (line.piece_code or "").strip().upper() in ("A", "B")
                    )],
                }
            )
            spec.multi_piece_split_suggestion_ids.write({"is_applied": False})
            if spec._layout_planning_inputs_ready():
                spec.with_context(skip_layout_auto_refresh=True).action_refresh_layout_planning_options()
        return False

    def _multi_piece_piece_values(self, sequence, code, name, length, width):
        return {
            "sequence": sequence,
            "piece_code": code,
            "piece_name": name,
            "piece_length_mm": length,
            "piece_width_mm": width,
            "piece_qty_per_fg": 1.0,
        }

    def _multi_piece_piece_commands(self, piece_values):
        existing_by_code = {}
        duplicate_ids = []
        for line in self.multi_piece_line_ids:
            code = (line.piece_code or "").strip().upper()
            if code in ("A", "B"):
                if code not in existing_by_code:
                    existing_by_code[code] = line
                else:
                    duplicate_ids.append(line.id)
        commands = [(2, line_id, 0) for line_id in duplicate_ids]
        for values in piece_values:
            code = (values.get("piece_code") or "").strip().upper()
            existing = existing_by_code.get(code)
            commands.append((1, existing.id, values) if existing else (0, 0, values))
        return commands

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        if "plant_id" in fields_list and not vals.get("plant_id"):
            vals["plant_id"] = self._default_plant_id()

        surface_field_names = {
            "surface_printing_required",
            "surface_print_process",
            "surface_print_colors",
            "surface_print_coverage_percent",
            "surface_top_sheet_mode",
            "surface_top_sheet_product_id",
            "surface_top_sheet_roll_code",
            "surface_lamination_required",
            "surface_lamination_type_id",
            "surface_lamination_side",
            "joining_type",
            "use_plant_consumable_defaults",
            "enable_consumables",
            "enable_corrugation_adhesive",
            "enable_lamination_adhesive",
            "enable_printing_ink",
            "enable_lamination_film",
            "enable_stitching_wire",
            "enable_jointing_glue",
            "enable_jointing_tape",
        }
        if "surface_profile_id" in fields_list and not vals.get("surface_profile_id"):
            vals["surface_profile_id"] = self._default_surface_profile_id(vals.get("plant_id"))
        if vals.get("surface_profile_id"):
            profile = self.env["corr.engine.surface.profile"].browse(vals["surface_profile_id"])
            for field_name, value in self._surface_profile_copy_vals(profile).items():
                if field_name in fields_list and not vals.get(field_name):
                    vals[field_name] = value
        elif surface_field_names.intersection(fields_list) and not vals.get("surface_top_sheet_mode"):
            vals["surface_top_sheet_mode"] = "none"
        return vals

    def _style_param_value_from_default(self, param):
        text = param.default_value
        if text in (None, ""):
            text = ""
        text = str(text)
        vals = {}
        if param.param_type == "float":
            vals["value_float"] = float(text or 0.0)
        elif param.param_type == "integer":
            vals["value_int"] = int(float(text or 0))
        elif param.param_type == "boolean":
            vals["value_bool"] = text.strip().lower() in ("1", "true", "yes", "y")
        elif param.param_type == "selection":
            vals["value_selection"] = text
        else:
            vals["value_char"] = text
        return vals

    def _prepare_style_param_value_vals(self, param):
        vals = {
            "style_id": param.style_id.id,
            "param_id": param.id,
            "key": param.key,
            "unit": param.unit,
            "required": param.required,
        }
        vals.update(self._style_param_value_from_default(param))
        return vals

    def _sync_business_style_fields_from_lines(self):
        for spec in self:
            fg_line = spec.style_param_value_ids.filtered(
                lambda line: (line.key or (line.param_id.key if line.param_id else "")) == "fg_per_sheet"
            )[:1]
            if fg_line:
                spec.fg_per_sheet = max(1, int(fg_line.value_int or 1))

    def _sync_style_lines_from_business_fields(self):
        for spec in self:
            if not spec._is_diecut_sheet_style():
                continue
            fg_value = max(1, int(spec.fg_per_sheet or 1))
            fg_line = spec.style_param_value_ids.filtered(
                lambda line: (line.key or (line.param_id.key if line.param_id else "")) == "fg_per_sheet"
            )[:1]
            if fg_line:
                fg_line.value_int = fg_value

    def _style_param_value_commands_for_style(self, style):
        params = style.param_ids.filtered(lambda param: param.active).sorted(
            lambda param: (param.sequence or 0, param.id or 0)
        )
        return [(5, 0, 0)] + [
            (0, 0, self._prepare_style_param_value_vals(param))
            for param in params
        ]

    @api.onchange("style_id")
    def _onchange_style_id_load_params(self):
        for spec in self:
            if not spec.style_id:
                spec.style_param_value_ids = [(5, 0, 0)]
                continue
            spec.style_param_value_ids = spec._style_param_value_commands_for_style(
                spec.style_id
            )
            spec._sync_business_style_fields_from_lines()

    def action_reload_style_parameters(self):
        for spec in self:
            if spec.style_id:
                spec.style_param_value_ids = spec._style_param_value_commands_for_style(
                    spec.style_id
                )
                spec._sync_business_style_fields_from_lines()
            else:
                spec.style_param_value_ids = [(5, 0, 0)]
        return {"type": "ir.actions.client", "tag": "reload"}

    def _ensure_layout_planning_cache_current(self):
        if self.env.context.get("skip_layout_cache_check"):
            return
        stale_specs = self.filtered(
            lambda spec: (
                spec.layout_planning_calculated
                and (spec.layout_planning_cache_version or "") != LAYOUT_PLANNING_CACHE_VERSION
            )
        )
        for spec in stale_specs:
            if spec._layout_planning_inputs_ready():
                spec.with_context(
                    skip_layout_cache_check=True,
                    skip_layout_auto_refresh=True,
                ).action_refresh_layout_planning_options()
            else:
                spec.with_context(skip_layout_cache_check=True).write(
                    {
                        "layout_planning_calculated": False,
                        "split_suggestions_calculated": False,
                        "layout_planning_message": False,
                        "layout_planning_diagnostics_html": False,
                        "multi_piece_split_suggestion_message": False,
                        "layout_planning_cache_version": LAYOUT_PLANNING_CACHE_VERSION,
                        "full_blank_layout_option_ids": [(5, 0, 0)],
                        "multi_piece_split_suggestion_ids": [(5, 0, 0)],
                    }
                )

    def read(self, fields=None, load="_classic_read"):
        layout_fields = {
            "layout_planning_calculated",
            "layout_planning_message",
            "layout_planning_hint",
            "layout_planning_diagnostics_html",
            "full_blank_layout_option_ids",
            "multi_piece_split_suggestion_ids",
            "multi_piece_split_suggestion_message",
            "has_full_blank_layout_options",
            "split_suggestions_calculated",
            "has_split_suggestions",
        }
        if fields is None or layout_fields.intersection(fields):
            self._ensure_layout_planning_cache_current()
        return super().read(fields=fields, load=load)

    @api.model_create_multi
    def create(self, vals_list):
        surface_fields = {
            "surface_printing_required",
            "surface_print_process",
            "surface_print_colors",
            "surface_print_coverage_percent",
            "surface_top_sheet_mode",
            "surface_top_sheet_product_id",
            "surface_top_sheet_roll_code",
            "surface_lamination_required",
            "surface_lamination_type_id",
            "surface_lamination_side",
        }
        for vals in vals_list:
            if not vals.get("plant_id"):
                vals["plant_id"] = self._default_plant_id()
            if not vals.get("surface_profile_id"):
                vals["surface_profile_id"] = self._default_surface_profile_id(vals.get("plant_id"))
            if vals.get("surface_profile_id") and not surface_fields.intersection(vals):
                profile = self.env["corr.engine.surface.profile"].browse(vals["surface_profile_id"])
                vals.update(self._surface_profile_copy_vals(profile))
        specs = super().create(vals_list)
        for spec, vals in zip(specs, vals_list):
            if spec.board_recipe_id and not vals.get("stack_line_ids"):
                spec.with_context(
                    skip_layout_auto_refresh=True
                ).write(spec._board_recipe_snapshot_write_vals(spec.board_recipe_id))
            if spec.style_id and not vals.get("style_param_value_ids"):
                commands = spec._style_param_value_commands_for_style(spec.style_id)
                if len(commands) > 1:
                    spec.style_param_value_ids = commands
            if spec._is_diecut_sheet_style():
                spec._sync_style_lines_from_business_fields()
        if not self.env.context.get("skip_layout_auto_refresh"):
            ready_specs = specs.filtered(lambda spec: spec._layout_planning_inputs_ready())
            if ready_specs:
                ready_specs.with_context(
                    skip_layout_auto_refresh=True
                ).action_refresh_layout_planning_options()
        return specs

    def write(self, vals):
        vals = dict(vals)
        normalize_stack_lines_after_write = (
            "stack_line_ids" in vals
            and not self.env.context.get("skip_board_line_sequence_normalize")
        )
        if (
            "stack_line_ids" in vals
            and "board_recipe_id" not in vals
            and not self.env.context.get("skip_board_recipe_snapshot")
        ):
            vals.setdefault("is_board_customized", True)
        if (
            "board_recipe_id" in vals
            and vals.get("board_recipe_id")
            and not self.env.context.get("skip_board_recipe_snapshot")
        ):
            recipe = self.env["corr.engine.board.recipe"].browse(vals["board_recipe_id"])
            if recipe:
                vals["stack_line_ids"] = self[:1]._board_recipe_snapshot_commands(recipe)
                vals["is_board_customized"] = False

        geometry_fields = {
            "style_id",
            "plant_id",
            "board_recipe_id",
            "layout_policy_id",
            "routing_profile_id",
            "forced_ups",
            "ups_enforcement",
            "box_length_mm",
            "box_width_mm",
            "box_height_mm",
            "manual_sheet_length_mm",
            "manual_sheet_width_mm",
            "fg_per_sheet",
            "glue_flap_override_mm",
            "stack_line_ids",
            "surface_profile_id",
            "surface_printing_required",
            "surface_print_process",
            "surface_print_colors",
            "surface_print_coverage_percent",
            "surface_top_sheet_mode",
            "surface_top_sheet_product_id",
            "surface_top_sheet_roll_code",
            "surface_lamination_required",
            "surface_lamination_type_id",
            "surface_lamination_side",
        }
        mark_split_stale = (
            geometry_fields.intersection(vals)
            and "multi_piece_split_stale" not in vals
            and any(
                (
                    spec.multi_piece_enabled
                    and spec.multi_piece_source == "accepted_auto_split"
                )
                or bool(spec.selected_layout_option_json)
                for spec in self
            )
        )
        reset_layout_planning = (
            bool(geometry_fields.intersection(vals))
            and "layout_planning_calculated" not in vals
            and "split_suggestions_calculated" not in vals
        )
        clear_selected_layout = bool(
            {
                "style_id",
                "plant_id",
                "board_recipe_id",
                "layout_policy_id",
                "routing_profile_id",
                "box_length_mm",
                "box_width_mm",
                "box_height_mm",
                "manual_sheet_length_mm",
                "manual_sheet_width_mm",
                "fg_per_sheet",
                "glue_flap_override_mm",
                "stack_line_ids",
                "surface_profile_id",
                "surface_printing_required",
                "surface_print_process",
                "surface_print_colors",
                "surface_print_coverage_percent",
                "surface_top_sheet_mode",
                "surface_top_sheet_product_id",
                "surface_top_sheet_roll_code",
                "surface_lamination_required",
                "surface_lamination_type_id",
                "surface_lamination_side",
                "joining_type",
                "use_plant_consumable_defaults",
                "enable_consumables",
                "enable_corrugation_adhesive",
                "enable_lamination_adhesive",
                "enable_printing_ink",
                "enable_lamination_film",
                "enable_stitching_wire",
                "enable_jointing_glue",
                "enable_jointing_tape",
            }.intersection(vals)
        )
        if clear_selected_layout:
            vals.setdefault("selected_layout_stage_identity", False)
            vals.setdefault("selected_layout_option_json", False)
            vals.setdefault("selected_layout_selection_mode", False)
        res = super().write(vals)
        if "style_id" in vals and "style_param_value_ids" not in vals:
            for spec in self:
                if spec.style_id:
                    spec.style_param_value_ids = spec._style_param_value_commands_for_style(
                        spec.style_id
                    )
                else:
                    spec.style_param_value_ids = [(5, 0, 0)]
                if "fg_per_sheet" in vals and spec._is_diecut_sheet_style():
                    spec._sync_style_lines_from_business_fields()
                else:
                    spec._sync_business_style_fields_from_lines()
        if "fg_per_sheet" in vals and "style_id" not in vals:
            self._sync_style_lines_from_business_fields()
        if normalize_stack_lines_after_write:
            self._normalize_stack_line_sequences()
        if not self.env.context.get("skip_board_recipe_snapshot"):
            self._ensure_recipe_snapshot_current()
        if mark_split_stale:
            self.filtered(
                lambda spec: (
                    spec.multi_piece_enabled
                    and spec.multi_piece_source == "accepted_auto_split"
                )
                or bool(spec.selected_layout_option_json)
            ).with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            ).write({"multi_piece_split_stale": True})
        if reset_layout_planning:
            self.with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            ).write(
                {
                    "layout_planning_calculated": False,
                    "split_suggestions_calculated": False,
                    "layout_planning_message": False,
                    "layout_planning_diagnostics_html": False,
                    "multi_piece_split_suggestion_message": False,
                    "layout_planning_cache_version": False,
                }
            )
            if not self.env.context.get("skip_layout_auto_refresh"):
                ready_specs = self.filtered(lambda spec: spec._layout_planning_inputs_ready())
                if ready_specs:
                    ready_specs.with_context(
                        skip_board_recipe_snapshot=True,
                        skip_layout_auto_refresh=True,
                    ).action_refresh_layout_planning_options()
        return res

    def _style_param_line_value(self, line):
        param_type = line.param_id.param_type or "text"
        if param_type == "float":
            return float(line.value_float or 0.0)
        if param_type == "integer":
            return int(line.value_int or 0)
        if param_type == "boolean":
            return bool(line.value_bool)
        if param_type == "selection":
            return line.value_selection or ""
        return line.value_char or ""

    def validate_style_parameters(self):
        for spec in self:
            for line in spec.style_param_value_ids:
                line._validate_value()
        return True

    def _validate_board_structure_lines(self):
        for spec in self:
            ordered_lines = spec.stack_line_ids.sorted(lambda item: (item.sequence or 0, item.id or 0))
            for index, line in enumerate(ordered_lines, start=1):
                line._validate_existing_business_fields(row_number=index)
        return True

    def collect_style_params(self):
        self.ensure_one()
        self.validate_style_parameters()
        params = {}
        for line in self.style_param_value_ids:
            if not line.active:
                continue
            key = line.key or (line.param_id.key if line.param_id else "")
            if not key:
                continue
            params[key] = self._style_param_line_value(line)
        if self._is_diecut_sheet_style():
            params["fg_per_sheet"] = max(1, int(self.fg_per_sheet or 1))
        return params

    @api.constrains("fg_per_sheet", "style_id")
    def _check_fg_per_sheet(self):
        for spec in self:
            if spec._is_diecut_sheet_style() and int(spec.fg_per_sheet or 0) < 1:
                raise ValidationError(_("FG per Sheet must be at least 1."))

    def action_apply_board_recipe(self):
        """Copy layers from the selected board recipe into spec board lines.

        - Existing spec lines are replaced with recipe layers.
        - The board returns to recipe-controlled/read-only mode.
        - Recipe changes later do not affect existing specs automatically.
        """

        for spec in self:
            recipe = spec.board_recipe_id
            if not recipe:
                continue

            spec.write(spec._board_recipe_snapshot_write_vals(recipe))
        return False

    def action_customize_board_structure(self):
        for spec in self:
            spec.stack_line_ids.write(
                {
                    "source_type": "manual",
                    "source_recipe_line_id": False,
                }
            )
        self.with_context(skip_board_recipe_snapshot=True).write(
            {
                "board_recipe_id": False,
                "is_board_customized": True,
            }
        )
        return False

    def action_duplicate_last_stack_line(self):
        Layer = self.env["corr.engine.v2.spec.layer"]
        for spec in self:
            lines = spec.stack_line_ids.sorted(lambda line: (line.sequence or 0, line.id or 0))
            if not lines:
                continue
            source = lines[-1]
            Layer.create(
                {
                    "spec_id": spec.id,
                    "sequence": int(source.sequence or 0) + 10,
                    "role": source.role,
                    "gsm": source.gsm,
                    "bf": source.bf,
                    "color": source.color,
                    "flute_type_id": source.flute_type_id.id,
                    "material_product_id": source.material_product_id.id,
                    "material_template_id": source.material_template_id.id,
                    "gsm_value_id": source.gsm_value_id.id,
                    "bf_value_id": source.bf_value_id.id,
                    "color_value_id": source.color_value_id.id,
                    "source_type": "manual",
                    "source_recipe_line_id": False,
                }
            )
            spec.is_board_customized = True
        return {"type": "ir.actions.client", "tag": "reload"}

    @api.onchange("board_recipe_id")
    def _onchange_board_recipe_id(self):
        for spec in self:
            recipe = spec.board_recipe_id
            if not recipe:
                if not spec.stack_line_ids:
                    spec.is_board_customized = True
                continue
            spec.update(
                {
                    "stack_line_ids": spec._board_recipe_snapshot_commands(recipe),
                    "is_board_customized": False,
                }
            )

    def _board_recipe_snapshot_commands(self, recipe):
        self.ensure_one()
        return [(5, 0, 0)] + [
            (0, 0, vals)
            for vals in self._prepare_recipe_layer_vals(recipe, include_spec=False)
        ]

    def _board_recipe_snapshot_write_vals(self, recipe):
        self.ensure_one()
        if not recipe:
            return {}
        return {
            "board_recipe_id": recipe.id,
            "stack_line_ids": self._board_recipe_snapshot_commands(recipe),
            "is_board_customized": False,
        }

    def _mark_manufacturing_inputs_changed(self):
        for spec in self:
            values = {
                "layout_planning_calculated": False,
                "split_suggestions_calculated": False,
                "layout_planning_message": False,
                "layout_planning_diagnostics_html": False,
                "multi_piece_split_suggestion_message": False,
                "layout_planning_cache_version": False,
                "last_preview_raw_json": False,
                "last_preview_generated_at": False,
                "last_preview_status": "pending",
                "last_preview_error": False,
            }
            if (
                (
                    spec.multi_piece_enabled
                    and spec.multi_piece_source == "accepted_auto_split"
                )
                or bool(spec.selected_layout_option_json)
            ):
                values["multi_piece_split_stale"] = True
            spec.with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            ).write(values)

    def _mark_board_structure_changed(self):
        self._mark_manufacturing_inputs_changed()
        self.with_context(
            skip_board_recipe_snapshot=True,
            skip_layout_auto_refresh=True,
        ).write({"is_board_customized": True})

    def _normalize_stack_line_sequences(self):
        Layer = self.env["corr.engine.v2.spec.layer"]
        for spec in self:
            lines = spec.stack_line_ids.sorted(lambda item: (item.sequence or 0, item.id or 0))
            for index, line in enumerate(lines, start=1):
                normalized_sequence = index * 10
                if int(line.sequence or 0) == normalized_sequence:
                    continue
                Layer.browse(line.id).with_context(
                    skip_board_line_change_mark=True,
                    skip_board_line_sequence_normalize=True,
                ).write({"sequence": normalized_sequence})

    def _normalize_multi_piece_line_sequences(self):
        Piece = self.env["corr.engine.v2.spec.piece"]
        for spec in self:
            pieces = spec.multi_piece_line_ids.sorted(lambda item: (item.sequence or 0, item.id or 0))
            for index, piece in enumerate(pieces, start=1):
                normalized_sequence = index * 10
                if int(piece.sequence or 0) == normalized_sequence:
                    continue
                Piece.browse(piece.id).with_context(
                    skip_piece_change_mark=True,
                    skip_piece_sequence_normalize=True,
                ).write({"sequence": normalized_sequence})

    def _board_line_signature_from_values(self, values):
        return {
            "sequence": int(values.get("sequence") or 0),
            "role": values.get("role") or "",
            "gsm": round(float(values.get("gsm") or 0.0), 4),
            "bf": round(float(values.get("bf") or 0.0), 4),
            "color": values.get("color") or "",
            "flute_type_id": int(values.get("flute_type_id") or 0),
            "material_product_id": int(values.get("material_product_id") or 0),
            "material_template_id": int(values.get("material_template_id") or 0),
            "gsm_value_id": int(values.get("gsm_value_id") or 0),
            "bf_value_id": int(values.get("bf_value_id") or 0),
            "color_value_id": int(values.get("color_value_id") or 0),
            "source_type": values.get("source_type") or "",
            "source_recipe_line_id": int(values.get("source_recipe_line_id") or 0),
        }

    def _board_recipe_snapshot_signature(self, recipe):
        self.ensure_one()
        return [
            self._board_line_signature_from_values(values)
            for values in self._prepare_recipe_layer_vals(recipe, include_spec=False)
        ]

    def _current_board_snapshot_signature(self):
        self.ensure_one()
        return [
            self._board_line_signature_from_values(
                {
                    "sequence": line.sequence,
                    "role": line.role,
                    "gsm": line.gsm,
                    "bf": line.bf,
                    "color": line.color,
                    "flute_type_id": line.flute_type_id.id,
                    "material_product_id": line.material_product_id.id,
                    "material_template_id": line.material_template_id.id,
                    "gsm_value_id": line.gsm_value_id.id,
                    "bf_value_id": line.bf_value_id.id,
                    "color_value_id": line.color_value_id.id,
                    "source_type": line.source_type,
                    "source_recipe_line_id": line.source_recipe_line_id.id,
                }
            )
            for line in self.stack_line_ids.sorted(lambda item: (item.sequence or 0, item.id or 0))
        ]

    def _ensure_recipe_snapshot_current(self):
        for spec in self:
            if not spec.board_recipe_id:
                continue
            current_signature = spec._current_board_snapshot_signature()
            recipe_signature = spec._board_recipe_snapshot_signature(spec.board_recipe_id)
            if current_signature == recipe_signature:
                if spec.is_board_customized:
                    spec.with_context(
                        skip_board_recipe_snapshot=True,
                        skip_layout_auto_refresh=True,
                    ).write({"is_board_customized": False})
                continue
            if spec.is_board_customized:
                continue
            spec.with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            ).write(spec._board_recipe_snapshot_write_vals(spec.board_recipe_id))

    def _prepare_recipe_layer_vals(self, recipe, include_spec=False):
        self.ensure_one()
        Value = self.env["product.attribute.value"]
        layer_model = self.env["corr.engine.v2.spec.layer"]
        attr_names = layer_model._material_attr_names()

        def _safe_id(record):
            return record.id if record and hasattr(record, "id") else False

        vals_list = []
        recipe_lines = recipe.line_ids.sorted(lambda item: (item.sequence or 0, item.id or 0))
        for line in recipe_lines:
            if not line.role:
                raise ValidationError(
                    _(
                        "Board Recipe '%(recipe)s' line %(sequence)s is missing Role. "
                        "Complete the recipe line before applying it to an Engine V2 spec."
                    )
                    % {
                        "recipe": recipe.display_name,
                        "sequence": int(line.sequence or 0),
                    }
                )
            material_product = line.material_product_id or line.resolved_product_id
            material_template = line.material_template_id or getattr(material_product, "product_tmpl_id", False)
            gsm_value = line.gsm_value_id
            bf_value = line.bf_value_id
            color_value = line.color_value_id
            if material_product and not (gsm_value and bf_value and color_value):
                for ptav in list(getattr(material_product, "product_template_attribute_value_ids", []) or []):
                    value = getattr(ptav, "product_attribute_value_id", False)
                    attr_name = str(getattr(getattr(value, "attribute_id", False), "name", "") or "").strip().lower()
                    if not value or not attr_name:
                        continue
                    if attr_name == attr_names["gsm"] and not gsm_value:
                        gsm_value = value
                    elif attr_name == attr_names["bf"] and not bf_value:
                        bf_value = value
                    elif attr_name == attr_names["color"] and not color_value:
                        color_value = value

            def _match_numeric_value(template, attr_name, numeric_value):
                if not template or not attr_name:
                    return False
                try:
                    numeric_value = float(numeric_value or 0.0)
                except Exception:
                    numeric_value = 0.0
                if numeric_value <= 0.0:
                    return False
                for value in layer_model._attribute_values_for_template(template, attr_name):
                    try:
                        candidate = layer_model._numeric_from_attribute_value(value)
                    except Exception:
                        candidate = 0.0
                    if abs(float(candidate or 0.0) - numeric_value) <= 1e-6:
                        return value
                return False

            def _match_text_value(template, attr_name, text_value):
                text = str(text_value or "").strip()
                if not template or not attr_name or not text:
                    return False
                text_norm = layer_model._normalized_material_text(text)
                for value in layer_model._attribute_values_for_template(template, attr_name):
                    candidate = str(value.name or "").strip()
                    candidate_norm = layer_model._normalized_material_text(candidate)
                    if candidate.strip().lower() == text.strip().lower():
                        return value
                    if text_norm and candidate_norm:
                        if text_norm == candidate_norm:
                            return value
                        if text_norm in candidate_norm or candidate_norm in text_norm:
                            return value
                    match = re.search(r"\(([A-Za-z0-9]+)\)", candidate)
                    if match and layer_model._normalized_material_text(match.group(1)) == text_norm:
                        return value
                return False

            if material_template:
                if not gsm_value:
                    gsm_value = _match_numeric_value(material_template, attr_names["gsm"], line.gsm)
                if not bf_value:
                    bf_value = _match_numeric_value(material_template, attr_names["bf"], line.bf)
                if not color_value:
                    color_value = _match_text_value(material_template, attr_names["color"], line.color)
            vals = {
                "sequence": line.sequence,
                "role": line.role,
                "gsm": line.gsm,
                "bf": line.bf,
                "color": line.color,
                "flute_type_id": _safe_id(line.flute_type_id),
                "material_product_id": _safe_id(material_product),
                "material_template_id": _safe_id(material_template),
                "gsm_value_id": _safe_id(gsm_value),
                "bf_value_id": _safe_id(bf_value),
                "color_value_id": _safe_id(color_value),
                "source_type": "recipe",
                "source_recipe_line_id": _safe_id(line),
            }
            if hasattr(line, "_snapshot_vals_from_attribute_values"):
                vals = line._snapshot_vals_from_attribute_values(vals)
            if vals.get("gsm_value_id") and not vals.get("gsm"):
                vals["gsm"] = layer_model._numeric_from_attribute_value(Value.browse(vals["gsm_value_id"]))
            if vals.get("bf_value_id") and not vals.get("bf"):
                vals["bf"] = layer_model._numeric_from_attribute_value(Value.browse(vals["bf_value_id"]))
            if vals.get("color_value_id") and not vals.get("color"):
                vals["color"] = Value.browse(vals["color_value_id"]).name or ""
            if vals.get("material_template_id"):
                missing = []
                if not vals.get("gsm") and not vals.get("gsm_value_id"):
                    missing.append("GSM")
                if not vals.get("bf") and not vals.get("bf_value_id"):
                    missing.append("BF")
                if not vals.get("color") and not vals.get("color_value_id"):
                    missing.append("Color")
                if missing:
                    raise ValidationError(
                        _(
                            "Board Recipe '%(recipe)s' line %(sequence)s is missing %(fields)s. "
                            "Complete the recipe line before applying it to an Engine V2 spec."
                        )
                        % {
                            "recipe": recipe.display_name,
                            "sequence": int(line.sequence or 0),
                            "fields": ", ".join(missing),
                        }
                    )
            if include_spec:
                vals["spec_id"] = self.id
            vals_list.append(vals)
        return vals_list

    @api.depends(
        "surface_profile_id",
        "surface_printing_required",
        "surface_print_process",
        "surface_print_colors",
        "surface_print_coverage_percent",
        "surface_top_sheet_mode",
        "surface_top_sheet_product_id",
        "surface_top_sheet_roll_code",
        "surface_lamination_required",
        "surface_lamination_type_id",
        "surface_lamination_side",
        "joining_type",
        "layout_policy_id",
        "layout_policy_id.allow_rotation",
        "layout_policy_id.preferred_ups",
        "layout_policy_id.max_ups",
        "layout_policy_id.layout_mode",
        "layout_policy_id.sheet_source",
        "layout_policy_id.trim_margin_mm",
        "layout_policy_id.gripper_margin_mm",
        "routing_profile_id",
        "routing_profile_id.stage_ids",
        "routing_profile_id.stage_ids.sequence",
        "routing_profile_id.stage_ids.stage_code",
        "routing_profile_id.stage_ids.stage_type",
        "routing_profile_id.stage_ids.waste_percent",
    )
    def _compute_profile_summary_html(self):
        for spec in self:
            spec.surface_profile_summary_html = spec._render_surface_profile_summary()
            spec.layout_policy_summary_html = spec._render_layout_policy_summary()
            spec.routing_profile_summary_html = spec._render_routing_profile_summary()

    def _summary_empty_html(self, message):
        return Markup(
            '<div class="text-muted o_corr_v2_profile_summary_empty">%s</div>'
        ) % escape(message)

    def _summary_table_html(self, rows):
        body = []
        for label, value in rows:
            body.append(
                "<tr>"
                '<th class="text-muted" style="width: 34%%; font-weight: 500; padding: 2px 12px 2px 0;">%s</th>'
                '<td style="padding: 2px 0;">%s</td>'
                "</tr>"
                % (escape(label), escape(value if value not in (None, "") else "-"))
            )
        return '<table class="table table-sm mb-2" style="width: 100%%;"><tbody>%s</tbody></table>' % "".join(body)

    def _selection_label(self, record, field_name):
        try:
            field = record._fields.get(field_name)
            value = getattr(record, field_name, False)
        except Exception:
            return "-"
        if not value:
            return "-"
        if field and getattr(field, "selection", None):
            selection = field.selection
            if callable(selection):
                selection = selection(record)
            return dict(selection).get(value, value)
        return value

    def _safe_record_display_name(self, record):
        if not record:
            return ""
        try:
            return record.display_name or record.name or ""
        except Exception:
            return ""

    def _safe_sorted_stage_lines(self, profile):
        try:
            lines = profile.stage_ids
        except Exception:
            return []
        if not lines:
            return []
        try:
            return lines.sorted(lambda line: (getattr(line, "sequence", 0) or 0, getattr(line, "id", 0) or 0))
        except Exception:
            try:
                return sorted(
                    list(lines),
                    key=lambda line: (getattr(line, "sequence", 0) or 0, getattr(line, "id", 0) or 0),
                )
            except Exception:
                return []

    def _yes_no(self, value):
        return _("Yes") if value else _("No")

    def _render_surface_profile_summary(self):
        self.ensure_one()
        profile = self.surface_profile_id
        if not profile:
            return self._summary_empty_html(_("No surface profile selected."))

        sections = [
            (
                _("Printing"),
                [
                    (_("Printing Required"), self._yes_no(self.surface_printing_required)),
                    (_("Print Method"), self._selection_label(self, "surface_print_process")),
                    (_("Print Colors"), self.surface_print_colors or 0),
                    (_("Coverage %"), self.surface_print_coverage_percent or 0.0),
                ],
            ),
            (
                _("Top Sheet"),
                [
                    (_("Top Sheet Mode"), self._selection_label(self, "surface_top_sheet_mode")),
                    (_("Top Sheet Product"), self._safe_record_display_name(self.surface_top_sheet_product_id) or "-"),
                    (_("Top Sheet Roll Code"), self.surface_top_sheet_roll_code or "-"),
                ],
            ),
            (
                _("Lamination"),
                [
                    (_("Lamination Enabled"), self._yes_no(self.surface_lamination_required)),
                    (_("Film Type"), self._safe_record_display_name(self.surface_lamination_type_id) or "-"),
                    (_("Lamination Side"), self._selection_label(self, "surface_lamination_side")),
                ],
            ),
        ]
        return self._render_profile_sections(self._safe_record_display_name(profile) or _("Surface Profile"), sections)

    def _render_layout_policy_summary(self):
        self.ensure_one()
        policy = self.layout_policy_id
        if not policy:
            return self._summary_empty_html(_("No layout policy selected."))

        sections = [
            (
                _("Layout Rules"),
                [
                    (_("Allow Rotation"), self._yes_no(policy.allow_rotation)),
                    (_("Preferred UPS"), policy.preferred_ups or "-"),
                    (_("Max UPS"), policy.max_ups or "-"),
                    (_("Optimization Strategy"), self._selection_label(policy, "layout_mode")),
                    (_("Sheet Source"), self._selection_label(policy, "sheet_source")),
                    (_("Trim Margin (mm)"), policy.trim_margin_mm or 0.0),
                    (_("Gripper Margin (mm)"), policy.gripper_margin_mm or 0.0),
                ],
            )
        ]
        return self._render_profile_sections(self._safe_record_display_name(policy) or _("Layout Policy"), sections)

    def _render_routing_profile_summary(self):
        self.ensure_one()
        profile = self.routing_profile_id
        if not profile:
            return self._summary_empty_html(_("No routing profile selected."))

        lines = self._safe_sorted_stage_lines(profile)
        if not lines:
            body = '<div class="text-muted">%s</div>' % escape(_("No routing stages configured."))
        else:
            rows = []
            rows.append(
                "<tr>"
                '<td style="width: 72px; padding: 2px 12px 2px 0;"></td>'
                '<td style="padding: 2px 12px 2px 0;">%s</td>'
                '<td class="text-muted" style="padding: 2px 0;">%s</td>'
                "</tr>"
                % (
                    escape(_("Joining Type")),
                    escape(self._selection_label(self, "joining_type") or _("None")),
                )
            )
            for line in lines:
                stage_name = (
                    self._safe_record_display_name(getattr(line, "stage_template_id", False))
                    or getattr(line, "stage_code", "")
                    or _("Unnamed Stage")
                )
                rows.append(
                    "<tr>"
                    '<td style="width: 72px; padding: 2px 12px 2px 0;">%s</td>'
                    '<td style="padding: 2px 12px 2px 0;">%s</td>'
                    '<td class="text-muted" style="padding: 2px 0;">%s</td>'
                    "</tr>"
                    % (
                        escape(line.sequence or 0),
                        escape(stage_name),
                        escape(self._selection_label(line, "stage_type")),
                    )
                )
            body = '<table class="table table-sm mb-0"><tbody>%s</tbody></table>' % "".join(rows)
        return Markup(
            '<div class="o_corr_v2_profile_summary">'
            '<div class="fw-bold mb-2">%s</div>'
            "%s"
            "</div>"
        ) % (escape(self._safe_record_display_name(profile) or _("Routing Profile")), Markup(body))

    def _render_profile_sections(self, title, sections):
        section_html = []
        for section_title, rows in sections:
            section_html.append(
                '<div class="fw-bold mt-2 mb-1">%s</div>%s'
                % (escape(section_title), self._summary_table_html(rows))
            )
        return Markup(
            '<div class="o_corr_v2_profile_summary">'
            '<div class="fw-bold mb-2">%s</div>'
            "%s"
            "</div>"
        ) % (escape(title), Markup("".join(section_html)))

    @api.depends(
        "routing_profile_id",
        "routing_profile_id.stage_ids",
        "routing_profile_id.stage_ids.sequence",
        "routing_profile_id.stage_ids.stage_code",
        "routing_profile_id.stage_ids.stage_type",
    )
    def _compute_routing_stage_summary_html(self):
        for spec in self:
            profile = spec.routing_profile_id
            if not profile:
                spec.routing_stage_summary_html = spec._summary_empty_html(
                    _("No routing profile selected.")
                )
                continue
            lines = spec._safe_sorted_stage_lines(profile)
            if not lines:
                spec.routing_stage_summary_html = spec._summary_empty_html(
                    _("No routing stages configured.")
                )
                continue
            items = []
            for line in lines:
                stage_name = (
                    spec._safe_record_display_name(getattr(line, "stage_template_id", False))
                    or getattr(line, "stage_code", "")
                    or _("Unnamed Stage")
                )
                items.append(
                    '<span class="badge rounded-pill text-bg-light me-1 mb-1">%s</span>'
                    % escape(stage_name)
                )
            spec.routing_stage_summary_html = Markup(
                '<div class="o_corr_v2_routing_stage_summary">%s</div>'
            ) % Markup("".join(items))

    def action_open_engine_v2_preview(self):
        """Open the Engine V2 preview wizard with preview data already rendered.

        The V2 spec is a manufacturing definition. Order quantity is kept on
        the transient preview wizard. The spec button creates the transient
        server-side and runs the preview immediately so users do not have to
        open an empty wizard and click Run a second time.
        """

        self.ensure_one()
        self._validate_board_structure_lines()
        self.validate_style_parameters()
        if self._layout_options_need_refresh():
            self.with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            ).action_refresh_layout_planning_options()
        wizard = self.env["corr.engine_v2.debug.wizard"].create(
            {
                "v2_spec_id": self.id,
                "plant_id": self.plant_id.id if self.plant_id else False,
                "order_qty": 1.0,
                "preview_backend": "stagecontext",
                "bom_backend": "stagecontext_shadow",
            }
        )
        try:
            wizard.action_run_debug()
        except Exception as exc:
            message = _("Preview auto-run failed: %s") % exc
            wizard.write(
                {
                    "preview_is_current": False,
                    "preview_warning": message,
                    "summary_html": Markup(
                        "<div class='alert alert-danger' role='alert'>%s</div>"
                    )
                    % escape(message),
                    "warnings_text": message,
                    "raw_json": False,
                }
            )

        action = self.env.ref(
            "corr_engine_v2.action_corr_engine_v2_debug_wizard",
            raise_if_not_found=False,
        )
        if action:
            result = action.read()[0]
        else:
            result = {
                "type": "ir.actions.act_window",
                "name": _("Engine V2 Debug Tool"),
                "res_model": "corr.engine_v2.debug.wizard",
                "view_mode": "form",
                "target": "new",
            }

        ctx = dict(self.env.context or {})
        ctx.update(
            {
                "default_v2_spec_id": self.id,
                "default_plant_id": self.plant_id.id if self.plant_id else False,
                "default_preview_backend": "stagecontext",
                "default_bom_backend": "stagecontext_shadow",
            }
        )
        result["context"] = ctx
        result["res_id"] = wizard.id
        result["target"] = "new"
        return result

    def store_last_preview_snapshot(self, *, raw_json=False, status="success", error=False):
        for spec in self:
            spec.write(
                {
                    "last_preview_raw_json": raw_json or False,
                    "last_preview_generated_at": fields.Datetime.now(),
                    "last_preview_status": status or "success",
                    "last_preview_error": error or False,
                }
            )


class CorrEngineV2SpecSplitSuggestion(models.Model):
    _name = "corr.engine.v2.spec.split.suggestion"
    _description = "Engine V2 Spec Split Suggestion"
    _order = "spec_id, option_rank, id"

    spec_id = fields.Many2one("corr.engine.v2.spec", required=True, ondelete="cascade")
    spec_multi_piece_enabled = fields.Boolean(
        related="spec_id.multi_piece_enabled",
        readonly=True,
    )
    option_rank = fields.Integer(string="Rank", default=10)
    split_axis = fields.Selection([("width", "Width"), ("length", "Length")], readonly=True)
    split_mode = fields.Char(string="Split Mode", readonly=True, default="equal")
    source_length_mm = fields.Float(string="Source Length (mm)", readonly=True)
    source_width_mm = fields.Float(string="Source Width (mm)", readonly=True)
    piece_a_size_mm = fields.Float(string="Piece A Size (mm)", readonly=True)
    piece_b_size_mm = fields.Float(string="Piece B Size (mm)", readonly=True)
    piece_a_length_mm = fields.Float(
        string="Piece A Length (mm)",
        compute="_compute_piece_dimensions",
    )
    piece_a_width_mm = fields.Float(
        string="Piece A Width (mm)",
        compute="_compute_piece_dimensions",
    )
    piece_b_length_mm = fields.Float(
        string="Piece B Length (mm)",
        compute="_compute_piece_dimensions",
    )
    piece_b_width_mm = fields.Float(
        string="Piece B Width (mm)",
        compute="_compute_piece_dimensions",
    )
    split_signature = fields.Char(string="Split Signature", readonly=True)
    status = fields.Char(string="Status", compute="_compute_status")
    reason = fields.Char(string="Reason", readonly=True)
    is_applied = fields.Boolean(string="Applied", readonly=True)
    spec_split_stale = fields.Boolean(
        related="spec_id.multi_piece_split_stale",
        readonly=True,
    )
    spec_has_accepted_split = fields.Boolean(
        string="Spec Has Accepted Split",
        compute="_compute_spec_has_accepted_split",
    )

    @api.depends(
        "split_axis",
        "source_length_mm",
        "source_width_mm",
        "piece_a_size_mm",
        "piece_b_size_mm",
    )
    def _compute_piece_dimensions(self):
        for suggestion in self:
            if suggestion.split_axis == "length":
                suggestion.piece_a_length_mm = suggestion.piece_a_size_mm
                suggestion.piece_a_width_mm = suggestion.source_width_mm
                suggestion.piece_b_length_mm = suggestion.piece_b_size_mm
                suggestion.piece_b_width_mm = suggestion.source_width_mm
            else:
                suggestion.piece_a_length_mm = suggestion.source_length_mm
                suggestion.piece_a_width_mm = suggestion.piece_a_size_mm
                suggestion.piece_b_length_mm = suggestion.source_length_mm
                suggestion.piece_b_width_mm = suggestion.piece_b_size_mm

    @api.depends("is_applied", "spec_split_stale", "spec_has_accepted_split")
    def _compute_status(self):
        for suggestion in self:
            if suggestion.is_applied:
                suggestion.status = _("Applied")
            elif suggestion.spec_split_stale and suggestion.spec_has_accepted_split:
                suggestion.status = _("Needs Reapply")
            else:
                suggestion.status = _("Feasible")

    @api.depends("spec_id.multi_piece_enabled", "spec_id.multi_piece_source")
    def _compute_spec_has_accepted_split(self):
        for suggestion in self:
            spec = suggestion.spec_id
            suggestion.spec_has_accepted_split = bool(
                spec
                and spec.multi_piece_enabled
                and spec.multi_piece_source == "accepted_auto_split"
            )

    action_state = fields.Char(
        string="Action",
        compute="_compute_action_state",
    )

    @api.depends("is_applied", "spec_split_stale", "spec_has_accepted_split")
    def _compute_action_state(self):
        for suggestion in self:
            if suggestion.is_applied:
                suggestion.action_state = _("Current")
            elif suggestion.spec_split_stale and suggestion.spec_has_accepted_split:
                suggestion.action_state = _("Reapply Split")
            else:
                suggestion.action_state = ""

    def action_apply_to_spec(self):
        self.ensure_one()
        self.spec_id._apply_multi_piece_split_suggestion(self)
        return False


class CorrEngineV2SpecLayoutOption(models.Model):
    _name = "corr.engine.v2.spec.layout.option"
    _description = "Engine V2 Spec Full Blank Layout Option"
    _order = "spec_id, stage_sequence, option_rank, id"

    spec_id = fields.Many2one("corr.engine.v2.spec", required=True, ondelete="cascade")
    stage_sequence = fields.Integer(string="Stage Sequence", readonly=True)
    stage_code = fields.Char(string="Stage Code", readonly=True)
    stage_name = fields.Char(string="Stage Name", readonly=True)
    stage_identity = fields.Char(string="Stage Identity", readonly=True)
    option_key = fields.Char(string="Option Key", readonly=True)
    option_rank = fields.Integer(string="Rank", readonly=True)
    up_count = fields.Integer(string="UPS", readonly=True)
    sheet_length_mm = fields.Float(string="Sheet Length (mm)", readonly=True)
    sheet_width_mm = fields.Float(string="Sheet Width (mm)", readonly=True)
    orientation = fields.Char(string="Orientation", readonly=True)
    is_preferred = fields.Boolean(string="Preferred", readonly=True)
    is_selected = fields.Boolean(string="Selected", readonly=True)
    is_current = fields.Boolean(string="Current", compute="_compute_current_state")
    status = fields.Char(string="Status", compute="_compute_current_state")
    action_state = fields.Char(string="Action", compute="_compute_current_state")
    reason = fields.Char(string="Reason", readonly=True)
    option_payload_json = fields.Text(string="Planner Payload", readonly=True)

    def _payload_signature(self, raw):
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            payload = {}
        return hashlib.sha1(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest() if payload else ""

    @api.depends(
        "spec_id.selected_layout_stage_identity",
        "spec_id.selected_layout_option_json",
        "spec_id.multi_piece_split_stale",
        "stage_identity",
        "stage_code",
        "option_payload_json",
        "is_selected",
    )
    def _compute_current_state(self):
        for option in self:
            spec = option.spec_id
            stage_identity = option.stage_identity or option.stage_code or ""
            selected_identity = spec.selected_layout_stage_identity if spec else ""
            current = bool(
                spec
                and not spec.multi_piece_split_stale
                and selected_identity
                and selected_identity == stage_identity
                and option._payload_signature(spec.selected_layout_option_json)
                == option._payload_signature(option.option_payload_json)
            )
            option.is_current = current
            option.status = _("Applied") if current else _("Feasible")
            option.action_state = _("Current") if current else ""

    def action_apply_to_spec(self):
        self.ensure_one()
        self.spec_id._apply_full_blank_layout_option(self)
        return False


class CorrEngineV2SpecStyleParamValue(models.Model):
    _name = "corr.engine.v2.spec.style.param.value"
    _description = "Engine V2 Spec Style Parameter Value"
    _order = "spec_id, param_id, id"

    spec_id = fields.Many2one(
        "corr.engine.v2.spec",
        string="Spec",
        required=True,
        ondelete="cascade",
    )
    style_id = fields.Many2one("corr.engine.style", string="Style", required=True)
    param_id = fields.Many2one(
        "corr.engine.style.param",
        string="Parameter",
        required=True,
        ondelete="restrict",
    )
    key = fields.Char(required=True)
    label = fields.Char(related="param_id.label", readonly=True)
    param_type = fields.Selection(related="param_id.param_type", readonly=True)
    value_float = fields.Float(string="Float Value")
    value_int = fields.Integer(string="Integer Value")
    value_bool = fields.Boolean(string="Boolean Value")
    value_char = fields.Char(string="Text Value")
    value_selection = fields.Char(string="Selection Value")
    display_value = fields.Char(
        string="Value",
        compute="_compute_display_value",
        inverse="_inverse_display_value",
    )
    sequence = fields.Integer(related="param_id.sequence", readonly=True)
    unit = fields.Char()
    required = fields.Boolean()
    min_value = fields.Float(related="param_id.min_value", readonly=True)
    max_value = fields.Float(related="param_id.max_value", readonly=True)
    help_text = fields.Text(related="param_id.help_text", readonly=True)
    active = fields.Boolean(related="param_id.active", readonly=True)

    @api.depends(
        "param_type",
        "value_float",
        "value_int",
        "value_bool",
        "value_char",
        "value_selection",
    )
    def _compute_display_value(self):
        for line in self:
            param_type = line.param_type or "text"
            if param_type == "float":
                line.display_value = ("%s" % (line.value_float or 0.0)).rstrip("0").rstrip(".")
            elif param_type == "integer":
                line.display_value = str(int(line.value_int or 0))
            elif param_type == "boolean":
                line.display_value = _("Yes") if line.value_bool else _("No")
            elif param_type == "selection":
                line.display_value = line.value_selection or ""
            else:
                line.display_value = line.value_char or ""

    def _inverse_display_value(self):
        for line in self:
            value = (line.display_value or "").strip()
            param_type = line.param_type or "text"
            if param_type == "float":
                line.value_float = float(value or 0.0)
            elif param_type == "integer":
                line.value_int = int(float(value or 0))
            elif param_type == "boolean":
                line.value_bool = value.lower() in ("1", "true", "yes", "y", "on")
            elif param_type == "selection":
                line.value_selection = value
            else:
                line.value_char = value

    @api.onchange("param_id")
    def _onchange_param_id_set_metadata(self):
        for line in self:
            param = line.param_id
            if not param:
                continue
            line.style_id = param.style_id
            line.key = param.key
            line.unit = param.unit
            line.required = param.required

    @api.model_create_multi
    def create(self, vals_list):
        Param = self.env["corr.engine.style.param"]
        for vals in vals_list:
            param_id = vals.get("param_id")
            if not param_id:
                continue
            param = Param.browse(param_id)
            if not vals.get("style_id"):
                vals["style_id"] = param.style_id.id
            vals.setdefault("key", param.key)
            vals.setdefault("unit", param.unit)
            vals.setdefault("required", param.required)
        return super().create(vals_list)

    def _value_for_validation(self):
        self.ensure_one()
        param_type = self.param_id.param_type or "text"
        if param_type == "float":
            return self.value_float
        if param_type == "integer":
            return self.value_int
        if param_type == "boolean":
            return self.value_bool
        if param_type == "selection":
            return self.value_selection
        return self.value_char

    def _validate_value(self):
        for line in self:
            if not line.active:
                continue
            label = line.label or line.key
            value = line._value_for_validation()
            param_type = line.param_id.param_type or "text"

            if line.required:
                if param_type in ("float", "integer"):
                    if value in (None, "") or float(value or 0.0) == 0.0:
                        raise ValidationError(_("%s is required for style %s.") % (label, line.style_id.code))
                elif param_type == "boolean":
                    if value is None:
                        raise ValidationError(_("%s is required for style %s.") % (label, line.style_id.code))
                elif not value:
                    raise ValidationError(_("%s is required for style %s.") % (label, line.style_id.code))

            if param_type in ("float", "integer"):
                numeric = float(value or 0.0)
                if line.min_value and numeric < line.min_value:
                    raise ValidationError(
                        _("%s must be at least %s %s.")
                        % (label, line.min_value, line.unit or "")
                    )
                if line.max_value and numeric > line.max_value:
                    raise ValidationError(
                        _("%s must be at most %s %s.")
                        % (label, line.max_value, line.unit or "")
                    )
        return True

    @api.constrains(
        "value_float",
        "value_int",
        "value_bool",
        "value_char",
        "value_selection",
        "required",
        "param_id",
    )
    def _check_value(self):
        self._validate_value()


class CorrEngineV2SpecLayer(models.Model):
    _name = "corr.engine.v2.spec.layer"
    _description = "Engine V2 Spec Board Layer"
    _order = "spec_id, sequence, id"

    spec_id = fields.Many2one("corr.engine.v2.spec", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    role = fields.Selection(
        [
            ("outer_liner", "Outer Liner"),
            ("medium", "Medium"),
            ("inner_liner", "Inner Liner"),
            ("center_liner", "Center Liner"),
            ("carton_board", "Carton Board"),
            ("other", "Other"),
        ],
        string="Role",
        required=True,
    )
    gsm = fields.Float(string="GSM", required=True)
    bf = fields.Float(string="Burst Factor (BF)")
    color = fields.Char(string="Color")
    flute_type_id = fields.Many2one(
        "corr.flute",
        string="Flute Type",
        help="Flute type, relevant for medium layers.",
    )
    material_product_id = fields.Many2one(
        "product.product",
        string="Material Product",
        help="Optional material product for this layer.",
    )
    material_template_id = fields.Many2one(
        "product.template",
        string="Material / Paper Source",
        default=lambda self: self._default_material_template_id(),
        domain="[('id', 'in', allowed_material_template_ids)]",
        help="Raw-material product template used to derive GSM/BF/Color choices and resolve the matching variant.",
    )
    gsm_value_id = fields.Many2one(
        "product.attribute.value",
        string="GSM",
        domain="[('id', 'in', allowed_gsm_value_ids)]",
    )
    bf_value_id = fields.Many2one(
        "product.attribute.value",
        string="BF",
        domain="[('id', 'in', allowed_bf_value_ids)]",
    )
    color_value_id = fields.Many2one(
        "product.attribute.value",
        string="Color",
        domain="[('id', 'in', allowed_color_value_ids)]",
    )
    resolved_product_id = fields.Many2one(
        "product.product",
        string="Resolved Variant",
        compute="_compute_material_resolution",
        readonly=True,
    )
    resolution_message = fields.Char(
        string="Resolution",
        compute="_compute_material_resolution",
        readonly=True,
    )
    allowed_material_template_ids = fields.Many2many(
        "product.template",
        "corr_engine_v2_spec_layer_allowed_template_rel",
        "line_id",
        "template_id",
        compute="_compute_allowed_material_values",
    )
    allowed_gsm_value_ids = fields.Many2many(
        "product.attribute.value",
        "corr_engine_v2_spec_layer_allowed_gsm_rel",
        "line_id",
        "value_id",
        compute="_compute_allowed_material_values",
    )
    allowed_bf_value_ids = fields.Many2many(
        "product.attribute.value",
        "corr_engine_v2_spec_layer_allowed_bf_rel",
        "line_id",
        "value_id",
        compute="_compute_allowed_material_values",
    )
    allowed_color_value_ids = fields.Many2many(
        "product.attribute.value",
        "corr_engine_v2_spec_layer_allowed_color_rel",
        "line_id",
        "value_id",
        compute="_compute_allowed_material_values",
    )
    source_type = fields.Selection(
        [
            ("manual", "Manual"),
            ("recipe", "From Recipe"),
        ],
        string="Source Type",
        default="manual",
    )
    source_recipe_line_id = fields.Many2one(
        "corr.engine.board.recipe.line",
        string="Source Recipe Line",
        ondelete="set null",
    )

    @api.model
    def _material_attr_names(self):
        icp = self.env["ir.config_parameter"].sudo()
        return {
            "gsm": (icp.get_param("corr_engine_v2.attr_gsm") or "GSM").strip().lower(),
            "bf": (icp.get_param("corr_engine_v2.attr_bf") or "BF").strip().lower(),
            "color": (icp.get_param("corr_engine_v2.attr_color") or "Color").strip().lower(),
        }

    @api.model
    def _allowed_material_template_ids_from_config(self):
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("corr_engine_v2.allowed_paper_material_template_ids", "")
            or ""
        )
        ids = []
        for item in raw.replace(";", ",").split(","):
            try:
                template_id = int((item or "").strip())
            except Exception:
                template_id = 0
            if template_id:
                ids.append(template_id)
        return list(dict.fromkeys(ids))

    @api.model
    def _default_material_template_id(self):
        Template = self.env["product.template"].sudo()
        configured = Template.browse(self._allowed_material_template_ids_from_config()).exists()
        kraft = configured.filtered(lambda tmpl: (tmpl.name or "").strip().lower() == "kraft paper")[:1]
        if kraft:
            return kraft.id
        if configured:
            return configured[0].id
        domain = [("active", "=", True), ("name", "=ilike", "Kraft Paper")]
        if "product_role" in Template._fields:
            domain.append(("product_role", "=", "raw_material"))
        return Template.search(domain, limit=1).id

    def _attribute_values_for_template(self, template, attr_name):
        if not template or not attr_name:
            return self.env["product.attribute.value"]
        attr_name = attr_name.strip().lower()
        values = self.env["product.attribute.value"]
        for attr_line in template.attribute_line_ids:
            if (attr_line.attribute_id.name or "").strip().lower() == attr_name:
                values |= attr_line.value_ids
        return values

    @api.depends("material_template_id")
    def _compute_allowed_material_values(self):
        Template = self.env["product.template"].sudo()
        configured_ids = self._allowed_material_template_ids_from_config()
        if configured_ids:
            configured = Template.browse(configured_ids).exists()
        else:
            domain = [("active", "=", True)]
            if "product_role" in Template._fields:
                domain.append(("product_role", "=", "raw_material"))
            configured = Template.search(domain)
        attr_names = self._material_attr_names()
        for line in self:
            template = line.material_template_id
            line.allowed_material_template_ids = configured
            line.allowed_gsm_value_ids = line._attribute_values_for_template(template, attr_names["gsm"])
            line.allowed_bf_value_ids = line._attribute_values_for_template(template, attr_names["bf"])
            line.allowed_color_value_ids = line._attribute_values_for_template(template, attr_names["color"])

    def _numeric_from_attribute_value(self, value):
        if not value:
            return 0.0
        match = re.search(r"[-+]?\d*\.?\d+", value.name or "")
        return float(match.group(0)) if match else 0.0

    def _normalized_material_text(self, value):
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return re.sub(r"[^a-z0-9]+", "", text)

    def _snapshot_vals_from_attribute_values(self, vals=None):
        vals = dict(vals or {})
        Value = self.env["product.attribute.value"]
        if vals.get("gsm_value_id") and "gsm" not in vals:
            vals["gsm"] = self._numeric_from_attribute_value(Value.browse(vals["gsm_value_id"]))
        if vals.get("bf_value_id") and "bf" not in vals:
            vals["bf"] = self._numeric_from_attribute_value(Value.browse(vals["bf_value_id"]))
        if vals.get("color_value_id") and "color" not in vals:
            vals["color"] = Value.browse(vals["color_value_id"]).name or ""
        return vals

    @api.model
    def _role_label(self, role):
        return dict(self._fields["role"].selection).get(role, role or "")

    @api.model
    def _board_line_material_name_from_values(self, values):
        template_id = int(values.get("material_template_id") or 0)
        if template_id:
            template = self.env["product.template"].browse(template_id)
            if template.exists():
                return template.display_name or template.name or ""
        product_id = int(values.get("material_product_id") or 0)
        if product_id:
            product = self.env["product.product"].browse(product_id)
            if product.exists():
                return product.display_name or product.name or ""
        return ""

    @api.model
    def _board_line_context_text(self, values):
        role_text = self._role_label(values.get("role"))
        material_text = self._board_line_material_name_from_values(values)
        return " / ".join(part for part in [role_text, material_text] if part)

    @api.model
    def _board_line_row_number_for_create(self, values, fallback_index=1):
        spec_id = int(values.get("spec_id") or 0)
        if not spec_id:
            return fallback_index
        spec = self.env["corr.engine.v2.spec"].browse(spec_id)
        if not spec.exists():
            return fallback_index
        return len(spec.stack_line_ids) + fallback_index

    @api.model
    def _merged_validation_values(self, values, existing=None):
        values = self._snapshot_vals_from_attribute_values(values)
        merged = {}
        field_names = (
            "spec_id",
            "sequence",
            "role",
            "gsm",
            "bf",
            "color",
            "flute_type_id",
            "material_product_id",
            "material_template_id",
            "gsm_value_id",
            "bf_value_id",
            "color_value_id",
            "source_type",
        )
        for field_name in field_names:
            if field_name in values:
                merged[field_name] = values.get(field_name)
            elif existing:
                field = existing._fields.get(field_name)
                current = existing[field_name]
                merged[field_name] = current.id if field and field.type == "many2one" else current
            else:
                merged[field_name] = False
        if not merged.get("material_template_id") and not merged.get("material_product_id"):
            merged["material_template_id"] = self._default_material_template_id() or False
        if not merged.get("source_type"):
            merged["source_type"] = "manual"
        return merged

    @api.model
    def _line_missing_business_fields(self, values):
        missing = []
        role = values.get("role")
        material_template_id = int(values.get("material_template_id") or 0)
        material_product_id = int(values.get("material_product_id") or 0)
        gsm = float(values.get("gsm") or 0.0)
        bf = float(values.get("bf") or 0.0)
        color = str(values.get("color") or "").strip()
        gsm_value_id = int(values.get("gsm_value_id") or 0)
        bf_value_id = int(values.get("bf_value_id") or 0)
        color_value_id = int(values.get("color_value_id") or 0)
        flute_type_id = int(values.get("flute_type_id") or 0)

        if not role:
            missing.append(_("Role"))
        if not material_template_id and not material_product_id:
            missing.append(_("Material / Paper Source"))
        if gsm <= 0.0 and not gsm_value_id:
            missing.append(_("GSM"))
        if bf <= 0.0 and not bf_value_id:
            missing.append(_("BF"))
        if not color and not color_value_id:
            missing.append(_("Color"))
        if role == "medium" and not flute_type_id:
            missing.append(_("Flute Type"))
        return missing

    @api.model
    def _is_effectively_empty_placeholder(self, values):
        role = values.get("role")
        material_product_id = int(values.get("material_product_id") or 0)
        gsm = float(values.get("gsm") or 0.0)
        bf = float(values.get("bf") or 0.0)
        color = str(values.get("color") or "").strip()
        flute_type_id = int(values.get("flute_type_id") or 0)
        gsm_value_id = int(values.get("gsm_value_id") or 0)
        bf_value_id = int(values.get("bf_value_id") or 0)
        color_value_id = int(values.get("color_value_id") or 0)
        return not any(
            [
                role,
                material_product_id,
                gsm > 0.0,
                bf > 0.0,
                color,
                flute_type_id,
                gsm_value_id,
                bf_value_id,
                color_value_id,
            ]
        )

    @api.model
    def _raise_board_line_validation_error(self, values, *, row_number=1, missing_fields=None):
        missing_fields = [field for field in (missing_fields or []) if field]
        context_text = self._board_line_context_text(values)
        line_label = _("Board Structure line %(line)s") % {"line": int(row_number or 1)}
        if context_text:
            line_label = _("%(label)s (%(context)s)") % {
                "label": line_label,
                "context": context_text,
            }
        if len(missing_fields) == 1:
            raise ValidationError(
                _(
                    "Board Structure is incomplete.\n%(line)s is missing %(field)s.\n"
                    "Please complete every board layer before saving or testing."
                )
                % {
                    "line": line_label,
                    "field": missing_fields[0],
                }
            )
        raise ValidationError(
            _(
                "Board Structure is incomplete.\n%(line)s is missing required fields: %(fields)s.\n"
                "Please complete every board layer before saving or testing."
            )
            % {
                "line": line_label,
                "fields": ", ".join(missing_fields),
            }
        )

    @api.model
    def _validate_business_values(self, values, *, existing=None, row_number=1):
        merged = self._merged_validation_values(values, existing=existing)
        missing_fields = self._line_missing_business_fields(merged)
        if missing_fields:
            self._raise_board_line_validation_error(
                merged,
                row_number=row_number,
                missing_fields=missing_fields,
            )
        return merged

    def _validate_existing_business_fields(self, row_number):
        self.ensure_one()
        return bool(
            self._validate_business_values(
                {},
                existing=self,
                row_number=row_number,
            )
        )

    @api.onchange("material_template_id")
    def _onchange_material_template_id(self):
        for line in self:
            line.gsm_value_id = False
            line.bf_value_id = False
            line.color_value_id = False
            line.material_product_id = False

    @api.onchange("gsm_value_id", "bf_value_id", "color_value_id")
    def _onchange_material_attribute_values(self):
        for line in self:
            if line.gsm_value_id:
                line.gsm = line._numeric_from_attribute_value(line.gsm_value_id)
            if line.bf_value_id:
                line.bf = line._numeric_from_attribute_value(line.bf_value_id)
            if line.color_value_id:
                line.color = line.color_value_id.name or ""

    @api.depends("material_template_id", "material_product_id", "gsm_value_id", "bf_value_id", "color_value_id")
    def _compute_material_resolution(self):
        for line in self:
            product, message = line._resolve_variant_without_deckle()
            line.resolved_product_id = product
            line.resolution_message = message

    def _resolve_variant_without_deckle(self):
        self.ensure_one()
        if self.material_product_id:
            return self.material_product_id, _("Matched from selected material product.")
        template = self.material_template_id
        if not template:
            return False, ""
        value_ids = [
            value.id
            for value in (self.gsm_value_id, self.bf_value_id, self.color_value_id)
            if value
        ]
        if not value_ids:
            return False, _("Select GSM/BF/Color to resolve variant.")
        candidates = template.product_variant_ids.filtered(lambda product: product.active)
        for value_id in value_ids:
            candidates = candidates.filtered(
                lambda product, vid=value_id: vid
                in product.product_template_attribute_value_ids.mapped("product_attribute_value_id").ids
            )
        if len(candidates) == 1:
            return candidates[0], _("Matched. Deckle is checked during preview.")
        if len(candidates) > 1:
            return False, _("Multiple variants match before deckle. Preview will use exact deckle.")
        return False, _("No variant matches selected material attributes.")

    def action_duplicate_layer(self):
        for line in self:
            spec = line.spec_id
            sequence = int(line.sequence or 0) + 10
            next_lines = spec.stack_line_ids.filtered(
                lambda other: (other.sequence or 0) > (line.sequence or 0)
            ).sorted(lambda other: (other.sequence or 0, other.id or 0))
            if next_lines:
                next_sequence = int(next_lines[0].sequence or 0)
                if next_sequence - int(line.sequence or 0) > 1:
                    sequence = int(line.sequence or 0) + max(1, (next_sequence - int(line.sequence or 0)) // 2)
            self.create(
                {
                    "spec_id": spec.id,
                    "sequence": sequence,
                    "role": line.role,
                    "gsm": line.gsm,
                    "bf": line.bf,
                    "color": line.color,
                    "flute_type_id": line.flute_type_id.id,
                    "material_product_id": line.material_product_id.id,
                    "material_template_id": line.material_template_id.id,
                    "gsm_value_id": line.gsm_value_id.id,
                    "bf_value_id": line.bf_value_id.id,
                    "color_value_id": line.color_value_id.id,
                    "source_type": "manual",
                    "source_recipe_line_id": False,
                }
            )
            spec.is_board_customized = True
            spec._normalize_stack_line_sequences()
        return True

    def action_move_up(self):
        for line in self:
            spec = line.spec_id
            ordered = spec.stack_line_ids.sorted(lambda item: (item.sequence or 0, item.id or 0))
            items = list(ordered)
            try:
                index = items.index(line)
            except ValueError:
                continue
            if index <= 0:
                continue
            previous = items[index - 1]
            current_sequence = int(line.sequence or 0)
            previous_sequence = int(previous.sequence or 0)
            line.with_context(
                skip_board_line_sequence_normalize=True,
            ).write({"sequence": previous_sequence})
            previous.with_context(
                skip_board_line_sequence_normalize=True,
            ).write({"sequence": current_sequence})
            spec._normalize_stack_line_sequences()
        return True

    def action_move_down(self):
        for line in self:
            spec = line.spec_id
            ordered = spec.stack_line_ids.sorted(lambda item: (item.sequence or 0, item.id or 0))
            items = list(ordered)
            try:
                index = items.index(line)
            except ValueError:
                continue
            if index >= len(items) - 1:
                continue
            following = items[index + 1]
            current_sequence = int(line.sequence or 0)
            following_sequence = int(following.sequence or 0)
            line.with_context(
                skip_board_line_sequence_normalize=True,
            ).write({"sequence": following_sequence})
            following.with_context(
                skip_board_line_sequence_normalize=True,
            ).write({"sequence": current_sequence})
            spec._normalize_stack_line_sequences()
        return True

    def write(self, vals):
        """If a recipe-sourced line is edited, flip it to manual/custom.

        When any board-definition field is changed on a line whose current
        source_type == 'recipe', we:
        - set source_type -> 'manual'
        - clear source_recipe_line_id
        - mark the parent spec as board-customized
        """
        board_fields = {
            "role",
            "gsm",
            "bf",
            "color",
            "flute_type_id",
            "material_product_id",
            "material_template_id",
            "gsm_value_id",
            "bf_value_id",
            "color_value_id",
        }
        reorder_fields = {"sequence"}
        vals = self._snapshot_vals_from_attribute_values(vals)
        touched_specs = self.mapped("spec_id")
        ordered_lines = {
            line.id: index
            for index, line in enumerate(
                touched_specs.mapped("stack_line_ids").sorted(
                    lambda item: (item.sequence or 0, item.id or 0)
                ),
                start=1,
            )
        }
        reorder_only_change = bool(
            reorder_fields.intersection(vals.keys())
            and not board_fields.intersection(vals.keys())
        )
        marks_board_change = bool(
            (board_fields | reorder_fields).intersection(vals.keys())
            and not self.env.context.get("skip_board_line_change_mark")
        )

        if self and board_fields.intersection(vals.keys()):
            def _line_value_changed(line):
                for field_name in board_fields.intersection(vals.keys()):
                    new_value = vals.get(field_name)
                    current_value = line[field_name]
                    field = line._fields.get(field_name)
                    if field and field.type == "many2one":
                        current_value = current_value.id
                        new_value = int(new_value or 0)
                    elif field and field.type in ("float", "monetary"):
                        try:
                            if abs(float(current_value or 0.0) - float(new_value or 0.0)) <= 1e-9:
                                continue
                        except Exception:
                            pass
                    if (current_value or False) != (new_value or False):
                        return True
                return False

            recipe_lines = self.filtered(
                lambda l: l.source_type == "recipe" and _line_value_changed(l)
            )
            if recipe_lines:
                manual_vals = vals.copy()
                manual_vals["source_type"] = "manual"
                manual_vals["source_recipe_line_id"] = False
                for line in recipe_lines:
                    self._validate_business_values(
                        manual_vals,
                        existing=line,
                        row_number=ordered_lines.get(line.id, 1),
                    )

                # Mark parent specs as customized once a recipe line is edited.
                recipe_lines.mapped("spec_id").write({"is_board_customized": True})

                # Apply updates to recipe-sourced lines with overridden source.
                super(CorrEngineV2SpecLayer, recipe_lines).write(manual_vals)

                # Apply the original vals to any remaining (non-recipe) lines.
                remaining = self - recipe_lines
                if remaining:
                    for line in remaining:
                        self._validate_business_values(
                            vals,
                            existing=line,
                            row_number=ordered_lines.get(line.id, 1),
                        )
                    super(CorrEngineV2SpecLayer, remaining).write(vals)
                if marks_board_change:
                    touched_specs._mark_board_structure_changed()
                    touched_specs.stack_line_ids.write(
                        {
                            "source_type": "manual",
                            "source_recipe_line_id": False,
                        }
                    )
                    if (
                        not reorder_only_change
                        and not self.env.context.get("skip_board_line_sequence_normalize")
                    ):
                        touched_specs._normalize_stack_line_sequences()
                return True

        if self and board_fields.intersection(vals.keys()):
            for line in self:
                self._validate_business_values(
                    vals,
                    existing=line,
                    row_number=ordered_lines.get(line.id, 1),
                )

        res = super().write(vals)
        if marks_board_change:
            touched_specs._mark_board_structure_changed()
            touched_specs.stack_line_ids.write(
                {
                    "source_type": "manual",
                    "source_recipe_line_id": False,
                }
            )
            if (
                not reorder_only_change
                and not self.env.context.get("skip_board_line_sequence_normalize")
            ):
                touched_specs._normalize_stack_line_sequences()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals = []
        for index, vals in enumerate(vals_list, start=1):
            merged = self._merged_validation_values(vals)
            if self._is_effectively_empty_placeholder(merged):
                continue
            row_number = self._board_line_row_number_for_create(vals, fallback_index=index)
            prepared_vals.append(
                self._validate_business_values(merged, row_number=row_number)
            )
        if not prepared_vals:
            return self.browse()
        vals_list = prepared_vals
        records = super().create(vals_list)
        if not self.env.context.get("skip_board_line_change_mark"):
            specs = records.filtered(lambda line: line.source_type != "recipe").mapped("spec_id")
            if specs:
                specs._mark_board_structure_changed()
                if not self.env.context.get("skip_board_line_sequence_normalize"):
                    specs._normalize_stack_line_sequences()
        return records


class CorrEngineV2SpecPiece(models.Model):
    _name = "corr.engine.v2.spec.piece"
    _description = "Engine V2 Spec Multi-Piece Line"
    _order = "spec_id, sequence, id"

    spec_id = fields.Many2one("corr.engine.v2.spec", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    piece_code = fields.Char(string="Piece Code", required=True)
    piece_name = fields.Char(string="Piece Name")
    piece_length_mm = fields.Float(string="Piece Length (mm)", required=True)
    piece_width_mm = fields.Float(string="Piece Width (mm)", required=True)
    piece_qty_per_fg = fields.Float(string="Qty / FG", default=1.0, required=True)
    stack_source = fields.Selection(
        [
            ("inherited", "Inherited"),
            ("custom", "Custom"),
        ],
        string="Stack Source",
        default="inherited",
        required=True,
    )
    piece_stack_line_ids = fields.One2many(
        "corr.engine.v2.spec.piece.layer",
        "piece_id",
        string="Piece Stack Layers",
    )
    board_recipe_id = fields.Many2one(
        "corr.engine.board.recipe",
        string="Piece Stack Profile",
    )
    flute_profile_id = fields.Many2one(
        "corr.flute",
        string="Piece Flute Profile",
    )
    surface_source = fields.Selection(
        [
            ("inherited", "Inherited"),
            ("custom", "Custom"),
        ],
        string="Surface Source",
        default="inherited",
        required=True,
    )
    surface_profile_id = fields.Many2one(
        "corr.engine.surface.profile",
        string="Piece Surface Profile",
    )
    top_sheet_source = fields.Selection(
        [
            ("inherited", "Inherited"),
            ("custom", "Custom"),
        ],
        string="Top Sheet Source",
        default="inherited",
        required=True,
    )
    top_sheet_mode = fields.Selection(
        [
            ("none", "None"),
            ("roll", "Top from Roll"),
            ("unit_sheet", "Unit Printed Sheet"),
        ],
        string="Piece Top Sheet Mode",
    )
    top_sheet_product_id = fields.Many2one(
        "product.product",
        string="Piece Top Sheet Product",
        domain="[('corr_top_sheet_selectable', '=', True)]",
    )
    effective_stack_summary = fields.Text(
        string="Effective Stack",
        compute="_compute_effective_piece_manufacturing",
    )
    effective_surface_summary = fields.Char(
        string="Effective Surface",
        compute="_compute_effective_piece_manufacturing",
    )
    effective_top_sheet_summary = fields.Char(
        string="Effective Top Sheet",
        compute="_compute_effective_piece_manufacturing",
    )
    recipe_signature = fields.Char(
        string="Recipe Signature",
        compute="_compute_effective_piece_manufacturing",
    )
    planning_status = fields.Char(
        string="Planning Status",
        compute="_compute_planning_status",
    )

    @api.depends("spec_id.multi_piece_split_stale", "spec_id.multi_piece_source")
    def _compute_planning_status(self):
        for piece in self:
            if (
                piece.spec_id
                and piece.spec_id.multi_piece_source == "accepted_auto_split"
                and piece.spec_id.multi_piece_split_stale
            ):
                piece.planning_status = _("Stale")
            else:
                piece.planning_status = _("Current")

    @api.model
    def _normalize_source_vals(self, vals):
        vals = dict(vals or {})
        for field_name in ("stack_source", "surface_source", "top_sheet_source"):
            if vals.get(field_name) == "override":
                vals[field_name] = "custom"
        return vals

    def write(self, vals):
        vals = self._normalize_source_vals(vals)
        tracked_fields = {
            "sequence",
            "piece_code",
            "piece_name",
            "piece_length_mm",
            "piece_width_mm",
            "piece_qty_per_fg",
            "stack_source",
            "board_recipe_id",
            "flute_profile_id",
            "surface_source",
            "surface_profile_id",
            "top_sheet_source",
            "top_sheet_mode",
            "top_sheet_product_id",
        }
        specs = self.mapped("spec_id")
        marks_piece_change = bool(
            tracked_fields.intersection(vals)
            and not self.env.context.get("skip_piece_change_mark")
        )
        res = super().write(vals)
        if marks_piece_change and specs:
            specs._mark_manufacturing_inputs_changed()
            if "sequence" in vals and not self.env.context.get("skip_piece_sequence_normalize"):
                specs._normalize_multi_piece_line_sequences()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._normalize_source_vals(vals) for vals in vals_list]
        return super().create(vals_list)

    def action_open_piece_popup(self):
        self.ensure_one()
        popup_view = self.env.ref(
            "corr_engine_v2.view_corr_engine_v2_spec_piece_popup_form",
            raise_if_not_found=False,
        )
        return {
            "type": "ir.actions.act_window",
            "name": self.piece_name or self.piece_code or "Piece",
            "res_model": "corr.engine.v2.spec.piece",
            "view_mode": "form",
            "views": [(popup_view.id, "form")] if popup_view else [(False, "form")],
            "view_id": popup_view.id if popup_view else False,
            "res_id": self.id,
            "target": "new",
            "context": dict(self.env.context, form_view_initial_mode="edit"),
        }

    def _normalized_source(self, value):
        value = (value or "inherited").strip().lower()
        if value == "override":
            return "custom"
        return value or "inherited"

    def _safe_record_id(self, record):
        if not record:
            return False
        try:
            return record.id or False
        except Exception:
            return False

    def _safe_record_display_name(self, record):
        if not record:
            return ""
        try:
            return record.display_name or record.name or ""
        except Exception:
            return ""

    @api.depends(
        "stack_source",
        "piece_stack_line_ids.sequence",
        "piece_stack_line_ids.role",
        "piece_stack_line_ids.gsm",
        "piece_stack_line_ids.bf",
        "piece_stack_line_ids.color",
        "piece_stack_line_ids.flute_type_id",
        "piece_stack_line_ids.material_product_id",
        "surface_source",
        "surface_profile_id",
        "top_sheet_source",
        "top_sheet_mode",
        "top_sheet_product_id",
        "spec_id.stack_line_ids.sequence",
        "spec_id.stack_line_ids.role",
        "spec_id.stack_line_ids.gsm",
        "spec_id.stack_line_ids.bf",
        "spec_id.stack_line_ids.color",
        "spec_id.stack_line_ids.flute_type_id",
        "spec_id.stack_line_ids.material_product_id",
        "spec_id.surface_profile_id",
        "spec_id.surface_top_sheet_mode",
        "spec_id.surface_top_sheet_product_id",
    )
    def _compute_effective_piece_manufacturing(self):
        for piece in self:
            stack = piece.get_effective_piece_stack(piece.spec_id)
            surface = piece.get_effective_piece_surface(piece.spec_id)
            top_sheet = piece.get_effective_piece_top_sheet(piece.spec_id)
            piece.effective_stack_summary = "\n".join(
                piece._stack_line_summary(line) for line in stack.get("lines", [])
            )
            piece.effective_surface_summary = surface.get("display_name") or "-"
            piece.effective_top_sheet_summary = top_sheet.get("display_name") or "-"
            piece.recipe_signature = piece._piece_recipe_signature(stack, surface, top_sheet)

    @api.onchange("stack_source")
    def _onchange_stack_source(self):
        for piece in self:
            if piece._normalized_source(piece.stack_source) != "custom":
                continue
            if piece.piece_stack_line_ids:
                continue
            parent_lines = piece._ordered_parent_stack_lines()
            piece.piece_stack_line_ids = [
                (0, 0, piece._layer_vals_from_parent(line))
                for line in parent_lines
            ]

    @api.onchange("surface_source", "surface_profile_id")
    def _onchange_piece_surface_profile_id(self):
        for piece in self:
            if piece._normalized_source(piece.surface_source) != "custom":
                continue
            profile = piece.surface_profile_id
            if not profile:
                continue
            piece.top_sheet_mode = profile.top_sheet_mode or piece.top_sheet_mode
            piece.top_sheet_product_id = profile.top_sheet_product_id.id or piece.top_sheet_product_id.id

    def get_effective_piece_stack(self, parent_spec):
        self.ensure_one()
        source = self._normalized_source(self.stack_source)
        lines = self.piece_stack_line_ids if source == "custom" else getattr(parent_spec, "stack_line_ids", False)
        ordered = self._ordered_lines(lines)
        return {
            "source": source,
            "lines": [self._stack_line_payload(line) for line in ordered],
        }

    def get_effective_piece_surface(self, parent_spec):
        self.ensure_one()
        source = self._normalized_source(self.surface_source)
        profile = self.surface_profile_id if source == "custom" else getattr(parent_spec, "surface_profile_id", False)
        return {
            "source": source,
            "profile_id": self._safe_record_id(profile),
            "display_name": self._safe_record_display_name(profile),
        }

    def get_effective_piece_top_sheet(self, parent_spec):
        self.ensure_one()
        source = self._normalized_source(self.top_sheet_source)
        if source == "custom":
            mode = self.top_sheet_mode or ""
            product = self.top_sheet_product_id
        else:
            mode = getattr(parent_spec, "surface_top_sheet_mode", "") if parent_spec else ""
            product = getattr(parent_spec, "surface_top_sheet_product_id", False) if parent_spec else False
        product_name = self._safe_record_display_name(product)
        mode_label = dict(self._fields["top_sheet_mode"].selection).get(mode, mode or "")
        display = " / ".join(item for item in [mode_label, product_name] if item)
        return {
            "source": source,
            "top_sheet_mode": mode,
            "top_sheet_product_id": self._safe_record_id(product),
            "top_sheet_product_name": product_name,
            "display_name": display,
        }

    def _ordered_parent_stack_lines(self):
        self.ensure_one()
        return self._ordered_lines(getattr(self.spec_id, "stack_line_ids", False))

    def _ordered_lines(self, lines):
        if not lines:
            return []
        try:
            sorter = getattr(lines, "sorted", None)
            if callable(sorter):
                return list(sorter(lambda line: (line.sequence or 0, line.id or 0)))
        except Exception:
            pass
        try:
            return sorted(list(lines), key=lambda line: (line.sequence or 0, line.id or 0))
        except Exception:
            return list(lines)

    def _layer_vals_from_parent(self, line):
        return {
            "sequence": line.sequence,
            "role": line.role,
            "gsm": line.gsm,
            "bf": line.bf,
            "color": line.color,
            "flute_type_id": line.flute_type_id.id,
            "material_product_id": line.material_product_id.id,
            "source_type": "manual",
            "source_recipe_line_id": False,
        }

    def _stack_line_payload(self, line):
        flute = getattr(line, "flute_type_id", False)
        product = getattr(line, "material_product_id", False)
        return {
            "sequence": line.sequence,
            "role": line.role,
            "gsm": line.gsm,
            "bf": line.bf,
            "color": line.color or "",
            "flute_type_id": self._safe_record_id(flute),
            "flute": (flute.code or flute.name or "") if flute else "",
            "flute_tuf": float(getattr(flute, "tuf", 0.0) or 0.0) if flute else 0.0,
            "material_product_id": self._safe_record_id(product),
            "material_product_name": self._safe_record_display_name(product),
            "source_type": line.source_type or "manual",
            "source_recipe_line_id": self._safe_record_id(line.source_recipe_line_id),
        }

    def _stack_line_summary(self, line):
        parts = [
            str(line.get("sequence") or ""),
            str(line.get("role") or ""),
            "GSM %s" % ("%g" % (line.get("gsm") or 0.0)),
        ]
        if line.get("bf"):
            parts.append("BF %s" % ("%g" % line.get("bf")))
        if line.get("color"):
            parts.append(str(line.get("color")))
        if line.get("flute"):
            parts.append("Flute %s" % line.get("flute"))
        return " | ".join(item for item in parts if item)

    def _piece_recipe_signature(self, stack, surface, top_sheet):
        payload = {
            "stack": stack,
            "surface": surface,
            "top_sheet": top_sheet,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()

    def _mark_piece_stack_changed(self):
        specs = self.mapped("spec_id")
        if specs:
            specs._mark_manufacturing_inputs_changed()

    def _normalize_piece_stack_line_sequences(self):
        Layer = self.env["corr.engine.v2.spec.piece.layer"]
        for piece in self:
            lines = piece.piece_stack_line_ids.sorted(lambda item: (item.sequence or 0, item.id or 0))
            for index, line in enumerate(lines, start=1):
                normalized_sequence = index * 10
                if int(line.sequence or 0) == normalized_sequence:
                    continue
                Layer.browse(line.id).with_context(
                    skip_piece_stack_line_change_mark=True,
                    skip_piece_stack_line_sequence_normalize=True,
                ).write({"sequence": normalized_sequence})


class CorrEngineV2SpecPieceLayer(models.Model):
    _name = "corr.engine.v2.spec.piece.layer"
    _description = "Engine V2 Piece Board Layer"
    _order = "piece_id, sequence, id"

    piece_id = fields.Many2one("corr.engine.v2.spec.piece", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    role = fields.Selection(
        [
            ("outer_liner", "Outer Liner"),
            ("medium", "Medium"),
            ("inner_liner", "Inner Liner"),
            ("center_liner", "Center Liner"),
            ("other", "Other"),
        ],
        string="Role",
        required=True,
    )
    gsm = fields.Float(string="GSM", required=True)
    bf = fields.Float(string="Burst Factor (BF)")
    color = fields.Char(string="Color")
    flute_type_id = fields.Many2one(
        "corr.flute",
        string="Flute Type",
        help="Flute type, relevant for medium layers.",
    )
    material_product_id = fields.Many2one(
        "product.product",
        string="Material Product",
        help="Optional material product for this layer.",
    )
    material_template_id = fields.Many2one(
        "product.template",
        string="Material / Paper Source",
        help="Raw-material product template used to derive GSM/BF/Color choices and resolve the matching variant.",
    )
    gsm_value_id = fields.Many2one(
        "product.attribute.value",
        string="GSM",
    )
    bf_value_id = fields.Many2one(
        "product.attribute.value",
        string="BF",
    )
    color_value_id = fields.Many2one(
        "product.attribute.value",
        string="Color",
    )
    resolved_product_id = fields.Many2one(
        "product.product",
        string="Resolved Variant",
        readonly=True,
    )
    resolution_message = fields.Char(
        string="Resolution",
        readonly=True,
    )
    source_type = fields.Selection(
        [
            ("manual", "Manual"),
            ("recipe", "From Recipe"),
        ],
        string="Source Type",
        default="manual",
    )
    source_recipe_line_id = fields.Many2one(
        "corr.engine.board.recipe.line",
        string="Source Recipe Line",
        ondelete="set null",
    )

    def action_duplicate_layer(self):
        for line in self:
            sequence = int(line.sequence or 0) + 10
            self.create(
                {
                    "piece_id": line.piece_id.id,
                    "sequence": sequence,
                    "role": line.role,
                    "gsm": line.gsm,
                    "bf": line.bf,
                    "color": line.color,
                    "flute_type_id": line.flute_type_id.id,
                    "material_product_id": line.material_product_id.id,
                    "source_type": "manual",
                    "source_recipe_line_id": False,
                }
            )
            line.piece_id._normalize_piece_stack_line_sequences()
        return True

    def action_move_up(self):
        for line in self:
            piece = line.piece_id
            ordered = piece.piece_stack_line_ids.sorted(lambda item: (item.sequence or 0, item.id or 0))
            items = list(ordered)
            try:
                index = items.index(line)
            except ValueError:
                continue
            if index <= 0:
                continue
            previous = items[index - 1]
            current_sequence = int(line.sequence or 0)
            previous_sequence = int(previous.sequence or 0)
            line.with_context(
                skip_piece_stack_line_sequence_normalize=True,
            ).write({"sequence": previous_sequence})
            previous.with_context(
                skip_piece_stack_line_sequence_normalize=True,
            ).write({"sequence": current_sequence})
            piece._normalize_piece_stack_line_sequences()
        return True

    def action_move_down(self):
        for line in self:
            piece = line.piece_id
            ordered = piece.piece_stack_line_ids.sorted(lambda item: (item.sequence or 0, item.id or 0))
            items = list(ordered)
            try:
                index = items.index(line)
            except ValueError:
                continue
            if index >= len(items) - 1:
                continue
            following = items[index + 1]
            current_sequence = int(line.sequence or 0)
            following_sequence = int(following.sequence or 0)
            line.with_context(
                skip_piece_stack_line_sequence_normalize=True,
            ).write({"sequence": following_sequence})
            following.with_context(
                skip_piece_stack_line_sequence_normalize=True,
            ).write({"sequence": current_sequence})
            piece._normalize_piece_stack_line_sequences()
        return True

    def _mark_piece_stack_lines_changed(self, pieces):
        if not pieces:
            return
        pieces.write({"stack_source": "custom"})
        pieces._mark_piece_stack_changed()
        pieces.piece_stack_line_ids.write(
            {
                "source_type": "manual",
                "source_recipe_line_id": False,
            }
        )
        if not self.env.context.get("skip_piece_stack_line_sequence_normalize"):
            pieces._normalize_piece_stack_line_sequences()

    def write(self, vals):
        vals = dict(vals or {})
        tracked_fields = {
            "sequence",
            "role",
            "gsm",
            "bf",
            "color",
            "flute_type_id",
            "material_product_id",
            "material_template_id",
            "gsm_value_id",
            "bf_value_id",
            "color_value_id",
        }
        pieces = self.mapped("piece_id")
        reorder_only_change = bool(
            "sequence" in vals
            and not {
                "role",
                "gsm",
                "bf",
                "color",
                "flute_type_id",
                "material_product_id",
                "material_template_id",
                "gsm_value_id",
                "bf_value_id",
                "color_value_id",
            }.intersection(vals)
        )
        marks_stack_change = bool(
            tracked_fields.intersection(vals)
            and not self.env.context.get("skip_piece_stack_line_change_mark")
        )
        if marks_stack_change:
            vals.setdefault("source_type", "manual")
            vals.setdefault("source_recipe_line_id", False)
        res = super().write(vals)
        if marks_stack_change:
            if reorder_only_change:
                pieces.write({"stack_source": "custom"})
                pieces._mark_piece_stack_changed()
                pieces.piece_stack_line_ids.write(
                    {
                        "source_type": "manual",
                        "source_recipe_line_id": False,
                    }
                )
            else:
                self._mark_piece_stack_lines_changed(pieces)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get("skip_piece_stack_line_change_mark"):
            pieces = records.mapped("piece_id")
            if pieces:
                pieces._mark_piece_stack_changed()
                if not self.env.context.get("skip_piece_stack_line_sequence_normalize"):
                    pieces._normalize_piece_stack_line_sequences()
        return records


def get_effective_piece_stack(piece, parent_spec):
    return piece.get_effective_piece_stack(parent_spec)


def get_effective_piece_surface(piece, parent_spec):
    return piece.get_effective_piece_surface(parent_spec)


def get_effective_piece_top_sheet(piece, parent_spec):
    return piece.get_effective_piece_top_sheet(parent_spec)



