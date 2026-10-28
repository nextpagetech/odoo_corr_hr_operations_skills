from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.sql import column_exists


class ProductTemplateCorrExt(models.Model):
    _inherit = "product.template"

    # Deterministic key for staged SFG reuse across multiple FGs
    corr_sfg_key = fields.Char(index=True)
    # Optional stage label for staged SFG products (e.g., 2-ply, 4-ply, 5-ply, die-cut)
    corr_stage = fields.Char()
    stage_type_id = fields.Many2one(
        "corr.stage.type",
        string="Stage Type",
        help="Stage master record for generated corrugation SFG products.",
    )

    # Simple operational role classification used for inventory/UIs.
    # NOTE: purely informational; does not drive routes, moves or reservations.
    product_role = fields.Selection(
        [
            ("raw_material", "Raw Material"),
            ("sfg", "Semi-finished"),
            ("finished_good", "Finished Good"),
            ("other", "Other / Unclassified"),
        ],
        string="Product Role",
        default="other",
        index=True,
        help=(
            "Operational role of this product for planning and inventory UIs.\n"
            "- Raw Material: base inputs (paper, glue, ink, etc.).\n"
            "- Semi-finished: staged SFGs created by corrugation Auto-BoM.\n"
            "- Finished Good: saleable corrugated FG (boxes, sheets, etc.).\n"
            "- Other / Unclassified: default for everything else."
        ),
    )

    corr_sfg_mode = fields.Selection(
        [
            ("inherit", "Inherit Global SFG Mode"),
            ("none", "No SFG (Flat FG BoM)"),
            ("flat_single_wo", "Flat FG (Single Work Order)"),
            ("phantom", "SFG as Phantom"),
            ("staged", "Staged Stockable SFGs"),
        ],
        string="Corr SFG Mode",
        help=(
            "Optional override for this product's corrugation BoM structure. "
            "Inherit: use global SFG Mode (Settings).\n"
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
            "Optional override for corrugated SFG staging for this product.\n"
            "Default: keep current behavior (build 4-ply when applicable).\n"
            "2/4/Board: always build 2-ply, 4-ply and board SFGs when applicable.\n"
            "Board-only: skip explicit 4-ply SFG; board SFG consumes 2-ply SFGs directly."
        ),
        default="inherit",
    )
    corr_current_engine_v2_bom_id = fields.Many2one(
        "mrp.bom",
        string="Current Engine V2 BoM",
        copy=False,
        index=True,
        ondelete="set null",
        domain="[('product_tmpl_id', '=', id), ('corr_engine_v2_generated', '=', True), "
               "('corr_is_current_engine_v2_bom', '=', True), ('type', '=', 'normal'), "
               "('active', '=', True), ('corr_sfg_key', '=', False), ('corr_stage', '=', False)]",
    )

    def _setup_complete(self):
        res = super()._setup_complete()
        self._ensure_corr_columns()
        return res

    @api.constrains("corr_current_engine_v2_bom_id")
    def _check_corr_current_engine_v2_bom_id(self):
        for product in self:
            bom = product.corr_current_engine_v2_bom_id
            if not bom:
                continue
            if not bom._corr_is_valid_current_engine_v2_bom_for(product, product.env.company):
                raise ValidationError("Current Engine V2 BoM must be a valid active Engine V2 FG BoM for this product.")

    def _corr_stage_type_sync_vals(self, vals):
        vals = dict(vals or {})
        StageType = self.env["corr.stage.type"]
        stage_type = StageType.browse(vals.get("stage_type_id")) if vals.get("stage_type_id") else StageType.browse()
        if not stage_type and vals.get("corr_stage"):
            stage_type = StageType.find_from_code(vals.get("corr_stage"))
            if stage_type:
                vals["stage_type_id"] = stage_type.id
        if stage_type and not vals.get("corr_stage"):
            vals["corr_stage"] = stage_type.code
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._corr_stage_type_sync_vals(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if "stage_type_id" in vals or "corr_stage" in vals:
            vals = self._corr_stage_type_sync_vals(vals)
        return super().write(vals)

    def _ensure_corr_columns(self):
        cr = self.env.cr
        table = self._table
        definitions = {
            "corr_sfg_key": ("varchar", None),
            "corr_stage": ("varchar", None),
            "stage_type_id": ("integer", None),
            "corr_sfg_mode": ("varchar", "inherit"),
            "corr_corr_stage_mode": ("varchar", "inherit"),
            "product_role": ("varchar", "other"),
            "corr_current_engine_v2_bom_id": ("integer", None),
        }
        for column, (definition, default_value) in definitions.items():
            if column_exists(cr, table, column):
                continue
            cr.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
            if default_value is not None:
                cr.execute(
                    f'UPDATE "{table}" SET "{column}" = %s WHERE "{column}" IS NULL',
                    (default_value,),
                )


class ProductProductCorrExt(models.Model):
    _inherit = "product.product"

    # Mirror template role for easy domains on variants (stored for reporting/search).
    product_role = fields.Selection(
        related="product_tmpl_id.product_role",
        store=True,
        index=True,
        readonly=True,
    )
    stage_type_id = fields.Many2one(
        related="product_tmpl_id.stage_type_id",
        store=True,
        index=True,
        readonly=True,
    )
    corr_top_sheet_selectable = fields.Boolean(
        string="Selectable as Top Sheet",
        compute="_compute_corr_top_sheet_selectable",
        search="_search_corr_top_sheet_selectable",
    )

    @api.model
    def _corr_top_sheet_category_ids_from_config(self):
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("corr_engine_v2.top_sheet_category_ids", "")
            or ""
        )
        ids = []
        for item in raw.replace(";", ",").split(","):
            try:
                category_id = int((item or "").strip())
            except Exception:
                category_id = 0
            if category_id:
                ids.append(category_id)
        return list(dict.fromkeys(ids))

    @api.model
    def _corr_top_sheet_selectable_domain(self):
        domain = [("product_role", "=", "raw_material")]
        category_ids = self._corr_top_sheet_category_ids_from_config()
        if category_ids:
            domain.append(("product_tmpl_id.categ_id", "child_of", category_ids))
        return domain

    def _compute_corr_top_sheet_selectable(self):
        category_ids = self._corr_top_sheet_category_ids_from_config()
        allowed_category_ids = set()
        if category_ids:
            allowed_category_ids = set(
                self.env["product.category"].search([("id", "child_of", category_ids)]).ids
            )
        for product in self:
            is_raw_material = product.product_role == "raw_material"
            if not is_raw_material:
                product.corr_top_sheet_selectable = False
            elif not category_ids:
                product.corr_top_sheet_selectable = True
            else:
                product.corr_top_sheet_selectable = (
                    product.product_tmpl_id.categ_id.id in allowed_category_ids
                )

    def _search_corr_top_sheet_selectable(self, operator, value):
        positive = (operator in ("=", "==") and bool(value)) or (
            operator in ("!=", "<>") and not bool(value)
        )
        domain = self._corr_top_sheet_selectable_domain()
        if positive:
            return domain
        return [("id", "not in", self.search(domain).ids)]

    def _setup_complete(self):
        res = super()._setup_complete()
        cr = self.env.cr
        table = self._table
        if not column_exists(cr, table, "product_role"):
            cr.execute(f'ALTER TABLE "{table}" ADD COLUMN "product_role" varchar')
            cr.execute(
                f'''
                UPDATE "{table}" AS pp
                   SET "product_role" = pt.product_role
                  FROM product_template pt
                 WHERE pp.product_tmpl_id = pt.id
                '''
            )
            cr.execute(f'UPDATE "{table}" SET "product_role" = %s WHERE "product_role" IS NULL', ("other",))
        if not column_exists(cr, table, "stage_type_id"):
            cr.execute(f'ALTER TABLE "{table}" ADD COLUMN "stage_type_id" integer')
            cr.execute(
                f'''
                UPDATE "{table}" AS pp
                   SET "stage_type_id" = pt.stage_type_id
                  FROM product_template pt
                 WHERE pp.product_tmpl_id = pt.id
                '''
            )
        return res
