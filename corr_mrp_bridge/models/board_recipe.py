import re

from odoo import api, fields, models


class CorrEngineBoardRecipe(models.Model):
    _name = "corr.engine.board.recipe"
    _description = "Engine Board Recipe"
    _order = "code, name"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    ply_type = fields.Char(string="Ply Type")
    active = fields.Boolean(default=True)

    line_ids = fields.One2many(
        "corr.engine.board.recipe.line",
        "recipe_id",
        string="Layers",
    )


class CorrEngineBoardRecipeLine(models.Model):
    _name = "corr.engine.board.recipe.line"
    _description = "Engine Board Recipe Line"
    _order = "recipe_id, sequence, id"

    recipe_id = fields.Many2one(
        "corr.engine.board.recipe",
        string="Recipe",
        required=True,
        ondelete="cascade",
    )
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
        help="Optional default material for this layer.",
    )
    material_template_id = fields.Many2one(
        "product.template",
        string="Material / Paper Source",
        default=lambda self: self._default_material_template_id(),
        domain="[('id', 'in', allowed_material_template_ids)]",
        help="Raw-material template used to derive valid GSM/BF/Color values and resolve a product variant.",
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
        "corr_board_recipe_line_allowed_template_rel",
        "line_id",
        "template_id",
        compute="_compute_allowed_material_values",
    )
    allowed_gsm_value_ids = fields.Many2many(
        "product.attribute.value",
        "corr_board_recipe_line_allowed_gsm_rel",
        "line_id",
        "value_id",
        compute="_compute_allowed_material_values",
    )
    allowed_bf_value_ids = fields.Many2many(
        "product.attribute.value",
        "corr_board_recipe_line_allowed_bf_rel",
        "line_id",
        "value_id",
        compute="_compute_allowed_material_values",
    )
    allowed_color_value_ids = fields.Many2many(
        "product.attribute.value",
        "corr_board_recipe_line_allowed_color_rel",
        "line_id",
        "value_id",
        compute="_compute_allowed_material_values",
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
        configured = Template.browse(configured_ids).exists() if configured_ids else Template.search(
            [("active", "=", True), ("product_role", "=", "raw_material")]
            if "product_role" in Template._fields
            else [("active", "=", True)]
        )
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

    @api.onchange("material_template_id")
    def _onchange_material_template_id(self):
        for line in self:
            line.gsm_value_id = False
            line.bf_value_id = False
            line.color_value_id = False
            line.material_product_id = False
            line.resolved_product_id = False
            line.resolution_message = False

    @api.onchange("gsm_value_id", "bf_value_id", "color_value_id")
    def _onchange_material_attribute_values(self):
        for line in self:
            if line.gsm_value_id:
                line.gsm = line._numeric_from_attribute_value(line.gsm_value_id)
            if line.bf_value_id:
                line.bf = line._numeric_from_attribute_value(line.bf_value_id)
            if line.color_value_id:
                line.color = line.color_value_id.name or ""

    @api.depends("material_template_id", "gsm_value_id", "bf_value_id", "color_value_id")
    def _compute_material_resolution(self):
        for line in self:
            product, message = line._resolve_variant_without_deckle()
            line.resolved_product_id = product
            line.resolution_message = message

    def _resolve_variant_without_deckle(self):
        self.ensure_one()
        template = self.material_template_id
        if not template:
            return False, ""
        value_ids = [
            value.id
            for value in (self.gsm_value_id, self.bf_value_id, self.color_value_id)
            if value
        ]
        if not value_ids:
            return False, "Select GSM/BF/Color to resolve variant."
        candidates = template.product_variant_ids.filtered(lambda product: product.active)
        for value_id in value_ids:
            candidates = candidates.filtered(
                lambda product, vid=value_id: vid
                in product.product_template_attribute_value_ids.mapped("product_attribute_value_id").ids
            )
        if len(candidates) == 1:
            return candidates[0], "Matched. Deckle is checked during preview."
        if len(candidates) > 1:
            return False, "Multiple variants match before deckle. Preview will use exact deckle."
        return False, "No variant matches selected material attributes."

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._snapshot_vals_from_attribute_values(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        vals = self._snapshot_vals_from_attribute_values(vals)
        return super().write(vals)

