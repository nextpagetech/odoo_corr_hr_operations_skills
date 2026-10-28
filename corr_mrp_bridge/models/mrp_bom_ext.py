import logging

from odoo import api, fields, models


_logger = logging.getLogger(__name__)


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    corr_spec_id = fields.Many2one("corr.spec.template", string="Corr Spec", ondelete="set null")
    corr_signature = fields.Char()
    corr_engine_v2_signature = fields.Char(
        string="Engine V2 Signature",
        index=True,
        help="Stable Engine V2 preview signature used to reuse/update generated BoMs.",
    )
    corr_engine_v2_generated = fields.Boolean(
        string="Engine V2 Generated",
        default=False,
        copy=False,
        index=True,
    )
    corr_is_current_engine_v2_bom = fields.Boolean(
        string="Current Engine V2 BoM",
        default=False,
        copy=False,
        index=True,
    )
    corr_engine_v2_version = fields.Integer(
        string="Engine V2 Version",
        default=1,
        copy=False,
        index=True,
    )
    corr_superseded_by_bom_id = fields.Many2one(
        "mrp.bom",
        string="Superseded By",
        copy=False,
        index=True,
        ondelete="set null",
    )
    corr_result_snapshot = fields.Text()
    corr_engine_trace = fields.Text(
        string="Engine Trace",
        readonly=True,
        help="JSON trace captured from Engine V2 BoM creation.",
    )
    corr_uom_warning = fields.Text(string="Engine UoM Warnings JSON", readonly=True)
    corr_uom_warning_text = fields.Text(string="Engine UoM Warnings", readonly=True)
    lock_manual_lines = fields.Boolean(string="Lock Manual Lines", help="If enabled, lines without a corr key (manual edits) won't be removed by sync.")
    # Optional stage metadata for staged SFG BoMs (2-ply/4-ply/..../die-cut)
    corr_stage = fields.Char(string="Corr Stage")
    stage_type_id = fields.Many2one(
        "corr.stage.type",
        string="Stage Type",
        help="Stage master record for generated corrugation SFG BoMs.",
    )
    # Deterministic key used to reuse SFG products/BoMs across FGs
    corr_sfg_key = fields.Char(string="Corr SFG Key", index=True)
    corr_use_gross_qty = fields.Boolean(
        string="Corr Use Gross Qty",
        help="Internal bridge flag to keep corr-generated BoM lines aligned "
             "with net or gross engine quantities when the BoM quantity changes.",
    )

    @api.model
    def _corr_engine_v2_current_bom_selection_enabled(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "corr_mrp_bridge.engine_v2_current_bom_selection_enabled",
            default="0",
        ) in ("1", "true", "True")

    def _corr_is_engine_v2_fg_bom(self):
        self.ensure_one()
        return bool(
            self.corr_engine_v2_generated
            and self.corr_engine_v2_signature
            and self.product_tmpl_id
            and self.type == "normal"
            and self.active
            and not self.corr_sfg_key
            and not self.corr_stage
        )

    def _corr_company_matches(self, company):
        self.ensure_one()
        if not company:
            return True
        return not self.company_id or self.company_id == company

    def _corr_is_valid_current_engine_v2_bom_for(self, product, company=None):
        self.ensure_one()
        if not self._corr_is_engine_v2_fg_bom():
            return False
        product_tmpl = product.product_tmpl_id if getattr(product, "_name", "") == "product.product" else product
        if not product_tmpl or self.product_tmpl_id != product_tmpl:
            return False
        if self.product_id and getattr(product, "_name", "") == "product.product" and self.product_id != product:
            return False
        return self._corr_company_matches(company)

    @api.model
    def _corr_current_engine_v2_bom_for_product(self, product, company=None):
        if not self._corr_engine_v2_current_bom_selection_enabled() or not product:
            return self.browse()
        product_tmpl = product.product_tmpl_id if getattr(product, "_name", "") == "product.product" else product
        bom = product_tmpl.corr_current_engine_v2_bom_id
        if bom and bom._corr_is_valid_current_engine_v2_bom_for(product, company):
            return bom
        if bom:
            _logger.info(
                "Engine V2 current BoM ignored: product_tmpl_id=%s bom_id=%s company_id=%s reason=invalid_pointer",
                product_tmpl.id,
                bom.id,
                company.id if company else None,
            )
        domain = [
            ("product_tmpl_id", "=", product_tmpl.id),
            ("corr_engine_v2_generated", "=", True),
            ("corr_is_current_engine_v2_bom", "=", True),
            ("corr_engine_v2_signature", "!=", False),
            ("corr_sfg_key", "=", False),
            ("corr_stage", "=", False),
            ("type", "=", "normal"),
            ("active", "=", True),
        ]
        if company:
            domain.append(("company_id", "in", [False, company.id]))
        for candidate in self.sudo().search(domain, order="company_id desc, corr_engine_v2_version desc, id desc"):
            if candidate._corr_is_valid_current_engine_v2_bom_for(product, company):
                return candidate
        _logger.info(
            "Engine V2 current BoM fallback: product_tmpl_id=%s company_id=%s reason=no_valid_current",
            product_tmpl.id,
            company.id if company else None,
        )
        return self.browse()

    def _corr_mark_as_current_engine_v2_fg_bom(self):
        for bom in self:
            if not bom._corr_engine_v2_current_bom_selection_enabled():
                _logger.info(
                    "Engine V2 current BoM disabled: bom_id=%s product_tmpl_id=%s",
                    bom.id,
                    bom.product_tmpl_id.id if bom.product_tmpl_id else None,
                )
                continue
            if not bom._corr_is_engine_v2_fg_bom():
                continue
            with bom.env.cr.savepoint():
                product_tmpl = bom.product_tmpl_id.sudo()
                domain = [
                    ("id", "!=", bom.id),
                    ("product_tmpl_id", "=", product_tmpl.id),
                    ("corr_engine_v2_generated", "=", True),
                    ("corr_is_current_engine_v2_bom", "=", True),
                    ("corr_sfg_key", "=", False),
                    ("corr_stage", "=", False),
                    ("type", "=", "normal"),
                ]
                if bom.company_id:
                    domain.append(("company_id", "in", [False, bom.company_id.id]))
                else:
                    domain.append(("company_id", "=", False))
                previous = bom.sudo().search(domain, order="corr_engine_v2_version desc, id desc")
                previous_max_version = max([int(v or 0) for v in previous.mapped("corr_engine_v2_version")] + [0])
                vals = {
                    "corr_engine_v2_generated": True,
                    "corr_is_current_engine_v2_bom": True,
                    "corr_superseded_by_bom_id": False,
                }
                if previous:
                    vals["corr_engine_v2_version"] = max(int(bom.corr_engine_v2_version or 0), previous_max_version + 1)
                elif not bom.corr_engine_v2_version:
                    vals["corr_engine_v2_version"] = 1
                if vals:
                    bom.sudo().write(vals)
                if previous:
                    previous.sudo().write({
                        "corr_is_current_engine_v2_bom": False,
                        "corr_superseded_by_bom_id": bom.id,
                    })
                    _logger.info(
                        "Engine V2 BoM superseded: product_tmpl_id=%s old_bom_ids=%s new_bom_id=%s version=%s",
                        product_tmpl.id,
                        previous.ids,
                        bom.id,
                        bom.corr_engine_v2_version,
                    )
                product_tmpl.write({"corr_current_engine_v2_bom_id": bom.id})
                _logger.info(
                    "Engine V2 current BoM set: product_tmpl_id=%s bom_id=%s signature=%s version=%s company_id=%s",
                    product_tmpl.id,
                    bom.id,
                    (bom.corr_engine_v2_signature or "")[:12],
                    bom.corr_engine_v2_version,
                    bom.company_id.id if bom.company_id else None,
                )
        return True

    @api.model
    def _corr_initialize_current_engine_v2_boms(self):
        domain = [
            ("corr_engine_v2_signature", "!=", False),
            ("corr_sfg_key", "=", False),
            ("corr_stage", "=", False),
            ("type", "=", "normal"),
            ("active", "=", True),
        ]
        product_ids = self.sudo().search(domain).mapped("product_tmpl_id").ids
        for product_tmpl in self.env["product.template"].sudo().browse(product_ids):
            candidates = self.sudo().search(
                domain + [("product_tmpl_id", "=", product_tmpl.id)],
                order="corr_is_current_engine_v2_bom desc, corr_engine_v2_version desc, write_date desc, id desc",
            )
            if candidates:
                candidates.write({"corr_engine_v2_generated": True})
                candidates[0]._corr_mark_as_current_engine_v2_fg_bom()
        return True

    @staticmethod
    def _corr_round_qty(value, decimals=2):
        q = 10 ** int(decimals or 0)
        return ((float(value or 0.0) * q) + 0.5) // 1 / q

    def _corr_build_qty_map(self, result):
        self.ensure_one()
        metrics = result.get("metrics") or {}
        use_gross = bool(self.corr_use_gross_qty and result.get("rm_lines_gross"))
        rm_lines = (result.get("rm_lines_gross") if use_gross else result.get("rm_lines")) or []
        wiz = self.env["corr.bom.generate.wizard"].new({
            "spec_id": self.corr_spec_id.id,
            "target_product_tmpl_id": self.product_tmpl_id.id,
            "include_waste_in_bom": use_gross,
        })
        qty_map = {}
        for comp in rm_lines:
            mapped, _, _, _ = wiz._map_component(comp, metrics)
            if not mapped:
                continue
            role = (comp.get("role") or "").lower()
            try:
                layer_idx = int(comp.get("layer_no") or 0)
            except Exception:
                layer_idx = 0
            if not layer_idx:
                layer_idx = mapped.get("layer_idx") or 0
            key = wiz._component_key(role, layer_idx, mapped["product"].id)
            qty_map[key] = qty_map.get(key, 0.0) + float(comp.get("qty") or 0.0)
        return qty_map

    def _corr_sync_generated_line_qtys(self):
        """Recompute corr-generated BoM line totals from the engine.

        Scope this only to top-level corr bridge BoMs so standard Odoo BoMs,
        staged SFGs, and manual lines keep their existing behavior.
        """
        for bom in self:
            if not bom.corr_spec_id or bom.corr_stage or bom.type != "normal":
                continue
            try:
                target_qty = float(bom.product_qty or 0.0)
            except Exception:
                target_qty = 0.0
            if target_qty <= 0.0:
                continue
            result = bom.env["corr.formula.engine"].compute_requirements(bom.corr_spec_id, target_qty)
            qty_map = bom._corr_build_qty_map(result)
            for line in bom.bom_line_ids.filtered(lambda l: l.corr_component_key):
                if line.corr_component_key not in qty_map:
                    continue
                exact_total = float(qty_map[line.corr_component_key] or 0.0)
                rounded_total = bom._corr_round_qty(exact_total, 2)
                base_qty = exact_total / target_qty if target_qty else 0.0
                line.product_qty = rounded_total
                line.corr_base_qty = base_qty

    @api.onchange("product_qty")
    def _onchange_product_qty_corr_sync(self):
        self._corr_sync_generated_line_qtys()

    def write(self, vals):
        sync_after = (
            "product_qty" in vals
            and not self.env.context.get("skip_corr_bom_qty_sync")
        )
        res = super().write(vals)
        if sync_after:
            self._corr_sync_generated_line_qtys()
        return res

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


class MrpBomLine(models.Model):
    _inherit = "mrp.bom.line"

    # Preserve exact corrugation quantities on BoM component lines so the
    # BoM mirrors the wizard/engine output instead of truncating sub-0.01 kg
    # values via the standard Product Unit of Measure precision.
    product_qty = fields.Float(
        string="Quantity",
        default=1.0,
        digits=(16, 6),
        required=True,
    )
    corr_base_qty = fields.Float(
        string="Corr Base Qty",
        digits=(16, 6),
        help="Exact corrugation requirement per 1 finished unit for generated corr lines.",
    )
    corr_component_key = fields.Char(index=True)
    corr_engine_generated = fields.Boolean(
        string="Engine Generated",
        index=True,
        help="Internal flag used by Engine V2 to replace only BoM lines it generated.",
    )
    corr_role = fields.Selection([
        ("liner", "Liner"),
        ("medium", "Medium"),
        ("adhesive", "Adhesive"),
        ("top_sheet", "Top Sheet"),
        ("ink", "Ink"),
        ("lamination", "Lamination Film"),
        ("wire", "Wire"),
        ("tape", "Tape"),
    ])
    corr_role_display = fields.Char(
        string="Corr Role",
        compute="_compute_corr_role_display",
        store=True,
    )
    corr_layer_no = fields.Integer(string="Layer #")
    corr_flute_code = fields.Char()
    corr_gsm = fields.Float()
    corr_manual = fields.Boolean(string="Manual Line", help="Mark this line to keep it during sync even if it has no matching key.")

    @api.depends("corr_role", "product_uom_id", "product_uom_id.name", "product_uom_id.category_id.name")
    def _compute_corr_role_display(self):
        role_label_map = {
            "liner": "Liner",
            "medium": "Medium",
            "adhesive": "Adhesive",
            "top_sheet": "Top Sheet",
            "ink": "Ink",
            "lamination": "Lamination Film",
            "wire": "Wire",
            "tape": "Tape",
        }

        def _top_sheet_variant(uom):
            if not uom:
                return "Top Sheet"
            uom_name = (uom.name or "").strip().lower()
            cat_name = (uom.category_id.name or "").strip().lower()
            if any(token in cat_name for token in ("weight", "mass")) or uom_name in {"kg", "g", "lb", "lbs"}:
                return "Top Sheet (Roll)"
            if any(token in cat_name for token in ("unit", "units")) or any(token in uom_name for token in ("unit", "units", "sheet", "sheets", "pcs", "pc")):
                return "Top Sheet (Units)"
            return "Top Sheet"

        for line in self:
            role_code = (line.corr_role or "").lower()
            if role_code == "top_sheet":
                line.corr_role_display = _top_sheet_variant(line.product_uom_id)
            else:
                line.corr_role_display = role_label_map.get(role_code, line.corr_role or "")

    @staticmethod
    def _corr_infer_role_from_key(vals):
        vals = dict(vals or {})
        key = (vals.get("corr_component_key") or "").strip()
        if key.startswith("top_sheet:") and not vals.get("corr_role"):
            vals["corr_role"] = "top_sheet"
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._corr_infer_role_from_key(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        vals = self._corr_infer_role_from_key(vals)
        return super().write(vals)
