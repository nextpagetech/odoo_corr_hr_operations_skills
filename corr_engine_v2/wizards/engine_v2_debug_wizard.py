import json
import hashlib
import logging
import math
import sys
from copy import deepcopy
from collections import defaultdict
from html import escape as html_escape
from types import SimpleNamespace

from odoo import api, fields, models, _
from odoo.exceptions import UserError

try:
    # Engine V2 integration (pure engine + adapters).
    from odoo.addons.corr_engine_v2.engine_v2 import compute as engine_v2_compute
    from odoo.addons.corr_engine_v2.engine_v2 import RmRequirement as engine_v2_RmRequirement
    from odoo.addons.corr_engine_v2.engine_v2.stage_graph import build_stage_graph
    from odoo.addons.corr_engine_v2.engine_v2.bom_adapter import (
        build_bom_structure,
        _resolve_rm_node as _engine_v2_resolve_rm_node,
    )
    from odoo.addons.corr_engine_v2.spec_adapter import build_engine_spec_input
    from odoo.addons.corr_engine_v2.plant_adapter import build_plant_profile
    # BoM adapter at the Odoo layer (mapping, signatures, create-from-preview).
    from odoo.addons.corr_mrp_bridge import bom_adapter as engine_v2_bom_adapter
    # Phase 8 dynamic architecture: shadow stage-first runtime builder.
    from odoo.addons.corr_engine_v2 import engine_v2_runtime as engine_v2_runtime_builder
    from odoo.addons.corr_engine_v2 import preview_adapter as engine_v2_preview_adapter
    from odoo.addons.corr_engine_v2.engine_v2 import manufacturing_plan as engine_v2_manufacturing_plan
    from odoo.addons.corr_engine_v2.engine_v2 import materialization_resolver as engine_v2_materialization_resolver
    from odoo.addons.corr_engine_v2.engine_v2 import materialization_writer as engine_v2_materialization_writer
    from odoo.addons.corr_engine_v2.engine_v2 import multi_piece_materialization as engine_v2_multi_piece_materialization
    from odoo.addons.corr_engine_v2.engine_v2.consumables import (
        CONSUMABLE_FLAG_FIELDS,
        consumable_state_signature,
        effective_consumable_state,
        is_consumable_enabled,
        is_consumable_role,
        should_include_consumable,
    )
except Exception:  # pragma: no cover - defensive fallback
    engine_v2_compute = None
    engine_v2_RmRequirement = None
    build_stage_graph = None
    build_bom_structure = None
    _engine_v2_resolve_rm_node = None
    build_engine_spec_input = None
    build_plant_profile = None
    engine_v2_bom_adapter = None
    engine_v2_runtime_builder = None
    engine_v2_preview_adapter = None
    engine_v2_manufacturing_plan = None
    engine_v2_materialization_resolver = None
    engine_v2_materialization_writer = None
    engine_v2_multi_piece_materialization = None
    CONSUMABLE_FLAG_FIELDS = {}
    consumable_state_signature = None
    effective_consumable_state = None
    is_consumable_enabled = None
    is_consumable_role = None
    should_include_consumable = None

_logger = logging.getLogger(__name__)


def _summary_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _summary_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(round(float(value)))
    except Exception:
        return default


def _summary_text(value):
    return str(value or "").strip()


def _summary_cost_source_label(value):
    mapping = {
        "standard_price": "Standard Cost",
        "last_purchase_price": "Last Purchase Price",
        "vendor_price": "Vendor Price",
    }
    key = _summary_text(value)
    return mapping.get(key, key)


def _summary_style_mode(context):
    if context.get("is_diecut_sheet_style"):
        return "diecut_sheet"
    if context.get("is_sheet_style"):
        return "sheet"
    if context.get("is_roll_style"):
        return "roll"
    return "box"


def _summary_rows(*rows):
    filtered = []
    for label, value in rows:
        if value in (None, "", [], {}):
            continue
        filtered.append((label, value))
    return filtered


def _build_business_summary_sections(summary, context):
    mode = _summary_style_mode(context)

    def _with_unit(value, unit):
        text = _summary_text(value)
        if not text:
            return ""
        return "%s %s" % (text, unit)

    spec_context = _summary_rows(
        ("Spec Name", context.get("spec_name")),
        ("Finished Product", context.get("finished_product")),
        ("Style / FEFCO", context.get("style_display")),
        ("Plant", context.get("plant_name")),
        ("Order Qty", context.get("order_qty_display")),
        ("Cost Source", context.get("cost_source_label")),
    )

    area_per_fg = _with_unit(summary.get("area_per_fg_m2_display"), "m2")
    weight_per_fg = _with_unit(summary.get("weight_per_fg_kg_display"), "kg")
    area_per_sheet = _with_unit(summary.get("area_per_sheet_m2_display"), "m2")
    area_per_roll = _with_unit(summary.get("area_per_roll_m2_display"), "m2")

    if mode == "diecut_sheet":
        geometry = _summary_rows(
            ("Sheet Length", _with_unit(summary.get("sheet_length_mm_display"), "mm")),
            ("Sheet Width", _with_unit(summary.get("sheet_width_mm_display"), "mm")),
            ("FG per Sheet", _with_unit(summary.get("fg_per_sheet_display"), "fg/sheet")),
            ("Area per Sheet", area_per_sheet),
            ("Area per FG", area_per_fg),
            ("Required Sheets", _with_unit(summary.get("required_sheets_display"), "sheets")),
        )
        production = _summary_rows(
            ("Order Qty", context.get("order_qty_display")),
            ("FG per Sheet", _with_unit(summary.get("fg_per_sheet_display"), "fg/sheet")),
            ("Required Sheets", _with_unit(summary.get("required_sheets_display"), "sheets")),
            ("Gross Blanks Produced", _with_unit(summary.get("gross_blanks_produced_display"), "blanks")),
            ("Cost per Unit", summary.get("material_cost_per_unit_display")),
            ("Total Cost for Order", summary.get("total_production_cost_for_order_display")),
        )
    elif mode == "sheet":
        geometry = _summary_rows(
            ("Sheet Length", _with_unit(summary.get("sheet_length_mm_display"), "mm")),
            ("Sheet Width", _with_unit(summary.get("sheet_width_mm_display"), "mm")),
            ("Area per Sheet", area_per_sheet),
        )
        production = _summary_rows(
            ("Order Qty", context.get("order_qty_display")),
            ("Required Sheets", _with_unit(summary.get("required_sheets_display"), "sheets")),
            ("Cost per Unit", summary.get("material_cost_per_unit_display")),
            ("Total Cost for Order", summary.get("total_production_cost_for_order_display")),
        )
    elif mode == "roll":
        geometry = _summary_rows(
            ("Roll Length", _with_unit(summary.get("roll_length_m_display"), "m")),
            ("Roll Width", _with_unit(summary.get("roll_width_mm_display"), "mm")),
            ("Area per Roll", area_per_roll),
        )
        production = _summary_rows(
            ("Order Qty", context.get("order_qty_display")),
            ("Cost per Unit", summary.get("material_cost_per_unit_display")),
            ("Total Cost for Order", summary.get("total_production_cost_for_order_display")),
        )
    else:
        geometry = _summary_rows(
            ("Blank Length", _with_unit(summary.get("blank_length_mm_display"), "mm")),
            ("Blank Width", _with_unit(summary.get("blank_width_mm_display"), "mm")),
            ("Area per FG", area_per_fg),
            ("Weight per FG", weight_per_fg),
        )
        production = _summary_rows(
            ("Order Qty", context.get("order_qty_display")),
            ("Required Sheets", _with_unit(summary.get("required_sheets_display"), "sheets")),
            ("Cost per Unit", summary.get("material_cost_per_unit_display")),
            ("Total Cost for Order", summary.get("total_production_cost_for_order_display")),
        )

    return [
        ("Spec Context", spec_context),
        ("Geometry / Size Summary", geometry),
        ("Quick Manufacturing Summary", production),
    ]


def _render_business_summary_html(sections, *, warnings_present=False):
    html = ["<h3>%s</h3>" % _("Engine V2 Summary")]
    if warnings_present:
        html.append(
            "<div class='alert alert-warning' role='alert'>%s</div>"
            % _("Warnings are present; see the Warnings tab for details.")
        )
    for title, rows in sections:
        if not rows:
            continue
        html.extend(
            [
                "<div class='mb-3'>",
                "<h4>%s</h4>" % html_escape(str(title)),
                "<table class='table table-sm table-striped'>",
                "<tbody>",
            ]
        )
        for label, value in rows:
            html.append(
                "<tr><th>%s</th><td>%s</td></tr>"
                % (html_escape(str(label)), html_escape(str(value)))
            )
        html.extend(["</tbody>", "</table>", "</div>"])
    return "\n".join(html)


class _EngineV2PreviewFormatter:
    """Centralized Engine V2 preview display formatting.

    This helper is intentionally presentation-only. It never changes raw_json,
    stored values, or calculation precision.
    """

    def __init__(self, plant=None):
        rules = {}
        try:
            raw_rules = getattr(plant, "rounding_rules_json", None) if plant else None
            if isinstance(raw_rules, str) and raw_rules:
                rules = json.loads(raw_rules)
            elif isinstance(raw_rules, dict):
                rules = dict(raw_rules)
        except Exception:
            rules = {}

        self.kg_digits = self._digits(
            getattr(plant, "rounding_kg_digits", None) if plant else None,
            rules.get("kg"),
            6,
        )
        self.m_digits = self._digits(
            getattr(plant, "rounding_m_digits", None) if plant else None,
            rules.get("m"),
            3,
        )
        self.m2_digits = self._digits(
            getattr(plant, "rounding_m2_digits", None) if plant else None,
            rules.get("m2"),
            6,
        )

    @staticmethod
    def _to_float(value, default=0.0):
        try:
            if value in (None, ""):
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _digits(*values):
        for value in values:
            try:
                if value in (None, ""):
                    continue
                digits = int(value)
                if digits >= 0:
                    return min(digits, 12)
            except Exception:
                continue
        return 6

    @staticmethod
    def _trim(value):
        if "." not in value:
            return value
        return value.rstrip("0").rstrip(".")

    def format_number(self, value, digits=3, thousands=False, trim=True):
        amount = self._to_float(value)
        fmt = ("{:,.%df}" if thousands else "{:.%df}") % max(int(digits), 0)
        rendered = fmt.format(amount)
        return self._trim(rendered) if trim else rendered

    def format_integer(self, value):
        amount = self._to_float(value)
        if abs(amount - round(amount)) < 0.0000001:
            return "{:,}".format(int(round(amount)))
        return self._trim("{:,.3f}".format(amount))

    def format_kg(self, value):
        return self.format_number(value, self.kg_digits)

    def format_m(self, value):
        return self.format_number(value, self.m_digits)

    def format_m2(self, value):
        return self.format_number(value, self.m2_digits)

    def format_currency(self, value, symbol=""):
        amount = self._to_float(value)
        rendered = "{:,.2f}".format(amount)
        return ("%s %s" % (symbol, rendered)).strip()

    def format_quantity(self, value, uom="", digits=None, trim=True):
        if digits is not None:
            return self.format_number(value, digits, trim=trim)
        uom_norm = (uom or "").strip().lower()
        if uom_norm in {"kg", "kgs", "kilogram", "kilograms"}:
            return self.format_kg(value)
        if uom_norm in {"m", "meter", "meters", "metre", "metres"}:
            return self.format_m(value)
        if uom_norm in {"m2", "mÂ²", "sqm", "sq.m", "square meter", "square meters"}:
            return self.format_m2(value)
        return self.format_integer(value)


def _normalize_stack_paper_role(role):
    """Map rich Engine V2 stack roles to paper roles used by resolution."""

    role_norm = (role or "").strip().lower()
    if role_norm in ("outer_liner", "inner_liner", "center_liner"):
        return "liner"
    if role_norm == "flute":
        return "medium"
    return role_norm


class CorrEngineV2DebugWizardSlittingOption(models.TransientModel):
    _name = "corr.engine_v2.debug.wizard.slitting.option"
    _description = "Engine V2 Debug Wizard Slitting Option"
    _order = "stage_sequence asc, option_rank asc, id asc"

    wizard_id = fields.Many2one(
        "corr.engine_v2.debug.wizard",
        string="Wizard",
        required=True,
        ondelete="cascade",
    )
    stage_sequence = fields.Integer(string="Stage Sequence", readonly=True)
    stage_code = fields.Char(string="Stage Code", readonly=True)
    stage_name = fields.Char(string="Stage Name", readonly=True)
    stage_type = fields.Char(string="Stage Type", readonly=True)
    stage_identity = fields.Char(string="Stage Identity", readonly=True)
    option_key = fields.Char(string="Option Key", readonly=True)
    option_rank = fields.Integer(string="Rank", readonly=True)
    up_count = fields.Integer(string="UPS", readonly=True)
    final_sheet_length_mm = fields.Float(string="Sheet Length (mm)", readonly=True)
    final_sheet_width_mm = fields.Float(string="Sheet Width (mm)", readonly=True)
    matched_workcenter_name = fields.Char(string="Workcenter", readonly=True)
    orientation_used = fields.Char(string="Orientation", readonly=True)
    rotated_used = fields.Boolean(string="Rotated", readonly=True)
    is_preferred = fields.Boolean(string="Preferred", readonly=True)
    is_selected = fields.Boolean(string="Selected", readonly=True)
    option_payload_json = fields.Text(string="Option Payload", readonly=True)

    def action_select_option(self):
        self.ensure_one()
        wizard = self.wizard_id
        if not wizard:
            return False

        siblings = wizard.slitting_option_line_ids.filtered(
            lambda line: line.stage_identity == self.stage_identity
        )
        siblings.write({"is_selected": False})
        self.write({"is_selected": True})
        wizard._refresh_slitting_option_selection_state()
        # Re-run the preview so RM resolution, raw_json, and downstream
        # Create-BoM inputs follow the updated route-level layout choice.
        return wizard.action_run_debug()


class CorrEngineV2DebugWizardAutoSplitOption(models.TransientModel):
    _name = "corr.engine_v2.debug.wizard.auto.split.option"
    _description = "Engine V2 Debug Wizard Auto Split Suggestion"
    _order = "option_rank asc, id asc"

    wizard_id = fields.Many2one(
        "corr.engine_v2.debug.wizard",
        string="Wizard",
        required=True,
        ondelete="cascade",
    )
    option_rank = fields.Integer(string="Rank", readonly=True)
    split_axis = fields.Selection([("width", "Width"), ("length", "Length")], readonly=True)
    split_method = fields.Char(string="Method", readonly=True)
    piece_a_size_mm = fields.Float(string="Piece A Size (mm)", readonly=True)
    piece_b_size_mm = fields.Float(string="Piece B Size (mm)", readonly=True)
    source_length_mm = fields.Float(string="Source Length (mm)", readonly=True)
    source_width_mm = fields.Float(string="Source Width (mm)", readonly=True)
    reason = fields.Char(string="Reason", readonly=True)
    is_selected = fields.Boolean(string="Accepted", readonly=True)

    def action_accept_suggestion(self):
        self.ensure_one()
        if self.is_selected:
            return False
        wizard = self.wizard_id
        if not wizard:
            return False
        if wizard.v2_spec_id:
            wizard._persist_accepted_auto_split(self)
            wizard.write(
                {
                    "multi_piece_enabled": False,
                    "multi_piece_source": "accepted_auto_split",
                    "multi_piece_strategy": "independent_routed_piece",
                    "multi_piece_split_axis": self.split_axis,
                    "multi_piece_split_mode": "equal",
                    "multi_piece_ratio_a": 1.0,
                    "multi_piece_ratio_b": 1.0,
                    "multi_piece_primary_size_mm": 0.0,
                    "multi_piece_piece_a_size_mm": 0.0,
                    "multi_piece_piece_b_size_mm": 0.0,
                    "multi_piece_split_after_stage_key": False,
                }
            )
            return wizard.action_run_debug()
        wizard.write(
            {
                "multi_piece_enabled": True,
                "multi_piece_source": "accepted_auto_split",
                "multi_piece_strategy": "independent_routed_piece",
                "multi_piece_split_axis": self.split_axis,
                "multi_piece_split_mode": "equal",
                "multi_piece_ratio_a": 1.0,
                "multi_piece_ratio_b": 1.0,
                "multi_piece_primary_size_mm": 0.0,
                "multi_piece_piece_a_size_mm": 0.0,
                "multi_piece_piece_b_size_mm": 0.0,
                "multi_piece_split_after_stage_key": False,
            }
        )
        return wizard.action_run_debug()

class CorrEngineV2DebugWizard(models.TransientModel):
    _name = "corr.engine_v2.debug.wizard"
    _description = "Engine V2 Debug / Preview"

    # Inputs
    spec_id = fields.Many2one("corr.spec.template", string="Legacy Spec")
    plant_id = fields.Many2one("corr.plant.param", string="Plant")
    order_qty = fields.Float(string="Order Quantity", default=1.0)
    cost_source = fields.Selection(
        [
            ("standard_price", "Standard Cost"),
            ("last_purchase_price", "Last Purchase Price"),
            ("vendor_price", "Vendor Price"),
        ],
        string="Cost Source",
        default="standard_price",
        help="Cost basis used for Engine V2 preview costing diagnostics.",
    )
    v2_spec_id = fields.Many2one(
        "corr.engine.v2.spec",
        string="Engine V2 Spec",
        help="Optional: when set, the wizard will validate manual-sheet layout requirements "
             "against this Engine V2 Spec and its Layout Policy before running the preview.",
    )
    preview_backend = fields.Selection(
        [
            ("legacy", "Legacy Preview"),
            ("stagecontext", "StageContext Preview"),
        ],
        string="Preview Backend",
        default="legacy",
        required=True,
        help="Legacy keeps the existing qgraph/bom_struct preview. StageContext uses the newer stage-based preview runtime for preview tabs only.",
    )
    bom_backend = fields.Selection(
        [
            ("legacy", "Legacy BoM"),
            ("stagecontext_shadow", "StageContext Shadow BoM"),
        ],
        string="BoM Backend",
        default="legacy",
        required=True,
        help="Debug-wizard-only BoM switch. Legacy uses bom_struct; StageContext Shadow uses the Phase 6A StageContext blueprint and reuses the same writer.",
    )

    # Preview state / feedback
    preview_is_current = fields.Boolean(string="Preview Up-to-date", default=False, readonly=True)
    preview_warning = fields.Text(string="Preview Status", readonly=True)
    preview_backend_applied = fields.Selection(
        [
            ("legacy", "Legacy Preview"),
            ("stagecontext", "StageContext Preview"),
        ],
        string="Applied Preview Backend",
        readonly=True,
    )

    # Outputs (rendered as HTML / text in tabs)
    summary_html = fields.Html(readonly=True)
    multi_piece_html = fields.Html(readonly=True)
    rm_breakdown_html = fields.Html(readonly=True)
    stage_costing_html = fields.Html(readonly=True, sanitize=False)
    costing_rm_total_display = fields.Char(string="Raw material total", readonly=True)
    costing_operation_total_display = fields.Char(string="Operation total", readonly=True)
    stage_flow_html = fields.Html(readonly=True)
    bom_preview_html = fields.Html(readonly=True)
    slitting_options_html = fields.Html(readonly=True)
    slitting_option_line_ids = fields.One2many(
        "corr.engine_v2.debug.wizard.slitting.option",
        "wizard_id",
        string="Slitting Options",
        readonly=True,
    )
    selected_slitting_options_json = fields.Text(readonly=True)
    layout_options_diagnostics_json = fields.Text(readonly=True)
    auto_split_suggestions_html = fields.Html(readonly=True)
    auto_split_diagnostics_json = fields.Text(readonly=True)
    auto_split_rejected_suggestions_json = fields.Text(readonly=True)
    auto_split_suggestion_line_ids = fields.One2many(
        "corr.engine_v2.debug.wizard.auto.split.option",
        "wizard_id",
        string="Auto Split Suggestions",
        readonly=True,
    )
    piece_layout_planning_html = fields.Html(readonly=True, sanitize=False)
    shared_upstream_split_html = fields.Html(readonly=True, sanitize=False)
    warnings_text = fields.Text(readonly=True)
    raw_json = fields.Text(readonly=True)
    raw_json_tools_html = fields.Html(readonly=True, sanitize=False)
    raw_json_summary_html = fields.Html(
        compute="_compute_raw_json_summary_html",
        readonly=True,
        sanitize=False,
    )

    use_plant_consumable_defaults = fields.Boolean(
        string="Use Plant Consumable Defaults",
        default=True,
    )
    enable_consumables = fields.Boolean(string="Enable Consumables")
    enable_corrugation_adhesive = fields.Boolean(string="Corrugation Adhesive")
    enable_lamination_adhesive = fields.Boolean(string="Lamination Adhesive")
    enable_printing_ink = fields.Boolean(string="Printing Ink")
    enable_lamination_film = fields.Boolean(string="Lamination Film")
    enable_stitching_wire = fields.Boolean(string="Stitching Wire")
    enable_jointing_glue = fields.Boolean(string="Jointing Glue")
    enable_jointing_tape = fields.Boolean(string="Jointing Tape")
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
    )

    # Preview-only multi-piece controls. These intentionally do not
    # persist onto the spec yet; they let users validate Phase 1 flow
    # before we add stored spec/template piece lines.
    multi_piece_enabled = fields.Boolean(string="Enable Multi-Piece Manufacturing")
    multi_piece_source = fields.Selection(
        [
            ("explicit_multi_piece_design", "Explicit Multi-Piece Design"),
            ("accepted_auto_split", "Accepted Auto Split"),
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
        [
            ("equal", "Equal"),
            ("ratio", "Ratio"),
            ("primary", "Primary Piece Size"),
            ("manual", "Manual Pieces"),
        ],
        string="Split Method",
        default="equal",
    )
    multi_piece_ratio_a = fields.Float(string="Ratio A", default=1.0)
    multi_piece_ratio_b = fields.Float(string="Ratio B", default=1.0)
    multi_piece_primary_piece = fields.Selection(
        [("piece_a", "Piece A"), ("piece_b", "Piece B")],
        string="Primary Piece",
        default="piece_a",
    )
    multi_piece_primary_size_mm = fields.Float(string="Primary Piece Size (mm)")
    multi_piece_piece_a_size_mm = fields.Float(string="Manual Piece A Size (mm)")
    multi_piece_piece_b_size_mm = fields.Float(string="Manual Piece B Size (mm)")
    multi_piece_merge_stage_key = fields.Char(string="Merge Stage", default="seaming")
    multi_piece_split_after_stage_key = fields.Char(string="Split After Stage")
    persistent_multi_piece_enabled = fields.Boolean(
        string="Persistent Multi-Piece Enabled",
        compute="_compute_persistent_multi_piece_state",
    )
    persistent_multi_piece_source = fields.Selection(
        [
            ("explicit_multi_piece_design", "Explicit Multi-Piece Design"),
            ("accepted_auto_split", "Accepted Auto Split"),
        ],
        string="Persistent Multi-Piece Source",
        compute="_compute_persistent_multi_piece_state",
    )

    @api.depends("v2_spec_id", "v2_spec_id.multi_piece_enabled", "v2_spec_id.multi_piece_source")
    def _compute_persistent_multi_piece_state(self):
        for wizard in self:
            spec = wizard.v2_spec_id
            wizard.persistent_multi_piece_enabled = bool(
                spec and spec.multi_piece_enabled
            )
            wizard.persistent_multi_piece_source = (
                spec.multi_piece_source if spec and spec.multi_piece_enabled else False
            )

    def _preview_formatter(self):
        self.ensure_one()
        plant = self.plant_id
        if not plant:
            plant = self.env["corr.plant.param"].sudo().search([], limit=1, order="id asc")
        return _EngineV2PreviewFormatter(plant)

    @api.model_create_multi
    def create(self, vals_list):
        copied_fields = set(self._consumable_field_names()) | set(
            self._business_process_field_names()
        )
        Spec = self.env["corr.engine.v2.spec"]
        for vals in vals_list:
            spec_id = vals.get("v2_spec_id") or self.env.context.get("default_v2_spec_id")
            if not spec_id:
                continue
            spec = Spec.browse(spec_id).exists()
            if not spec:
                continue
            defaults = self._consumable_defaults_from_v2_spec(spec)
            for field_name, value in defaults.items():
                if field_name in copied_fields:
                    vals.setdefault(field_name, value)
        return super().create(vals_list)

    def _active_spec_record(self):
        self.ensure_one()
        return self.v2_spec_id or self.spec_id

    def _active_fg_product_tmpl(self):
        self.ensure_one()
        spec = self._active_spec_record()
        return getattr(spec, "product_tmpl_id", False) if spec else False

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Default plant: first corr.plant.param when available.
        if "plant_id" in fields_list and not res.get("plant_id"):
            Plant = self.env["corr.plant.param"].sudo()
            plant = Plant.search([], limit=1, order="id asc")
            if plant:
                res["plant_id"] = plant.id
        # Default cost source from configuration when requested.
        if "cost_source" in fields_list and not res.get("cost_source"):
            icp = self.env["ir.config_parameter"].sudo()
            val = icp.get_param("corr_mrp_bridge.engine_v2_cost_source") or "standard_price"
            if val not in ("standard_price", "last_purchase_price", "vendor_price"):
                val = "standard_price"
            res["cost_source"] = val
        if "preview_backend" in fields_list and not res.get("preview_backend"):
            icp = self.env["ir.config_parameter"].sudo()
            val = (icp.get_param("corr_mrp_bridge.engine_v2_preview_backend") or "legacy").strip().lower()
            if val not in ("legacy", "stagecontext"):
                val = "legacy"
            res["preview_backend"] = val
        if "bom_backend" in fields_list and not res.get("bom_backend"):
            res["bom_backend"] = "legacy"
        default_v2_spec_id = res.get("v2_spec_id") or self.env.context.get("default_v2_spec_id")
        if default_v2_spec_id:
            spec = self.env["corr.engine.v2.spec"].browse(default_v2_spec_id).exists()
            if spec and getattr(spec, "multi_piece_enabled", False):
                res.update(
                    self._multi_piece_defaults_from_v2_spec(spec, fields_list=fields_list)
                )
            if spec:
                res.update(self._consumable_defaults_from_v2_spec(spec, fields_list=fields_list))
        return res

    @api.model
    def _consumable_field_names(self):
        return [
            "use_plant_consumable_defaults",
            "enable_consumables",
            "enable_corrugation_adhesive",
            "enable_lamination_adhesive",
            "enable_printing_ink",
            "enable_lamination_film",
            "enable_stitching_wire",
            "enable_jointing_glue",
            "enable_jointing_tape",
        ]

    @api.model
    def _business_process_field_names(self):
        return ["joining_type"]

    @api.model
    def _consumable_defaults_from_v2_spec(self, spec, fields_list=None):
        fields_set = set(fields_list or [])
        values = {}
        for name in self._consumable_field_names():
            if fields_set and name not in fields_set:
                continue
            if hasattr(spec, name):
                values[name] = bool(getattr(spec, name))
        for name in self._business_process_field_names():
            if fields_set and name not in fields_set:
                continue
            if hasattr(spec, name):
                values[name] = getattr(spec, name) or "none"
        return values

    def _sync_consumable_controls_from_v2_spec(self):
        for wizard in self:
            spec = wizard.v2_spec_id
            if not spec:
                continue
            for field_name, value in wizard._consumable_defaults_from_v2_spec(spec).items():
                if field_name in wizard._fields:
                    wizard[field_name] = value

    def _sync_consumable_controls_to_v2_spec(self):
        for wizard in self:
            spec = wizard.v2_spec_id
            if not spec:
                continue
            vals = {
                field_name: bool(wizard[field_name])
                for field_name in wizard._consumable_field_names()
                if field_name in wizard._fields and field_name in spec._fields
            }
            for field_name in wizard._business_process_field_names():
                if field_name in wizard._fields and field_name in spec._fields:
                    vals[field_name] = wizard[field_name] or "none"
            if vals:
                spec.with_context(
                    skip_board_recipe_snapshot=True,
                    skip_layout_auto_refresh=True,
                ).write(vals)

    def _effective_consumable_state(self, plant_profile=None, spec=None):
        if effective_consumable_state is None:
            return {}
        spec = spec or self.v2_spec_id
        options = {
            field_name: bool(self[field_name])
            for field_name in self._consumable_field_names()
            if field_name in self._fields
        }
        if "joining_type" in self._fields:
            options["joining_type"] = self.joining_type or "none"
        if bool(options.get("use_plant_consumable_defaults", True)):
            options.pop("enable_consumables", None)
            for field_name in list(CONSUMABLE_FLAG_FIELDS.values()):
                options.pop(field_name, None)
        return effective_consumable_state(
            plant_profile=plant_profile,
            spec=spec,
            options=options,
        )

    def _raw_consumable_system_param(self):
        try:
            return self.env["ir.config_parameter"].sudo().get_param("engine_v2_enable_consumables")
        except Exception:
            return None

    def _consumable_debug_metadata(self, state, spec=None):
        spec = spec or self.v2_spec_id
        return {
            "system_parameter_key": "engine_v2_enable_consumables",
            "system_parameter_raw": self._raw_consumable_system_param(),
            "use_plant_consumable_defaults": bool(self.use_plant_consumable_defaults),
            "spec_enable_consumables": bool(getattr(spec, "enable_consumables", False)) if spec else False,
            "joining_type": self.joining_type or getattr(spec, "joining_type", "none") or "none",
            "printing_required": bool(getattr(spec, "surface_printing_required", False)) if spec else False,
            "lamination_required": bool(getattr(spec, "surface_lamination_required", False)) if spec else False,
            "effective_enable_consumables": bool((state or {}).get("enable_consumables")),
            "effective_enable_lamination_film": bool((state or {}).get("enable_lamination_film")),
            "effective_state": dict(state or {}),
        }

    @api.model
    def _multi_piece_defaults_from_v2_spec(self, spec, fields_list=None):
        fields_set = set(fields_list or [])

        def _include(name):
            return not fields_set or name in fields_set

        values = {}
        if _include("multi_piece_enabled"):
            values["multi_piece_enabled"] = True
        if _include("multi_piece_source"):
            values["multi_piece_source"] = spec.multi_piece_source or "explicit_multi_piece_design"
        if _include("multi_piece_strategy"):
            values["multi_piece_strategy"] = (
                getattr(spec, "multi_piece_strategy", None) or "independent_routed_piece"
            )
        if _include("multi_piece_split_axis"):
            values["multi_piece_split_axis"] = spec.multi_piece_split_axis or "width"
        if _include("multi_piece_split_mode"):
            values["multi_piece_split_mode"] = spec.multi_piece_split_mode or "equal"
        if _include("multi_piece_merge_stage_key"):
            values["multi_piece_merge_stage_key"] = spec.multi_piece_merge_stage_key or "seaming"
        if _include("multi_piece_split_after_stage_key"):
            values["multi_piece_split_after_stage_key"] = (
                getattr(spec, "multi_piece_split_after_stage_key", None) or ""
            )
        return values

    def _sync_multi_piece_controls_from_v2_spec(self):
        for wizard in self:
            if wizard.env.context.get("skip_v2_spec_multi_piece_sync"):
                continue
            spec = wizard.v2_spec_id
            if not spec or not getattr(spec, "multi_piece_enabled", False):
                continue
            values = wizard._multi_piece_defaults_from_v2_spec(spec)
            for field_name, value in values.items():
                if field_name in wizard._fields:
                    wizard[field_name] = value

    def _sync_selected_layout_from_v2_spec(self):
        for wizard in self:
            spec = wizard.v2_spec_id
            if not spec:
                continue
            stage_identity = getattr(spec, "selected_layout_stage_identity", "") or ""
            option_json = getattr(spec, "selected_layout_option_json", "") or ""
            if not stage_identity or not option_json:
                continue
            try:
                option = json.loads(option_json or "{}")
            except Exception:
                option = {}
            if option:
                wizard.selected_slitting_options_json = json.dumps(
                    {stage_identity: option},
                    indent=2,
                    sort_keys=True,
                )

    def _sync_selected_layout_to_v2_spec(self, selected_layout=None):
        for wizard in self:
            spec = wizard.v2_spec_id
            if not spec:
                continue
            target = spec.with_context(
                skip_board_recipe_snapshot=True,
                skip_layout_auto_refresh=True,
            )
            layout = dict(selected_layout or {})
            stage_identity = layout.get("stage_identity") or ""
            stage_entry = dict(layout.get("stage_entry") or {})
            option = dict(layout.get("option") or {})
            if not stage_identity or not option:
                target.write(
                    {
                        "selected_layout_stage_identity": False,
                        "selected_layout_option_json": False,
                        "selected_layout_selection_mode": False,
                        "multi_piece_split_stale": False,
                    }
                )
                continue
            payload = {
                "stage_template_id": stage_entry.get("stage_template_id"),
                "stage_contract_code": stage_entry.get("stage_contract_code"),
                "stage_code": stage_entry.get("stage_code") or option.get("stage_code"),
                "stage_name": stage_entry.get("stage_name") or option.get("stage_name"),
                "stage_type": stage_entry.get("stage_type") or option.get("stage_type"),
                **option,
            }
            target.write(
                {
                    "selected_layout_stage_identity": stage_identity,
                    "selected_layout_option_json": json.dumps(
                        payload, indent=2, sort_keys=True
                    ),
                    "selected_layout_selection_mode": "manual",
                    "multi_piece_split_stale": False,
                }
            )

    @api.onchange("v2_spec_id")
    def _onchange_v2_spec_multi_piece_controls(self):
        for wizard in self:
            wizard._sync_multi_piece_controls_from_v2_spec()
            wizard._sync_selected_layout_from_v2_spec()
            wizard._sync_consumable_controls_from_v2_spec()

    @api.onchange(
        "spec_id",
        "plant_id",
        "order_qty",
        "v2_spec_id",
        "preview_backend",
        "multi_piece_enabled",
        "multi_piece_split_axis",
        "multi_piece_strategy",
        "multi_piece_split_mode",
        "multi_piece_ratio_a",
        "multi_piece_ratio_b",
        "multi_piece_primary_piece",
        "multi_piece_primary_size_mm",
        "multi_piece_piece_a_size_mm",
        "multi_piece_piece_b_size_mm",
        "multi_piece_merge_stage_key",
        "multi_piece_split_after_stage_key",
        "use_plant_consumable_defaults",
        "enable_consumables",
        "enable_corrugation_adhesive",
        "enable_lamination_adhesive",
        "enable_printing_ink",
        "enable_lamination_film",
        "enable_stitching_wire",
        "enable_jointing_glue",
        "enable_jointing_tape",
        "joining_type",
    )
    def _onchange_inputs_mark_preview_stale(self):
        for wizard in self:
            wizard.preview_is_current = False
            wizard.preview_warning = _(
                "The preview is stale because spec, plant, order quantity, or preview backend changed. "
                "Please run the Engine V2 preview again before creating a BoM."
            )
            wizard.preview_backend_applied = False
            wizard.summary_html = False
            wizard.multi_piece_html = False
            wizard.rm_breakdown_html = False
            wizard.stage_costing_html = False
            wizard.costing_rm_total_display = False
            wizard.costing_operation_total_display = False
            wizard.stage_flow_html = False
            wizard.bom_preview_html = False
            wizard.slitting_options_html = False
            wizard.slitting_option_line_ids = [(5, 0, 0)]
            wizard.selected_slitting_options_json = False
            wizard.layout_options_diagnostics_json = False
            wizard.auto_split_suggestions_html = False
            wizard.auto_split_diagnostics_json = False
            wizard.auto_split_rejected_suggestions_json = False
            wizard.auto_split_suggestion_line_ids = [(5, 0, 0)]
            wizard.piece_layout_planning_html = False
            wizard.shared_upstream_split_html = False
            wizard.warnings_text = False
            wizard.raw_json = False
            wizard.raw_json_tools_html = False

    def _ensure_engine_available(self):
        if not (engine_v2_compute and build_engine_spec_input and build_plant_profile and build_stage_graph and build_bom_structure):
            raise UserError(
                _(
                    "Engine V2 debug tools are not available. "
                    "Please ensure the corr_mrp_bridge Engine V2 components are installed."
                )
            )
        self._patch_engine_v2_flow_product_validation()

    def _patch_engine_v2_flow_product_validation(self):
        module_name = getattr(engine_v2_compute, "__module__", "")
        engine_module = sys.modules.get(module_name)
        if engine_module is None:
            return False

        EngineResult = getattr(engine_module, "EngineResult", None)
        EngineProduct = getattr(engine_module, "EngineProduct", None)
        ROLE_SFG = getattr(engine_module, "ROLE_SFG", "sfg")
        if not EngineResult or not EngineProduct:
            return False
        if getattr(EngineResult, "_corr_v2_flow_product_validation_patched", False):
            return True

        original_validate = getattr(EngineResult, "validate", None)

        def _validate_with_flow_sfg_registration(result):
            product_keys = {
                product.key for product in list(getattr(result, "products", []) or [])
            }
            for edge in list(getattr(getattr(result, "stage_graph", None), "flows", []) or []):
                key = str(getattr(edge, "product_key", "") or "")
                if key.startswith("sfg_") and key not in product_keys:
                    product_keys.add(key)
                    result.products.append(
                        EngineProduct(
                            key=key,
                            role=ROLE_SFG,
                            description="SFG for %s" % key[4:].replace("__", " / "),
                            uom="unit",
                        )
                    )
            if original_validate:
                try:
                    return original_validate(result)
                except ValueError as exc:
                    message = str(exc)
                    if "Flow uses unknown product_key" not in message:
                        raise
                    repaired = False
                    product_keys = {
                        product.key
                        for product in list(getattr(result, "products", []) or [])
                    }
                    for edge in list(
                        getattr(getattr(result, "stage_graph", None), "flows", []) or []
                    ):
                        key = str(getattr(edge, "product_key", "") or "")
                        if key.startswith("sfg_") and key not in product_keys:
                            product_keys.add(key)
                            repaired = True
                            result.products.append(
                                EngineProduct(
                                    key=key,
                                    role=ROLE_SFG,
                                    description="SFG for %s" % key[4:].replace("__", " / "),
                                    uom="unit",
                                )
                            )
                    if repaired:
                        return original_validate(result)
                    raise
            return None

        EngineResult.validate = _validate_with_flow_sfg_registration
        EngineResult._corr_v2_flow_product_validation_patched = True
        return True

    def _build_shadow_product_template_map(self, shadow_runtime):
        ids = set()
        runtime = shadow_runtime or {}
        for item in runtime.get("material_requirements") or []:
            try:
                product_tmpl_id = int(item.get("product_tmpl_id") or 0)
            except Exception:
                product_tmpl_id = 0
            if product_tmpl_id:
                ids.add(product_tmpl_id)
        for ctx in runtime.get("stage_contexts") or []:
            for field_name in ("rm_attachments", "consumable_attachments", "intermediate_attachments"):
                for att in ctx.get(field_name) or []:
                    try:
                        product_tmpl_id = int(att.get("product_tmpl_id") or 0)
                    except Exception:
                        product_tmpl_id = 0
                    if product_tmpl_id:
                        ids.add(product_tmpl_id)

        if not ids:
            return {}

        ProductTemplate = self.env["product.template"].sudo()
        records = ProductTemplate.browse(sorted(ids)).exists()
        return {rec.id: (rec.display_name or rec.name or str(rec.id)) for rec in records}

    def _build_consumable_profile_product_map(self):
        def _key_for_role(role):
            text = str(role or "").strip().lower()
            if "ink" in text or "print" in text:
                return "ink"
            if "wire" in text or "stitch" in text:
                return "wire"
            if "tape" in text:
                return "tape"
            if "film" in text or "lamination" in text:
                return "lamination"
            if "adhesive" in text or "glue" in text or "gum" in text:
                if "corrugation" in text:
                    return "adhesive_corrugation"
                if "fg" in text or "joint" in text or "seam" in text:
                    return "adhesive_fg"
                return "adhesive_board"
            return ""

        try:
            profiles = self.env["corr.engine.material.profile"].sudo().search(
                [
                    ("active", "=", True),
                    ("material_type", "=", "consumable"),
                    ("role", "!=", False),
                ],
                order="id",
            )
        except Exception:
            return {}
        grouped = {}
        for profile in profiles:
            key = _key_for_role(profile.role)
            tmpl = profile.product_tmpl_id
            if not key or not tmpl:
                continue
            grouped.setdefault(key, []).append(profile)
        result = {}
        for key, matches in grouped.items():
            if len(matches) != 1:
                continue
            tmpl = matches[0].product_tmpl_id
            variant = tmpl.product_variant_id if tmpl else False
            uom = (variant.uom_id if variant else False) or tmpl.uom_id
            result[key] = {
                "product_tmpl_id": tmpl.id,
                "product_label": tmpl.display_name or tmpl.name or str(tmpl.id),
                "uom_id": uom.id if uom else None,
                "uom_name": uom.display_name if uom else "",
            }
        return result

    def _build_stagecontext_preview_model(self, shadow_runtime, result, costing, qgraph=None, paper_resolution=None):
        if not shadow_runtime or engine_v2_preview_adapter is None:
            return None

        spec = self._active_spec_record()
        plant = self.plant_id
        try:
            preview_model = engine_v2_preview_adapter.build_preview_model(
                shadow_runtime,
                spec_name=getattr(spec, "name", "") or "",
                style=getattr(spec, "style", "") or "",
                plant_name=getattr(plant, "name", "") or "",
                order_qty=float(self.order_qty or 0.0),
                metrics=getattr(result, "metrics", None),
                costing=costing,
                rm_requirements=getattr(result, "rm_requirements", []) or [],
                product_templates=self._build_shadow_product_template_map(shadow_runtime),
                consumable_profile_products=self._build_consumable_profile_product_map(),
                paper_resolution=paper_resolution,
                qgraph=qgraph or getattr(result, "quantity_stage_graph", None),
                summary_context=self._build_preview_summary_context(
                    result=result,
                    qgraph=qgraph or getattr(result, "quantity_stage_graph", None),
                    costing=costing,
                ),
            )
            self._enrich_preview_model_top_sheet_products(preview_model)
            return preview_model
        except Exception:  # pragma: no cover - defensive best-effort
            _logger.exception("Phase 5 preview adapter failed in debug wizard")
            return None

    def _enrich_preview_model_top_sheet_products(self, preview_model):
        if not isinstance(preview_model, dict):
            return
        materials = list(preview_model.get("materials") or [])
        product_ids = set()
        for row in materials:
            metadata = dict((row or {}).get("metadata") or {})
            try:
                product_id = int(metadata.get("top_sheet_product_id") or metadata.get("product_id") or 0)
            except Exception:
                product_id = 0
            role = str((row or {}).get("role") or metadata.get("semantic_role") or "").strip().lower()
            if role == "top_sheet_external" and product_id:
                product_ids.add(product_id)
        if not product_ids:
            return
        products = self.env["product.product"].sudo().browse(sorted(product_ids)).exists()
        product_name_by_id = {product.id: (product.display_name or product.default_code or product.name or str(product.id)) for product in products}
        for row in materials:
            metadata = dict((row or {}).get("metadata") or {})
            role = str((row or {}).get("role") or metadata.get("semantic_role") or "").strip().lower()
            if role != "top_sheet_external":
                continue
            try:
                product_id = int(metadata.get("top_sheet_product_id") or metadata.get("product_id") or 0)
            except Exception:
                product_id = 0
            product_name = product_name_by_id.get(product_id)
            if not product_name:
                continue
            row["product"] = product_name
            metadata.setdefault("product_id", product_id)
            metadata["product_name"] = product_name
            metadata["product_label"] = product_name
            if product_id and not metadata.get("writer_product_key"):
                metadata["writer_product_key"] = "product.product:%s" % product_id
            row["metadata"] = metadata

    def _reopen_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }

    def _make_slitting_option_key(self, stage_entry, option):
        stage_code = (stage_entry or {}).get("stage_code") or ""
        matched = (option or {}).get("matched_workcenter") or {}
        key_struct = {
            "stage_code": stage_code,
            "rank": int((option or {}).get("rank") or 0),
            "up_count": int((option or {}).get("up_count") or 0),
            "final_sheet_length_mm": float((option or {}).get("final_sheet_length_mm", 0.0) or 0.0),
            "final_sheet_width_mm": float((option or {}).get("final_sheet_width_mm", 0.0) or 0.0),
            "workcenter_id": int(matched.get("workcenter_id") or 0),
            "orientation_used": (option or {}).get("orientation_used") or "",
        }
        return json.dumps(key_struct, sort_keys=True)

    def _serialize_selected_slitting_options(self):
        self.ensure_one()
        selected_map = {}
        for line in self.slitting_option_line_ids.filtered(lambda item: item.is_selected):
            try:
                payload = json.loads(line.option_payload_json or "{}")
            except Exception:
                payload = {}
            payload.setdefault("stage_code", line.stage_code or "")
            payload.setdefault("stage_name", line.stage_name or "")
            payload.setdefault("stage_type", line.stage_type or "")
            selected_map[line.stage_identity or line.stage_code or str(line.id)] = payload
        return json.dumps(selected_map, indent=2, sort_keys=True)

    def _deserialize_selected_slitting_options(self):
        self.ensure_one()
        raw = (self.selected_slitting_options_json or "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _deserialize_debug_json(self, raw_value):
        raw = (raw_value or "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _resolve_layout_option_selection(self, shadow_runtime):
        """Return the active route-level layout option for the current preview."""
        self.ensure_one()
        slitting_options = (shadow_runtime or {}).get("slitting_options") or {}
        stages = list(slitting_options.get("stages") or [])
        layout_driving_stage = dict(slitting_options.get("layout_driving_stage") or {})
        if not stages or not layout_driving_stage:
            return {}

        layout_identity = (
            layout_driving_stage.get("stage_contract_code")
            or layout_driving_stage.get("stage_code")
            or str(layout_driving_stage.get("stage_template_id") or "")
        )
        if not layout_identity:
            return {}

        stage_entry = next(
            (
                item
                for item in stages
                if (
                    item.get("stage_contract_code")
                    or item.get("stage_code")
                    or str(item.get("stage_template_id") or "")
                ) == layout_identity
            ),
            {},
        )
        if not stage_entry:
            return {}

        feasible_options = list(stage_entry.get("feasible_options") or [])
        selected_map = self._deserialize_selected_slitting_options()
        selected_payload = dict(selected_map.get(layout_identity) or {})
        if selected_payload:
            selected_key = self._make_slitting_option_key(stage_entry, selected_payload)
            matched_option = next(
                (
                    option
                    for option in feasible_options
                    if self._make_slitting_option_key(stage_entry, option) == selected_key
                ),
                None,
            )
            if matched_option:
                return {
                    "stage_identity": layout_identity,
                    "stage_entry": dict(stage_entry),
                    "option": dict(matched_option),
                    "selection_source": "wizard_selected_option",
                }

        preferred_option = dict(stage_entry.get("preferred_option") or {})
        if preferred_option:
            return {
                "stage_identity": layout_identity,
                "stage_entry": dict(stage_entry),
                "option": preferred_option,
                "selection_source": "runtime_preferred_option",
            }

        if feasible_options:
            return {
                "stage_identity": layout_identity,
                "stage_entry": dict(stage_entry),
                "option": dict(feasible_options[0]),
                "selection_source": "first_feasible_option_fallback",
            }
        return {}

    def _apply_selected_layout_to_metrics(self, result, layout_selection):
        """Keep preview summary metrics aligned with the selected layout option."""

        option = dict((layout_selection or {}).get("option") or {})
        up_count = int(option.get("up_count") or 0)
        if up_count <= 0:
            return
        metrics = getattr(result, "metrics", None)
        if not metrics:
            return
        extra = dict(getattr(metrics, "extra", {}) or {})
        extra["layout_blanks_per_sheet"] = up_count
        extra["boxes_per_sheet"] = float(up_count)
        extra["effective_ups"] = up_count
        if option.get("final_sheet_width_mm"):
            extra["selected_layout_sheet_width_mm"] = float(option.get("final_sheet_width_mm") or 0.0)
        if option.get("final_sheet_length_mm"):
            extra["selected_layout_sheet_length_mm"] = float(option.get("final_sheet_length_mm") or 0.0)
        extra["selected_layout_up_count"] = up_count
        extra["selected_layout_orientation"] = option.get("orientation_used") or ""
        metrics.extra = extra

    def _render_slitting_options_html(self):
        self.ensure_one()
        fmt = self._preview_formatter()
        lines = self.slitting_option_line_ids.sorted(
            key=lambda line: (line.stage_sequence, line.option_rank or 9999, line.id)
        )
        if not lines:
            diagnostics = self._deserialize_debug_json(self.layout_options_diagnostics_json)
            html = ["<p>%s</p>" % _("No feasible slitting/layout options were exposed for this preview.")]
            reason = diagnostics.get("reason")
            if reason:
                html.append(
                    "<div class='alert alert-warning' role='alert'><strong>%s:</strong> %s</div>"
                    % (_("Reason"), html_escape(str(reason)))
                )
            html.append(self._render_layout_stage_diagnostics_html(diagnostics))
            return "\n".join(html)

        grouped = defaultdict(list)
        for line in lines:
            grouped[line.stage_identity or line.stage_code or str(line.id)].append(line)

        html = ["<h3>%s</h3>" % _("Layout Options")]
        html.append(
            "<p>%s</p>"
            % _(
                "Preferred options are highlighted below. Use the Select action in the table to override the current choice."
            )
        )
        primary_group = next(iter(grouped.values()), [])
        stage_lines = sorted(primary_group, key=lambda line: (line.option_rank or 9999, line.id))
        if not stage_lines:
            return "<p>%s</p>" % _(
                "No feasible slitting/layout options were exposed for this preview."
            )

        anchor = stage_lines[0]
        selected = next((line for line in stage_lines if line.is_selected), None) or anchor
        preferred = next((line for line in stage_lines if line.is_preferred), None) or anchor
        alternatives = [line for line in stage_lines if line.id not in {preferred.id, selected.id}]

        html.append("<div class='card mb-3'>")
        html.append("<div class='card-body'>")
        html.append(
            "<div class='alert alert-info' role='alert'><strong>%s:</strong> %s-up | %s: %s x %s mm | %s: %s</div>"
            % (
                _("Preferred"),
                fmt.format_integer(preferred.up_count or 0),
                _("Sheet"),
                fmt.format_integer(preferred.final_sheet_length_mm or 0.0),
                fmt.format_integer(preferred.final_sheet_width_mm or 0.0),
                _("Orientation"),
                preferred.orientation_used or "-",
            )
        )
        html.append(
            "<div class='alert alert-success' role='alert'><strong>%s:</strong> %s-up | %s: %s x %s mm | %s: %s</div>"
            % (
                _("Selected"),
                fmt.format_integer(selected.up_count or 0),
                _("Sheet"),
                fmt.format_integer(selected.final_sheet_length_mm or 0.0),
                fmt.format_integer(selected.final_sheet_width_mm or 0.0),
                _("Orientation"),
                selected.orientation_used or "-",
            )
        )
        if alternatives:
            html.append("<h5>%s</h5>" % _("Other Feasible Options"))
            html.append(
                "<table class='table table-sm table-striped'><thead><tr>"
                "<th>%s</th><th>%s</th><th>%s</th>"
                "</tr></thead><tbody>"
                % (
                    _("Slitting UPS"),
                    _("Sheet Size"),
                    _("Orientation"),
                )
            )
            for line in alternatives:
                html.append(
                    "<tr><td>%s</td><td>%s x %s mm</td><td>%s</td></tr>"
                    % (
                        fmt.format_integer(line.up_count or 0),
                        fmt.format_integer(line.final_sheet_length_mm or 0.0),
                        fmt.format_integer(line.final_sheet_width_mm or 0.0),
                        line.orientation_used or "-",
                    )
                )
            html.append("</tbody></table>")
        else:
            html.append("<p>%s</p>" % _("No alternative feasible options were exposed for this layout."))
        html.append("</div></div>")

        return "\n".join(html)

    def _render_layout_stage_diagnostics_html(self, diagnostics):
        diagnostics = dict(diagnostics or {})
        stages = list(
            diagnostics.get("displayed_stage_details")
            or diagnostics.get("all_stage_details")
            or []
        )
        if not stages:
            return ""
        fmt = self._preview_formatter()
        html = ["<h4>%s</h4>" % _("Stage-wise Full Blank Feasibility")]
        html.append(
            "<table class='table table-sm table-striped'>"
            "<thead><tr>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "</tr></thead><tbody>"
            % (
                _("Stage"),
                _("Workcenter"),
                _("Blank Checked"),
                _("Width / Deckle Limits"),
                _("Length Limits"),
                _("Result"),
            )
        )
        for stage in stages:
            stage_label = "%s%s" % (
                stage.get("stage_name") or stage.get("stage_code") or "-",
                (" (%s)" % stage.get("capability_code")) if stage.get("capability_code") else "",
            )
            checks = list(stage.get("option_checks") or [])
            if not checks:
                reasons = "; ".join(stage.get("rejection_reasons") or []) or stage.get("status") or "-"
                html.append(
                    "<tr><td>%s</td><td>-</td><td>%s x %s mm</td><td>-</td><td>-</td><td>%s</td></tr>"
                    % (
                        html_escape(stage_label),
                        fmt.format_integer(stage.get("blank_length_mm") or 0.0),
                        fmt.format_integer(stage.get("blank_width_mm") or 0.0),
                        html_escape(str(reasons)),
                    )
                )
                continue
            for check in checks:
                dimensions = list(check.get("dimension_checks") or [])
                first_dim = dimensions[0] if dimensions else {}
                length = first_dim.get("length_mm") or stage.get("blank_length_mm") or 0.0
                width = first_dim.get("width_mm") or stage.get("blank_width_mm") or 0.0
                width_limits = "%s-%s mm" % (
                    fmt.format_integer(check.get("width_min_mm") or 0.0)
                    if check.get("width_min_mm")
                    else "-",
                    fmt.format_integer(check.get("width_max_mm") or 0.0)
                    if check.get("width_max_mm")
                    else "-",
                )
                if check.get("width_min_cm") or check.get("width_max_cm"):
                    width_limits += " (%s-%s cm)" % (
                        fmt.format_number(check.get("width_min_cm") or 0.0, digits=1)
                        if check.get("width_min_cm")
                        else "-",
                        fmt.format_number(check.get("width_max_cm") or 0.0, digits=1)
                        if check.get("width_max_cm")
                        else "-",
                    )
                length_limits = "%s-%s mm" % (
                    fmt.format_integer(check.get("length_min_mm") or 0.0)
                    if check.get("length_min_mm")
                    else "-",
                    fmt.format_integer(check.get("length_max_mm") or 0.0)
                    if check.get("length_max_mm")
                    else "-",
                )
                if check.get("length_min_mm") or check.get("length_max_mm"):
                    length_limits += " (%s-%s cm)" % (
                        fmt.format_number((check.get("length_min_mm") or 0.0) / 10.0, digits=1)
                        if check.get("length_min_mm")
                        else "-",
                        fmt.format_number((check.get("length_max_mm") or 0.0) / 10.0, digits=1)
                        if check.get("length_max_mm")
                        else "-",
                    )
                reasons = "; ".join(check.get("rejection_reasons") or [])
                status = check.get("status") or ""
                result = _("Pass") if status == "feasible" else _("Fail")
                if reasons:
                    result = "%s: %s" % (result, reasons)
                html.append(
                    "<tr><td>%s</td><td>%s</td><td>%s x %s mm</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (
                        html_escape(stage_label),
                        html_escape(check.get("workcenter_name") or "-"),
                        fmt.format_integer(length),
                        fmt.format_integer(width),
                        html_escape(width_limits),
                        html_escape(length_limits),
                        html_escape(result),
                    )
                )
        html.append("</tbody></table>")
        return "\n".join(html)

    def _refresh_slitting_option_selection_state(self):
        self.ensure_one()
        self.selected_slitting_options_json = self._serialize_selected_slitting_options()
        self.slitting_options_html = self._render_slitting_options_html()

    def _slitting_stage_diagnostic_payload(self, stage_entry):
        stage_entry = dict(stage_entry or {})
        blank = dict(stage_entry.get("blank_dimensions") or {})
        option_checks = []
        for option in list(stage_entry.get("options") or []):
            if int(option.get("up_count") or 0) != 1:
                continue
            for candidate in list(option.get("candidate_results") or []):
                constraints = dict(candidate.get("resolved_constraints") or {})
                dimension_checks = list(candidate.get("dimension_checks") or [])
                option_checks.append(
                    {
                        "workcenter_id": candidate.get("workcenter_id"),
                        "workcenter_name": candidate.get("workcenter_name") or "",
                        "status": candidate.get("status") or "",
                        "operation_name": candidate.get("operation_name") or "",
                        "source": candidate.get("source") or "",
                        "width_min_cm": constraints.get("width_min_cm"),
                        "width_max_cm": constraints.get("width_max_cm"),
                        "width_min_mm": (
                            float(constraints.get("width_min_cm") or 0.0) * 10.0
                            if constraints.get("width_min_cm")
                            else None
                        ),
                        "width_max_mm": (
                            float(constraints.get("width_max_cm") or 0.0) * 10.0
                            if constraints.get("width_max_cm")
                            else None
                        ),
                        "length_min_mm": constraints.get("length_min_mm"),
                        "length_max_mm": constraints.get("length_max_mm"),
                        "required_flutes": list(constraints.get("required_flutes") or []),
                        "supported_flutes": list(constraints.get("supported_flutes") or []),
                        "dimension_checks": dimension_checks,
                        "rejection_reasons": list(candidate.get("rejection_reasons") or []),
                    }
                )
            break
        return {
            "stage_template_id": stage_entry.get("stage_template_id"),
            "stage_contract_code": stage_entry.get("stage_contract_code"),
            "stage_code": stage_entry.get("stage_code") or "",
            "stage_name": stage_entry.get("stage_name") or "",
            "stage_type": stage_entry.get("stage_type") or "",
            "capability_code": stage_entry.get("capability_code") or "",
            "sequence": stage_entry.get("sequence"),
            "status": stage_entry.get("status") or "",
            "rejection_reasons": list(stage_entry.get("rejection_reasons") or []),
            "blank_length_mm": blank.get("blank_length_mm"),
            "blank_width_mm": blank.get("blank_width_mm"),
            "rotation_allowed": blank.get("rotation_allowed"),
            "option_checks": option_checks,
            "drives_layout_selection": bool(stage_entry.get("drives_layout_selection")),
            "is_layout_driving_stage": bool(stage_entry.get("is_layout_driving_stage")),
        }

    def _load_slitting_option_selection_ui(self, shadow_runtime, selected_map=None):
        self.ensure_one()
        slitting_options = (shadow_runtime or {}).get("slitting_options") or {}
        all_stages = list(slitting_options.get("stages") or [])
        stages = list(all_stages)
        diagnostics = {
            "layout_driving_stage": dict(slitting_options.get("layout_driving_stage") or {}),
            "stage_count": len(all_stages),
            "summary": dict(slitting_options.get("summary") or {}),
            "all_stage_details": [
                self._slitting_stage_diagnostic_payload(stage_entry)
                for stage_entry in all_stages
            ],
        }
        selected_map = dict(selected_map or self._deserialize_selected_slitting_options())
        layout_driving_stage = dict(slitting_options.get("layout_driving_stage") or {})
        layout_driving_identity = (
            layout_driving_stage.get("stage_contract_code")
            or layout_driving_stage.get("stage_code")
            or str(layout_driving_stage.get("stage_template_id") or "")
        )
        if layout_driving_identity:
            stages = [
                stage_entry
                for stage_entry in stages
                if (
                    stage_entry.get("stage_contract_code")
                    or stage_entry.get("stage_code")
                    or str(stage_entry.get("stage_template_id") or "")
                ) == layout_driving_identity
            ]
        elif stages:
            stages = [stages[0]]
        diagnostics["displayed_stage_count"] = len(stages)
        diagnostics["displayed_stage_codes"] = [
            stage_entry.get("stage_code") or stage_entry.get("stage_contract_code") or ""
            for stage_entry in stages
        ]
        commands = [(5, 0, 0)]
        for stage_entry in stages:
            stage_identity = (
                stage_entry.get("stage_contract_code")
                or stage_entry.get("stage_code")
                or str(stage_entry.get("stage_template_id") or "")
            )
            feasible_options = list(stage_entry.get("feasible_options") or [])
            preferred_key = None
            preferred_option = stage_entry.get("preferred_option") or {}
            if preferred_option:
                preferred_key = self._make_slitting_option_key(stage_entry, preferred_option)
            selected_key = None
            selected_payload = dict(selected_map.get(stage_identity) or {})
            if selected_payload:
                selected_key = self._make_slitting_option_key(stage_entry, selected_payload)
                valid_keys = {
                    self._make_slitting_option_key(stage_entry, option)
                    for option in feasible_options
                }
                if selected_key not in valid_keys:
                    selected_key = None
            for option in feasible_options:
                option_key = self._make_slitting_option_key(stage_entry, option)
                matched = option.get("matched_workcenter") or {}
                commands.append(
                    (
                        0,
                        0,
                        {
                            "stage_sequence": int(stage_entry.get("sequence") or 0),
                            "stage_code": stage_entry.get("stage_code") or "",
                            "stage_name": stage_entry.get("stage_name") or "",
                            "stage_type": stage_entry.get("stage_type") or "",
                            "stage_identity": stage_identity,
                            "option_key": option_key,
                            "option_rank": int(option.get("rank") or 0),
                            "up_count": int(option.get("up_count") or 0),
                            "final_sheet_length_mm": float(option.get("final_sheet_length_mm", 0.0) or 0.0),
                            "final_sheet_width_mm": float(option.get("final_sheet_width_mm", 0.0) or 0.0),
                            "matched_workcenter_name": matched.get("workcenter_name") or "",
                            "orientation_used": option.get("orientation_used") or "",
                            "rotated_used": bool(option.get("rotated_used")),
                            "is_preferred": bool(preferred_key and option_key == preferred_key),
                            "is_selected": bool(
                                (selected_key and option_key == selected_key)
                                or (not selected_key and preferred_key and option_key == preferred_key)
                            ),
                            "option_payload_json": json.dumps(
                                {
                                    "stage_template_id": stage_entry.get("stage_template_id"),
                                    "stage_contract_code": stage_entry.get("stage_contract_code"),
                                    "stage_code": stage_entry.get("stage_code"),
                                    "stage_name": stage_entry.get("stage_name"),
                                    "stage_type": stage_entry.get("stage_type"),
                                    **option,
                                },
                                sort_keys=True,
                            ),
                        },
                    )
                )
        self.slitting_option_line_ids = commands
        diagnostics["displayed_option_count"] = max(0, len(commands) - 1)
        if not stages:
            diagnostics["reason"] = "no_layout_stages_in_slitting_options"
        elif diagnostics["displayed_option_count"] <= 0:
            diagnostics["reason"] = "layout_stages_present_but_no_feasible_options"
            diagnostics["displayed_stage_details"] = [
                self._slitting_stage_diagnostic_payload(stage_entry)
                for stage_entry in stages
            ]
        else:
            diagnostics["reason"] = "layout_options_available"
        try:
            self.layout_options_diagnostics_json = json.dumps(diagnostics, indent=2, sort_keys=True)
        except Exception:
            self.layout_options_diagnostics_json = False
        self._refresh_slitting_option_selection_state()

    def _build_multi_piece_options(self, spec_input):
        self.ensure_one()
        if not self.multi_piece_enabled:
            return None

        split_axis = (self.multi_piece_split_axis or "width").strip().lower()
        split_mode_ui = (self.multi_piece_split_mode or "equal").strip().lower()
        split_mode = "equal" if split_mode_ui == "equal" else "uneven"
        merge_stage_key = (self.multi_piece_merge_stage_key or "seaming").strip() or "seaming"
        try:
            blank_length = float(getattr(spec_input, "blank_length_mm", 0.0) or 0.0)
            blank_width = float(getattr(spec_input, "blank_width_mm", 0.0) or 0.0)
        except Exception:
            blank_length = 0.0
            blank_width = 0.0
        payload = {
            "enabled": True,
            "source": self.multi_piece_source or "explicit_multi_piece_design",
            "status": "accepted",
            "is_design_intent": (self.multi_piece_source or "explicit_multi_piece_design")
            == "explicit_multi_piece_design",
            "materialization_allowed": False,
            "piece_count": 2,
            "multi_piece_strategy": self.multi_piece_strategy or "independent_routed_piece",
            "strategy": self.multi_piece_strategy or "independent_routed_piece",
            "split_after_stage_key": (self.multi_piece_split_after_stage_key or "").strip(),
            "split_axis": split_axis,
            "split_mode": split_mode,
            "split_method": split_mode_ui,
            "merge_stage_key": merge_stage_key,
        }
        if blank_length > 0.0:
            payload["source_length_mm"] = blank_length
        if blank_width > 0.0:
            payload["source_width_mm"] = blank_width

        if split_mode_ui == "equal":
            return payload

        if split_mode_ui == "ratio":
            ratio_a = float(self.multi_piece_ratio_a or 0.0)
            ratio_b = float(self.multi_piece_ratio_b or 0.0)
            if ratio_a <= 0.0 or ratio_b <= 0.0:
                raise UserError(_("Ratio split requires positive Ratio A and Ratio B values."))
            payload["ratio_a"] = ratio_a
            payload["ratio_b"] = ratio_b
            return payload
        elif split_mode_ui == "primary":
            primary_size = float(self.multi_piece_primary_size_mm or 0.0)
            if primary_size <= 0.0:
                raise UserError(_("Primary piece size must be positive."))
            payload["primary_piece_key"] = self.multi_piece_primary_piece or "piece_a"
            payload["primary_size_mm"] = primary_size
            return payload
        elif split_mode_ui == "manual":
            size_a = float(self.multi_piece_piece_a_size_mm or 0.0)
            size_b = float(self.multi_piece_piece_b_size_mm or 0.0)
            if size_a <= 0.0 or size_b <= 0.0:
                raise UserError(_("Manual multi-piece split requires positive Piece A and Piece B sizes."))
            payload["split_sizes_mm"] = [size_a, size_b]
        else:
            raise UserError(_("Unsupported multi-piece split method: %s") % split_mode_ui)

        return payload

    def _persist_accepted_auto_split(self, option):
        self.ensure_one()
        spec = self.v2_spec_id
        if not spec:
            return False

        axis = (option.split_axis or "width").strip().lower()
        source_length = float(option.source_length_mm or 0.0)
        source_width = float(option.source_width_mm or 0.0)
        piece_a_size = float(option.piece_a_size_mm or 0.0)
        piece_b_size = float(option.piece_b_size_mm or 0.0)
        if axis == "length":
            piece_a_length = piece_a_size
            piece_b_length = piece_b_size
            piece_a_width = source_width
            piece_b_width = source_width
        else:
            piece_a_length = source_length
            piece_b_length = source_length
            piece_a_width = piece_a_size
            piece_b_width = piece_b_size

        piece_commands = self._accepted_auto_split_piece_commands(
            spec,
            [
                {
                    "sequence": 10,
                    "piece_code": "A",
                    "piece_name": "Piece A",
                    "piece_length_mm": piece_a_length,
                    "piece_width_mm": piece_a_width,
                    "piece_qty_per_fg": 1.0,
                },
                {
                    "sequence": 20,
                    "piece_code": "B",
                    "piece_name": "Piece B",
                    "piece_length_mm": piece_b_length,
                    "piece_width_mm": piece_b_width,
                    "piece_qty_per_fg": 1.0,
                },
            ],
        )

        spec.write(
            {
                "multi_piece_enabled": True,
                "multi_piece_source": "accepted_auto_split",
                "multi_piece_strategy": "independent_routed_piece",
                "multi_piece_split_axis": axis,
                "multi_piece_split_mode": "equal",
                "multi_piece_source_length_mm": source_length,
                "multi_piece_source_width_mm": source_width,
                "accepted_split_signature": spec._split_signature(
                    axis,
                    "equal",
                    source_length,
                    source_width,
                    piece_a_size,
                    piece_b_size,
                )
                if hasattr(spec, "_split_signature")
                else False,
                "multi_piece_split_stale": False,
                "multi_piece_merge_stage_key": self.multi_piece_merge_stage_key or "seaming",
                "multi_piece_split_after_stage_key": False,
                "multi_piece_line_ids": piece_commands,
            }
        )
        return True

    def _accepted_auto_split_piece_commands(self, spec, piece_values):
        existing_by_code = {}
        duplicate_ids = []
        for line in spec.multi_piece_line_ids:
            code = (line.piece_code or "").strip().upper()
            if code in ("A", "B"):
                if code not in existing_by_code:
                    existing_by_code[code] = line
                else:
                    duplicate_ids.append(line.id)

        commands = [(2, line_id, 0) for line_id in duplicate_ids]
        for values in piece_values:
            code = (values.get("piece_code") or "").strip().upper()
            line = existing_by_code.get(code)
            if line:
                commands.append((1, line.id, values))
            else:
                commands.append((0, 0, values))
        return commands

    def _persist_multi_piece_source_dimensions_from_result(self, spec, result):
        if not spec or not getattr(spec, "multi_piece_enabled", False):
            return False

        piece_plan = getattr(result, "piece_plan", None)
        if not piece_plan:
            try:
                extra = getattr(getattr(result, "metrics", None), "extra", {}) or {}
                piece_plan = extra.get("multi_piece") or {}
            except Exception:
                piece_plan = {}
        if not piece_plan:
            return False

        enabled = self._get_piece_plan_value(piece_plan, "enabled", False)
        if not enabled:
            return False
        source_length = float(
            self._get_piece_plan_value(piece_plan, "source_length_mm", 0.0) or 0.0
        )
        source_width = float(
            self._get_piece_plan_value(piece_plan, "source_width_mm", 0.0) or 0.0
        )
        if source_length <= 0.0 or source_width <= 0.0:
            return False

        values = {}
        if float(getattr(spec, "multi_piece_source_length_mm", 0.0) or 0.0) <= 0.0:
            values["multi_piece_source_length_mm"] = source_length
        if float(getattr(spec, "multi_piece_source_width_mm", 0.0) or 0.0) <= 0.0:
            values["multi_piece_source_width_mm"] = source_width
        if not values:
            return False
        spec.write(values)
        return True

    def action_clear_accepted_split(self):
        self.ensure_one()
        self._clear_accepted_auto_split_state()
        return self.action_run_debug()

    def _clear_accepted_auto_split_state(self):
        self.ensure_one()
        if self.v2_spec_id and self.v2_spec_id.multi_piece_enabled:
            self.v2_spec_id.write(
                {
                    "multi_piece_enabled": False,
                    "multi_piece_source": "explicit_multi_piece_design",
                    "multi_piece_strategy": "independent_routed_piece",
                    "multi_piece_split_axis": "width",
                    "multi_piece_split_mode": "equal",
                    "multi_piece_source_length_mm": 0.0,
                    "multi_piece_source_width_mm": 0.0,
                    "accepted_split_signature": False,
                    "multi_piece_split_stale": False,
                    "multi_piece_merge_stage_key": "seaming",
                    "multi_piece_split_after_stage_key": False,
                    "multi_piece_line_ids": [(5, 0, 0)],
                }
            )
        self.write(
            {
                "multi_piece_enabled": False,
                "multi_piece_source": "explicit_multi_piece_design",
                "multi_piece_strategy": "independent_routed_piece",
                "multi_piece_split_mode": "equal",
                "multi_piece_ratio_a": 1.0,
                "multi_piece_ratio_b": 1.0,
                "multi_piece_primary_size_mm": 0.0,
                "multi_piece_piece_a_size_mm": 0.0,
                "multi_piece_piece_b_size_mm": 0.0,
                "multi_piece_split_after_stage_key": False,
            }
        )
        return True

    def _accepted_auto_split_full_blank_has_feasible_layout(
        self,
        result,
        qgraph,
        spec,
        plant_param,
        *,
        v2_spec=None,
    ):
        self.ensure_one()
        if engine_v2_runtime_builder is None:
            return False
        if not self._is_accepted_auto_split_result(result):
            return False

        piece_plan = self._piece_plan_dict(result)
        blank_length = float(self._get_piece_plan_value(piece_plan, "source_length_mm", 0.0) or 0.0)
        blank_width = float(self._get_piece_plan_value(piece_plan, "source_width_mm", 0.0) or 0.0)
        if blank_length <= 0.0 or blank_width <= 0.0:
            blank_length, blank_width = self._result_blank_dimensions(result)
        if blank_length <= 0.0 or blank_width <= 0.0:
            return False

        try:
            routing = engine_v2_runtime_builder._build_routing_instance(
                self.env,
                spec,
                plant_param,
                qgraph,
                v2_spec=v2_spec,
            )
            stages = list(getattr(routing, "stages", []) or [])
            stage_contexts = engine_v2_runtime_builder._build_stage_contexts(self.env, stages)
        except Exception:
            _logger.exception("Engine V2 accepted auto split full-blank recheck routing build failed")
            return False

        extra = deepcopy(getattr(getattr(result, "metrics", None), "extra", {}) or {})
        geometry = dict(extra.get("geometry") or {})
        geometry["blank_length_mm"] = blank_length
        geometry["blank_width_mm"] = blank_width
        extra["geometry"] = geometry
        layout = dict(extra.get("layout") or {})
        layout["sheet_length_mm"] = 0.0
        layout["sheet_width_mm"] = 0.0
        layout["blank_length_mm"] = blank_length
        layout["blank_width_mm"] = blank_width
        layout["sheet_source"] = "accepted_auto_split_full_blank_recheck"
        extra["layout"] = layout
        full_blank_result = SimpleNamespace(metrics=SimpleNamespace(extra=extra))

        try:
            slitting_options = engine_v2_runtime_builder._build_slitting_options(
                self.env,
                stages,
                stage_contexts,
                spec=spec,
                v2_spec=v2_spec,
                engine_result=full_blank_result,
            )
        except Exception:
            _logger.exception("Engine V2 accepted auto split full-blank recheck failed")
            return False

        layout_driver = dict((slitting_options or {}).get("layout_driving_stage") or {})
        layout_driver_identity = (
            layout_driver.get("stage_contract_code")
            or layout_driver.get("stage_code")
            or str(layout_driver.get("stage_template_id") or "")
        )
        stages_payload = list((slitting_options or {}).get("stages") or [])
        if layout_driver_identity:
            stages_payload = [
                stage_entry
                for stage_entry in stages_payload
                if (
                    stage_entry.get("stage_contract_code")
                    or stage_entry.get("stage_code")
                    or str(stage_entry.get("stage_template_id") or "")
                )
                == layout_driver_identity
            ]
        elif stages_payload:
            stages_payload = [stages_payload[0]]

        return any(stage_entry.get("feasible_options") for stage_entry in stages_payload)

    def _piece_inherited_manufacturing_display(self):
        self.ensure_one()
        spec = self._active_spec_record()

        def _display(record):
            if not record:
                return ""
            return (
                getattr(record, "display_name", None)
                or getattr(record, "name", None)
                or getattr(record, "code", None)
                or ""
            )

        flutes = []
        for line_source_name in ("stack_line_ids",):
            try:
                lines = list(getattr(spec, line_source_name, []) or [])
            except Exception:
                lines = []
            for line in lines:
                flute = getattr(line, "flute_type_id", None) or getattr(line, "flute_id", None)
                flute_code = (getattr(flute, "code", "") or getattr(flute, "name", "") or "") if flute else ""
                code = (
                    getattr(flute, "code", None)
                    or getattr(flute, "name", None)
                    or ""
                ) if flute else ""
                if code and code not in flutes:
                    flutes.append(code)
        if not flutes:
            try:
                recipe = getattr(spec, "board_recipe_id", None)
                recipe_lines = list(getattr(recipe, "line_ids", []) or []) if recipe else []
            except Exception:
                recipe_lines = []
            for line in recipe_lines:
                flute = getattr(line, "flute_type_id", None) or getattr(line, "flute_id", None)
                code = (
                    getattr(flute, "code", None)
                    or getattr(flute, "name", None)
                    or ""
                ) if flute else ""
                if code and code not in flutes:
                    flutes.append(code)

        surface = getattr(spec, "surface_profile_id", None) if spec else None
        top_sheet_product = getattr(spec, "surface_top_sheet_product_id", None) if spec else None
        return {
            "flute_profile": ", ".join(flutes),
            "surface_profile": _display(surface),
            "top_sheet_mode": self._top_sheet_mode_label(
                getattr(spec, "surface_top_sheet_mode", "") if spec else ""
            ),
            "top_sheet_product": _display(top_sheet_product),
        }

    def _top_sheet_mode_label(self, value):
        value = (value or "").strip()
        labels = {
            "none": _("None"),
            "roll": _("Top from Roll"),
            "unit_sheet": _("Unit Sheet"),
        }
        return labels.get(value, value.replace("_", " ").title() if value else "")

    def _render_multi_piece_html(self, result):
        self.ensure_one()
        fmt = self._preview_formatter()
        inherited = self._piece_inherited_manufacturing_display()
        piece_plan = getattr(result, "piece_plan", None)
        if not piece_plan:
            extra = getattr(getattr(result, "metrics", None), "extra", {}) or {}
            piece_plan = (extra.get("multi_piece") or {}) if isinstance(extra, dict) else {}
        if not piece_plan:
            return "<p>%s</p>" % _("Multi-piece manufacturing is not enabled for this preview.")

        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        pieces = list(_get(piece_plan, "pieces", []) or [])
        effective_piece_payload = self._build_piece_manufacturing_override_payload(piece_plan)
        effective_by_key = {
            self._canonical_piece_key(piece.get("piece_key")): piece
            for piece in list(effective_piece_payload.get("pieces") or [])
        }
        split_axis = _get(piece_plan, "split_axis", "") or ""
        split_mode = _get(piece_plan, "split_mode", "") or ""
        merge_stage = _get(piece_plan, "merge_stage_key", "") or "seaming"
        source_length = _get(piece_plan, "source_length_mm", 0.0) or 0.0
        source_width = _get(piece_plan, "source_width_mm", 0.0) or 0.0

        html = ["<h3>%s</h3>" % _("Multi-Piece Manufacturing")]
        html.append(
            "<table class='table table-sm table-striped'>"
            "<tbody>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s x %s mm</td></tr>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "</tbody></table>"
            % (
                _("Split Axis"),
                html_escape(str(split_axis or "-")),
                _("Split Mode"),
                html_escape(str(split_mode or "-")),
                _("Source Blank"),
                fmt.format_integer(source_length),
                fmt.format_integer(source_width),
                _("Merge Stage"),
                html_escape(str(merge_stage or "-")),
            )
        )

        html.append("<h4>%s</h4>" % _("Pieces"))
        html.append(
            "<table class='table table-sm table-striped'><thead><tr>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "</tr></thead><tbody>"
            % (
                _("Piece"),
                _("Qty / FG"),
                _("Length"),
                _("Width"),
                _("Area"),
                _("Stack Source"),
                _("Flute Profile"),
                _("Surface Source"),
                _("Surface Profile"),
                _("Top Sheet Source"),
                _("Top Sheet Mode"),
                _("Top Sheet Product"),
            )
        )
        for piece in pieces:
            name = _get(piece, "piece_name", "") or _get(piece, "piece_key", "")
            piece_key = self._canonical_piece_key(_get(piece, "piece_key", ""))
            effective = effective_by_key.get(piece_key) or {}
            stack = dict(effective.get("stack") or {})
            surface = dict(effective.get("surface") or {})
            top_sheet = dict(effective.get("top_sheet") or {})
            length = float(_get(piece, "piece_length_mm", 0.0) or 0.0)
            width = float(_get(piece, "piece_width_mm", 0.0) or 0.0)
            area = (length * width) / 1_000_000.0 if length > 0.0 and width > 0.0 else 0.0
            html.append(
                "<tr><td>%s</td><td>%s</td><td>%s mm</td><td>%s mm</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    html_escape(str(name or "-")),
                    fmt.format_quantity(_get(piece, "piece_qty_per_fg", 1.0) or 1.0),
                    fmt.format_integer(length),
                    fmt.format_integer(width),
                    fmt.format_m2(area),
                    html_escape(str(effective.get("stack_source") or _get(piece, "stack_source", "inherited") or "inherited").title()),
                    html_escape(stack.get("effective_flute_profile") or inherited.get("flute_profile") or "-"),
                    html_escape(str(effective.get("surface_source") or _get(piece, "surface_source", "inherited") or "inherited").title()),
                    html_escape(surface.get("effective_surface_profile") or inherited.get("surface_profile") or "-"),
                    html_escape(str(effective.get("top_sheet_source") or _get(piece, "top_sheet_source", "inherited") or "inherited").title()),
                    html_escape(
                        self._top_sheet_mode_label(top_sheet.get("effective_top_sheet_mode") or "")
                        or inherited.get("top_sheet_mode")
                        or "-"
                    ),
                    html_escape(top_sheet.get("effective_top_sheet_product") or inherited.get("top_sheet_product") or "-"),
                )
            )
        html.append("</tbody></table>")

        if effective_piece_payload.get("custom_overrides_present"):
            html.append(
                "<div class='alert alert-warning'>%s</div>"
                % _(
                    "Piece-specific stack/surface/top sheet overrides are preview-only in Phase J1. "
                    "Create BoM is blocked until piece-specific RM, costing, and materialization are validated."
                )
            )

        routes_by_piece = defaultdict(list)
        for stage in getattr(getattr(result, "stage_graph", None), "stages", []) or []:
            piece_key = getattr(stage, "piece_key", None)
            if piece_key:
                routes_by_piece[piece_key].append(getattr(stage, "base_stage_key", "") or getattr(stage, "key", ""))
        if routes_by_piece:
            html.append("<h4>%s</h4>" % _("Projected Piece Flow"))
            html.append("<table class='table table-sm'><tbody>")
            for piece in pieces:
                piece_key = _get(piece, "piece_key", "")
                name = _get(piece, "piece_name", "") or piece_key
                route = " > ".join(routes_by_piece.get(piece_key) or [])
                html.append(
                    "<tr><th>%s</th><td>%s</td></tr>"
                    % (html_escape(str(name or "-")), html_escape(route or "-"))
                )
            html.append(
                "<tr><th>%s</th><td>%s</td></tr>"
                % (
                    _("Merge"),
                    html_escape(
                        "%s -> FG"
                        % (
                            " + ".join(
                                [
                                    "%s" % (_get(piece, "piece_name", "") or _get(piece, "piece_key", ""))
                                    for piece in pieces
                                ]
                            )
                            + " -> "
                            + str(merge_stage or "seaming")
                        )
                    ),
                )
            )
            html.append("</tbody></table>")

        rm_totals = defaultdict(float)
        for rm in getattr(result, "rm_requirements", []) or []:
            metadata = getattr(rm, "metadata", {}) or {}
            piece_key = metadata.get("piece_key")
            if not piece_key:
                continue
            try:
                qty = float(getattr(rm, "qty_per_fg", 0.0) or 0.0)
            except Exception:
                qty = 0.0
            if getattr(rm, "qty_uom", "") == "kg":
                rm_totals[piece_key] += qty
        if rm_totals:
            html.append("<h4>%s</h4>" % _("RM by Piece"))
            html.append("<table class='table table-sm table-striped'><tbody>")
            for piece in pieces:
                piece_key = _get(piece, "piece_key", "")
                name = _get(piece, "piece_name", "") or piece_key
                html.append(
                    "<tr><th>%s</th><td>%s</td></tr>"
                    % (html_escape(str(name or "-")), fmt.format_kg(rm_totals.get(piece_key, 0.0)))
                )
            html.append("</tbody></table>")
        return "\n".join(html)

    def _preview_has_feasible_layout_options(self, shadow_runtime):
        slitting_options = (shadow_runtime or {}).get("slitting_options") or {}
        summary = dict(slitting_options.get("summary") or {})
        try:
            feasible_count = int(summary.get("feasible_option_count", 0) or 0)
        except Exception:
            feasible_count = 0
        if feasible_count > 0:
            return True
        for stage_entry in list(slitting_options.get("stages") or []):
            if list(stage_entry.get("feasible_options") or []):
                return True
        return False

    def _layout_option_engine_ran(self, shadow_runtime):
        slitting_options = (shadow_runtime or {}).get("slitting_options") or {}
        if list(slitting_options.get("stages") or []):
            return True
        summary = dict(slitting_options.get("summary") or {})
        try:
            return int(summary.get("stage_count", 0) or 0) > 0
        except Exception:
            return False

    def _result_has_infeasible_derived_sheet(self, result):
        metrics = getattr(result, "metrics", None)
        extra = getattr(metrics, "extra", {}) or {}
        if not isinstance(extra, dict):
            return False
        feasibility = dict(extra.get("derived_sheet_feasibility") or {})
        return (feasibility.get("status") or "").strip().lower() == "infeasible"

    def _result_blank_dimensions(self, result):
        metrics = getattr(result, "metrics", None)
        extra = getattr(metrics, "extra", {}) or {}
        geometry = dict(extra.get("geometry") or {}) if isinstance(extra, dict) else {}
        length = float(geometry.get("blank_length_mm") or 0.0)
        width = float(geometry.get("blank_width_mm") or 0.0)
        if length <= 0.0:
            length = float(getattr(result, "blank_length_mm", 0.0) or 0.0)
        if width <= 0.0:
            width = float(getattr(result, "blank_width_mm", 0.0) or 0.0)
        if (length <= 0.0 or width <= 0.0) and metrics:
            layout = dict(extra.get("layout") or {}) if isinstance(extra, dict) else {}
            length = length or float(layout.get("blank_length_mm") or 0.0)
            width = width or float(layout.get("blank_width_mm") or 0.0)
            length = length or float(layout.get("sheet_length_mm") or 0.0)
            width = width or float(layout.get("sheet_width_mm") or 0.0)
        if (length <= 0.0 or width <= 0.0) and metrics and isinstance(extra, dict):
            length = length or float(extra.get("selected_layout_sheet_length_mm") or 0.0)
            width = width or float(extra.get("selected_layout_sheet_width_mm") or 0.0)
            length = length or float(extra.get("derived_sheet_length_mm") or 0.0)
            width = width or float(extra.get("derived_sheet_width_mm") or 0.0)
        if length <= 0.0 or width <= 0.0:
            for item in list(getattr(result, "trace", []) or []):
                try:
                    step, payload = item
                except Exception:
                    continue
                if step != "style_resolved":
                    continue
                payload = dict(payload or {})
                length = length or float(payload.get("blank_length_mm") or 0.0)
                width = width or float(payload.get("blank_width_mm") or 0.0)
                break
        return length, width

    def _summary_style_display(self, spec):
        style_rec = getattr(spec, "style_id", None)
        if style_rec:
            return (
                getattr(style_rec, "display_name", "")
                or getattr(style_rec, "name", "")
                or getattr(style_rec, "code", "")
                or ""
            )
        return getattr(spec, "style", "") or ""

    def _summary_route_stage_codes(self, preview_model=None):
        stage_codes = []
        if isinstance(preview_model, dict):
            for row in list(preview_model.get("stages") or []):
                code = _summary_text(row.get("stage_code") or row.get("stage_name"))
                if code:
                    stage_codes.append(code)
        if stage_codes:
            return stage_codes

        spec = self._active_spec_record()
        routing_profile = getattr(spec, "routing_profile_id", None)
        if not routing_profile:
            return []

        try:
            stages = routing_profile.stage_ids.sorted(lambda stage: (stage.sequence, stage.id))
        except Exception:
            stages = routing_profile.stage_ids
        for stage in stages:
            code = (
                getattr(stage, "stage_code", "")
                or getattr(getattr(stage, "stage_template_id", None), "code", "")
                or getattr(getattr(stage, "stage_template_id", None), "name", "")
                or getattr(stage, "display_name", "")
                or ""
            )
            code = _summary_text(code)
            if code:
                stage_codes.append(code)
        return stage_codes

    def _summary_sheet_dimensions_mm(self, result=None):
        spec = self._active_spec_record()
        metrics = getattr(result, "metrics", None) if result else None
        extra = getattr(metrics, "extra", {}) or {}
        layout = dict(extra.get("layout") or {}) if isinstance(extra, dict) else {}
        geometry = dict(extra.get("geometry") or {}) if isinstance(extra, dict) else {}
        length = 0.0
        width = 0.0
        layout_policy = getattr(spec, "layout_policy_id", False) if spec else False
        use_manual_sheet = bool(
            layout_policy and (getattr(layout_policy, "sheet_source", "") or "") == "manual"
        )

        for values in (
            (
                getattr(spec, "manual_sheet_length_mm", 0.0) if use_manual_sheet else 0.0,
                getattr(spec, "manual_sheet_width_mm", 0.0) if use_manual_sheet else 0.0,
            ),
            (
                getattr(spec, "box_length_mm", 0.0),
                getattr(spec, "box_width_mm", 0.0),
            ),
            (
                geometry.get("sheet_length_mm"),
                geometry.get("sheet_width_mm"),
            ),
            (
                layout.get("sheet_length_mm"),
                layout.get("sheet_width_mm"),
            ),
            (
                extra.get("selected_layout_sheet_length_mm"),
                extra.get("selected_layout_sheet_width_mm"),
            ),
            (
                extra.get("derived_sheet_length_mm"),
                extra.get("derived_sheet_width_mm"),
            ),
        ):
            if length <= 0.0:
                length = _summary_float(values[0], 0.0)
            if width <= 0.0:
                width = _summary_float(values[1], 0.0)
            if length > 0.0 and width > 0.0:
                break
        return length, width

    def _summary_required_sheets(self, qgraph=None, summary=None):
        summary = dict(summary or {})
        for key in ("required_sheets", "parent_sheets_required", "sheets_required"):
            value = _summary_int(summary.get(key), 0)
            if value > 0:
                return value
        try:
            sheet_node = qgraph.nodes.get("sheet") if qgraph else None
            value = _summary_int(getattr(sheet_node, "qty", 0), 0)
            if value > 0:
                return value
        except Exception:
            pass
        return 0

    def _build_preview_summary_context(self, result=None, qgraph=None, costing=None):
        fmt = self._preview_formatter()
        spec = self._active_spec_record()
        plant = self.plant_id or getattr(spec, "plant_id", None)
        blank_length_mm, blank_width_mm = self._result_blank_dimensions(result) if result else (0.0, 0.0)
        sheet_length_mm, sheet_width_mm = self._summary_sheet_dimensions_mm(result)
        roll_length_m = _summary_float(getattr(spec, "roll_length_m", 0.0), 0.0)
        roll_width_mm = _summary_float(getattr(spec, "box_width_mm", 0.0), 0.0)
        metrics = getattr(result, "metrics", None) if result else None
        m2_per_fg = _summary_float(getattr(metrics, "m2_per_box", 0.0), 0.0)
        kg_per_fg = _summary_float(getattr(metrics, "kg_per_box", 0.0), 0.0)
        fg_per_sheet = max(1, _summary_int(getattr(spec, "fg_per_sheet", 0), 0)) if getattr(spec, "is_diecut_sheet_style", False) else 0
        required_sheets = self._summary_required_sheets(qgraph=qgraph)
        order_qty = _summary_float(self.order_qty, 0.0)
        if getattr(spec, "is_diecut_sheet_style", False) and fg_per_sheet > 0 and required_sheets <= 0 and order_qty > 0.0:
            required_sheets = int(math.ceil(order_qty / float(fg_per_sheet)))
        if getattr(spec, "is_sheet_style", False) and required_sheets <= 0 and order_qty > 0.0:
            required_sheets = _summary_int(order_qty, 0)

        stage_codes = self._summary_route_stage_codes()
        summary_context = {
            "finished_product": getattr(getattr(spec, "product_tmpl_id", None), "display_name", "") or getattr(getattr(spec, "product_tmpl_id", None), "name", "") or "",
            "routing_profile_name": getattr(getattr(spec, "routing_profile_id", None), "display_name", "") or getattr(getattr(spec, "routing_profile_id", None), "name", "") or "",
            "stage_codes": stage_codes,
            "style_display": self._summary_style_display(spec),
            "is_sheet_style": bool(getattr(spec, "is_sheet_style", False)),
            "is_roll_style": bool(getattr(spec, "is_roll_style", False)),
            "is_diecut_sheet_style": bool(getattr(spec, "is_diecut_sheet_style", False)),
            "blank_length_mm": blank_length_mm,
            "blank_width_mm": blank_width_mm,
            "sheet_length_mm": sheet_length_mm,
            "sheet_width_mm": sheet_width_mm,
            "roll_length_m": roll_length_m,
            "roll_width_mm": roll_width_mm,
            "area_per_fg_m2": m2_per_fg,
            "weight_per_fg_kg": kg_per_fg,
            "area_per_sheet_m2": (sheet_length_mm * sheet_width_mm / 1000000.0) if sheet_length_mm > 0.0 and sheet_width_mm > 0.0 else 0.0,
            "area_per_roll_m2": (roll_length_m * roll_width_mm / 1000.0) if roll_length_m > 0.0 and roll_width_mm > 0.0 else 0.0,
            "fg_per_sheet": fg_per_sheet,
            "required_sheets": required_sheets,
            "gross_blanks_produced": required_sheets * fg_per_sheet if fg_per_sheet > 0 and required_sheets > 0 else 0,
            "blanks_produced": _summary_int(order_qty, 0),
            "fg_output_qty": order_qty,
            "routing_stage_count": len(stage_codes),
            "cost_source_label": _summary_cost_source_label(
                (costing or {}).get("cost_source") or self._get_cost_source()
            ),
            "order_qty_display": fmt.format_integer(order_qty),
            "blank_length_mm_display": fmt.format_quantity(blank_length_mm, "", digits=2, trim=False) if blank_length_mm > 0.0 else "",
            "blank_width_mm_display": fmt.format_quantity(blank_width_mm, "", digits=2, trim=False) if blank_width_mm > 0.0 else "",
            "sheet_length_mm_display": fmt.format_quantity(sheet_length_mm, "", digits=2, trim=False) if sheet_length_mm > 0.0 else "",
            "sheet_width_mm_display": fmt.format_quantity(sheet_width_mm, "", digits=2, trim=False) if sheet_width_mm > 0.0 else "",
            "roll_length_m_display": fmt.format_quantity(roll_length_m, "", digits=3, trim=False) if roll_length_m > 0.0 else "",
            "roll_width_mm_display": fmt.format_quantity(roll_width_mm, "", digits=2, trim=False) if roll_width_mm > 0.0 else "",
            "area_per_fg_m2_display": fmt.format_m2(m2_per_fg) if m2_per_fg > 0.0 else "",
            "weight_per_fg_kg_display": fmt.format_kg(kg_per_fg) if kg_per_fg > 0.0 else "",
            "area_per_sheet_m2_display": fmt.format_m2((sheet_length_mm * sheet_width_mm / 1000000.0)) if sheet_length_mm > 0.0 and sheet_width_mm > 0.0 else "",
            "area_per_roll_m2_display": fmt.format_m2((roll_length_m * roll_width_mm / 1000.0)) if roll_length_m > 0.0 and roll_width_mm > 0.0 else "",
            "fg_per_sheet_display": fmt.format_integer(fg_per_sheet) if fg_per_sheet > 0 else "",
            "required_sheets_display": fmt.format_integer(required_sheets) if required_sheets > 0 else "",
            "gross_blanks_produced_display": fmt.format_integer(required_sheets * fg_per_sheet) if fg_per_sheet > 0 and required_sheets > 0 else "",
            "blanks_produced_display": fmt.format_integer(order_qty) if order_qty > 0.0 else "",
            "fg_output_qty_display": fmt.format_integer(order_qty) if order_qty > 0.0 else "",
            "routing_stage_count_display": fmt.format_integer(len(stage_codes)) if stage_codes else "",
            "material_cost_per_unit_display": "",
            "material_cost_for_order_display": "",
            "operation_cost_for_order_display": "",
            "total_production_cost_for_order_display": "",
        }

        if costing:
            symbol = (costing.get("currency") or {}).get("symbol") or ""
            summary_context.update(
                {
                    "material_cost_per_unit_display": fmt.format_currency(costing.get("total_cost_per_fg", 0.0), symbol),
                    "material_cost_for_order_display": fmt.format_currency(costing.get("total_cost_for_order", 0.0), symbol),
                    "operation_cost_for_order_display": fmt.format_currency(costing.get("total_operation_cost", 0.0), symbol),
                    "total_production_cost_for_order_display": fmt.format_currency(costing.get("total_production_cost", 0.0), symbol),
                }
            )

        return summary_context

    def _build_summary_render_context(self, summary=None, preview_model=None, plant_name="", order_qty=None, costing=None):
        fmt = self._preview_formatter()
        spec = self._active_spec_record()
        summary = dict(summary or {})
        incoming_context = dict(summary.get("context") or {})
        stage_codes = list(incoming_context.get("stage_codes") or self._summary_route_stage_codes(preview_model))
        operation_flow_display = " -> ".join(stage_codes)
        order_qty_value = _summary_float(order_qty if order_qty is not None else summary.get("order_qty"), 0.0)
        return {
            "spec_name": getattr(spec, "name", "") or summary.get("spec_name") or "",
            "finished_product": incoming_context.get("finished_product") or getattr(getattr(spec, "product_tmpl_id", None), "display_name", "") or getattr(getattr(spec, "product_tmpl_id", None), "name", "") or "",
            "style_display": incoming_context.get("style_display") or self._summary_style_display(spec) or summary.get("style") or "",
            "plant_name": incoming_context.get("plant_name") or plant_name or summary.get("plant_name") or getattr(getattr(spec, "plant_id", None), "name", "") or getattr(self.plant_id, "name", "") or "",
            "routing_profile_name": incoming_context.get("routing_profile_name") or getattr(getattr(spec, "routing_profile_id", None), "display_name", "") or getattr(getattr(spec, "routing_profile_id", None), "name", "") or "",
            "order_qty_display": incoming_context.get("order_qty_display") or fmt.format_integer(order_qty_value),
            "cost_source_label": incoming_context.get("cost_source_label") or _summary_cost_source_label((costing or {}).get("cost_source") or self._get_cost_source()),
            "is_sheet_style": bool(incoming_context.get("is_sheet_style") or getattr(spec, "is_sheet_style", False)),
            "is_roll_style": bool(incoming_context.get("is_roll_style") or getattr(spec, "is_roll_style", False)),
            "is_diecut_sheet_style": bool(incoming_context.get("is_diecut_sheet_style") or getattr(spec, "is_diecut_sheet_style", False)),
            "operation_flow_display": operation_flow_display,
        }

    def _build_summary_render_payload(self, summary=None, result=None, qgraph=None, costing=None):
        fmt = self._preview_formatter()
        summary = dict(summary or {})
        context = dict(summary.get("context") or {})

        def _display_value(raw, digits=2):
            value = _summary_float(raw, 0.0)
            if value <= 0.0:
                return ""
            return fmt.format_quantity(value, "", digits=digits, trim=False)

        payload = {
            "blank_length_mm_display": context.get("blank_length_mm_display") or "",
            "blank_width_mm_display": context.get("blank_width_mm_display") or "",
            "sheet_length_mm_display": context.get("sheet_length_mm_display") or "",
            "sheet_width_mm_display": context.get("sheet_width_mm_display") or "",
            "roll_length_m_display": context.get("roll_length_m_display") or "",
            "roll_width_mm_display": context.get("roll_width_mm_display") or "",
            "area_per_fg_m2_display": context.get("area_per_fg_m2_display") or "",
            "weight_per_fg_kg_display": context.get("weight_per_fg_kg_display") or "",
            "area_per_sheet_m2_display": context.get("area_per_sheet_m2_display") or "",
            "area_per_roll_m2_display": context.get("area_per_roll_m2_display") or "",
            "fg_per_sheet_display": context.get("fg_per_sheet_display") or "",
            "required_sheets_display": context.get("required_sheets_display") or "",
            "gross_blanks_produced_display": context.get("gross_blanks_produced_display") or "",
            "blanks_produced_display": context.get("blanks_produced_display") or "",
            "fg_output_qty_display": context.get("fg_output_qty_display") or "",
            "routing_stage_count_display": context.get("routing_stage_count_display") or "",
            "material_cost_per_unit_display": context.get("material_cost_per_unit_display") or "",
            "material_cost_for_order_display": context.get("material_cost_for_order_display") or "",
            "operation_cost_for_order_display": context.get("operation_cost_for_order_display") or "",
            "total_production_cost_for_order_display": context.get("total_production_cost_for_order_display") or "",
        }
        if result is not None:
            payload.update(self._build_preview_summary_context(result=result, qgraph=qgraph, costing=costing))
        else:
            if not payload["required_sheets_display"]:
                required_sheets = self._summary_required_sheets(qgraph=qgraph, summary=summary)
                payload["required_sheets_display"] = fmt.format_integer(required_sheets) if required_sheets > 0 else ""
            if not payload["routing_stage_count_display"]:
                routing_stage_count = _summary_int(summary.get("routing_stage_count"), 0)
                if routing_stage_count > 0:
                    payload["routing_stage_count_display"] = fmt.format_integer(routing_stage_count)
            if not payload["fg_output_qty_display"]:
                fg_output_qty = _summary_float(summary.get("fg_stage_total_qty") or summary.get("order_qty"), 0.0)
                if fg_output_qty > 0.0:
                    payload["fg_output_qty_display"] = fmt.format_integer(fg_output_qty)

        return payload

    def _piece_plan_dict(self, result):
        piece_plan = getattr(result, "piece_plan", None)
        if not piece_plan:
            extra = getattr(getattr(result, "metrics", None), "extra", {}) or {}
            if isinstance(extra, dict):
                piece_plan = extra.get("multi_piece") or {}
        return piece_plan

    def _get_piece_plan_value(self, piece_plan, key, default=None):
        if isinstance(piece_plan, dict):
            return piece_plan.get(key, default)
        return getattr(piece_plan, key, default)

    def _is_accepted_auto_split_result(self, result):
        piece_plan = self._piece_plan_dict(result)
        return (self._get_piece_plan_value(piece_plan, "source", "") or "") == "accepted_auto_split"

    def _load_equal_split_suggestion_lines(
        self,
        *,
        blank_length,
        blank_width,
        selected_axis=None,
        selected_reason=None,
        result=None,
        qgraph=None,
        spec=None,
        plant_param=None,
        v2_spec=None,
    ):
        commands = [(5, 0, 0)]
        rejected_suggestions = []
        validation_context = self._auto_split_validation_context(
            result=result,
            qgraph=qgraph,
            spec=spec,
            plant_param=plant_param,
            v2_spec=v2_spec,
        )
        suggestions = [
            {
                "option_rank": 1,
                "split_axis": "width",
                "piece_a_size_mm": blank_width / 2.0,
                "piece_b_size_mm": blank_width / 2.0,
                "source_length_mm": blank_length,
                "source_width_mm": blank_width,
                "reason": selected_reason or _("Equal width split suggestion for infeasible full blank."),
            },
            {
                "option_rank": 2,
                "split_axis": "length",
                "piece_a_size_mm": blank_length / 2.0,
                "piece_b_size_mm": blank_length / 2.0,
                "source_length_mm": blank_length,
                "source_width_mm": blank_width,
                "reason": selected_reason or _("Equal length split suggestion for infeasible full blank."),
            },
        ]
        for suggestion in suggestions:
            validation = self._validate_auto_split_suggestion(
                suggestion,
                validation_context=validation_context,
            )
            suggestion["validation"] = validation
            if not validation.get("valid", True):
                rejected_suggestions.append(suggestion)
                continue
            commands.append(
                (
                    0,
                    0,
                    {
                        "option_rank": suggestion["option_rank"],
                        "split_axis": suggestion["split_axis"],
                        "split_method": "equal",
                        "piece_a_size_mm": suggestion["piece_a_size_mm"],
                        "piece_b_size_mm": suggestion["piece_b_size_mm"],
                        "source_length_mm": suggestion["source_length_mm"],
                        "source_width_mm": suggestion["source_width_mm"],
                        "reason": suggestion["reason"],
                        "is_selected": bool(selected_axis and suggestion["split_axis"] == selected_axis),
                    },
                )
            )
        self.auto_split_suggestion_line_ids = commands
        diagnostics = {
            "blank_length_mm": blank_length,
            "blank_width_mm": blank_width,
            "selected_axis": selected_axis or "",
            "validation_context_available": bool(validation_context),
            "valid_candidate_count": max(0, len(commands) - 1),
            "rejected_candidate_count": len(rejected_suggestions),
            "reason": "equal_split_candidates_generated"
            if len(commands) > 1
            else "equal_split_candidates_rejected",
        }
        try:
            self.auto_split_rejected_suggestions_json = json.dumps(
                rejected_suggestions,
                indent=2,
                sort_keys=True,
            )
        except Exception:
            self.auto_split_rejected_suggestions_json = False
        diagnostics["rejected_suggestions"] = rejected_suggestions
        try:
            self.auto_split_diagnostics_json = json.dumps(diagnostics, indent=2, sort_keys=True)
        except Exception:
            self.auto_split_diagnostics_json = False

    def _auto_split_validation_context(self, *, result, qgraph, spec, plant_param, v2_spec=None):
        if engine_v2_runtime_builder is None or not result or not qgraph or not spec or not plant_param:
            return None
        try:
            routing = engine_v2_runtime_builder._build_routing_instance(
                self.env,
                spec,
                plant_param,
                qgraph,
                v2_spec=v2_spec,
            )
            stages = list(getattr(routing, "stages", []) or [])
            stage_contexts = engine_v2_runtime_builder._build_stage_contexts(self.env, stages)
        except Exception:
            _logger.exception("Engine V2 auto split validation routing build failed")
            return None
        return {
            "stages": stages,
            "stage_contexts": stage_contexts,
            "spec": spec,
            "v2_spec": v2_spec,
            "base_extra": deepcopy(getattr(getattr(result, "metrics", None), "extra", {}) or {}),
        }

    def _validate_auto_split_suggestion(self, suggestion, *, validation_context=None):
        if not validation_context:
            return {"valid": True, "reason": "validation_context_unavailable"}
        pieces = self._auto_split_candidate_pieces(suggestion)
        diagnostics = []
        for piece in pieces:
            check = self._validate_auto_split_piece(piece, validation_context=validation_context)
            diagnostics.append(check)
            if not check.get("valid"):
                return {
                    "valid": False,
                    "reason": check.get("reason") or "piece_layout_owner_rejected",
                    "pieces": diagnostics,
                }
        return {"valid": True, "reason": "all_pieces_fit_layout_owner", "pieces": diagnostics}

    def _auto_split_candidate_pieces(self, suggestion):
        axis = str((suggestion or {}).get("split_axis") or "").strip().lower()
        source_length = float((suggestion or {}).get("source_length_mm") or 0.0)
        source_width = float((suggestion or {}).get("source_width_mm") or 0.0)
        size_a = float((suggestion or {}).get("piece_a_size_mm") or 0.0)
        size_b = float((suggestion or {}).get("piece_b_size_mm") or 0.0)
        if axis == "length":
            return [
                {"piece_key": "A", "length_mm": size_a, "width_mm": source_width},
                {"piece_key": "B", "length_mm": size_b, "width_mm": source_width},
            ]
        return [
            {"piece_key": "A", "length_mm": source_length, "width_mm": size_a},
            {"piece_key": "B", "length_mm": source_length, "width_mm": size_b},
        ]

    def _validate_auto_split_piece(self, piece, *, validation_context):
        length = float((piece or {}).get("length_mm") or 0.0)
        width = float((piece or {}).get("width_mm") or 0.0)
        extra = deepcopy(validation_context.get("base_extra") or {})
        geometry = dict(extra.get("geometry") or {})
        geometry["blank_length_mm"] = length
        geometry["blank_width_mm"] = width
        extra["geometry"] = geometry
        layout = dict(extra.get("layout") or {})
        layout["sheet_length_mm"] = 0.0
        layout["sheet_width_mm"] = 0.0
        layout["blank_length_mm"] = length
        layout["blank_width_mm"] = width
        layout["sheet_source"] = "auto_split_candidate_validation"
        extra["layout"] = layout
        piece_result = SimpleNamespace(metrics=SimpleNamespace(extra=extra))
        try:
            slitting_options = engine_v2_runtime_builder._build_slitting_options(
                self.env,
                validation_context.get("stages") or [],
                validation_context.get("stage_contexts") or [],
                spec=validation_context.get("spec"),
                v2_spec=validation_context.get("v2_spec"),
                engine_result=piece_result,
            )
        except Exception as exc:
            _logger.exception("Engine V2 auto split piece validation failed")
            return {
                "valid": False,
                "piece_key": (piece or {}).get("piece_key"),
                "length_mm": length,
                "width_mm": width,
                "reason": str(exc),
            }
        owner_stage_entry = self._resolve_piece_layout_owner_stage(
            slitting_options.get("stages") or []
        )
        owner = self._piece_layout_owner_stage_summary(owner_stage_entry)
        if not owner_stage_entry:
            return {
                "valid": False,
                "piece_key": (piece or {}).get("piece_key"),
                "length_mm": length,
                "width_mm": width,
                "reason": _("No layout owner stage was found for split validation."),
                "layout_owner_stage": None,
            }
        option = self._piece_final_blank_layout_option(
            owner_stage_entry,
            blank_length=length,
            blank_width=width,
        )
        if option and option.get("feasible"):
            return {
                "valid": True,
                "piece_key": (piece or {}).get("piece_key"),
                "length_mm": length,
                "width_mm": width,
                "layout_owner_stage": owner,
            }
        reasons = list((option or {}).get("rejection_reasons") or [])
        reason = self._format_auto_split_piece_rejection_reason(
            piece,
            owner_stage_entry,
            reasons,
        )
        return {
            "valid": False,
            "piece_key": (piece or {}).get("piece_key"),
            "length_mm": length,
            "width_mm": width,
            "reason": reason,
            "layout_owner_stage": owner,
            "rejection_reasons": reasons,
            "owner_stage_diagnostics": {
                "rejected_options": list(owner_stage_entry.get("rejected_options") or []),
                "candidate_workcenters": list(owner_stage_entry.get("candidate_workcenters") or []),
            },
        }

    def _format_auto_split_piece_rejection_reason(self, piece, owner_stage_entry, reasons):
        stage_name = (
            (owner_stage_entry or {}).get("stage_name")
            or (owner_stage_entry or {}).get("stage_code")
            or _("layout owner")
        )
        length = float((piece or {}).get("length_mm") or 0.0)
        width = float((piece or {}).get("width_mm") or 0.0)
        piece_key = (piece or {}).get("piece_key") or "-"
        width_token = "width %.1f mm" % width if width > 0.0 else ""
        length_token = "length %.1f mm" % length if length > 0.0 else ""

        # Prefer the rejection that matches the actual candidate dimensions.
        for reason in list(reasons or []):
            text = str(reason or "")
            if length_token and length_token in text:
                return _("Piece length %(length)s mm exceeds %(stage)s constraint: %(reason)s") % {
                    "length": self._preview_formatter().format_integer(length),
                    "stage": stage_name,
                    "reason": text,
                }
        for reason in list(reasons or []):
            text = str(reason or "")
            if width_token and width_token in text:
                return _("Piece width %(width)s mm exceeds %(stage)s constraint: %(reason)s") % {
                    "width": self._preview_formatter().format_integer(width),
                    "stage": stage_name,
                    "reason": text,
                }
        for reason in list(reasons or []):
            text = str(reason or "")
            if "width" in text and "exceeds" in text:
                return _("Piece %(piece)s is infeasible at %(stage)s: %(reason)s") % {
                    "piece": piece_key,
                    "stage": stage_name,
                    "reason": text,
                }
        for reason in list(reasons or []):
            text = str(reason or "")
            if "length" in text and "exceeds" in text:
                return _("Piece %(piece)s is infeasible at %(stage)s: %(reason)s") % {
                    "piece": piece_key,
                    "stage": stage_name,
                    "reason": text,
                }
        if reasons:
            return _("%(stage)s rejected piece %(piece)s: %(reason)s") % {
                "stage": stage_name,
                "piece": piece_key,
                "reason": "; ".join(str(item) for item in reasons),
            }
        return _("%s rejected the split candidate.") % stage_name

    def _load_auto_split_suggestions(self, result, shadow_runtime, *, qgraph=None, spec=None, plant_param=None, v2_spec=None):
        self.ensure_one()
        commands = [(5, 0, 0)]
        self.auto_split_diagnostics_json = False
        self.auto_split_rejected_suggestions_json = False
        if self._is_accepted_auto_split_result(result):
            piece_plan = self._piece_plan_dict(result)
            blank_length = float(self._get_piece_plan_value(piece_plan, "source_length_mm", 0.0) or 0.0)
            blank_width = float(self._get_piece_plan_value(piece_plan, "source_width_mm", 0.0) or 0.0)
            selected_axis = self._get_piece_plan_value(piece_plan, "split_axis", "") or ""
            if blank_length <= 0.0 or blank_width <= 0.0:
                blank_length, blank_width = self._result_blank_dimensions(result)
            if blank_length > 0.0 and blank_width > 0.0:
                self._load_equal_split_suggestion_lines(
                    blank_length=blank_length,
                    blank_width=blank_width,
                    selected_axis=selected_axis,
                    result=result,
                    qgraph=qgraph,
                    spec=spec,
                    plant_param=plant_param,
                    v2_spec=v2_spec,
                )
            else:
                self.auto_split_suggestion_line_ids = commands
                self.auto_split_diagnostics_json = json.dumps(
                    {"reason": "accepted_split_blank_dimensions_missing"},
                    indent=2,
                    sort_keys=True,
                )
            self.auto_split_suggestions_html = self._render_auto_split_suggestions_html(accepted=True)
            return
        if self.multi_piece_enabled:
            self.auto_split_suggestion_line_ids = commands
            self.auto_split_diagnostics_json = json.dumps(
                {"reason": "explicit_multi_piece_preview"},
                indent=2,
                sort_keys=True,
            )
            self.auto_split_suggestions_html = self._render_auto_split_suggestions_html()
            return
        # Use the actual wizard option lines as the source of truth after
        # _load_slitting_option_selection_ui() has filtered the route-level
        # layout driver. The shadow summary can still contain stale or
        # non-displayed feasibility counts.
        if self.slitting_option_line_ids:
            self.auto_split_suggestion_line_ids = commands
            self.auto_split_diagnostics_json = json.dumps(
                {"reason": "full_blank_layout_already_feasible"},
                indent=2,
                sort_keys=True,
            )
            self.auto_split_suggestions_html = self._render_auto_split_suggestions_html(feasible=True)
            return

        blank_length, blank_width = self._result_blank_dimensions(result)
        if blank_length <= 0.0 or blank_width <= 0.0:
            self.auto_split_suggestion_line_ids = commands
            self.auto_split_diagnostics_json = json.dumps(
                {
                    "reason": "blank_dimensions_missing",
                    "blank_length_mm": blank_length,
                    "blank_width_mm": blank_width,
                },
                indent=2,
                sort_keys=True,
            )
            self.auto_split_suggestions_html = self._render_auto_split_suggestions_html()
            return

        self._load_equal_split_suggestion_lines(
            blank_length=blank_length,
            blank_width=blank_width,
            result=result,
            qgraph=qgraph,
            spec=spec,
            plant_param=plant_param,
            v2_spec=v2_spec,
        )
        self.auto_split_suggestions_html = self._render_auto_split_suggestions_html()

    def _render_auto_split_suggestions_html(self, feasible=False, accepted=False):
        self.ensure_one()
        fmt = self._preview_formatter()
        lines = self.auto_split_suggestion_line_ids.sorted(key=lambda line: (line.option_rank, line.id))
        if feasible:
            return "<p>%s</p>" % _("No split required. Full blank layout has feasible options.")
        if not lines:
            diagnostics = self._deserialize_debug_json(self.auto_split_diagnostics_json)
            html = ["<p>%s</p>" % _("No auto split suggestions are available for this preview.")]
            reason = diagnostics.get("reason")
            if reason:
                html.append(
                    "<div class='alert alert-warning' role='alert'><strong>%s:</strong> %s</div>"
                    % (_("Reason"), html_escape(str(reason)))
                )
            rejected = list(diagnostics.get("rejected_suggestions") or [])
            if rejected:
                first = dict(rejected[0] or {})
                validation = dict(first.get("validation") or {})
                html.append(
                    "<div class='alert alert-secondary' role='alert'>%s</div>"
                    % html_escape(str(validation.get("reason") or _("All split candidates were rejected.")))
                )
            return "\n".join(html)
        html = ["<h3>%s</h3>" % _("Auto Split Alternatives")]
        if accepted:
            html.append(
                "<div class='alert alert-info' role='alert'>%s</div>"
                % _("A split is accepted for this preview. You can switch to another split or clear it.")
            )
        else:
            html.append(
                "<div class='alert alert-warning' role='alert'>%s</div>"
                % _("No feasible full-blank layout option was found. You can accept an equal split and rerun preview.")
            )
        html.append(
            "<table class='table table-sm table-striped'><thead><tr>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "</tr></thead><tbody>"
            % (_("Status"), _("Split"), _("Piece A"), _("Piece B"), _("Reason"))
        )
        for line in lines:
            axis_label = _("Width-wise") if line.split_axis == "width" else _("Length-wise")
            html.append(
                "<tr><td>%s</td><td>%s</td><td>%s mm</td><td>%s mm</td><td>%s</td></tr>"
                % (
                    _("Accepted") if line.is_selected else "",
                    axis_label,
                    fmt.format_integer(line.piece_a_size_mm or 0.0),
                    fmt.format_integer(line.piece_b_size_mm or 0.0),
                    html_escape(line.reason or ""),
                )
            )
        html.append("</tbody></table>")
        return "\n".join(html)

    def _build_piece_layout_planning(self, result, qgraph, spec, plant_param, v2_spec=None):
        self.ensure_one()
        if engine_v2_runtime_builder is None:
            return {"enabled": False, "reason": "stagecontext_runtime_unavailable", "pieces": []}

        piece_plan = self._piece_plan_dict(result)
        if not piece_plan or not bool(self._get_piece_plan_value(piece_plan, "enabled", False)):
            return {"enabled": False, "reason": "multi_piece_not_enabled", "pieces": []}

        pieces = list(self._get_piece_plan_value(piece_plan, "pieces", []) or [])
        if not pieces:
            return {"enabled": False, "reason": "no_piece_lines", "pieces": []}

        try:
            routing = engine_v2_runtime_builder._build_routing_instance(
                self.env,
                spec,
                plant_param,
                qgraph,
                v2_spec=v2_spec,
            )
            stages = list(getattr(routing, "stages", []) or [])
            stage_contexts = engine_v2_runtime_builder._build_stage_contexts(self.env, stages)
        except Exception as exc:
            _logger.exception("Engine V2 piece layout routing build failed")
            return {"enabled": False, "reason": "routing_build_failed", "error": str(exc), "pieces": []}

        base_extra = deepcopy(getattr(getattr(result, "metrics", None), "extra", {}) or {})
        planning = {
            "enabled": True,
            "source": self._get_piece_plan_value(piece_plan, "source", ""),
            "layout_mode": (
                "split_multi_piece"
                if (self._get_piece_plan_value(piece_plan, "source", "") or "") == "accepted_auto_split"
                else "multi_piece"
            ),
            "split_axis": self._get_piece_plan_value(piece_plan, "split_axis", "") or "",
            "split_mode": self._get_piece_plan_value(piece_plan, "split_mode", "") or "",
            "source_length_mm": float(
                self._get_piece_plan_value(piece_plan, "source_length_mm", 0.0) or 0.0
            ),
            "source_width_mm": float(
                self._get_piece_plan_value(piece_plan, "source_width_mm", 0.0) or 0.0
            ),
            "is_stale": bool(getattr(v2_spec, "multi_piece_split_stale", False)) if v2_spec else False,
            "status": "planning_only",
            "materialization_allowed": False,
            "preference_model": "higher_up_then_direct_then_smaller_sheet",
            "pieces": [],
        }

        for index, piece in enumerate(pieces, start=1):
            piece_key = self._get_piece_plan_value(piece, "piece_key", "") or "piece_%s" % index
            piece_name = self._get_piece_plan_value(piece, "piece_name", "") or piece_key
            blank_length = float(self._get_piece_plan_value(piece, "piece_length_mm", 0.0) or 0.0)
            blank_width = float(self._get_piece_plan_value(piece, "piece_width_mm", 0.0) or 0.0)

            extra = deepcopy(base_extra)
            geometry = dict(extra.get("geometry") or {})
            geometry["blank_length_mm"] = blank_length
            geometry["blank_width_mm"] = blank_width
            extra["geometry"] = geometry
            layout = dict(extra.get("layout") or {})
            layout["sheet_length_mm"] = 0.0
            layout["sheet_width_mm"] = 0.0
            layout["blank_length_mm"] = blank_length
            layout["blank_width_mm"] = blank_width
            layout["sheet_source"] = "piece_blank_planning"
            extra["layout"] = layout
            piece_result = SimpleNamespace(metrics=SimpleNamespace(extra=extra))

            try:
                slitting_options = engine_v2_runtime_builder._build_slitting_options(
                    self.env,
                    stages,
                    stage_contexts,
                    spec=spec,
                    v2_spec=v2_spec,
                    engine_result=piece_result,
                )
            except Exception as exc:
                _logger.exception("Engine V2 piece layout planning failed for %s", piece_key)
                planning["pieces"].append(
                    {
                        "piece_key": piece_key,
                        "piece_name": piece_name,
                        "blank_length_mm": blank_length,
                        "blank_width_mm": blank_width,
                        "status": "error",
                        "error": str(exc),
                        "stages": [],
                        "preferred_option": None,
                        "selected_option": None,
                    }
                )
                continue

            stage_entries = []
            preferred_option = None
            selected_option = None
            owner_stage_entry = self._resolve_piece_layout_owner_stage(
                slitting_options.get("stages") or []
            )
            layout_stage = self._piece_layout_owner_stage_summary(owner_stage_entry)
            if not owner_stage_entry:
                planning["pieces"].append(
                    {
                        "piece_key": piece_key,
                        "piece_name": piece_name,
                        "blank_length_mm": blank_length,
                        "blank_width_mm": blank_width,
                        "status": "no_layout_owner",
                        "layout_owner_stage": None,
                        "layout_driving_stage": None,
                        "preferred_option": None,
                        "selected_option": None,
                        "stages": [],
                        "summary": dict(slitting_options.get("summary") or {}),
                    }
                )
                continue

            for stage_entry in [owner_stage_entry]:
                entry_identity = (
                    stage_entry.get("stage_contract_code")
                    or stage_entry.get("stage_code")
                    or str(stage_entry.get("stage_template_id") or "")
                )
                preferred = self._piece_final_blank_layout_option(
                    stage_entry,
                    blank_length=blank_length,
                    blank_width=blank_width,
                )
                if not preferred:
                    preferred = self._fallback_piece_owner_layout_option(
                        stage_entry,
                        blank_length=blank_length,
                        blank_width=blank_width,
                    )
                selected = preferred
                preferred_option = preferred
                selected_option = selected
                stage_entries.append(
                    {
                        "stage_code": stage_entry.get("stage_code") or "",
                        "stage_name": stage_entry.get("stage_name") or "",
                        "stage_type": stage_entry.get("stage_type") or "",
                        "capability_code": stage_entry.get("capability_code") or "",
                        "sequence": stage_entry.get("sequence") or 0,
                        "layout_owner": True,
                        "owner_resolution": layout_stage.get("owner_resolution"),
                        "stage_identity": entry_identity,
                        "blank_dimensions": dict(stage_entry.get("blank_dimensions") or {}),
                        "status": stage_entry.get("status") or "",
                        "rejection_reasons": list(stage_entry.get("rejection_reasons") or []),
                        "preferred_option": preferred,
                        "selected_option": selected,
                        "feasible_options": [preferred] if preferred else [],
                        "rejected_options": list(stage_entry.get("rejected_options") or []),
                        "owner_stage_diagnostics": {
                            "engine_preferred_option": stage_entry.get("preferred_option"),
                            "engine_feasible_options": list(stage_entry.get("feasible_options") or []),
                            "nested_piece_planning_allowed": False,
                        },
                    }
                )
            planning["pieces"].append(
                {
                    "piece_key": piece_key,
                    "piece_name": piece_name,
                    "piece_qty_per_fg": float(
                        self._get_piece_plan_value(piece, "piece_qty_per_fg", 1.0) or 1.0
                    ),
                    "blank_length_mm": blank_length,
                    "blank_width_mm": blank_width,
                    "status": "planned",
                    "layout_owner_stage": layout_stage or None,
                    "layout_driving_stage": layout_stage or None,
                    "preferred_option": preferred_option,
                    "selected_option": selected_option,
                    "stages": stage_entries,
                    "summary": dict(slitting_options.get("summary") or {}),
                }
            )
        return planning

    def _piece_final_blank_layout_option(self, stage_entry, *, blank_length, blank_width):
        """Return the Phase E piece layout: the piece blank itself, 1-up.

        Accepted pieces are final manufacturing blanks in Phase E. Additional
        UPS/slitting options would be nested piece planning and are deferred.
        """

        length = float(blank_length or 0.0)
        width = float(blank_width or 0.0)
        if length <= 0.0 or width <= 0.0:
            return None
        owner_reasons = list((stage_entry or {}).get("rejection_reasons") or [])
        return {
            "rank": 1,
            "up_count": 1,
            "final_sheet_length_mm": length,
            "final_sheet_width_mm": width,
            "orientation_used": "direct",
            "rotated_used": False,
            "feasible": not owner_reasons,
            "planning_mode": "piece_final_blank",
            "nested_piece_planning_allowed": False,
            "rejection_reasons": owner_reasons,
        }

    def _fallback_piece_owner_layout_option(self, stage_entry, *, blank_length, blank_width):
        """Expose the owner-stage 1-up piece sheet when no feasible row exists.

        Phase E is planning-only. The detailed feasibility result, including
        workcenter rejection reasons, stays in raw JSON under the owner stage.
        The main UI should still show the layout inherited by the piece.
        """

        dims = dict((stage_entry or {}).get("blank_dimensions") or {})
        length = float(blank_length or dims.get("blank_length_mm") or 0.0)
        width = float(blank_width or dims.get("blank_width_mm") or 0.0)
        orientation = "direct"
        checks = list(dims.get("checked_orientations") or [])
        if checks:
            first = dict(checks[0] or {})
            orientation = first.get("orientation") or orientation
            length = float(first.get("length_mm") or length or 0.0)
            width = float(first.get("width_mm") or width or 0.0)
        if length <= 0.0 or width <= 0.0:
            return None
        return {
            "rank": 1,
            "up_count": 1,
            "final_sheet_length_mm": length,
            "final_sheet_width_mm": width,
            "orientation_used": orientation,
            "rotated_used": orientation == "rotated",
            "feasible": False,
            "planning_fallback": True,
            "fallback_reason": "no_feasible_owner_stage_option",
            "rejection_reasons": list((stage_entry or {}).get("rejection_reasons") or []),
        }

    def _normalize_piece_layout_option(self, option):
        if not option:
            return None
        option = dict(option or {})
        length = float(
            option.get("final_sheet_length_mm")
            or option.get("candidate_length_mm")
            or 0.0
        )
        width = float(
            option.get("final_sheet_width_mm")
            or option.get("candidate_width_mm")
            or 0.0
        )
        orientation = option.get("orientation_used") or option.get("used_orientation") or ""
        normalized = dict(option)
        normalized["up_count"] = int(float(option.get("up_count") or 0.0))
        normalized["final_sheet_length_mm"] = length
        normalized["final_sheet_width_mm"] = width
        normalized["orientation_used"] = orientation
        normalized["rotated_used"] = bool(
            option.get("rotated_used") or option.get("rotation_used")
        )
        return normalized

    def _resolve_piece_layout_owner_stage(self, stage_entries):
        entries = list(stage_entries or [])
        if not entries:
            return None
        explicit = [
            entry
            for entry in entries
            if bool(entry.get("is_layout_driving_stage"))
        ]
        if explicit:
            return sorted(explicit, key=lambda item: (item.get("sequence") or 0))[0]
        return sorted(entries, key=lambda item: (item.get("sequence") or 0))[0]

    def _piece_layout_owner_stage_summary(self, stage_entry):
        if not stage_entry:
            return {}
        return {
            "stage_template_id": stage_entry.get("stage_template_id"),
            "stage_contract_code": stage_entry.get("stage_contract_code"),
            "stage_code": stage_entry.get("stage_code"),
            "stage_name": stage_entry.get("stage_name"),
            "stage_type": stage_entry.get("stage_type"),
            "capability_code": stage_entry.get("capability_code"),
            "sequence": stage_entry.get("sequence"),
            "owner_resolution": (
                "explicit_layout_driving_stage"
                if stage_entry.get("is_layout_driving_stage")
                else "first_layout_eligible_stage"
            ),
        }

    def _render_piece_layout_planning_html(self, planning):
        self.ensure_one()
        if not planning or not planning.get("enabled"):
            return "<p>%s</p>" % _("Piece layout planning is available only for multi-piece previews.")

        fmt = self._preview_formatter()
        html = ["<h3>%s</h3>" % _("Piece Layout Planning")]
        layout_mode = str(planning.get("layout_mode") or "").strip().lower()
        split_axis = str(planning.get("split_axis") or "").strip().title()
        split_mode = str(planning.get("split_mode") or "").strip().title()
        source_length_mm = float(planning.get("source_length_mm") or 0.0)
        source_width_mm = float(planning.get("source_width_mm") or 0.0)
        is_stale = bool(planning.get("is_stale"))
        source = str(planning.get("source") or "").strip().lower()

        if layout_mode == "split_multi_piece":
            layout_mode_label = _("Split / Multi-Piece")
        elif layout_mode == "multi_piece":
            layout_mode_label = _("Multi-Piece")
        else:
            layout_mode_label = _("Full Blank")

        if is_stale:
            status_label = _("Stale")
            html.append(
                "<div class='alert alert-warning' role='alert'>%s</div>"
                % _(
                    "Accepted split is stale. Please reapply split from Spec -> Layout Planning."
                )
            )
        elif source == "accepted_auto_split":
            status_label = _("Accepted")
            html.append(
                "<div class='alert alert-info' role='alert'>%s</div>"
                % _("Width split is accepted for this spec." if split_axis == "Width" else "Length split is accepted for this spec.")
            )
        else:
            status_label = _("Not Accepted")
            html.append(
                "<div class='alert alert-info' role='alert'>%s</div>"
                % _(
                    "To change or clear this split, go back to Spec -> Layout Planning."
                )
            )

        html.append("<h4>%s</h4>" % _("Current Layout Decision"))
        html.append(
            "<table class='table table-sm table-striped'><tbody>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s x %s mm</td></tr>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "</tbody></table>"
            % (
                _("Layout Mode"),
                html_escape(layout_mode_label),
                _("Split Axis"),
                html_escape(split_axis or "-"),
                _("Split Mode"),
                html_escape(split_mode or "-"),
                _("Source Blank"),
                fmt.format_integer(source_length_mm),
                fmt.format_integer(source_width_mm),
                _("Status"),
                html_escape(status_label),
            )
        )

        html.append("<h4>%s</h4>" % _("Selected Pieces"))
        html.append(
            "<table class='table table-sm table-striped'><thead><tr>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "</tr></thead><tbody>"
            % (_("Piece"), _("Length"), _("Width"), _("Qty / FG"))
        )
        for piece in list(planning.get("pieces") or []):
            owner = dict(piece.get("layout_owner_stage") or {})
            selected = dict(piece.get("selected_option") or {})
            preferred = dict(piece.get("preferred_option") or {})
            stage_rows = list(piece.get("stages") or [])
            owner_stage_payload = dict(stage_rows[0] or {}) if stage_rows else {}
            options = []
            options.extend(list(owner_stage_payload.get("feasible_options") or []))
            if selected and not options:
                fallback = dict(selected)
                fallback.setdefault("rank", 1)
                fallback.setdefault("feasible", False)
                options.append(fallback)

            html.append(
                "<tr><td>%s</td><td>%s mm</td><td>%s mm</td><td>%s</td></tr>"
                % (
                    html_escape(piece.get("piece_name") or piece.get("piece_key") or "-"),
                    fmt.format_integer(piece.get("blank_length_mm") or 0.0),
                    fmt.format_integer(piece.get("blank_width_mm") or 0.0),
                    fmt.format_quantity(piece.get("piece_qty_per_fg") or 1.0),
                )
            )

            alternative_options = []
            for idx, option in enumerate(options, start=1):
                option = self._normalize_piece_layout_option(option) or dict(option or {})
                is_selected = self._piece_layout_option_matches(option, selected)
                if is_selected:
                    continue
                reasons = list(option.get("rejection_reasons") or [])
                feasible = bool(option.get("feasible"))
                reason_text = _("Feasible") if feasible else ", ".join(reasons)
                if not reason_text and option.get("planning_fallback"):
                    reason_text = _("Inherited owner-stage piece layout")
                elif not reason_text:
                    reason_text = "-"
                alternative_options.append(
                    {
                        "rank": option.get("rank") or idx,
                        "sheet_length_mm": option.get("final_sheet_length_mm") or 0.0,
                        "sheet_width_mm": option.get("final_sheet_width_mm") or 0.0,
                        "orientation": str(option.get("orientation_used") or "-").title(),
                        "reason": reason_text,
                    }
                )
        html.append("</tbody></table>")

        flattened_alternatives = []
        for piece in list(planning.get("pieces") or []):
            stage_rows = list(piece.get("stages") or [])
            owner_stage_payload = dict(stage_rows[0] or {}) if stage_rows else {}
            selected = dict(piece.get("selected_option") or {})
            for idx, option in enumerate(list(owner_stage_payload.get("feasible_options") or []), start=1):
                option = self._normalize_piece_layout_option(option) or dict(option or {})
                if self._piece_layout_option_matches(option, selected):
                    continue
                flattened_alternatives.append(
                    {
                        "piece_name": piece.get("piece_name") or piece.get("piece_key") or "-",
                        "rank": option.get("rank") or idx,
                        "sheet_length_mm": option.get("final_sheet_length_mm") or 0.0,
                        "sheet_width_mm": option.get("final_sheet_width_mm") or 0.0,
                        "orientation": str(option.get("orientation_used") or "-").title(),
                        "feasible": bool(option.get("feasible")),
                        "reason": ", ".join(list(option.get("rejection_reasons") or [])) or _("Feasible"),
                    }
                )

        if flattened_alternatives:
            html.append("<h4>%s</h4>" % _("Other Available Alternatives"))
            html.append(
                "<table class='table table-sm table-striped'><thead><tr>"
                "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                "</tr></thead><tbody>"
                % (_("Piece"), _("Rank"), _("Sheet Size"), _("Orientation"), _("Status"))
            )
            for option in flattened_alternatives:
                html.append(
                    "<tr><td>%s</td><td>%s</td><td>%s x %s mm</td><td>%s</td><td>%s</td></tr>"
                    % (
                        html_escape(str(option.get("piece_name") or "-")),
                        html_escape(str(option.get("rank") or "-")),
                        fmt.format_integer(option.get("sheet_length_mm") or 0.0),
                        fmt.format_integer(option.get("sheet_width_mm") or 0.0),
                        html_escape(str(option.get("orientation") or "-")),
                        html_escape(str(option.get("reason") or "-")),
                    )
                )
            html.append("</tbody></table>")
        else:
            html.append("<p class='text-muted'>%s</p>" % _("No feasible full blank option was found."))

        html.append(
            "<div class='alert alert-secondary' role='alert'>%s</div>"
            % _("To change or clear this split, go back to Spec -> Layout Planning.")
        )
        return "\n".join(html)

    def _render_shared_upstream_split_html(self, payload):
        self.ensure_one()
        fmt = self._preview_formatter()
        payload = dict(payload or {})
        debug = dict(payload.get("phase_h_shared_upstream_split_dry_run") or {})
        multi_piece = dict(payload.get("multi_piece") or {})
        strategy = (
            multi_piece.get("multi_piece_strategy")
            or multi_piece.get("strategy")
            or "independent_routed_piece"
        )
        if str(strategy or "").strip().lower() != "shared_upstream_split":
            return "<p>%s</p>" % _("Shared Upstream Split is not selected for this preview.")
        if not debug:
            return "<p>%s</p>" % _("Shared Upstream Split diagnostics were not generated. Rerun the preview.")

        guard = dict(debug.get("guard") or {})
        source_blank = dict(debug.get("source_blank") or {})
        route = dict(debug.get("route_segmentation") or {})
        allocations = {
            str(row.get("piece_key") or ""): dict(row or {})
            for row in list(debug.get("allocation_percentages") or [])
        }

        html = ["<h3>%s</h3>" % _("Shared Upstream Split")]
        if guard.get("passed"):
            html.append(
                "<div class='alert alert-info'>%s</div>"
                % _("Dry-run only. BoM creation will be enabled in Phase H3.")
            )
        else:
            blockers = ", ".join(str(item) for item in list(guard.get("blockers") or []))
            html.append(
                "<div class='alert alert-warning'><strong>%s</strong><br/>%s</div>"
                % (_("Blocked"), html_escape(blockers or _("No blocker details were returned.")))
            )

        html.append(
            "<table class='table table-sm table-striped'><tbody>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s x %s mm</td></tr>"
            "<tr><th>%s</th><td>%s cm</td></tr>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "</tbody></table>"
            % (
                _("Strategy"),
                _("Shared Upstream Split"),
                _("Split After Stage"),
                html_escape(str(debug.get("split_after_stage") or "-")),
                _("Source Blank"),
                fmt.format_integer(source_blank.get("length_mm") or 0.0),
                fmt.format_integer(source_blank.get("width_mm") or 0.0),
                _("Source Deckle"),
                fmt.format_quantity(debug.get("source_deckle_cm") or 0.0),
                _("Shared Stages"),
                html_escape(" > ".join(route.get("shared_stage_keys") or []) or "-"),
                _("Post-Split Branch"),
                html_escape(" > ".join(route.get("post_split_branch_stage_keys") or []) or "-"),
                _("Merge Stage"),
                html_escape(str(route.get("merge_stage_key") or "-")),
            )
        )

        h2_debug_for_flow = dict(payload.get("phase_h2_shared_upstream_materialization_dry_run") or {})
        flow = dict(
            payload.get("shared_upstream_flow")
            or h2_debug_for_flow.get("shared_upstream_flow")
            or ((h2_debug_for_flow.get("materialization_plan") or {}).get("metadata") or {}).get("shared_upstream_flow")
            or {}
        )
        tree_lines = list(flow.get("tree_text_lines") or [])
        if tree_lines:
            split_node = dict(flow.get("split_node") or {})
            if split_node.get("real_slitting_bom"):
                flow_note = _("Equal split creates a real split BoM with a Slitting operation. No custom MO is created.")
            else:
                flow_note = _("Diagnostic view only. Split node is logical metadata; no custom split operation or MO is created.")
            html.append("<h4>%s</h4>" % _("Shared Upstream Flow"))
            html.append(
                "<div class='alert alert-info'>%s</div>"
                % flow_note
            )
            html.append(
                "<pre style='white-space: pre-wrap; background: #f8f9fa; padding: 8px;'>%s</pre>"
                % html_escape("\n".join(str(line) for line in tree_lines))
            )

        html.append("<h4>%s</h4>" % _("Split Outputs"))
        html.append(
            "<table class='table table-sm table-striped'><thead><tr>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "</tr></thead><tbody>"
            % (_("Piece"), _("Length"), _("Width"), _("Deckle"), _("Allocation"))
        )
        for output in list(debug.get("split_outputs") or []):
            key = str(output.get("piece_key") or "")
            allocation = allocations.get(key) or {}
            html.append(
                "<tr><td>%s</td><td>%s mm</td><td>%s mm</td><td>%s cm</td><td>%s%%</td></tr>"
                % (
                    html_escape(str(output.get("piece_name") or key or "-")),
                    fmt.format_integer(output.get("length_mm") or 0.0),
                    fmt.format_integer(output.get("width_mm") or 0.0),
                    fmt.format_quantity(output.get("deckle_cm") or 0.0),
                    fmt.format_quantity(allocation.get("percentage") or 0.0),
                )
            )
        html.append("</tbody></table>")

        checks = list((debug.get("diagnostics") or {}).get("capacity_checks") or [])
        if checks:
            html.append("<h4>%s</h4>" % _("Feasibility Diagnostics"))
            html.append(
                "<table class='table table-sm'><thead><tr>"
                "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                "</tr></thead><tbody>"
                % (_("Scope"), _("Stage"), _("Status"), _("Reason"))
            )
            for check in checks:
                html.append(
                    "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (
                        html_escape(str(check.get("scope") or "-")),
                        html_escape(str(check.get("stage_name") or check.get("stage_key") or "-")),
                        html_escape(str(check.get("status") or "-")),
                        html_escape(str(check.get("reason") or "-")),
                    )
                )
            html.append("</tbody></table>")

        h2_debug = dict(payload.get("phase_h2_shared_upstream_materialization_dry_run") or {})
        if h2_debug:
            html.append("<h4>%s</h4>" % _("H2 Materialization Dry-Run"))
            html.append(
                "<div class='alert alert-info'>%s</div>"
                % _("Dry-run only. Products, BoMs, and MOs are not created until Phase H3.")
            )
            summary = dict(h2_debug.get("candidate_summary") or {})
            chain = dict(summary.get("dependency_chain") or {})
            tree_lines = list(chain.get("tree_text_lines") or [])
            if tree_lines:
                html.append("<h5>%s</h5>" % _("Parent / Child Chain"))
                html.append(
                    "<pre style='white-space: pre-wrap; background: #f8f9fa; padding: 8px;'>%s</pre>"
                    % html_escape("\n".join(str(line) for line in tree_lines))
                )
            chain_rows = list(chain.get("all_candidates") or [])
            if chain_rows:
                html.append("<h5>%s</h5>" % _("Candidate Chain Diagnostics"))
                html.append(
                    "<table class='table table-sm'><thead><tr>"
                    "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                    "</tr></thead><tbody>"
                    % (
                        _("Candidate"),
                        _("Parent"),
                        _("Role"),
                        _("Decision"),
                        _("Matched Product"),
                        _("Matched BoM"),
                    )
                )
                for row in chain_rows:
                    html.append(
                        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                        % (
                            html_escape(str(row.get("candidate_key") or "-")),
                            html_escape(str(row.get("parent_candidate_key") or "-")),
                            html_escape(str(row.get("product_role") or "-")),
                            html_escape(str(row.get("reuse_create_decision") or "-")),
                            html_escape(str(row.get("matched_product_id") or "-")),
                            html_escape(str(row.get("matched_bom_id") or "-")),
                        )
                    )
                html.append("</tbody></table>")
            product_rows = list(summary.get("products") or [])
            bom_rows = list(summary.get("boms") or [])
            if product_rows:
                html.append("<h5>%s</h5>" % _("Product Candidates"))
                html.append(
                    "<table class='table table-sm'><thead><tr>"
                    "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                    "</tr></thead><tbody>"
                    % (_("Candidate"), _("Reuse Status"), _("Resolver"), _("Blockers"))
                )
                for row in product_rows:
                    html.append(
                        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                        % (
                            html_escape(str(row.get("candidate_key") or "-")),
                            html_escape(str(row.get("reuse_status") or "-")),
                            html_escape(str(row.get("resolver_action") or row.get("resolver_reason") or "-")),
                            html_escape(", ".join(str(item) for item in list(row.get("blockers") or [])) or "-"),
                        )
                    )
                html.append("</tbody></table>")
            if bom_rows:
                html.append("<h5>%s</h5>" % _("BoM Candidates"))
                html.append(
                    "<table class='table table-sm'><thead><tr>"
                    "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                    "</tr></thead><tbody>"
                    % (_("Candidate"), _("Reuse Status"), _("Resolver"), _("Blockers"))
                )
                for row in bom_rows:
                    html.append(
                        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                        % (
                            html_escape(str(row.get("candidate_key") or "-")),
                            html_escape(str(row.get("reuse_status") or "-")),
                            html_escape(str(row.get("resolver_action") or row.get("resolver_reason") or "-")),
                            html_escape(", ".join(str(item) for item in list(row.get("blockers") or [])) or "-"),
                        )
                    )
                html.append("</tbody></table>")
        return "\n".join(html)

    def _piece_layout_option_matches(self, left, right):
        if not left or not right:
            return False
        try:
            return (
                int(float(left.get("up_count") or 0)) == int(float(right.get("up_count") or 0))
                and abs(
                    float(left.get("final_sheet_length_mm") or 0.0)
                    - float(right.get("final_sheet_length_mm") or 0.0)
                )
                <= 1e-6
                and abs(
                    float(left.get("final_sheet_width_mm") or 0.0)
                    - float(right.get("final_sheet_width_mm") or 0.0)
                )
                <= 1e-6
                and str(left.get("orientation_used") or "").lower()
                == str(right.get("orientation_used") or "").lower()
            )
        except Exception:
            return False

    def _accepted_auto_split_layout_html(self, result):
        self.ensure_one()
        piece_plan = getattr(result, "piece_plan", None)
        if not piece_plan:
            extra = getattr(getattr(result, "metrics", None), "extra", {}) or {}
            if isinstance(extra, dict):
                piece_plan = extra.get("multi_piece") or {}
        if not piece_plan:
            return ""

        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        source = (_get(piece_plan, "source", "") or "").strip()
        if source != "accepted_auto_split":
            return ""

        fmt = self._preview_formatter()
        split_axis = _get(piece_plan, "split_axis", "") or ""
        split_mode = _get(piece_plan, "split_mode", "") or ""
        source_length = float(_get(piece_plan, "source_length_mm", 0.0) or 0.0)
        source_width = float(_get(piece_plan, "source_width_mm", 0.0) or 0.0)
        pieces = list(_get(piece_plan, "pieces", []) or [])

        html = ["<h3>%s</h3>" % _("Accepted Multi-Piece Layout")]
        html.append(
            "<div class='alert alert-info' role='alert'>%s</div>"
            % _(
                "An auto split has been accepted for this preview. "
                "Materialization remains disabled until multi-piece BoM generation is implemented."
            )
        )
        html.append(
            "<table class='table table-sm table-striped'><tbody>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s x %s mm</td></tr>"
            "</tbody></table>"
            % (
                _("Split Axis"),
                html_escape(str(split_axis or "-").title()),
                _("Split Mode"),
                html_escape(str(split_mode or "-").title()),
                _("Source Blank"),
                fmt.format_integer(source_length),
                fmt.format_integer(source_width),
            )
        )
        html.append("<h4>%s</h4>" % _("Selected Pieces"))
        html.append(
            "<table class='table table-sm table-striped'><thead><tr>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "</tr></thead><tbody>"
            % (_("Piece"), _("Length"), _("Width"), _("Qty / FG"))
        )
        for piece in pieces:
            name = _get(piece, "piece_name", "") or _get(piece, "piece_key", "")
            html.append(
                "<tr><td>%s</td><td>%s mm</td><td>%s mm</td><td>%s</td></tr>"
                % (
                    html_escape(str(name or "-")),
                    fmt.format_integer(_get(piece, "piece_length_mm", 0.0) or 0.0),
                    fmt.format_integer(_get(piece, "piece_width_mm", 0.0) or 0.0),
                    fmt.format_quantity(_get(piece, "piece_qty_per_fg", 1.0) or 1.0),
                )
            )
        html.append("</tbody></table>")
        return "\n".join(html)

    def _active_multi_piece_layout_html(self, result):
        self.ensure_one()
        piece_plan = getattr(result, "piece_plan", None)
        if not piece_plan:
            extra = getattr(getattr(result, "metrics", None), "extra", {}) or {}
            if isinstance(extra, dict):
                piece_plan = extra.get("multi_piece") or {}
        if not piece_plan and self.v2_spec_id and getattr(self.v2_spec_id, "multi_piece_enabled", False):
            pieces = []
            try:
                lines = self.v2_spec_id.multi_piece_line_ids.sorted(
                    lambda line: (line.piece_index or 0, line.id or 0)
                )
            except Exception:
                lines = getattr(self.v2_spec_id, "multi_piece_line_ids", []) or []
            for line in list(lines or []):
                pieces.append(
                    {
                        "piece_key": getattr(line, "piece_key", "") or "",
                        "piece_name": getattr(line, "piece_name", "") or "",
                        "piece_index": getattr(line, "piece_index", 0) or 0,
                        "piece_qty_per_fg": getattr(line, "piece_qty_per_fg", 1.0) or 1.0,
                        "piece_length_mm": getattr(line, "piece_length_mm", 0.0) or 0.0,
                        "piece_width_mm": getattr(line, "piece_width_mm", 0.0) or 0.0,
                    }
                )
            piece_plan = {
                "enabled": True,
                "source": getattr(self.v2_spec_id, "multi_piece_source", "") or "explicit_multi_piece_design",
                "split_axis": getattr(self.v2_spec_id, "multi_piece_split_axis", "") or "",
                "split_mode": getattr(self.v2_spec_id, "multi_piece_split_mode", "") or "",
                "source_length_mm": getattr(self.v2_spec_id, "multi_piece_source_length_mm", 0.0) or 0.0,
                "source_width_mm": getattr(self.v2_spec_id, "multi_piece_source_width_mm", 0.0) or 0.0,
                "pieces": pieces,
            }
        if not piece_plan:
            return ""

        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        if not bool(_get(piece_plan, "enabled", False)):
            return ""

        source = (_get(piece_plan, "source", "") or self.multi_piece_source or "").strip()
        if source == "accepted_auto_split":
            return self._accepted_auto_split_layout_html(result)

        fmt = self._preview_formatter()
        split_axis = _get(piece_plan, "split_axis", "") or self.multi_piece_split_axis or ""
        split_mode = _get(piece_plan, "split_mode", "") or self.multi_piece_split_mode or ""
        source_length = float(_get(piece_plan, "source_length_mm", 0.0) or 0.0)
        source_width = float(_get(piece_plan, "source_width_mm", 0.0) or 0.0)
        pieces = list(_get(piece_plan, "pieces", []) or [])

        html = ["<h3>%s</h3>" % _("Multi-Piece Layout")]
        html.append(
            "<div class='alert alert-info' role='alert'>%s</div>"
            % _(
                "This preview is using an explicit multi-piece definition. "
                "Full-blank slitting options are not shown here; piece-level feasibility is shown in Piece Layout Planning."
            )
        )
        html.append(
            "<table class='table table-sm table-striped'><tbody>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s</td></tr>"
            "<tr><th>%s</th><td>%s x %s mm</td></tr>"
            "</tbody></table>"
            % (
                _("Split Axis"),
                html_escape(str(split_axis or "-").title()),
                _("Split Mode"),
                html_escape(str(split_mode or "-").title()),
                _("Source Blank"),
                fmt.format_integer(source_length),
                fmt.format_integer(source_width),
            )
        )
        if pieces:
            html.append("<h4>%s</h4>" % _("Selected Pieces"))
            html.append(
                "<table class='table table-sm table-striped'><thead><tr>"
                "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                "</tr></thead><tbody>"
                % (_("Piece"), _("Length"), _("Width"), _("Qty / FG"))
            )
            for piece in pieces:
                name = _get(piece, "piece_name", "") or _get(piece, "piece_key", "")
                html.append(
                    "<tr><td>%s</td><td>%s mm</td><td>%s mm</td><td>%s</td></tr>"
                    % (
                        html_escape(str(name or "-")),
                        fmt.format_integer(_get(piece, "piece_length_mm", 0.0) or 0.0),
                        fmt.format_integer(_get(piece, "piece_width_mm", 0.0) or 0.0),
                        fmt.format_quantity(_get(piece, "piece_qty_per_fg", 1.0) or 1.0),
                    )
                )
            html.append("</tbody></table>")
        return "\n".join(html)

    def action_run_debug(self):
        self.ensure_one()
        self._ensure_engine_available()
        self._sync_multi_piece_controls_from_v2_spec()
        self._sync_selected_layout_from_v2_spec()
        self._sync_consumable_controls_to_v2_spec()

        spec = self._active_spec_record()
        if not spec:
            raise UserError(_("Please select a legacy spec or an Engine V2 spec."))
        plant_param = self.plant_id
        if not plant_param and getattr(spec, "plant_id", False):
            plant_param = spec.plant_id
        if not plant_param:
            Plant = self.env["corr.plant.param"].sudo()
            plant_param = Plant.search([], limit=1, order="id asc")

        if not plant_param:
            raise UserError(_("No plant parameters found (corr.plant.param)."))
        if self.plant_id != plant_param:
            self.plant_id = plant_param

        # Optional Engine V2 manual-sheet validation when a V2 spec
        # context is explicitly provided on the wizard.
        v2_spec = self.v2_spec_id
        if v2_spec:
            if (
                getattr(v2_spec, "multi_piece_enabled", False)
                and getattr(v2_spec, "multi_piece_source", "")
                == "accepted_auto_split"
                and getattr(v2_spec, "multi_piece_split_stale", False)
                and not self.env.context.get("allow_stale_accepted_split_for_planning")
            ):
                raise UserError(
                    _(
                        "Planning inputs changed after the accepted split. "
                        "Refresh layout options and reapply split before running preview."
                    )
                )
            layout_policy = v2_spec.layout_policy_id
            if layout_policy and (layout_policy.sheet_source or "") == "manual":
                if not v2_spec.manual_sheet_length_mm or not v2_spec.manual_sheet_width_mm:
                    raise UserError(
                        _(
                            "The selected Engine V2 Spec uses a manual sheet layout policy, "
                            "but manual sheet length and/or width are not set.\n"
                            "Please fill manual sheet dimensions on the Engine V2 Spec before running the preview."
                        )
                    )

        try:
            order_qty = float(self.order_qty or 0.0)
        except Exception:
            order_qty = 0.0
        if order_qty <= 0.0:
            order_qty = 1.0
            self.order_qty = order_qty

        # 1) Build engine inputs
        spec_input = build_engine_spec_input(spec, sfg_mode="staged")
        plant_profile_defaults = build_plant_profile(plant_param)
        consumable_state = self._effective_consumable_state(
            plant_profile=plant_profile_defaults,
            spec=v2_spec,
        )
        plant_profile = build_plant_profile(plant_param, options=consumable_state)

        # 2) Run Engine V2
        options = {
            "order_qty": order_qty,
            "allow_infeasible_derived_sheet_preview": True,
        }
        multi_piece_options = self._build_multi_piece_options(spec_input)
        if multi_piece_options:
            options["multi_piece"] = multi_piece_options
        try:
            result = engine_v2_compute(spec_input, plant_profile, options=options)
        except ValueError as e:
            # Surface engine-level validation (e.g. infeasible derived sheet)
            # as a user-facing preview error instead of a generic traceback.
            raise UserError(_("Engine V2 validation failed: %s") % (e,)) from e
        if v2_spec:
            self._persist_multi_piece_source_dimensions_from_result(v2_spec, result)

        # 3) Quantity StageGraph and BOM structure
        try:
            qgraph = getattr(result, "quantity_stage_graph", None)
        except Exception:
            qgraph = None
        if not qgraph:
            qgraph = build_stage_graph(result, order_qty)

        bom_struct = build_bom_structure(result, order_qty)

        # Phase 8 shadow runtime builder: construct stage-first runtime
        # structures in parallel, without affecting current behaviour.
        shadow_runtime = None
        try:
            if engine_v2_runtime_builder is not None:
                shadow_runtime = engine_v2_runtime_builder.build_shadow_runtime(
                    self.env,
                    spec,
                    plant_param,
                    qgraph,
                    order_qty=order_qty,
                    v2_spec=v2_spec,
                    engine_result=result,
                    consumable_state=consumable_state,
                )
        except Exception:  # pragma: no cover - defensive best-effort
            _logger.exception("Phase 8 shadow runtime builder failed in debug wizard")
            shadow_runtime = None

        # Phase 8 parity report: compare shadow runtime against current Engine V2
        # structures. This is debug-only and must never affect behaviour.
        if shadow_runtime:
            try:
                parity_report = self._compute_phase8_parity(result, qgraph, shadow_runtime)
                if isinstance(parity_report, dict):
                    shadow_runtime["parity_report"] = parity_report
            except Exception:  # pragma: no cover - defensive best-effort
                _logger.exception("Phase 8 parity computation failed in debug wizard")
        initial_shadow_runtime = deepcopy(shadow_runtime or {})

        selected_layout = self._resolve_layout_option_selection(shadow_runtime)
        self._apply_selected_layout_to_metrics(result, selected_layout)

        # Layout selection changes boxes_per_sheet / layout_blanks_per_sheet.
        # Rebuild quantity-dependent structures after the selected route-level
        # layout is applied; otherwise preview can show selected 2-up but keep
        # stale default qgraph quantities such as ceil(order_qty / 3).
        qgraph = build_stage_graph(result, order_qty)
        bom_struct = build_bom_structure(result, order_qty)
        try:
            result.quantity_stage_graph = qgraph
        except Exception:
            pass

        if self._accepted_auto_split_full_blank_has_feasible_layout(
            result,
            qgraph,
            spec,
            plant_param,
            v2_spec=v2_spec,
        ):
            self._clear_accepted_auto_split_state()
            return self.action_run_debug()

        # Rebuild StageContext runtime against the selected-layout qgraph so
        # stage quantities, operation quantities, and summary buckets align.
        shadow_runtime = None
        try:
            if engine_v2_runtime_builder is not None:
                shadow_runtime = engine_v2_runtime_builder.build_shadow_runtime(
                    self.env,
                    spec,
                    plant_param,
                    qgraph,
                    order_qty=order_qty,
                    v2_spec=v2_spec,
                    engine_result=result,
                )
        except Exception:  # pragma: no cover - defensive best-effort
            _logger.exception("Phase 8 shadow runtime builder failed after layout selection in debug wizard")
            shadow_runtime = None

        if initial_shadow_runtime and self._preview_has_feasible_layout_options(initial_shadow_runtime):
            rebuilt_has_options = self._preview_has_feasible_layout_options(shadow_runtime)
            if not rebuilt_has_options:
                shadow_runtime = dict(shadow_runtime or {})
                shadow_runtime["slitting_options"] = deepcopy(
                    (initial_shadow_runtime or {}).get("slitting_options") or {}
                )
                shadow_runtime["slitting_options_restored_from_initial"] = True

        if shadow_runtime:
            try:
                shadow_runtime["selected_layout"] = selected_layout
                parity_report = self._compute_phase8_parity(result, qgraph, shadow_runtime)
                if isinstance(parity_report, dict):
                    shadow_runtime["parity_report"] = parity_report
            except Exception:  # pragma: no cover - defensive best-effort
                _logger.exception("Phase 8 parity computation failed after layout selection in debug wizard")

        piece_layout_planning = self._build_piece_layout_planning(
            result,
            qgraph,
            spec,
            plant_param,
            v2_spec=v2_spec,
        )

        piece_specific_rm_requirements = self._build_piece_specific_rm_requirements_preview(
            result,
            spec,
            piece_layout_planning=piece_layout_planning,
        )
        if piece_specific_rm_requirements:
            try:
                result.rm_requirements = piece_specific_rm_requirements
            except Exception:
                pass

        # Resolve paper (liner/medium) requirements against product variants
        # using the active route-level layout selection when available.
        paper_resolution = self._build_paper_resolution(
            result,
            bom_struct,
            layout_selection=selected_layout,
        )
        self._apply_g1_piece_resolution_to_preview(
            result,
            bom_struct,
            paper_resolution,
            piece_layout_planning,
        )

        # Compute material costing diagnostics based on resolved RM
        # requirements and mapped products. This must not alter engine
        # quantities or BoM structures; it is preview-only.
        try:
            costing = self._compute_cost_summary(
                result,
                order_qty,
                paper_resolution=paper_resolution,
                consumable_state=consumable_state,
            )
        except Exception:  # pragma: no cover - defensive best-effort
            _logger.exception("Engine V2 costing computation failed")
            costing = None

        # Compute operation costing (machine + labor) per StageGraph
        # quantity stage for the current plant. This is also preview-only
        # and must not mutate engine quantities or BoM structures.
        op_costing = None
        try:
            op_costing = self._compute_operation_costing(qgraph, costing, plant_param)
        except Exception:  # pragma: no cover - defensive best-effort
            _logger.exception("Engine V2 operation costing computation failed")
            op_costing = None

        # Merge operation costing into the costing diagnostics structure so
        # that summary HTML and raw_json can surface combined totals while
        # keeping material costing logic unchanged.
        if costing and op_costing:
            total_material = float(costing.get("total_cost_for_order", 0.0) or 0.0)
            total_operation = float(op_costing.get("total_operation_cost", 0.0) or 0.0)
            total_production = total_material + total_operation

            costing = dict(costing)
            costing["total_operation_cost"] = total_operation
            costing["total_production_cost"] = total_production
            stages_info = op_costing.get("stages") or {}
            if stages_info:
                costing["stage_costs"] = stages_info
        elif op_costing and not costing:
            # Edge-case: no material costing (e.g. no rm_requirements)
            # but operation costing is available. Expose a minimal block.
            total_operation = float(op_costing.get("total_operation_cost", 0.0) or 0.0)
            total_material = float(op_costing.get("total_material_cost", 0.0) or 0.0)
            total_production = total_material + total_operation
            costing = {
                "order_qty": float(order_qty or 0.0),
                "currency": {},
                "total_cost_per_fg": 0.0,
                "total_cost_for_order": total_material,
                "cost_source": "",
                "effective_cost_sources": [],
                "components": [],
                "rm_cost_index": {},
                "missing_cost": [],
                "total_operation_cost": total_operation,
                "total_production_cost": total_production,
                "stage_costs": op_costing.get("stages") or {},
            }

        # Validate layout: blanks_per_sheet must be non-zero to proceed.
        metrics = getattr(result, "metrics", None)
        extra = getattr(metrics, "extra", {}) or {}
        blanks_per_sheet = int(extra.get("layout_blanks_per_sheet", 0) or 0)
        if blanks_per_sheet <= 0:
            raise UserError(
                _(
                    "Engine V2 layout could not be resolved (blanks_per_sheet = 0).\n"
                    "Please ensure that sheet size is defined either on the plant "
                    "or on the Engine V2 Spec, and that box/blank dimensions are valid."
                )
            )

        # Augment metrics with order-level adhesive totals when the
        # engine reported per-box adhesive usage. This keeps summary
        # reporting simple while respecting the preview -> create
        # contract (no recompute, just derived diagnostics).
        try:
            if metrics and isinstance(extra, dict):
                per_box_adh = float(extra.get("adhesive_total_kg_per_box", 0.0) or 0.0)
                if per_box_adh > 0.0:
                    extra = dict(extra)
                    extra.setdefault(
                        "adhesive_total_kg_for_order",
                        per_box_adh * float(order_qty or 0.0),
                    )
                    metrics.extra = extra
        except Exception:
            # Adhesive totals are best-effort diagnostics and must not
            # affect preview validity.
            pass

        preview_model = self._build_stagecontext_preview_model(
            shadow_runtime,
            result,
            costing,
            qgraph=qgraph,
            paper_resolution=paper_resolution,
        )
        self._apply_g1_resolution_to_preview_model(
            preview_model,
            result,
            paper_resolution,
            bom_struct,
        )
        self._apply_multi_piece_fg_rollup_to_preview_model(
            preview_model,
            result,
            order_qty,
        )
        piece_specific_rm_breakdown = self._build_piece_specific_rm_breakdown_payload(
            result,
            order_qty,
        )
        if preview_model is not None and piece_specific_rm_breakdown:
            preview_model["piece_specific_rm_breakdown"] = piece_specific_rm_breakdown
        if preview_model is not None:
            preview_model["consumable_state"] = (
                consumable_state_signature(consumable_state)
                if consumable_state_signature is not None
                else dict(consumable_state or {})
            )
        piece_specific_costing = self._build_piece_specific_costing_payload(costing)
        if costing is not None and piece_specific_costing:
            costing["piece_specific_costing"] = piece_specific_costing
        self._suppress_unit_top_sheet_legacy_preview_materials(preview_model)
        preview_backend_selected = (self.preview_backend or "legacy").strip().lower()
        preview_backend_applied = "legacy"
        preview_status = False

        # 4) Render sections
        warnings_text = self._render_warnings_text(bom_struct)
        if preview_backend_selected == "stagecontext" and preview_model:
            preview_backend_applied = "stagecontext"
            warnings_text = self._render_preview_model_warnings_text(preview_model, extra_text=warnings_text)
            self.summary_html = self._render_preview_model_summary_html(preview_model)
            self.rm_breakdown_html = self._render_preview_model_rm_breakdown_html(preview_model)
            self.stage_flow_html = self._render_preview_model_stage_flow_html(preview_model)
            self.bom_preview_html = self._render_preview_model_bom_html(preview_model)
        else:
            if preview_backend_selected == "stagecontext":
                preview_status = _(
                    "StageContext preview was requested, but the shadow runtime adapter was unavailable. "
                    "Legacy preview was rendered instead."
                )
            self.summary_html = self._render_summary_html(result, qgraph, order_qty, plant_param, warnings_text, costing)
            self.rm_breakdown_html = self._render_rm_breakdown_html(result, order_qty, costing)
            self.stage_flow_html = self._render_stage_flow_html(qgraph)
            self.bom_preview_html = self._render_bom_preview_html(bom_struct)
        self.multi_piece_html = self._render_multi_piece_html(result)
        self.stage_costing_html = self._render_stage_costing_html(costing)
        self._set_costing_summary_totals(costing)
        self._load_slitting_option_selection_ui(
            shadow_runtime,
            selected_map={selected_layout.get("stage_identity"): selected_layout.get("option")}
            if selected_layout.get("stage_identity") and selected_layout.get("option")
            else None,
        )
        self._load_auto_split_suggestions(
            result,
            shadow_runtime,
            qgraph=qgraph,
            spec=spec,
            plant_param=plant_param,
            v2_spec=v2_spec,
        )
        accepted_split_layout_html = self._accepted_auto_split_layout_html(result)
        if accepted_split_layout_html:
            self.slitting_options_html = accepted_split_layout_html
            self.slitting_option_line_ids = [(5, 0, 0)]
        elif not self.slitting_option_line_ids:
            multi_piece_layout_html = self._active_multi_piece_layout_html(result)
            if multi_piece_layout_html:
                self.slitting_options_html = multi_piece_layout_html
        self.piece_layout_planning_html = self._render_piece_layout_planning_html(piece_layout_planning)
        self.warnings_text = warnings_text
        # Surface/printing summary for raw JSON so that preview clearly
        # reflects the effective spec-level inputs used by the engine.
        try:
            surface_summary = {
                "printing_required": bool(getattr(spec, "printing_required", False)),
                "print_colors": int(getattr(spec, "print_colors", 0) or 0),
                "coverage_pct": float(getattr(spec, "coverage_pct", 0.0) or 0.0),
                "top_layer_mode_ui": getattr(spec, "top_layer_mode_ui", ""),
                "top_sheet_product_id": getattr(
                    getattr(spec, "top_sheet_product_id", None), "id", None
                ),
                "surface_profile_id": getattr(
                    getattr(spec, "surface_profile_id", None), "id", None
                ),
            }
        except Exception:
            surface_summary = {}

        self.raw_json = self._render_raw_json(
            result,
            qgraph,
            bom_struct,
            paper_resolution,
            costing,
            shadow_runtime=shadow_runtime,
            spec_surface=surface_summary,
            preview_model=preview_model,
            preview_backend_selected=preview_backend_selected,
            preview_backend_applied=preview_backend_applied,
            selected_layout=selected_layout,
            piece_layout_planning=piece_layout_planning,
            multi_piece_options=multi_piece_options,
            consumable_state=consumable_state,
        )
        try:
            self.shared_upstream_split_html = self._render_shared_upstream_split_html(
                json.loads(self.raw_json or "{}")
            )
        except Exception:
            self.shared_upstream_split_html = "<p>%s</p>" % _(
                "Shared Upstream Split diagnostics could not be rendered."
            )
        self.raw_json_tools_html = self._render_raw_json_tools_html()

        # Mark preview as current
        self.preview_is_current = True
        self.preview_backend_applied = preview_backend_applied
        self.preview_warning = preview_status
        self._sync_selected_layout_to_v2_spec(selected_layout)

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }

    def _render_raw_json_tools_html(self):
        return (
            "<div class='d-flex align-items-center gap-2 mb-2'>"
            "<span class='text-muted small'>%s</span>"
            "<span class='text-muted small o_corr_copy_raw_json_status'></span>"
            "</div>"
        ) % html_escape(_("Full JSON is retained for server actions; this tab shows a safe diagnostics summary."))

    @api.depends("raw_json")
    def _compute_raw_json_summary_html(self):
        for wizard in self:
            wizard.raw_json_summary_html = (
                wizard._render_raw_json_summary_html(wizard.raw_json)
                if wizard.raw_json
                else False
            )

    def _render_raw_json_summary_html(self, raw_json):
        try:
            payload = json.loads(raw_json or "{}")
        except Exception as exc:
            return "<p class='text-danger'>%s</p>" % html_escape(
                _("Raw JSON summary could not be rendered: %s") % exc
            )

        def _cell(value):
            if value in (None, False):
                value = ""
            return "<td>%s</td>" % html_escape(str(value))

        materialization_debug = (
            payload.get("phase7b_materialization_dry_run")
            or payload.get("phase_h2_shared_upstream_materialization")
            or payload.get("phase_g2_routed_piece_materialization")
            or {}
        )
        materialization_plan = dict((materialization_debug or {}).get("materialization_plan") or {})
        resolver = dict((materialization_debug or {}).get("resolver_dry_run") or {})
        boms = list(materialization_plan.get("boms") or [])
        preview_model = dict(payload.get("phase5_preview_model") or {})
        shadow_runtime = dict(payload.get("phase8_shadow_runtime") or {})
        preview_materials = list(preview_model.get("materials") or [])
        stage_contexts = list(shadow_runtime.get("stage_contexts") or [])

        bom_rows = []
        for bom in boms[:30]:
            lines = list((bom or {}).get("lines") or [])
            operations = list((bom or {}).get("operations") or [])
            operation = operations[0] if operations else {}
            previous = [
                line for line in lines
                if str((line or {}).get("line_type") or "") == "previous_output"
            ]
            rm_lines = [
                line for line in lines
                if str((line or {}).get("line_type") or "") in ("rm_input", "consumable_input")
            ]
            bom_rows.append(
                "<tr>%s%s%s%s%s%s%s</tr>"
                % (
                    _cell((bom or {}).get("segment_key")),
                    _cell(operation.get("stage_code") or operation.get("operation_stage_code")),
                    _cell(operation.get("stage_name") or operation.get("operation_name")),
                    _cell((bom or {}).get("product_candidate_key")),
                    _cell(len(previous)),
                    _cell(len(rm_lines)),
                    _cell(", ".join(
                        str((line or {}).get("source_segment_key") or "")
                        for line in previous[:4]
                        if (line or {}).get("source_segment_key")
                    )),
                )
            )
        if not bom_rows:
            bom_rows.append(
                "<tr><td colspan='7' class='text-muted'>%s</td></tr>"
                % html_escape(_("No materialized BoM diagnostics found in the payload."))
            )

        stage_rows = []
        for ctx in stage_contexts[:30]:
            stage_rows.append(
                "<tr>%s%s%s%s%s%s%s</tr>"
                % (
                    _cell((ctx or {}).get("index")),
                    _cell((ctx or {}).get("sequence")),
                    _cell((ctx or {}).get("stage_code")),
                    _cell((ctx or {}).get("stage_name")),
                    _cell((ctx or {}).get("stage_template_id")),
                    _cell((ctx or {}).get("stage_contract_code")),
                    _cell((ctx or {}).get("execution_type")),
                )
            )
        if not stage_rows:
            stage_rows.append(
                "<tr><td colspan='7' class='text-muted'>%s</td></tr>"
                % html_escape(_("No StageContext rows found in the payload."))
            )

        material_rows = []
        for row in preview_materials[:40]:
            metadata = dict((row or {}).get("metadata") or {})
            material_rows.append(
                "<tr>%s%s%s%s%s%s%s%s%s%s%s%s</tr>"
                % (
                    _cell((row or {}).get("role") or (row or {}).get("role_code")),
                    _cell(metadata.get("kind")),
                    _cell((row or {}).get("layer_no") or metadata.get("layer_no")),
                    _cell((row or {}).get("stage")),
                    _cell((row or {}).get("stage_code")),
                    _cell((row or {}).get("stage_template_id")),
                    _cell((row or {}).get("stage_contract_code")),
                    _cell(metadata.get("stage_template_id")),
                    _cell(metadata.get("stage_contract_code")),
                    _cell(metadata.get("assigned_route_stage")),
                    _cell(metadata.get("component_stage")),
                    _cell(metadata.get("writer_stage")),
                )
            )
        if not material_rows:
            material_rows.append(
                "<tr><td colspan='12' class='text-muted'>%s</td></tr>"
                % html_escape(_("No preview material rows found in the payload."))
            )

        blockers = list(resolver.get("blockers") or [])
        blocker_rows = []
        for blocker in blockers[:10]:
            blocker_rows.append(
                "<tr>%s%s%s</tr>"
                % (
                    _cell((blocker or {}).get("code")),
                    _cell((blocker or {}).get("segment_key")),
                    _cell((blocker or {}).get("message")),
                )
            )
        if not blocker_rows:
            blocker_rows.append(
                "<tr><td colspan='3' class='text-muted'>%s</td></tr>"
                % html_escape(_("No materialization blockers reported."))
            )

        raw_len = len(raw_json or "")
        html = [
            "<div class='o_corr_raw_json_summary'>",
            "<h4>%s</h4>" % html_escape(_("Preview / BoM Backend")),
            "<table class='table table-sm table-bordered'><tbody>",
            "<tr><th>%s</th>%s</tr>" % (html_escape(_("Preview Backend Selected")), _cell(payload.get("preview_backend_selected"))),
            "<tr><th>%s</th>%s</tr>" % (html_escape(_("Preview Backend Applied")), _cell(payload.get("preview_backend_applied"))),
            "<tr><th>%s</th>%s</tr>" % (html_escape(_("BoM Backend Selected")), _cell(payload.get("bom_backend_selected"))),
            "<tr><th>%s</th>%s</tr>" % (html_escape(_("Raw JSON Size")), _cell("%s chars" % raw_len)),
            "</tbody></table>",
            "<h4>%s</h4>" % html_escape(_("Materialized BoMs")),
            "<table class='table table-sm table-bordered'><thead><tr>",
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            % (
                html_escape(_("Segment")),
                html_escape(_("Stage Code")),
                html_escape(_("Stage Name")),
                html_escape(_("Product Candidate")),
                html_escape(_("Previous Lines")),
                html_escape(_("RM/Consumable Lines")),
                html_escape(_("Previous Sources")),
            ),
            "</tr></thead><tbody>%s</tbody></table>" % "".join(bom_rows),
            "<h4>%s</h4>" % html_escape(_("StageContext Identity Rows")),
            "<table class='table table-sm table-bordered'><thead><tr>",
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            % (
                html_escape(_("Index")),
                html_escape(_("Sequence")),
                html_escape(_("Stage Code")),
                html_escape(_("Stage Name")),
                html_escape(_("Template ID")),
                html_escape(_("Contract Code")),
                html_escape(_("Execution")),
            ),
            "</tr></thead><tbody>%s</tbody></table>" % "".join(stage_rows),
            "<h4>%s</h4>" % html_escape(_("Material Row Identity Diagnostics")),
            "<table class='table table-sm table-bordered'><thead><tr>",
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            % (
                html_escape(_("Role")),
                html_escape(_("Kind")),
                html_escape(_("Layer")),
                html_escape(_("Row Stage")),
                html_escape(_("Row Stage Code")),
                html_escape(_("Row Template ID")),
                html_escape(_("Row Contract")),
                html_escape(_("Meta Template ID")),
                html_escape(_("Meta Contract")),
                html_escape(_("Meta Assigned")),
                html_escape(_("Meta Component")),
                html_escape(_("Meta Writer")),
            ),
            "</tr></thead><tbody>%s</tbody></table>" % "".join(material_rows),
            "<h4>%s</h4>" % html_escape(_("Materialization Blockers")),
            "<table class='table table-sm table-bordered'><thead><tr>",
            "<th>%s</th><th>%s</th><th>%s</th>"
            % (html_escape(_("Code")), html_escape(_("Segment")), html_escape(_("Message"))),
            "</tr></thead><tbody>%s</tbody></table>" % "".join(blocker_rows),
            "</div>",
        ]
        return "".join(html)

    # -------------------------------------------------------------------------
    # Phase 8 parity validation (shadow-only)
    # -------------------------------------------------------------------------

    def _compute_phase8_parity(self, result, qgraph, shadow_runtime):
        """Compare Phase 8 shadow runtime with current Engine V2 outputs.

        Parity is reported at a coarse-grained level and is intended only for
        diagnostics. This helper must never raise; callers wrap it in
        best-effort try/except blocks.
        """

        report = {
            "materials": {
                "matched": [],
                "missing_in_shadow": [],
                "extra_in_shadow": [],
                "synthetic_missing_in_shadow": [],
                "synthetic_extra_in_shadow": [],
            },
            "stages": {
                "engine_bucket_order": [],
                "shadow_bucket_order": [],
                "missing_buckets_in_shadow": [],
                "extra_buckets_in_shadow": [],
                "bucket_order_differences": [],
                "engine_process_stage_order": [],
                "shadow_process_stage_order": [],
            },
            "buckets": {
                "matches": [],
                "mismatches": [],
            },
            "summary": {},
            "warnings": [],
        }

        try:
            # ----------------------
            # Materials parity
            # ----------------------
            engine_rm = list(getattr(result, "rm_requirements", []) or [])
            engine_material_keys = set()
            engine_material_info = {}

            def _engine_bucket_for_stage(stage_key):
                if stage_key and _engine_v2_resolve_rm_node is not None:
                    try:
                        return _engine_v2_resolve_rm_node(qgraph, stage_key)
                    except Exception:
                        return None
                return None

            for rm in engine_rm:
                stage_key = getattr(rm, "stage_key", None) or ""
                role = getattr(rm, "role", "") or ""
                bucket = _engine_bucket_for_stage(stage_key)
                ctype = (getattr(rm, "consumption_type", "area") or "area").lower()
                meta = getattr(rm, "metadata", {}) or {}
                kind = (meta.get("kind") or "") or ""
                key = (stage_key, role, bucket)
                engine_material_keys.add(key)
                engine_material_info[key] = {
                    "stage": stage_key,
                    "role": role,
                    "bucket": bucket,
                    "consumption_type": ctype,
                    "kind": kind,
                }

            shadow_materials = list(shadow_runtime.get("material_requirements") or [])
            shadow_material_keys = set()
            shadow_material_info = {}

            def _shadow_bucket_for_stage(stage_code, default_bucket=None):
                bucket = _engine_bucket_for_stage(stage_code)
                if bucket is None:
                    bucket = default_bucket
                return bucket

            for m in shadow_materials:
                stage_code = (m.get("stage_code") or "") or ""
                role = (m.get("role") or "") or ""
                default_bucket = m.get("default_bucket") or None
                bucket = _shadow_bucket_for_stage(stage_code, default_bucket=default_bucket)
                material_type = (m.get("material_type") or "") or ""
                attach_basis = (m.get("attach_quantity_basis") or "") or ""
                # Map attach basis to a coarse consumption type for diagnostics.
                if attach_basis == "per_m2":
                    ctype = "area"
                elif attach_basis == "per_box":
                    ctype = "unit"
                elif attach_basis == "per_length":
                    ctype = "length"
                elif attach_basis == "per_time":
                    ctype = "time"
                else:
                    ctype = "custom"
                key = (stage_code, role, bucket)
                shadow_material_keys.add(key)
                shadow_material_info[key] = {
                    "stage": stage_code,
                    "role": role,
                    "bucket": bucket,
                    "material_type": material_type,
                    "attach_quantity_basis": attach_basis,
                    "consumption_type": ctype,
                }

            def _mk_engine_entry(key):
                return dict(engine_material_info.get(key, {}))

            def _mk_shadow_entry(key):
                return dict(shadow_material_info.get(key, {}))

            matched_keys = engine_material_keys & shadow_material_keys
            missing_keys = engine_material_keys - shadow_material_keys
            extra_keys = shadow_material_keys - engine_material_keys

            def _is_synthetic_role(role: str) -> bool:
                r = (role or "").lower()
                return r.startswith("adhesive_") or r.startswith("wire_") or r.startswith("tape_")

            normal_missing = set()
            synthetic_missing = set()
            for stage, role, bucket in missing_keys:
                if _is_synthetic_role(role):
                    synthetic_missing.add((stage, role, bucket))
                else:
                    normal_missing.add((stage, role, bucket))

            normal_extra = set()
            synthetic_extra = set()
            for stage, role, bucket in extra_keys:
                if _is_synthetic_role(role):
                    synthetic_extra.add((stage, role, bucket))
                else:
                    normal_extra.add((stage, role, bucket))

            report["materials"]["matched"] = [
                _mk_engine_entry(k) | _mk_shadow_entry(k) for k in sorted(matched_keys)
            ]
            report["materials"]["missing_in_shadow"] = [
                _mk_engine_entry(k) for k in sorted(normal_missing)
            ]
            report["materials"]["extra_in_shadow"] = [
                _mk_shadow_entry(k) for k in sorted(normal_extra)
            ]
            report["materials"]["synthetic_missing_in_shadow"] = [
                _mk_engine_entry(k) for k in sorted(synthetic_missing)
            ]
            report["materials"]["synthetic_extra_in_shadow"] = [
                _mk_shadow_entry(k) for k in sorted(synthetic_extra)
            ]

            if normal_missing:
                report["warnings"].append(
                    "%s material keys present in engine rm_requirements but missing in "
                    "Phase 8 shadow material_requirements." % len(normal_missing)
                )
            if normal_extra:
                report["warnings"].append(
                    "%s material keys present only in Phase 8 shadow "
                    "material_requirements." % len(normal_extra)
                )
            if synthetic_missing:
                report["warnings"].append(
                    "%s synthetic consumable material keys (e.g. adhesive_*, wire_*, tape_*) "
                    "are not yet mapped in Phase 8 shadow material_requirements."
                    % len(synthetic_missing)
                )

            # ----------------------
            # Stages parity: quantity buckets vs routing buckets
            # ----------------------
            # Engine bucket order from quantity StageGraph nodes.
            try:
                engine_nodes = getattr(qgraph, "nodes", {}) or {}
            except Exception:
                engine_nodes = {}
            required_bucket_order = [
                name
                for name in ["raw_material", "board", "sheet", "blanks", "finished_goods"]
                if name in engine_nodes
            ]

            routing = shadow_runtime.get("routing") or {}
            shadow_process_stage_order = [
                (s.get("stage_code") or "") or "" for s in routing.get("stages") or []
            ]

            # Map process stages to buckets via engine's stage->bucket logic.
            shadow_bucket_order = []
            seen_buckets = set()
            for stage_code in shadow_process_stage_order:
                if not stage_code:
                    continue
                bucket = _engine_bucket_for_stage(stage_code)
                if not bucket:
                    continue
                if bucket not in seen_buckets:
                    seen_buckets.add(bucket)
                    shadow_bucket_order.append(bucket)

            report["stages"]["engine_bucket_order"] = required_bucket_order
            report["stages"]["shadow_bucket_order"] = shadow_bucket_order
            report["stages"]["engine_process_stage_order"] = required_bucket_order
            report["stages"]["shadow_process_stage_order"] = shadow_process_stage_order

            engine_bucket_set = set(required_bucket_order)
            shadow_bucket_set = set(shadow_bucket_order)

            missing_buckets = engine_bucket_set - shadow_bucket_set
            extra_buckets = shadow_bucket_set - engine_bucket_set

            report["stages"]["missing_buckets_in_shadow"] = sorted(missing_buckets)
            report["stages"]["extra_buckets_in_shadow"] = sorted(extra_buckets)

            if missing_buckets:
                report["warnings"].append(
                    "Quantity buckets %s appear in Engine V2 StageGraph but are not "
                    "covered by any Phase 8 shadow routing stage."
                    % ", ".join(sorted(missing_buckets))
                )
            if extra_buckets:
                report["warnings"].append(
                    "Phase 8 shadow routing covers additional buckets %s not present "
                    "in Engine V2 StageGraph."
                    % ", ".join(sorted(extra_buckets))
                )

            # Order differences for common buckets.
            engine_idx = {b: i for i, b in enumerate(required_bucket_order)}
            shadow_idx = {b: i for i, b in enumerate(shadow_bucket_order)}
            bucket_order_diffs = []
            for b in sorted(engine_bucket_set & shadow_bucket_set):
                ei = engine_idx.get(b)
                si = shadow_idx.get(b)
                if ei is not None and si is not None and ei != si:
                    bucket_order_diffs.append(
                        {"bucket": b, "engine_index": ei, "shadow_index": si}
                    )
            report["stages"]["bucket_order_differences"] = bucket_order_diffs
            if bucket_order_diffs:
                report["warnings"].append(
                    "%s quantity buckets have different order between Engine V2 StageGraph "
                    "and Phase 8 shadow routing." % len(bucket_order_diffs)
                )

            # ----------------------
            # Bucket parity
            # ----------------------
            # ----------------------
            # Bucket parity: compare engine stage->bucket mapping vs
            # shadow-declared bucket on stage_cost_lines for the same
            # process stages. This validates that Phase 8 bucket config
            # is aligned with live engine logic.
            # ----------------------
            engine_bucket_map = {}
            routing_stages = routing.get("stages") or []
            for s in routing_stages:
                stage_code = (s.get("stage_code") or "") or ""
                if not stage_code:
                    continue
                engine_bucket_map[stage_code] = _engine_bucket_for_stage(stage_code)

            shadow_bucket_map = {}
            for line in shadow_runtime.get("stage_cost_lines") or []:
                code = (line.get("stage_code") or "") or ""
                bucket = line.get("bucket") or None
                if code and code not in shadow_bucket_map:
                    shadow_bucket_map[code] = bucket

            bucket_matches = []
            bucket_mismatches = []
            for stage_code, eng_bucket in engine_bucket_map.items():
                if stage_code in shadow_bucket_map:
                    shadow_bucket = shadow_bucket_map.get(stage_code)
                    if (eng_bucket or None) == (shadow_bucket or None):
                        bucket_matches.append(
                            {"stage": stage_code, "bucket": eng_bucket}
                        )
                    else:
                        bucket_mismatches.append(
                            {
                                "stage": stage_code,
                                "engine_bucket": eng_bucket,
                                "shadow_bucket": shadow_bucket,
                            }
                        )

            report["buckets"]["matches"] = bucket_matches
            report["buckets"]["mismatches"] = bucket_mismatches

            if bucket_mismatches:
                report["warnings"].append(
                    "%s stages have different bucket mappings between Engine V2 and "
                    "Phase 8 shadow runtime." % len(bucket_mismatches)
                )

            # ----------------------
            # Summary counters
            # ----------------------
            report["summary"] = {
                "materials": {
                    "engine_keys": len(engine_material_keys),
                    "shadow_keys": len(shadow_material_keys),
                    "matched": len(matched_keys),
                    "missing_in_shadow": len(normal_missing),
                    "extra_in_shadow": len(normal_extra),
                    "synthetic_missing_in_shadow": len(synthetic_missing),
                    "synthetic_extra_in_shadow": len(synthetic_extra),
                },
                "stages": {
                    "engine_bucket_count": len(required_bucket_order),
                    "shadow_bucket_count": len(shadow_bucket_order),
                    "missing_buckets_in_shadow": len(missing_buckets),
                    "extra_buckets_in_shadow": len(extra_buckets),
                    "bucket_order_differences": len(bucket_order_diffs),
                },
                "buckets": {
                    "matches": len(bucket_matches),
                    "mismatches": len(bucket_mismatches),
                },
            }

        except Exception as exc:  # pragma: no cover - defensive
            report["warnings"].append(
                "Phase 8 parity computation failed: %s" % (exc,)
            )

        return report

    def _render_stage_costing_html(self, costing):
        """Render a business-facing costing summary.

        Stage-wise material routing remains in raw_json/stage_costs for
        diagnostics, but the UI presents three business sections:
        raw-material cost, operation cost, and final manufacturing cost.
        """

        if not costing:
            return "<p>%s</p>" % _("No costing data available for this preview.")
        fmt = self._preview_formatter()
        currency = costing.get("currency") or {}
        symbol = currency.get("symbol") or ""

        try:
            stage_costs = dict(costing.get("stage_costs") or {})
        except Exception:
            stage_costs = {}

        def _fmt_amount(vals, key):
            try:
                return float(vals.get(key, 0.0) or 0.0)
            except Exception:
                return 0.0

        total_rm = _fmt_amount(costing, "total_cost_for_order")
        total_operation = _fmt_amount(costing, "total_operation_cost")
        total_manufacturing = _fmt_amount(costing, "total_production_cost")
        if total_manufacturing <= 0.0:
            total_manufacturing = total_rm + total_operation
        order_qty = _fmt_amount(costing, "order_qty")
        cost_per_unit = total_manufacturing / order_qty if order_qty > 0.0 else 0.0

        final_rows = [
            "<tr><th>%s</th><td>%s</td></tr>" % (_("Raw material total"), fmt.format_currency(total_rm, symbol)),
            "<tr><th>%s</th><td>%s</td></tr>" % (_("Operation total"), fmt.format_currency(total_operation, symbol)),
            "<tr><th>%s</th><td>%s</td></tr>" % (_("Total manufacturing cost"), fmt.format_currency(total_manufacturing, symbol)),
        ]
        if order_qty > 0.0:
            final_rows.append(
                "<tr><th>%s</th><td>%s</td></tr>"
                % (_("Cost per unit"), fmt.format_currency(cost_per_unit, symbol))
            )

        modal_uid = str(self.id or "new")
        rm_modal_id = "engine_v2_rm_cost_details_%s" % modal_uid
        op_modal_id = "engine_v2_operation_cost_details_%s" % modal_uid
        rm_detail_html = self._render_rm_cost_detail_html(costing)
        op_detail_html = self._render_operation_cost_detail_html(costing)

        html = ["<h3>%s</h3>" % _("Costing Summary")]
        piece_specific = dict((costing or {}).get("piece_specific_costing") or {})
        if piece_specific.get("enabled"):
            html.extend(
                [
                    "<h4>%s</h4>" % _("Piece-level Material Cost"),
                    "<table class='table table-sm table-striped'>",
                    "<thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr></thead>"
                    % (_("Piece"), _("Stack Source"), _("Material Cost / FG"), _("Material Cost / Order")),
                    "<tbody>",
                ]
            )
            for piece in list(piece_specific.get("pieces") or []):
                html.append(
                    "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (
                        html_escape(str(piece.get("piece_name") or piece.get("piece_key") or "")),
                        html_escape(str(piece.get("stack_source") or "")),
                        fmt.format_currency(piece.get("material_cost_per_fg", 0.0), symbol),
                        fmt.format_currency(piece.get("material_cost_for_order", 0.0), symbol),
                    )
                )
            fg_rollup = dict(piece_specific.get("fg_rollup") or {})
            html.extend(
                [
                    "</tbody>",
                    "</table>",
                    "<h4>%s</h4>" % _("FG Material Rollup"),
                    "<table class='table table-sm table-striped'>",
                    "<tbody>",
                    "<tr><th>%s</th><td>%s</td></tr>"
                    % (_("Material cost per FG"), fmt.format_currency(fg_rollup.get("material_cost_per_fg", 0.0), symbol)),
                    "<tr><th>%s</th><td>%s</td></tr>"
                    % (_("Material cost for order"), fmt.format_currency(fg_rollup.get("material_cost_for_order", 0.0), symbol)),
                    "</tbody>",
                    "</table>",
                ]
            )

        html.extend([
            "<h4>%s</h4>" % _("Raw Material Cost Summary"),
            "<table class='table table-sm table-striped'>",
            "<tbody>",
            "<tr><th>%s</th><td>%s</td><td class='text-end'>"
            "<button type='button' class='btn btn-secondary btn-sm' data-bs-toggle='modal' data-bs-target='#%s'>%s</button>"
            "</td></tr>"
            % (
                _("Raw material total"),
                fmt.format_currency(total_rm, symbol),
                rm_modal_id,
                _("View Details"),
            ),
            "</tbody>",
            "</table>",
            "<h4>%s</h4>" % _("Operation Cost Summary"),
            "<table class='table table-sm table-striped'>",
            "<tbody>",
            "<tr><th>%s</th><td>%s</td><td class='text-end'>"
            "<button type='button' class='btn btn-secondary btn-sm' data-bs-toggle='modal' data-bs-target='#%s'>%s</button>"
            "</td></tr>"
            % (
                _("Operation total"),
                fmt.format_currency(total_operation, symbol),
                op_modal_id,
                _("View Details"),
            ),
            "</tbody>",
            "</table>",
            "<h4>%s</h4>" % _("Final Manufacturing Cost"),
            "<table class='table table-sm table-striped'>",
            "<tbody>",
            *final_rows,
            "</tbody>",
            "</table>",
            self._render_inline_costing_modal(rm_modal_id, _("Raw Material Cost Details"), rm_detail_html),
            self._render_inline_costing_modal(op_modal_id, _("Operation Cost Details"), op_detail_html),
        ])
        warnings = []
        try:
            missing_cost = list(costing.get("missing_cost") or [])
        except Exception:
            missing_cost = []
        for item in missing_cost:
            if not isinstance(item, dict):
                continue
            message = item.get("warning") or item.get("cost_warning_message") or ""
            if not message and item.get("reason") == "standard_price_missing_or_zero":
                product_name = item.get("product_name") or item.get("product_id") or _("Unknown product")
                message = "Standard price missing/zero for product %s" % (product_name,)
            if message and message not in warnings:
                warnings.append(message)
        if warnings:
            html.extend(
                [
                    "<div class='alert alert-warning mt-2'>",
                    "<strong>%s</strong>" % _("Costing warnings"),
                    "<ul class='mb-0'>",
                    *["<li>%s</li>" % html_escape(str(message)) for message in warnings],
                    "</ul>",
                    "</div>",
                ]
            )
        return "\n".join(html)

    def _render_inline_costing_modal(self, modal_id, title, body_html):
        return "\n".join(
            [
                "<div class='modal fade' id='%s' tabindex='-1' aria-hidden='true'>" % html_escape(str(modal_id)),
                "<div class='modal-dialog modal-xl modal-dialog-scrollable'>",
                "<div class='modal-content'>",
                "<div class='modal-header'>",
                "<h5 class='modal-title'>%s</h5>" % html_escape(str(title or "")),
                "<button type='button' class='btn-close' data-bs-dismiss='modal' aria-label='%s'></button>"
                % html_escape(str(_("Close"))),
                "</div>",
                "<div class='modal-body'>",
                body_html or "",
                "</div>",
                "<div class='modal-footer'>",
                "<button type='button' class='btn btn-primary' data-bs-dismiss='modal'>%s</button>"
                % html_escape(str(_("Close"))),
                "</div>",
                "</div>",
                "</div>",
                "</div>",
            ]
        )

    def _set_costing_summary_totals(self, costing):
        self.ensure_one()
        if not costing:
            self.costing_rm_total_display = False
            self.costing_operation_total_display = False
            return
        fmt = self._preview_formatter()
        currency = costing.get("currency") or {}
        symbol = currency.get("symbol") or ""

        def _amount(key):
            try:
                return float(costing.get(key, 0.0) or 0.0)
            except Exception:
                return 0.0

        self.costing_rm_total_display = fmt.format_currency(_amount("total_cost_for_order"), symbol)
        self.costing_operation_total_display = fmt.format_currency(_amount("total_operation_cost"), symbol)

    def _render_rm_cost_detail_html(self, costing):
        fmt = self._preview_formatter()
        currency = costing.get("currency") or {}
        symbol = currency.get("symbol") or ""

        def _safe_text(value):
            return html_escape(str(value or ""))

        def _as_float(value):
            try:
                return float(value or 0.0)
            except Exception:
                return 0.0

        def _qty(value, uom):
            return fmt.format_quantity(_as_float(value), uom or "")

        try:
            trace_lines = list(costing.get("costing_trace") or costing.get("components") or [])
        except Exception:
            trace_lines = []
        try:
            missing_cost = list(costing.get("missing_cost") or [])
        except Exception:
            missing_cost = []

        header = (
            "<tr>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "</tr>"
            % (
                _("Layer No"),
                _("Role"),
                _("Product"),
                _("Qty"),
                _("UoM"),
                _("Cost Source"),
                _("Unit Cost"),
                _("Total Cost"),
                _("Notes / Warnings"),
            )
        )
        rows = []
        seen_missing_keys = set()
        for line in trace_lines:
            if not isinstance(line, dict):
                continue
            warning = line.get("cost_warning_message") or line.get("cost_warning") or ""
            warning_class = " class='table-warning'" if warning else ""
            key = (
                line.get("product_key") or "",
                line.get("engine_role") or line.get("role") or "",
                line.get("layer_no") or "",
                line.get("product_id") or "",
                line.get("cost_warning") or "",
            )
            seen_missing_keys.add(key)
            uom = line.get("uom") or line.get("uom_name") or ""
            rows.append(
                "<tr%s>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "</tr>"
                % (
                    warning_class,
                    _safe_text(line.get("layer_no") or ""),
                    _safe_text(line.get("engine_role") or line.get("role") or ""),
                    _safe_text(line.get("product_name") or line.get("product_id") or ""),
                    _qty(line.get("qty_for_order"), uom),
                    _safe_text(uom),
                    _safe_text(line.get("cost_source") or line.get("requested_cost_source") or ""),
                    fmt.format_currency(_as_float(line.get("selected_unit_cost")), symbol),
                    fmt.format_currency(_as_float(line.get("cost_for_order")), symbol),
                    _safe_text(warning),
                )
            )

        for item in missing_cost:
            if not isinstance(item, dict):
                continue
            warning = item.get("warning") or item.get("reason") or ""
            key = (
                item.get("product_key") or "",
                item.get("role") or item.get("base_role") or "",
                item.get("layer_no") or "",
                item.get("product_id") or "",
                item.get("reason") or "",
            )
            if key in seen_missing_keys and item.get("product_id"):
                continue
            rows.append(
                "<tr class='table-warning'>"
                "<td>%s</td><td>%s</td><td>%s</td><td></td><td></td>"
                "<td>%s</td><td></td><td></td><td>%s</td>"
                "</tr>"
                % (
                    _safe_text(item.get("layer_no") or ""),
                    _safe_text(item.get("role") or item.get("base_role") or ""),
                    _safe_text(item.get("product_name") or item.get("product_id") or _("Unresolved")),
                    _safe_text(item.get("cost_source") or costing.get("cost_source") or ""),
                    _safe_text(warning),
                )
            )

        return "\n".join(
            [
                "<div style='max-height: 70vh; overflow: auto;'>",
                "<table class='table table-sm table-striped'>",
                "<thead>%s</thead>" % header,
                "<tbody>",
                *(rows or ["<tr><td colspan='9'>%s</td></tr>" % _("No raw-material cost lines were computed.")]),
                "</tbody>",
                "</table>",
                "</div>",
            ]
        )

    def _render_operation_cost_detail_html(self, costing):
        fmt = self._preview_formatter()
        currency = costing.get("currency") or {}
        symbol = currency.get("symbol") or ""
        try:
            stage_costs = dict(costing.get("stage_costs") or {})
        except Exception:
            stage_costs = {}

        def _safe_text(value):
            return html_escape(str(value or ""))

        def _as_float(value):
            try:
                return float(value or 0.0)
            except Exception:
                return 0.0

        header = (
            "<tr>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "</tr>"
            % (
                _("Operation / Stage"),
                _("Quantity Basis"),
                _("Machine Cost Method"),
                _("Machine Rate"),
                _("Machine Cost"),
                _("Labor Cost Method"),
                _("Labor Rate"),
                _("Labor Cost"),
                _("Total Operation Cost"),
            )
        )
        preferred_order = ["raw_material", "board", "sheet", "blanks", "finished_goods"]
        rows = []
        seen = set()
        for stage_key in preferred_order + sorted(k for k in stage_costs.keys() if k not in preferred_order):
            if stage_key in seen:
                continue
            info = stage_costs.get(stage_key)
            if not info:
                continue
            seen.add(stage_key)
            machine = _as_float(info.get("machine_cost"))
            labor = _as_float(info.get("labor_cost"))
            total = _as_float(info.get("total_operation_cost")) or machine + labor
            if not (machine or labor or total):
                continue
            qty_basis = "%s %s" % (
                fmt.format_quantity(_as_float(info.get("qty")), ""),
                info.get("stage_qty_basis_label") or info.get("stage_qty_basis") or "",
            )
            rows.append(
                "<tr>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "</tr>"
                % (
                    _safe_text(stage_key),
                    _safe_text(qty_basis.strip()),
                    _safe_text(info.get("machine_cost_method") or ""),
                    fmt.format_currency(_as_float(info.get("machine_rate")), symbol),
                    fmt.format_currency(machine, symbol),
                    _safe_text(info.get("labor_cost_method") or ""),
                    fmt.format_currency(_as_float(info.get("labor_rate")), symbol),
                    fmt.format_currency(labor, symbol),
                    fmt.format_currency(total, symbol),
                )
            )

        return "\n".join(
            [
                "<div style='max-height: 70vh; overflow: auto;'>",
                "<table class='table table-sm table-striped'>",
                "<thead>%s</thead>" % header,
                "<tbody>",
                *(rows or ["<tr><td colspan='9'>%s</td></tr>" % _("No configured operation costs were computed.")]),
                "</tbody>",
                "</table>",
                "</div>",
            ]
        )

    def _compute_required_deckle_cm(self, result, layout_option=None):
        """Compute target deckle (cm) for paper rolls.

        Route-aware order:
        - when a layout/slitting option is selected, use its final sheet width
          as the authoritative deckle input;
        - otherwise preserve the legacy behaviour for backward compatibility.
        """
        self.ensure_one()

        selected_width_mm = 0.0
        try:
            selected_width_mm = float((layout_option or {}).get("final_sheet_width_mm", 0.0) or 0.0)
        except Exception:
            selected_width_mm = 0.0
        if selected_width_mm > 0.0:
            return selected_width_mm / 10.0

        # 1) Base deckle in cm from metrics.extra["geometry"]["deckle_mm"] when present.
        base_cm = None
        try:
            metrics = getattr(result, "metrics", None)
            extra = getattr(metrics, "extra", {}) or {}
            geometry = extra.get("geometry") or {}
            deckle_mm = float(geometry.get("deckle_mm") or 0.0)
            if deckle_mm > 0:
                base_cm = deckle_mm / 10.0
        except Exception:
            base_cm = None

        # Fallback: derive from spec blank_width_mm or width_mm.
        spec = self._active_spec_record()
        if (base_cm is None or base_cm <= 0.0) and spec:
            try:
                bw = float(getattr(spec, "manual_sheet_width_mm", 0.0) or 0.0)
            except Exception:
                bw = 0.0
            if bw > 0.0:
                base_cm = bw / 10.0
        if (base_cm is None or base_cm <= 0.0) and spec:
            try:
                bw = float(getattr(spec, "blank_width_mm", 0.0) or 0.0)
            except Exception:
                bw = 0.0
            if bw <= 0.0:
                try:
                    bw = float(getattr(spec, "width_mm", 0.0) or 0.0)
                except Exception:
                    bw = 0.0
            if bw > 0.0:
                base_cm = bw / 10.0

        if not base_cm or base_cm <= 0.0 or not spec:
            return None

        # 2) Scale by UPS hints from spec.extra_params_json.
        try:
            params_raw = getattr(spec, "extra_params_json", {}) or {}
        except Exception:
            params_raw = {}
        if isinstance(params_raw, str):
            try:
                params = json.loads((params_raw or "{}").strip() or "{}")
            except Exception:
                params = {}
        elif isinstance(params_raw, dict):
            params = dict(params_raw)
        else:
            params = {}

        style = (getattr(spec, "style", "") or "").strip()

        def _int_from_params(keys, default=1):
            val = default
            for k in keys:
                if k in params:
                    try:
                        raw = params.get(k)
                        val = int(float(raw or default))
                        break
                    except Exception:
                        val = default
            if val <= 0:
                val = default
            return val

        if style == "BMC-Custom":
            pu = _int_from_params(["printing_ups", "printing_ups_ui"], default=1)
            return base_cm * pu

        su = _int_from_params(["liner_slitting_ups", "liner_slitting_ups_ui"], default=1)
        if su <= 1:
            try:
                forced_ups = int(float(getattr(spec, "forced_ups", 0) or 0))
            except Exception:
                forced_ups = 0
            if forced_ups > 1:
                su = forced_ups
        return base_cm * su

    def _build_paper_resolution(self, result, bom_struct, layout_selection=None):
        """Resolve paper layers (liner/medium) to product variants for preview.

        Returns a JSON-serialisable list of resolution entries and mutates
        rm_requirements and bom_struct in-place to attach resolution metadata.
        """
        self.ensure_one()

        Map = self.env["corr.material.product.map"].sudo()
        Product = self.env["product.product"].sudo()
        Uom = self.env["uom.uom"].sudo()

        try:
            rm_reqs = list(getattr(result, "rm_requirements", []) or [])
        except Exception:
            rm_reqs = []
        if not rm_reqs:
            return []

        selected_layout_option = dict((layout_selection or {}).get("option") or {})
        selected_layout_stage = dict((layout_selection or {}).get("stage_entry") or {})
        deckle_cm = self._compute_required_deckle_cm(result, layout_option=selected_layout_option)

        # Map layer_no -> stack line info (GSM, BF, Color) from the spec.
        spec = self._active_spec_record()
        stack_info_by_idx = {}
        if spec:
            try:
                lines = spec.stack_line_ids.sorted(lambda l: (l.sequence, l.id))
            except Exception:
                lines = spec.stack_line_ids
            idx = 1
            for line in lines:
                raw_role = (line.role or "").strip().lower()
                normalized_role = _normalize_stack_paper_role(raw_role)
                flute = getattr(line, "flute_type_id", None) or getattr(line, "flute_id", None)
                flute_code = (getattr(flute, "code", "") or getattr(flute, "name", "") or "") if flute else ""
                stack_info_by_idx[idx] = {
                    "role": normalized_role,
                    "raw_role": raw_role,
                    "gsm": float(line.gsm or 0.0),
                    "bf": float(line.bf or 0.0) if line.bf is not None else None,
                    "color": line.color or "",
                    "material_product_id": getattr(getattr(line, "material_product_id", None), "id", False) or False,
                    "material_product_name": getattr(getattr(line, "material_product_id", None), "display_name", "") or "",
                    "material_product_uom_id": getattr(getattr(getattr(line, "material_product_id", None), "uom_id", None), "id", False) or False,
                    "material_product_uom_name": getattr(getattr(getattr(line, "material_product_id", None), "uom_id", None), "display_name", "") or "",
                    "material_template_id": getattr(getattr(line, "material_template_id", None), "id", False) or False,
                    "material_template_name": getattr(getattr(line, "material_template_id", None), "display_name", "") or "",
                    "flute_code": flute_code,
                    "flute": flute_code,
                    "flute_tuf": float(getattr(flute, "tuf", 0.0) or 0.0) if flute else 0.0,
                }
                idx += 1

        paper_resolution = []

        def _resolution_key(role, layer_no):
            return f"{role or ''}:{int(layer_no or 0)}"

        resolution_by_key = {}
        existing_physical_stack_keys = set()

        for rm in rm_reqs:
            role_raw = (getattr(rm, "role", "") or "").strip().lower()
            if role_raw not in ("liner", "medium", "top_roll", "carton_board"):
                continue
            # Treat top_roll as a liner for paper variant resolution so
            # that promoted top liners (used as rm_top_roll) reuse the
            # same mapping logic and metadata as regular liner layers.
            if role_raw == "medium":
                role_base = "medium"
            elif role_raw == "carton_board":
                role_base = "carton_board"
            else:
                role_base = "liner"
            layer_no = getattr(rm, "layer_no", None)
            try:
                layer_idx = int(layer_no or 0)
            except Exception:
                layer_idx = 0

            meta = getattr(rm, "metadata", {}) or {}
            source_layer_no = meta.get("source_layer_no")
            try:
                source_layer_idx = int(source_layer_no or 0)
            except Exception:
                source_layer_idx = 0
            gsm_meta = meta.get("gsm")
            try:
                gsm_val = float(gsm_meta or 0.0)
            except Exception:
                gsm_val = 0.0

            # Unit-sheet top layers prepend an external sheet, so RM layer_no
            # is an effective layer number. Source stack BF/color/flute must
            # be read from source_layer_no when the RM rule provides it.
            stack_lookup_idx = source_layer_idx or layer_idx
            if stack_lookup_idx and role_base in ("liner", "medium"):
                existing_physical_stack_keys.add((role_base, stack_lookup_idx))
            stack_info = stack_info_by_idx.get(stack_lookup_idx) or {}
            if gsm_val <= 0.0:
                try:
                    gsm_val = float(stack_info.get("gsm") or 0.0)
                except Exception:
                    gsm_val = 0.0

            bf_val = meta.get("bf")
            if bf_val in (None, ""):
                bf_val = stack_info.get("bf")
            color_val = meta.get("color")
            if color_val in (None, ""):
                color_val = stack_info.get("color") or None
            material_template_id = meta.get("material_template_id") or stack_info.get("material_template_id") or None
            material_template_name = meta.get("material_template_name") or stack_info.get("material_template_name") or ""
            material_product_id = meta.get("product_id") or meta.get("material_product_id") or stack_info.get("material_product_id") or None
            material_product_name = meta.get("product_name") or meta.get("material_product_name") or stack_info.get("material_product_name") or ""
            material_product_uom_id = meta.get("uom_id") or meta.get("material_product_uom_id") or stack_info.get("material_product_uom_id") or None
            material_product_uom_name = meta.get("uom_name") or meta.get("material_product_uom_name") or stack_info.get("material_product_uom_name") or ""
            flute_val = meta.get("flute_code") or meta.get("flute")
            if flute_val in (None, ""):
                flute_val = stack_info.get("flute_code") or None

            explicit_product = Product.browse()
            if material_product_id:
                try:
                    explicit_product = Product.browse(int(material_product_id))
                    if not explicit_product or not explicit_product.exists():
                        explicit_product = Product.browse()
                except Exception:
                    explicit_product = Product.browse()

            if explicit_product:
                res = {
                    "status": "exact",
                    "product": explicit_product,
                    "uom": explicit_product.uom_id,
                    "candidate_ids": [explicit_product.id],
                    "debug": {"trace_action": "paper_resolution_from_selected_material_product"},
                }
                material_product_name = material_product_name or explicit_product.display_name
                material_product_uom_id = material_product_uom_id or explicit_product.uom_id.id
                material_product_uom_name = material_product_uom_name or explicit_product.uom_id.display_name
            else:
                res = Map.resolve_engine_paper_variant_v1(
                    role=role_base,
                    gsm=gsm_val,
                    bf=bf_val,
                    color=color_val,
                    flute=flute_val if role_base == "medium" else None,
                    deckle_cm=deckle_cm,
                    material_template_id=material_template_id,
                    exact_resolution_only=True,
                )

            status = res.get("status") or "missing"
            prod = res.get("product") or Product.browse()
            uom = res.get("uom") or Uom.browse()
            candidate_ids = list(res.get("candidate_ids") or [])
            if status != "exact":
                prod = Product.browse()
                uom = Uom.browse()

            qty_per_fg = float(getattr(rm, "qty_per_fg", 0.0) or 0.0)
            qty_uom = getattr(rm, "qty_uom", "") or ""
            unresolved_note = ""
            if deckle_cm not in (None, 0):
                unresolved_note = "Exact paper variant not found for required deckle %.1f cm" % float(deckle_cm)
            else:
                unresolved_note = "Exact paper variant not found for selected layout"

            entry = {
                "key": _resolution_key(role_base, layer_idx),
                "role": role_base,
                "layer_no": layer_idx or None,
                "source_layer_no": stack_lookup_idx or None,
                "gsm": gsm_val,
                "bf": bf_val,
                "color": color_val or "",
                "material_template_id": material_template_id or None,
                "material_template_name": material_template_name or "",
                "material_product_id": material_product_id or None,
                "material_product_name": material_product_name or "",
                "flute_code": flute_val or "",
                "required_deckle_cm": deckle_cm,
                "selected_layout": {
                    "selection_source": (layout_selection or {}).get("selection_source") or "",
                    "stage_code": selected_layout_stage.get("stage_code") or "",
                    "stage_name": selected_layout_stage.get("stage_name") or "",
                    "up_count": int(selected_layout_option.get("up_count") or 0) or None,
                    "final_sheet_length_mm": float(selected_layout_option.get("final_sheet_length_mm", 0.0) or 0.0) or None,
                    "final_sheet_width_mm": float(selected_layout_option.get("final_sheet_width_mm", 0.0) or 0.0) or None,
                    "orientation_used": selected_layout_option.get("orientation_used") or "",
                    "matched_workcenter": dict(selected_layout_option.get("matched_workcenter") or {}),
                },
                "qty_per_fg": qty_per_fg,
                "qty_uom": qty_uom,
                "status": status,
                "product_id": prod.id or None,
                "product_name": prod.display_name if prod and prod.exists() else ("Missing / Unresolved" if status != "exact" else ""),
                "uom_id": uom.id or None,
                "uom_name": uom.display_name if uom and uom.exists() else "",
                "candidate_ids": candidate_ids,
                "resolution_note": unresolved_note if status != "exact" else "",
                "debug": dict(
                    res.get("debug") or {},
                    trace_action="paper_resolution_from_rm_requirement",
                    effective_layer_no=layer_idx or None,
                    source_layer_no=stack_lookup_idx or None,
                ),
            }
            paper_resolution.append(entry)
            resolution_by_key[_resolution_key(role_base, layer_idx)] = entry

            # Attach resolution metadata back onto rm.metadata for preview.
            if isinstance(meta, dict):
                res_meta = {
                    "status": status,
                    "product_id": entry["product_id"],
                    "product_name": entry["product_name"],
                    "uom_id": entry["uom_id"],
                    "uom_name": entry["uom_name"],
                    "candidate_ids": candidate_ids,
                    "required_deckle_cm": deckle_cm,
                    "material_template_id": material_template_id or None,
                    "material_template_name": material_template_name or "",
                    "material_product_id": material_product_id or None,
                    "material_product_name": material_product_name or "",
                    "flute_code": flute_val or "",
                    "resolution_note": entry["resolution_note"],
                }
                meta = dict(meta)
                meta["resolution"] = res_meta
                meta["resolution_status"] = status
                meta["required_deckle_cm"] = deckle_cm
                if flute_val:
                    meta["flute_code"] = flute_val
                meta["resolution_product_name"] = entry["product_name"] or ""
                if entry["resolution_note"]:
                    meta["resolution_note"] = entry["resolution_note"]
                setattr(rm, "metadata", meta)

        # Also annotate bom_struct components by matching role/layer when available.
        try:
            components = list(bom_struct.get("components") or [])
        except Exception:
            components = []
        for comp in components:
            try:
                meta = comp.get("metadata") or {}
            except Exception:
                meta = {}
            role = (meta.get("type") or meta.get("role") or "").strip().lower()
            layer_no = meta.get("layer_no")
            try:
                layer_idx = int(layer_no or 0)
            except Exception:
                layer_idx = 0
            key = _resolution_key(role, layer_idx)
            entry = resolution_by_key.get(key)
            if not entry:
                continue
            res_meta = {
                "status": entry["status"],
                "product_id": entry["product_id"],
                "product_name": entry["product_name"],
                "uom_id": entry["uom_id"],
                "uom_name": entry["uom_name"],
                "candidate_ids": entry["candidate_ids"],
                "required_deckle_cm": entry["required_deckle_cm"],
                "resolution_note": entry["resolution_note"],
            }
            meta = dict(meta)
            meta["resolution"] = res_meta
            meta["resolution_status"] = entry["status"]
            meta["required_deckle_cm"] = entry["required_deckle_cm"]
            meta["resolution_product_name"] = entry["product_name"] or ""
            if entry["resolution_note"]:
                meta["resolution_note"] = entry["resolution_note"]
            comp["metadata"] = meta

        # Preview must preserve the final board layer count. Some legacy
        # StageContext/RM combinations only carry a generic liner attachment
        # and omit the remaining base liner RM after a unit-sheet top layer
        # replacement. Recover non-replaced stack paper layers here so preview
        # and BoM resolution still reflect the actual stack. Replaced top
        # liner layers remain suppressed by preview_adapter.
        existing_keys = set(resolution_by_key)
        replaced_liner_layers = {
            int(entry.get("layer_no") or 0)
            for entry in paper_resolution
            if (entry.get("role") or "").strip().lower() == "top_roll"
            and int(entry.get("layer_no") or 0)
        }
        replaced_liner_layers.update(
            int(getattr(rm, "layer_no", 0) or 0)
            for rm in rm_reqs
            if (
                (getattr(rm, "role", "") or "").strip().lower() == "top_roll"
                or (
                    (getattr(rm, "role", "") or "").strip().lower() == "top_sheet_external"
                    and bool((getattr(rm, "metadata", {}) or {}).get("top_sheet_replaces_liner"))
                )
            )
            and int(getattr(rm, "layer_no", 0) or 0)
        )
        unit_sheet_prepends_stack_layers = any(
            (getattr(rm, "role", "") or "").strip().lower() == "top_sheet_external"
            and not bool((getattr(rm, "metadata", {}) or {}).get("top_sheet_replaces_liner"))
            for rm in rm_reqs
        )
        try:
            m2_per_box = float(getattr(getattr(result, "metrics", None), "m2_per_box", 0.0) or 0.0)
        except Exception:
            m2_per_box = 0.0

        for layer_idx, stack_info in sorted(stack_info_by_idx.items()):
            role_stack = _normalize_stack_paper_role(stack_info.get("role"))
            final_layer_idx = layer_idx + 1 if unit_sheet_prepends_stack_layers else layer_idx
            if role_stack not in ("liner", "medium"):
                paper_resolution.append(
                    {
                        "key": _resolution_key(role_stack or "unsupported", final_layer_idx),
                        "role": role_stack or "",
                        "layer_no": final_layer_idx,
                        "status": "trace",
                        "product_name": "",
                        "qty_per_fg": 0.0,
                        "qty_uom": "",
                        "debug": {
                            "trace_action": "paper_recovery_skipped_unsupported_stack_role",
                            "raw_role": stack_info.get("raw_role") or "",
                            "normalized_role": role_stack,
                            "source_layer_no": layer_idx,
                            "layer_no": final_layer_idx,
                            "unit_sheet_prepends_stack_layers": unit_sheet_prepends_stack_layers,
                        },
                    }
                )
                continue
            if role_stack == "liner" and final_layer_idx in replaced_liner_layers:
                paper_resolution.append(
                    {
                        "key": _resolution_key(role_stack, final_layer_idx),
                        "role": role_stack,
                        "layer_no": final_layer_idx,
                        "status": "trace",
                        "product_name": "",
                        "qty_per_fg": 0.0,
                        "qty_uom": "",
                        "debug": {
                            "trace_action": "paper_recovery_skipped_replaced_liner_layer",
                            "raw_role": stack_info.get("raw_role") or "",
                            "normalized_role": role_stack,
                            "source_layer_no": layer_idx,
                            "layer_no": final_layer_idx,
                            "replaced_liner_layers": sorted(replaced_liner_layers),
                            "unit_sheet_prepends_stack_layers": unit_sheet_prepends_stack_layers,
                        },
                    }
                )
                continue
            key = _resolution_key(role_stack, final_layer_idx)
            existing_entry = resolution_by_key.get(key)
            if (role_stack, layer_idx) in existing_physical_stack_keys:
                paper_resolution.append(
                    {
                        "key": key,
                        "role": role_stack,
                        "layer_no": final_layer_idx,
                        "status": "trace",
                        "product_name": "",
                        "qty_per_fg": 0.0,
                        "qty_uom": "",
                        "debug": {
                            "trace_action": "paper_recovery_skipped_existing_physical_stack_layer",
                            "raw_role": stack_info.get("raw_role") or "",
                            "normalized_role": role_stack,
                            "source_layer_no": layer_idx,
                            "layer_no": final_layer_idx,
                            "unit_sheet_prepends_stack_layers": unit_sheet_prepends_stack_layers,
                        },
                    }
                )
                continue
            if existing_entry and (existing_entry.get("status") or "") == "exact":
                paper_resolution.append(
                    {
                        "key": key,
                        "role": role_stack,
                        "layer_no": final_layer_idx,
                        "status": "trace",
                        "product_name": "",
                        "qty_per_fg": 0.0,
                        "qty_uom": "",
                        "debug": {
                            "trace_action": "paper_recovery_skipped_existing_key",
                            "raw_role": stack_info.get("raw_role") or "",
                            "normalized_role": role_stack,
                            "source_layer_no": layer_idx,
                            "layer_no": final_layer_idx,
                            "unit_sheet_prepends_stack_layers": unit_sheet_prepends_stack_layers,
                        },
                    }
                )
                continue

            gsm_val = float(stack_info.get("gsm") or 0.0)
            if gsm_val <= 0.0 or m2_per_box <= 0.0:
                paper_resolution.append(
                    {
                        "key": key,
                        "role": role_stack,
                        "layer_no": layer_idx,
                        "status": "trace",
                        "product_name": "",
                        "qty_per_fg": 0.0,
                        "qty_uom": "",
                        "debug": {
                            "trace_action": "paper_recovery_skipped_missing_qty_basis",
                            "raw_role": stack_info.get("raw_role") or "",
                            "normalized_role": role_stack,
                            "source_layer_no": layer_idx,
                            "layer_no": final_layer_idx,
                            "gsm": gsm_val,
                            "m2_per_box": m2_per_box,
                            "unit_sheet_prepends_stack_layers": unit_sheet_prepends_stack_layers,
                        },
                    }
                )
                continue
            bf_val = stack_info.get("bf")
            color_val = stack_info.get("color") or None
            material_template_id = stack_info.get("material_template_id") or None
            material_template_name = stack_info.get("material_template_name") or ""
            flute_val = stack_info.get("flute_code") or None
            try:
                flute_tuf = float(stack_info.get("flute_tuf") or 0.0)
            except Exception:
                flute_tuf = 0.0
            if role_stack != "medium" or flute_tuf <= 0.0:
                flute_tuf = 1.0
            res = Map.resolve_engine_paper_variant_v1(
                role=role_stack,
                gsm=gsm_val,
                bf=bf_val,
                color=color_val,
                flute=flute_val if role_stack == "medium" else None,
                deckle_cm=deckle_cm,
                material_template_id=material_template_id,
                exact_resolution_only=True,
            )
            status = res.get("status") or "missing"
            prod = res.get("product") or Product.browse()
            uom = res.get("uom") or Uom.browse()
            candidate_ids = list(res.get("candidate_ids") or [])
            if status != "exact":
                prod = Product.browse()
                uom = Uom.browse()
            unresolved_note = (
                "Exact paper variant not found for required deckle %.1f cm" % float(deckle_cm)
                if deckle_cm not in (None, 0)
                else "Exact paper variant not found for selected layout"
            )
            entry = {
                "key": key,
                "role": role_stack,
                "layer_no": final_layer_idx,
                "gsm": gsm_val,
                "bf": bf_val,
                "color": color_val or "",
                "material_template_id": material_template_id or None,
                "material_template_name": material_template_name or "",
                "flute_code": flute_val or "",
                "required_deckle_cm": deckle_cm,
                "selected_layout": {
                    "selection_source": (layout_selection or {}).get("selection_source") or "",
                    "stage_code": selected_layout_stage.get("stage_code") or "",
                    "stage_name": selected_layout_stage.get("stage_name") or "",
                    "up_count": int(selected_layout_option.get("up_count") or 0) or None,
                    "final_sheet_length_mm": float(selected_layout_option.get("final_sheet_length_mm", 0.0) or 0.0) or None,
                    "final_sheet_width_mm": float(selected_layout_option.get("final_sheet_width_mm", 0.0) or 0.0) or None,
                    "orientation_used": selected_layout_option.get("orientation_used") or "",
                    "matched_workcenter": dict(selected_layout_option.get("matched_workcenter") or {}),
                },
                "qty_per_fg": (gsm_val * m2_per_box * flute_tuf) / 1000.0,
                "qty_uom": "kg",
                "status": status,
                "product_id": prod.id or None,
                "product_name": prod.display_name if prod and prod.exists() else ("Missing / Unresolved" if status != "exact" else ""),
                "uom_id": uom.id or None,
                "uom_name": uom.display_name if uom and uom.exists() else "",
                "candidate_ids": candidate_ids,
                "resolution_note": unresolved_note if status != "exact" else "",
                "debug": dict(
                    res.get("debug") or {},
                    trace_action=(
                        "paper_recovery_replaced_existing_missing_key"
                        if existing_entry
                        else "paper_recovery_emitted_stack_layer"
                    ),
                    recovered_from_stack=True,
                    raw_role=stack_info.get("raw_role") or "",
                    normalized_role=role_stack,
                    source_layer_no=layer_idx,
                    layer_no=final_layer_idx,
                    unit_sheet_prepends_stack_layers=unit_sheet_prepends_stack_layers,
                    flute_code=stack_info.get("flute_code") or "",
                    tuf_applied=flute_tuf if role_stack == "medium" else 1.0,
                ),
            }
            if existing_entry:
                existing_entry.clear()
                existing_entry.update(entry)
            else:
                paper_resolution.append(entry)
            resolution_by_key[key] = entry
            existing_keys.add(key)

        return paper_resolution

    def _get_cost_source(self):
        """Return the requested costing source with validation and fallback."""

        self.ensure_one()
        val = (self.cost_source or "").strip() or "standard_price"
        if val not in ("standard_price", "last_purchase_price", "vendor_price"):
            val = "standard_price"
        return val

    def _get_last_purchase_price(self, product):
        """Best-effort last purchase price for a product.variant.

        Preference order:
          1) product.last_purchase_price field when present and > 0,
          2) most recent positive stock valuation layer unit_cost/value,
          3) most recent confirmed/done purchase.order.line.price_unit,
          4) 0.0 when unavailable.
        """

        try:
            val = getattr(product, "last_purchase_price", None)
        except Exception:
            val = None
        try:
            if val not in (None, ""):
                v = float(val or 0.0)
                if v > 0.0:
                    return v
        except Exception:
            pass

        # Fallback 2: latest stock valuation layer with positive quantity/value.
        try:
            SVL = self.env["stock.valuation.layer"].sudo()
        except Exception:
            SVL = None
        if SVL is not None:
            try:
                company_id = self.env.company.id
            except Exception:
                company_id = None
            domain = [("product_id", "=", product.id)]
            if company_id:
                domain.append(("company_id", "=", company_id))
            try:
                svl = SVL.search(domain, order="create_date desc, id desc", limit=1)
            except Exception:
                svl = SVL.browse()
            if svl and svl.exists():
                try:
                    qty = float(svl.quantity or 0.0)
                except Exception:
                    qty = 0.0
                try:
                    value = float(svl.value or 0.0)
                except Exception:
                    value = 0.0
                try:
                    unit_cost = float(svl.unit_cost or 0.0)
                except Exception:
                    unit_cost = 0.0
                if unit_cost > 0.0:
                    return unit_cost
                if qty > 0.0 and value != 0.0:
                    return value / qty

        # Fallback 3: search recent PO lines when purchase is installed.
        try:
            POL = self.env["purchase.order.line"].sudo()
        except Exception:
            POL = None
        if POL is None:
            return 0.0

        try:
            line = POL.search(
                [("product_id", "=", product.id), ("state", "in", ["purchase", "done"])],
                order="order_id.date_order desc, id desc",
                limit=1,
            )
            if line and line.exists():
                return float(line.price_unit or 0.0)
        except Exception:
            return 0.0

        return 0.0

    def _get_vendor_price(self, product):
        """Placeholder for vendor-price costing.

        Currently returns 0.0 so that callers fall back to standard_price.
        """

        _ = product
        return 0.0

    def _safe_product_standard_price(self, product):
        """Return a product's standard price as a float.

        Odoo 18 stores this company-dependent field on product.product as
        JSONB. Normal ORM reads normally return a float, but this helper also
        tolerates dict-like values to make costing diagnostics explicit.
        """

        if not product:
            return 0.0

        def _as_float(value):
            try:
                if value in (None, ""):
                    return 0.0
                return float(value or 0.0)
            except Exception:
                return 0.0

        try:
            raw = getattr(product, "standard_price", 0.0)
        except Exception:
            raw = 0.0
        if isinstance(raw, dict):
            company_id = None
            try:
                company_id = self.env.company.id
            except Exception:
                company_id = None
            for key in (str(company_id or ""), company_id, "False", False):
                if key in raw:
                    return _as_float(raw.get(key))
            return 0.0
        price = _as_float(raw)
        if price > 0.0:
            return price

        # Defensive fallback for custom/Odoo-version variants where cost is
        # exposed on the template instead of the variant.
        try:
            tmpl = product.product_tmpl_id
            raw_tmpl = getattr(tmpl, "standard_price", 0.0)
        except Exception:
            raw_tmpl = 0.0
        if isinstance(raw_tmpl, dict):
            company_id = None
            try:
                company_id = self.env.company.id
            except Exception:
                company_id = None
            for key in (str(company_id or ""), company_id, "False", False):
                if key in raw_tmpl:
                    return _as_float(raw_tmpl.get(key))
            return 0.0
        return _as_float(raw_tmpl)

    def _get_unit_cost_for_preview(self, product, uom, cost_source):
        """Return selected unit cost diagnostics for the given product/UoM.

        cost_source is one of:
          - 'standard_price'
          - 'last_purchase_price'
          - 'vendor_price'

        The selected source is strict for preview diagnostics. Missing/zero
        cost values are reported by the caller instead of silently falling
        back to another source.
        """

        try:
            base_cost = 0.0
            effective = cost_source or "standard_price"

            if cost_source == "last_purchase_price":
                base_cost = self._get_last_purchase_price(product)
            elif cost_source == "vendor_price":
                base_cost = self._get_vendor_price(product)
            else:
                base_cost = self._safe_product_standard_price(product)
                effective = "standard_price"
        except Exception:
            base_cost = 0.0
            effective = cost_source or "standard_price"

        unit_cost = float(base_cost or 0.0)

        # Convert from product's UoM into the target UoM when categories match.
        try:
            puom = product.uom_id
        except Exception:
            puom = None
        try:
            if puom and uom and puom.category_id and uom.category_id and puom.category_id.id == uom.category_id.id:
                if puom.id != uom.id:
                    unit_cost = puom._compute_price(unit_cost, uom)
        except Exception:
            # Keep base unit_cost when conversion fails.
            unit_cost = float(base_cost or 0.0)

        return unit_cost, effective

    def _compute_cost_summary(self, result, order_qty, paper_resolution=None, consumable_state=None):
        """Compute material costing diagnostics for the current preview.

        This helper:
        - reads Engine V2 rm_requirements (per-FG quantities),
        - resolves them to concrete products/UoMs using the same mapping
          semantics as the BoM adapter (including paper resolution),
        - multiplies quantities by product.standard_price to obtain:
            * per-FG material cost,
            * order-level material cost,
        - returns a JSON-serialisable dict with a per-RM cost index
          used by both HTML preview and raw_json.

        It must NOT mutate engine quantities, metrics, or BoM structures.
        """

        self.ensure_one()

        try:
            rm_reqs = list(getattr(result, "rm_requirements", []) or [])
        except Exception:
            rm_reqs = []
        if not rm_reqs:
            return None

        env = self.env
        Map = env["corr.material.product.map"].sudo()
        Product = env["product.product"].sudo()
        Uom = env["uom.uom"].sudo()

        # Reuse adapter helpers when available to keep role and subtype
        # normalisation consistent with BoM creation.
        normalize_role = getattr(
            engine_v2_bom_adapter, "_normalize_role_for_mapping", lambda r: (r or "").lower()
        )
        normalize_code = getattr(
            engine_v2_bom_adapter, "_normalize_subtype_code", lambda v: (str(v).strip().lower() or None) if v not in (None, "") else None
        )

        try:
            oq = float(order_qty or 0.0)
        except Exception:
            oq = 0.0
        if oq <= 0.0:
            oq = 1.0

        # Company currency for display / diagnostics.
        currency = getattr(env.company, "currency_id", None)
        cur_info = {
            "id": currency.id if currency else None,
            "name": currency.name if currency else "",
            "symbol": currency.symbol if currency else "",
        }

        components = []
        costing_trace = []
        rm_cost_index = {}
        missing_cost = []

        requested_source = self._get_cost_source()
        paper_resolution_by_key = {}
        piece_paper_resolution_by_key = {}
        for entry in list(paper_resolution or []):
            if not isinstance(entry, dict):
                continue
            role_key = (entry.get("role") or "").strip().lower()
            try:
                layer_key = int(entry.get("layer_no") or 0)
            except Exception:
                layer_key = 0
            if role_key and layer_key:
                paper_resolution_by_key["%s:%s" % (role_key, layer_key)] = entry
                piece_key = self._canonical_piece_key(
                    entry.get("piece_key")
                    or dict(entry.get("metadata") or {}).get("piece_key")
                )
                if piece_key:
                    piece_paper_resolution_by_key["%s:%s:%s" % (piece_key, role_key, layer_key)] = entry

        total_cost_per_fg = 0.0
        total_cost_for_order = 0.0
        effective_sources = set()
        consumable_state = dict(consumable_state or self._effective_consumable_state())

        for rm in rm_reqs:
            product_key = getattr(rm, "product_key", "") or ""
            role_raw = getattr(rm, "role", "") or ""
            metadata = getattr(rm, "metadata", {}) or {}
            if (
                is_consumable_role is not None
                and is_consumable_role(role_raw, product_key=product_key, metadata=metadata)
                and should_include_consumable is not None
                and not should_include_consumable(
                    consumable_state,
                    role_raw,
                    product_key=product_key,
                    metadata=metadata,
                    material_type="consumable",
                    category="consumable",
                )
            ):
                costing_trace.append(
                    {
                        "product_key": product_key,
                        "role": role_raw,
                        "action": "skipped_disabled_consumable",
                    }
                )
                continue
            base_role = normalize_role(role_raw)
            stage_key = getattr(rm, "stage_key", None)
            layer_no = getattr(rm, "layer_no", None)
            try:
                layer_idx = int(layer_no or 0)
            except Exception:
                layer_idx = 0

            piece_key = self._canonical_piece_key(metadata.get("piece_key"))

            product = None
            uom = None
            unresolved_reason = ""

            # 1) Board papers: use preview paper resolution so costing
            #    follows the same variant choice as the preview + create
            #    flow (resolve_engine_paper_variant_v1).
            if base_role in ("liner", "medium"):
                res = metadata.get("resolution") or {}
                status = (metadata.get("resolution_status") or res.get("status") or "").strip().lower()
                resolved_statuses = {"exact", "resolved"}
                prod_id = metadata.get("product_id") or res.get("product_id")
                uom_id = metadata.get("uom_id") or res.get("uom_id")
                recovered_entry = None
                if status not in resolved_statuses or not prod_id:
                    if piece_key:
                        recovered_entry = piece_paper_resolution_by_key.get(
                            "%s:%s:%s" % (piece_key, base_role, layer_idx)
                        )
                    if not recovered_entry:
                        recovered_entry = paper_resolution_by_key.get("%s:%s" % (base_role, layer_idx))
                    recovered_status = (recovered_entry.get("status") or "").strip().lower() if recovered_entry else ""
                    if recovered_entry and recovered_status in resolved_statuses:
                        status = recovered_status
                        prod_id = recovered_entry.get("product_id")
                        uom_id = recovered_entry.get("uom_id")

                if status not in resolved_statuses or not prod_id:
                    missing_cost.append(
                        {
                            "product_key": product_key,
                            "role": role_raw,
                            "base_role": base_role,
                            "stage_key": stage_key,
                            "layer_no": layer_idx or None,
                            "reason": "unresolved_paper_variant",
                            "resolution_status": status or "missing",
                        }
                    )
                    continue

                product = Product.browse(prod_id)
                if not product or not product.exists():
                    missing_cost.append(
                        {
                            "product_key": product_key,
                            "role": role_raw,
                            "base_role": base_role,
                            "stage_key": stage_key,
                            "layer_no": layer_idx or None,
                            "reason": "resolved_product_missing",
                            "resolution_status": status or "missing",
                        }
                    )
                    continue

                if uom_id:
                    uom = Uom.browse(uom_id)
                    if not uom or not uom.exists():
                        uom = product.uom_id
                else:
                    uom = product.uom_id

            # 2) Consumables and other RMs: reuse corr.material.product.map
            #    semantics from the BoM adapter.
            else:
                engine_code = None
                if role_raw.strip().lower() == "top_sheet_external":
                    prod_id = metadata.get("top_sheet_product_id")
                    try:
                        prod_id = int(prod_id or 0)
                    except Exception:
                        prod_id = 0
                    if prod_id:
                        product = Product.browse(prod_id)
                        if product and product.exists():
                            uom = product.uom_id
                        else:
                            product = None
                            unresolved_reason = "top_sheet_product_missing"
                    else:
                        unresolved_reason = "top_sheet_product_id_missing"
                elif base_role == "adhesive":
                    kind = (metadata.get("kind") or "").strip().lower()
                    if not kind and product_key.lower().startswith("rm_adhesive_"):
                        kind = product_key.lower().replace("rm_adhesive_", "", 1)
                    if kind in ("adhesive_corrugation", "corrugation"):
                        engine_code = "corrugation"
                    elif kind in ("adhesive_board", "board"):
                        engine_code = "board"
                    elif kind in ("adhesive_fg", "fg", "seaming"):
                        engine_code = "fg"
                elif base_role == "ink":
                    engine_code = normalize_code(metadata.get("print_process"))
                elif base_role == "wire":
                    prod_id = metadata.get("wire_product_id") or metadata.get("product_id")
                    try:
                        prod_id = int(prod_id or 0)
                    except Exception:
                        prod_id = 0
                    if prod_id:
                        product = Product.browse(prod_id)
                        if product and product.exists():
                            uom = product.uom_id
                        else:
                            product = None
                            unresolved_reason = "wire_product_missing"
                    engine_code = normalize_code(metadata.get("wire_grade"))
                elif base_role == "lamination":
                    lam_code = metadata.get("lamination_type")
                    if not lam_code:
                        lam_id = metadata.get("lamination_type_id")
                        try:
                            lam_int = int(lam_id or 0)
                        except Exception:
                            lam_int = 0
                        if lam_int:
                            try:
                                LamType = env["corr.lamination.type"].sudo()
                                lam = LamType.browse(lam_int)
                                if lam and lam.exists():
                                    lam_code = lam.code or None
                            except Exception:
                                lam_code = lam_code
                    engine_code = normalize_code(lam_code)

                mapping = False
                if product is None and not unresolved_reason:
                    mapping = Map.find_match(base_role, engine_code=engine_code)
                if product is None and not mapping:
                    reason = unresolved_reason or "no_mapping"
                    missing_cost.append(
                        {
                            "product_key": product_key,
                            "role": role_raw,
                            "base_role": base_role,
                            "stage_key": stage_key,
                            "layer_no": layer_idx or None,
                            "reason": reason,
                            "engine_code": engine_code,
                        }
                    )
                    continue

                if product is None:
                    product = mapping.product_id
                    uom = mapping.uom_id or product.uom_id

            if not product or not product.exists():
                missing_cost.append(
                    {
                        "product_key": product_key,
                        "role": role_raw,
                        "base_role": base_role,
                        "stage_key": stage_key,
                        "layer_no": layer_idx or None,
                        "reason": "product_missing",
                    }
                )
                continue

            if not uom or not uom.exists():
                uom = product.uom_id

            try:
                qty_per_fg = float(getattr(rm, "qty_per_fg", 0.0) or 0.0)
            except Exception:
                qty_per_fg = 0.0
            if qty_per_fg <= 0.0:
                continue

            # Order-level quantity for costing must use the resolved RM
            # line quantity directly. Unit-sheet top-sheet RMs are already
            # emitted as business units by the engine/preview model; applying
            # layout UPS rounding again would under-cost them.
            qty_for_order = qty_per_fg * oq

            # Raw cost inputs for debugging.
            try:
                raw_standard_price = self._safe_product_standard_price(product)
            except Exception:
                raw_standard_price = 0.0
            try:
                raw_last_purchase = float(self._get_last_purchase_price(product) or 0.0)
            except Exception:
                raw_last_purchase = 0.0

            unit_cost, effective_source = self._get_unit_cost_for_preview(product, uom, requested_source)
            effective_sources.add(effective_source)

            fallback_reason = ""
            if effective_source != requested_source:
                fallback_reason = "requested %s, fell back to %s" % (requested_source, effective_source)
            cost_warning = ""
            cost_warning_message = ""
            if unit_cost <= 0.0:
                if requested_source == "standard_price":
                    cost_warning = "standard_price_missing_or_zero"
                    cost_warning_message = "Standard price missing/zero for product %s" % (
                        product.display_name or product.id
                    )
                elif requested_source == "last_purchase_price":
                    cost_warning = "last_purchase_price_missing_or_zero"
                    cost_warning_message = "Last purchase price missing/zero for product %s" % (
                        product.display_name or product.id
                    )
                elif requested_source == "vendor_price":
                    cost_warning = "vendor_price_missing_or_zero"
                    cost_warning_message = "Vendor price missing/zero for product %s" % (
                        product.display_name or product.id
                    )
                else:
                    cost_warning = "selected_cost_missing_or_zero"
                    cost_warning_message = "Selected cost missing/zero for product %s" % (
                        product.display_name or product.id
                    )
                missing_cost.append(
                    {
                        "product_key": product_key,
                        "role": role_raw,
                        "base_role": base_role,
                        "stage_key": stage_key,
                        "layer_no": layer_idx or None,
                        "product_id": product.id,
                        "product_name": product.display_name or "",
                        "reason": cost_warning,
                        "cost_source": requested_source,
                        "standard_price": raw_standard_price,
                        "last_purchase_price": raw_last_purchase,
                        "warning": cost_warning_message,
                    }
                )

            cost_per_fg = qty_per_fg * unit_cost
            cost_for_order = qty_for_order * unit_cost

            total_cost_per_fg += cost_per_fg
            total_cost_for_order += cost_for_order

            key_str = "%s|%s|%s|%s" % (
                product_key or "",
                role_raw or "",
                layer_idx or "",
                (stage_key or "").strip() if isinstance(stage_key, str) else (stage_key or ""),
            )
            rm_cost_index[key_str] = {
                "cost_per_fg": cost_per_fg,
                "cost_for_order": cost_for_order,
            }

            line_trace = {
                "product_id": product.id,
                "product_name": product.display_name or "",
                "product_key": product_key,
                "role": base_role,
                "engine_role": role_raw,
                "stage_key": stage_key,
                "target_stage": stage_key,
                "layer_no": layer_idx or None,
                "qty_per_fg": qty_per_fg,
                "qty_for_order": qty_for_order,
                "uom_id": uom.id if uom else None,
                "uom_name": uom.display_name if uom and uom.exists() else "",
                "uom": uom.display_name if uom and uom.exists() else "",
                "unit_cost": unit_cost,
                "selected_unit_cost": unit_cost,
                "cost_per_fg": cost_per_fg,
                "cost_for_order": cost_for_order,
                "cost_source": requested_source,
                # Debug fields for costing inspection.
                "requested_cost_source": requested_source,
                "effective_cost_source": effective_source,
                "product_uom_id": product.uom_id.id if getattr(product, "uom_id", None) else None,
                "target_uom_id": uom.id if uom else None,
                "standard_price": raw_standard_price,
                "last_purchase_price": raw_last_purchase,
                "raw_standard_price": raw_standard_price,
                "raw_last_purchase_price": raw_last_purchase,
                "converted_unit_cost": unit_cost,
                "fallback_reason": fallback_reason,
                "cost_warning": cost_warning,
                "cost_warning_message": cost_warning_message,
                "piece_key": piece_key or "",
                "piece_index": metadata.get("piece_index"),
                "piece_name": metadata.get("piece_name") or "",
                "stack_source": metadata.get("stack_source") or "inherited",
                "recipe_signature": metadata.get("recipe_signature") or "",
                "source_layer_no": metadata.get("source_layer_no"),
                "gsm": metadata.get("gsm"),
                "bf": metadata.get("bf"),
                "color": metadata.get("color") or "",
                "flute_code": metadata.get("flute_code") or metadata.get("flute") or "",
            }
            components.append(line_trace)
            costing_trace.append(dict(line_trace))

        return {
            "order_qty": oq,
            "currency": cur_info,
            "total_cost_per_fg": total_cost_per_fg,
            "total_cost_for_order": total_cost_for_order,
            "cost_source": requested_source,
            "effective_cost_sources": sorted(effective_sources),
            "components": components,
            "costing_trace": costing_trace,
            "rm_cost_index": rm_cost_index,
            "missing_cost": missing_cost,
        }

    def _compute_operation_costing(self, qgraph, costing, plant_param):
        """Compute operation costing per quantity StageGraph stage.

        This helper:
        - uses the quantity StageGraph node quantities as the stage quantity
          basis,
        - reads per-plant, per-stage operation costing configuration from
          corr.engine.v2.operation.cost,
        - aggregates material cost per quantity stage using the same RM â†’
          StageGraph node routing as the BoM adapter,
        - returns a JSON-serialisable dict with stage-wise operation and
          total costs, and annotates the quantity StageGraph nodes with an
          ``operation_cost`` block for raw_json debugging.

        Formulas:
          - material_cost(stage) = sum(component.cost_for_order routed to stage)
          - machine_cost(stage) = machine_rate * StageGraph node qty
          - labor_cost(stage) = labor_rate * StageGraph node qty
          - total_operation_cost(stage) = machine_cost + labor_cost
          - stage_total_cost(stage) = material_cost + total_operation_cost

        Stage totals are local to the bucket. They are not cumulative
        carry-forward totals from upstream stages.

        It must NOT mutate engine quantities, metrics, or BoM structures.
        """

        self.ensure_one()

        # Guard: quantity StageGraph is required for operation costing.
        try:
            nodes = getattr(qgraph, "nodes", {}) or {}
        except Exception:
            nodes = {}
        if not nodes:
            return None

        # Base stage info from quantity StageGraph.
        def _qty_basis_label(node_name):
            key = (node_name or "").strip().lower()
            if key == "raw_material":
                return "per_kg"
            if key in ("board", "sheet"):
                return "per_sheet"
            if key == "blanks":
                return "per_blank"
            if key == "finished_goods":
                return "per_box"
            return "per_stage_qty"

        stage_info = {}
        for name, node in nodes.items():
            try:
                qty = float(getattr(node, "qty", 0.0) or 0.0)
            except Exception:
                qty = 0.0
            stage_info[name] = {
                "qty": qty,
                "stage_qty_basis": "quantity_stage_graph_qty",
                "stage_qty_basis_label": _qty_basis_label(name),
                "material_cost": 0.0,
                "machine_cost": 0.0,
                "labor_cost": 0.0,
                "total_operation_cost": 0.0,
            }

        # Aggregate material cost per quantity StageGraph node by routing
        # RM stage_key â†’ StageGraph node using the same helper as the BoM
        # adapter.
        total_material_cost = 0.0
        if costing:
            try:
                total_material_cost = float(costing.get("total_cost_for_order", 0.0) or 0.0)
            except Exception:
                total_material_cost = 0.0
            components = list(costing.get("components") or [])
            trace_lines = list(costing.get("costing_trace") or [])
            resolve_rm_node = _engine_v2_resolve_rm_node
            for idx, comp in enumerate(components):
                try:
                    stage_key = comp.get("stage_key")
                except Exception:
                    stage_key = None
                try:
                    cost_for_order = float(comp.get("cost_for_order", 0.0) or 0.0)
                except Exception:
                    cost_for_order = 0.0
                node_name = None
                if resolve_rm_node and qgraph is not None:
                    try:
                        node_name = resolve_rm_node(qgraph, stage_key, rm=comp)
                    except Exception:
                        node_name = None
                target_stage = node_name or stage_key or ""
                comp["target_stage"] = target_stage
                if idx < len(trace_lines) and isinstance(trace_lines[idx], dict):
                    trace_lines[idx]["target_stage"] = target_stage
                if node_name and node_name in stage_info:
                    stage_info[node_name]["material_cost"] = stage_info[node_name].get("material_cost", 0.0) + cost_for_order

        # Per-plant, per-stage operation costing configuration.
        plant = plant_param or self.plant_id
        total_operation_cost = 0.0
        if plant:
            OpCost = self.env["corr.engine.v2.operation.cost"].sudo()
            configs = OpCost.search([("plant_id", "=", plant.id), ("active", "=", True)])
            cfg_by_bucket = {}
            for rec in configs:
                raw_key = (rec.stage_key or "").strip().lower()
                if not raw_key:
                    continue
                bucket = None
                # Direct match against quantity bucket names.
                for bucket_name in stage_info.keys():
                    if (bucket_name or "").strip().lower() == raw_key:
                        bucket = bucket_name
                        break
                # Allow dynamic engine stage keys (2ply, ts_print, diecut, fg, ...)
                # by mapping them to quantity buckets via the pure Engine V2
                # resolver when available.
                if not bucket and _engine_v2_resolve_rm_node and qgraph is not None:
                    try:
                        mapped = _engine_v2_resolve_rm_node(qgraph, raw_key)
                    except Exception:
                        mapped = ""
                    mapped_key = (mapped or "").strip().lower()
                    if mapped_key:
                        for bucket_name in stage_info.keys():
                            if (bucket_name or "").strip().lower() == mapped_key:
                                bucket = bucket_name
                                break
                if not bucket or bucket in cfg_by_bucket:
                    continue
                cfg_by_bucket[bucket] = rec

            for name, info in stage_info.items():
                cfg = cfg_by_bucket.get(name)
                if not cfg:
                    continue
                qty = float(info.get("qty", 0.0) or 0.0)

                mach_method = cfg.machine_cost_method or "per_unit"
                try:
                    mach_rate = float(cfg.machine_rate or 0.0)
                except Exception:
                    mach_rate = 0.0
                if mach_method == "per_unit":
                    machine_cost = mach_rate * qty
                else:
                    # Placeholder for future per_hour support.
                    machine_cost = 0.0

                lab_method = cfg.labor_cost_method or "per_unit"
                try:
                    lab_rate = float(cfg.labor_rate or 0.0)
                except Exception:
                    lab_rate = 0.0
                if lab_method == "per_unit":
                    labor_cost = lab_rate * qty
                else:
                    labor_cost = 0.0

                stage_op_total = machine_cost + labor_cost
                total_operation_cost += stage_op_total

                info.update(
                    {
                        "machine_cost": machine_cost,
                        "labor_cost": labor_cost,
                        "total_operation_cost": stage_op_total,
                        "machine_cost_method": mach_method,
                        "machine_rate": mach_rate,
                        "labor_cost_method": lab_method,
                        "labor_rate": lab_rate,
                    }
                )

        # Final stage totals (material + operation).
        for info in stage_info.values():
            try:
                mat = float(info.get("material_cost", 0.0) or 0.0)
            except Exception:
                mat = 0.0
            try:
                op = float(info.get("total_operation_cost", 0.0) or 0.0)
            except Exception:
                op = 0.0
            info["stage_total_cost"] = mat + op

        # Attach operation_cost metadata for all quantity StageGraph nodes
        # so that per-stage operation costing is visible in raw_json even
        # when some stages have no explicit configuration (their costs
        # remain zero but the structure is present).
        try:
            for name, info in stage_info.items():
                node = nodes.get(name)
                if node is None:
                    continue
                meta = getattr(node, "meta", None) or {}
                try:
                    qty = float(info.get("qty", 0.0) or 0.0)
                except Exception:
                    qty = 0.0
                try:
                    machine_cost = float(info.get("machine_cost", 0.0) or 0.0)
                except Exception:
                    machine_cost = 0.0
                try:
                    labor_cost = float(info.get("labor_cost", 0.0) or 0.0)
                except Exception:
                    labor_cost = 0.0
                try:
                    total_op = float(info.get("total_operation_cost", 0.0) or 0.0)
                except Exception:
                    total_op = 0.0
                try:
                    stage_mat = float(info.get("material_cost", 0.0) or 0.0)
                except Exception:
                    stage_mat = 0.0
                try:
                    stage_total = float(info.get("stage_total_cost", 0.0) or 0.0)
                except Exception:
                    stage_total = stage_mat + total_op

                op_meta = {
                    "machine_cost": machine_cost,
                    "labor_cost": labor_cost,
                    "total_operation_cost": total_op,
                    "stage_material_cost": stage_mat,
                    "stage_total_cost": stage_total,
                    "machine_cost_method": info.get("machine_cost_method") or "",
                    "machine_rate": float(info.get("machine_rate", 0.0) or 0.0),
                    "labor_cost_method": info.get("labor_cost_method") or "",
                    "labor_rate": float(info.get("labor_rate", 0.0) or 0.0),
                    "stage_qty": qty,
                    "stage_qty_basis": info.get("stage_qty_basis") or "quantity_stage_graph_qty",
                    "stage_qty_basis_label": info.get("stage_qty_basis_label") or _qty_basis_label(name),
                }
                meta["operation_cost"] = op_meta
                node.meta = meta
        except Exception:
            # Operation costing metadata is best-effort and must not
            # break the preview flow.
            pass

        total_production_cost = total_material_cost + total_operation_cost

        return {
            "stages": stage_info,
            "total_material_cost": total_material_cost,
            "total_operation_cost": total_operation_cost,
            "total_production_cost": total_production_cost,
        }

    def _ensure_bom_adapter_available(self):
        if not engine_v2_bom_adapter:
            raise UserError(
                _(
                    "Engine V2 BoM adapter is not available. "
                    "Please ensure corr_mrp_bridge is correctly installed."
                )
            )

    def _format_materialization_blocker_summary(self, materialization_debug, limit=8):
        resolver = dict((materialization_debug or {}).get("resolver_dry_run") or {})
        lines = []

        def add_line(text):
            text = str(text or "").strip()
            if text and len(lines) < limit:
                lines.append(text)

        for blocker in list(resolver.get("blockers") or []):
            if isinstance(blocker, dict):
                scope = blocker.get("scope") or "resolver"
                candidate = blocker.get("candidate_key") or blocker.get("segment_key") or "-"
                reason = blocker.get("reason") or blocker.get("code") or "-"
                add_line("%s %s: %s" % (scope, candidate, reason))
            else:
                add_line(str(blocker))

        for bom in list(resolver.get("boms") or []):
            bom = dict(bom or {})
            if bom.get("action") != "blocked_bom" and not bom.get("line_diagnostics"):
                continue
            bom_key = bom.get("bom_candidate_key") or "-"
            if bom.get("action") == "blocked_bom":
                add_line("bom %s: %s" % (bom_key, bom.get("reason") or "blocked_bom"))
            for diagnostic in list(bom.get("line_diagnostics") or []):
                diagnostic = dict(diagnostic or {})
                reason = diagnostic.get("reason") or "-"
                role = diagnostic.get("role") or "-"
                product = (
                    diagnostic.get("product_name")
                    or diagnostic.get("product_id")
                    or "unresolved product"
                )
                suffix = []
                deckle = diagnostic.get("required_deckle_cm")
                if deckle not in (None, "", 0, 0.0):
                    suffix.append("deckle=%s" % deckle)
                uom = diagnostic.get("uom") or ""
                if uom:
                    suffix.append("uom=%s" % uom)
                lookup_reason = self._materialization_lookup_reason(diagnostic)
                if lookup_reason:
                    suffix.append(str(lookup_reason))
                detail = " (%s)" % ", ".join(suffix) if suffix else ""
                add_line(
                    "line %s / %s / %s: %s%s"
                    % (bom_key, role, product, reason, detail)
                )

        for product in list(resolver.get("products") or []):
            product = dict(product or {})
            if product.get("action") != "blocked_product":
                continue
            add_line(
                "product %s: %s"
                % (
                    product.get("product_candidate_key") or "-",
                    product.get("reason") or "blocked",
                )
            )

        if not lines:
            return _("No detailed blocker rows were returned by the resolver.")
        return "\n".join("- %s" % line for line in lines)

    def _materialization_lookup_reason(self, diagnostic):
        for lookup in list((diagnostic or {}).get("product_lookup") or []):
            lookup = dict(lookup or {})
            if lookup.get("exists"):
                continue
            if lookup.get("error"):
                return lookup.get("error")
            if lookup.get("skipped_reason"):
                return lookup.get("skipped_reason")
            if lookup.get("searched_model") and lookup.get("searched_id"):
                return "missing %s:%s" % (
                    lookup.get("searched_model"),
                    lookup.get("searched_id"),
                )
        return ""

    def _find_materialized_boms_from_plan(self, materialization_plan):
        if not materialization_plan:
            return self.env["mrp.bom"].browse()
        try:
            from odoo.addons.corr_engine_v2.engine_v2 import materialization_writer as _writer_mod
        except Exception:
            return self.env["mrp.bom"].browse()
        signatures = []
        for planned_bom in list(materialization_plan.get("boms") or []):
            planned_bom = dict(planned_bom or {})
            try:
                signature = _writer_mod._bom_signature(planned_bom)
            except Exception:
                signature = ""
            if signature:
                signatures.append(signature)
        if not signatures:
            return self.env["mrp.bom"].browse()
        return self.env["mrp.bom"].sudo().with_context(active_test=False).search(
            [("corr_engine_v2_signature", "in", list(dict.fromkeys(signatures)))],
            order="id desc",
        )

    def _find_fg_merge_bom_by_terminal_piece_components(self, product_tmpl, materialization_plan):
        if not product_tmpl or not materialization_plan:
            return False
        planned_products = {
            str(product.get("candidate_key") or ""): dict(product or {})
            for product in list(materialization_plan.get("products") or [])
        }
        planned_fg_bom = next(
            (
                dict(bom or {})
                for bom in list(materialization_plan.get("boms") or [])
                if str(bom.get("bom_role") or "").strip().lower() == "fg"
                or bool(dict(bom.get("metadata") or {}).get("fg_merge_bom"))
                or str(bom.get("bom_key") or "") == "bom:fg:multi_piece_merge"
            ),
            {},
        )
        if not planned_fg_bom:
            return False
        terminal_variant_ids = []
        ProductTemplate = self.env["product.template"].sudo().with_context(active_test=False)
        for line in list(planned_fg_bom.get("lines") or []):
            candidate_key = str(line.get("product_candidate_key") or "")
            planned_product = planned_products.get(candidate_key) or {}
            corr_sfg_key = str(planned_product.get("corr_sfg_key") or "").strip()
            if not corr_sfg_key:
                continue
            tmpl = ProductTemplate.search([("corr_sfg_key", "=", corr_sfg_key)], order="id desc", limit=1)
            variant = getattr(tmpl, "product_variant_id", False) if tmpl else False
            variant_id = getattr(variant, "id", False) if variant else False
            if variant_id:
                terminal_variant_ids.append(int(variant_id))
        terminal_variant_ids = list(dict.fromkeys(terminal_variant_ids))
        if not terminal_variant_ids:
            return False
        BomLine = self.env["mrp.bom.line"].sudo().with_context(active_test=False)
        lines = BomLine.search([
            ("product_id", "in", terminal_variant_ids),
        ])
        bom_ids = []
        for line in lines:
            bom = getattr(line, "bom_id", False)
            if not bom:
                continue
            bom_product_tmpl = getattr(bom, "product_tmpl_id", False)
            if not bom_product_tmpl or bom_product_tmpl.id != product_tmpl.id:
                continue
            bom_ids.append(bom.id)
        if not bom_ids:
            return False
        Bom = self.env["mrp.bom"].sudo().with_context(active_test=False)
        candidate_boms = Bom.search([("id", "in", list(dict.fromkeys(bom_ids)))], order="id desc")
        for bom in candidate_boms:
            line_product_ids = {
                getattr(getattr(line, "product_id", False), "id", False)
                for line in getattr(bom, "bom_line_ids", [])
                if getattr(line, "product_id", False)
            }
            if all(variant_id in line_product_ids for variant_id in terminal_variant_ids):
                return bom
        return candidate_boms[:1] if candidate_boms else False

    def _find_fg_merge_bom_from_materialization_plan(self, product_tmpl, materialization_plan):
        if not product_tmpl or not materialization_plan:
            return False
        planned_fg_bom = next(
            (
                dict(bom or {})
                for bom in list(materialization_plan.get("boms") or [])
                if str(bom.get("bom_role") or "").strip().lower() == "fg"
                or bool(dict(bom.get("metadata") or {}).get("fg_merge_bom"))
                or str(bom.get("bom_key") or "") == "bom:fg:multi_piece_merge"
            ),
            {},
        )
        if not planned_fg_bom:
            return False
        metadata = dict(planned_fg_bom.get("metadata") or {})
        corr_engine_v2_signature = ""
        corr_segment_key = str(planned_fg_bom.get("segment_key") or metadata.get("segment_key") or "fg_merge")
        try:
            from odoo.addons.corr_engine_v2.engine_v2 import materialization_writer as _writer_mod
            corr_engine_v2_signature = _writer_mod._bom_signature(planned_fg_bom)
        except Exception:
            corr_engine_v2_signature = ""
        corr_sfg_key = (
            planned_fg_bom.get("corr_sfg_key")
            or metadata.get("corr_sfg_key")
            or ""
        )
        Bom = self.env["mrp.bom"].sudo().with_context(active_test=False)
        bom_fields = set(getattr(Bom, "_fields", {}) or {})
        has_corr_segment_key = "corr_segment_key" in bom_fields
        search_domains = []
        if corr_engine_v2_signature:
            domain = [
                ("product_tmpl_id", "=", product_tmpl.id),
                ("corr_engine_v2_signature", "=", corr_engine_v2_signature),
            ]
            if corr_segment_key and has_corr_segment_key:
                domain.append(("corr_segment_key", "=", corr_segment_key))
            search_domains.append(domain)
        if corr_segment_key and has_corr_segment_key:
            search_domains.append(
                [
                    ("product_tmpl_id", "=", product_tmpl.id),
                    ("corr_segment_key", "=", corr_segment_key),
                ]
            )
        if corr_sfg_key:
            search_domains.append(
                [
                    ("product_tmpl_id", "=", product_tmpl.id),
                    ("corr_sfg_key", "=", corr_sfg_key),
                ]
            )
        search_domains.append([("product_tmpl_id", "=", product_tmpl.id)])
        for domain in search_domains:
            bom = Bom.search(domain, order="id desc", limit=1)
            if bom:
                return bom
        return False

    def _find_live_fg_merge_bom(self, product_tmpl, materialization_plan=None):
        if not product_tmpl:
            return False
        Bom = self.env["mrp.bom"].sudo().with_context(active_test=False)
        bom_fields = set(getattr(Bom, "_fields", {}) or {})
        merge_signature = ""
        if materialization_plan:
            planned_fg_bom = next(
                (
                    dict(bom or {})
                    for bom in list(materialization_plan.get("boms") or [])
                    if str(bom.get("bom_role") or "").strip().lower() == "fg"
                    or bool(dict(bom.get("metadata") or {}).get("fg_merge_bom"))
                    or str(bom.get("bom_key") or "") == "bom:fg:multi_piece_merge"
                ),
                {},
            )
            if planned_fg_bom:
                try:
                    from odoo.addons.corr_engine_v2.engine_v2 import materialization_writer as _writer_mod
                    merge_signature = _writer_mod._bom_signature(planned_fg_bom)
                except Exception:
                    merge_signature = ""
        search_domains = []
        if merge_signature and "corr_engine_v2_signature" in bom_fields:
            search_domains.append(
                [
                    ("product_tmpl_id", "=", product_tmpl.id),
                    ("corr_engine_v2_signature", "=", merge_signature),
                ]
            )
        if "corr_segment_key" in bom_fields:
            search_domains.append(
                [
                    ("product_tmpl_id", "=", product_tmpl.id),
                    ("corr_segment_key", "=", "fg_merge"),
                ]
            )
        search_domains.append([("product_tmpl_id", "=", product_tmpl.id)])
        for domain in search_domains:
            bom = Bom.search(domain, order="id desc", limit=1)
            if bom:
                return bom
        return False

    def action_create_bom_from_preview(self):
        self.ensure_one()
        self._ensure_bom_adapter_available()

        if not self.raw_json:
            raise UserError(
                _(
                    "No preview data available.\n"
                    "Please run the Engine V2 preview before creating a BoM."
                )
            )

        try:
            payload = json.loads(self.raw_json or "{}")
        except Exception:
            raise UserError(
                _(
                    "The preview payload could not be parsed.\n"
                    "Please rerun the Engine V2 preview before creating a BoM."
                )
            )

        multi_piece_payload = dict(payload.get("multi_piece") or {})
        metrics_extra = dict(((payload.get("metrics") or {}).get("extra") or {}))
        metrics_multi_piece = dict(metrics_extra.get("multi_piece") or {})
        is_multi_piece_preview = bool(
            multi_piece_payload.get("enabled") or metrics_multi_piece.get("enabled")
        )
        multi_piece_strategy = (
            multi_piece_payload.get("multi_piece_strategy")
            or multi_piece_payload.get("strategy")
            or metrics_multi_piece.get("multi_piece_strategy")
            or metrics_multi_piece.get("strategy")
            or "independent_routed_piece"
        )
        if is_multi_piece_preview and self._payload_has_custom_piece_manufacturing(payload):
            j3c_guard = (
                engine_v2_multi_piece_materialization.validate_phase_j3c_write_preconditions(payload)
                if engine_v2_multi_piece_materialization
                else {"passed": False, "blockers": ["multi_piece_materialization_module_unavailable"]}
            )
            payload["phase_j3c_piece_override_write_guard"] = j3c_guard
            try:
                self.raw_json = json.dumps(payload, indent=2, sort_keys=True)
            except Exception:
                pass
            if not j3c_guard.get("passed"):
                blocker_summary = "- %s" % "\n- ".join(
                    str(item) for item in list(j3c_guard.get("blockers") or [])
                )
                raise UserError(
                    _(
                        "Piece-specific BoM creation is currently allowed only for validated J3C independent multi-piece flows.\n"
                        "%s"
                    )
                    % blocker_summary
                )
        if (
            is_multi_piece_preview
            and str(multi_piece_strategy or "").strip().lower() == "shared_upstream_split"
        ):
            current_split_after_stage = (
                (self.multi_piece_split_after_stage_key or "").strip()
                or str(
                    getattr(self.v2_spec_id, "multi_piece_split_after_stage_key", "")
                    or ""
                ).strip()
                or str(payload.get("multi_piece_split_after_stage_key") or "").strip()
            )
            current_strategy = (
                str(
                    multi_piece_payload.get("multi_piece_strategy")
                    or multi_piece_payload.get("strategy")
                    or payload.get("multi_piece_strategy")
                    or ""
                ).strip()
                or (self.multi_piece_strategy or "").strip()
                or str(getattr(self.v2_spec_id, "multi_piece_strategy", "") or "").strip()
                or "shared_upstream_split"
            )
            multi_piece_payload["multi_piece_strategy"] = current_strategy
            multi_piece_payload["strategy"] = current_strategy
            if current_split_after_stage:
                multi_piece_payload["split_after_stage_key"] = current_split_after_stage
                payload["multi_piece_split_after_stage_key"] = current_split_after_stage
            payload["multi_piece_strategy"] = current_strategy
            payload["multi_piece"] = multi_piece_payload
            needs_phase_h_refresh = not (
                payload.get("phase_h_shared_upstream_split_dry_run")
                and payload.get("phase_h2_shared_upstream_materialization_dry_run")
            )
            h1_guard_existing = dict(
                (
                    dict(payload.get("phase_h_shared_upstream_split_dry_run") or {}).get("guard")
                    or {}
                )
            )
            if any(
                blocker in set(h1_guard_existing.get("blockers") or [])
                for blocker in (
                    "split_after_stage_required",
                    "split_after_stage_not_found_in_active_route",
                    "post_split_branch_stages_required",
                )
            ):
                needs_phase_h_refresh = True
            if needs_phase_h_refresh and engine_v2_multi_piece_materialization:
                h1_debug = engine_v2_multi_piece_materialization.build_phase_h_shared_upstream_split_debug_payload(
                    payload,
                )
                payload["phase_h_shared_upstream_split_dry_run"] = h1_debug
                payload["h1_guardrail_status"] = (
                    "allowed" if (h1_debug.get("guard") or {}).get("passed") else "blocked"
                )
                h2_debug = (
                    engine_v2_multi_piece_materialization.build_phase_h2_shared_upstream_materialization_debug_payload(
                        payload,
                        mode=(self.env.context or {}).get("corr_materialization_dry_run_mode")
                        or "stockable",
                        paper_resolver=self._resolve_g1_piece_paper_variant,
                    )
                )
                materialization_plan = h2_debug.get("materialization_plan") or {}
                if (
                    engine_v2_materialization_resolver
                    and materialization_plan
                    and (h2_debug.get("guard") or {}).get("passed")
                ):
                    h2_debug["resolver_dry_run"] = (
                        engine_v2_materialization_resolver.resolve_materialization_plan_dry_run(
                            self.env,
                            materialization_plan,
                            company_id=getattr(self.env.company, "id", None),
                            fg_product_tmpl_id=getattr(self._active_fg_product_tmpl(), "id", None),
                        )
                    )
                    h2_debug["candidate_summary"] = (
                        engine_v2_multi_piece_materialization.build_h2_candidate_summary(
                            materialization_plan,
                            h2_debug.get("resolver_dry_run") or {},
                        )
                    )
                payload["phase_h2_shared_upstream_materialization_dry_run"] = h2_debug
                if h2_debug.get("shared_upstream_flow"):
                    payload["shared_upstream_flow"] = h2_debug.get("shared_upstream_flow")
                try:
                    self.raw_json = json.dumps(payload, indent=2, sort_keys=True)
                except Exception:
                    pass
            h3_guard = (
                engine_v2_multi_piece_materialization.validate_phase_h3_write_preconditions(payload)
                if engine_v2_multi_piece_materialization
                else {"passed": False, "blockers": ["multi_piece_materialization_module_unavailable"]}
            )
            payload["phase_h3_shared_upstream_write_guard"] = h3_guard
            if not h3_guard.get("passed"):
                h2_debug = dict(payload.get("phase_h2_shared_upstream_materialization_dry_run") or {})
                blocker_summary = (
                    self._format_materialization_blocker_summary(h2_debug)
                    if h2_debug and (h2_debug.get("resolver_dry_run") or {}).get("blockers")
                    else "- %s" % "\n- ".join(str(item) for item in list(h3_guard.get("blockers") or []))
                )
                raise UserError(
                    _(
                        "Shared Upstream Split cannot be materialized because H3 write preconditions failed.\n"
                        "%s\n"
                        "Please inspect Raw JSON for full diagnostics."
                    )
                    % blocker_summary
                )

        bom_backend_selected = (self.bom_backend or "legacy").strip().lower()

        bom_struct = (payload.get("bom_struct") or {}) if isinstance(payload, dict) else {}
        components = bom_struct.get("components") or []
        if not is_multi_piece_preview and bom_backend_selected == "legacy" and not components:
            raise UserError(
                _(
                    "The Engine V2 preview does not contain any BoM components.\n"
                    "Please check the configuration and rerun the preview."
                )
            )

        spec = self._active_spec_record()
        if not spec:
            raise UserError(_("Please select a legacy spec or an Engine V2 spec before creating a BoM."))
        plant = self.plant_id
        if not plant:
            raise UserError(_("Please select a plant before creating a BoM."))

        spec_id_preview = payload.get("spec_id")
        spec_model_preview = payload.get("spec_model")
        plant_id_preview = payload.get("plant_id")
        order_qty_preview = payload.get("order_qty")

        if spec_model_preview and spec_model_preview != getattr(spec, "_name", ""):
            raise UserError(
                _(
                    "The preview was generated for a different spec type.\n"
                    "Please rerun the Engine V2 preview to keep BoM creation consistent."
                )
            )
        if spec_id_preview and int(spec_id_preview) != spec.id:
            raise UserError(
                _(
                    "The preview was generated for a different spec.\n"
                    "Please rerun the Engine V2 preview to keep BoM creation consistent."
                )
            )
        if plant_id_preview and int(plant_id_preview) != plant.id:
            raise UserError(
                _(
                    "The preview was generated for a different plant.\n"
                    "Please rerun the Engine V2 preview to keep BoM creation consistent."
                )
            )

        try:
            qty_preview = float(order_qty_preview or 0.0)
        except Exception:
            qty_preview = 0.0
        try:
            qty_current = float(self.order_qty or 0.0)
        except Exception:
            qty_current = 0.0
        if qty_preview > 0.0 and abs(qty_preview - qty_current) > 1e-6:
            raise UserError(
                _(
                    "The order quantity has changed since the preview was generated.\n"
                    "Please rerun the Engine V2 preview before creating a BoM."
                )
            )

        product_tmpl = self._active_fg_product_tmpl()
        if not product_tmpl:
            raise UserError(
                _(
                    "The selected spec is not linked to a finished product.\n"
                    "Please set a finished product on the spec before creating a BoM."
                )
            )
        fg_product = product_tmpl.product_variant_id
        if not fg_product:
            raise UserError(
                _(
                    "The finished product does not have a product variant.\n"
                    "Please check the product configuration."
                )
            )

        if not self.preview_is_current:
            raise UserError(
                _(
                    "The current preview is stale.\n"
                    "Please run the Engine V2 preview again before creating a BoM."
                )
            )

        writer_ctx = dict(self.env.context or {})
        writer_ctx["corr_materialization_writer_enabled"] = True
        writer_ctx["corr_materialization_fg_product_tmpl_id"] = product_tmpl.id
        if is_multi_piece_preview:
            writer_ctx["corr_materialization_writer_strategy"] = "materialized_multi_level"
        writer_self = self.with_context(writer_ctx)
        if is_multi_piece_preview:
            if str(multi_piece_strategy or "").strip().lower() == "shared_upstream_split":
                materialization_debug = dict(
                    payload.get("phase_h2_shared_upstream_materialization_dry_run") or {}
                )
                multi_piece_materialization_phase_key = "phase_h2_shared_upstream_materialization_dry_run"
            else:
                payload = self._payload_with_piece_specific_rm_requirements(payload)
                materialization_mode = (
                    (self.env.context or {}).get("corr_materialization_dry_run_mode")
                    or "stockable"
                )
                g2_debug_candidate = {}
                if engine_v2_multi_piece_materialization:
                    g2_debug_candidate = (
                        engine_v2_multi_piece_materialization.build_phase_g2_routed_piece_materialization_debug_payload(
                            payload,
                            mode=materialization_mode,
                            paper_resolver=self._resolve_g1_piece_paper_variant,
                        )
                    )
                    materialization_plan = g2_debug_candidate.get("materialization_plan") or {}
                    if (
                        engine_v2_materialization_resolver
                        and materialization_plan
                        and (g2_debug_candidate.get("guard") or {}).get("passed")
                    ):
                        g2_debug_candidate["resolver_dry_run"] = (
                            engine_v2_materialization_resolver.resolve_materialization_plan_dry_run(
                                self.env,
                                materialization_plan,
                                company_id=getattr(self.env.company, "id", None),
                                fg_product_tmpl_id=getattr(product_tmpl, "id", None),
                            )
                        )
                    payload["phase_g2_routed_piece_materialization_dry_run"] = g2_debug_candidate
                g2_guard = dict(g2_debug_candidate.get("guard") or {})
                g2_segmentation = dict(g2_debug_candidate.get("route_segmentation") or {})
                if (
                    g2_debug_candidate
                    and g2_guard.get("passed")
                    and g2_segmentation.get("merge_stage_found")
                    and g2_segmentation.get("branch_stage_keys")
                ):
                    materialization_debug = g2_debug_candidate
                    multi_piece_materialization_phase_key = "phase_g2_routed_piece_materialization_dry_run"
                else:
                    materialization_debug = dict(
                        payload.get("phase_g1_multi_piece_materialization_dry_run") or {}
                    )
                    multi_piece_materialization_phase_key = "phase_g1_multi_piece_materialization_dry_run"
            if not materialization_debug:
                raise UserError(
                    _(
                        "The multi-piece materialization plan is missing.\n"
                        "Please rerun the Engine V2 preview before creating the multi-piece BoM."
                    )
                )
        else:
            materialization_debug = dict(payload.get("phase7b_materialization_dry_run") or {})
            multi_piece_materialization_phase_key = ""
        materialization_execution_diagnostics = {
            "context_gate_enabled": False,
            "system_gate_enabled": False,
            "effective_execution_allowed": False,
            "selected_writer_strategy": "",
            "fallback_reason_if_legacy_writer_used": "materialization_writer_unavailable",
        }
        if engine_v2_materialization_writer and materialization_debug:
            materialization_plan = materialization_debug.get("materialization_plan") or {}
            resolver_dry_run = materialization_debug.get("resolver_dry_run") or {}
            if engine_v2_materialization_resolver and materialization_plan:
                resolver_dry_run = engine_v2_materialization_resolver.resolve_materialization_plan_dry_run(
                    writer_self.env,
                    materialization_plan,
                    company_id=getattr(writer_self.env.company, "id", None),
                    fg_product_tmpl_id=product_tmpl.id,
                )
                materialization_debug["resolver_dry_run"] = resolver_dry_run
            writer_config = engine_v2_materialization_writer.writer_config_from_env(writer_self.env)
            writer_strategy = engine_v2_materialization_writer.build_materialization_writer_strategy(
                resolver_dry_run,
                materialization_plan=materialization_plan,
                config=writer_config,
            )
            executor_diagnostics = engine_v2_materialization_writer.build_guarded_executor_diagnostics(
                resolver_dry_run,
                materialization_plan=materialization_plan,
                config=writer_config,
            )
            selected_writer_strategy = writer_strategy.get("strategy") or ""
            materialization_execution_diagnostics = {
                "context_gate_enabled": bool(writer_config.context_flag_enabled),
                "system_gate_enabled": bool(writer_config.system_parameter_enabled),
                "effective_execution_allowed": bool(
                    executor_diagnostics.get("would_execute_materialized_writer")
                ),
                "selected_writer_strategy": selected_writer_strategy,
                "fallback_reason_if_legacy_writer_used": "",
                "guard": executor_diagnostics.get("guard") or {},
                "executor_phase": executor_diagnostics.get("phase"),
                "input_has_blockers": bool(executor_diagnostics.get("input_has_blockers")),
                "blockers": list(executor_diagnostics.get("blockers") or []),
            }
            materialization_debug["guarded_orm_executor"] = executor_diagnostics
            materialization_debug["orm_writer_execution_diagnostics"] = executor_diagnostics
            materialization_debug["create_bom_execution_gate_diagnostics"] = (
                materialization_execution_diagnostics
            )
            if is_multi_piece_preview:
                payload[multi_piece_materialization_phase_key] = materialization_debug
            else:
                payload["phase7b_materialization_dry_run"] = materialization_debug

            if selected_writer_strategy == "materialized_multi_level":
                if materialization_execution_diagnostics["effective_execution_allowed"]:
                    execution_result = engine_v2_materialization_writer.execute_materialization_writer(
                        writer_self.env,
                        resolver_dry_run,
                        materialization_plan=materialization_plan,
                        config=writer_config,
                    )
                    materialization_debug["orm_writer_execution_result"] = execution_result
                    if (
                        is_multi_piece_preview
                        and str(multi_piece_strategy or "").strip().lower() == "shared_upstream_split"
                    ):
                        payload["phase_h3_shared_upstream_write_result"] = {
                            "phase": "H3",
                            "strategy": "shared_upstream_split",
                            "executed": bool(execution_result.get("executed")),
                            "created_products": execution_result.get("created_product_templates") or {},
                            "reused_products": execution_result.get("reused_products") or {},
                            "created_boms": execution_result.get("created_boms") or {},
                            "reused_boms": execution_result.get("reused_boms") or {},
                            "bom_execution_order": execution_result.get("bom_execution_order") or [],
                            "blockers": [],
                            "no_mo_creation": bool(execution_result.get("no_mo_creation")),
                            "split_node_logical_only": True,
                        }
                    if is_multi_piece_preview:
                        payload[multi_piece_materialization_phase_key] = materialization_debug
                    else:
                        payload["phase7b_materialization_dry_run"] = materialization_debug
                    try:
                        self.raw_json = json.dumps(payload, indent=2, sort_keys=True)
                    except Exception:
                        pass

                    materialized_bom_id = False
                    bom_order = list(execution_result.get("bom_execution_order") or [])
                    for bom_info in reversed(bom_order):
                        if bom_info.get("bom_role") == "fg" and bom_info.get("id"):
                            materialized_bom_id = bom_info.get("id")
                            break
                    if not materialized_bom_id:
                        for bucket_name in ("created_boms", "reused_boms"):
                            bucket = dict(execution_result.get(bucket_name) or {})
                            for candidate_key, bom_info in bucket.items():
                                if (
                                    str(bom_info.get("bom_role") or "") == "fg"
                                    and bom_info.get("id")
                                ):
                                    materialized_bom_id = bom_info.get("id")
                                    materialization_execution_diagnostics[
                                        "fg_merge_bom_result_source"
                                    ] = "%s:%s" % (bucket_name, candidate_key)
                                    break
                            if materialized_bom_id:
                                break
                    if not materialized_bom_id:
                        for bucket_name in ("created_boms", "reused_boms"):
                            bucket = dict(execution_result.get(bucket_name) or {})
                            bom_info = bucket.get("bom:fg:multi_piece_merge") or {}
                            if bom_info.get("id"):
                                materialized_bom_id = bom_info.get("id")
                                materialization_execution_diagnostics[
                                    "fg_merge_bom_result_source"
                                ] = "%s:%s" % (bucket_name, "bom:fg:multi_piece_merge")
                                break
                    if not materialized_bom_id and bom_order:
                        materialized_bom_id = bom_order[-1].get("id")
                    if not materialized_bom_id:
                        planned_fg_bom = writer_self._find_fg_merge_bom_from_materialization_plan(
                            product_tmpl,
                            materialization_plan,
                        )
                        if planned_fg_bom:
                            materialized_bom_id = planned_fg_bom.id
                            materialization_execution_diagnostics[
                                "fg_merge_bom_result_source"
                            ] = "search:product_tmpl_id+corr_engine_v2_signature"
                    if not materialized_bom_id:
                        live_fg_merge_bom = writer_self._find_live_fg_merge_bom(
                            product_tmpl,
                            materialization_plan,
                        )
                        if live_fg_merge_bom:
                            materialized_bom_id = live_fg_merge_bom.id
                            materialization_execution_diagnostics[
                                "fg_merge_bom_result_source"
                            ] = "search:live_fg_merge_bom"
                    if materialized_bom_id:
                        return {
                            "type": "ir.actions.act_window",
                            "name": _("Engine V2 Materialized BoM"),
                            "res_model": "mrp.bom",
                            "view_mode": "form",
                            "res_id": materialized_bom_id,
                            "target": "current",
                        }
                    materialization_execution_diagnostics[
                        "fallback_reason_if_legacy_writer_used"
                    ] = "materialized_writer_executed_without_bom_result"
                    if is_multi_piece_preview:
                        result_bom_ids = []
                        seen_bom_ids = set()
                        for bom_info in list(execution_result.get("bom_execution_order") or []):
                            bom_id = bom_info.get("id")
                            if bom_id and bom_id not in seen_bom_ids:
                                seen_bom_ids.add(bom_id)
                                result_bom_ids.append(bom_id)
                        for bucket_name in ("created_boms", "reused_boms"):
                            for bom_info in dict(execution_result.get(bucket_name) or {}).values():
                                bom_id = bom_info.get("id")
                                if bom_id and bom_id not in seen_bom_ids:
                                    seen_bom_ids.add(bom_id)
                                    result_bom_ids.append(bom_id)
                        Bom = self.env["mrp.bom"].sudo()
                        if result_bom_ids:
                            result_boms = Bom.browse(result_bom_ids).exists()
                            fg_product_boms = result_boms.filtered(lambda b: getattr(b, "product_tmpl_id", False) and b.product_tmpl_id.id == product_tmpl.id)
                            if len(fg_product_boms) == 1:
                                materialization_execution_diagnostics[
                                    "fg_merge_bom_result_source"
                                ] = "fallback:single_fg_product_bom_from_execution_ids"
                                return {
                                    "type": "ir.actions.act_window",
                                    "name": _("Engine V2 Materialized BoM"),
                                    "res_model": "mrp.bom",
                                    "view_mode": "form",
                                    "res_id": fg_product_boms.id,
                                    "target": "current",
                                }
                            if fg_product_boms:
                                materialization_execution_diagnostics[
                                    "fg_merge_bom_result_source"
                                ] = "fallback:fg_product_boms_from_execution_ids"
                                return {
                                    "type": "ir.actions.act_window",
                                    "name": _("Engine V2 Materialized BoMs"),
                                    "res_model": "mrp.bom",
                                    "view_mode": "list,form",
                                    "domain": [("id", "in", fg_product_boms.ids)],
                                    "target": "current",
                                }
                            latest_result_bom = result_boms[:1]
                            if latest_result_bom:
                                materialization_execution_diagnostics[
                                    "fg_merge_bom_result_source"
                                ] = "fallback:latest_execution_bom"
                                return {
                                    "type": "ir.actions.act_window",
                                    "name": _("Engine V2 Materialized BoM"),
                                    "res_model": "mrp.bom",
                                    "view_mode": "form",
                                    "res_id": latest_result_bom.id,
                                    "target": "current",
                                }
                            materialization_execution_diagnostics[
                                "fg_merge_bom_result_source"
                            ] = "fallback:all_execution_bom_ids"
                            return {
                                "type": "ir.actions.act_window",
                                "name": _("Engine V2 Materialized BoMs"),
                                "res_model": "mrp.bom",
                                "view_mode": "list,form",
                                "domain": [("id", "in", result_bom_ids)],
                                "target": "current",
                            }
                        planned_boms = writer_self._find_materialized_boms_from_plan(materialization_plan)
                        if planned_boms:
                            fg_planned_boms = planned_boms.filtered(lambda b: getattr(b, "product_tmpl_id", False) and b.product_tmpl_id.id == product_tmpl.id)
                            if len(fg_planned_boms) == 1:
                                materialization_execution_diagnostics[
                                    "fg_merge_bom_result_source"
                                ] = "fallback:single_fg_product_bom_from_plan_signatures"
                                return {
                                    "type": "ir.actions.act_window",
                                    "name": _("Engine V2 Materialized BoM"),
                                    "res_model": "mrp.bom",
                                    "view_mode": "form",
                                    "res_id": fg_planned_boms.id,
                                    "target": "current",
                                }
                            if fg_planned_boms:
                                materialization_execution_diagnostics[
                                    "fg_merge_bom_result_source"
                                ] = "fallback:fg_product_boms_from_plan_signatures"
                                return {
                                    "type": "ir.actions.act_window",
                                    "name": _("Engine V2 Materialized BoMs"),
                                    "res_model": "mrp.bom",
                                    "view_mode": "list,form",
                                    "domain": [("id", "in", fg_planned_boms.ids)],
                                    "target": "current",
                                }
                            latest_planned_bom = planned_boms[:1]
                            if latest_planned_bom:
                                materialization_execution_diagnostics[
                                    "fg_merge_bom_result_source"
                                ] = "fallback:latest_materialized_bom_from_plan_signatures"
                                return {
                                    "type": "ir.actions.act_window",
                                    "name": _("Engine V2 Materialized BoM"),
                                    "res_model": "mrp.bom",
                                    "view_mode": "form",
                                    "res_id": latest_planned_bom.id,
                                    "target": "current",
                                }
                            materialization_execution_diagnostics[
                                "fg_merge_bom_result_source"
                            ] = "fallback:all_materialized_boms_from_plan_signatures"
                            return {
                                "type": "ir.actions.act_window",
                                "name": _("Engine V2 Materialized BoMs"),
                                "res_model": "mrp.bom",
                                "view_mode": "list,form",
                                "domain": [("id", "in", planned_boms.ids)],
                                "target": "current",
                            }
                        fallback_domain = [("product_tmpl_id", "=", product_tmpl.id)]
                        component_match_bom = writer_self._find_fg_merge_bom_by_terminal_piece_components(
                            product_tmpl,
                            materialization_plan,
                        )
                        if component_match_bom:
                            materialization_execution_diagnostics[
                                "fg_merge_bom_result_source"
                            ] = "fallback:terminal_piece_component_match"
                            return {
                                "type": "ir.actions.act_window",
                                "name": _("Engine V2 Materialized BoM"),
                                "res_model": "mrp.bom",
                                "view_mode": "form",
                                "res_id": component_match_bom.id,
                                "target": "current",
                            }
                        live_fg_merge_bom = writer_self._find_live_fg_merge_bom(
                            product_tmpl,
                            materialization_plan,
                        )
                        if live_fg_merge_bom:
                            materialization_execution_diagnostics[
                                "fg_merge_bom_result_source"
                            ] = "fallback:live_fg_merge_bom"
                            return {
                                "type": "ir.actions.act_window",
                                "name": _("Engine V2 Materialized BoM"),
                                "res_model": "mrp.bom",
                                "view_mode": "form",
                                "res_id": live_fg_merge_bom.id,
                                "target": "current",
                            }
                        fallback_bom = Bom.search(
                            fallback_domain,
                            order="id desc",
                            limit=1,
                        )
                        if fallback_bom:
                            materialization_execution_diagnostics[
                                "fg_merge_bom_result_source"
                            ] = "fallback:latest_bom_for_fg_product_tmpl"
                            return {
                                "type": "ir.actions.act_window",
                                "name": _("Engine V2 Materialized BoM"),
                                "res_model": "mrp.bom",
                                "view_mode": "form",
                                "res_id": fallback_bom.id,
                                "target": "current",
                            }
                        materialization_execution_diagnostics[
                            "fg_merge_bom_result_source"
                        ] = "fallback:no_bom_found_after_writer_execution"
                        materialization_execution_diagnostics[
                            "execution_result_summary"
                        ] = {
                            "bom_execution_order": list(execution_result.get("bom_execution_order") or []),
                            "created_boms": dict(execution_result.get("created_boms") or {}),
                            "reused_boms": dict(execution_result.get("reused_boms") or {}),
                            "product_tmpl_id": product_tmpl.id,
                        }
                        materialization_debug["create_bom_execution_gate_diagnostics"] = (
                            materialization_execution_diagnostics
                        )
                        if is_multi_piece_preview:
                            payload[multi_piece_materialization_phase_key] = materialization_debug
                        else:
                            payload["phase7b_materialization_dry_run"] = materialization_debug
                        try:
                            self.raw_json = json.dumps(payload, indent=2, sort_keys=True)
                        except Exception:
                            pass
                        raise UserError(
                            _(
                                "Materialization writer executed, but no materialized BoM could be located for opening.\n"
                                "Raw JSON has been updated with execution diagnostics.\n"
                                "Check create_bom_execution_gate_diagnostics.execution_result_summary.\n"
                                "\n"
                                "Execution summary:\n"
                                "- plan boms: %(plan_bom_count)s\n"
                                "- created bom keys: %(created_bom_keys)s\n"
                                "- reused bom keys: %(reused_bom_keys)s\n"
                                "- bom execution order count: %(bom_execution_count)s"
                            )
                            % {
                                "plan_bom_count": len(list((materialization_plan or {}).get("boms") or [])),
                                "created_bom_keys": ", ".join(
                                    sorted(dict(execution_result.get("created_boms") or {}).keys())
                                )
                                or "-",
                                "reused_bom_keys": ", ".join(
                                    sorted(dict(execution_result.get("reused_boms") or {}).keys())
                                )
                                or "-",
                                "bom_execution_count": len(list(execution_result.get("bom_execution_order") or [])),
                            }
                        )
                else:
                    materialization_execution_diagnostics[
                        "fallback_reason_if_legacy_writer_used"
                    ] = executor_diagnostics.get("disabled_reason") or "materialized_writer_not_allowed"
            else:
                materialization_execution_diagnostics[
                    "fallback_reason_if_legacy_writer_used"
                ] = (
                    writer_strategy.get("reason")
                    or "selected_writer_strategy_is_legacy_single_bom_flattened"
                )

        if materialization_debug:
            materialization_debug["create_bom_execution_gate_diagnostics"] = (
                materialization_execution_diagnostics
            )
            if is_multi_piece_preview:
                payload[multi_piece_materialization_phase_key] = materialization_debug
            else:
                payload["phase7b_materialization_dry_run"] = materialization_debug

        materialization_blockers = []
        try:
            materialization_blockers = list(
                (materialization_debug.get("resolver_dry_run") or {}).get("blockers") or []
            )
        except Exception:
            materialization_blockers = []
        if (
            not is_multi_piece_preview
            and materialization_blockers
            and str(bom_backend_selected or "").strip().lower() == "stagecontext_shadow"
        ):
            try:
                self.raw_json = json.dumps(payload, indent=2, sort_keys=True)
            except Exception:
                pass
            blocker_summary = self._format_materialization_blocker_summary(
                materialization_debug
            )
            raise UserError(
                _(
                    "StageContext materialized BoM creation is blocked by resolver diagnostics.\n"
                    "%s\n"
                    "The flat single-BoM fallback was not used because it would hide the stage ownership problem."
                )
                % blocker_summary
            )

        if (
            not is_multi_piece_preview
            and str(bom_backend_selected or "").strip().lower() == "stagecontext_shadow"
            and materialization_debug
            and materialization_execution_diagnostics.get("selected_writer_strategy") == "materialized_multi_level"
            and not materialization_execution_diagnostics.get("effective_execution_allowed")
        ):
            try:
                self.raw_json = json.dumps(payload, indent=2, sort_keys=True)
            except Exception:
                pass
            disabled_reason = (
                materialization_execution_diagnostics.get("fallback_reason_if_legacy_writer_used")
                or "materialized_writer_not_allowed"
            )
            raise UserError(
                _(
                    "StageContext multi-level BoM creation is not enabled.\n"
                    "The preview contains materialized SFG boundaries, so the flat fallback was not used because it would hide stages such as Top Sheet Cut.\n"
                    "Enable the materialization writer system parameter, then rerun Create BoM from Preview.\n"
                    "Reason: %s"
                )
                % disabled_reason
            )

        if is_multi_piece_preview:
            try:
                self.raw_json = json.dumps(payload, indent=2, sort_keys=True)
            except Exception:
                pass
            if materialization_blockers:
                blocker_summary = self._format_materialization_blocker_summary(
                    materialization_debug
                )
                raise UserError(
                    _(
                        "Multi-piece materialization is blocked by resolver diagnostics.\n"
                        "%s\n"
                        "Please inspect Raw JSON for full component/product diagnostics."
                    )
                    % blocker_summary
                )
            disabled_reason = (
                materialization_execution_diagnostics.get("fallback_reason_if_legacy_writer_used")
                or "materialized_writer_not_allowed"
            )
            raise UserError(
                _(
                    "Multi-piece write is not enabled by the existing Phase 7B gates.\n"
                    "Enable the materialization writer context flag and system parameter, "
                    "then rerun Create BoM from Preview.\n"
                    "Reason: %s"
                )
                % disabled_reason
            )

        # Determine whether a BoM already exists for this Engine V2
        # signature so we can provide clear feedback to the user.
        existing_bom = False
        try:
            signature = engine_v2_bom_adapter._compute_signature_from_preview_payload(payload)
            Bom = self.env["mrp.bom"].sudo()
            if "corr_engine_v2_signature" in getattr(Bom, "_fields", {}):
                existing_bom = Bom.search(
                    [
                        ("product_tmpl_id", "=", product_tmpl.id),
                        ("corr_engine_v2_signature", "=", signature),
                    ],
                    limit=1,
                )
        except Exception:
            # Signature computation is best-effort for feedback only.
            existing_bom = False

        bom_creation_diagnostics = {}
        bom = engine_v2_bom_adapter.create_bom_from_selected_preview_payload(
            writer_self.with_context(
                engine_v2_selected_slitting_options_json=self.selected_slitting_options_json or ""
            ).env,
            payload,
            fg_product,
            bom_backend=bom_backend_selected,
            diagnostics_sink=bom_creation_diagnostics,
        )

        if bom_creation_diagnostics or materialization_debug:
            try:
                payload["bom_creation_diagnostics"] = {
                    **dict(payload.get("bom_creation_diagnostics") or {}),
                    **bom_creation_diagnostics,
                    "bom_backend_selected": bom_backend_selected,
                    "bom_id": bom.id,
                    "context_gate_enabled": materialization_execution_diagnostics.get(
                        "context_gate_enabled"
                    ),
                    "system_gate_enabled": materialization_execution_diagnostics.get(
                        "system_gate_enabled"
                    ),
                    "effective_execution_allowed": materialization_execution_diagnostics.get(
                        "effective_execution_allowed"
                    ),
                    "selected_writer_strategy": materialization_execution_diagnostics.get(
                        "selected_writer_strategy"
                    ),
                    "fallback_reason_if_legacy_writer_used": materialization_execution_diagnostics.get(
                        "fallback_reason_if_legacy_writer_used"
                    ),
                }
                self.raw_json = json.dumps(payload, indent=2, sort_keys=True)
            except Exception:
                pass

        created = not bool(existing_bom)
        action_name = (
            _("Engine V2 BoM (created from preview)")
            if created
            else _("Engine V2 BoM (reused from preview)")
        )

        return {
            "type": "ir.actions.act_window",
            "name": action_name,
            "res_model": "mrp.bom",
            "view_mode": "form",
            "res_id": bom.id,
            "target": "current",
        }

    def _render_summary_html(self, result, qgraph, order_qty, plant_param, warnings_text=None, costing=None):
        summary = {
            "order_qty": _summary_float(order_qty, 0.0),
            "routing_stage_count": len(self._summary_route_stage_codes()),
        }
        context = self._build_summary_render_context(
            summary=summary,
            plant_name=getattr(plant_param, "name", "") or "",
            order_qty=order_qty,
            costing=costing,
        )
        payload = self._build_summary_render_payload(
            summary=summary,
            result=result,
            qgraph=qgraph,
            costing=costing,
        )
        sections = _build_business_summary_sections(payload, context)
        return _render_business_summary_html(sections, warnings_present=bool(warnings_text))

        metrics = getattr(result, "metrics", None)
        extra = getattr(metrics, "extra", {}) or {}
        layout = extra.get("layout") or {}

        m2_per_box = getattr(metrics, "m2_per_box", 0.0) if metrics else 0.0
        kg_per_box = getattr(metrics, "kg_per_box", 0.0) if metrics else 0.0
        blanks_per_sheet = int(extra.get("layout_blanks_per_sheet", 0) or 0)
        boxes_per_sheet = float(extra.get("boxes_per_sheet", 0.0) or 0.0)

        sheets_required = 0
        try:
            sheet_node = qgraph.nodes.get("sheet")
            if sheet_node:
                sheets_required = int(sheet_node.qty or 0)
        except Exception:
            pass
        slit_sheets_produced = 0
        try:
            blanks_node = qgraph.nodes.get("blanks")
            if blanks_node:
                slit_sheets_produced = int(blanks_node.qty or 0)
        except Exception:
            pass

        plant_name = getattr(plant_param, "name", "") or ""
        spec_rec = self._active_spec_record()
        spec_name = getattr(spec_rec, "name", "") or ""
        style = getattr(spec_rec, "style", "") or ""
        if not style:
            style_rec = getattr(spec_rec, "style_id", None)
            style = getattr(style_rec, "code", "") or getattr(style_rec, "name", "") or ""

        rows = []
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Spec"), spec_name))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Style"), style))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Plant"), plant_name))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Order Qty"), order_qty))
        rows.append("<tr><th>%s</th><td>%.6f</td></tr>" % (_("mÂ² per box"), m2_per_box))
        rows.append("<tr><th>%s</th><td>%.6f</td></tr>" % (_("kg per box"), kg_per_box))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Blanks per sheet"), blanks_per_sheet))
        rows.append("<tr><th>%s</th><td>%.3f</td></tr>" % (_("Boxes per sheet"), boxes_per_sheet))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Parent sheets / runs required"), sheets_required))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Slit sheets / blanks produced"), slit_sheets_produced))

        # Layout orientation and basic info
        orientation = layout.get("orientation") or ""
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Layout orientation"), orientation))

        # Material and operation costing (preview-only diagnostics).
        if costing:
            cost_source = (costing.get("cost_source") or "standard_price").strip()
            effective_sources = costing.get("effective_cost_sources") or []
            try:
                total_per_fg = float(costing.get("total_cost_per_fg", 0.0) or 0.0)
            except Exception:
                total_per_fg = 0.0
            try:
                total_for_order = float(costing.get("total_cost_for_order", 0.0) or 0.0)
            except Exception:
                total_for_order = 0.0
            try:
                total_operation = float(costing.get("total_operation_cost", 0.0) or 0.0)
            except Exception:
                total_operation = 0.0
            try:
                total_production = float(
                    costing.get("total_production_cost", total_for_order + total_operation)
                    or (total_for_order + total_operation)
                )
            except Exception:
                total_production = total_for_order + total_operation
            currency = costing.get("currency") or {}
            symbol = currency.get("symbol") or ""

            def _fmt(amount):
                if symbol:
                    return "%s %.6f" % (symbol, amount)
                return "%.6f" % amount

            rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Cost source (requested)"), cost_source or "standard_price"))
            if effective_sources:
                rows.append(
                    "<tr><th>%s</th><td>%s</td></tr>"
                    % (_("Cost source (effective)"), ", ".join(sorted(set(effective_sources))))
                )
            rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Material cost per unit"), _fmt(total_per_fg)))
            rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Material cost for order"), _fmt(total_for_order)))
            rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Operation cost for order"), _fmt(total_operation)))
            rows.append(
                "<tr><th>%s</th><td>%s</td></tr>"
                % (_("Total production cost for order"), _fmt(total_production))
            )

        html = ["<h3>%s</h3>" % _("Engine V2 Summary")]
        if warnings_text:
            html.append(
                "<div class='alert alert-warning' role='alert'>%s</div>"
                % _("Warnings are present; see the Warnings tab for details.")
            )
        html += [
            "<table class='table table-sm table-striped'>",
            "<tbody>",
            *rows,
            "</tbody>",
            "</table>",
        ]
        return "\n".join(html)

    def _display_rm_quantity(self, value, uom="", role="", metadata=None, product_key=""):
        """Return display-only quantity/UoM for RM Breakdown rows."""

        try:
            qty = float(value or 0.0)
        except Exception:
            qty = 0.0
        uom_text = str(uom or "")
        metadata = dict(metadata or {})
        candidates = {
            str(role or "").strip().lower(),
            str(product_key or "").strip().lower(),
            str(metadata.get("kind") or "").strip().lower(),
            str(metadata.get("role") or "").strip().lower(),
            str(metadata.get("role_code") or "").strip().lower(),
            str(metadata.get("semantic_role") or "").strip().lower(),
            str(metadata.get("product_key") or "").strip().lower(),
            str(metadata.get("material_key") or "").strip().lower(),
        }
        uom_norm = uom_text.strip().lower()
        gram_roles = {"wire", "stitching_wire", "rm_wire", "ink", "printing_ink", "rm_ink"}
        if candidates.intersection(gram_roles) and uom_norm in {
            "kg",
            "kgs",
            "kilogram",
            "kilograms",
        }:
            return qty * 1000.0, "g", 4
        return qty, uom_text, 3

    def _consumable_profile_key_for_display(self, role="", product_key="", metadata=None):
        metadata = dict(metadata or {})
        probe = " ".join(
            str(value or "").strip().lower()
            for value in (
                role,
                product_key,
                metadata.get("kind"),
                metadata.get("role"),
                metadata.get("role_code"),
                metadata.get("semantic_role"),
                metadata.get("product_key"),
                metadata.get("material_key"),
            )
            if value
        )
        if "ink" in probe or "print" in probe:
            return "ink"
        if "wire" in probe or "stitch" in probe:
            return "wire"
        if "tape" in probe:
            return "tape"
        if "film" in probe or "lamination" in probe:
            return "lamination"
        if "adhesive" in probe or "glue" in probe or "gum" in probe:
            if "corrugation" in probe:
                return "adhesive_corrugation"
            if "fg" in probe or "joint" in probe or "seam" in probe:
                return "adhesive_fg"
            return "adhesive_board"
        return ""

    def _display_rm_product_label(self, product_key="", role="", metadata=None, profile_products=None):
        metadata = dict(metadata or {})
        profile_products = profile_products or {}

        resolved_name = (
            metadata.get("resolution_product_name")
            or metadata.get("product_name")
            or (metadata.get("resolution", {}) or {}).get("product_name")
        )
        if resolved_name:
            return str(resolved_name)

        is_consumable_like = False
        try:
            is_consumable_like = bool(
                is_consumable_role
                and is_consumable_role(role, product_key=product_key, metadata=metadata)
            )
        except Exception:
            is_consumable_like = False
        if is_consumable_like:
            profile_key = self._consumable_profile_key_for_display(
                role=role,
                product_key=product_key,
                metadata=metadata,
            )
            profile_product = profile_products.get(profile_key) or {}
            if profile_product.get("product_label"):
                return str(profile_product["product_label"])
        return str(product_key or "")

    def _render_rm_breakdown_html(self, result, order_qty, costing=None):
        consumable_state = self._effective_consumable_state()
        consumable_profile_products = self._build_consumable_profile_product_map()
        rm_reqs = []
        for rm in list(getattr(result, "rm_requirements", []) or []):
            product_key = getattr(rm, "product_key", "") or ""
            role = getattr(rm, "role", "") or ""
            metadata = getattr(rm, "metadata", {}) or {}
            if (
                should_include_consumable is not None
                and is_consumable_role is not None
                and is_consumable_role(role, product_key=product_key, metadata=metadata)
                and not should_include_consumable(
                    consumable_state,
                    role,
                    product_key=product_key,
                    metadata=metadata,
                    material_type="consumable",
                    category="consumable",
                )
            ):
                continue
            rm_reqs.append(rm)
        if not rm_reqs:
            return "<p>%s</p>" % _("No raw-material requirements computed.")

        fmt = self._preview_formatter()
        cost_index = {}
        if costing:
            try:
                cost_index = dict(costing.get("rm_cost_index") or {})
            except Exception:
                cost_index = {}

        metrics = getattr(result, "metrics", None)
        try:
            extra = getattr(metrics, "extra", {}) or {}
        except Exception:
            extra = {}

        header = (
            "<tr>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "</tr>"
            % (
                _("Product Key"),
                _("Layer"),
                _("Source Layer"),
                _("Role"),
                _("GSM"),
                _("BF"),
                _("Color"),
                _("Flute"),
                _("Stage"),
                _("Qty per FG"),
                _("Total Qty"),
                _("UoM"),
                _("Consumption Type"),
                _("Cost per FG"),
                _("Total Cost"),
                _("Metadata"),
            )
        )
        rows = [header]
        for rm in rm_reqs:
            product_key = getattr(rm, "product_key", "") or ""
            role = getattr(rm, "role", "") or ""
            stage_key = getattr(rm, "stage_key", "") or ""
            qty_per_fg = float(getattr(rm, "qty_per_fg", 0.0) or 0.0)
            qty_uom = getattr(rm, "qty_uom", "") or ""
            ctype = (getattr(rm, "consumption_type", "area") or "area").lower()
            metadata = getattr(rm, "metadata", {}) or {}
            layer_no = metadata.get("layer_no") or getattr(rm, "layer_no", None)
            source_layer_no = metadata.get("source_layer_no") or ""
            gsm = metadata.get("gsm") or (metadata.get("resolution", {}) or {}).get("gsm") or ""
            bf = metadata.get("bf") or (metadata.get("resolution", {}) or {}).get("bf") or ""
            color = metadata.get("color") or (metadata.get("resolution", {}) or {}).get("color") or ""
            flute = (
                metadata.get("flute")
                or metadata.get("flute_code")
                or (metadata.get("resolution", {}) or {}).get("flute_code")
                or ""
            )

            # Default order-level quantity: linear scaling from per-FG.
            total_qty = qty_per_fg * float(order_qty or 0.0)

            # For unit-sheet top-sheet RMs, enforce integer sheet counts
            # per order in the preview so that Total Qty reflects the
            # actual number of printed sheets required. Per-FG qty is
            # derived from this integer requirement for display only.
            if role == "top_sheet_external" and ctype == "unit" and order_qty:
                try:
                    boxes_per_sheet = float(extra.get("boxes_per_sheet", 0.0) or 0.0)
                except Exception:
                    boxes_per_sheet = 0.0
                try:
                    printing_ups_val = int(extra.get("printing_ups", 0) or 0)
                except Exception:
                    printing_ups_val = 0
                if printing_ups_val <= 0 and metrics is not None:
                    try:
                        printing_ups_val = int(getattr(metrics, "printing_ups", 0) or 0)
                    except Exception:
                        printing_ups_val = 0
                if printing_ups_val <= 0:
                    printing_ups_val = 1

                if boxes_per_sheet > 0.0:
                    sheets_total = math.ceil(float(order_qty) / boxes_per_sheet)
                else:
                    sheets_total = math.ceil(float(order_qty) / float(printing_ups_val or 1))

                total_qty = float(sheets_total)
                qty_per_fg = total_qty / float(order_qty or 1.0)

            # Cost lookup keyed by (product_key, role, layer_no, stage_key).
            cost_per_fg = 0.0
            total_cost = 0.0
            try:
                layer_no = metadata.get("layer_no")
                try:
                    layer_idx = int(layer_no or 0)
                except Exception:
                    layer_idx = 0
                key_str = "%s|%s|%s|%s" % (
                    product_key or "",
                    role or "",
                    layer_idx or "",
                    stage_key.strip() if isinstance(stage_key, str) else (stage_key or ""),
                )
                entry = cost_index.get(key_str) or {}
                cost_per_fg = float(entry.get("cost_per_fg", 0.0) or 0.0)
                total_cost = float(entry.get("cost_for_order", 0.0) or 0.0)
            except Exception:
                cost_per_fg = 0.0
                total_cost = 0.0

            meta_parts = []
            if layer_no is not None:
                meta_parts.append("layer_no=%s" % layer_no)
            if source_layer_no:
                meta_parts.append("source_layer_no=%s" % source_layer_no)
            kind = metadata.get("kind")
            if kind:
                meta_parts.append("kind=%s" % kind)
            display_product = self._display_rm_product_label(
                product_key=product_key,
                role=role,
                metadata=metadata,
                profile_products=consumable_profile_products,
            )
            if display_product and display_product != product_key and product_key:
                meta_parts.append("source_key=%s" % product_key)
            # For unit-sheet top sheets, surface the selected product
            # explicitly so that the RM breakdown clearly reflects the
            # chosen SKU used as the authoritative RM.
            try:
                if role == "top_sheet_external":
                    ts_pid = metadata.get("top_sheet_product_id")
                    if ts_pid:
                        Product = self.env["product.product"].sudo()
                        prod = Product.browse(int(ts_pid))
                        if prod and prod.exists():
                            meta_parts.append("product=%s" % (prod.display_name or prod.default_code or ts_pid))
                        else:
                            meta_parts.append("product_id=%s" % ts_pid)
            except Exception:
                # Preview display must never break on lookup failures.
                pass
            # Paper variant resolution (when available) is rendered inline
            # so that each RM row clearly shows its mapping status.
            res_status = metadata.get("resolution_status")
            res_name = metadata.get("resolution_product_name")
            if not res_name:
                res_name = (metadata.get("resolution", {}) or {}).get("product_name")
            required_deckle = metadata.get("required_deckle_cm")
            if res_status:
                # Short reason for non-exact resolutions.
                reason = ""
                if res_status == "missing":
                    reason = metadata.get("resolution_note") or "exact variant not found"
                elif res_status == "ambiguous":
                    reason = "multiple candidates"
                parts = ["paper=%s" % res_status]
                if res_name:
                    parts.append(res_name)
                if required_deckle:
                    parts.append("deckle=%.1fcm" % float(required_deckle))
                if reason:
                    parts.append(reason)
                meta_parts.append(" / ".join(parts))
            meta_str = ", ".join(str(p) for p in meta_parts) if meta_parts else ""
            display_qty_per_fg, display_uom, display_digits = self._display_rm_quantity(
                qty_per_fg,
                qty_uom,
                role,
                metadata,
                product_key,
            )
            display_total_qty, _display_total_uom, _display_total_digits = self._display_rm_quantity(
                total_qty,
                qty_uom,
                role,
                metadata,
                product_key,
            )

            rows.append(
                "<tr>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%.6f</td><td>%.6f</td><td>%s</td>"
                "</tr>"
                % (
                    display_product,
                    layer_no or "",
                    source_layer_no or "",
                    role,
                    gsm or "",
                    bf or "",
                    color or "",
                    flute or "",
                    stage_key or "",
                    fmt.format_quantity(display_qty_per_fg, display_uom, digits=display_digits, trim=False),
                    fmt.format_quantity(display_total_qty, display_uom, digits=display_digits, trim=False),
                    display_uom,
                    ctype,
                    cost_per_fg,
                    total_cost,
                    meta_str,
                )
            )

        html = [
            "<h3>%s</h3>" % _("Raw-material Breakdown"),
            "<table class='table table-sm table-striped'>",
            "<thead>%s</thead>" % header,
            "<tbody>",
            *rows[1:],  # body rows only
            "</tbody>",
            "</table>",
        ]
        return "\n".join(html)

    def _render_stage_flow_html(self, qgraph):
        nodes = getattr(qgraph, "nodes", {}) or {}
        edges = getattr(qgraph, "edges", []) or []

        # Node table
        node_rows = [
            "<tr><th>%s</th><th>%s</th></tr>" % (_("Stage"), _("Quantity"))
        ]
        # Preserve logical order when possible.
        ordered_keys = ["raw_material", "board", "sheet", "blanks", "finished_goods"]
        for key in ordered_keys:
            node = nodes.get(key)
            if not node:
                continue
            node_rows.append("<tr><td>%s</td><td>%s</td></tr>" % (key, node.qty))

        # Edge list with qty_ratio and waste
        edge_lines = []
        for e in edges:
            ratio = float(getattr(e, "qty_ratio", 0.0) or 0.0)
            waste = float(getattr(e, "waste_percent", 0.0) or 0.0)
            label = "%s â†’ %s Ã— %.6g" % (e.from_node, e.to_node, ratio)
            if waste:
                label += " (%s %.2f%%)" % (_("waste"), waste * 100.0)
            edge_lines.append("<li>%s</li>" % label)

        html = [
            "<h3>%s</h3>" % _("Stage Flow"),
            "<h4>%s</h4>" % _("Stage Quantities"),
            "<table class='table table-sm table-striped'>",
            "<thead>%s</thead>" % node_rows[0],
            "<tbody>",
            *node_rows[1:],
            "</tbody>",
            "</table>",
            "<h4>%s</h4>" % _("Edges"),
            "<ul>",
            *edge_lines,
            "</ul>",
        ]
        return "\n".join(html)

    def _render_bom_preview_html(self, bom_struct):
        components = bom_struct.get("components") or []
        operations = bom_struct.get("operations") or []

        # Components table
        comp_rows = [
            "<tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr>"
            % (_("Product"), _("Quantity"), _("UoM"), _("Stage"))
        ]
        for c in components:
            comp_rows.append(
                "<tr><td>%s</td><td>%.6f</td><td>%s</td><td>%s</td></tr>"
                % (
                    c.get("product") or "",
                    float(c.get("qty", 0.0) or 0.0),
                    c.get("uom") or "",
                    c.get("stage") or "",
                )
            )

        # Operations table
        op_rows = [
            "<tr><th>%s</th><th>%s</th><th>%s</th></tr>"
            % (_("Stage"), _("Input Qty"), _("Output Qty"))
        ]
        for op in operations:
            op_rows.append(
                "<tr><td>%s</td><td>%.6f</td><td>%.6f</td></tr>"
                % (
                    op.get("stage") or "",
                    float(op.get("input_qty", 0.0) or 0.0),
                    float(op.get("output_qty", 0.0) or 0.0),
                )
            )

        html = [
            "<h3>%s</h3>" % _("BOM Preview"),
            "<h4>%s</h4>" % _("Components"),
            "<table class='table table-sm table-striped'>",
            "<thead>%s</thead>" % comp_rows[0],
            "<tbody>",
            *comp_rows[1:],
            "</tbody>",
            "</table>",
            "<h4>%s</h4>" % _("Operations"),
            "<table class='table table-sm table-striped'>",
            "<thead>%s</thead>" % op_rows[0],
            "<tbody>",
            *op_rows[1:],
            "</tbody>",
            "</table>",
        ]
        return "\n".join(html)

    def _render_warnings_text(self, bom_struct):
        # Reuse the summary-style warnings from BOM structure metadata if present.
        components = bom_struct.get("components") or []
        if not components:
            return ""

        env = self.env
        Map = env["corr.material.product.map"].sudo()

        uom_warnings = []

        try:
            ref_kg = env.ref("uom.product_uom_kgm")
        except Exception:
            ref_kg = None
        try:
            ref_unit = env.ref("uom.product_uom_unit")
        except Exception:
            ref_unit = None
        try:
            ref_m = env.ref("uom.product_uom_meter")
        except Exception:
            ref_m = None

        def _cat(u):
            return u.category_id.id if u and u.category_id else None

        weight_cat = _cat(ref_kg)
        unit_cat = _cat(ref_unit)
        length_cat = _cat(ref_m)

        for comp in components:
            product_key = (comp.get("product") or "").strip()
            qty = float(comp.get("qty", 0.0) or 0.0)
            if not product_key or qty <= 0.0:
                continue

            role_norm = product_key.lower()
            base_role = role_norm.replace("rm_", "", 1)

            mapping = Map.find_match(base_role)
            if not mapping:
                continue

            product = mapping.product_id
            uom = mapping.uom_id or product.uom_id

            metadata = comp.get("metadata") or {}
            ctype = (metadata.get("consumption_type") or "area").lower()

            uom_cat = _cat(uom)
            warn = False
            if ctype == "area" and weight_cat and uom_cat and uom_cat != weight_cat:
                warn = True
            elif ctype == "unit" and unit_cat and uom_cat and uom_cat != unit_cat:
                warn = True
            elif ctype == "length" and uom_cat and length_cat and weight_cat:
                if uom_cat not in (length_cat, weight_cat):
                    warn = True

            if warn:
                uom_warnings.append(
                    {
                        "product": product.display_name or product_key,
                        "product_key": product_key,
                        "uom": uom.display_name or uom.name,
                        "uom_category": uom.category_id.display_name if uom.category_id else "",
                        "consumption_type": ctype,
                        "level": "warning",
                    }
                )

        if not uom_warnings:
            return ""

        lines = []
        count = len(uom_warnings)
        lines.append(_("Engine UoM warnings: %(count)d issue(s) detected") % {"count": count})
        for item in uom_warnings:
            role = item.get("product") or item.get("product_key") or ""
            uom_name = item.get("uom") or ""
            ctype = (item.get("consumption_type") or "").lower()
            if ctype == "area":
                basis = "area-based"
            elif ctype == "unit":
                basis = "unit-based"
            elif ctype == "length":
                basis = "length-based"
            else:
                basis = ctype or "unknown"
            lines.append(
                _("- %(role)s: UoM '%(uom)s' may not match %(basis)s consumption")
                % {"role": role, "uom": uom_name, "basis": basis}
            )
        return "\n".join(lines)

    def _render_preview_model_summary_html(self, preview_model):
        summary = dict((preview_model or {}).get("summary") or {})
        costing = dict(summary.get("costing") or {})
        context = self._build_summary_render_context(
            summary=summary,
            preview_model=preview_model,
            costing=costing,
        )
        payload = self._build_summary_render_payload(summary=summary, costing=costing)
        sections = _build_business_summary_sections(payload, context)
        return _render_business_summary_html(
            sections,
            warnings_present=bool((preview_model or {}).get("warnings")),
        )

        fmt = self._preview_formatter()
        summary = dict((preview_model or {}).get("summary") or {})
        costing = dict(summary.get("costing") or {})
        rows = []
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Preview source"), _("StageContext runtime")))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Spec"), summary.get("spec_name") or ""))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Style"), summary.get("style") or ""))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Plant"), summary.get("plant_name") or ""))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Order Qty"), fmt.format_integer(summary.get("order_qty", 0.0))))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("mÂ² per box"), fmt.format_m2(summary.get("m2_per_box", 0.0))))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("kg per box"), fmt.format_kg(summary.get("kg_per_box", 0.0))))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Blanks per sheet"), fmt.format_integer(summary.get("blanks_per_sheet", 0))))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Boxes per sheet"), fmt.format_integer(summary.get("boxes_per_sheet", 0.0))))
        rows.append(
            "<tr><th>%s</th><td>%s</td></tr>"
            % (_("Parent sheets / runs required"), fmt.format_integer(summary.get("parent_sheets_required", summary.get("sheets_required", 0.0))))
        )
        rows.append(
            "<tr><th>%s</th><td>%s</td></tr>"
            % (_("Slit sheets / blanks produced"), fmt.format_integer(summary.get("slit_sheets_produced", summary.get("fg_stage_total_qty", 0.0))))
        )
        rows.append(
            "<tr><th>%s</th><td>%s (%s)</td></tr>"
            % (
                _("Sheet-stage total"),
                fmt.format_integer(summary.get("sheet_stage_total_qty", 0.0)),
                ", ".join(summary.get("sheet_stage_codes") or []),
            )
        )
        rows.append(
            "<tr><th>%s</th><td>%s (%s)</td></tr>"
            % (
                _("FG-stage total"),
                fmt.format_integer(summary.get("fg_stage_total_qty", 0.0)),
                ", ".join(summary.get("fg_stage_codes") or []),
            )
        )
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Layout orientation"), summary.get("layout_orientation") or ""))
        rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Routing stages"), int(summary.get("routing_stage_count", 0) or 0)))

        if costing:
            symbol = (costing.get("currency") or {}).get("symbol") or ""

            rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Cost source (requested)"), costing.get("cost_source") or ""))
            effective = costing.get("effective_cost_sources") or []
            if effective:
                rows.append(
                    "<tr><th>%s</th><td>%s</td></tr>"
                    % (_("Cost source (effective)"), ", ".join(sorted(set(effective))))
                )
            rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Material cost per unit"), fmt.format_currency(costing.get("total_cost_per_fg", 0.0), symbol)))
            rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Material cost for order"), fmt.format_currency(costing.get("total_cost_for_order", 0.0), symbol)))
            rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Operation cost for order"), fmt.format_currency(costing.get("total_operation_cost", 0.0), symbol)))
            rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_("Total production cost for order"), fmt.format_currency(costing.get("total_production_cost", 0.0), symbol)))

        html = ["<h3>%s</h3>" % _("Engine V2 Summary")]
        if (preview_model or {}).get("warnings"):
            html.append(
                "<div class='alert alert-warning' role='alert'>%s</div>"
                % _("Warnings are present; see the Warnings tab for details.")
            )
        html += [
            "<table class='table table-sm table-striped'>",
            "<tbody>",
            *rows,
            "</tbody>",
            "</table>",
        ]
        return "\n".join(html)

    def _render_preview_model_rm_breakdown_html(self, preview_model):
        fmt = self._preview_formatter()
        consumable_state = dict(
            (preview_model or {}).get("consumable_state")
            or self._effective_consumable_state()
            or {}
        )

        def _include_material_row(row):
            if should_include_consumable is None:
                return True
            metadata = dict((row or {}).get("metadata") or {})
            return should_include_consumable(
                consumable_state,
                (row or {}).get("role") or "",
                product_key=(row or {}).get("product_key") or "",
                metadata=metadata,
                material_type=(row or {}).get("material_type") or "",
                category=(row or {}).get("category") or "",
            )

        piece_specific = dict((preview_model or {}).get("piece_specific_rm_breakdown") or {})
        if piece_specific.get("enabled"):
            header = (
                "<tr>"
                "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                "<th>%s</th><th>%s</th><th>%s</th>"
                "</tr>"
                % (
                    _("Product"),
                    _("Layer"),
                    _("Source Layer"),
                    _("Role"),
                    _("GSM"),
                    _("BF"),
                    _("Color"),
                    _("Flute"),
                    _("Stage"),
                    _("Stack Source"),
                    _("Qty per FG"),
                    _("Total Qty"),
                    _("UoM"),
                    _("Qty Source"),
                    _("Notes"),
                )
            )

            def _render_rows(rows):
                rendered = []
                for item in list(rows or []):
                    notes = ", ".join(item.get("notes") or [])
                    metadata = dict(item.get("metadata") or {})
                    display_qty_per_fg, display_uom, display_digits = self._display_rm_quantity(
                        item.get("qty_per_fg", 0.0),
                        item.get("uom") or "",
                        item.get("role") or "",
                        metadata,
                        item.get("product_key") or item.get("material_key") or "",
                    )
                    display_total_qty, _display_total_uom, _display_total_digits = self._display_rm_quantity(
                        item.get("total_qty", 0.0),
                        item.get("uom") or "",
                        item.get("role") or "",
                        metadata,
                        item.get("product_key") or item.get("material_key") or "",
                    )
                    rendered.append(
                        "<tr>"
                        "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                        "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                        "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                        "<td>%s</td><td>%s</td><td>%s</td>"
                        "</tr>"
                        % (
                            html_escape(str(item.get("product") or "")),
                            html_escape(str(item.get("layer_no") or "")),
                            html_escape(str(item.get("source_layer_no") or "")),
                            html_escape(str(item.get("role") or "")),
                            html_escape(str(item.get("gsm") or "")),
                            html_escape(str(item.get("bf") or "")),
                            html_escape(str(item.get("color") or "")),
                            html_escape(str(item.get("flute") or "")),
                            html_escape(str(item.get("stage_code") or "")),
                            html_escape(str(item.get("stack_source") or "")),
                            fmt.format_quantity(display_qty_per_fg, display_uom, digits=display_digits, trim=False),
                            fmt.format_quantity(display_total_qty, display_uom, digits=display_digits, trim=False),
                            html_escape(str(display_uom or "")),
                            html_escape(str(item.get("quantity_source") or "")),
                            html_escape(notes),
                        )
                    )
                return rendered

            rows_by_piece = defaultdict(list)
            for row in list(piece_specific.get("rows") or []):
                if not _include_material_row(row):
                    continue
                rows_by_piece[(row.get("piece_index") or 0, row.get("piece_key") or "")].append(row)

            html = ["<h3>%s</h3>" % _("Raw-material Breakdown")]
            for (piece_index, piece_key), rows in sorted(rows_by_piece.items(), key=lambda item: item[0]):
                piece_name = rows[0].get("piece_name") or piece_key or _("Piece")
                html.extend(
                    [
                        "<h4>%s</h4>" % html_escape(str(piece_name)),
                        "<table class='table table-sm table-striped'>",
                        "<thead>%s</thead>" % header,
                        "<tbody>",
                        *_render_rows(rows),
                        "</tbody>",
                        "</table>",
                    ]
                )

            fg_rows = [row for row in list(piece_specific.get("fg_rollup_rows") or []) if _include_material_row(row)]
            if fg_rows:
                html.extend(
                    [
                        "<h4>%s</h4>" % _("FG Rollup"),
                        "<table class='table table-sm table-striped'>",
                        "<thead>%s</thead>" % header,
                        "<tbody>",
                        *_render_rows(fg_rows),
                        "</tbody>",
                        "</table>",
                    ]
                )
            return "\n".join(html)

        rows_in = [
            row
            for row in list((preview_model or {}).get("materials") or [])
            if _include_material_row(row)
        ]
        if not rows_in:
            return "<p>%s</p>" % _("No StageContext material attachments were exposed.")

        header = (
            "<tr>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "<th>%s</th><th>%s</th><th>%s</th>"
            "</tr>"
            % (
                _("Product"),
                _("Layer"),
                _("Source Layer"),
                _("Role"),
                _("GSM"),
                _("BF"),
                _("Color"),
                _("Flute"),
                _("Stage"),
                _("Category"),
                _("Qty per FG"),
                _("Total Qty"),
                _("UoM"),
                _("Qty Source"),
                _("Notes"),
            )
        )
        rows = [header]

        def _paper_attrs_from_product_label(product_label):
            text = str(product_label or "")
            if "(" not in text or ")" not in text:
                return {}
            try:
                inside = text.split("(", 1)[-1].rsplit(")", 1)[0]
                parts = [part.strip() for part in inside.split(",")]
            except Exception:
                parts = []
            if len(parts) < 4:
                return {}
            return {
                "gsm": parts[0],
                "color": parts[2],
                "bf": parts[3],
            }

        for item in rows_in:
            metadata = dict(item.get("metadata") or {})
            notes = ", ".join(item.get("notes") or [])
            resolution = dict(metadata.get("resolution") or {})
            product_label = item.get("product") or ""
            product_attrs = _paper_attrs_from_product_label(product_label)
            layer_no = item.get("layer_no") or metadata.get("layer_no") or ""
            source_layer_no = metadata.get("source_layer_no") or ""
            gsm = metadata.get("gsm") or resolution.get("gsm") or product_attrs.get("gsm") or ""
            bf = metadata.get("bf") or resolution.get("bf") or product_attrs.get("bf") or ""
            color = metadata.get("color") or resolution.get("color") or product_attrs.get("color") or ""
            flute = (
                metadata.get("flute")
                or metadata.get("flute_code")
                or resolution.get("flute_code")
                or ""
            )
            display_qty_per_fg, display_uom, display_digits = self._display_rm_quantity(
                item.get("qty_per_fg", 0.0),
                item.get("uom") or "",
                item.get("role") or "",
                metadata,
                item.get("product_key") or item.get("material_key") or "",
            )
            display_total_qty, _display_total_uom, _display_total_digits = self._display_rm_quantity(
                item.get("total_qty", 0.0),
                item.get("uom") or "",
                item.get("role") or "",
                metadata,
                item.get("product_key") or item.get("material_key") or "",
            )
            rows.append(
                "<tr>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td>"
                "</tr>"
                % (
                    product_label,
                    layer_no,
                    source_layer_no,
                    item.get("role") or "",
                    gsm,
                    bf,
                    color,
                    flute,
                    item.get("stage_code") or "",
                    item.get("category") or "",
                    fmt.format_quantity(display_qty_per_fg, display_uom, digits=display_digits, trim=False),
                    fmt.format_quantity(display_total_qty, display_uom, digits=display_digits, trim=False),
                    display_uom,
                    item.get("quantity_source") or "",
                    notes,
                )
            )

        html = [
            "<h3>%s</h3>" % _("Raw-material Breakdown"),
            "<table class='table table-sm table-striped'>",
            "<thead>%s</thead>" % header,
            "<tbody>",
            *rows[1:],
            "</tbody>",
            "</table>",
        ]
        return "\n".join(html)

    def _suppress_unit_top_sheet_legacy_preview_materials(self, preview_model):
        """Remove duplicate legacy paper rows after preview-model enrichment.

        Unit top-sheet previews can expose the same physical paper via two
        paths: the resolved paper row and a legacy role fallback attachment.
        At this point the UI tabs read preview_model["materials"], so this
        final pass keeps the resolved row authoritative.
        """

        if not isinstance(preview_model, dict):
            return
        materials = list(preview_model.get("materials") or [])
        if not materials:
            return

        def _norm(value):
            return " ".join(str(value or "").split()).strip().lower()

        has_unit_top_sheet = any(
            _norm(row.get("role")) == "top_sheet_external"
            and _norm(row.get("uom")) in {"unit", "units", "sheet", "sheets", "pcs", "pc"}
            for row in materials
        )
        if not has_unit_top_sheet:
            return

        def _paper_key(row):
            role = _norm(row.get("role"))
            if role not in {"liner", "medium"}:
                return None
            metadata = dict(row.get("metadata") or {})
            try:
                layer_no = int(row.get("layer_no") or metadata.get("layer_no") or 0) or None
            except Exception:
                layer_no = None
            try:
                source_layer_no = int(metadata.get("source_layer_no") or 0) or None
            except Exception:
                source_layer_no = None
            product = _norm(row.get("product"))
            if not product:
                return None
            return (role, layer_no, source_layer_no, product, _norm(row.get("uom")))

        resolved_keys = set()
        for row in materials:
            if _norm(row.get("quantity_source")) != "paper_resolution":
                continue
            key = _paper_key(row)
            if key:
                resolved_keys.add(key)
        if not resolved_keys:
            return

        trace = list(preview_model.get("material_row_trace") or [])
        kept = []
        changed = False
        legacy_sources = {"legacy_role_fallback", "legacy_rm_requirement", "legacy_stage_fallback"}
        for row in materials:
            source = _norm(row.get("quantity_source"))
            key = _paper_key(row)
            if key in resolved_keys and source in legacy_sources:
                changed = True
                trace.append(
                    {
                        "action": "suppressed_unit_top_sheet_legacy_preview_material",
                        "path": "wizard_preview_model_material_filter",
                        "stage_code": str(row.get("stage_code") or ""),
                        "role": str(row.get("role") or ""),
                        "layer_no": row.get("layer_no"),
                        "qty_source": str(row.get("quantity_source") or ""),
                        "product": str(row.get("product") or ""),
                        "kept_qty_source": "paper_resolution",
                    }
                )
                continue
            kept.append(row)

        if changed:
            preview_model["materials"] = kept
            preview_model["material_row_trace"] = trace

    def _render_preview_model_stage_flow_html(self, preview_model):
        fmt = self._preview_formatter()
        stages = list((preview_model or {}).get("stages") or [])
        buckets = list((preview_model or {}).get("flow_buckets") or [])
        if not stages and not buckets:
            return "<p>%s</p>" % _("No StageContext stage flow was exposed.")

        bucket_header = (
            "<tr><th>%s</th><th>%s</th><th>%s</th></tr>"
            % (_("Bucket"), _("Quantity"), _("Stages"))
        )
        bucket_rows = [bucket_header]
        for bucket in buckets:
            bucket_rows.append(
                "<tr><td>%s</td><td>%s %s</td><td>%s</td></tr>"
                % (
                    bucket.get("bucket") or "",
                    fmt.format_quantity(bucket.get("qty", 0.0), bucket.get("uom") or ""),
                    bucket.get("uom") or "",
                    ", ".join(bucket.get("stages") or []),
                )
            )

        header = (
            "<tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr>"
            % (
                _("Stage"),
                _("Type"),
                _("Input Qty"),
                _("Output Qty"),
                _("UoM"),
                _("Inputs"),
                _("Warnings"),
            )
        )
        rows = [header]
        for stage in stages:
            inputs = []
            if int(stage.get("rm_attachment_count", 0) or 0):
                inputs.append(_("RM: %s") % int(stage.get("rm_attachment_count", 0) or 0))
            if int(stage.get("consumable_attachment_count", 0) or 0):
                inputs.append(_("Consumables: %s") % int(stage.get("consumable_attachment_count", 0) or 0))
            if int(stage.get("intermediate_attachment_count", 0) or 0):
                inputs.append(_("Intermediates: %s") % int(stage.get("intermediate_attachment_count", 0) or 0))
            rows.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    stage.get("stage_code") or "",
                    stage.get("stage_type") or "",
                    fmt.format_quantity(stage.get("input_qty", 0.0), stage.get("output_uom") or ""),
                    fmt.format_quantity(stage.get("output_qty", 0.0), stage.get("output_uom") or ""),
                    stage.get("output_uom") or "",
                    ", ".join(inputs),
                    ", ".join(stage.get("warnings") or []),
                )
            )

        flow_lines = []
        codes = [str(stage.get("stage_code") or "") for stage in stages if stage.get("stage_code")]
        if codes:
            flow_lines.append("<li>%s</li>" % " &rarr; ".join(codes))

        html = [
            "<h3>%s</h3>" % _("Stage Flow"),
            "<h4>%s</h4>" % _("Quantity Buckets"),
            "<table class='table table-sm table-striped'>",
            "<thead>%s</thead>" % bucket_header,
            "<tbody>",
            *bucket_rows[1:],
            "</tbody>",
            "</table>",
            "<h4>%s</h4>" % _("Stage Quantities"),
            "<table class='table table-sm table-striped'>",
            "<thead>%s</thead>" % header,
            "<tbody>",
            *rows[1:],
            "</tbody>",
            "</table>",
            "<h4>%s</h4>" % _("Operation Flow"),
            "<ul>",
            *flow_lines,
            "</ul>",
        ]
        return "\n".join(html)

    def _render_preview_model_bom_html(self, preview_model):
        fmt = self._preview_formatter()
        materials = list((preview_model or {}).get("materials") or [])
        operations = list((preview_model or {}).get("routed_operations") or [])
        if not operations:
            operations = list((preview_model or {}).get("operations") or [])

        comp_header = (
            "<tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr>"
            % (_("Product"), _("Role"), _("Category"), _("Total Qty"), _("Stage"))
        )
        comp_rows = [comp_header]
        for item in materials:
            comp_rows.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s %s</td><td>%s</td></tr>"
                % (
                    item.get("product") or "",
                    item.get("role") or "",
                    item.get("category") or "",
                    fmt.format_quantity(item.get("total_qty", 0.0), item.get("uom") or "", digits=3, trim=False),
                    item.get("uom") or "",
                    item.get("stage_code") or "",
                )
            )

        op_header = (
            "<tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr>"
            % (_("Stage"), _("Input Qty"), _("Output Qty"), _("UoM"), _("Output"))
        )
        op_rows = [op_header]
        for op in operations:
            output_label = op.get("yield_output_label") or op.get("output_kind") or op.get("yield_output_kind") or ""
            stage_label = op.get("stage_name") or op.get("stage_code") or op.get("stage") or ""
            op_rows.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    stage_label,
                    fmt.format_quantity(op.get("input_qty", 0.0), op.get("uom") or ""),
                    fmt.format_quantity(op.get("output_qty", 0.0), op.get("uom") or ""),
                    op.get("uom") or "",
                    output_label,
                )
            )

        html = [
            "<h3>%s</h3>" % _("BOM Preview"),
            "<h4>%s</h4>" % _("Components"),
            "<table class='table table-sm table-striped'>",
            "<thead>%s</thead>" % comp_header,
            "<tbody>",
            *comp_rows[1:],
            "</tbody>",
            "</table>",
            "<h4>%s</h4>" % _("Operations"),
            "<table class='table table-sm table-striped'>",
            "<thead>%s</thead>" % op_header,
            "<tbody>",
            *op_rows[1:],
            "</tbody>",
            "</table>",
        ]
        return "\n".join(html)

    def _render_preview_model_warnings_text(self, preview_model, extra_text=""):
        lines = []
        warnings = list((preview_model or {}).get("warnings") or [])
        if warnings:
            lines.append(_("StageContext preview warnings: %(count)d issue(s) detected") % {"count": len(warnings)})
            lines.extend("- %s" % warning for warning in warnings)
        if extra_text:
            if lines:
                lines.append("")
            lines.append(extra_text)
        return "\n".join(lines)

    def _build_piece_manufacturing_override_payload(self, piece_plan):
        spec = self._active_spec_record()
        inherited_display = self._piece_inherited_manufacturing_display()

        def _record_ref(record):
            if not record:
                return None
            try:
                rec_id = getattr(record, "id", None)
                name = getattr(record, "display_name", None) or getattr(record, "name", None) or ""
            except Exception:
                return None
            return {"id": rec_id, "name": name}

        parent_surface = getattr(spec, "surface_profile_id", None) if spec else None
        parent_top_sheet_product = (
            getattr(spec, "surface_top_sheet_product_id", None) if spec else None
        )
        parent_flutes = []
        try:
            for line in list(getattr(spec, "stack_line_ids", []) or []):
                flute = getattr(line, "flute_type_id", None) or getattr(line, "flute_id", None)
                if not flute:
                    continue
                code = getattr(flute, "code", "") or getattr(flute, "name", "") or ""
                if code and code not in parent_flutes:
                    parent_flutes.append(code)
        except Exception:
            parent_flutes = []

        piece_records = self._piece_records_by_key(spec)
        pieces_payload = []
        for piece in list(getattr(piece_plan, "pieces", []) or []):
            piece_key = getattr(piece, "piece_key", "")
            piece_record = piece_records.get(self._canonical_piece_key(piece_key))
            effective = self._effective_piece_manufacturing_payload(
                piece,
                piece_record=piece_record,
                spec=spec,
                inherited_display=inherited_display,
            )
            pieces_payload.append(effective)

        return {
            "enabled": bool(getattr(piece_plan, "enabled", False)),
            "phase": "J1",
            "behavior": "preview_only_piece_specific_recipe_setup",
            "materialization_allowed": False,
            "piece_sfg_creation_allowed": False,
            "piece_bom_creation_allowed": False,
            "piece_routing_execution_allowed": False,
            "custom_overrides_present": any(
                self._source_is_custom(piece.get("stack_source"))
                or self._source_is_custom(piece.get("surface_source"))
                or self._source_is_custom(piece.get("top_sheet_source"))
                for piece in pieces_payload
            ),
            "parent_spec": {
                "board_recipe": _record_ref(getattr(spec, "board_recipe_id", None) if spec else None),
                "flute_profiles": parent_flutes,
                "surface_profile": _record_ref(parent_surface),
                "top_sheet_mode": getattr(spec, "surface_top_sheet_mode", "") if spec else "",
                "top_sheet_product": _record_ref(parent_top_sheet_product),
            },
            "pieces": pieces_payload,
        }

    def _piece_records_by_key(self, spec):
        result = {}
        if not spec:
            return result
        try:
            lines = spec.multi_piece_line_ids.sorted(lambda line: (line.sequence or 0, line.id or 0))
        except Exception:
            lines = getattr(spec, "multi_piece_line_ids", []) or []
        for line in list(lines or []):
            for key in (
                getattr(line, "piece_code", None),
                getattr(line, "piece_name", None),
            ):
                key = self._canonical_piece_key(key)
                if key and key not in result:
                    result[key] = line
        return result

    def _canonical_piece_key(self, value):
        raw = str(value or "").strip()
        if not raw:
            return ""
        normalized = raw.upper().replace("-", "_").replace(" ", "_")
        aliases = {
            "PIECE_A": "A",
            "PIECE_1": "A",
            "A1": "A",
            "PIECE_B": "B",
            "PIECE_2": "B",
            "B1": "B",
        }
        if normalized in aliases:
            return aliases[normalized]
        if normalized in {"A", "B"}:
            return normalized
        return normalized

    def _effective_piece_manufacturing_payload(self, piece, *, piece_record=None, spec=None, inherited_display=None):
        inherited_display = inherited_display or {}
        piece_key = self._canonical_piece_key(getattr(piece, "piece_key", ""))
        piece_name = getattr(piece, "piece_name", "")

        if piece_record:
            stack = piece_record.get_effective_piece_stack(spec)
            surface = piece_record.get_effective_piece_surface(spec)
            top_sheet = piece_record.get_effective_piece_top_sheet(spec)
            stack_source = stack.get("source") or "inherited"
            surface_source = surface.get("source") or "inherited"
            top_sheet_source = top_sheet.get("source") or "inherited"
            recipe_signature = piece_record.recipe_signature
            effective_surface_profile = {
                "id": surface.get("profile_id"),
                "name": surface.get("display_name") or "",
            }
            effective_top_sheet_product = {
                "id": top_sheet.get("top_sheet_product_id"),
                "name": top_sheet.get("top_sheet_product_name") or "",
            }
            effective_top_sheet_mode = top_sheet.get("top_sheet_mode") or ""
        else:
            stack_source = self._normalized_piece_source(getattr(piece, "stack_source", "inherited"))
            surface_source = self._normalized_piece_source(getattr(piece, "surface_source", "inherited"))
            top_sheet_source = self._normalized_piece_source(getattr(piece, "top_sheet_source", "inherited"))
            stack = {
                "source": stack_source,
                "lines": self._parent_stack_line_payloads(spec),
            }
            effective_surface_profile = {
                "id": getattr(getattr(spec, "surface_profile_id", None), "id", None) if spec else None,
                "name": inherited_display.get("surface_profile") or "",
            }
            effective_top_sheet_product = {
                "id": getattr(getattr(spec, "surface_top_sheet_product_id", None), "id", None) if spec else None,
                "name": inherited_display.get("top_sheet_product") or "",
            }
            effective_top_sheet_mode = getattr(spec, "surface_top_sheet_mode", "") if spec else ""
            recipe_signature = self._hash_piece_recipe_payload(
                stack,
                effective_surface_profile,
                {
                    "top_sheet_mode": effective_top_sheet_mode,
                    "top_sheet_product": effective_top_sheet_product,
                },
            )

        return {
            "piece_key": piece_key,
            "piece_name": piece_name,
            "stack_source": stack_source,
            "effective_stack_lines": list(stack.get("lines") or []),
            "surface_source": surface_source,
            "effective_surface_profile": effective_surface_profile,
            "top_sheet_source": top_sheet_source,
            "effective_top_sheet_mode": effective_top_sheet_mode,
            "effective_top_sheet_product": effective_top_sheet_product,
            "recipe_signature": recipe_signature,
            "stack": {
                "source": stack_source,
                "effective_stack_lines": list(stack.get("lines") or []),
                "effective_source": "piece_custom" if self._source_is_custom(stack_source) else "parent_spec",
                "effective_flute_profile": self._flute_summary_from_stack(stack.get("lines") or [])
                or inherited_display.get("flute_profile", ""),
            },
            "surface": {
                "source": surface_source,
                "effective_source": "piece_custom" if self._source_is_custom(surface_source) else "parent_spec",
                "effective_surface_profile": effective_surface_profile.get("name") or "",
                "effective_surface_profile_id": effective_surface_profile.get("id"),
            },
            "top_sheet": {
                "source": top_sheet_source,
                "effective_source": "piece_custom" if self._source_is_custom(top_sheet_source) else "parent_spec",
                "effective_top_sheet_mode": effective_top_sheet_mode,
                "effective_top_sheet_product": effective_top_sheet_product.get("name") or "",
                "effective_top_sheet_product_id": effective_top_sheet_product.get("id"),
            },
            "behavior": "custom_preview_only"
            if any(
                self._source_is_custom(source)
                for source in (stack_source, surface_source, top_sheet_source)
            )
            else "inherit_parent_values",
            "editable": True,
            "materialization_allowed": False
            if any(
                self._source_is_custom(source)
                for source in (stack_source, surface_source, top_sheet_source)
            )
            else True,
        }

    def _parent_stack_line_payloads(self, spec):
        if not spec:
            return []
        try:
            lines = spec.stack_line_ids.sorted(lambda line: (line.sequence or 0, line.id or 0))
        except Exception:
            lines = getattr(spec, "stack_line_ids", []) or []
        result = []
        for line in list(lines or []):
            flute = getattr(line, "flute_type_id", None)
            product = getattr(line, "material_product_id", None)
            result.append(
                {
                    "sequence": getattr(line, "sequence", 0),
                    "role": getattr(line, "role", ""),
                    "gsm": getattr(line, "gsm", 0.0),
                    "bf": getattr(line, "bf", 0.0),
                    "color": getattr(line, "color", "") or "",
                    "flute_type_id": getattr(flute, "id", False) if flute else False,
                    "flute": (getattr(flute, "code", "") or getattr(flute, "name", "") or "") if flute else "",
                    "flute_tuf": float(getattr(flute, "tuf", 0.0) or 0.0) if flute else 0.0,
                    "material_product_id": getattr(product, "id", False) if product else False,
                    "material_product_name": getattr(product, "display_name", "") if product else "",
                    "source_type": getattr(line, "source_type", "") or "manual",
                    "source_recipe_line_id": getattr(getattr(line, "source_recipe_line_id", None), "id", False),
                }
            )
        return result

    def _flute_summary_from_stack(self, lines):
        flutes = []
        for line in list(lines or []):
            flute = str((line or {}).get("flute") or "").strip()
            if flute and flute not in flutes:
                flutes.append(flute)
        return ", ".join(flutes)

    def _normalized_piece_source(self, value):
        value = str(value or "inherited").strip().lower()
        return "custom" if value == "override" else (value or "inherited")

    def _source_is_custom(self, value):
        return self._normalized_piece_source(value) == "custom"

    def _hash_piece_recipe_payload(self, stack, surface, top_sheet):
        blob = json.dumps(
            {"stack": stack, "surface": surface, "top_sheet": top_sheet},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()

    def _payload_has_custom_piece_manufacturing(self, payload):
        overrides = dict((payload or {}).get("piece_manufacturing_overrides") or {})
        for piece in list(overrides.get("pieces") or []):
            if any(
                self._source_is_custom(piece.get(key))
                for key in ("stack_source", "surface_source", "top_sheet_source")
            ):
                return True
            for section in ("stack", "surface", "top_sheet"):
                if self._source_is_custom((piece.get(section) or {}).get("source")):
                    return True
        multi_piece = dict((payload or {}).get("multi_piece") or {})
        for piece in list(multi_piece.get("pieces") or []):
            if any(
                self._source_is_custom(piece.get(key))
                for key in ("stack_source", "surface_source", "top_sheet_source")
            ):
                return True
        return False

    def _payload_with_piece_specific_rm_requirements(self, payload):
        """Use J2 piece-effective RM rows as the materialization input.

        Preview already resolves custom piece papers against the piece deckle.
        The writer/resolver path consumes payload["rm_requirements"], so mirror
        those resolved rows there before rebuilding the J3 materialization plan.
        """
        payload = deepcopy(dict(payload or {}))
        piece_specific = dict(payload.get("piece_specific_rm_breakdown") or {})
        rows = list(piece_specific.get("rows") or [])
        if not rows:
            return payload

        def _product_key(role, metadata):
            role = str(role or metadata.get("role") or metadata.get("type") or "").strip().lower()
            kind = str(metadata.get("kind") or "").strip().lower()
            if role == "carton_board" or kind == "carton_board":
                return "rm_carton_board"
            if role == "medium":
                return "rm_medium"
            if role == "top_roll" or kind == "top_roll":
                return "rm_top_roll"
            if role == "top_sheet_external" or kind == "top_sheet_external":
                return "rm_top_sheet_external"
            return "rm_liner"

        rm_requirements = []
        for row in rows:
            row = dict(row or {})
            metadata = deepcopy(dict(row.get("metadata") or {}))
            role = str(row.get("role") or metadata.get("role") or metadata.get("type") or "").strip()
            stage_key = row.get("stage_code") or row.get("stage_key") or metadata.get("stage_key")
            layer_no = row.get("layer_no") or metadata.get("layer_no")
            qty_uom = row.get("uom") or metadata.get("uom_name") or metadata.get("qty_uom") or ""
            resolution = dict(metadata.get("resolution") or {})
            product_id = (
                metadata.get("product_id")
                or row.get("product_id")
                or metadata.get("top_sheet_product_id")
                or row.get("top_sheet_product_id")
                or resolution.get("product_id")
            )
            paper_role = str(role or "").strip().lower() in {"liner", "medium", "top_roll", "carton_board"}
            for key in (
                "piece_key",
                "piece_index",
                "piece_name",
                "stack_source",
                "surface_source",
                "top_sheet_source",
                "recipe_signature",
                "source_layer_no",
                "gsm",
                "bf",
                "color",
                "flute",
                "flute_code",
                "flute_type",
                "flute_type_code",
                "flute_type_id",
                "flute_tuf",
            ):
                value = row.get(key)
                if value not in (None, ""):
                    metadata[key] = value
            if paper_role:
                metadata["force_live_paper_resolution"] = True
                metadata["source_kind"] = "piece_effective_recipe"
            if product_id and not metadata.get("writer_product_key"):
                metadata["writer_product_key"] = "product.product:%s" % product_id
            if product_id and not resolution.get("product_id"):
                resolution["product_id"] = product_id
            product_name = metadata.get("product_name") or row.get("product_name") or row.get("display_product")
            if product_name:
                metadata["product_name"] = product_name
            if product_name and not resolution.get("product_name"):
                resolution["product_name"] = product_name
            product_tmpl_id = metadata.get("product_tmpl_id") or row.get("product_tmpl_id")
            if product_tmpl_id:
                metadata["product_tmpl_id"] = product_tmpl_id
            uom_id = metadata.get("uom_id") or row.get("uom_id")
            if uom_id:
                metadata["uom_id"] = uom_id
            if uom_id and not resolution.get("uom_id"):
                resolution["uom_id"] = uom_id
            required_deckle_cm = metadata.get("required_deckle_cm") or row.get("required_deckle_cm")
            if required_deckle_cm:
                metadata["required_deckle_cm"] = required_deckle_cm
            if required_deckle_cm and not resolution.get("required_deckle_cm"):
                resolution["required_deckle_cm"] = required_deckle_cm
            if paper_role:
                product_id = None
                metadata.pop("product_id", None)
                metadata.pop("product_tmpl_id", None)
                metadata.pop("writer_product_key", None)
                resolution.pop("product_id", None)
                resolution.pop("product_tmpl_id", None)
            if product_id:
                metadata["product_id"] = product_id
                metadata["resolution_status"] = metadata.get("resolution_status") or "resolved"
                resolution["status"] = resolution.get("status") or "resolved"
            metadata["resolution"] = resolution
            rm_requirements.append(
                {
                    "product_key": row.get("product_key") or _product_key(role, metadata),
                    "role": role,
                    "stage_key": stage_key,
                    "layer_no": layer_no,
                    "qty_per_fg": row.get("qty_per_fg", 0.0),
                    "qty_uom": qty_uom,
                    "required_deckle_cm": required_deckle_cm,
                    "product_id": product_id,
                    "uom_id": uom_id,
                    "consumption_type": metadata.get("consumption_type")
                    or ("unit" if qty_uom == "unit" else "area"),
                    "metadata": metadata,
                }
            )

        payload["rm_requirements"] = rm_requirements
        payload["j3_piece_effective_rm_requirements_applied"] = {
            "enabled": True,
            "source": "piece_specific_rm_breakdown.rows",
            "row_count": len(rm_requirements),
        }
        return payload

    def _resolve_g1_piece_paper_variant(self, **kwargs):
        try:
            Map = self.env["corr.material.product.map"].sudo()
        except Exception:
            Map = None
        result = {}
        if Map is not None:
            try:
                result = Map.resolve_engine_paper_variant_v1(
                    role=kwargs.get("role"),
                    gsm=kwargs.get("gsm"),
                    bf=kwargs.get("bf"),
                    color=kwargs.get("color"),
                    flute=kwargs.get("flute"),
                    deckle_cm=kwargs.get("deckle_cm"),
                    material_template_id=kwargs.get("material_template_id"),
                    exact_resolution_only=True,
                )
            except Exception:
                result = {}
        product = (result or {}).get("product")
        if product and not self._piece_paper_product_matches_request(product, **kwargs):
            result = {}
        fallback_debug = {}
        if not (result or {}).get("product"):
            product, fallback_debug = self._fallback_resolve_g1_piece_paper_variant(**kwargs)
            if product:
                result = {
                    "status": "resolved",
                    "product": product,
                    "uom": getattr(product, "uom_id", None),
                    "candidate_ids": [getattr(product, "id", None)] if getattr(product, "id", None) else [],
                    "debug": {"fallback_product_attribute_search": fallback_debug},
                }
        product = (result or {}).get("product")
        uom = (result or {}).get("uom")
        status = (result or {}).get("status") or "missing"
        product_exists = False
        uom_exists = False
        try:
            product_exists = bool(product and product.exists())
        except Exception:
            product_exists = bool(getattr(product, "id", None))
        try:
            uom_exists = bool(uom and uom.exists())
        except Exception:
            uom_exists = bool(getattr(uom, "id", None))
        return {
            "status": status,
            "product_id": product.id if product_exists else None,
            "product_name": product.display_name if product_exists else "",
            "uom_id": uom.id if uom_exists else None,
            "uom_name": uom.display_name if uom_exists else "",
            "candidate_ids": list((result or {}).get("candidate_ids") or []),
            "required_deckle_cm": kwargs.get("deckle_cm"),
            "debug": dict((result or {}).get("debug") or {}),
        }

    def _piece_paper_product_matches_request(self, product, **kwargs):
        try:
            product_exists = bool(product and product.exists())
        except Exception:
            product_exists = bool(getattr(product, "id", None))
        if not product_exists:
            return False

        requested_gsm = self._piece_paper_float(kwargs.get("gsm"))
        requested_deckle = self._piece_paper_float(kwargs.get("deckle_cm"))
        requested_bf = self._piece_paper_float(kwargs.get("bf"))
        requested_color = self._piece_paper_normalize(kwargs.get("color"))

        product_gsm = self._piece_paper_product_attr_float(product, ["GSM"])
        product_deckle = self._piece_paper_product_attr_float(
            product,
            ["Deckle (cm)", "Deckle", "Deckle(cm)", "Reel Width (cm)", "Reel Width"],
        )
        product_bf = self._piece_paper_product_attr_float(product, ["BF", "Burst Factor"])
        product_color = self._piece_paper_normalize(self._piece_paper_product_attr(product, ["Color"]))

        if requested_gsm > 0.0 and (product_gsm <= 0.0 or abs(product_gsm - requested_gsm) > 0.001):
            return False
        if requested_deckle > 0.0 and (product_deckle <= 0.0 or abs(product_deckle - requested_deckle) > 0.5):
            return False
        if requested_bf > 0.0 and product_bf > 0.0 and abs(product_bf - requested_bf) > 0.001:
            return False
        if requested_color and product_color and requested_color not in product_color and product_color not in requested_color:
            return False
        return True

    def _piece_paper_product_attr(self, product, names):
        wanted = {str(name or "").strip().lower() for name in list(names or [])}
        try:
            values = product.product_template_attribute_value_ids
        except Exception:
            values = []
        for value in values:
            attr_name = str(getattr(getattr(value, "attribute_id", None), "name", "") or "").strip().lower()
            if attr_name in wanted:
                return getattr(value, "name", "") or ""
        return ""

    def _piece_paper_product_attr_float(self, product, names):
        return self._piece_paper_float(self._piece_paper_product_attr(product, names))

    def _piece_paper_float(self, value):
        try:
            return float(value)
        except Exception:
            try:
                import re

                match = re.search(r"[-+]?\d*\.?\d+", str(value or ""))
                return float(match.group(0)) if match else 0.0
            except Exception:
                return 0.0

    def _piece_paper_normalize(self, value):
        text = str(value or "").strip().lower()
        for token in ("(", ")", "-", "_"):
            text = text.replace(token, " ")
        return " ".join(text.split())

    def _fallback_resolve_g1_piece_paper_variant(self, **kwargs):
        """Fallback for piece preview display when map resolution misses.

        This is read-only and only returns an existing product variant. It
        matches the same business inputs users see on Kraft Paper variants:
        GSM, Deckle, optional BF/color, and does not require separate medium
        variants when the same paper can be consumed as liner or medium.
        """

        debug = {
            "attempted": True,
            "gsm": kwargs.get("gsm"),
            "bf": kwargs.get("bf"),
            "color": kwargs.get("color"),
            "deckle_cm": kwargs.get("deckle_cm"),
            "candidate_count": 0,
            "matched_product_id": None,
            "matched_by": "",
        }
        try:
            gsm = float(kwargs.get("gsm") or 0.0)
        except Exception:
            gsm = 0.0
        try:
            deckle = float(kwargs.get("deckle_cm") or 0.0)
        except Exception:
            deckle = 0.0
        if gsm <= 0.0 or deckle <= 0.0:
            debug["skipped_reason"] = "missing_gsm_or_deckle"
            return None, debug

        def _norm(value):
            text = str(value or "").strip().lower()
            for token in ("(", ")", "-", "_"):
                text = text.replace(token, " ")
            return " ".join(text.split())

        def _to_float(value):
            try:
                return float(value)
            except Exception:
                try:
                    import re
                    match = re.search(r"[-+]?\d*\.?\d+", str(value or ""))
                    return float(match.group(0)) if match else 0.0
                except Exception:
                    return 0.0

        def _attr(product, names):
            for value in product.product_template_attribute_value_ids:
                attr_name = (getattr(value.attribute_id, "name", "") or "").strip().lower()
                if attr_name in {name.lower() for name in names}:
                    return value.name
            return ""

        try:
            Product = self.env["product.product"].sudo().with_context(active_test=False)
            candidates = Product.search([("active", "in", [True, False])])
        except Exception:
            debug["skipped_reason"] = "product_model_unavailable"
            return None, debug

        wanted_color = _norm(kwargs.get("color"))
        wanted_bf = _to_float(kwargs.get("bf"))
        usable = []
        for product in candidates:
            gsm_val = _to_float(_attr(product, ["GSM"]))
            deckle_val = _to_float(_attr(product, ["Deckle (cm)", "Deckle", "Deckle(cm)", "Reel Width (cm)", "Reel Width"]))
            if abs(gsm_val - gsm) > max(abs(gsm) * 0.05, 0.01):
                continue
            if abs(deckle_val - deckle) > 0.5:
                continue
            bf_val = _to_float(_attr(product, ["BF"]))
            color_val = _norm(_attr(product, ["Color"]))
            score = 0
            if wanted_bf and bf_val:
                score += abs(bf_val - wanted_bf)
            if wanted_color and color_val and wanted_color not in color_val and color_val not in wanted_color:
                score += 10
            usable.append((score, product.id, product))

        debug["candidate_count"] = len(usable)
        if not usable:
            debug["skipped_reason"] = "no_attribute_match"
            return None, debug
        usable.sort(key=lambda item: (item[0], item[1]))
        product = usable[0][2]
        debug["matched_product_id"] = product.id
        debug["matched_by"] = "product_variant_attributes"
        return product, debug

    def _apply_g1_piece_resolution_to_preview(
        self,
        result,
        bom_struct,
        paper_resolution,
        piece_layout_planning,
    ):
        if not engine_v2_multi_piece_materialization:
            return
        piece_plan = getattr(result, "piece_plan", None)
        if not piece_plan or not bool(getattr(piece_plan, "enabled", False)):
            return

        def _serialize_rm(rm):
            return {
                "product_key": getattr(rm, "product_key", ""),
                "role": getattr(rm, "role", ""),
                "stage_key": getattr(rm, "stage_key", None),
                "layer_no": getattr(rm, "layer_no", None),
                "qty_per_fg": getattr(rm, "qty_per_fg", 0.0),
                "qty_uom": getattr(rm, "qty_uom", ""),
                "consumption_type": getattr(rm, "consumption_type", "area"),
                "metadata": deepcopy(getattr(rm, "metadata", {}) or {}),
            }

        def _row_key(row):
            meta = dict((row or {}).get("metadata") or {})
            return (
                str((row or {}).get("product_key") or ""),
                str((row or {}).get("role") or ""),
                str((row or {}).get("stage_key") or ""),
                int((row or {}).get("layer_no") or meta.get("layer_no") or 0),
                str(meta.get("piece_key") or "").strip().upper(),
            )

        def _resolution_key(role, layer_no):
            return "%s:%s" % ((role or "").strip().lower(), int(layer_no or 0))

        try:
            payload = {
                "multi_piece": {
                    "enabled": bool(getattr(piece_plan, "enabled", False)),
                    "source": getattr(piece_plan, "source", ""),
                    "status": getattr(piece_plan, "status", ""),
                    "piece_count": int(getattr(piece_plan, "piece_count", 0) or 0),
                    "multi_piece_strategy": getattr(
                        piece_plan,
                        "multi_piece_strategy",
                        "",
                    )
                    or getattr(self.v2_spec_id, "multi_piece_strategy", "")
                    or self.multi_piece_strategy
                    or "independent_routed_piece",
                    "strategy": getattr(
                        piece_plan,
                        "multi_piece_strategy",
                        "",
                    )
                    or getattr(self.v2_spec_id, "multi_piece_strategy", "")
                    or self.multi_piece_strategy
                    or "independent_routed_piece",
                    "split_after_stage_key": getattr(
                        piece_plan,
                        "split_after_stage_key",
                        "",
                    )
                    or getattr(self.v2_spec_id, "multi_piece_split_after_stage_key", "")
                    or self.multi_piece_split_after_stage_key
                    or "",
                    "split_axis": getattr(piece_plan, "split_axis", ""),
                    "split_mode": getattr(piece_plan, "split_mode", ""),
                    "source_width_mm": getattr(piece_plan, "source_width_mm", 0.0),
                    "source_length_mm": getattr(piece_plan, "source_length_mm", 0.0),
                    "merge_stage_key": getattr(piece_plan, "merge_stage_key", ""),
                    "pieces": [
                        {
                            "piece_key": getattr(piece, "piece_key", ""),
                            "piece_name": getattr(piece, "piece_name", ""),
                            "piece_qty_per_fg": getattr(piece, "piece_qty_per_fg", 0.0),
                            "piece_width_mm": getattr(piece, "piece_width_mm", 0.0),
                            "piece_length_mm": getattr(piece, "piece_length_mm", 0.0),
                            "stack_source": getattr(piece, "stack_source", "inherited"),
                            "surface_source": getattr(piece, "surface_source", "inherited"),
                            "top_sheet_source": getattr(piece, "top_sheet_source", "inherited"),
                        }
                        for piece in list(getattr(piece_plan, "pieces", []) or [])
                    ],
                },
                "piece_layout_planning": dict(piece_layout_planning or {}),
                "rm_requirements": [
                    _serialize_rm(rm)
                    for rm in list(getattr(result, "rm_requirements", []) or [])
                ],
            }
            enriched = (
                engine_v2_multi_piece_materialization.enrich_phase_g1_piece_rm_requirements(
                    payload,
                    paper_resolver=self._resolve_g1_piece_paper_variant,
                )
            )
        except Exception:  # pragma: no cover - defensive
            _logger.exception("Phase G1 preview RM enrichment failed")
            return

        enriched_rows = list((enriched or {}).get("rm_requirements") or [])
        piece_deckle_by_key = {}
        try:
            for piece in list(getattr(piece_plan, "pieces", []) or []):
                key = str(getattr(piece, "piece_key", "") or "").strip().upper()
                width = float(getattr(piece, "piece_width_mm", 0.0) or 0.0)
                if key and width > 0.0:
                    piece_deckle_by_key[key] = width / 10.0
        except Exception:
            piece_deckle_by_key = {}
        enriched_by_key = {_row_key(row): dict(row or {}) for row in enriched_rows}

        resolution_by_key = {}
        for row in enriched_rows:
            row = dict(row or {})
            metadata = dict(row.get("metadata") or {})
            resolution = dict(metadata.get("resolution") or {})
            piece_key = str(metadata.get("piece_key") or "").strip().upper()
            fallback_deckle = piece_deckle_by_key.get(piece_key)
            if fallback_deckle and not metadata.get("required_deckle_cm"):
                metadata["required_deckle_cm"] = fallback_deckle
            if fallback_deckle and not resolution.get("required_deckle_cm"):
                resolution["required_deckle_cm"] = fallback_deckle
            role = str(metadata.get("semantic_role") or row.get("role") or "").strip().lower()
            if role == "top_roll":
                role = "liner"
            if role not in ("liner", "medium"):
                continue
            layer_no = int(row.get("layer_no") or metadata.get("layer_no") or 0)
            if not layer_no:
                continue
            if not resolution.get("product_id") and not metadata.get("product_id") and fallback_deckle:
                resolved = self._resolve_g1_piece_paper_variant(
                    role=role,
                    gsm=resolution.get("gsm") or metadata.get("gsm"),
                    bf=resolution.get("bf") or metadata.get("bf"),
                    color=resolution.get("color") or metadata.get("color"),
                    flute=resolution.get("flute_code") or metadata.get("flute_code") if role == "medium" else None,
                    deckle_cm=fallback_deckle,
                )
                if resolved.get("product_id"):
                    resolution.update(resolved)
                    metadata["product_id"] = resolved.get("product_id")
                    metadata["product_name"] = resolved.get("product_name")
                    metadata["resolution_product_name"] = resolved.get("product_name")
                    metadata["resolution_status"] = resolved.get("status") or "resolved"
                    metadata["writer_product_key"] = "product.product:%s" % resolved.get("product_id")
                    metadata["uom_id"] = resolved.get("uom_id")
                    metadata["uom_name"] = resolved.get("uom_name")
            if not resolution.get("product_id") and not metadata.get("product_id"):
                continue
            entry = {
                "key": _resolution_key(role, layer_no),
                "role": role,
                "layer_no": layer_no,
                "source_layer_no": metadata.get("source_layer_no") or layer_no,
                "gsm": resolution.get("gsm") or metadata.get("gsm"),
                "bf": resolution.get("bf") or metadata.get("bf"),
                "color": resolution.get("color") or metadata.get("color") or "",
                "flute_code": resolution.get("flute_code") or metadata.get("flute_code") or "",
                "required_deckle_cm": metadata.get("required_deckle_cm")
                or resolution.get("required_deckle_cm"),
                "qty_per_fg": row.get("qty_per_fg"),
                "qty_uom": row.get("qty_uom"),
                "status": resolution.get("status") or metadata.get("resolution_status") or "exact",
                "product_id": metadata.get("product_id") or resolution.get("product_id"),
                "product_name": (
                    metadata.get("product_name")
                    or metadata.get("resolution_product_name")
                    or resolution.get("product_name")
                    or ""
                ),
                "uom_id": metadata.get("uom_id") or resolution.get("uom_id"),
                "uom_name": metadata.get("uom_name") or resolution.get("uom_name") or "",
                "candidate_ids": list(resolution.get("candidate_ids") or []),
                "resolution_note": resolution.get("resolution_note") or metadata.get("resolution_note") or "",
                "debug": dict(resolution.get("debug") or {}),
            }
            if entry.get("product_id") and entry.get("product_name"):
                entry["status"] = entry.get("status") or "resolved"
                entry["resolution_note"] = "Exact paper variant resolved"
            resolution_by_key[entry["key"]] = entry

        for rm in list(getattr(result, "rm_requirements", []) or []):
            row = _serialize_rm(rm)
            enriched_row = enriched_by_key.get(_row_key(row))
            if not enriched_row:
                continue
            metadata = dict(enriched_row.get("metadata") or {})
            resolution = dict(metadata.get("resolution") or {})
            if resolution.get("product_name") and not metadata.get("resolution_product_name"):
                metadata["resolution_product_name"] = resolution.get("product_name")
            if metadata.get("product_id") or resolution.get("product_id"):
                resolved_status = resolution.get("status") or metadata.get("resolution_status") or "resolved"
                if str(resolved_status or "").strip().lower() == "missing":
                    resolved_status = "resolved"
                metadata["resolution_status"] = resolved_status
                resolution["status"] = resolved_status
                metadata["product_id"] = metadata.get("product_id") or resolution.get("product_id")
                metadata["product_name"] = (
                    metadata.get("product_name")
                    or metadata.get("resolution_product_name")
                    or resolution.get("product_name")
                    or ""
                )
                metadata["resolution_product_name"] = (
                    metadata.get("resolution_product_name")
                    or resolution.get("product_name")
                    or metadata.get("product_name")
                    or ""
                )
                metadata["uom_id"] = metadata.get("uom_id") or resolution.get("uom_id")
                metadata["uom_name"] = metadata.get("uom_name") or resolution.get("uom_name")
                metadata["resolution_note"] = "Exact paper variant resolved"
                resolution["resolution_note"] = "Exact paper variant resolved"
                metadata["resolution"] = resolution
            elif resolution.get("status") and not metadata.get("resolution_status"):
                metadata["resolution_status"] = resolution.get("status")
            try:
                setattr(rm, "metadata", metadata)
            except Exception:
                pass

        existing_paper_by_key = {
            _resolution_key(entry.get("role"), entry.get("layer_no")): entry
            for entry in list(paper_resolution or [])
            if isinstance(entry, dict)
        }
        for key, entry in resolution_by_key.items():
            existing = existing_paper_by_key.get(key)
            if existing:
                existing.update({k: v for k, v in entry.items() if v not in (None, "")})
            else:
                paper_resolution.append(entry)

        for comp in list((bom_struct or {}).get("components") or []):
            metadata = dict(comp.get("metadata") or {})
            role = str(metadata.get("type") or metadata.get("role") or "").strip().lower()
            if role == "top_roll":
                role = "liner"
            layer_no = int(metadata.get("layer_no") or 0)
            entry = resolution_by_key.get(_resolution_key(role, layer_no))
            if not entry:
                continue
            metadata["resolution"] = {
                "status": entry.get("status"),
                "product_id": entry.get("product_id"),
                "product_name": entry.get("product_name"),
                "uom_id": entry.get("uom_id"),
                "uom_name": entry.get("uom_name"),
                "candidate_ids": list(entry.get("candidate_ids") or []),
                "required_deckle_cm": entry.get("required_deckle_cm"),
                "resolution_note": entry.get("resolution_note"),
            }
            metadata["resolution_status"] = entry.get("status")
            metadata["resolution_product_name"] = entry.get("product_name") or ""
            metadata["required_deckle_cm"] = entry.get("required_deckle_cm")
            comp["metadata"] = metadata
            if entry.get("product_id") and entry.get("product_name"):
                comp["product"] = entry.get("product_name")

    def _apply_g1_resolution_to_preview_model(
        self,
        preview_model,
        result,
        paper_resolution,
        bom_struct,
    ):
        if not isinstance(preview_model, dict):
            return
        piece_plan = self._piece_plan_dict(result)
        if not piece_plan or not bool(self._get_piece_plan_value(piece_plan, "enabled", False)):
            return
        pieces = list(self._get_piece_plan_value(piece_plan, "pieces", []) or [])
        strategy = str(
            self._get_piece_plan_value(piece_plan, "multi_piece_strategy", "")
            or self._get_piece_plan_value(piece_plan, "strategy", "")
            or ""
        ).strip().lower()
        source_width_mm = 0.0
        try:
            source_width_mm = float(self._get_piece_plan_value(piece_plan, "source_width_mm", 0.0) or 0.0)
        except Exception:
            source_width_mm = 0.0
        shared_upstream_deckle_cm = source_width_mm / 10.0 if source_width_mm > 0.0 else 0.0

        def _piece_value(piece, key, default=None):
            if isinstance(piece, dict):
                return piece.get(key, default)
            return getattr(piece, key, default)

        equal_piece_multiplier = 0.0
        if pieces:
            try:
                lengths = {
                    round(float(_piece_value(piece, "piece_length_mm", 0.0) or 0.0), 6)
                    for piece in pieces
                }
                widths = {
                    round(float(_piece_value(piece, "piece_width_mm", 0.0) or 0.0), 6)
                    for piece in pieces
                }
                # Only use this fallback for equal pieces. Uneven pieces must
                # use explicit piece-tagged RM quantities because simple
                # multiplication can overstate/understate material.
                if len(lengths) == 1 and len(widths) == 1:
                    equal_piece_multiplier = sum(
                        float(_piece_value(piece, "piece_qty_per_fg", 1.0) or 1.0)
                        for piece in pieces
                    )
            except Exception:
                equal_piece_multiplier = 0.0

        def _norm_role(value):
            role = str(value or "").strip().lower()
            return "liner" if role == "top_roll" else role

        def _layer(value):
            try:
                return int(value or 0)
            except Exception:
                return 0

        paper_by_key = {}
        for entry in list(paper_resolution or []):
            if not isinstance(entry, dict):
                continue
            role = _norm_role(entry.get("role"))
            layer_no = _layer(entry.get("layer_no"))
            if role not in ("liner", "medium") or not layer_no:
                continue
            product_id = entry.get("product_id")
            product_name = entry.get("product_name")
            if product_id and product_name:
                status = str(entry.get("status") or "resolved").strip().lower() or "resolved"
                paper_by_key[(role, layer_no)] = entry

        rm_by_key = {}
        for rm in list(getattr(result, "rm_requirements", []) or []):
            metadata = dict(getattr(rm, "metadata", {}) or {})
            role = _norm_role(metadata.get("semantic_role") or getattr(rm, "role", ""))
            layer_no = _layer(getattr(rm, "layer_no", None) or metadata.get("layer_no"))
            if role not in ("liner", "medium") or not layer_no:
                continue
            resolution = dict(metadata.get("resolution") or {})
            product_id = metadata.get("product_id") or resolution.get("product_id")
            product_name = (
                metadata.get("product_name")
                or metadata.get("resolution_product_name")
                or resolution.get("product_name")
            )
            status = str(metadata.get("resolution_status") or resolution.get("status") or "").strip().lower()
            if product_id and product_name and status != "missing":
                rm_by_key[(role, layer_no)] = {
                    "product_id": product_id,
                    "product_name": product_name,
                    "uom_id": metadata.get("uom_id") or resolution.get("uom_id"),
                    "uom_name": metadata.get("uom_name") or resolution.get("uom_name"),
                    "required_deckle_cm": metadata.get("required_deckle_cm") or resolution.get("required_deckle_cm"),
                    "status": status or "resolved",
                    "resolution_note": "Exact paper variant resolved",
                    "source": "result.rm_requirements",
                }

        source_by_key = {}
        for key, entry in paper_by_key.items():
            source_by_key[key] = {
                "product_id": entry.get("product_id"),
                "product_name": entry.get("product_name"),
                "uom_id": entry.get("uom_id"),
                "uom_name": entry.get("uom_name"),
                "required_deckle_cm": entry.get("required_deckle_cm"),
                "status": entry.get("status") or "resolved",
                "resolution_note": "Exact paper variant resolved",
                "source": "paper_resolution",
            }
        source_by_key.update(rm_by_key)
        if not source_by_key:
            return

        for row in list(preview_model.get("materials") or []):
            metadata = dict(row.get("metadata") or {})
            role = _norm_role(row.get("role") or metadata.get("semantic_role"))
            layer_no = _layer(row.get("layer_no") or metadata.get("layer_no"))
            source = source_by_key.get((role, layer_no))
            if not source:
                continue
            row["product"] = source.get("product_name") or row.get("product")
            metadata["product_id"] = source.get("product_id")
            metadata["product_name"] = source.get("product_name")
            metadata["product_label"] = source.get("product_name")
            metadata["resolution_product_name"] = source.get("product_name")
            metadata["resolution_status"] = source.get("status") or "resolved"
            metadata["resolution_note"] = source.get("resolution_note") or "Exact paper variant resolved"
            metadata["required_deckle_cm"] = source.get("required_deckle_cm")
            if source.get("product_id"):
                metadata["writer_product_key"] = "product.product:%s" % source.get("product_id")
            resolution = dict(metadata.get("resolution") or {})
            resolution.update(
                {
                    "status": source.get("status") or "resolved",
                    "product_id": source.get("product_id"),
                    "product_name": source.get("product_name"),
                    "uom_id": source.get("uom_id"),
                    "uom_name": source.get("uom_name"),
                    "required_deckle_cm": source.get("required_deckle_cm"),
                    "resolution_note": source.get("resolution_note") or "Exact paper variant resolved",
                }
            )
            metadata["resolution"] = resolution
            notes = [
                note
                for note in list(row.get("notes") or [])
                if "Exact paper variant not found" not in str(note)
            ]
            row["notes"] = notes
            row["metadata"] = metadata

        for comp in list((bom_struct or {}).get("components") or []):
            metadata = dict(comp.get("metadata") or {})
            role = _norm_role(metadata.get("type") or metadata.get("role"))
            layer_no = _layer(metadata.get("layer_no"))
            source = source_by_key.get((role, layer_no))
            if not source:
                continue
            comp["product"] = source.get("product_name") or comp.get("product")
            metadata["product_id"] = source.get("product_id")
            metadata["product_name"] = source.get("product_name")
            metadata["product_label"] = source.get("product_name")
            metadata["resolution_product_name"] = source.get("product_name")
            metadata["resolution_status"] = source.get("status") or "resolved"
            metadata["resolution_note"] = source.get("resolution_note") or "Exact paper variant resolved"
            metadata["required_deckle_cm"] = source.get("required_deckle_cm")
            if source.get("product_id"):
                metadata["writer_product_key"] = "product.product:%s" % source.get("product_id")
            resolution = dict(metadata.get("resolution") or {})
            resolution.update(
                {
                    "status": source.get("status") or "resolved",
                    "product_id": source.get("product_id"),
                    "product_name": source.get("product_name"),
                    "uom_id": source.get("uom_id"),
                    "uom_name": source.get("uom_name"),
                    "required_deckle_cm": source.get("required_deckle_cm"),
                    "resolution_note": source.get("resolution_note") or "Exact paper variant resolved",
                }
            )
            metadata["resolution"] = resolution
            comp["metadata"] = metadata

    def _apply_multi_piece_fg_rollup_to_preview_model(self, preview_model, result, order_qty):
        """Force RM Breakdown rows to FG-level quantities for multi-piece previews.

        The StageContext adapter may emit some rows from paper_resolution and
        others from unmatched legacy RM rows. This pass uses the final
        piece-tagged rm_requirements as the source of truth for display
        quantities, so every layer shows Piece A + Piece B at FG level.
        """

        if not isinstance(preview_model, dict):
            return
        piece_plan = self._piece_plan_dict(result)
        if not piece_plan or not bool(self._get_piece_plan_value(piece_plan, "enabled", False)):
            return
        pieces = list(self._get_piece_plan_value(piece_plan, "pieces", []) or [])
        strategy = str(
            self._get_piece_plan_value(piece_plan, "multi_piece_strategy", "")
            or self._get_piece_plan_value(piece_plan, "strategy", "")
            or ""
        ).strip().lower()
        try:
            source_width_mm = float(self._get_piece_plan_value(piece_plan, "source_width_mm", 0.0) or 0.0)
        except Exception:
            source_width_mm = 0.0
        shared_upstream_deckle_cm = source_width_mm / 10.0 if source_width_mm > 0.0 else 0.0

        def _piece_value(piece, key, default=None):
            if isinstance(piece, dict):
                return piece.get(key, default)
            return getattr(piece, key, default)

        equal_piece_multiplier = 0.0
        if pieces:
            try:
                lengths = {
                    round(float(_piece_value(piece, "piece_length_mm", 0.0) or 0.0), 6)
                    for piece in pieces
                }
                widths = {
                    round(float(_piece_value(piece, "piece_width_mm", 0.0) or 0.0), 6)
                    for piece in pieces
                }
                if len(lengths) == 1 and len(widths) == 1:
                    equal_piece_multiplier = sum(
                        float(_piece_value(piece, "piece_qty_per_fg", 1.0) or 1.0)
                        for piece in pieces
                    )
            except Exception:
                equal_piece_multiplier = 0.0

        def _norm_role(value):
            role = str(value or "").strip().lower()
            return "liner" if role == "top_roll" else role

        def _layer(value):
            try:
                return int(value or 0)
            except Exception:
                return 0

        def _base_stage(value):
            text = str(value or "").strip()
            if "__piece_" in text:
                return text.split("__piece_", 1)[0]
            return text

        def _uom(value):
            return str(value or "").strip().lower()

        def _product_attrs(product_text):
            text = str(product_text or "")
            attrs = {}
            if "(" not in text or ")" not in text:
                return attrs
            try:
                inside = text.split("(", 1)[-1].rsplit(")", 1)[0]
                parts = [part.strip() for part in inside.split(",")]
            except Exception:
                parts = []
            if len(parts) >= 4:
                attrs["gsm"] = parts[0]
                attrs["color"] = parts[2]
                attrs["bf"] = parts[3]
            return attrs

        rollup = {}
        for rm in list(getattr(result, "rm_requirements", []) or []):
            metadata = dict(getattr(rm, "metadata", {}) or {})
            if not metadata.get("multi_piece") and not metadata.get("piece_key"):
                continue
            role = _norm_role(metadata.get("semantic_role") or getattr(rm, "role", ""))
            if role not in ("liner", "medium", "top_sheet_external", "lamination_film"):
                continue
            layer_no = _layer(getattr(rm, "layer_no", None) or metadata.get("layer_no"))
            if not layer_no and role in ("liner", "medium"):
                continue
            key = (
                _base_stage(getattr(rm, "stage_key", "")),
                role,
                layer_no,
                _uom(getattr(rm, "qty_uom", "")),
            )
            entry = rollup.setdefault(
                key,
                {
                    "qty_per_fg": 0.0,
                    "piece_keys": [],
                    "attrs": {},
                },
            )
            try:
                entry["qty_per_fg"] += float(getattr(rm, "qty_per_fg", 0.0) or 0.0)
            except Exception:
                pass
            piece_key = str(metadata.get("piece_key") or "").strip()
            if piece_key and piece_key not in entry["piece_keys"]:
                entry["piece_keys"].append(piece_key)
            resolution = dict(metadata.get("resolution") or {})
            for attr in ("gsm", "bf", "color", "flute", "flute_code"):
                value = metadata.get(attr) or resolution.get(attr)
                if value not in (None, "") and attr not in entry["attrs"]:
                    entry["attrs"][attr] = value

        if not rollup:
            return

        try:
            order_qty_float = float(order_qty or 0.0)
        except Exception:
            order_qty_float = 0.0

        for row in list(preview_model.get("materials") or []):
            metadata = dict(row.get("metadata") or {})
            role = _norm_role(row.get("role") or metadata.get("semantic_role"))
            layer_no = _layer(row.get("layer_no") or metadata.get("layer_no"))
            if (
                strategy == "shared_upstream_split"
                and shared_upstream_deckle_cm > 0.0
                and role in ("liner", "medium")
            ):
                self._apply_shared_upstream_full_deckle_display_resolution(
                    row,
                    metadata,
                    role=role,
                    deckle_cm=shared_upstream_deckle_cm,
                )
            key = (
                _base_stage(row.get("stage_code")),
                role,
                layer_no,
                _uom(row.get("uom")),
            )
            entry = rollup.get(key)
            if not entry:
                qty_source = str(row.get("quantity_source") or "").strip().lower()
                if (
                    equal_piece_multiplier > 1.0
                    and qty_source == "paper_resolution"
                    and role in ("liner", "medium")
                    and not metadata.get("fg_level_rollup")
                ):
                    try:
                        qty_per_fg = float(row.get("qty_per_fg", 0.0) or 0.0) * equal_piece_multiplier
                    except Exception:
                        qty_per_fg = 0.0
                    if qty_per_fg > 0.0:
                        row["qty_per_fg"] = qty_per_fg
                        row["total_qty"] = qty_per_fg * order_qty_float
                        metadata["multi_piece"] = True
                        metadata["fg_level_rollup"] = True
                        metadata["fg_level_rollup_source"] = "debug_wizard_equal_piece_fallback"
                        metadata["rollup_piece_keys"] = [
                            str(_piece_value(piece, "piece_key", "") or "").strip()
                            for piece in pieces
                            if str(_piece_value(piece, "piece_key", "") or "").strip()
                        ]
                        attrs = _product_attrs(row.get("product"))
                        resolution = dict(metadata.get("resolution") or {})
                        for attr, value in attrs.items():
                            if value in (None, ""):
                                continue
                            metadata[attr] = value
                            resolution[attr] = value
                        metadata["resolution"] = resolution
                        notes = list(row.get("notes") or [])
                        if "multi-piece FG-level quantity rollup" not in notes:
                            notes.append("multi-piece FG-level quantity rollup")
                        if "equal-piece fallback" not in notes:
                            notes.append("equal-piece fallback")
                        row["notes"] = notes
                        row["metadata"] = metadata
                continue
            qty_per_fg = float(entry.get("qty_per_fg", 0.0) or 0.0)
            if qty_per_fg > 0.0:
                row["qty_per_fg"] = qty_per_fg
                row["total_qty"] = qty_per_fg * order_qty_float
            attrs = dict(entry.get("attrs") or {})
            attrs.update({k: v for k, v in _product_attrs(row.get("product")).items() if v not in (None, "")})
            resolution = dict(metadata.get("resolution") or {})
            for attr, value in attrs.items():
                if value in (None, ""):
                    continue
                metadata[attr] = value
                resolution[attr] = value
            piece_keys = list(entry.get("piece_keys") or [])
            if piece_keys:
                metadata["rollup_piece_keys"] = piece_keys
                metadata["piece_key"] = ",".join(piece_keys)
            metadata["multi_piece"] = True
            metadata["fg_level_rollup"] = True
            metadata["fg_level_rollup_source"] = "debug_wizard_rm_requirements"
            metadata["resolution"] = resolution
            notes = list(row.get("notes") or [])
            if "multi-piece FG-level quantity rollup" not in notes:
                notes.append("multi-piece FG-level quantity rollup")
            row["notes"] = notes
            row["metadata"] = metadata

    def _apply_shared_upstream_full_deckle_display_resolution(self, row, metadata, *, role, deckle_cm):
        resolution = dict(metadata.get("resolution") or {})
        gsm = metadata.get("gsm") or resolution.get("gsm")
        bf = metadata.get("bf") or resolution.get("bf")
        color = metadata.get("color") or resolution.get("color")
        flute = metadata.get("flute") or metadata.get("flute_code") or resolution.get("flute") or resolution.get("flute_code")
        resolved = self._resolve_g1_piece_paper_variant(
            role=role,
            gsm=gsm,
            bf=bf,
            color=color,
            flute=flute,
            deckle_cm=deckle_cm,
        )
        metadata["required_deckle_cm"] = deckle_cm
        resolution["required_deckle_cm"] = deckle_cm
        metadata["shared_upstream_full_deckle_display"] = True
        if resolved.get("product_id"):
            metadata["product_id"] = resolved.get("product_id")
            metadata["product_name"] = resolved.get("product_name")
            metadata["writer_product_key"] = "product.product:%s" % resolved.get("product_id")
            metadata["resolution_status"] = resolved.get("status") or "resolved"
            metadata["resolution_note"] = "Exact full-deckle shared upstream paper variant resolved"
            resolution.update(
                {
                    "product_id": resolved.get("product_id"),
                    "product_name": resolved.get("product_name"),
                    "status": resolved.get("status") or "resolved",
                    "resolution_note": "Exact full-deckle shared upstream paper variant resolved",
                }
            )
            row["product"] = resolved.get("product_name") or row.get("product")
            notes = list(row.get("notes") or [])
            if "shared-upstream full-deckle resolution" not in notes:
                notes.append("shared-upstream full-deckle resolution")
            row["notes"] = notes
        else:
            metadata.pop("product_id", None)
            metadata.pop("writer_product_key", None)
            metadata["resolution_status"] = resolved.get("status") or "missing"
            metadata["resolution_note"] = "Exact full-deckle shared upstream paper variant not found"
            resolution.pop("product_id", None)
            resolution["status"] = resolved.get("status") or "missing"
            resolution["resolution_note"] = "Exact full-deckle shared upstream paper variant not found"
        metadata["resolution"] = resolution
        row["metadata"] = metadata

    def _build_g1_resolution_reconciliation_debug(
        self,
        result,
        paper_resolution,
        bom_struct,
        preview_model,
    ):
        def _norm_role(value):
            role = str(value or "").strip().lower()
            return "liner" if role == "top_roll" else role

        def _layer(value):
            try:
                return int(value or 0)
            except Exception:
                return 0

        def _pick_metadata(metadata):
            metadata = dict(metadata or {})
            resolution = dict(metadata.get("resolution") or {})
            return {
                "product_id": metadata.get("product_id") or resolution.get("product_id"),
                "product_name": (
                    metadata.get("product_name")
                    or metadata.get("product_label")
                    or metadata.get("resolution_product_name")
                    or resolution.get("product_name")
                ),
                "resolution_status": metadata.get("resolution_status") or resolution.get("status"),
                "required_deckle_cm": metadata.get("required_deckle_cm") or resolution.get("required_deckle_cm"),
                "writer_product_key": metadata.get("writer_product_key"),
            }

        rm_rows = []
        for rm in list(getattr(result, "rm_requirements", []) or []):
            metadata = dict(getattr(rm, "metadata", {}) or {})
            role = _norm_role(metadata.get("semantic_role") or getattr(rm, "role", ""))
            if role not in ("liner", "medium"):
                continue
            rm_rows.append(
                {
                    "source": "result.rm_requirements",
                    "product_key": getattr(rm, "product_key", ""),
                    "role": role,
                    "layer_no": _layer(getattr(rm, "layer_no", None) or metadata.get("layer_no")),
                    "piece_key": metadata.get("piece_key"),
                    **_pick_metadata(metadata),
                }
            )

        paper_rows = []
        for entry in list(paper_resolution or []):
            if not isinstance(entry, dict):
                continue
            role = _norm_role(entry.get("role"))
            if role not in ("liner", "medium"):
                continue
            paper_rows.append(
                {
                    "source": "paper_resolution",
                    "role": role,
                    "layer_no": _layer(entry.get("layer_no")),
                    "product_id": entry.get("product_id"),
                    "product_name": entry.get("product_name"),
                    "resolution_status": entry.get("status"),
                    "required_deckle_cm": entry.get("required_deckle_cm"),
                }
            )

        component_rows = []
        for comp in list((bom_struct or {}).get("components") or []):
            metadata = dict(comp.get("metadata") or {})
            role = _norm_role(metadata.get("type") or metadata.get("role"))
            if role not in ("liner", "medium"):
                continue
            component_rows.append(
                {
                    "source": "bom_struct.components",
                    "role": role,
                    "layer_no": _layer(metadata.get("layer_no")),
                    "display_product": comp.get("product"),
                    **_pick_metadata(metadata),
                }
            )

        preview_rows = []
        for row in list((preview_model or {}).get("materials") or []):
            metadata = dict(row.get("metadata") or {})
            role = _norm_role(row.get("role") or metadata.get("semantic_role"))
            if role not in ("liner", "medium"):
                continue
            preview_rows.append(
                {
                    "source": "phase5_preview_model.materials",
                    "role": role,
                    "layer_no": _layer(row.get("layer_no") or metadata.get("layer_no")),
                    "display_product": row.get("product"),
                    **_pick_metadata(metadata),
                }
            )

        return {
            "enabled": bool(getattr(getattr(result, "piece_plan", None), "enabled", False)),
            "rm_requirements": rm_rows,
            "paper_resolution": paper_rows,
            "bom_struct_components": component_rows,
            "preview_model_materials": preview_rows,
        }

    def _render_raw_json(
        self,
        result,
        qgraph,
        bom_struct,
        paper_resolution=None,
        costing=None,
        shadow_runtime=None,
        spec_surface=None,
        preview_model=None,
        preview_backend_selected=None,
        preview_backend_applied=None,
        selected_layout=None,
        piece_layout_planning=None,
        multi_piece_options=None,
        consumable_state=None,
    ):
        """Render a compact JSON payload for power users."""

        def _serialize_rm(rm):
            return {
                "product_key": getattr(rm, "product_key", ""),
                "role": getattr(rm, "role", ""),
                "stage_key": getattr(rm, "stage_key", None),
                "layer_no": getattr(rm, "layer_no", None),
                "qty_per_fg": getattr(rm, "qty_per_fg", 0.0),
                "qty_uom": getattr(rm, "qty_uom", ""),
                "consumption_type": getattr(rm, "consumption_type", "area"),
                "metadata": getattr(rm, "metadata", {}) or {},
            }

        def _serialize_metrics(metrics):
            if not metrics:
                return {}
            return {
                "m2_per_box": metrics.m2_per_box,
                "kg_per_box": metrics.kg_per_box,
                "extra": metrics.extra or {},
            }

        def _serialize_qgraph(graph):
            nodes = {}
            for key, node in (getattr(graph, "nodes", {}) or {}).items():
                nodes[key] = {"qty": node.qty, "meta": node.meta or {}}
            edges = []
            for e in getattr(graph, "edges", []) or []:
                edges.append(
                    {
                        "from": e.from_node,
                        "to": e.to_node,
                        "qty_ratio": e.qty_ratio,
                        "waste_percent": e.waste_percent,
                    }
                )
            return {"nodes": nodes, "edges": edges}

        def _serialize_trace(res):
            try:
                trace_items = list(getattr(res, "trace", []) or [])
            except Exception:
                return []
            serialized = []
            for item in trace_items:
                try:
                    step, payload_step = item
                except Exception:
                    continue
                serialized.append(
                    {
                        "step": step,
                        "payload": payload_step or {},
                    }
                )
            return serialized

        def _serialize_spec_stack_lines():
            spec = self._active_spec_record()
            if not spec:
                return []
            try:
                lines = spec.stack_line_ids.sorted(lambda line: (line.sequence, line.id))
            except Exception:
                lines = getattr(spec, "stack_line_ids", []) or []
            rows = []
            idx = 1
            for line in list(lines or []):
                raw_role = (getattr(line, "role", "") or "").strip().lower()
                role = _normalize_stack_paper_role(raw_role)
                flute = getattr(line, "flute_type_id", None) or getattr(line, "flute_id", None)
                flute_code = (getattr(flute, "code", "") or getattr(flute, "name", "") or "") if flute else ""
                rows.append(
                    {
                        "sequence": int(getattr(line, "sequence", idx) or idx),
                        "layer_no": idx,
                        "role": role,
                        "raw_role": raw_role,
                        "gsm": float(getattr(line, "gsm", 0.0) or 0.0),
                        "bf": float(getattr(line, "bf", 0.0) or 0.0),
                        "color": getattr(line, "color", "") or "",
                        "flute_code": flute_code,
                        "flute": flute_code,
                    }
                )
                idx += 1
            return rows

        active_fg_tmpl = self._active_fg_product_tmpl()
        fg_product_name = (
            getattr(active_fg_tmpl, "display_name", None)
            or getattr(active_fg_tmpl, "name", None)
            or ""
        )
        payload = {
            "spec_id": getattr(result, "spec_id", None),
            "spec_model": getattr(self._active_spec_record(), "_name", ""),
            "fg_product_name": fg_product_name,
            "finished_product_name": fg_product_name,
            "style_code": getattr(result, "style_code", ""),
            "top_sheet_mode": getattr(self._active_spec_record(), "surface_top_sheet_mode", "") or "",
            "top_sheet_roll_code": getattr(self._active_spec_record(), "surface_top_sheet_roll_code", "") or "",
            "metrics": _serialize_metrics(getattr(result, "metrics", None)),
            "rm_requirements": [_serialize_rm(rm) for rm in getattr(result, "rm_requirements", []) or []],
            "spec_stack_lines": _serialize_spec_stack_lines(),
            "quantity_stage_graph": _serialize_qgraph(qgraph),
            "bom_struct": bom_struct,
            "trace": _serialize_trace(result),
            "plant_id": getattr(self.plant_id, "id", None),
            "order_qty": float(self.order_qty or 0.0),
            "paper_resolution": list(paper_resolution or []),
            "selected_layout": dict(selected_layout or {}),
            "preview_backend_selected": preview_backend_selected or "legacy",
            "preview_backend_applied": preview_backend_applied or "legacy",
            "bom_backend_selected": (self.bom_backend or "legacy"),
            "consumable_state": (
                consumable_state_signature(consumable_state)
                if consumable_state_signature is not None
                else dict(consumable_state or {})
            ),
            "consumable_debug": self._consumable_debug_metadata(consumable_state),
        }
        if shadow_runtime is not None:
            shadow_runtime = dict(shadow_runtime or {})
            shadow_runtime["consumable_state"] = payload["consumable_state"]
        piece_plan = getattr(result, "piece_plan", None)
        if piece_plan:
            try:
                effective_multi_piece_strategy = (
                    (self.multi_piece_strategy if self.multi_piece_enabled else "")
                    or getattr(piece_plan, "multi_piece_strategy", "")
                    or getattr(self.v2_spec_id, "multi_piece_strategy", "")
                    or "independent_routed_piece"
                )
                effective_split_after_stage_key = (
                    (self.multi_piece_split_after_stage_key if self.multi_piece_enabled else "")
                    or getattr(piece_plan, "split_after_stage_key", "")
                    or getattr(self.v2_spec_id, "multi_piece_split_after_stage_key", "")
                    or ""
                )
                payload["multi_piece"] = {
                    "enabled": bool(getattr(piece_plan, "enabled", False)),
                    "source": getattr(piece_plan, "source", ""),
                    "status": getattr(piece_plan, "status", ""),
                    "is_design_intent": bool(getattr(piece_plan, "is_design_intent", False)),
                    "materialization_allowed": bool(
                        getattr(piece_plan, "materialization_allowed", False)
                    ),
                    "piece_count": int(getattr(piece_plan, "piece_count", 0) or 0),
                    "multi_piece_strategy": effective_multi_piece_strategy,
                    "strategy": effective_multi_piece_strategy,
                    "split_after_stage_key": effective_split_after_stage_key,
                    "split_axis": getattr(piece_plan, "split_axis", ""),
                    "split_mode": getattr(piece_plan, "split_mode", ""),
                    "source_width_mm": getattr(piece_plan, "source_width_mm", 0.0),
                    "source_length_mm": getattr(piece_plan, "source_length_mm", 0.0),
                    "merge_stage_key": getattr(piece_plan, "merge_stage_key", ""),
                    "pieces": [
                        {
                            "piece_key": getattr(piece, "piece_key", ""),
                            "piece_name": getattr(piece, "piece_name", ""),
                            "piece_index": getattr(piece, "piece_index", 0),
                            "piece_qty_per_fg": getattr(piece, "piece_qty_per_fg", 0.0),
                            "piece_width_mm": getattr(piece, "piece_width_mm", 0.0),
                            "piece_length_mm": getattr(piece, "piece_length_mm", 0.0),
                            "stack_source": getattr(piece, "stack_source", "inherited"),
                            "board_recipe_key": getattr(piece, "board_recipe_key", ""),
                            "flute_profile_key": getattr(piece, "flute_profile_key", ""),
                            "surface_source": getattr(piece, "surface_source", "inherited"),
                            "surface_profile_key": getattr(piece, "surface_profile_key", ""),
                            "top_sheet_source": getattr(piece, "top_sheet_source", "inherited"),
                            "top_sheet_mode": getattr(piece, "top_sheet_mode", ""),
                            "top_sheet_product_key": getattr(piece, "top_sheet_product_key", ""),
                        }
                        for piece in list(getattr(piece_plan, "pieces", []) or [])
                    ],
                }
                piece_manufacturing_overrides = self._build_piece_manufacturing_override_payload(piece_plan)
                payload["piece_manufacturing_overrides"] = piece_manufacturing_overrides
                payload["effective_piece_manufacturing"] = piece_manufacturing_overrides.get("pieces") or []
                effective_piece_by_key = {
                    self._canonical_piece_key(piece.get("piece_key")): piece
                    for piece in list(piece_manufacturing_overrides.get("pieces") or [])
                }
                for piece_row in list(payload["multi_piece"].get("pieces") or []):
                    effective_piece = effective_piece_by_key.get(
                        self._canonical_piece_key(piece_row.get("piece_key"))
                    ) or {}
                    if not effective_piece:
                        continue
                    piece_row["stack_source"] = (
                        effective_piece.get("stack_source")
                        or piece_row.get("stack_source")
                        or "inherited"
                    )
                    piece_row["surface_source"] = (
                        effective_piece.get("surface_source")
                        or piece_row.get("surface_source")
                        or "inherited"
                    )
                    piece_row["top_sheet_source"] = (
                        effective_piece.get("top_sheet_source")
                        or piece_row.get("top_sheet_source")
                        or "inherited"
                    )
                    stack = dict(effective_piece.get("stack") or {})
                    surface = dict(effective_piece.get("surface") or {})
                    top_sheet = dict(effective_piece.get("top_sheet") or {})
                    piece_row["flute_profile_key"] = (
                        stack.get("effective_flute_profile")
                        or piece_row.get("flute_profile_key")
                        or ""
                    )
                    piece_row["surface_profile_key"] = (
                        surface.get("effective_surface_profile")
                        or piece_row.get("surface_profile_key")
                        or ""
                    )
                    piece_row["top_sheet_mode"] = (
                        top_sheet.get("effective_top_sheet_mode")
                        or piece_row.get("top_sheet_mode")
                        or ""
                    )
                    piece_row["top_sheet_product_key"] = (
                        top_sheet.get("effective_top_sheet_product")
                        or piece_row.get("top_sheet_product_key")
                        or ""
                    )
                payload["multi_piece_strategy"] = effective_multi_piece_strategy
                payload["multi_piece_split_after_stage_key"] = effective_split_after_stage_key
            except Exception:
                pass
        if multi_piece_options:
            options_multi_piece = dict(multi_piece_options or {})
            existing_multi_piece = dict(payload.get("multi_piece") or {})
            if not existing_multi_piece:
                existing_multi_piece = options_multi_piece
            else:
                for key, value in options_multi_piece.items():
                    if key in {
                        "multi_piece_strategy",
                        "strategy",
                        "split_after_stage_key",
                        "source",
                        "status",
                        "split_axis",
                        "split_mode",
                        "split_method",
                        "merge_stage_key",
                    }:
                        existing_multi_piece[key] = value
            if self.multi_piece_enabled:
                existing_multi_piece["enabled"] = True
                existing_multi_piece["multi_piece_strategy"] = (
                    self.multi_piece_strategy or existing_multi_piece.get("multi_piece_strategy")
                    or "independent_routed_piece"
                )
                existing_multi_piece["strategy"] = existing_multi_piece["multi_piece_strategy"]
                existing_multi_piece["split_after_stage_key"] = (
                    self.multi_piece_split_after_stage_key
                    or existing_multi_piece.get("split_after_stage_key")
                    or ""
                )
            payload["multi_piece"] = existing_multi_piece
            payload["multi_piece_strategy"] = existing_multi_piece.get("multi_piece_strategy") or existing_multi_piece.get("strategy")
            payload["multi_piece_split_after_stage_key"] = existing_multi_piece.get("split_after_stage_key") or ""
        if self.auto_split_suggestion_line_ids:
            payload["auto_split_suggestions"] = [
                {
                    "rank": line.option_rank,
                    "split_axis": line.split_axis,
                    "split_method": line.split_method,
                    "piece_a_size_mm": line.piece_a_size_mm,
                    "piece_b_size_mm": line.piece_b_size_mm,
                    "source_length_mm": line.source_length_mm,
                    "source_width_mm": line.source_width_mm,
                    "reason": line.reason,
                }
                for line in self.auto_split_suggestion_line_ids.sorted(
                    key=lambda item: (item.option_rank, item.id)
                )
            ]
        if self.auto_split_rejected_suggestions_json:
            try:
                payload["auto_split_rejected_suggestions"] = json.loads(
                    self.auto_split_rejected_suggestions_json or "[]"
                )
            except Exception:
                payload["auto_split_rejected_suggestions"] = []
        if self.layout_options_diagnostics_json:
            try:
                payload["layout_options_diagnostics"] = json.loads(
                    self.layout_options_diagnostics_json or "{}"
                )
            except Exception:
                payload["layout_options_diagnostics"] = {}
        if self.auto_split_diagnostics_json:
            try:
                payload["auto_split_diagnostics"] = json.loads(
                    self.auto_split_diagnostics_json or "{}"
                )
            except Exception:
                payload["auto_split_diagnostics"] = {}
        if piece_layout_planning:
            payload["piece_layout_planning"] = piece_layout_planning
        if preview_model and preview_model.get("piece_specific_rm_breakdown"):
            payload["piece_specific_rm_breakdown"] = preview_model.get("piece_specific_rm_breakdown")
        if costing and costing.get("piece_specific_costing"):
            payload["piece_specific_costing"] = costing.get("piece_specific_costing")
        try:
            payload["g1_resolution_reconciliation"] = (
                self._build_g1_resolution_reconciliation_debug(
                    result,
                    paper_resolution,
                    bom_struct,
                    preview_model,
                )
            )
        except Exception:  # pragma: no cover - defensive
            _logger.exception("Phase G1 resolution reconciliation debug render failed")

        multi_piece_strategy = (
            (payload.get("multi_piece") or {}).get("multi_piece_strategy")
            or (payload.get("multi_piece") or {}).get("strategy")
            or "independent_routed_piece"
        )
        multi_piece_strategy = str(multi_piece_strategy or "").strip().lower()
        payload["phase_h_enabled"] = bool(
            (payload.get("multi_piece") or {}).get("enabled")
            and multi_piece_strategy == "shared_upstream_split"
        )
        payload["h1_guardrail_status"] = "not_applicable"
        if (
            (payload.get("multi_piece") or {}).get("enabled")
            and multi_piece_strategy == "independent_routed_piece"
            and engine_v2_multi_piece_materialization
        ):
            try:
                materialization_mode = (
                    (self.env.context or {}).get("corr_materialization_dry_run_mode")
                    or "stockable"
                )
                payload = (
                    engine_v2_multi_piece_materialization.enrich_phase_g1_piece_rm_requirements(
                        payload,
                        paper_resolver=self._resolve_g1_piece_paper_variant,
                    )
                )
                g1_debug = (
                    engine_v2_multi_piece_materialization.build_phase_g1_materialization_debug_payload(
                        payload,
                        mode=materialization_mode,
                    )
                )
                materialization_plan = g1_debug.get("materialization_plan") or {}
                if (
                    engine_v2_materialization_resolver
                    and materialization_plan
                    and (g1_debug.get("guard") or {}).get("passed")
                ):
                    g1_debug["resolver_dry_run"] = (
                        engine_v2_materialization_resolver.resolve_materialization_plan_dry_run(
                            self.env,
                            materialization_plan,
                            company_id=getattr(self.env.company, "id", None),
                            fg_product_tmpl_id=getattr(self._active_fg_product_tmpl(), "id", None),
                        )
                    )
                if engine_v2_materialization_writer and materialization_plan:
                    materialization_writer_config = engine_v2_materialization_writer.writer_config_from_env(self.env)
                    resolver_dry_run = g1_debug.get("resolver_dry_run") or {}
                    g1_debug["writer_strategy"] = (
                        engine_v2_materialization_writer.build_materialization_writer_strategy(
                            resolver_dry_run,
                            materialization_plan=materialization_plan,
                            config=materialization_writer_config,
                        )
                    )
                    g1_debug["writer_implementation_plan"] = (
                        engine_v2_materialization_writer.build_guarded_writer_implementation_plan(
                            resolver_dry_run,
                            materialization_plan=materialization_plan,
                            config=materialization_writer_config,
                        )
                    )
                    g1_debug["orm_writer_payload_plan"] = (
                        engine_v2_materialization_writer.build_orm_writer_payload_plan(
                            resolver_dry_run,
                            materialization_plan=materialization_plan,
                            config=materialization_writer_config,
                            env=self.env,
                        )
                    )
                    g1_debug["guarded_orm_executor"] = (
                        engine_v2_materialization_writer.build_guarded_executor_diagnostics(
                            resolver_dry_run,
                            materialization_plan=materialization_plan,
                            config=materialization_writer_config,
                        )
                    )
                json.dumps(g1_debug)
                payload["phase_g1_multi_piece_materialization_dry_run"] = g1_debug
            except Exception:  # pragma: no cover - defensive
                _logger.exception("Phase G1 multi-piece materialization dry-run render failed")
        if spec_surface:
            payload["spec_surface"] = spec_surface

        if costing:
            try:
                order_qty = float(costing.get("order_qty", 0.0) or 0.0)
            except Exception:
                order_qty = 0.0
            try:
                total_per_fg = float(costing.get("total_cost_per_fg", 0.0) or 0.0)
            except Exception:
                total_per_fg = 0.0
            try:
                total_for_order = float(costing.get("total_cost_for_order", 0.0) or 0.0)
            except Exception:
                total_for_order = 0.0
            try:
                total_operation = float(costing.get("total_operation_cost", 0.0) or 0.0)
            except Exception:
                total_operation = 0.0
            try:
                total_production = float(
                    costing.get("total_production_cost", total_for_order + total_operation)
                    or (total_for_order + total_operation)
                )
            except Exception:
                total_production = total_for_order + total_operation

            try:
                stage_costs = dict(costing.get("stage_costs") or {})
            except Exception:
                stage_costs = {}

            try:
                payload["costing"] = {
                    "order_qty": order_qty,
                    "currency": costing.get("currency") or {},
                    "total_cost_per_fg": total_per_fg,
                    "total_cost_for_order": total_for_order,
                    "total_operation_cost": total_operation,
                    "total_production_cost": total_production,
                    "cost_source": costing.get("cost_source"),
                    "effective_cost_sources": list(costing.get("effective_cost_sources") or []),
                    "components": list(costing.get("components") or []),
                    "costing_trace": list(costing.get("costing_trace") or []),
                    "missing_cost": list(costing.get("missing_cost") or []),
                }
                if stage_costs:
                    payload["costing"]["stage_costs"] = stage_costs
                if costing.get("piece_specific_costing"):
                    payload["costing"]["piece_specific_costing"] = costing.get("piece_specific_costing")
            except Exception:
                pass

        if shadow_runtime:
            try:
                json.dumps(shadow_runtime)
                payload["phase8_shadow_runtime"] = shadow_runtime
                stage_feasibility = shadow_runtime.get("stage_feasibility")
                if stage_feasibility:
                    payload["stage_feasibility"] = stage_feasibility
                slitting_options = shadow_runtime.get("slitting_options")
                if slitting_options:
                    payload["slitting_options"] = slitting_options
            except Exception:  # pragma: no cover - defensive
                pass

        if preview_model:
            try:
                json.dumps(preview_model)
                payload["phase5_preview_model"] = preview_model
            except Exception:  # pragma: no cover - defensive
                pass

        if (
            (payload.get("multi_piece") or {}).get("enabled")
            and multi_piece_strategy == "shared_upstream_split"
            and engine_v2_multi_piece_materialization
        ):
            try:
                h1_debug = (
                    engine_v2_multi_piece_materialization.build_phase_h_shared_upstream_split_debug_payload(
                        payload,
                    )
                )
                json.dumps(h1_debug)
                payload["phase_h_shared_upstream_split_dry_run"] = h1_debug
                payload["h1_guardrail_status"] = (
                    "allowed" if (h1_debug.get("guard") or {}).get("passed") else "blocked"
                )
            except Exception as exc:  # pragma: no cover - defensive
                _logger.exception("Phase H1 shared upstream split dry-run render failed")
                payload["h1_guardrail_status"] = "error"
                payload["phase_h_shared_upstream_split_error"] = {
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                }
            try:
                materialization_mode = (
                    (self.env.context or {}).get("corr_materialization_dry_run_mode")
                    or "stockable"
                )
                h2_debug = (
                    engine_v2_multi_piece_materialization.build_phase_h2_shared_upstream_materialization_debug_payload(
                        payload,
                        mode=materialization_mode,
                        paper_resolver=self._resolve_g1_piece_paper_variant,
                    )
                )
                materialization_plan = h2_debug.get("materialization_plan") or {}
                if (
                    engine_v2_materialization_resolver
                    and materialization_plan
                    and (h2_debug.get("guard") or {}).get("passed")
                ):
                    h2_debug["resolver_dry_run"] = (
                        engine_v2_materialization_resolver.resolve_materialization_plan_dry_run(
                            self.env,
                            materialization_plan,
                            company_id=getattr(self.env.company, "id", None),
                            fg_product_tmpl_id=getattr(self._active_fg_product_tmpl(), "id", None),
                        )
                    )
                    h2_debug["candidate_summary"] = (
                        engine_v2_multi_piece_materialization.build_h2_candidate_summary(
                            materialization_plan,
                            h2_debug.get("resolver_dry_run") or {},
                        )
                    )
                json.dumps(h2_debug)
                payload["phase_h2_shared_upstream_materialization_dry_run"] = h2_debug
                if h2_debug.get("shared_upstream_flow"):
                    payload["shared_upstream_flow"] = h2_debug.get("shared_upstream_flow")
            except Exception as exc:  # pragma: no cover - defensive
                _logger.exception("Phase H2 shared upstream materialization dry-run render failed")
                payload["phase_h2_shared_upstream_materialization_error"] = {
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                }

        if shadow_runtime and preview_model and engine_v2_bom_adapter:
            try:
                stagecontext_blueprint = engine_v2_bom_adapter.build_bom_blueprint_from_stagecontext_preview_payload(
                    self.env,
                    payload,
                )
                json.dumps(stagecontext_blueprint)
                payload["phase6_stagecontext_bom_blueprint"] = stagecontext_blueprint
                payload["phase6_stagecontext_bom_struct"] = stagecontext_blueprint.get("writer_payload") or {}
            except Exception:  # pragma: no cover - defensive
                _logger.exception("Phase 6A StageContext BoM blueprint render failed")

        if shadow_runtime and preview_model and engine_v2_manufacturing_plan:
            try:
                materialization_mode = (
                    (self.env.context or {}).get("corr_materialization_dry_run_mode")
                    or "stockable"
                )
                materialization_debug = (
                    engine_v2_manufacturing_plan.build_materialization_debug_payload_from_stagecontext_preview_payload(
                        payload,
                        mode=materialization_mode,
                    )
                )
                if engine_v2_materialization_resolver:
                    materialization_debug["resolver_dry_run"] = (
                        engine_v2_materialization_resolver.resolve_materialization_plan_dry_run(
                            self.env,
                            materialization_debug.get("materialization_plan") or {},
                            company_id=getattr(self.env.company, "id", None),
                            fg_product_tmpl_id=getattr(self._active_fg_product_tmpl(), "id", None),
                        )
                    )
                if engine_v2_materialization_writer:
                    materialization_writer_config = engine_v2_materialization_writer.writer_config_from_env(self.env)
                    materialization_debug["writer_strategy"] = (
                        engine_v2_materialization_writer.build_materialization_writer_strategy(
                            materialization_debug.get("resolver_dry_run") or {},
                            materialization_plan=materialization_debug.get("materialization_plan") or {},
                            config=materialization_writer_config,
                        )
                    )
                    materialization_debug["writer_implementation_plan"] = (
                        engine_v2_materialization_writer.build_guarded_writer_implementation_plan(
                            materialization_debug.get("resolver_dry_run") or {},
                            materialization_plan=materialization_debug.get("materialization_plan") or {},
                            config=materialization_writer_config,
                        )
                    )
                    materialization_debug["orm_writer_payload_plan"] = (
                        engine_v2_materialization_writer.build_orm_writer_payload_plan(
                            materialization_debug.get("resolver_dry_run") or {},
                            materialization_plan=materialization_debug.get("materialization_plan") or {},
                            config=materialization_writer_config,
                            env=self.env,
                        )
                    )
                    orm_writer_execution_diagnostics = (
                        engine_v2_materialization_writer.build_guarded_executor_diagnostics(
                            materialization_debug.get("resolver_dry_run") or {},
                            materialization_plan=materialization_debug.get("materialization_plan") or {},
                            config=materialization_writer_config,
                        )
                    )
                    materialization_debug["guarded_orm_executor"] = orm_writer_execution_diagnostics
                    materialization_debug["orm_writer_execution_diagnostics"] = orm_writer_execution_diagnostics
                json.dumps(materialization_debug)
                payload["phase7b_materialization_dry_run"] = materialization_debug
            except Exception:  # pragma: no cover - defensive
                _logger.exception("Phase 7B MaterializationPlan dry-run render failed")

        if (
            (payload.get("multi_piece") or {}).get("enabled")
            and multi_piece_strategy == "independent_routed_piece"
            and engine_v2_multi_piece_materialization
        ):
            try:
                materialization_mode = (
                    (self.env.context or {}).get("corr_materialization_dry_run_mode")
                    or "stockable"
                )
                g2_debug = (
                    engine_v2_multi_piece_materialization.build_phase_g2_routed_piece_materialization_debug_payload(
                        payload,
                        mode=materialization_mode,
                        paper_resolver=self._resolve_g1_piece_paper_variant,
                    )
                )
                materialization_plan = g2_debug.get("materialization_plan") or {}
                if (
                    engine_v2_materialization_resolver
                    and materialization_plan
                    and (g2_debug.get("guard") or {}).get("passed")
                ):
                    g2_debug["resolver_dry_run"] = (
                        engine_v2_materialization_resolver.resolve_materialization_plan_dry_run(
                            self.env,
                            materialization_plan,
                            company_id=getattr(self.env.company, "id", None),
                            fg_product_tmpl_id=getattr(self._active_fg_product_tmpl(), "id", None),
                        )
                    )
                if engine_v2_materialization_writer and materialization_plan:
                    materialization_writer_config = engine_v2_materialization_writer.writer_config_from_env(self.env)
                    resolver_dry_run = g2_debug.get("resolver_dry_run") or {}
                    g2_debug["writer_strategy"] = (
                        engine_v2_materialization_writer.build_materialization_writer_strategy(
                            resolver_dry_run,
                            materialization_plan=materialization_plan,
                            config=materialization_writer_config,
                        )
                    )
                    g2_debug["writer_implementation_plan"] = (
                        engine_v2_materialization_writer.build_guarded_writer_implementation_plan(
                            resolver_dry_run,
                            materialization_plan=materialization_plan,
                            config=materialization_writer_config,
                        )
                    )
                json.dumps(g2_debug)
                payload["phase_g2_routed_piece_materialization_dry_run"] = g2_debug
            except Exception:  # pragma: no cover - defensive
                _logger.exception("Phase G2 routed piece materialization dry-run render failed")

        try:
            return json.dumps(payload, indent=2, sort_keys=True)
        except Exception:
            return "{}"

    def _piece_override_preview_enabled(self, result, spec):
        piece_plan = self._piece_plan_dict(result)
        if not piece_plan or not bool(self._get_piece_plan_value(piece_plan, "enabled", False)):
            return False
        strategy = str(
            self._get_piece_plan_value(piece_plan, "multi_piece_strategy", "")
            or self._get_piece_plan_value(piece_plan, "strategy", "")
            or "independent_routed_piece"
        ).strip().lower()
        if strategy != "independent_routed_piece":
            return False
        for piece_record in (self._piece_records_by_key(spec) or {}).values():
            if any(
                self._source_is_custom(getattr(piece_record, field_name, "inherited"))
                for field_name in ("stack_source", "surface_source", "top_sheet_source")
            ):
                return True
        return False

    def _normalize_piece_stack_rm_role(self, value):
        role = str(value or "").strip().lower()
        if role == "medium":
            return "medium"
        if role in {"outer_liner", "inner_liner", "center_liner", "liner"}:
            return "liner"
        return ""

    def _piece_stage_assignments(self, result):
        assignments = {}
        for rm in list(getattr(result, "rm_requirements", []) or []):
            metadata = dict(getattr(rm, "metadata", {}) or {})
            piece_key = self._canonical_piece_key(metadata.get("piece_key"))
            if not piece_key:
                continue
            role = str(getattr(rm, "role", "") or "").strip().lower()
            target = assignments.setdefault(piece_key, {})
            stage_key = getattr(rm, "stage_key", None)
            if role in {"liner", "medium"} and stage_key and "paper" not in target:
                target["paper"] = stage_key
            elif role == "top_roll" and stage_key and "top_roll" not in target:
                target["top_roll"] = stage_key
            elif role == "top_sheet_external" and stage_key and "top_sheet_external" not in target:
                target["top_sheet_external"] = stage_key
        return assignments

    def _piece_required_deckle_cm(self, piece, piece_layout_planning=None):
        piece_key = self._canonical_piece_key(
            piece.get("piece_key") if isinstance(piece, dict) else getattr(piece, "piece_key", "")
        )
        for entry in list(dict(piece_layout_planning or {}).get("pieces") or []):
            if self._canonical_piece_key((entry or {}).get("piece_key")) != piece_key:
                continue
            selected = dict((entry or {}).get("selected_option") or {})
            try:
                width_mm = float(selected.get("final_sheet_width_mm", 0.0) or 0.0)
            except Exception:
                width_mm = 0.0
            if width_mm > 0.0:
                return width_mm / 10.0
        try:
            width_mm = float(
                (piece.get("piece_width_mm") if isinstance(piece, dict) else getattr(piece, "piece_width_mm", 0.0))
                or 0.0
            )
        except Exception:
            width_mm = 0.0
        return width_mm / 10.0 if width_mm > 0.0 else 0.0

    def _piece_stack_line_flute_tuf(self, line, flute_code=None):
        line = dict(line or {})
        try:
            direct = float(line.get("flute_tuf") or 0.0)
        except Exception:
            direct = 0.0
        if direct > 0.0:
            return direct
        Flute = self.env.get("corr.flute") if hasattr(self, "env") else None
        if not Flute:
            return 0.0
        flute = False
        flute_id = line.get("flute_type_id") or line.get("flute_id")
        try:
            flute_id = int(flute_id or 0)
        except Exception:
            flute_id = 0
        if flute_id:
            try:
                flute = Flute.sudo().browse(flute_id)
            except Exception:
                flute = False
        if not flute or not getattr(flute, "id", False):
            code = str(
                flute_code
                or line.get("flute_code")
                or line.get("flute")
                or line.get("flute_type_code")
                or line.get("flute_type")
                or ""
            ).strip()
            if code.lower().startswith("flute "):
                code = code.split(None, 1)[1].strip()
            if code:
                try:
                    flute = Flute.sudo().search(["|", ("code", "=", code), ("name", "=", code)], limit=1)
                except Exception:
                    flute = False
        try:
            return float(getattr(flute, "tuf", 0.0) or 0.0) if flute else 0.0
        except Exception:
            return 0.0

    def _resolve_piece_preview_paper(self, *, role, gsm, bf, color, flute, deckle_cm):
        if role not in {"liner", "medium"}:
            return {}
        resolved = self._resolve_g1_piece_paper_variant(
            role=role,
            gsm=gsm,
            bf=bf,
            color=color,
            flute=flute if role == "medium" else None,
            deckle_cm=deckle_cm,
        )
        resolution = {
            "status": resolved.get("status") or "missing",
            "product_id": resolved.get("product_id"),
            "product_name": resolved.get("product_name") or "",
            "uom_id": resolved.get("uom_id"),
            "uom_name": resolved.get("uom_name") or "",
            "candidate_ids": list(resolved.get("candidate_ids") or []),
            "required_deckle_cm": deckle_cm,
            "resolution_note": "",
            "gsm": gsm,
            "bf": bf,
            "color": color or "",
            "flute_code": flute or "",
        }
        if resolution["product_id"] and resolution["product_name"]:
            resolution["status"] = "resolved"
            resolution["resolution_note"] = "Exact paper variant resolved"
        else:
            resolution["resolution_note"] = (
                "Exact paper variant not found for required deckle %.1f cm" % float(deckle_cm)
                if deckle_cm not in (None, 0, 0.0)
                else "Exact paper variant not found for selected layout"
            )
        return resolution

    def _build_piece_specific_rm_requirements_preview(self, result, spec, piece_layout_planning=None):
        if engine_v2_RmRequirement is None:
            return None
        if not self._piece_override_preview_enabled(result, spec):
            return None
        piece_plan = self._piece_plan_dict(result)
        pieces = list(self._get_piece_plan_value(piece_plan, "pieces", []) or [])
        if not pieces:
            return None
        piece_records = self._piece_records_by_key(spec)
        stage_assignments = self._piece_stage_assignments(result)
        rm_rows = []

        for piece_index, piece in enumerate(pieces, start=1):
            piece_key = self._canonical_piece_key(
                piece.get("piece_key") if isinstance(piece, dict) else getattr(piece, "piece_key", "")
            )
            piece_name = (
                piece.get("piece_name") if isinstance(piece, dict) else getattr(piece, "piece_name", "")
            ) or piece_key
            piece_record = piece_records.get(piece_key)
            effective = self._effective_piece_manufacturing_payload(
                piece,
                piece_record=piece_record,
                spec=spec,
                inherited_display={},
            )
            stack_source = effective.get("stack_source") or "inherited"
            surface_source = effective.get("surface_source") or "inherited"
            top_sheet_source = effective.get("top_sheet_source") or "inherited"
            recipe_signature = effective.get("recipe_signature") or ""
            top_sheet = dict(effective.get("top_sheet") or {})
            stack_lines = list((effective.get("stack") or {}).get("effective_stack_lines") or [])
            if not stack_lines:
                continue

            try:
                piece_qty_per_fg = float(
                    (piece.get("piece_qty_per_fg") if isinstance(piece, dict) else getattr(piece, "piece_qty_per_fg", 1.0))
                    or 1.0
                )
            except Exception:
                piece_qty_per_fg = 1.0
            try:
                piece_length_mm = float(
                    (piece.get("piece_length_mm") if isinstance(piece, dict) else getattr(piece, "piece_length_mm", 0.0))
                    or 0.0
                )
            except Exception:
                piece_length_mm = 0.0
            try:
                piece_width_mm = float(
                    (piece.get("piece_width_mm") if isinstance(piece, dict) else getattr(piece, "piece_width_mm", 0.0))
                    or 0.0
                )
            except Exception:
                piece_width_mm = 0.0
            if piece_length_mm <= 0.0 or piece_width_mm <= 0.0:
                continue

            area_m2 = (piece_length_mm * piece_width_mm / 1000000.0) * piece_qty_per_fg
            deckle_cm = self._piece_required_deckle_cm(piece, piece_layout_planning=piece_layout_planning)
            top_sheet_mode = str(top_sheet.get("effective_top_sheet_mode") or "").strip().lower()
            unit_sheet_mode = top_sheet_mode == "unit_sheet"
            roll_top_mode = top_sheet_mode == "roll"

            top_liner_index = None
            top_liner_line = None
            for idx, line in enumerate(stack_lines, start=1):
                if self._normalize_piece_stack_rm_role((line or {}).get("role")) == "liner":
                    top_liner_index = idx
                    top_liner_line = dict(line or {})
                    break

            stage_map = stage_assignments.get(piece_key, {})
            paper_stage_key = stage_map.get("paper")
            top_roll_stage_key = stage_map.get("top_roll") or paper_stage_key
            top_sheet_stage_key = stage_map.get("top_sheet_external") or paper_stage_key

            for idx, line in enumerate(stack_lines, start=1):
                line = dict(line or {})
                base_role = self._normalize_piece_stack_rm_role(line.get("role"))
                if base_role not in {"liner", "medium"}:
                    continue
                if roll_top_mode and top_liner_index is not None and idx == top_liner_index:
                    continue
                try:
                    gsm = float(line.get("gsm") or 0.0)
                except Exception:
                    gsm = 0.0
                if gsm <= 0.0:
                    continue
                try:
                    bf = float(line.get("bf")) if line.get("bf") not in (None, "") else None
                except Exception:
                    bf = None
                color = str(line.get("color") or "")
                flute = str(
                    line.get("flute_code")
                    or line.get("flute")
                    or line.get("flute_type_code")
                    or line.get("flute_type")
                    or ""
                )
                try:
                    tuf = float(line.get("flute_tuf") or 0.0) if base_role == "medium" else 1.0
                except Exception:
                    tuf = 0.0 if base_role == "medium" else 1.0
                if base_role == "medium" and tuf <= 0.0:
                    tuf = self._piece_stack_line_flute_tuf(line, flute)
                if base_role == "medium" and tuf <= 0.0:
                    tuf = 1.0
                qty_kg = (gsm * area_m2 * (tuf if base_role == "medium" else 1.0)) / 1000.0
                if qty_kg <= 0.0:
                    continue
                effective_layer_no = idx + 1 if unit_sheet_mode else idx
                resolution = self._resolve_piece_preview_paper(
                    role=base_role,
                    gsm=gsm,
                    bf=bf,
                    color=color,
                    flute=flute,
                    deckle_cm=deckle_cm,
                )
                metadata = {
                    "multi_piece": True,
                    "piece_key": piece_key,
                    "piece_index": piece_index,
                    "piece_name": piece_name,
                    "stack_source": stack_source,
                    "surface_source": surface_source,
                    "top_sheet_source": top_sheet_source,
                    "recipe_signature": recipe_signature,
                    "gsm": gsm,
                    "bf": bf,
                    "color": color,
                    "flute_code": flute,
                    "layer_no": effective_layer_no,
                    "source_layer_no": idx,
                    "type": base_role,
                    "role": line.get("role") or base_role,
                    "unit_top_sheet_internal_layer_shift": bool(unit_sheet_mode),
                    "tuf_applied": tuf if base_role == "medium" else 1.0,
                    "required_deckle_cm": deckle_cm,
                    "resolution": resolution,
                    "resolution_status": resolution.get("status"),
                    "resolution_product_name": resolution.get("product_name") or "",
                    "resolution_note": resolution.get("resolution_note") or "",
                    "product_id": resolution.get("product_id"),
                    "product_name": resolution.get("product_name") or "",
                    "uom_id": resolution.get("uom_id"),
                    "uom_name": resolution.get("uom_name") or "",
                }
                if resolution.get("product_id"):
                    metadata["writer_product_key"] = "product.product:%s" % resolution.get("product_id")
                rm_rows.append(
                    engine_v2_RmRequirement(
                        product_key="rm_medium" if base_role == "medium" else "rm_liner",
                        role=base_role,
                        stage_key=paper_stage_key,
                        layer_no=effective_layer_no,
                        qty_per_fg=qty_kg,
                        qty_uom="kg",
                        consumption_type="area",
                        metadata=metadata,
                    )
                )

            if roll_top_mode and top_liner_line:
                try:
                    gsm = float(top_liner_line.get("gsm") or 0.0)
                except Exception:
                    gsm = 0.0
                if gsm > 0.0:
                    try:
                        bf = float(top_liner_line.get("bf")) if top_liner_line.get("bf") not in (None, "") else None
                    except Exception:
                        bf = None
                    color = str(top_liner_line.get("color") or "")
                    qty_kg = (gsm * area_m2) / 1000.0
                    resolution = self._resolve_piece_preview_paper(
                        role="liner",
                        gsm=gsm,
                        bf=bf,
                        color=color,
                        flute="",
                        deckle_cm=deckle_cm,
                    )
                    metadata = {
                        "multi_piece": True,
                        "piece_key": piece_key,
                        "piece_index": piece_index,
                        "piece_name": piece_name,
                        "stack_source": stack_source,
                        "surface_source": surface_source,
                        "top_sheet_source": top_sheet_source,
                        "recipe_signature": recipe_signature,
                        "gsm": gsm,
                        "bf": bf,
                        "color": color,
                        "layer_no": top_liner_index,
                        "source_layer_no": top_liner_index,
                        "type": "liner",
                        "role": top_liner_line.get("role") or "outer_liner",
                        "kind": "top_roll",
                        "top_sheet_mode": "roll",
                        "required_deckle_cm": deckle_cm,
                        "resolution": resolution,
                        "resolution_status": resolution.get("status"),
                        "resolution_product_name": resolution.get("product_name") or "",
                        "resolution_note": resolution.get("resolution_note") or "",
                        "product_id": resolution.get("product_id"),
                        "product_name": resolution.get("product_name") or "",
                        "uom_id": resolution.get("uom_id"),
                        "uom_name": resolution.get("uom_name") or "",
                    }
                    if resolution.get("product_id"):
                        metadata["writer_product_key"] = "product.product:%s" % resolution.get("product_id")
                    rm_rows.append(
                        engine_v2_RmRequirement(
                            product_key="rm_top_roll",
                            role="top_roll",
                            stage_key=top_roll_stage_key,
                            layer_no=top_liner_index,
                            qty_per_fg=qty_kg,
                            qty_uom="kg",
                            consumption_type="area",
                            metadata=metadata,
                        )
                    )

            if unit_sheet_mode:
                top_sheet_product_id = top_sheet.get("effective_top_sheet_product_id")
                top_sheet_product_name = (
                    dict(effective.get("top_sheet") or {}).get("effective_top_sheet_product")
                    or dict(effective.get("effective_top_sheet_product") or {}).get("name")
                    or ""
                )
                rm_rows.append(
                    engine_v2_RmRequirement(
                        product_key="rm_top_sheet_external",
                        role="top_sheet_external",
                        stage_key=top_sheet_stage_key,
                        layer_no=1,
                        qty_per_fg=piece_qty_per_fg,
                        qty_uom="unit",
                        consumption_type="unit",
                        metadata={
                            "multi_piece": True,
                            "piece_key": piece_key,
                            "piece_index": piece_index,
                            "piece_name": piece_name,
                            "stack_source": stack_source,
                            "surface_source": surface_source,
                            "top_sheet_source": top_sheet_source,
                            "recipe_signature": recipe_signature,
                            "layer_no": 1,
                            "type": "top_sheet_external",
                            "role": "top_sheet_external",
                            "kind": "top_sheet_external",
                            "top_sheet_mode": "unit_sheet",
                            "top_sheet_product_id": top_sheet_product_id,
                            "top_sheet_product_name": top_sheet_product_name,
                            "resolution_status": "resolved" if top_sheet_product_id else "missing",
                            "resolution_product_name": top_sheet_product_name,
                            "product_id": top_sheet_product_id,
                            "product_name": top_sheet_product_name,
                            "writer_product_key": "product.product:%s" % top_sheet_product_id
                            if top_sheet_product_id
                            else "",
                        },
                    )
                )

        for rm in list(getattr(result, "rm_requirements", []) or []):
            metadata = dict(getattr(rm, "metadata", {}) or {})
            role = getattr(rm, "role", "") or ""
            product_key = getattr(rm, "product_key", "") or ""
            try:
                keep_consumable = bool(
                    is_consumable_role
                    and is_consumable_role(role, product_key=product_key, metadata=metadata)
                )
            except Exception:
                keep_consumable = False
            if keep_consumable:
                rm_rows.append(rm)

        return rm_rows or None

    def _build_piece_specific_rm_breakdown_payload(self, result, order_qty):
        if not self._piece_override_preview_enabled(result, self._active_spec_record()):
            return None
        rows = []
        rollup = {}
        for rm in list(getattr(result, "rm_requirements", []) or []):
            metadata = dict(getattr(rm, "metadata", {}) or {})
            piece_key = self._canonical_piece_key(metadata.get("piece_key"))
            if not piece_key:
                continue
            row = {
                "piece_key": piece_key,
                "piece_index": metadata.get("piece_index"),
                "piece_name": metadata.get("piece_name") or piece_key,
                "stack_source": metadata.get("stack_source") or "inherited",
                "recipe_signature": metadata.get("recipe_signature") or "",
                "product": metadata.get("product_name")
                or metadata.get("resolution_product_name")
                or metadata.get("top_sheet_product_name")
                or getattr(rm, "product_key", ""),
                "role": getattr(rm, "role", "") or "",
                "stage_code": getattr(rm, "stage_key", "") or "",
                "layer_no": getattr(rm, "layer_no", None) or metadata.get("layer_no"),
                "source_layer_no": metadata.get("source_layer_no"),
                "gsm": metadata.get("gsm"),
                "bf": metadata.get("bf"),
                "color": metadata.get("color") or "",
                "flute": metadata.get("flute_code") or "",
                "qty_per_fg": float(getattr(rm, "qty_per_fg", 0.0) or 0.0),
                "total_qty": float(getattr(rm, "qty_per_fg", 0.0) or 0.0) * float(order_qty or 0.0),
                "uom": getattr(rm, "qty_uom", "") or "",
                "quantity_source": "piece_effective_recipe",
                "notes": [metadata.get("resolution_note")] if metadata.get("resolution_note") else [],
                "metadata": metadata,
            }
            rows.append(row)
            identity = (
                str(row.get("product") or ""),
                str(row.get("role") or ""),
                int(row.get("layer_no") or 0),
                str(row.get("stage_code") or ""),
                str(row.get("uom") or ""),
            )
            target = rollup.setdefault(
                identity,
                {
                    **{k: row.get(k) for k in ("product", "role", "layer_no", "source_layer_no", "gsm", "bf", "color", "flute", "stage_code", "uom")},
                    "qty_per_fg": 0.0,
                    "total_qty": 0.0,
                    "piece_keys": [],
                    "quantity_source": "fg_rollup_from_piece_effective_recipe",
                    "notes": ["FG rollup"],
                },
            )
            target["qty_per_fg"] += row["qty_per_fg"]
            target["total_qty"] += row["total_qty"]
            if piece_key not in target["piece_keys"]:
                target["piece_keys"].append(piece_key)
        if not rows:
            return None
        rows.sort(key=lambda item: (item.get("piece_index") or 0, item.get("layer_no") or 0, item.get("role") or ""))
        fg_rollup_rows = list(rollup.values())
        fg_rollup_rows.sort(key=lambda item: (item.get("layer_no") or 0, item.get("role") or "", item.get("product") or ""))
        return {
            "enabled": True,
            "rows": rows,
            "fg_rollup_rows": fg_rollup_rows,
        }

    def _build_piece_specific_costing_payload(self, costing):
        if not costing:
            return None
        components = list(costing.get("components") or [])
        piece_rows = [row for row in components if str(row.get("piece_key") or "").strip()]
        if not piece_rows:
            return None

        grouped = {}
        for row in piece_rows:
            piece_key = str(row.get("piece_key") or "").strip()
            target = grouped.setdefault(
                piece_key,
                {
                    "piece_key": piece_key,
                    "piece_index": row.get("piece_index"),
                    "piece_name": row.get("piece_name") or piece_key,
                    "stack_source": row.get("stack_source") or "inherited",
                    "recipe_signature": row.get("recipe_signature") or "",
                    "material_cost_per_fg": 0.0,
                    "material_cost_for_order": 0.0,
                    "components": [],
                },
            )
            target["material_cost_per_fg"] += float(row.get("cost_per_fg", 0.0) or 0.0)
            target["material_cost_for_order"] += float(row.get("cost_for_order", 0.0) or 0.0)
            target["components"].append(dict(row))

        pieces = list(grouped.values())
        pieces.sort(key=lambda item: (item.get("piece_index") or 0, item.get("piece_key") or ""))
        return {
            "enabled": True,
            "pieces": pieces,
            "fg_rollup": {
                "material_cost_per_fg": float(costing.get("total_cost_per_fg", 0.0) or 0.0),
                "material_cost_for_order": float(costing.get("total_cost_for_order", 0.0) or 0.0),
            },
        }



