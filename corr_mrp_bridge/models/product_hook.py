import json
import logging
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


def _corr_normalize_import_header(header):
    """Normalize import headers so business aliases can match forgivingly."""
    if not isinstance(header, str):
        return False
    normalized = re.sub(r"[_\-/]+", " ", header.strip().lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _corr_product_import_alias_pairs():
    pairs = [
        ("Product Code", "default_code"),
        ("Product Name", "name"),
        ("Style / FEFCO", "corr_style"),
        ("Style FEFCO", "corr_style"),
        ("Style Code", "corr_style"),
        ("FEFCO Style", "corr_style"),
        ("FEFCO", "corr_style"),
        ("corr_style_code", "corr_style"),
        ("Sheet Length (mm)", "corr_length_mm"),
        ("Length (mm)", "corr_length_mm"),
        ("Sheet Width (mm)", "corr_width_mm"),
        ("Width (mm)", "corr_width_mm"),
        ("Sheet Height (mm)", "corr_height_mm"),
        ("Height (mm)", "corr_height_mm"),
        ("Ply Count", "corr_ply_count"),
        ("Plant", "corr_plant_code"),
        ("Plant Code", "corr_plant_code"),
        ("Routing Profile", "corr_routing_profile_code"),
        ("Routing Profile Code", "corr_routing_profile_code"),
        ("Board Recipe", "corr_board_recipe_code"),
        ("Board Recipe Code", "corr_board_recipe_code"),
        ("FG per Sheet", "corr_fg_per_sheet"),
        ("FG / Sheet", "corr_fg_per_sheet"),
        ("Finished Goods per Sheet", "corr_fg_per_sheet"),
        ("Forced UPS", "corr_forced_ups"),
        ("Top Sheet Mode", "corr_top_layer_mode"),
        ("Top Layer Mode", "corr_top_layer_mode"),
        ("Top Sheet Product Code", "corr_top_sheet_code"),
        ("Top Sheet Code", "corr_top_sheet_code"),
        ("Top Sheet Roll Code", "corr_top_sheet_roll_code"),
        ("Dimensions Mode", "corr_dimensions_mode"),
        ("Printing UPS", "corr_printing_ups"),
        ("Liner Slitting UPS", "corr_liner_slitting_ups"),
        ("Sheet Waste %", "corr_sheet_waste_pct"),
        ("Setup Sheets", "corr_setup_sheets"),
        ("Pack Size", "corr_pack_size"),
        ("Round To Pack", "corr_round_to_pack"),
        ("Print Band (mm)", "corr_print_band_mm"),
        ("Adhesive Factor Top Sheet", "corr_adhesive_factor_top_sheet"),
        ("Printing Required", "corr_printing_required"),
        ("Print Colors", "corr_print_colors"),
        ("Coverage %", "corr_coverage_pct"),
        ("Lamination Code", "corr_lamination_code"),
        ("Locking Code", "corr_locking_code"),
        ("Category", "import_categ_name"),
        ("UoM", "import_uom_name"),
        ("Sales Tax Rate", "sales_tax_rate"),
        ("Sales Tax Price Included", "sales_tax_price_included"),
        ("Purchase Tax Rate", "purchase_tax_rate"),
        ("Purchase Tax Price Included", "purchase_tax_price_included"),
        ("Diecut Codes", "diecut_codes"),
        ("Skip Auto BoM", "corr_autobom_skip"),
    ]
    for layer in range(1, 10):
        pairs.extend(
            [
                (f"Layer {layer} Role", f"corr_layer{layer}_role"),
                (f"Layer {layer} GSM", f"corr_layer{layer}_gsm"),
                (f"Layer {layer} BF", f"corr_layer{layer}_bf"),
                (f"Layer {layer} Color", f"corr_layer{layer}_color"),
                (f"Layer {layer} Material", f"corr_layer{layer}_material_template"),
                (f"Layer {layer} Material Template", f"corr_layer{layer}_material_template"),
                (f"corr_layer{layer}_material", f"corr_layer{layer}_material_template"),
                (f"Layer {layer} Flute", f"corr_layer{layer}_flute_code"),
                (f"Layer {layer} Flute Code", f"corr_layer{layer}_flute_code"),
                (f"corr_layer{layer}_flute", f"corr_layer{layer}_flute_code"),
            ]
        )
    return pairs


CORR_PRODUCT_IMPORT_ALIAS_MAP = {
    _corr_normalize_import_header(alias): field
    for alias, field in _corr_product_import_alias_pairs()
}


class ProductTemplateCorr(models.Model):
    _inherit = "product.template"

    # Import-friendly corrugation fields (kept out of default views; used by auto-BoM)
    corr_style = fields.Char(string="Corr Style")
    corr_length_mm = fields.Float(digits=(16, 3))
    corr_width_mm = fields.Float(digits=(16, 3))
    corr_height_mm = fields.Float(digits=(16, 3))
    corr_ply_count = fields.Integer()
    corr_printing_required = fields.Boolean()
    corr_print_colors = fields.Integer()
    corr_coverage_pct = fields.Float(digits=(16, 2))
    corr_lamination_code = fields.Char()
    corr_locking_code = fields.Char()

    # BMC/top-sheet options for bulk import (mapped to spec.extra_params_json)
    corr_top_layer_mode = fields.Char(string="Top Layer Mode (Roll|Unit)")
    corr_dimensions_mode = fields.Char(string="Dimensions Mode (sheet|per_box)")
    corr_printing_ups = fields.Integer(string="Printing UPS")
    corr_liner_slitting_ups = fields.Integer(string="Liner Slitting UPS")
    corr_sheet_waste_pct = fields.Float(string="Top-sheet Waste %")
    corr_setup_sheets = fields.Integer(string="Setup Sheets")
    corr_pack_size = fields.Integer(string="Pack Size")
    corr_round_to_pack = fields.Char(string="Round To Pack (Yes/No)")
    corr_print_band_mm = fields.Float(string="Print Band (mm)")
    corr_adhesive_factor_top_sheet = fields.Float(string="Adhesive Factor (Top Sheet)")
    # Optional: bind top sheet SKU by code or direct M2O
    corr_top_sheet_code = fields.Char(string="Top Sheet Code (default_code)")
    corr_top_sheet_roll_code = fields.Char(string="Top Sheet Roll Code")
    corr_top_sheet_product_id = fields.Many2one("product.product", string="Top Sheet Product")
    corr_plant_code = fields.Char(string="Plant Code")
    corr_routing_profile_code = fields.Char(string="Routing Profile Code")
    corr_board_recipe_code = fields.Char(string="Board Recipe Code")
    corr_fg_per_sheet = fields.Integer(string="FG per Sheet")
    corr_forced_ups = fields.Integer(string="Forced UPS")
    corr_v2_multi_piece_enabled = fields.Boolean(string="Engine V2 Multi-Piece")
    corr_v2_multi_piece_strategy = fields.Char(string="Engine V2 Multi-Piece Strategy")
    corr_v2_multi_piece_split_axis = fields.Char(string="Engine V2 Multi-Piece Split Axis")
    corr_v2_multi_piece_split_mode = fields.Char(string="Engine V2 Multi-Piece Split Mode")
    corr_v2_multi_piece_source_length_mm = fields.Float(string="Engine V2 Multi-Piece Source Length")
    corr_v2_multi_piece_source_width_mm = fields.Float(string="Engine V2 Multi-Piece Source Width")
    corr_v2_multi_piece_merge_stage_key = fields.Char(string="Engine V2 Multi-Piece Merge Stage")
    corr_v2_piece1_code = fields.Char(string="Engine V2 Piece 1 Code")
    corr_v2_piece1_name = fields.Char(string="Engine V2 Piece 1 Name")
    corr_v2_piece1_length_mm = fields.Float(string="Engine V2 Piece 1 Length")
    corr_v2_piece1_width_mm = fields.Float(string="Engine V2 Piece 1 Width")
    corr_v2_piece1_qty_per_fg = fields.Float(string="Engine V2 Piece 1 Qty per FG")
    corr_v2_piece1_top_sheet_mode = fields.Char(string="Engine V2 Piece 1 Top Sheet Mode")
    corr_v2_piece1_top_sheet_code = fields.Char(string="Engine V2 Piece 1 Top Sheet Code")
    corr_v2_piece2_code = fields.Char(string="Engine V2 Piece 2 Code")
    corr_v2_piece2_name = fields.Char(string="Engine V2 Piece 2 Name")
    corr_v2_piece2_length_mm = fields.Float(string="Engine V2 Piece 2 Length")
    corr_v2_piece2_width_mm = fields.Float(string="Engine V2 Piece 2 Width")
    corr_v2_piece2_qty_per_fg = fields.Float(string="Engine V2 Piece 2 Qty per FG")
    corr_v2_piece2_top_sheet_mode = fields.Char(string="Engine V2 Piece 2 Top Sheet Mode")
    corr_v2_piece2_top_sheet_code = fields.Char(string="Engine V2 Piece 2 Top Sheet Code")

    # Up to 9 layers (extendable later)
    corr_layer1_role = fields.Selection([("liner", "Liner"), ("medium", "Medium")])
    corr_layer1_gsm = fields.Float(digits=(16, 3))
    corr_layer1_bf = fields.Float(digits=(16, 3))
    corr_layer1_flute_code = fields.Char()
    corr_layer1_material_template = fields.Char()
    corr_layer1_color = fields.Char()
    corr_layer2_role = fields.Selection([("liner", "Liner"), ("medium", "Medium")])
    corr_layer2_gsm = fields.Float(digits=(16, 3))
    corr_layer2_bf = fields.Float(digits=(16, 3))
    corr_layer2_flute_code = fields.Char()
    corr_layer2_material_template = fields.Char()
    corr_layer2_color = fields.Char()
    corr_layer3_role = fields.Selection([("liner", "Liner"), ("medium", "Medium")])
    corr_layer3_gsm = fields.Float(digits=(16, 3))
    corr_layer3_bf = fields.Float(digits=(16, 3))
    corr_layer3_flute_code = fields.Char()
    corr_layer3_material_template = fields.Char()
    corr_layer3_color = fields.Char()
    corr_layer4_role = fields.Selection([("liner", "Liner"), ("medium", "Medium")])
    corr_layer4_gsm = fields.Float(digits=(16, 3))
    corr_layer4_bf = fields.Float(digits=(16, 3))
    corr_layer4_flute_code = fields.Char()
    corr_layer4_material_template = fields.Char()
    corr_layer4_color = fields.Char()
    corr_layer5_role = fields.Selection([("liner", "Liner"), ("medium", "Medium")])
    corr_layer5_gsm = fields.Float(digits=(16, 3))
    corr_layer5_bf = fields.Float(digits=(16, 3))
    corr_layer5_flute_code = fields.Char()
    corr_layer5_material_template = fields.Char()
    corr_layer5_color = fields.Char()
    corr_layer6_role = fields.Selection([("liner", "Liner"), ("medium", "Medium")])
    corr_layer6_gsm = fields.Float(digits=(16, 3))
    corr_layer6_bf = fields.Float(digits=(16, 3))
    corr_layer6_flute_code = fields.Char()
    corr_layer6_material_template = fields.Char()
    corr_layer6_color = fields.Char()
    corr_layer7_role = fields.Selection([("liner", "Liner"), ("medium", "Medium")])
    corr_layer7_gsm = fields.Float(digits=(16, 3))
    corr_layer7_bf = fields.Float(digits=(16, 3))
    corr_layer7_flute_code = fields.Char()
    corr_layer7_material_template = fields.Char()
    corr_layer7_color = fields.Char()
    corr_layer8_role = fields.Selection([("liner", "Liner"), ("medium", "Medium")])
    corr_layer8_gsm = fields.Float(digits=(16, 3))
    corr_layer8_bf = fields.Float(digits=(16, 3))
    corr_layer8_flute_code = fields.Char()
    corr_layer8_material_template = fields.Char()
    corr_layer8_color = fields.Char()
    corr_layer9_role = fields.Selection([("liner", "Liner"), ("medium", "Medium")])
    corr_layer9_gsm = fields.Float(digits=(16, 3))
    corr_layer9_bf = fields.Float(digits=(16, 3))
    corr_layer9_flute_code = fields.Char()
    corr_layer9_material_template = fields.Char()
    corr_layer9_color = fields.Char()

    # Engine V2 multi-piece custom stack upload fields.
    corr_v2_piece1_layer1_role = fields.Char()
    corr_v2_piece1_layer1_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer1_bf = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer1_flute_code = fields.Char()
    corr_v2_piece1_layer1_material_template = fields.Char()
    corr_v2_piece1_layer1_color = fields.Char()
    corr_v2_piece1_layer2_role = fields.Char()
    corr_v2_piece1_layer2_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer2_bf = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer2_flute_code = fields.Char()
    corr_v2_piece1_layer2_material_template = fields.Char()
    corr_v2_piece1_layer2_color = fields.Char()
    corr_v2_piece1_layer3_role = fields.Char()
    corr_v2_piece1_layer3_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer3_bf = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer3_flute_code = fields.Char()
    corr_v2_piece1_layer3_material_template = fields.Char()
    corr_v2_piece1_layer3_color = fields.Char()
    corr_v2_piece1_layer4_role = fields.Char()
    corr_v2_piece1_layer4_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer4_bf = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer4_flute_code = fields.Char()
    corr_v2_piece1_layer4_material_template = fields.Char()
    corr_v2_piece1_layer4_color = fields.Char()
    corr_v2_piece1_layer5_role = fields.Char()
    corr_v2_piece1_layer5_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer5_bf = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer5_flute_code = fields.Char()
    corr_v2_piece1_layer5_material_template = fields.Char()
    corr_v2_piece1_layer5_color = fields.Char()
    corr_v2_piece1_layer6_role = fields.Char()
    corr_v2_piece1_layer6_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer6_bf = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer6_flute_code = fields.Char()
    corr_v2_piece1_layer6_material_template = fields.Char()
    corr_v2_piece1_layer6_color = fields.Char()
    corr_v2_piece1_layer7_role = fields.Char()
    corr_v2_piece1_layer7_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer7_bf = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer7_flute_code = fields.Char()
    corr_v2_piece1_layer7_material_template = fields.Char()
    corr_v2_piece1_layer7_color = fields.Char()
    corr_v2_piece1_layer8_role = fields.Char()
    corr_v2_piece1_layer8_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer8_bf = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer8_flute_code = fields.Char()
    corr_v2_piece1_layer8_material_template = fields.Char()
    corr_v2_piece1_layer8_color = fields.Char()
    corr_v2_piece1_layer9_role = fields.Char()
    corr_v2_piece1_layer9_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer9_bf = fields.Float(digits=(16, 3))
    corr_v2_piece1_layer9_flute_code = fields.Char()
    corr_v2_piece1_layer9_material_template = fields.Char()
    corr_v2_piece1_layer9_color = fields.Char()
    corr_v2_piece2_layer1_role = fields.Char()
    corr_v2_piece2_layer1_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer1_bf = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer1_flute_code = fields.Char()
    corr_v2_piece2_layer1_material_template = fields.Char()
    corr_v2_piece2_layer1_color = fields.Char()
    corr_v2_piece2_layer2_role = fields.Char()
    corr_v2_piece2_layer2_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer2_bf = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer2_flute_code = fields.Char()
    corr_v2_piece2_layer2_material_template = fields.Char()
    corr_v2_piece2_layer2_color = fields.Char()
    corr_v2_piece2_layer3_role = fields.Char()
    corr_v2_piece2_layer3_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer3_bf = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer3_flute_code = fields.Char()
    corr_v2_piece2_layer3_material_template = fields.Char()
    corr_v2_piece2_layer3_color = fields.Char()
    corr_v2_piece2_layer4_role = fields.Char()
    corr_v2_piece2_layer4_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer4_bf = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer4_flute_code = fields.Char()
    corr_v2_piece2_layer4_material_template = fields.Char()
    corr_v2_piece2_layer4_color = fields.Char()
    corr_v2_piece2_layer5_role = fields.Char()
    corr_v2_piece2_layer5_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer5_bf = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer5_flute_code = fields.Char()
    corr_v2_piece2_layer5_material_template = fields.Char()
    corr_v2_piece2_layer5_color = fields.Char()
    corr_v2_piece2_layer6_role = fields.Char()
    corr_v2_piece2_layer6_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer6_bf = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer6_flute_code = fields.Char()
    corr_v2_piece2_layer6_material_template = fields.Char()
    corr_v2_piece2_layer6_color = fields.Char()
    corr_v2_piece2_layer7_role = fields.Char()
    corr_v2_piece2_layer7_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer7_bf = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer7_flute_code = fields.Char()
    corr_v2_piece2_layer7_material_template = fields.Char()
    corr_v2_piece2_layer7_color = fields.Char()
    corr_v2_piece2_layer8_role = fields.Char()
    corr_v2_piece2_layer8_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer8_bf = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer8_flute_code = fields.Char()
    corr_v2_piece2_layer8_material_template = fields.Char()
    corr_v2_piece2_layer8_color = fields.Char()
    corr_v2_piece2_layer9_role = fields.Char()
    corr_v2_piece2_layer9_gsm = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer9_bf = fields.Float(digits=(16, 3))
    corr_v2_piece2_layer9_flute_code = fields.Char()
    corr_v2_piece2_layer9_material_template = fields.Char()
    corr_v2_piece2_layer9_color = fields.Char()

    # Diecut mapping (optional)
    diecut_ids = fields.Many2many("corr.diecut", string="Diecuts")
    primary_diecut_id = fields.Many2one("corr.diecut", string="Primary Diecut")
    diecut_codes = fields.Char(string="Diecut Codes (CSV)")

    corr_autobom_skip = fields.Boolean(string="Skip Auto BoM")
    # Import helpers (optional): set by CSV and mapped to actual M2O before create
    import_categ_name = fields.Char(string="Category (by name)")
    import_uom_name = fields.Char(string="UoM (by name)")

    # Optional import helpers for taxes by percentage
    sales_tax_rate = fields.Char(string="Sales Tax Rate (e.g., 10 or 10%)")
    purchase_tax_rate = fields.Char(string="Purchase Tax Rate (e.g., 10 or 10%)")
    sales_tax_price_included = fields.Char(string="Sales Tax Price Included? Yes/No")
    purchase_tax_price_included = fields.Char(string="Purchase Tax Price Included? Yes/No")
    corr_autobom_status = fields.Selection(
        [
            ("idle", "Idle"),
            ("success", "Success"),
            ("failed", "Failed"),
            ("validation_failed", "Validation Failed"),
            ("spec_upsert_failed", "Spec Upsert Failed"),
            ("preview_failed", "Preview Failed"),
            ("material_resolution_failed", "Material Resolution Failed"),
            ("bom_materialization_failed", "BoM Materialization Failed"),
            ("bom_write_failed", "BoM Write Failed"),
        ],
        string="Corr Auto BoM Status",
        default="idle",
        copy=False,
    )
    corr_autobom_backend = fields.Selection(
        [
            ("legacy", "Legacy"),
            ("v2", "Engine V2"),
        ],
        string="Corr Auto BoM Backend",
        copy=False,
    )
    corr_autobom_message = fields.Text(
        string="Corr Auto BoM Message",
        copy=False,
    )
    corr_autobom_last_run_at = fields.Datetime(
        string="Corr Auto BoM Last Run At",
        copy=False,
    )
    corr_autobom_error_stage = fields.Selection(
        [
            ("validation", "Validation"),
            ("spec_upsert", "Spec Upsert"),
            ("preview", "Preview"),
            ("material_resolution", "Material Resolution"),
            ("bom_materialization", "BoM Materialization"),
            ("bom_write", "BoM Write"),
        ],
        string="Corr Auto BoM Error Stage",
        copy=False,
    )
    corr_autobom_error_payload = fields.Text(
        string="Corr Auto BoM Error Payload",
        copy=False,
    )
    corr_autobom_spec_id = fields.Integer(
        string="Corr Auto BoM Spec ID",
        copy=False,
    )
    corr_autobom_preview_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("success", "Success"),
            ("failed", "Failed"),
        ],
        string="Corr Auto BoM Preview Status",
        copy=False,
    )
    corr_autobom_bom_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("success", "Success"),
            ("failed", "Failed"),
        ],
        string="Corr Auto BoM BoM Status",
        copy=False,
    )

    @api.model
    def _corr_import_alias_target(self, header):
        if not isinstance(header, str):
            return header
        if header in self._fields:
            return header
        return CORR_PRODUCT_IMPORT_ALIAS_MAP.get(_corr_normalize_import_header(header), header)

    @api.model
    def _corr_is_direct_import_field(self, header):
        return isinstance(header, str) and header in self._fields

    @api.model
    def _corr_apply_import_header_aliases(self, import_fields, data):
        resolved = []
        by_target = {}
        warnings = []
        for index, header in enumerate(import_fields):
            target = self._corr_import_alias_target(header)
            item = {
                "index": index,
                "source": header,
                "target": target,
                "direct": self._corr_is_direct_import_field(header),
            }
            resolved.append(item)
            if isinstance(target, str) and target in self._fields:
                by_target.setdefault(target, []).append(item)

        ignored_indexes = set()
        for target, items in by_target.items():
            if len(items) <= 1:
                continue
            direct_items = [item for item in items if item["direct"]]
            kept = direct_items[0] if direct_items else items[0]
            ignored = [item for item in items if item["index"] != kept["index"]]
            ignored_indexes.update(item["index"] for item in ignored)
            warnings.append(
                _(
                    "Duplicate import columns map to '%(field)s'. Keeping '%(kept)s' and ignoring %(ignored)s."
                )
                % {
                    "field": target,
                    "kept": kept["source"],
                    "ignored": ", ".join("'%s'" % item["source"] for item in ignored),
                }
            )

        kept_items = [item for item in resolved if item["index"] not in ignored_indexes]
        mapped_fields = [item["target"] for item in kept_items]
        source_map = {
            item["target"]: item["source"]
            for item in kept_items
            if isinstance(item["target"], str) and item["target"] in self._fields
        }
        if not ignored_indexes:
            return mapped_fields, data, warnings, source_map

        kept_indexes = [item["index"] for item in kept_items]
        filtered_data = []
        for row in data:
            filtered_data.append([row[index] if index < len(row) else False for index in kept_indexes])
        return mapped_fields, filtered_data, warnings, source_map

    @api.model
    def load(self, fields, data):
        mapped_fields, mapped_data, warnings, source_map = self._corr_apply_import_header_aliases(fields, data)
        self._corr_validate_import_duplicate_product_codes(mapped_fields, mapped_data)
        mapped_fields, mapped_data = self._corr_inject_existing_product_ids_for_import(mapped_fields, mapped_data)
        model = self.with_context(corr_import_field_sources=source_map)
        result = super(ProductTemplateCorr, model).load(mapped_fields, mapped_data)
        batch = False
        try:
            if (
                not self.env.context.get("import_dryrun")
                and self._corr_import_should_create_result_batch(mapped_fields, mapped_data)
            ):
                with self.env.cr.savepoint():
                    batch = model._corr_create_import_result_batch(mapped_fields, mapped_data)
        except Exception:
            _logger.exception("Unable to create corrugation bulk upload result batch")
        if warnings and isinstance(result, dict):
            messages = result.setdefault("messages", [])
            messages[:0] = [
                {
                    "type": "warning",
                    "message": warning,
                    "record": False,
                    "field": False,
                    "rows": {"from": 0, "to": 0},
                }
                for warning in warnings
            ]
        if batch and isinstance(result, dict):
            result["corr_bulk_upload_summary"] = model._corr_bulk_upload_summary_payload(batch)
        return result

    @api.model
    def _corr_bulk_upload_summary_payload(self, batch):
        batch = batch.sudo().exists()
        if not batch:
            return False
        batch.ensure_one()
        action = self.env.ref("corr_mrp_bridge.action_corr_bulk_upload_batch").sudo().read()[0]
        action.update(
            {
                "res_id": batch.id,
                "views": [(False, "form")],
                "view_mode": "form",
                "target": "current",
            }
        )
        has_failed = bool(batch.failed_count)
        title = _("Bulk Upload Completed with Errors") if has_failed else _("Bulk Upload Completed Successfully")
        message = _(
            "Total: %(total)s\nSuccess: %(success)s\nAuto Fixed: %(auto_fixed)s\nFailed: %(failed)s"
        ) % {
            "total": batch.total_rows,
            "success": batch.success_count,
            "auto_fixed": batch.auto_fixed_count,
            "failed": batch.failed_count,
        }
        return {
            "batch_id": batch.id,
            "title": title,
            "message": message,
            "type": "warning" if has_failed else "success",
            "total": batch.total_rows,
            "success": batch.success_count,
            "auto_fixed": batch.auto_fixed_count,
            "failed": batch.failed_count,
            "action": action,
        }

    @api.model
    def _corr_inject_existing_product_ids_for_import(self, mapped_fields, mapped_data):
        """Let Odoo's native import update existing Engine V2 products by DB id."""
        mapped_fields = list(mapped_fields or [])
        mapped_data = list(mapped_data or [])
        if not mapped_data or ".id" in mapped_fields or "id" in mapped_fields:
            return mapped_fields, mapped_data
        if not self._corr_bulk_upload_use_engine_v2():
            return mapped_fields, mapped_data
        if not self._corr_import_should_create_result_batch(mapped_fields, mapped_data):
            return mapped_fields, mapped_data
        try:
            code_index = mapped_fields.index("default_code")
        except ValueError:
            return mapped_fields, mapped_data

        row_infos = []
        codes = set()
        for row in mapped_data:
            vals = {
                field_name: row[index] if index < len(row or []) else False
                for index, field_name in enumerate(mapped_fields)
                if isinstance(field_name, str) and field_name in self._fields
            }
            if not self._corr_row_looks_corrugated(vals):
                row_infos.append((False, False))
                continue
            code = str((row[code_index] if code_index < len(row or []) else "") or "").strip()
            row_infos.append((code, vals))
            if code:
                codes.add(code)
        if not codes:
            return mapped_fields, mapped_data

        ProductTemplate = self.env["product.template"].sudo().with_context(active_test=False)
        domain = [("default_code", "in", list(codes))]
        if "company_id" in ProductTemplate._fields:
            domain += ["|", ("company_id", "=", self.env.company.id), ("company_id", "=", False)]
        matches_by_code = {}
        for product in ProductTemplate.search(domain, order="id asc"):
            code = str(product.default_code or "").strip()
            if code:
                matches_by_code.setdefault(code, ProductTemplate.browse())
                matches_by_code[code] |= product

        duplicate_lines = []
        for code in sorted(codes):
            matches = matches_by_code.get(code, ProductTemplate.browse())
            if len(matches) > 1:
                duplicate_lines.append(
                    "%s - product.template IDs %s"
                    % (code, ", ".join(str(product_id) for product_id in matches.ids))
                )
        if duplicate_lines:
            raise UserError(
                _(
                    "Cannot import because the database already contains duplicate Product Codes:\n%s\n\n"
                    "Please merge or archive duplicates so each Product Code matches exactly one product."
                )
                % "\n".join(duplicate_lines)
            )

        id_values = []
        has_existing_match = False
        for code, _vals in row_infos:
            product = matches_by_code.get(code, ProductTemplate.browse()) if code else ProductTemplate.browse()
            product_id = product.id if len(product) == 1 else False
            has_existing_match = has_existing_match or bool(product_id)
            id_values.append(product_id or False)
        if not has_existing_match:
            return mapped_fields, mapped_data

        id_index = len(mapped_fields)
        mapped_fields.append(".id")
        aligned_data = []
        for index, row in enumerate(mapped_data):
            aligned_row = list(row or [])
            while len(aligned_row) < id_index:
                aligned_row.append(False)
            aligned_row.append(id_values[index])
            aligned_data.append(aligned_row)
        return mapped_fields, aligned_data

    def _corr_validate_import_duplicate_product_codes(self, mapped_fields, mapped_data):
        try:
            code_index = list(mapped_fields or []).index("default_code")
        except ValueError:
            return True
        rows_by_code = {}
        display_code = {}
        for row_number, row in enumerate(mapped_data or [], start=2):
            raw_code = row[code_index] if code_index < len(row or []) else False
            code = str(raw_code or "").strip()
            if not code:
                continue
            rows_by_code.setdefault(code, []).append(row_number)
            display_code.setdefault(code, code)
        duplicate_lines = []
        for code, row_numbers in rows_by_code.items():
            if len(row_numbers) <= 1:
                continue
            if len(row_numbers) == 2:
                rows_text = _("rows %(first)s and %(second)s") % {
                    "first": row_numbers[0],
                    "second": row_numbers[1],
                }
            else:
                rows_text = _("rows %s") % ", ".join(str(number) for number in row_numbers)
            duplicate_lines.append("%s - %s." % (display_code[code], rows_text))
        if duplicate_lines:
            raise UserError(
                _(
                    "Duplicate Product Code found in uploaded file:\n%s\n\n"
                    "Please keep only one row per Product Code and upload again. "
                    "No products were created or updated."
                )
                % "\n".join(duplicate_lines)
            )
        return True

    def _corr_import_should_create_result_batch(self, mapped_fields, mapped_data):
        if not mapped_data:
            return False
        corr_fields = {
            "corr_style",
            "corr_length_mm",
            "corr_width_mm",
            "corr_ply_count",
            "corr_board_recipe_code",
            "corr_routing_profile_code",
        }
        return bool(corr_fields.intersection(set(mapped_fields or [])))

    def _corr_import_row_value(self, mapped_fields, row, field_name):
        try:
            index = list(mapped_fields or []).index(field_name)
        except ValueError:
            return False
        return row[index] if index < len(row or []) else False

    def _corr_find_imported_template(self, code, name):
        Template = self.env["product.template"].sudo().with_context(active_test=False)
        code = str(code or "").strip()
        name = str(name or "").strip()
        product = False
        if code:
            product = Template.search([("default_code", "=", code)], limit=1)
            if not product:
                variant = self.env["product.product"].sudo().with_context(active_test=False).search(
                    [("default_code", "=", code)],
                    limit=1,
                )
                product = variant.product_tmpl_id if variant else False
        if not product and name:
            product = Template.search([("name", "=", name)], limit=1)
        return product

    def _corr_create_import_result_batch(self, mapped_fields, mapped_data):
        Batch = self.env["corr.bulk.upload.batch"].sudo()
        Line = self.env["corr.bulk.upload.line"].sudo()
        batch = Batch.create(
            {
                "name": _("Product Bulk Upload %s") % fields.Datetime.now(),
            }
        )
        line_vals = []
        for index, row in enumerate(mapped_data or [], start=2):
            code = self._corr_import_row_value(mapped_fields, row, "default_code")
            name = self._corr_import_row_value(mapped_fields, row, "name")
            product = self._corr_find_imported_template(code, name)
            vals = {
                "batch_id": batch.id,
                "excel_row": index,
                "product_tmpl_id": product.id if product else False,
                "product_code": code or (product.default_code if product else False),
                "product_name": name or (product.display_name if product else False),
                "status": "failed",
                "error_message": _("Product was not created or could not be matched after import."),
                "action_required": _("Check the import error messages and product code/name."),
            }
            if product:
                vals.update(
                    {
                        "product_code": product.default_code or code or False,
                        "product_name": product.display_name or name or False,
                    }
                )
            line_vals.append(vals)
        lines = Line.create(line_vals)
        for line in lines.filtered("product_tmpl_id"):
            Line._corr_update_from_product(line)
        batch._corr_apply_batch_autofix_status()
        return batch

    def _corr_parse_bool(self, val, default=False):
        if val is None:
            return default
        s = str(val).strip().lower()
        return s in ("1", "true", "yes", "y", "t")

    def _corr_parse_rate(self, val):
        if val is None:
            return None
        try:
            s = str(val).strip().replace("%", "")
            if not s:
                return None
            return float(s)
        except Exception:
            return None

    def _corr_find_or_create_tax(self, rate, scope, price_included, company_id):
        try:
            Tax = self.env["account.tax"].sudo().with_company(company_id)
        except Exception:
            return False
        if rate is None:
            return False
        domain = [
            ("type_tax_use", "=", scope),
            ("amount_type", "=", "percent"),
            ("amount", "=", float(rate)),
            ("price_include", "=", bool(price_included)),
            ("active", "=", True),
            ("company_id", "=", company_id),
        ]
        tax = Tax.search(domain, limit=1)
        if tax:
            return tax
        # Create a simple percent tax
        try:
            Group = self.env["account.tax.group"].sudo().with_company(company_id)
            grp = Group.search([], limit=1)
        except Exception:
            grp = False
        name = ("Sales " if scope == "sale" else "Purchase ") + f"{int(rate) if float(rate).is_integer() else rate}%" + (" (Incl.)" if price_included else "")
        vals = {
            "name": name,
            "type_tax_use": scope,
            "amount_type": "percent",
            "amount": float(rate),
            "price_include": bool(price_included),
            "active": True,
            "company_id": company_id,
        }
        if grp:
            vals["tax_group_id"] = grp.id
        return Tax.create(vals)

    def _corr_bulk_upload_use_engine_v2(self):
        return (
            self.env["ir.config_parameter"].sudo().get_param(
                "corr_mrp_bridge.bulk_upload_use_engine_v2",
                default="0",
            )
            == "1"
        )

    def _corr_active_engine(self):
        engine = (
            self.env["ir.config_parameter"].sudo().get_param(
                "corr_mrp_bridge.active_engine",
                default="old",
            )
            or "old"
        )
        return "v2" if engine == "v2" else "old"

    def _corr_json_text(self, payload):
        if not payload:
            return False
        try:
            return json.dumps(payload, indent=2, sort_keys=True, default=str)
        except Exception:
            try:
                return json.dumps(payload, default=str)
            except Exception:
                return str(payload)

    def _corr_set_autobom_status(
        self,
        status,
        backend,
        message=None,
        *,
        error_stage=False,
        error_payload=False,
        spec=False,
        preview_status=False,
        bom_status=False,
    ):
        self.ensure_one()
        vals = {
            "corr_autobom_status": status or "idle",
            "corr_autobom_backend": backend or False,
            "corr_autobom_message": message or False,
            "corr_autobom_last_run_at": fields.Datetime.now(),
            "corr_autobom_error_stage": error_stage or False,
            "corr_autobom_error_payload": self._corr_json_text(error_payload),
            "corr_autobom_spec_id": spec.id if getattr(spec, "id", False) else False,
            "corr_autobom_preview_status": preview_status or False,
            "corr_autobom_bom_status": bom_status or False,
        }
        self.with_context(skip_corr_autobom=True).write(vals)

    def _corr_v2_error_status_from_stage(self, stage, message=""):
        text = str(message or "").strip().lower()
        stage_key = str(stage or "").strip().lower()
        if stage_key == "validation":
            return "validation_failed", "validation"
        if stage_key == "spec_upsert":
            return "spec_upsert_failed", "spec_upsert"
        if stage_key == "material_resolution":
            return "material_resolution_failed", "material_resolution"
        if stage_key == "preview":
            material_tokens = (
                "no exact deckle",
                "no matching material",
                "no variant found",
                "no exact paper",
                "exact paper variant not found",
                "material resolution",
                "resolved material",
            )
            if any(token in text for token in material_tokens):
                return "material_resolution_failed", "material_resolution"
            return "preview_failed", "preview"
        if stage_key == "bom":
            material_tokens = (
                "no material variant",
                "no exact paper",
                "exact paper variant not found",
                "material resolution",
                "preview exact resolution missing product_id",
            )
            if any(token in text for token in material_tokens):
                return "material_resolution_failed", "material_resolution"
            write_tokens = (
                "unable to create",
                "unable to update",
                "bom line",
                "write failed",
                "create/update bom line",
            )
            if any(token in text for token in write_tokens):
                return "bom_write_failed", "bom_write"
            return "bom_materialization_failed", "bom_materialization"
        return "failed", False

    def _corr_set_v2_failure(self, stage, message, *, spec=False, payload=False):
        self.ensure_one()
        status, error_stage = self._corr_v2_error_status_from_stage(stage, message)
        preview_status = "failed" if stage in {"preview", "bom"} else False
        bom_status = "failed" if stage == "bom" else ("pending" if stage in {"validation", "spec_upsert", "preview"} else False)
        self._corr_set_autobom_status(
            status,
            "v2",
            message,
            error_stage=error_stage,
            error_payload=payload,
            spec=spec,
            preview_status=preview_status,
            bom_status=bom_status,
        )
        try:
            self.message_post(body=("Auto BoM generation blocked [%s]: %s" % (error_stage or stage, message)))
        except Exception:
            pass

    @api.model
    def _corr_row_looks_corrugated(self, vals):
        vals = vals or {}
        if vals.get("corr_style"):
            return True
        for i in range(1, 10):
            if vals.get(f"corr_layer{i}_role") or vals.get(f"corr_layer{i}_gsm"):
                return True
        return False

    @api.model
    def _corr_should_upsert_existing_product_on_create(self, vals):
        if not (self.env.context or {}).get("import_file"):
            return False
        if not self._corr_bulk_upload_use_engine_v2():
            return False
        if not self._corr_row_looks_corrugated(vals):
            return False
        default_code = str((vals or {}).get("default_code") or "").strip()
        return bool(default_code)

    @api.model
    def _corr_find_existing_product_for_upsert(self, vals):
        default_code = str((vals or {}).get("default_code") or "").strip()
        if not default_code:
            return self.env["product.template"]
        ProductTemplate = self.env["product.template"].sudo().with_context(active_test=False)
        company_id = vals.get("company_id") or self.env.company.id
        domain = [("default_code", "=", default_code)]
        if "company_id" in ProductTemplate._fields:
            domain += ["|", ("company_id", "=", company_id), ("company_id", "=", False)]
        return ProductTemplate.search(domain, order="id asc", limit=1)

    def _corr_find_v2_style(self):
        self.ensure_one()
        raw = str(self.corr_style or "").strip()
        if not raw:
            return False
        code = raw
        try:
            if raw.isdigit():
                code = str(raw).zfill(4)
        except Exception:
            code = raw
        Style = self.env["corr.engine.style"].sudo()
        style = Style.search([("code", "=", code), ("active", "=", True)], limit=1)
        if style:
            return style
        style = Style.search([("code", "=ilike", code), ("active", "=", True)], limit=1)
        if style:
            return style

        name_matches = Style.search([("name", "=ilike", raw), ("active", "=", True)], limit=2)
        if len(name_matches) == 1:
            return name_matches
        return False

    def _corr_import_source_column(self, field_name, default=None):
        sources = (self.env.context or {}).get("corr_import_field_sources") or {}
        return sources.get(field_name) or default or field_name

    def _corr_available_v2_style_codes(self):
        Style = self.env["corr.engine.style"].sudo()
        styles = Style.search([("active", "=", True)], order="code")
        return [str(style.code or "").strip() for style in styles if str(style.code or "").strip()]

    def _corr_invalid_v2_style_message(self):
        self.ensure_one()
        uploaded = str(self.corr_style or "").strip()
        source_column = self._corr_import_source_column("corr_style", default="corr_style")
        available = ", ".join(self._corr_available_v2_style_codes()) or _("none")
        if not uploaded:
            return _(
                "Missing style value from column '%(column)s'. Looked up by code, then unique name. "
                "Available style codes: %(codes)s."
            ) % {
                "column": source_column,
                "codes": available,
            }
        return _(
            "Invalid style code/name '%(style)s' from column '%(column)s'. "
            "Looked up by code, then unique name. Available style codes: %(codes)s."
        ) % {
            "style": uploaded,
            "column": source_column,
            "codes": available,
        }

    def _corr_find_v2_plant(self):
        self.ensure_one()
        code = str(self.corr_plant_code or "").strip()
        if not code:
            return False
        Plant = self.env["corr.plant.param"].sudo()
        plant = Plant.search([("code", "=", code)], limit=1)
        if not plant:
            plant = Plant.search([("code", "=ilike", code)], limit=1)
        return plant

    def _corr_find_v2_routing_profile(self):
        self.ensure_one()
        code = str(self.corr_routing_profile_code or "").strip()
        if not code:
            return False
        Profile = self.env["corr.engine.routing.profile"].sudo()
        profile = Profile.search([("name", "=", code), ("active", "=", True)], limit=1)
        if not profile:
            profile = Profile.search([("name", "=ilike", code), ("active", "=", True)], limit=1)
        return profile

    def _corr_find_v2_board_recipe(self):
        self.ensure_one()
        code = str(self.corr_board_recipe_code or "").strip()
        if not code:
            return False
        Recipe = self.env["corr.engine.board.recipe"].sudo()
        recipe = Recipe.search([("code", "=", code), ("active", "=", True)], limit=1)
        if not recipe:
            recipe = Recipe.search([("code", "=ilike", code), ("active", "=", True)], limit=1)
        return recipe

    def _corr_find_material_template(self, template_code):
        template_code = str(template_code or "").strip()
        if not template_code:
            return False
        Template = self.env["product.template"].sudo().with_context(active_test=False)
        template = self._corr_choose_material_template_code_matches(
            Template.search([("default_code", "=", template_code)], limit=20),
            template_code,
        )
        if not template:
            template = self._corr_choose_material_template_code_matches(
                Template.search([("default_code", "=ilike", template_code)], limit=20),
                template_code,
            )
        if not template:
            template = Template.search([("name", "=", template_code)], limit=1)
        if not template:
            template = Template.search([("name", "=ilike", template_code)], limit=1)
        if not template:
            template = self._corr_find_material_template_by_variant_code(template_code)
        return template

    def _corr_choose_material_template_code_matches(self, templates, template_code):
        templates = templates.filtered(lambda tmpl: self._corr_material_template_is_suitable(tmpl)).sorted("id")
        if len(templates) == 1:
            return templates[0]
        if len(templates) > 1:
            raise UserError(
                _(
                    "Material code '%s' matches multiple raw material templates. "
                    "Please use a unique material code or material template name."
                )
                % template_code
            )
        return False

    def _corr_material_template_is_suitable(self, template):
        if not template:
            return False
        if "product_role" in template._fields and template.product_role != "raw_material":
            return False
        configured_ids = (
            self.env["corr.engine.v2.spec.layer"]
            .sudo()
            ._allowed_material_template_ids_from_config()
        )
        if configured_ids and template.id not in configured_ids:
            return False
        return True

    def _corr_find_material_template_by_variant_code(self, template_code):
        Product = self.env["product.product"].sudo().with_context(active_test=False)
        for operator in ("=", "=ilike"):
            products = Product.search([("default_code", operator, template_code)], limit=20)
            if not products:
                continue
            templates = products.mapped("product_tmpl_id").filtered(
                lambda tmpl: self._corr_material_template_is_suitable(tmpl)
            )
            templates = templates.sorted("id")
            if len(templates) == 1:
                return templates[0]
            if len(templates) > 1:
                raise UserError(
                    _(
                        "Material code '%s' matches multiple raw material templates. "
                        "Please use a unique material code or material template name."
                    )
                    % template_code
                )
            return False
        return False

    def _corr_find_template_attribute_value(self, template, attr_name, raw_value):
        if not template or not attr_name or raw_value in (None, ""):
            return False
        layer_model = self.env["corr.engine.v2.spec.layer"]
        values = layer_model._attribute_values_for_template(template, attr_name)
        if not values:
            return False
        if attr_name == layer_model._material_attr_names()["color"]:
            target = layer_model._normalized_material_text(raw_value)
            return values.filtered(
                lambda value, norm=target: layer_model._normalized_material_text(value.name) == norm
            )[:1]
        try:
            target_num = float(raw_value or 0.0)
        except Exception:
            return False
        for value in values:
            if abs(layer_model._numeric_from_attribute_value(value) - target_num) <= 0.0001:
                return value
        return False

    def _corr_material_attr_names(self):
        layer_model = self.env["corr.engine.v2.spec.layer"]
        attr_names = dict(layer_model._material_attr_names() or {})
        icp = self.env["ir.config_parameter"].sudo()
        attr_names["deckle"] = (icp.get_param("corr_engine_v2.attr_deckle") or "Deckle").strip().lower()
        return attr_names

    def _corr_resolve_v2_layer_material_values(
        self,
        *,
        material_template=False,
        gsm=0.0,
        bf=False,
        color=False,
        deckle_cm=False,
        auto_create_missing_values=False,
    ):
        if not material_template:
            return {
                "gsm_value_id": False,
                "bf_value_id": False,
                "color_value_id": False,
                "deckle_value_id": False,
                "material_product_id": False,
                "material_product_match_count": 0,
                "material_product_match_codes": [],
            }

        attr_names = self._corr_material_attr_names()
        if auto_create_missing_values:
            self._corr_ensure_material_template_attribute_values(
                material_template=material_template,
                gsm=gsm,
                bf=bf,
                color=color,
                deckle_cm=deckle_cm,
            )
        gsm_value = self._corr_find_template_attribute_value(
            material_template, attr_names["gsm"], gsm
        )
        bf_value = self._corr_find_template_attribute_value(
            material_template, attr_names["bf"], bf
        ) if bf not in (False, None, "") else False
        color_value = self._corr_find_template_attribute_value(
            material_template, attr_names["color"], color
        ) if color not in (False, None, "") else False
        deckle_value = self._corr_find_template_attribute_value(
            material_template, attr_names["deckle"], deckle_cm
        ) if deckle_cm not in (False, None, "", 0, 0.0) else False

        variant = False
        match_count = 0
        match_codes = []
        value_ids = [value.id for value in (gsm_value, bf_value, color_value, deckle_value) if value]
        if value_ids:
            candidates = self.env["product.product"].sudo().with_context(active_test=False).search(
                [("product_tmpl_id", "=", material_template.id)]
            )
            for value_id in value_ids:
                candidates = candidates.filtered(
                    lambda product, vid=value_id: vid
                    in product.product_template_attribute_value_ids.mapped("product_attribute_value_id").ids
                )
            exact_candidates = candidates.filtered(lambda product: product.active)
            chosen_pool = exact_candidates or candidates
            match_count = len(chosen_pool)
            match_codes = [
                code
                for code in chosen_pool.mapped("default_code")
                if code
            ][:10]
            if match_count == 1:
                variant = chosen_pool[0]

        return {
            "gsm_value_id": gsm_value.id if gsm_value else False,
            "bf_value_id": bf_value.id if bf_value else False,
            "color_value_id": color_value.id if color_value else False,
            "deckle_value_id": deckle_value.id if deckle_value else False,
            "material_product_id": variant.id if variant else False,
            "material_product_match_count": match_count,
            "material_product_match_codes": match_codes,
        }

    def _corr_ensure_material_template_attribute_values(
        self,
        *,
        material_template,
        gsm=0.0,
        bf=False,
        color=False,
        deckle_cm=False,
    ):
        if not material_template:
            return False
        if not self._corr_bulk_upload_auto_create_missing_materials_enabled():
            return False
        try:
            gsm_float = float(gsm or 0.0)
        except Exception:
            gsm_float = 0.0
        if gsm_float <= 0.0:
            return False
        Wizard = self.env["corr.missing.kraft.variant.wizard"].sudo()
        wizard = Wizard.create({"product_template_id": material_template.id})
        wizard._find_or_create_attribute_value("GSM", gsm_float)
        if bf not in (False, None, ""):
            try:
                bf_float = float(bf or 0.0)
            except Exception:
                bf_float = 0.0
            if bf_float > 0.0:
                wizard._find_or_create_attribute_value("BF", bf_float)
        color_text = str(color or "").strip()
        if color_text:
            wizard._find_or_create_attribute_value("Color", color_text)
        if deckle_cm not in (False, None, "", 0, 0.0):
            try:
                deckle_float = float(deckle_cm or 0.0)
            except Exception:
                deckle_float = 0.0
            if deckle_float > 0.0:
                wizard._find_or_create_attribute_value("Deckle", deckle_float)
        return True

    def _corr_find_flute(self, flute_code):
        flute_code = str(flute_code or "").strip()
        if not flute_code:
            return False
        Flute = self.env["corr.flute"].sudo()
        flute = Flute.search([("code", "=", flute_code)], limit=1)
        if not flute:
            flute = Flute.search(["|", ("code", "=ilike", flute_code), ("name", "=ilike", flute_code)], limit=1)
        return flute

    def _corr_find_product_variant_by_code(self, product_code):
        product_code = str(product_code or "").strip()
        if not product_code:
            return False
        Product = self.env["product.product"].sudo().with_context(active_test=False)
        product = Product.search([("default_code", "=", product_code)], limit=1)
        if not product:
            product = Product.search([("default_code", "=ilike", product_code)], limit=1)
        return product

    def _corr_validation_required_deckle_cm(self):
        self.ensure_one()
        style_code = str(self.corr_style or "").strip().upper()
        if style_code not in ("0110", "DIECUT_SHEET"):
            return False
        try:
            sheet_width_mm = float(self.corr_width_mm or 0.0)
        except Exception:
            sheet_width_mm = 0.0
        if sheet_width_mm <= 0.0:
            return False
        try:
            forced_ups = int(self.corr_forced_ups or 0)
        except Exception:
            forced_ups = 0
        if forced_ups <= 0:
            forced_ups = 1
        deckle_mm = sheet_width_mm * forced_ups
        if deckle_mm <= 0.0:
            return False
        return deckle_mm / 10.0

    def _corr_normalize_top_sheet_mode(self):
        self.ensure_one()
        raw_mode = str(self.corr_top_layer_mode or "").strip().lower()
        if not raw_mode:
            return "none"
        mode_aliases = {
            "none": "none",
            "no": "none",
            "false": "none",
            "0": "none",
            "roll": "roll",
            "from roll": "roll",
            "from_roll": "roll",
            "top from roll": "roll",
            "top_from_roll": "roll",
            "unit": "unit_sheet",
            "unit_sheet": "unit_sheet",
            "unit sheet": "unit_sheet",
            "sheet": "unit_sheet",
            "unit printed sheet": "unit_sheet",
        }
        return mode_aliases.get(raw_mode)

    def _corr_resolve_v2_top_sheet_inputs(self):
        self.ensure_one()
        mode = self._corr_normalize_top_sheet_mode()
        if mode is None:
            raise UserError(_("Invalid top sheet mode '%s'. Use None, Roll, or Unit Sheet.") % (self.corr_top_layer_mode or ""))

        product = self.corr_top_sheet_product_id or False
        product_code = str(self.corr_top_sheet_code or "").strip()
        roll_code = str(self.corr_top_sheet_roll_code or "").strip()

        if mode == "unit_sheet":
            if not product and product_code:
                product = self._corr_find_product_variant_by_code(product_code)
            if not product:
                if self.env["ir.config_parameter"].sudo().get_param(
                    "corr_mrp_bridge.bulk_upload_auto_create_missing_top_sheets",
                    default="0",
                ) == "1":
                    raise UserError(
                        _(
                            "Top Sheet Mode 'Unit Sheet' requires a valid top sheet product or code. "
                            "Auto-create is enabled, but this upload row does not provide enough top sheet "
                            "product master data to create it safely."
                        )
                    )
                raise UserError(_("Top Sheet Mode 'Unit Sheet' requires a valid top sheet product or code."))
            return {
                "mode": "unit_sheet",
                "product": product,
                "product_code": product_code or (product.default_code or False),
                "roll_code": False,
            }

        if mode == "roll":
            effective_roll_code = roll_code or product_code
            if not effective_roll_code:
                raise UserError(_("Top Sheet Mode 'Roll' requires a top sheet roll code."))
            return {
                "mode": "roll",
                "product": False,
                "product_code": False,
                "roll_code": effective_roll_code,
            }

        return {
            "mode": "none",
            "product": False,
            "product_code": False,
            "roll_code": False,
        }

    def _corr_build_v2_layer_source_data(self):
        self.ensure_one()
        layers = []
        role_map = {
            "liner": "inner_liner",
            "medium": "medium",
        }
        valid_roles = set(role_map.keys()) | {"outer_liner", "inner_liner", "center_liner", "carton_board", "other"}
        for i in range(1, 10):
            role_raw = self[f"corr_layer{i}_role"]
            gsm_raw = self[f"corr_layer{i}_gsm"]
            bf_raw = getattr(self, f"corr_layer{i}_bf", None)
            flute_code = getattr(self, f"corr_layer{i}_flute_code", None)
            material_template_code = getattr(self, f"corr_layer{i}_material_template", None)
            color = getattr(self, f"corr_layer{i}_color", None)

            if not any([role_raw, gsm_raw, bf_raw, flute_code, material_template_code, color]):
                continue

            role_norm = str(role_raw or "").strip().lower()
            if not role_norm or role_norm not in valid_roles:
                raise UserError(_("Invalid layer role on corr_layer%s.") % i)
            role_v2 = role_map.get(role_norm, role_norm)

            try:
                gsm = float(gsm_raw or 0.0)
            except Exception:
                gsm = 0.0
            if gsm <= 0.0:
                raise UserError(_("Invalid GSM on corr_layer%s.") % i)

            bf = 0.0
            if bf_raw not in (None, ""):
                try:
                    bf = float(bf_raw or 0.0)
                except Exception:
                    raise UserError(_("Invalid BF on corr_layer%s.") % i)
                if bf < 0.0:
                    raise UserError(_("Invalid BF on corr_layer%s.") % i)

            flute = False
            flute_code_norm = str(flute_code or "").strip()
            if role_v2 == "medium":
                if not flute_code_norm:
                    raise UserError(_("Unknown or missing flute code on corr_layer%s.") % i)
                flute = self._corr_find_flute(flute_code_norm)
                if not flute:
                    raise UserError(_("Unknown flute code '%s' on corr_layer%s.") % (flute_code_norm, i))

            material_template = False
            material_template_code_norm = str(material_template_code or "").strip()
            if material_template_code_norm:
                material_template = self._corr_find_material_template(material_template_code_norm)
                if not material_template:
                    raise UserError(
                        _("No matching material template '%s' on corr_layer%s.") % (material_template_code_norm, i)
                    )
            deckle_cm = self._corr_validation_required_deckle_cm()
            resolved_values = self._corr_resolve_v2_layer_material_values(
                material_template=material_template,
                gsm=gsm,
                bf=bf if bf_raw not in (None, "") else False,
                color=str(color or "").strip() or False,
                deckle_cm=deckle_cm,
                auto_create_missing_values=self._corr_bulk_upload_auto_create_missing_materials_enabled(),
            )
            if material_template:
                attr_names = self._corr_material_attr_names()
                if not resolved_values.get("gsm_value_id"):
                    raise UserError(
                        _("Material template '%(template)s' does not have a GSM value matching %(gsm)s on corr_layer%(layer)s.")
                        % {
                            "template": material_template.display_name,
                            "gsm": gsm,
                            "layer": i,
                        }
                    )
                if bf_raw not in (None, "") and not resolved_values.get("bf_value_id"):
                    raise UserError(
                        _("Material template '%(template)s' does not have a BF value matching %(bf)s on corr_layer%(layer)s.")
                        % {
                            "template": material_template.display_name,
                            "bf": bf,
                            "layer": i,
                        }
                    )
                if str(color or "").strip() and not resolved_values.get("color_value_id"):
                    raise UserError(
                        _("Material template '%(template)s' does not have a Color value matching '%(color)s' on corr_layer%(layer)s.")
                        % {
                            "template": material_template.display_name,
                            "color": str(color or "").strip(),
                            "layer": i,
                        }
                    )
                deckle_attr_values = self.env["corr.engine.v2.spec.layer"]._attribute_values_for_template(
                    material_template, attr_names["deckle"]
                )
                if deckle_cm and deckle_attr_values and not resolved_values.get("deckle_value_id"):
                    raise UserError(
                        _("Material template '%(template)s' does not have a Deckle value matching %(deckle).1f cm on corr_layer%(layer)s.")
                        % {
                            "template": material_template.display_name,
                            "deckle": float(deckle_cm),
                            "layer": i,
                        }
                    )
                if (
                    not resolved_values.get("material_product_id")
                    and not self._corr_bulk_upload_auto_create_missing_materials_enabled()
                ):
                    details = [
                        _("Material Template = %s") % material_template.display_name,
                        _("GSM = %s") % gsm,
                    ]
                    if bf_raw not in (None, ""):
                        details.append(_("BF = %s") % bf)
                    color_text = str(color or "").strip()
                    if color_text:
                        details.append(_("Color = %s") % color_text)
                    if deckle_cm:
                        details.append(_("Deckle = %.1f cm") % float(deckle_cm))
                    match_count = int(resolved_values.get("material_product_match_count") or 0)
                    match_codes = list(resolved_values.get("material_product_match_codes") or [])
                    if match_count > 1:
                        if match_codes:
                            details.append(
                                _("Matching variants found = %s") % ", ".join(match_codes)
                            )
                        raise UserError(
                            _(
                                "Multiple material variants matched corr_layer%(layer)s: %(details)s. "
                                "Please remove duplicate variants or make the upload more specific."
                            )
                            % {
                                "layer": i,
                                "details": ", ".join(details),
                            }
                        )
                    raise UserError(
                        _(
                            "No material variant found for corr_layer%(layer)s: %(details)s. "
                            "Please create the missing product variant or correct the upload."
                        )
                        % {
                            "layer": i,
                            "details": ", ".join(details),
                        }
                    )

            layers.append(
                {
                    "sequence": i * 10,
                    "layer_index": i,
                    "role": role_v2,
                    "gsm": gsm,
                    "bf": bf if bf_raw not in (None, "") else False,
                    "flute_code": flute_code_norm or False,
                    "flute_id": flute.id if flute else False,
                    "material_template_code": material_template_code_norm or False,
                    "material_template_id": material_template.id if material_template else False,
                    "color": str(color or "").strip() or False,
                    "gsm_value_id": resolved_values.get("gsm_value_id") or False,
                    "bf_value_id": resolved_values.get("bf_value_id") or False,
                    "color_value_id": resolved_values.get("color_value_id") or False,
                    "material_product_id": resolved_values.get("material_product_id") or False,
                }
            )
        return layers

    def _corr_build_v2_layer_source_data_from_prefix(self, prefix, label):
        self.ensure_one()
        layers = []
        role_map = {
            "liner": "inner_liner",
            "medium": "medium",
        }
        valid_roles = set(role_map.keys()) | {"outer_liner", "inner_liner", "center_liner", "carton_board", "other"}
        for i in range(1, 10):
            role_raw = getattr(self, f"{prefix}_layer{i}_role", False)
            gsm_raw = getattr(self, f"{prefix}_layer{i}_gsm", False)
            bf_raw = getattr(self, f"{prefix}_layer{i}_bf", False)
            flute_code = getattr(self, f"{prefix}_layer{i}_flute_code", False)
            material_template_code = getattr(self, f"{prefix}_layer{i}_material_template", False)
            color = getattr(self, f"{prefix}_layer{i}_color", False)

            if not any([role_raw, gsm_raw, bf_raw, flute_code, material_template_code, color]):
                continue

            role_norm = str(role_raw or "").strip().lower()
            if not role_norm or role_norm not in valid_roles:
                raise UserError(_("Invalid layer role on %(label)s layer %(layer)s.") % {
                    "label": label,
                    "layer": i,
                })
            role_v2 = role_map.get(role_norm, role_norm)

            try:
                gsm = float(gsm_raw or 0.0)
            except Exception:
                gsm = 0.0
            if gsm <= 0.0:
                raise UserError(_("Invalid GSM on %(label)s layer %(layer)s.") % {
                    "label": label,
                    "layer": i,
                })

            bf = 0.0
            if bf_raw not in (None, ""):
                try:
                    bf = float(bf_raw or 0.0)
                except Exception:
                    raise UserError(_("Invalid BF on %(label)s layer %(layer)s.") % {
                        "label": label,
                        "layer": i,
                    })
                if bf < 0.0:
                    raise UserError(_("Invalid BF on %(label)s layer %(layer)s.") % {
                        "label": label,
                        "layer": i,
                    })

            flute = False
            flute_code_norm = str(flute_code or "").strip()
            if role_v2 == "medium":
                if not flute_code_norm:
                    raise UserError(_("Unknown or missing flute code on %(label)s layer %(layer)s.") % {
                        "label": label,
                        "layer": i,
                    })
                flute = self._corr_find_flute(flute_code_norm)
                if not flute:
                    raise UserError(_("Unknown flute code '%(flute)s' on %(label)s layer %(layer)s.") % {
                        "flute": flute_code_norm,
                        "label": label,
                        "layer": i,
                    })

            material_template = False
            material_template_code_norm = str(material_template_code or "").strip()
            if material_template_code_norm:
                material_template = self._corr_find_material_template(material_template_code_norm)
                if not material_template:
                    raise UserError(
                        _("No matching material template '%(template)s' on %(label)s layer %(layer)s.")
                        % {
                            "template": material_template_code_norm,
                            "label": label,
                            "layer": i,
                        }
                    )
            resolved_values = self._corr_resolve_v2_layer_material_values(
                material_template=material_template,
                gsm=gsm,
                bf=bf if bf_raw not in (None, "") else False,
                color=str(color or "").strip() or False,
                deckle_cm=False,
                auto_create_missing_values=self._corr_bulk_upload_auto_create_missing_materials_enabled(),
            )
            layers.append({
                "sequence": i * 10,
                "layer_index": i,
                "role": role_v2,
                "gsm": gsm,
                "bf": bf if bf_raw not in (None, "") else False,
                "flute_code": flute_code_norm or False,
                "flute_id": flute.id if flute else False,
                "material_template_code": material_template_code_norm or False,
                "material_template_id": material_template.id if material_template else False,
                "color": str(color or "").strip() or False,
                "gsm_value_id": resolved_values.get("gsm_value_id") or False,
                "bf_value_id": resolved_values.get("bf_value_id") or False,
                "color_value_id": resolved_values.get("color_value_id") or False,
                "material_product_id": resolved_values.get("material_product_id") or False,
            })
        return layers

    def _corr_v2_layer_signature(self, layers):
        signature = []
        for layer in layers or []:
            signature.append((
                str(layer.get("role") or ""),
                round(float(layer.get("gsm") or 0.0), 4),
                round(float(layer.get("bf") or 0.0), 4),
                str(layer.get("flute_code") or "").strip().upper(),
                str(layer.get("material_template_code") or "").strip().lower(),
                str(layer.get("color") or "").strip().lower(),
            ))
        return tuple(signature)

    def _corr_v2_layer_commands(self, layers):
        commands = []
        for layer in layers or []:
            commands.append((0, 0, {
                "sequence": int(layer["sequence"]),
                "role": layer["role"],
                "gsm": float(layer["gsm"]),
                "bf": float(layer["bf"]) if layer.get("bf") not in (False, None, "") else False,
                "color": layer.get("color") or False,
                "flute_type_id": layer.get("flute_id") or False,
                "material_product_id": layer.get("material_product_id") or False,
                "material_template_id": layer.get("material_template_id") or False,
                "gsm_value_id": layer.get("gsm_value_id") or False,
                "bf_value_id": layer.get("bf_value_id") or False,
                "color_value_id": layer.get("color_value_id") or False,
                "source_type": "manual",
            }))
        return commands

    def _corr_v2_multi_piece_enabled(self):
        self.ensure_one()
        return bool(self.corr_v2_multi_piece_enabled)

    def _corr_normalize_v2_top_sheet_mode_value(self, raw_mode):
        raw_mode = str(raw_mode or "").strip().lower()
        if not raw_mode:
            return False
        mode_aliases = {
            "none": "none",
            "no": "none",
            "false": "none",
            "0": "none",
            "roll": "roll",
            "from roll": "roll",
            "from_roll": "roll",
            "top from roll": "roll",
            "top_from_roll": "roll",
            "unit": "unit_sheet",
            "unit_sheet": "unit_sheet",
            "unit sheet": "unit_sheet",
            "sheet": "unit_sheet",
            "unit printed sheet": "unit_sheet",
        }
        return mode_aliases.get(raw_mode)

    def _corr_v2_piece_top_sheet_vals(self, piece_no):
        self.ensure_one()
        mode = self._corr_normalize_v2_top_sheet_mode_value(
            getattr(self, f"corr_v2_piece{piece_no}_top_sheet_mode", False)
        )
        code = str(getattr(self, f"corr_v2_piece{piece_no}_top_sheet_code", False) or "").strip()
        if not mode and not code:
            return {}
        if mode in (False, "none"):
            return {"top_sheet_source": "custom", "top_sheet_mode": "none", "top_sheet_product_id": False}
        if mode == "roll":
            raise UserError(
                _("Piece-specific top sheet roll codes are not supported in Engine V2 piece lines. "
                  "Use inherited parent roll top-sheet settings or Unit Sheet per piece.")
            )
        product = self._corr_find_product_variant_by_code(code)
        if not product:
            if self.env["ir.config_parameter"].sudo().get_param(
                "corr_mrp_bridge.bulk_upload_auto_create_missing_top_sheets",
                default="0",
            ) == "1":
                raise UserError(
                    _(
                        "Piece %s Unit Sheet requires a valid top sheet product code. "
                        "Auto-create is enabled, but this upload row does not provide enough top sheet "
                        "product master data to create it safely."
                    )
                    % piece_no
                )
            raise UserError(_("Piece %s Unit Sheet requires a valid top sheet product code.") % piece_no)
        return {
            "top_sheet_source": "custom",
            "top_sheet_mode": "unit_sheet",
            "top_sheet_product_id": product.id,
        }

    def _corr_build_v2_multi_piece_plan(self, base_layer_data):
        self.ensure_one()
        if not self._corr_v2_multi_piece_enabled():
            return {
                "enabled": False,
                "common_layer_data": base_layer_data,
                "commands": False,
            }

        p1_layers = self._corr_build_v2_layer_source_data_from_prefix("corr_v2_piece1", _("Piece 1"))
        p2_layers = self._corr_build_v2_layer_source_data_from_prefix("corr_v2_piece2", _("Piece 2"))
        p1_sig = self._corr_v2_layer_signature(p1_layers)
        p2_sig = self._corr_v2_layer_signature(p2_layers)
        pieces_have_same_stack = bool(
            (p1_layers and not p2_layers)
            or (p2_layers and not p1_layers)
            or (p1_layers and p2_layers and p1_sig == p2_sig)
        )
        common_layer_data = base_layer_data
        if not common_layer_data:
            common_layer_data = p1_layers if p1_layers else p2_layers

        split_axis = str(self.corr_v2_multi_piece_split_axis or "width").strip().lower()
        if split_axis not in ("width", "length"):
            raise UserError(_("Invalid Engine V2 multi-piece split axis: %s") % self.corr_v2_multi_piece_split_axis)
        split_mode = str(self.corr_v2_multi_piece_split_mode or "uneven").strip().lower()
        if split_mode not in ("equal", "uneven"):
            raise UserError(_("Invalid Engine V2 multi-piece split mode: %s") % self.corr_v2_multi_piece_split_mode)
        strategy = str(self.corr_v2_multi_piece_strategy or "independent_routed_piece").strip()
        if strategy not in ("independent_routed_piece", "shared_upstream_split"):
            raise UserError(_("Invalid Engine V2 multi-piece strategy: %s") % self.corr_v2_multi_piece_strategy)
        p1_effective_layers = p1_layers or common_layer_data
        p2_effective_layers = p2_layers or common_layer_data

        def _piece_vals(piece_no, default_code, default_name, layers):
            length = float(getattr(self, f"corr_v2_piece{piece_no}_length_mm", 0.0) or 0.0)
            width = float(getattr(self, f"corr_v2_piece{piece_no}_width_mm", 0.0) or 0.0)
            if length <= 0.0 or width <= 0.0:
                raise UserError(_("Engine V2 multi-piece upload requires Piece %s length and width.") % piece_no)
            vals = {
                "sequence": piece_no * 10,
                "piece_code": str(getattr(self, f"corr_v2_piece{piece_no}_code", False) or default_code).strip(),
                "piece_name": str(getattr(self, f"corr_v2_piece{piece_no}_name", False) or default_name).strip(),
                "piece_length_mm": length,
                "piece_width_mm": width,
                "piece_qty_per_fg": float(getattr(self, f"corr_v2_piece{piece_no}_qty_per_fg", 0.0) or 1.0),
                "stack_source": "inherited",
                "surface_source": "inherited",
                "top_sheet_source": "inherited",
            }
            top_sheet_vals = self._corr_v2_piece_top_sheet_vals(piece_no)
            if top_sheet_vals:
                vals.update(top_sheet_vals)
            if layers and (not pieces_have_same_stack or strategy == "independent_routed_piece"):
                vals["stack_source"] = "custom"
                vals["piece_stack_line_ids"] = self._corr_v2_layer_commands(layers)
            return vals

        return {
            "enabled": True,
            "common_layer_data": common_layer_data,
            "pieces_have_same_stack": pieces_have_same_stack,
            "vals": {
                "multi_piece_enabled": True,
                "multi_piece_source": "explicit_multi_piece_design",
                "multi_piece_strategy": strategy,
                "multi_piece_split_axis": split_axis,
                "multi_piece_split_mode": split_mode,
                "multi_piece_source_length_mm": float(self.corr_v2_multi_piece_source_length_mm or self.corr_length_mm or 0.0),
                "multi_piece_source_width_mm": float(self.corr_v2_multi_piece_source_width_mm or self.corr_width_mm or 0.0),
                "multi_piece_merge_stage_key": str(self.corr_v2_multi_piece_merge_stage_key or "seaming").strip(),
                "multi_piece_line_ids": [
                    (0, 0, _piece_vals(1, "piece_a", _("Piece A"), p1_effective_layers)),
                    (0, 0, _piece_vals(2, "piece_b", _("Piece B"), p2_effective_layers)),
                ],
            },
        }

    def _corr_autobom_v2_validate_inputs(self):
        self.ensure_one()
        style = self._corr_find_v2_style()
        if not style:
            raise UserError(self._corr_invalid_v2_style_message())

        plant = self._corr_find_v2_plant()
        if not plant:
            raise UserError(_("Missing or invalid plant code."))

        routing_profile = self._corr_find_v2_routing_profile()
        if not routing_profile:
            raise UserError(_("Missing or invalid routing profile code."))
        if not routing_profile.stage_ids:
            raise UserError(
                _("Routing profile '%s' has no stage lines configured.")
                % (routing_profile.display_name or routing_profile.name)
            )

        board_recipe = self._corr_find_v2_board_recipe()
        layer_data = self._corr_build_v2_layer_source_data()
        multi_piece_plan = self._corr_build_v2_multi_piece_plan(layer_data)
        layer_data = multi_piece_plan.get("common_layer_data") or layer_data
        if not board_recipe and not layer_data:
            raise UserError(_("Missing board recipe or board structure."))

        style_code = str(getattr(style, "code", "") or "").strip().upper()
        fg_per_sheet = False
        if style_code == "DIECUT_SHEET":
            try:
                fg_per_sheet = int(self.corr_fg_per_sheet or 0)
            except Exception:
                fg_per_sheet = 0
            if fg_per_sheet < 1:
                raise UserError(_("DIECUT_SHEET requires FG per Sheet as an integer >= 1."))

        top_sheet = self._corr_resolve_v2_top_sheet_inputs()

        return {
            "style": style,
            "plant": plant,
            "routing_profile": routing_profile,
            "board_recipe": board_recipe,
            "layer_data": layer_data,
            "fg_per_sheet": fg_per_sheet,
            "top_sheet": top_sheet,
            "multi_piece_plan": multi_piece_plan,
        }

    def _corr_build_v2_spec_vals(self, validation):
        self.ensure_one()
        style = validation["style"]
        plant = validation["plant"]
        routing_profile = validation["routing_profile"]
        board_recipe = validation.get("board_recipe")
        layer_data = list(validation.get("layer_data") or [])
        top_sheet = validation.get("top_sheet") or {}
        style_code = str(getattr(style, "code", "") or "").strip().upper()

        vals = {
            "name": self.name or _("Engine V2 Spec"),
            "product_tmpl_id": self.id,
            "style_id": style.id,
            "plant_id": plant.id,
            "routing_profile_id": routing_profile.id,
            "box_length_mm": float(self.corr_length_mm or 0.0),
            "box_width_mm": float(self.corr_width_mm or 0.0),
            "box_height_mm": float(self.corr_height_mm or 0.0),
            "surface_printing_required": bool(self.corr_printing_required or False),
            "surface_print_colors": int(self.corr_print_colors or 0),
            "surface_print_coverage_percent": float(self.corr_coverage_pct or 0.0),
        }

        vals["surface_top_sheet_mode"] = top_sheet.get("mode") or "none"
        vals["surface_top_sheet_product_id"] = top_sheet.get("product").id if top_sheet.get("product") else False
        vals["surface_top_sheet_roll_code"] = top_sheet.get("roll_code") or False

        lam_code = str(self.corr_lamination_code or "").strip()
        if lam_code:
            Lam = self.env["corr.lamination.type"].sudo()
            lam = Lam.search([("code", "=", lam_code)], limit=1) or Lam.search([("name", "=ilike", lam_code)], limit=1)
            if lam:
                vals["surface_lamination_required"] = True
                vals["surface_lamination_type_id"] = lam.id

        if style_code in ("0110", "DIECUT_SHEET"):
            vals["manual_sheet_length_mm"] = float(self.corr_length_mm or 0.0)
            vals["manual_sheet_width_mm"] = float(self.corr_width_mm or 0.0)
        if style_code == "0100":
            vals["box_height_mm"] = 0.0
        if style_code == "DIECUT_SHEET":
            vals["fg_per_sheet"] = int(validation.get("fg_per_sheet") or 1)
        if int(self.corr_forced_ups or 0) > 0:
            vals["forced_ups"] = int(self.corr_forced_ups or 0)
        else:
            vals["forced_ups"] = False

        if board_recipe:
            vals["board_recipe_id"] = board_recipe.id
            vals["is_board_customized"] = False
        elif layer_data:
            vals["board_recipe_id"] = False
            vals["is_board_customized"] = True
            vals["stack_line_ids"] = [
                (
                    0,
                    0,
                    {
                        "sequence": int(layer["sequence"]),
                        "role": layer["role"],
                        "gsm": float(layer["gsm"]),
                        "bf": float(layer["bf"]) if layer.get("bf") not in (False, None, "") else False,
                        "color": layer.get("color") or False,
                        "flute_type_id": layer.get("flute_id") or False,
                        "material_product_id": layer.get("material_product_id") or False,
                        "material_template_id": layer.get("material_template_id") or False,
                        "gsm_value_id": layer.get("gsm_value_id") or False,
                        "bf_value_id": layer.get("bf_value_id") or False,
                        "color_value_id": layer.get("color_value_id") or False,
                        "source_type": "manual",
                    },
                )
                for layer in layer_data
            ]
        multi_piece_plan = validation.get("multi_piece_plan") or {}
        if multi_piece_plan.get("enabled"):
            vals.update(multi_piece_plan.get("vals") or {})
        return vals

    def _corr_upsert_v2_spec(self, validation):
        self.ensure_one()
        Spec = self.env["corr.engine.v2.spec"].sudo().with_context(
            skip_layout_auto_refresh=True,
            skip_board_recipe_snapshot=False,
        )
        spec = Spec.search([("product_tmpl_id", "=", self.id)], limit=1)
        vals = self._corr_build_v2_spec_vals(validation)
        if spec:
            write_vals = dict(vals)
            if "stack_line_ids" in write_vals:
                write_vals["stack_line_ids"] = [(5, 0, 0)] + list(write_vals["stack_line_ids"])
            if "multi_piece_line_ids" in write_vals:
                write_vals["multi_piece_line_ids"] = [(5, 0, 0)] + list(write_vals["multi_piece_line_ids"])
            spec.write(write_vals)
            return spec, False
        spec = Spec.create([vals])[0]
        return spec, True

    def _corr_layer_material_resolution_message(self, line, layer_number):
        template = line.material_template_id or getattr(line.material_product_id, "product_tmpl_id", False)
        parts = []
        if template:
            parts.append(template.display_name or template.name or _("Material"))
        try:
            gsm = float(line.gsm or 0.0)
        except Exception:
            gsm = 0.0
        if gsm:
            parts.append(_("GSM %s") % ("%g" % gsm))
        try:
            bf = float(line.bf or 0.0)
        except Exception:
            bf = 0.0
        if bf:
            parts.append(_("BF %s") % ("%g" % bf))
        color = str(line.color or "").strip()
        if color:
            parts.append(_("Color %s") % color)
        deckle_cm = self._corr_validation_required_deckle_cm()
        if deckle_cm:
            parts.append(_("Deckle %.1f cm") % float(deckle_cm))
        return _(
            "No material variant found for Layer %(layer)s: %(details)s. "
            "Please create the missing product variant or correct the upload."
        ) % {
            "layer": layer_number,
            "details": ", ".join(parts) or (line.display_name or _("Board layer")),
        }

    def _corr_validate_v2_spec_material_resolution(self, spec):
        self.ensure_one()
        spec.ensure_one()
        try:
            lines = spec.stack_line_ids.sorted(lambda line: (line.sequence or 0, line.id or 0))
        except Exception:
            lines = spec.stack_line_ids
        for index, line in enumerate(lines, start=1):
            if not line.material_template_id and not line.material_product_id:
                continue
            if line.resolved_product_id:
                if not line.material_product_id:
                    line.with_context(
                        skip_board_line_change_mark=True,
                        skip_board_line_sequence_normalize=True,
                    ).write({"material_product_id": line.resolved_product_id.id})
                continue
            resolution_message = str(getattr(line, "resolution_message", "") or "").strip().lower()
            if "multiple variants match before deckle" in resolution_message:
                # The spec-line compute intentionally resolves without deckle; Engine V2 preview
                # performs the exact deckle-aware material resolution. Do not block upload here.
                continue
            if self._corr_bulk_upload_auto_create_missing_materials_enabled():
                # During bulk upload, exact deckle is known only in the Engine V2 preview.
                # Let preview produce the deterministic material identity, then auto-create
                # and retry once from that deckle-aware payload.
                continue
            raise UserError(self._corr_layer_material_resolution_message(line, index))

    def _corr_preview_material_resolution_errors(self, payload):
        errors = []
        paper_resolution = payload.get("paper_resolution") or []
        if not isinstance(paper_resolution, list):
            return errors
        multi_piece = payload.get("multi_piece") or {}
        if not isinstance(multi_piece, dict):
            multi_piece = {}
        strategy = str(
            multi_piece.get("strategy")
            or multi_piece.get("multi_piece_strategy")
            or multi_piece.get("strategy_type")
            or ""
        ).strip().lower()
        piece_rows = ((payload.get("piece_specific_rm_breakdown") or {}).get("rows") or [])
        has_piece_specific_rows = isinstance(piece_rows, list) and bool(piece_rows)
        skip_parent_resolution_rows = bool(
            multi_piece.get("enabled")
            and strategy == "independent_routed_piece"
            and has_piece_specific_rows
        )

        def _paper_resolution_piece_key(entry):
            metadata = (entry or {}).get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}
            return str((entry or {}).get("piece_key") or metadata.get("piece_key") or "").strip()

        def _paper_resolution_layer_key(entry):
            role = str((entry or {}).get("role") or "").strip().lower()
            layer = (entry or {}).get("source_layer_no") or (entry or {}).get("layer_no") or "-"
            try:
                layer = int(layer)
            except Exception:
                layer = str(layer or "-").strip()
            try:
                gsm = round(float((entry or {}).get("gsm") or 0.0), 4)
            except Exception:
                gsm = 0.0
            try:
                bf = round(float((entry or {}).get("bf") or 0.0), 4)
            except Exception:
                bf = 0.0
            color = str((entry or {}).get("color") or "").strip().lower()
            return (role, layer, gsm, bf, color)

        def _paper_resolution_entry_is_resolved(entry):
            status = str((entry or {}).get("status") or "").strip().lower()
            note = str((entry or {}).get("resolution_note") or "").strip().lower()
            try:
                product_id = int((entry or {}).get("product_id") or 0)
            except Exception:
                product_id = 0
            if "resolved" in note and "not found" not in note:
                return True
            return status in ("exact", "resolved") and product_id

        resolved_layer_keys = {
            _paper_resolution_layer_key(entry)
            for entry in paper_resolution
            if isinstance(entry, dict) and _paper_resolution_entry_is_resolved(entry)
        }

        for entry in paper_resolution:
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "").strip().lower()
            if status == "trace" or entry.get("trace_only"):
                continue
            role = str(entry.get("role") or "").strip().lower()
            if role not in ("liner", "medium", "top_roll", "carton_board"):
                continue
            if skip_parent_resolution_rows and not _paper_resolution_piece_key(entry):
                continue
            if _paper_resolution_entry_is_resolved(entry):
                continue
            if _paper_resolution_layer_key(entry) in resolved_layer_keys:
                continue
            layer = entry.get("source_layer_no") or entry.get("layer_no") or "-"
            template_name = entry.get("material_template_name") or _("Material")
            details = [str(template_name)]
            if entry.get("gsm") not in (None, ""):
                try:
                    details.append(_("GSM %s") % ("%g" % float(entry.get("gsm") or 0.0)))
                except Exception:
                    details.append(_("GSM %s") % entry.get("gsm"))
            if entry.get("bf") not in (None, ""):
                try:
                    details.append(_("BF %s") % ("%g" % float(entry.get("bf") or 0.0)))
                except Exception:
                    details.append(_("BF %s") % entry.get("bf"))
            if str(entry.get("color") or "").strip():
                details.append(_("Color %s") % str(entry.get("color")).strip())
            if entry.get("required_deckle_cm") not in (None, ""):
                try:
                    details.append(_("Deckle %.1f cm") % float(entry.get("required_deckle_cm") or 0.0))
                except Exception:
                    pass
            note = str(entry.get("resolution_note") or "").strip()
            message = _(
                "No material variant found for Layer %(layer)s: %(details)s. "
                "Please create the missing product variant or correct the upload."
            ) % {
                "layer": layer,
                "details": ", ".join(details),
            }
            if note:
                message = "%s %s" % (message, note)
            errors.append(message)
        return errors

    def _corr_validate_v2_preview_material_payload(self, payload):
        errors = self._corr_preview_material_resolution_errors(payload)
        if errors:
            raise UserError("\n".join(errors))

    def _corr_bulk_upload_auto_create_missing_materials_enabled(self):
        value = self.env["ir.config_parameter"].sudo().get_param(
            "corr_mrp_bridge.bulk_upload_auto_create_missing_materials",
            default="0",
        )
        return str(value).strip().lower() in ("1", "true", "yes", "y", "t")

    def _corr_missing_material_specs_from_preview_payload(self, payload):
        specs = []
        seen = set()
        paper_resolution = payload.get("paper_resolution") or []
        if not isinstance(paper_resolution, list):
            return specs
        for entry in paper_resolution:
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "").strip().lower()
            note = str(entry.get("resolution_note") or "").strip().lower()
            if status in ("exact", "resolved") and entry.get("product_id"):
                continue
            if "not found" not in note and "missing" not in note and status not in ("missing", "not_found", "none", ""):
                continue
            role = str(entry.get("role") or "").strip().lower()
            if role not in ("liner", "medium", "top_roll", "carton_board"):
                continue
            try:
                gsm = float(entry.get("gsm") or 0.0)
                bf = float(entry.get("bf") or 0.0)
                deckle = float(entry.get("required_deckle_cm") or 0.0)
            except Exception:
                continue
            color = str(entry.get("color") or "").strip()
            if not (gsm and bf and deckle and color):
                continue
            template = False
            template_id = entry.get("material_template_id") or entry.get("product_template_id") or entry.get("template_id")
            try:
                template_id = int(template_id or 0)
            except Exception:
                template_id = 0
            if template_id:
                template = self.env["product.template"].sudo().with_context(active_test=False).browse(template_id).exists()
            if not template:
                template_code = (
                    entry.get("material_template_code")
                    or entry.get("material_code")
                    or entry.get("material_template_name")
                    or entry.get("material_name")
                )
                template = self._corr_find_material_template(template_code)
            if not template:
                continue
            key = (template.id, round(gsm, 4), round(bf, 4), color.lower(), round(deckle, 4))
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                {
                    "template": template,
                    "gsm": gsm,
                    "bf": bf,
                    "color": color,
                    "deckle": deckle,
                }
            )
        return specs

    def _corr_autocreate_missing_material_variants_from_payload(self, payload):
        self.ensure_one()
        if not self._corr_bulk_upload_auto_create_missing_materials_enabled():
            return []
        specs = self._corr_missing_material_specs_from_preview_payload(payload)
        if not specs:
            return []
        created_ids = []
        Wizard = self.env["corr.missing.kraft.variant.wizard"].sudo()
        for spec in specs:
            with self.env.cr.savepoint():
                template = spec["template"]
                before_ids = set(template.product_variant_ids.ids)
                wizard = Wizard.create({"product_template_id": template.id})
                wizard.line_ids.create(
                    {
                        "wizard_id": wizard.id,
                        "deckle": spec["deckle"],
                        "color": spec["color"],
                        "gsm": spec["gsm"],
                        "bf": spec["bf"],
                    }
                )
                wizard.action_create_variants()
                template.invalidate_recordset(["product_variant_ids"])
                after_ids = set(template.product_variant_ids.ids)
                created_ids.extend(sorted(after_ids - before_ids))
        return list(dict.fromkeys(created_ids))

    def _corr_run_v2_preview_and_persist(self, spec):
        self.ensure_one()
        auto_created_material_variant_ids = []
        for attempt in range(2):
            wizard = self.env["corr.engine_v2.debug.wizard"].sudo().create(
                {
                    "v2_spec_id": spec.id,
                    "plant_id": spec.plant_id.id if spec.plant_id else False,
                    "order_qty": 1.0,
                    "preview_backend": "stagecontext",
                    "bom_backend": "stagecontext_shadow",
                }
            )
            try:
                wizard.action_run_debug()
            except Exception as exc:
                message = str(exc)
                spec.store_last_preview_snapshot(
                    raw_json=False,
                    status="failed",
                    error=message,
                )
                raise UserError(_("Preview failed: %s") % message) from exc

            raw_json = wizard.raw_json or False
            preview_warning = wizard.preview_warning or False
            preview_is_current = bool(wizard.preview_is_current)
            if not raw_json or not preview_is_current:
                error = preview_warning or _("Preview failed to produce a usable payload.")
                spec.store_last_preview_snapshot(
                    raw_json=raw_json,
                    status="failed",
                    error=error,
                )
                raise UserError(_("Preview failed: %s") % error)

            try:
                payload = json.loads(raw_json or "{}")
            except Exception as exc:
                spec.store_last_preview_snapshot(
                    raw_json=raw_json,
                    status="failed",
                    error=_("Preview payload is invalid: %s") % exc,
                )
                raise UserError(_("Preview failed: Preview payload is invalid: %s") % exc) from exc

            errors = self._corr_preview_material_resolution_errors(payload)
            if errors and attempt == 0:
                created_ids = self._corr_autocreate_missing_material_variants_from_payload(payload)
                if created_ids:
                    auto_created_material_variant_ids.extend(created_ids)
                    continue
            if errors:
                exc = UserError("\n".join(errors))
                exc.auto_created_material_variant_ids = list(dict.fromkeys(auto_created_material_variant_ids))
                raise exc
            break
        else:
            raise UserError(_("Preview failed to produce a usable payload."))

        spec.store_last_preview_snapshot(
            raw_json=raw_json,
            status="success",
            error=False,
        )
        return {
            "wizard_id": wizard.id,
            "raw_json": raw_json,
            "preview_warning": preview_warning,
            "preview_backend_applied": wizard.preview_backend_applied or "legacy",
            "auto_created_material_variant_ids": list(dict.fromkeys(auto_created_material_variant_ids)),
        }

    def _corr_create_v2_bom_from_persisted_preview(self, spec):
        self.ensure_one()
        spec.ensure_one()

        if spec.last_preview_status != "success":
            raise UserError(_("Persisted preview payload is not in success state."))
        if not spec.last_preview_raw_json:
            raise UserError(_("Persisted preview payload is missing."))

        try:
            payload = json.loads(spec.last_preview_raw_json or "{}")
        except Exception as exc:
            raise UserError(_("Persisted preview payload is invalid: %s") % exc) from exc
        if not isinstance(payload, dict) or not payload:
            raise UserError(_("Persisted preview payload is invalid."))
        self._corr_validate_v2_preview_material_payload(payload)

        product_tmpl = spec.product_tmpl_id or self
        fg_product = product_tmpl.product_variant_id
        if not fg_product:
            raise UserError(_("Finished product variant is missing for BoM creation."))

        order_qty = 1.0
        try:
            order_qty = float(payload.get("order_qty") or 1.0)
        except Exception:
            order_qty = 1.0
        if order_qty <= 0.0:
            order_qty = 1.0

        existing_bom = False
        signature = False
        try:
            signature = payload.get("bom_creation_diagnostics", {}).get("signature") or False
        except Exception:
            signature = False
        if not signature:
            try:
                from odoo.addons.corr_mrp_bridge import bom_adapter as corr_bom_adapter
            except Exception:
                try:
                    from custom_addons.corr_mrp_bridge import bom_adapter as corr_bom_adapter
                except Exception:
                    corr_bom_adapter = False
            if corr_bom_adapter:
                try:
                    signature = corr_bom_adapter._compute_signature_from_preview_payload(payload)
                except Exception:
                    signature = False
        if signature:
            Bom = self.env["mrp.bom"].sudo()
            if "corr_engine_v2_signature" in getattr(Bom, "_fields", {}):
                existing_bom = Bom.search(
                    [
                        ("product_tmpl_id", "=", product_tmpl.id),
                        ("corr_engine_v2_signature", "=", signature),
                    ],
                    limit=1,
                )

        DebugWizard = self.env["corr.engine_v2.debug.wizard"].sudo()
        wizard = DebugWizard.create(
            {
                "v2_spec_id": spec.id,
                "plant_id": spec.plant_id.id if spec.plant_id else False,
                "order_qty": order_qty,
                "preview_backend": "stagecontext",
                "bom_backend": "stagecontext_shadow",
            }
        )
        wizard.write(
            {
                "raw_json": spec.last_preview_raw_json,
                "preview_is_current": True,
                "preview_warning": False,
                "preview_backend_applied": payload.get("preview_backend_applied") or "stagecontext",
            }
        )

        try:
            action = wizard.action_create_bom_from_preview()
        except Exception as exc:
            message = str(exc or "")
            lowered = message.lower()
            if any(token in lowered for token in ("component", "product not found", "variant not found", "missing product")):
                raise UserError(_("BoM materialization failed: component product not found. %s") % message) from exc
            if any(token in lowered for token in ("bom line", "create", "update", "write")):
                raise UserError(_("BoM write failed: unable to create/update BoM line. %s") % message) from exc
            raise UserError(_("BoM materialization failed: %s") % message) from exc
        if not isinstance(action, dict) or action.get("res_model") != "mrp.bom":
            raise UserError(_("Engine V2 BoM creation did not return a BoM action."))

        Bom = self.env["mrp.bom"].sudo()
        bom = False
        if action.get("res_id"):
            bom = Bom.browse(int(action["res_id"])).exists()
        elif action.get("domain"):
            bom = Bom.search(action["domain"], order="id desc", limit=1)
        if not bom:
            raise UserError(_("Engine V2 BoM creation did not return a BoM record."))

        return {
            "bom_id": bom.id,
            "bom_created": not bool(existing_bom),
            "bom_reused": bool(existing_bom),
            "bom_backend_applied": "stagecontext_shadow",
            "signature": getattr(bom, "corr_engine_v2_signature", False) or signature or False,
            "diagnostics": {
                "preview_payload_source": "persisted_spec_snapshot",
                "bom_action_name": action.get("name"),
                "bom_action_view_mode": action.get("view_mode"),
                "wizard_id": wizard.id,
            },
        }

    def action_corr_compute_preview(self):
        self.ensure_one()
        if self._corr_active_engine() == "v2":
            SpecV2 = self.env["corr.engine.v2.spec"].sudo()
            spec_v2 = SpecV2.search([("product_tmpl_id", "=", self.id)], limit=1)
            if not spec_v2:
                try:
                    with self.env.cr.savepoint():
                        validation = self._corr_autobom_v2_validate_inputs()
                        spec_v2, _created = self._corr_upsert_v2_spec(validation)
                except Exception as exc:
                    raise UserError(
                        _(
                            "Engine V2 is enabled, but no Engine V2 spec could be opened "
                            "or created for this product.\n%s"
                        )
                        % (exc,)
                    ) from exc
            if not spec_v2:
                raise UserError(
                    _("Engine V2 is enabled, but no Engine V2 spec exists for this product.")
                )

            action = self.env.ref(
                "corr_engine_v2.action_corr_engine_v2_spec",
                raise_if_not_found=False,
            )
            if action:
                result = action.read()[0]
            else:
                result = {
                    "type": "ir.actions.act_window",
                    "name": _("Engine V2 Spec"),
                    "res_model": "corr.engine.v2.spec",
                    "view_mode": "form",
                    "target": "current",
                }
            form_view = self.env.ref(
                "corr_engine_v2.view_corr_engine_v2_spec_form",
                raise_if_not_found=False,
            )
            result.update(
                {
                    "name": _("Engine V2 Spec"),
                    "res_model": "corr.engine.v2.spec",
                    "view_mode": "form",
                    "views": [(form_view.id, "form")] if form_view else [(False, "form")],
                    "target": "current",
                    "res_id": spec_v2.id,
                }
            )
            return result

        # Old engine mode is intentionally legacy-only.
        Spec = self.env["corr.spec.template"].sudo()
        spec = Spec.search([("product_tmpl_id", "=", self.id)], limit=1)
        if not spec:
            try:
                with self.env.cr.savepoint():
                    self._corr_autobom_legacy_maybe()
            except Exception:
                pass
            spec = Spec.search([("product_tmpl_id", "=", self.id)], limit=1)
        if not spec:
            # Last resort: build a spec via engine but bypass stack checks
            engine = self.env["corr.formula.engine"].with_context(skip_corr_stack_check=True)
            spec = engine.map_product_to_spec(self)
        return {
            "type": "ir.actions.act_window",
            "name": _("Spec Template"),
            "res_model": "corr.spec.template",
            "view_mode": "form",
            "target": "current",
            "res_id": spec.id,
        }

    # ---------------------
    # Auto-BoM on create
    # ---------------------
    @api.model_create_multi
    def create(self, vals_list):
        # Allow internal creators (bridge) to bypass auto-BoM completely
        if self.env.context.get("skip_corr_autobom"):
            return super().create(vals_list)
        # Pre-process import helper names into M2O ids for category and UoM
        new_vals_list = []
        Cat = self.env["product.category"].sudo()
        Uom = self.env["uom.uom"].sudo()
        for vals in vals_list:
            v = dict(vals)
            # Category by name if categ_id not provided
            categ_name = v.pop("import_categ_name", None) or v.pop("categ_name", None)
            if categ_name and not v.get("categ_id"):
                cat = Cat.search([("name", "=ilike", str(categ_name).strip())], limit=1)
                if cat:
                    v["categ_id"] = cat.id
            # UoM by name if uom_id not provided
            uom_name = v.pop("import_uom_name", None) or v.pop("uom_name", None)
            if uom_name and not v.get("uom_id"):
                uom = Uom.search([("name", "=ilike", str(uom_name).strip())], limit=1)
                if uom:
                    v["uom_id"] = uom.id
                    # also set purchase UoM if not specified
                    if not v.get("uom_po_id"):
                        v["uom_po_id"] = uom.id
            # Optional: Map tax rates to taxes if explicit ids not provided
            # Sales
            if not v.get("taxes_id"):
                rate = self._corr_parse_rate(v.pop("sales_tax_rate", None))
                inc = self._corr_parse_bool(v.pop("sales_tax_price_included", None), default=False)
                if rate is not None:
                    company_id = v.get("company_id") or self.env.company.id
                    tax = self._corr_find_or_create_tax(rate, scope="sale", price_included=inc, company_id=company_id)
                    if tax:
                        v["taxes_id"] = [(6, 0, [tax.id])]
            # Purchase
            if not v.get("supplier_taxes_id"):
                prate = self._corr_parse_rate(v.pop("purchase_tax_rate", None))
                pinc = self._corr_parse_bool(v.pop("purchase_tax_price_included", None), default=False)
                if prate is not None:
                    company_id = v.get("company_id") or self.env.company.id
                    ptax = self._corr_find_or_create_tax(prate, scope="purchase", price_included=pinc, company_id=company_id)
                    if ptax:
                        v["supplier_taxes_id"] = [(6, 0, [ptax.id])]
            # Optional: Map diecut codes (comma-separated) to diecut_ids
            dcodes = v.pop("diecut_codes", None)
            if dcodes and not v.get("diecut_ids"):
                try:
                    codes = [c.strip() for c in str(dcodes).split(",") if c and str(c).strip()]
                except Exception:
                    codes = []
                if codes:
                    Die = self.env["corr.diecut"].sudo()
                    recs = Die.search([("code", "in", codes)])
                    if recs:
                        v["diecut_ids"] = [(6, 0, recs.ids)]
            new_vals_list.append(v)
        if any(self._corr_should_upsert_existing_product_on_create(vals) for vals in new_vals_list):
            result_ids = []
            for vals in new_vals_list:
                if self._corr_should_upsert_existing_product_on_create(vals):
                    existing = self._corr_find_existing_product_for_upsert(vals)
                    if existing:
                        upsert_vals = dict(vals)
                        # Import rows that match an archived product by
                        # default_code should revive that product unless the
                        # file explicitly controls active state. Otherwise the
                        # upload reports success but the record stays hidden in
                        # standard active-only product views.
                        if "active" not in upsert_vals and not bool(existing.active):
                            upsert_vals["active"] = True
                        existing.with_context(skip_corr_autobom=True).write(upsert_vals)
                        result_ids.append(existing.id)
                        continue
                created = super().create([vals])
                result_ids.extend(created.ids)
            products = self.env["product.template"].sudo().browse(result_ids)
        else:
            products = super().create(new_vals_list)
        # Always try to sync attributes on create
        for tmpl in products:
            try:
                tmpl._corr_sync_attributes()
            except Exception:
                pass
        icp = self.env["ir.config_parameter"].sudo()
        enabled = icp.get_param("corr_mrp_bridge.autobom_enabled", default="1") == "1"
        if not enabled:
            return products
        for tmpl in products:
            try:
                # Run auto-BoM inside a savepoint so that any SQL error
                # does not abort the main transaction (which would later
                # explode as InFailedSqlTransaction during mail/followers).
                with self.env.cr.savepoint():
                    tmpl._corr_autobom_maybe()
            except Exception as e:
                # Do not block create; log to server log + chatter
                _logger.exception(
                    "Auto BoM generation failed during product.create for template %s (id=%s)",
                    getattr(tmpl, "display_name", repr(tmpl)),
                    getattr(tmpl, "id", "n/a"),
                )
                try:
                    tmpl.message_post(body=("Auto BoM generation failed: %s" % (str(e))))
                except Exception:
                    pass
        return products

    def write(self, vals):
        res = super().write(vals)
        # Map tax rate helpers on update if provided and explicit ids not set
        sales_rate = vals.pop("sales_tax_rate", None)
        sales_inc = vals.pop("sales_tax_price_included", None)
        purchase_rate = vals.pop("purchase_tax_rate", None)
        purchase_inc = vals.pop("purchase_tax_price_included", None)
        if sales_rate is not None and not vals.get("taxes_id"):
            rate = self._corr_parse_rate(sales_rate)
            inc = self._corr_parse_bool(sales_inc, default=False)
            if rate is not None:
                for tmpl in self:
                    tax = tmpl._corr_find_or_create_tax(rate, scope="sale", price_included=inc, company_id=tmpl.company_id.id)
                    if tax:
                        tmpl.write({"taxes_id": [(6, 0, [tax.id])]})
        if purchase_rate is not None and not vals.get("supplier_taxes_id"):
            prate = self._corr_parse_rate(purchase_rate)
            pinc = self._corr_parse_bool(purchase_inc, default=False)
            if prate is not None:
                for tmpl in self:
                    ptax = tmpl._corr_find_or_create_tax(prate, scope="purchase", price_included=pinc, company_id=tmpl.company_id.id)
                    if ptax:
                        tmpl.write({"supplier_taxes_id": [(6, 0, [ptax.id])]})
        # Map diecut_codes -> diecut_ids on update as well (bulk-friendly)
        if "diecut_codes" in vals:
            Die = self.env["corr.diecut"].sudo()
            for tmpl in self:
                dcodes = tmpl.diecut_codes
                try:
                    codes = [c.strip() for c in str(dcodes or "").split(",") if c and str(c).strip()]
                except Exception:
                    codes = []
                if codes:
                    recs = Die.search([("code", "in", codes)])
                    tmpl.write({"diecut_ids": [(6, 0, recs.ids)]})
        icp = self.env["ir.config_parameter"].sudo()
        enabled = icp.get_param("corr_mrp_bridge.autobom_enabled", default="1") == "1"
        if not enabled or self.env.context.get("skip_corr_autobom"):
            return res
        corr_keys = [
            "corr_style", "corr_length_mm", "corr_width_mm", "corr_height_mm", "corr_ply_count",
            "corr_printing_required", "corr_print_colors", "corr_coverage_pct",
            "corr_lamination_code", "corr_locking_code",
            "corr_plant_code", "corr_routing_profile_code", "corr_board_recipe_code", "corr_fg_per_sheet",
            # any layer fields
        ]
        layer_keys = []
        for i in range(1, 10):
            layer_keys += [
                f"corr_layer{i}_role",
                f"corr_layer{i}_gsm",
                f"corr_layer{i}_bf",
                f"corr_layer{i}_flute_code",
                f"corr_layer{i}_material_template",
                f"corr_layer{i}_color",
            ]
        v2_multi_piece_keys = [
            "corr_v2_multi_piece_enabled",
            "corr_v2_multi_piece_strategy",
            "corr_v2_multi_piece_split_axis",
            "corr_v2_multi_piece_split_mode",
            "corr_v2_multi_piece_source_length_mm",
            "corr_v2_multi_piece_source_width_mm",
            "corr_v2_multi_piece_merge_stage_key",
            "corr_v2_piece1_code",
            "corr_v2_piece1_name",
            "corr_v2_piece1_length_mm",
            "corr_v2_piece1_width_mm",
            "corr_v2_piece1_qty_per_fg",
            "corr_v2_piece1_top_sheet_mode",
            "corr_v2_piece1_top_sheet_code",
            "corr_v2_piece2_code",
            "corr_v2_piece2_name",
            "corr_v2_piece2_length_mm",
            "corr_v2_piece2_width_mm",
            "corr_v2_piece2_qty_per_fg",
            "corr_v2_piece2_top_sheet_mode",
            "corr_v2_piece2_top_sheet_code",
        ]
        for piece_no in (1, 2):
            for layer_no in range(1, 10):
                v2_multi_piece_keys += [
                    f"corr_v2_piece{piece_no}_layer{layer_no}_role",
                    f"corr_v2_piece{piece_no}_layer{layer_no}_gsm",
                    f"corr_v2_piece{piece_no}_layer{layer_no}_bf",
                    f"corr_v2_piece{piece_no}_layer{layer_no}_flute_code",
                    f"corr_v2_piece{piece_no}_layer{layer_no}_material_template",
                    f"corr_v2_piece{piece_no}_layer{layer_no}_color",
                ]
        # Also trigger when BMC/top-sheet extra params change (so spec.extra_params_json stays in sync)
        xp_keys = [
            "corr_top_layer_mode", "corr_dimensions_mode", "corr_printing_ups", "corr_liner_slitting_ups",
            "corr_sheet_waste_pct", "corr_setup_sheets", "corr_pack_size", "corr_round_to_pack",
            "corr_print_band_mm", "corr_adhesive_factor_top_sheet",
            # Ensure updates to top-sheet selectors also refresh the spec/BoM
            "corr_top_sheet_code", "corr_top_sheet_roll_code", "corr_top_sheet_product_id",
        ]
        keys = set(vals.keys())
        if keys.intersection(set(corr_keys + layer_keys + xp_keys + v2_multi_piece_keys)):
            for tmpl in self:
                try:
                    # Keep descriptive attributes in sync from imported corr_* fields
                    try:
                        tmpl._corr_sync_attributes()
                    except Exception:
                        pass
                    # Same pattern as in create(): run auto-BoM in a savepoint
                    # to avoid poisoning the whole transaction on SQL errors.
                    with self.env.cr.savepoint():
                        tmpl._corr_autobom_maybe()
                except Exception:
                    # Log error but do not block write; chatter will also carry details
                    _logger.exception(
                        "Auto BoM generation failed during product.write for template %s (id=%s)",
                        getattr(tmpl, "display_name", repr(tmpl)),
                        getattr(tmpl, "id", "n/a"),
                    )
        return res

    def _corr__read_layers(self):
        self.ensure_one()
        layers = []
        for i in range(1, 10):
            role = self["corr_layer%u_role" % i]
            gsm = self["corr_layer%u_gsm" % i]
            flute = self["corr_layer%u_flute_code" % i]
            bf = getattr(self, f"corr_layer{i}_bf", None)
            if role and gsm:
                rec = {"role": role, "gsm": float(gsm)}
                if role == "medium" and flute:
                    rec["flute_code"] = flute
                if bf:
                    try:
                        bf_f = float(bf)
                        if bf_f > 0:
                            rec["bf"] = bf_f
                    except Exception:
                        pass
                layers.append(rec)
        return layers

    def _corr_autobom_maybe(self):
        self.ensure_one()
        if self._corr_bulk_upload_use_engine_v2():
            return self._corr_autobom_v2_maybe()
        return self._corr_autobom_legacy_maybe()

    def _corr_autobom_v2_maybe(self):
        self.ensure_one()
        if self.corr_autobom_skip:
            return False
        if not (
            self.corr_style
            and self.corr_length_mm
            and self.corr_width_mm
            and (self.corr_ply_count or 0) > 0
        ):
            return False
        try:
            validation = self._corr_autobom_v2_validate_inputs()
        except UserError as exc:
            message = exc.args[0] if exc.args else str(exc)
            self._corr_set_v2_failure(
                "validation",
                message,
                payload={
                    "style_code": self.corr_style or False,
                    "plant_code": self.corr_plant_code or False,
                    "routing_profile_code": self.corr_routing_profile_code or False,
                    "board_recipe_code": self.corr_board_recipe_code or False,
                },
            )
            return False
        try:
            spec, created = self._corr_upsert_v2_spec(validation)
            self._corr_validate_v2_spec_material_resolution(spec)
        except Exception as exc:
            _logger.exception(
                "Engine V2 spec sync failed for template %s (id=%s)",
                getattr(self, "display_name", repr(self)),
                getattr(self, "id", "n/a"),
            )
            is_material_resolution_error = isinstance(exc, UserError) and "material variant" in str(exc).lower()
            message = (exc.args[0] if getattr(exc, "args", None) else str(exc)) if is_material_resolution_error else _("Engine V2 spec sync failed: %s") % exc
            failure_stage = "material_resolution" if is_material_resolution_error else "spec_upsert"
            self._corr_set_v2_failure(
                failure_stage,
                message,
                payload={
                    "validation_summary": {
                        "style_id": validation["style"].id if validation.get("style") else False,
                        "plant_id": validation["plant"].id if validation.get("plant") else False,
                        "routing_profile_id": validation["routing_profile"].id if validation.get("routing_profile") else False,
                        "board_recipe_id": validation["board_recipe"].id if validation.get("board_recipe") else False,
                        "layer_count": len(validation.get("layer_data") or []),
                    },
                },
            )
            return False
        try:
            self._corr_set_autobom_status(
                "idle",
                "v2",
                _("Engine V2 spec synchronized. Preview pending."),
                spec=spec,
                preview_status="pending",
                bom_status="pending",
            )
            preview_result = self._corr_run_v2_preview_and_persist(spec)
        except Exception as exc:
            message = exc.args[0] if getattr(exc, "args", None) else str(exc)
            auto_created_material_variant_ids = getattr(exc, "auto_created_material_variant_ids", []) or []
            self._corr_set_v2_failure(
                "preview",
                message,
                spec=spec,
                payload={
                    "spec_id": spec.id,
                    "preview_status": getattr(spec, "last_preview_status", False) or False,
                    "preview_error": getattr(spec, "last_preview_error", False) or False,
                    "auto_created_material_variant_ids": auto_created_material_variant_ids,
                    "auto_created_product_ids": [],
                    "autofix": {
                        "auto_created_material_variant_ids": auto_created_material_variant_ids,
                        "auto_created_product_ids": [],
                    },
                },
            )
            return False
        try:
            self._corr_set_autobom_status(
                "idle",
                "v2",
                _("Engine V2 preview persisted. BoM creation pending."),
                spec=spec,
                preview_status="success",
                bom_status="pending",
            )
            bom_result = self._corr_create_v2_bom_from_persisted_preview(spec)
        except Exception as exc:
            message = exc.args[0] if getattr(exc, "args", None) else str(exc)
            auto_created_material_variant_ids = preview_result.get("auto_created_material_variant_ids") or []
            self._corr_set_v2_failure(
                "bom",
                message,
                spec=spec,
                payload={
                    "spec_id": spec.id,
                    "preview_status": getattr(spec, "last_preview_status", False) or False,
                    "preview_generated_at": getattr(spec, "last_preview_generated_at", False) or False,
                    "auto_created_material_variant_ids": auto_created_material_variant_ids,
                    "auto_created_product_ids": [],
                    "autofix": {
                        "auto_created_material_variant_ids": auto_created_material_variant_ids,
                        "auto_created_product_ids": [],
                    },
                },
            )
            return False

        message = _("Engine V2 spec, preview, and BoM created/updated successfully.")
        self._corr_set_autobom_status(
            "success",
            "v2",
            message,
            spec=spec,
            preview_status="success",
            bom_status="success",
            error_payload={
                "spec_id": spec.id,
                "spec_created": bool(created),
                "preview_wizard_id": preview_result["wizard_id"],
                "preview_backend_applied": preview_result["preview_backend_applied"],
                "bom_id": bom_result["bom_id"],
                "bom_created": bom_result["bom_created"],
                "bom_backend_applied": bom_result["bom_backend_applied"],
                "auto_created_material_variant_ids": preview_result.get("auto_created_material_variant_ids") or [],
                "auto_created_product_ids": [],
                "autofix": {
                    "auto_created_material_variant_ids": preview_result.get("auto_created_material_variant_ids") or [],
                    "auto_created_product_ids": [],
                },
            },
        )
        try:
            self.message_post(body=("Auto BoM generation passed: %s" % message))
        except Exception:
            pass
        return {
            "status": "success",
            "spec_id": spec.id,
            "spec_created": bool(created),
            "style_id": validation["style"].id,
            "plant_id": validation["plant"].id,
            "routing_profile_id": validation["routing_profile"].id,
            "board_recipe_id": validation["board_recipe"].id if validation.get("board_recipe") else False,
            "layer_count": len(validation.get("layer_data") or []),
            "fg_per_sheet": validation.get("fg_per_sheet") or False,
            "preview_wizard_id": preview_result["wizard_id"],
            "preview_backend_applied": preview_result["preview_backend_applied"],
            "bom_id": bom_result["bom_id"],
            "bom_created": bom_result["bom_created"],
            "bom_backend_applied": bom_result["bom_backend_applied"],
            "auto_created_material_variant_ids": preview_result.get("auto_created_material_variant_ids") or [],
        }

    def _corr_autobom_legacy_maybe(self):
        self.ensure_one()
        if self.corr_autobom_skip:
            return False
        icp = self.env["ir.config_parameter"].sudo()
        scope = icp.get_param("corr_mrp_bridge.autobom_scope", default="template") or "template"
        include_ops = icp.get_param("corr_mrp_bridge.default_include_ops", default="1") == "1"
        include_sfg = icp.get_param("corr_mrp_bridge.default_include_sfg", default="1") == "1"
        include_scrap = icp.get_param("corr_mrp_bridge.default_include_scrap", default="1") == "1"
        overhead_1000 = float(icp.get_param("corr_mrp_bridge.default_overhead_per_1000", default="0") or 0.0)
        # New setting: staged SFG mode (overrides legacy boolean when set)
        # Default to staged SFGs when not explicitly configured; allow per-product/spec override
        sfg_mode_global = icp.get_param("corr_mrp_bridge.sfg_mode") or "staged"
        sfg_mode = sfg_mode_global
        try:
            mode_pt = (self.corr_sfg_mode or "").strip()
            if mode_pt and mode_pt != "inherit":
                sfg_mode = mode_pt
        except Exception:
            sfg_mode = sfg_mode_global

        # Normalize style (handle Excel dropping leading zero)
        original_style = (self.corr_style or "0201")
        style_raw = str(original_style).strip()
        style_norm = style_raw
        try:
            if style_raw.isdigit():
                style_norm = str(style_raw).zfill(4)
        except Exception:
            pass
        # Allow sheet/pad/bmc where height may be zero
        allowed_styles = {"0201", "0202", "0203", "MONO_STE", "SHEET", "PAD", "BMC-Custom"}
        style_used = style_norm if style_norm in allowed_styles else "0201"

        # Gating: require minimum corr fields
        # Always need Style, L, W, Ply
        if not (self.corr_style and self.corr_length_mm and self.corr_width_mm and (self.corr_ply_count or 0) > 0):
            # Post a helpful chatter note explaining what is missing
            try:
                missing = []
                if not self.corr_style:
                    missing.append("corr_style")
                if not self.corr_length_mm:
                    missing.append("corr_length_mm")
                if not self.corr_width_mm:
                    missing.append("corr_width_mm")
                if not (self.corr_ply_count or 0) > 0:
                    missing.append("corr_ply_count")
                if missing:
                    self.message_post(body=("Auto BoM skipped: missing required fields: %s" % ", ".join(missing)))
            except Exception:
                pass
            return False
        # For box and mono-carton styles also require Height.
        if style_used in ("0201", "0202", "0203", "MONO_STE") and not self.corr_height_mm:
            try:
                self.message_post(body=("Auto BoM skipped: 0201/0202/0203/MONO_STE requires corr_height_mm > 0"))
            except Exception:
                pass
            return False

        layers = self._corr__read_layers()
        if not layers:
            try:
                self.message_post(body=("Auto BoM skipped: no corrugated layers provided (corr_layer*_*)"))
            except Exception:
                pass
            return False

        # Resolve optional lamination/locking by code
        Lam = self.env["corr.lamination.type"].sudo()
        Lock = self.env["corr.locking.type"].sudo()
        # Lamination: try by code, then by name (ilike)
        lam = False
        lam_code = (self.corr_lamination_code or "").strip()
        if lam_code:
            lam = Lam.search([("code", "=", lam_code)], limit=1)
            if not lam:
                lam = Lam.search([("name", "ilike", lam_code)], limit=1)
        # Locking: accept code, name, or method synonyms (glue/wire/tape)
        lock = False
        lock_code = (self.corr_locking_code or "").strip()
        if lock_code:
            lock = Lock.search([("code", "=", lock_code)], limit=1)
            if not lock:
                # Try by name contains
                lock = Lock.search([("name", "ilike", lock_code)], limit=1)
            if not lock:
                val = lock_code.lower()
                # Common human inputs mapped to method
                method = None
                if val in {"wire", "wired", "stitch", "stitching", "sticthing", "staple", "stapling"}:
                    method = "wire"
                elif val in {"glue", "glued", "pasting", "paste"}:
                    method = "glue"
                elif val in {"tape", "taped", "taping"}:
                    method = "tape"
                if method:
                    lock = Lock.search([("method", "=", method)], limit=1)

        # Build stack lines (One2many) from layers
        Flute = self.env["corr.flute"].sudo()
        line_cmds = []
        seq = 10
        for layer in layers:
            role = (layer.get("role") or "").lower()
            gsm = float(layer.get("gsm") or 0.0)
            vals_line = {"sequence": seq, "role": role, "gsm": gsm}
            if layer.get("bf"):
                try:
                    bf_f = float(layer.get("bf"))
                    if bf_f > 0:
                        vals_line["bf"] = bf_f
                except Exception:
                    pass
            if role == "medium":
                flute_code = (layer.get("flute_code") or "").strip()
                fl = Flute.search([("code", "=", flute_code)], limit=1) if flute_code else False
                if not fl:
                    raise UserError(_(f"Unknown or missing flute code for medium layer (seq {seq}): '{flute_code}'"))
                vals_line["flute_id"] = fl.id
            line_cmds.append((0, 0, vals_line))
            seq += 10

        # Upsert spec for this product
        Spec = self.env["corr.spec.template"].sudo().with_context(skip_corr_stack_check=True)
        spec = Spec.search([("product_tmpl_id", "=", self.id)], limit=1)
        # Compose spec name with product name + item code (default_code) when available
        code = None
        try:
            # default_code may live on template or on a single variant; prefer template then main variant
            code = getattr(self, "default_code", None)
            if not code and self.product_variant_id:
                code = self.product_variant_id.default_code
        except Exception:
            code = None
        spec_name = f"Spec for {self.name}"
        if code:
            spec_name = f"Spec for {self.name} [{code}]"
        # Base dimensions in mm from import fields
        Lmm = float(self.corr_length_mm or 0.0)
        Wmm = float(self.corr_width_mm or 0.0)
        Hmm = float(self.corr_height_mm or 0.0)
        vals = {
            "name": spec_name,
            "product_tmpl_id": self.id,
            "style": style_used,
            "length_mm": Lmm,
            "width_mm": Wmm,
            "height_mm": Hmm,
            # Mirror mm into cm so Spec form shows correct values even when created via bulk import
            "length_cm": Lmm / 10.0 if Lmm else 0.0,
            "width_cm": Wmm / 10.0 if Wmm else 0.0,
            "height_cm": Hmm / 10.0 if Hmm else 0.0,
            "ply_count": int(self.corr_ply_count or len(layers) or 0),
            "printing_required": bool(self.corr_printing_required or False),
            "print_colors": int(self.corr_print_colors or 0),
            "coverage_pct": float(self.corr_coverage_pct or 0.0),
            "lamination_type_id": lam.id if lam else False,
            "locking_type_id": lock.id if lock else False,
        }
        # Build extra params JSON for BMC and printing options
        xp = {}
        def _put(key, val, coerce=None):
            if val is None or val == "":
                return
            try:
                if coerce is float:
                    xp[key] = float(val)
                elif coerce is int:
                    xp[key] = int(val)
                elif coerce == 'bool':
                    xp[key] = str(val).strip().lower() in ("1","true","yes","y") if not isinstance(val, bool) else bool(val)
                else:
                    xp[key] = str(val)
            except Exception:
                xp[key] = val

        _put("top_layer_mode", self.corr_top_layer_mode)
        # Dimensions mode: honor product value; else fallback to system default parameter
        # System parameter: corr_mrp_bridge.default_dimensions_mode -> 'sheet' or 'per_box'
        dim_default = (icp.get_param("corr_mrp_bridge.default_dimensions_mode", default="sheet") or "sheet").strip().lower()
        dim_raw = (self.corr_dimensions_mode or dim_default or "sheet")
        try:
            dim_norm = str(dim_raw).strip().lower()
        except Exception:
            dim_norm = "sheet"
        # Accept a few human variants from CSV (spaces, hyphens, short forms)
        per_box_aliases = {
            "per_box", "perbox", "box", "blank", "1", "true", "yes", "y",
            "per box", "per-box", "pb",
        }
        if dim_norm in per_box_aliases:
            xp["dimensions_mode"] = "per_box"
            # Set a redundant boolean flag so UI fallbacks also pick it up
            xp["per_box_dimensions"] = True
        else:
            xp["dimensions_mode"] = "sheet"
            xp["per_box_dimensions"] = False
        _put("printing_ups", self.corr_printing_ups, int)
        _put("liner_slitting_ups", self.corr_liner_slitting_ups, int)
        _put("sheet_waste_pct", self.corr_sheet_waste_pct, float)
        _put("setup_sheets", self.corr_setup_sheets, int)
        _put("pack_size", self.corr_pack_size, int)
        _put("round_to_pack", self.corr_round_to_pack, 'bool')
        _put("print_band_mm", self.corr_print_band_mm, float)
        _put("adhesive_factor_top_sheet", self.corr_adhesive_factor_top_sheet, float)
        # Top sheet product binding
        pid = None
        code = (self.corr_top_sheet_code or "").strip()
        if self.corr_top_sheet_product_id:
            try:
                pid = int(self.corr_top_sheet_product_id.id)
            except Exception:
                pid = None
        elif code:
            try:
                P = self.env["product.product"].sudo()
                prod = P.search([("default_code", "=", code)], limit=1)
                if prod:
                    pid = prod.id
            except Exception:
                pid = None
        if pid:
            xp["top_sheet_product_id"] = int(pid)
        if spec:
            # Replace stack lines to mirror imported layers
            cmds = [(5, 0, 0)] + line_cmds
            vals2 = dict(vals)
            vals2["stack_line_ids"] = cmds
            # Reflect top sheet selectors on the spec for UI convenience
            # When Top Layer Mode is Roll and no explicit override was provided,
            # do not rehydrate these fields; instead clear any existing values
            # so the wizard can auto-pick the roll.
            tlm = (self.corr_top_layer_mode or "").strip().lower()
            if ("roll" in tlm) and not pid and not code:
                vals2["top_sheet_product_id"] = False
                vals2["top_sheet_code"] = False
            else:
                if pid:
                    vals2["top_sheet_product_id"] = pid
                if code:
                    vals2["top_sheet_code"] = code
            if xp:
                vals2["extra_params_json"] = xp
            spec.with_context(skip_corr_stack_check=True).write(vals2)
        else:
            vals2 = dict(vals)
            vals2["stack_line_ids"] = line_cmds
            # For new specs, simply omit the fields in Roll mode when not provided
            tlm = (self.corr_top_layer_mode or "").strip().lower()
            if not (("roll" in tlm) and not pid and not code):
                if pid:
                    vals2["top_sheet_product_id"] = pid
                if code:
                    vals2["top_sheet_code"] = code
            if xp:
                vals2["extra_params_json"] = xp
            spec = Spec.create(vals2)

        # Let the spec override SFG mode when configured; otherwise keep
        # product/global behavior. This keeps existing behavior unchanged
        # for specs where corr_sfg_mode is left to 'inherit' (default).
        try:
            mode_spec = (getattr(spec, "corr_sfg_mode", "") or "inherit").strip()
            if mode_spec and mode_spec != "inherit":
                sfg_mode = mode_spec
        except Exception:
            pass

        # Generate/Update BoM using the existing wizard logic
        # Run BoM wizard under a context that disables auto-BoM hooks on any
        # product.template writes performed by the wizard itself, to avoid
        # accidental recursive _corr_autobom_maybe() calls.
        Wiz = self.env["corr.bom.generate.wizard"].sudo().with_context(
            skip_corr_autobom=True
        )
        wiz = Wiz.create({
            "spec_id": spec.id,
            "target_product_tmpl_id": self.id,
            "include_operations": include_ops,
            "include_scrap_byproduct": include_scrap,
            "overhead_per_1000": overhead_1000,
            "remove_obsolete": True,
            "preview": False,
            "missing_mappings": False,
            # Keep legacy field for backward compatibility (used only when sfg_mode == 'phantom')
            "include_sfg_phantom": (sfg_mode == "phantom"),
            "sfg_mode": sfg_mode,
        })
        try:
            wiz.action_apply()
            self.message_post(body=("Auto BoM generated: ops=%s, sfg=%s, scrap=%s, overhead/1000=%s" % (
                "ON" if include_ops else "OFF",
                "ON" if include_sfg else "OFF",
                "ON" if include_scrap else "OFF",
                overhead_1000,
            )))
            if style_used != style_raw:
                # Post a plain-text note (avoid HTML tags showing in some views)
                self.message_post(body=(
                    "Style normalized: Input='%s' -> Used='%s' (allowed: 0201, 0202, 0203, MONO_STE)" % (style_raw, style_used)
                ))
        except UserError as ue:
            # Be defensive: some exceptions (e.g., ValidationError) may not expose `.name`
            msg = getattr(ue, "name", None)
            if not msg:
                # Prefer first arg if present, else fallback to string repr
                msg = (ue.args[0] if ue.args else str(ue))
            _logger.warning(
                "Auto BoM generation blocked for template %s (id=%s): %s",
                getattr(self, "display_name", repr(self)),
                getattr(self, "id", "n/a"),
                msg,
            )
            self.message_post(body=("Auto BoM generation blocked: %s" % (msg)))
        except Exception as e:
            _logger.exception(
                "Auto BoM generation failed in _corr_autobom_maybe for template %s (id=%s)",
                getattr(self, "display_name", repr(self)),
                getattr(self, "id", "n/a"),
            )
            self.message_post(body=("Auto BoM generation failed: %s" % (str(e))))
        return True

    # --------------------------------------------------
    # Auto-sync product attributes from corr_* fields
    # --------------------------------------------------
    def _corr_sync_attributes(self):
        self.ensure_one()
        # Derive a small set of descriptive attributes from corr fields.
        # This helps downstream flows (e.g., SFG selection by Flute)
        try:
            # Build a concise board stack summary for end-user visibility
            # Example: "3-Ply B: 200/120/150 gsm"
            def _fmt_num(val):
                try:
                    f = float(val)
                    if float(int(f)) == f:
                        return str(int(f))
                    return ("{:.2f}".format(f)).rstrip('0').rstrip('.')
                except Exception:
                    return str(val)

            layers = []
            try:
                layers = self._corr__read_layers()
            except Exception:
                layers = []
            ply_val = 0
            try:
                ply_val = int(self.corr_ply_count or (len(layers) or 0))
            except Exception:
                ply_val = len(layers) if layers else 0
            # Collect flute codes from medium layers in order (unique preserve order)
            flute_codes = []
            for rec in (layers or []):
                code = (rec.get("flute_code") or "").strip()
                if code and code not in flute_codes:
                    flute_codes.append(code)
            flutes_part = " + ".join(flute_codes) if flute_codes else ""
            # GSMs in layer order (show integers without .0)
            gsm_list = []
            for rec in (layers or []):
                gsm = rec.get("gsm")
                if gsm is not None and gsm != "":
                    gsm_list.append(_fmt_num(gsm))
            gsm_part = "/".join(gsm_list) if gsm_list else ""
            board_stack_val = None
            if ply_val:
                if flutes_part and gsm_part:
                    board_stack_val = f"{ply_val}-Ply {flutes_part}: {gsm_part} gsm"
                elif flutes_part:
                    board_stack_val = f"{ply_val}-Ply {flutes_part}"
                elif gsm_part:
                    board_stack_val = f"{ply_val}-Ply: {gsm_part} gsm"
                else:
                    board_stack_val = f"{ply_val}-Ply"
            # Flute: take the first medium layer's flute code
            flute_val = None
            for i in range(1, 10):
                role = (self["corr_layer%u_role" % i] or "").lower()
                code = (self["corr_layer%u_flute_code" % i] or "").strip()
                if role == "medium" and code:
                    flute_val = code
                    break

            # Style: normalize numeric codes to 4 digits
            style_raw = (self.corr_style or "").strip()
            style_val = style_raw
            try:
                if style_raw and style_raw.isdigit():
                    style_val = str(style_raw).zfill(4)
            except Exception:
                style_val = style_raw

            lam_val = (self.corr_lamination_code or "").strip() or None
            lock_val = (self.corr_locking_code or "").strip() or None

            colors_val = None
            if self.corr_print_colors:
                try:
                    colors_val = str(int(self.corr_print_colors))
                except Exception:
                    colors_val = str(self.corr_print_colors)

            attr_map = {}
            if flute_val:
                attr_map["Flute"] = flute_val
            if style_val:
                attr_map["Style"] = style_val
            if lam_val:
                attr_map["Lamination"] = lam_val
            if lock_val:
                attr_map["Locking"] = lock_val
            if colors_val:
                attr_map["Print Colors"] = colors_val
            if board_stack_val:
                attr_map["Board Stack"] = board_stack_val

            if not attr_map:
                return False

            Attr = self.env["product.attribute"].sudo()
            Val = self.env["product.attribute.value"].sudo()

            def _ensure_attr_value(tmpl, name, value):
                # Skip if user already set this attribute manually
                line = tmpl.attribute_line_ids.filtered(lambda l: l.attribute_id and l.attribute_id.name == name)[:1]
                if line and line.value_ids:
                    return
                attr = Attr.search([("name", "=", name)], limit=1)
                if not attr:
                    attr = Attr.create({
                        "name": name,
                        "create_variant": "no_variant",
                    })
                val = Val.search([("name", "=", value), ("attribute_id", "=", attr.id)], limit=1)
                if not val:
                    val = Val.create({
                        "name": value,
                        "attribute_id": attr.id,
                    })
                if line:
                    line.value_ids = [(6, 0, [val.id])]
                else:
                    tmpl.attribute_line_ids = [(0, 0, {
                        "attribute_id": attr.id,
                        "value_ids": [(6, 0, [val.id])],
                    })]

            for k, v in attr_map.items():
                _ensure_attr_value(self, k, v)
            return True
        except Exception:
            return False


class ProductProductTopSheet(models.Model):
    _inherit = "product.product"

    def _corr_maybe_autocode_top_sheet(self):
        """Auto-assign default_code for Top Printed Sheet variants.

        Rules:
        - Only for templates in the configured Top Sheet category
          (system parameter 'planning.top_sheet_category_name',
           defaulting to 'Top Printed Sheet').
        - Only when the variant's default_code is empty/False.
        - Code pattern: 'TOP:<PrintingPaperCode>', where PrintingPaperCode
          is taken from the configured attribute name
          (system parameter 'planning.top_sheet_attribute_name',
           defaulting to 'Printing Paper Code').
        - Does not override an existing default_code.
        """
        if not self:
            return
        icp = self.env["ir.config_parameter"].sudo()
        cat_name = (icp.get_param("planning.top_sheet_category_name") or "Top Printed Sheet").strip()
        attr_name = (icp.get_param("planning.top_sheet_attribute_name") or "Printing Paper Code").strip()
        if not cat_name or not attr_name:
            return

        to_update = []
        for prod in self:
            try:
                tmpl = prod.product_tmpl_id
                categ = tmpl and tmpl.categ_id or False
            except Exception:
                continue
            # Category gating
            if not (categ and (categ.name or "").strip() == cat_name):
                continue
            # Do not override an existing code
            if prod.default_code:
                continue
            # Look for the Printing Paper Code attribute value on the variant
            code_val = None
            try:
                ptavs = getattr(prod, "product_template_attribute_value_ids", []) or []
            except Exception:
                ptavs = []
            for ptav in ptavs:
                attr = getattr(ptav, "attribute_id", False)
                if not attr:
                    continue
                if (attr.name or "").strip() != attr_name:
                    continue
                # Prefer the explicit value name; fall back to underlying value record
                val = getattr(ptav, "name", None)
                pav = getattr(ptav, "product_attribute_value_id", False)
                if (not val) and pav:
                    val = getattr(pav, "name", None)
                if val:
                    code_val = str(val).strip()
                    break
            if not code_val:
                continue
            full_code = f"TOP:{code_val}"
            to_update.append((prod, full_code))

        # Apply updates with a guard to avoid recursive re-entry into write()
        for prod, full_code in to_update:
            try:
                super(ProductProductTopSheet, prod.with_context(skip_corr_top_autocode=True)).write(
                    {"default_code": full_code}
                )
            except Exception:
                _logger.exception(
                    "Failed to assign auto top-sheet default_code '%s' to product %s (id=%s)",
                    full_code,
                    getattr(prod, "display_name", repr(prod)),
                    getattr(prod, "id", "n/a"),
                )

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        # Auto-code only when not explicitly skipped
        try:
            if not self.env.context.get("skip_corr_top_autocode"):
                products._corr_maybe_autocode_top_sheet()
        except Exception:
            _logger.exception("Error while auto-coding top sheet products on create")
        return products

    def write(self, vals):
        res = super().write(vals)
        # Avoid recursion when we are the ones writing the code
        if self.env.context.get("skip_corr_top_autocode"):
            return res
        try:
            self._corr_maybe_autocode_top_sheet()
        except Exception:
            _logger.exception("Error while auto-coding top sheet products on write")
        return res
