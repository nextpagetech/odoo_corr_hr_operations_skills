from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class CorrMaterialProductMap(models.Model):
    _name = "corr.material.product.map"
    _description = "Corrugation Material → Product Mapping"
    _order = "kind, flute_code, gsm_min, id"

    kind = fields.Selection(
        [
            ("liner", "Liner"),
            ("medium", "Medium"),
            ("carton_board", "Carton Board"),
            ("adhesive", "Adhesive"),
            ("ink", "Ink"),
            ("lamination", "Lamination Film"),
            ("wire", "Wire"),
            ("tape", "Tape"),
        ],
        required=True,
    )
    flute_code = fields.Char(help="For Medium mappings; leave empty for others")
    gsm_min = fields.Float(default=0.0)
    gsm_max = fields.Float(default=0.0)
    engine_code = fields.Char(help="Optional engine code/name for consumables")
    product_id = fields.Many2one("product.product", required=True, ondelete="restrict")
    uom_id = fields.Many2one("uom.uom", string="Component UoM", help="UoM for BoM line; defaults to product's UoM")
    active = fields.Boolean(default=True)

    @api.constrains("gsm_min", "gsm_max")
    def _check_gsm_range(self):
        for rec in self:
            if rec.gsm_min and rec.gsm_max and rec.gsm_max < rec.gsm_min:
                raise ValidationError(_("gsm_max must be >= gsm_min"))

    @api.constrains("kind", "flute_code", "gsm_min", "gsm_max", "active")
    def _check_overlap(self):
        for rec in self:
            if not rec.active:
                continue
            if rec.kind not in ("liner", "medium", "carton_board"):
                continue
            domain = [
                ("id", "!=", rec.id),
                ("active", "=", True),
                ("kind", "=", rec.kind),
            ]
            if rec.kind == "medium":
                domain.append(("flute_code", "=", rec.flute_code or False))
            others = self.search(domain)
            gmin = rec.gsm_min or 0.0
            gmax = rec.gsm_max or 0.0
            for o in others:
                omin = o.gsm_min or 0.0
                omax = o.gsm_max or 0.0
                # If no range defined on either, skip strict check
                if not (gmin or gmax or omin or omax):
                    continue
                # Overlap if intervals intersect
                if (omin <= (gmax or omin) and (o. gsm_max or gmax) >= gmin):
                    raise ValidationError(_("Overlapping GSM ranges for %s (flute %s).") % (rec.kind, rec.flute_code or "-"))

    def match_component(self, role: str, gsm: float | None = None, flute: str | None = None, engine_code: str | None = None):
        self.ensure_one()
        # not used; matching done via search helper
        return False

    @api.model
    def find_match(self, role: str, gsm: float | None = None, flute: str | None = None, engine_code: str | None = None):
        domain = [("kind", "=", role), ("active", "=", True)]
        if role == "medium" and flute:
            domain.append(("flute_code", "=", flute))
        candidates = self.search(domain)
        if role in ("liner", "medium", "carton_board") and gsm is not None:
            # filter by gsm band if provided
            def ok(rec):
                if rec.gsm_min and gsm < rec.gsm_min:
                    return False
                if rec.gsm_max and gsm > rec.gsm_max:
                    return False
                if role == "medium" and rec.flute_code and flute and rec.flute_code != flute:
                    return False
                return True
            candidates = candidates.filtered(ok)
            # Prefer specific GSM ranges over catch‑all (0/0), then narrowest range, then closest center
            if candidates:
                def spec_key(rec):
                    gmin = float(rec.gsm_min or 0.0)
                    gmax = float(rec.gsm_max or 0.0)
                    has_range = (gmin or gmax) and (gmax >= gmin)
                    # priority: 0 for ranged, 1 for catch‑all
                    priority = 0 if has_range else 1
                    # width only meaningful when both bounds present; larger when missing
                    width = (gmax - gmin) if (rec.gsm_min and rec.gsm_max) else 1e9
                    # distance from center for tie‑break
                    center = (gmin + gmax) / 2.0 if (rec.gsm_min and rec.gsm_max) else 1e9
                    mid_dist = abs(center - float(gsm))
                    return (priority, width, mid_dist, rec.id)

                best = sorted(list(candidates), key=spec_key)[0]
                return self.browse(best.id)
        elif role in ("adhesive", "ink", "lamination", "wire", "tape") and engine_code:
            candidates = candidates.filtered(lambda r: (r.engine_code or "").lower() == engine_code.lower()) or candidates
        return candidates[:1]

    # Advanced attribute-based picker for planning wizard
    @api.model
    def auto_find_variant_v2(
        self,
        role: str,
        *,  # keyword-only
        flute: str | None = None,
        gsm: float | None = None,
        bf: float | None = None,
        color: str | None = None,
        deckle_total_cm: float | None = None,
        deckle_tolerance_cm: float = 0.5,
        strict_color_override: bool | None = None,
    ):
        """Resolve a product variant by product attributes.

        - role liner: GSM±tol, optional BF±tol, Color exact (case-insensitive), Deckle(cm)≈target±tolerance.
        - role medium: Flute exact + GSM±tol, optional BF±tol, Color exact, Deckle(cm)≈target±tolerance.

        Returns dict {product, uom} or None.
        """
        if role not in ("liner", "medium", "carton_board"):
            return None
        Product = self.env["product.product"].sudo()
        Category = self.env["product.category"].sudo()

        # Tolerances
        icp = self.env["ir.config_parameter"].sudo()
        tol_pct_param = icp.get_param("corr_mrp_bridge.auto_map_tolerance_pct")
        try:
            tol_pct = float(tol_pct_param) if tol_pct_param is not None else None
        except Exception:
            tol_pct = None
        if tol_pct is None:
            plant = self.env["corr.plant.param"].sudo().search([], limit=1)
            tol_pct = float((plant and plant.gsm_tolerance_pct) or 5.0)
        tol_abs_gsm = abs(float(gsm)) * (tol_pct / 100.0) if gsm not in (None, 0) else None
        # BF handling: optionally strict (require BF) or tolerant when missing.
        strict_bf = False
        try:
            # Reuse helper below to parse bool-like params
            def _as_bool(val):
                try:
                    return str(val).strip().lower() in ("1", "true", "yes", "y") if val is not None else False
                except Exception:
                    return False
            strict_bf = _as_bool(self.env["ir.config_parameter"].sudo().get_param("planning.strict_bf_match"))
        except Exception:
            strict_bf = False
        tol_abs_bf = None
        if bf is not None:
            try:
                bff = float(bf)
                if bff > 0:
                    tol_abs_bf = abs(bff) * (tol_pct / 100.0)
            except Exception:
                tol_abs_bf = None
        if strict_bf and tol_abs_bf is None:
            # When strict BF is requested and no usable BF provided, abort
            return None

        # Default color if missing
        if not color:
            color = icp.get_param("planning.default_color_name") or "Natural Shade"

        # Strict color matching toggle and aliases for normalization
        def _as_bool(val):
            try:
                return str(val).strip().lower() in ("1", "true", "yes", "y") if val is not None else False
            except Exception:
                return False

        strict_color = _as_bool(icp.get_param("planning.strict_color_match"))
        if strict_color_override is not None:
            strict_color = bool(strict_color_override)
        aliases_raw = icp.get_param("planning.color_aliases_json")
        try:
            import json as _json
            color_aliases = _json.loads(aliases_raw) if aliases_raw else {}
            if not isinstance(color_aliases, dict):
                color_aliases = {}
        except Exception:
            color_aliases = {}

        def _norm_color(val: str | None) -> str | None:
            if val is None:
                return None
            try:
                import re
                s = str(val).strip().lower()
                # remove parenthetical suffixes like (ns)
                s = re.sub(r"\(.*?\)", "", s)
                # collapse whitespace
                s = " ".join(s.split())
                # apply alias mapping
                mapped = color_aliases.get(s)
                if mapped:
                    s = str(mapped).strip().lower()
                return s
            except Exception:
                return str(val).strip().lower()

        # Base domain; avoid over-filtering here. We'll refine in Python to allow
        # products missing certain attributes (e.g., Color) and just de-prioritize them.
        domain = [("active", "=", True)]

        # Optional category scoping to speed up and disambiguate (mapping-less mode)
        # - For paper rolls (liner/medium): planning.paper_roll_category_name
        # - For top_sheet (not used here)
        icp = self.env["ir.config_parameter"].sudo()
        if role in ("liner", "medium"):
            cat_name = icp.get_param("planning.paper_roll_category_name")
            if cat_name:
                cat = Category.search([("name", "=ilike", cat_name.strip())], limit=1)
                if cat:
                    domain.append(("product_tmpl_id.categ_id", "child_of", cat.id))
        # Do not DB-filter by Flute; many catalogs don't carry a Flute attribute on paper variants.
        # We'll prefer Flute matches in Python when present, but allow missing.
        # Do NOT filter by Color at the domain level; many catalogs miss the attribute
        # or use a different naming. We'll prefer exact color in ranking below.
        # Do NOT add a DB-level deckle attribute filter; synonym names vary.
        # We'll evaluate deckle entirely in Python with tolerant parsing.

        candidates = Product.search(domain)

        def get_attr(prod, name):
            for v in prod.product_template_attribute_value_ids:
                if v.attribute_id and (v.attribute_id.name or "").lower() == name.lower():
                    return v.name
            return None

        def get_attr_from_list(prod, names: list[str]):
            for nm in names:
                val = get_attr(prod, nm)
                if val is not None:
                    return val
            return None

        def to_float(val):
            """Best-effort numeric parser.

            Accepts numbers, strings like "90", " 90.0 ", "90 cm", "Deckle 90", etc.
            Returns float or None when no digits found.
            """
            if val is None:
                return None
            try:
                # Fast path for clean numerics
                return float(val)
            except Exception:
                try:
                    import re
                    m = re.search(r"[-+]?\d*\.?\d+", str(val))
                    if m:
                        return float(m.group(0))
                except Exception:
                    pass
            return None

        best = None
        for p in candidates:
            # Skip mediums when looking for liners
            if role == "liner" and get_attr(p, "Flute"):
                continue
            # Flute preference for medium: if product has a Flute attribute and a flute was requested,
            # require match; otherwise accept products without Flute.
            if role == "medium" and flute:
                fv = get_attr(p, "Flute")
                if fv and fv.lower() != flute.lower():
                    continue

            # Color handling (strict or soft with normalization and aliases)
            colv_raw = get_attr(p, "Color")
            colv_norm = _norm_color(colv_raw)
            want_color = _norm_color(color)
            color_exact = False
            color_ok = True
            if want_color:
                if strict_color:
                    # require exact (normalized) color
                    color_ok = (colv_norm is not None and colv_norm == want_color)
                else:
                    # prefer exact, allow missing/mismatch (penalize in ranking)
                    color_ok = True
                color_exact = (colv_norm is not None and colv_norm == want_color)
            if not color_ok:
                continue

            # GSM within tolerance
            if gsm is not None and tol_abs_gsm is not None:
                gv = to_float(get_attr(p, "GSM"))
                if gv is None or abs(gv - float(gsm)) > tol_abs_gsm:
                    continue
                diff_gsm = abs(gv - float(gsm))
            else:
                diff_gsm = 0.0

            # BF matching (optional): only enforce when a usable BF was provided
            if tol_abs_bf is not None:
                bv = to_float(get_attr(p, "BF"))
                if bv is None:
                    # skip candidates that don't carry BF when BF matching is requested
                    continue
                try:
                    diff_bf = abs(bv - float(bf))
                except Exception:
                    diff_bf = 1e9
                if diff_bf > tol_abs_bf:
                    continue
            else:
                diff_bf = 0.0

            # Deckle within absolute tolerance
            if deckle_total_cm is not None:
                # Accept common synonyms for deckle attribute
                dv_raw = get_attr_from_list(p, [
                    "Deckle (cm)",
                    "Deckle",
                    "Deckle(cm)",
                    "Reel Width (cm)",
                    "Reel Width",
                ])
                dv = to_float(dv_raw)
                try:
                    deckle_tol = float(deckle_tolerance_cm) if deckle_tolerance_cm is not None else 0.5
                except Exception:
                    deckle_tol = 0.5
                if dv is None or abs(dv - float(deckle_total_cm)) > deckle_tol:
                    continue
                diff_deckle = abs(dv - float(deckle_total_cm))
            else:
                diff_deckle = 0.0

            # Ranking: prefer exact color (penalty 0), then smaller deckle diff,
            # then GSM diff, then BF diff
            rank = (
                0 if color_exact else 1,
                diff_deckle,
                diff_gsm,
                diff_bf,
                p.id,
            )
            if best is None or rank < best[0]:
                best = (rank, p)

        if best and best[1]:
            prod = best[1]
            return {"product": prod, "uom": prod.uom_id}
        return None

    @api.model
    def resolve_engine_paper_variant_v1(
        self,
        role: str,
        *,
        gsm: float | None = None,
        bf: float | None = None,
        color: str | None = None,
        flute: str | None = None,
        deckle_cm: float | None = None,
        material_template_id: int | None = None,
        exact_resolution_only: bool = False,
    ) -> dict:
        """Resolve a paper variant for Engine V2 preview.

        Primary matching inputs:
        - role: \"liner\", \"medium\" or \"carton_board\"
        - gsm: target grammage
        - flute: required flute code for medium paper
        - deckle_cm: required total deckle in cm (variant deckle must be >=)

        Secondary ranking inputs:
        - bf: bursting factor (optional)
        - color: paper color

        Returns a plain dict with keys:
          - status: \"exact\" | \"usable\" | \"missing\" | \"ambiguous\"
          - product: product.product record or False
          - uom: uom.uom record or False
          - candidate_ids: list of product IDs considered usable
        """
        status_missing = {
            "status": "missing",
            "product": False,
            "uom": False,
            "candidate_ids": [],
            "debug": {
                "initial_product_search_count": 0,
                "after_category_filter": 0,
                "after_color": 0,
                "after_flute": 0,
                "after_gsm": 0,
                "after_bf": 0,
                "after_deckle": 0,
                "final_candidate_ids": [],
                "first_failed_stage": "",
                "candidates": [],
            },
        }

        role_norm = (role or "").strip().lower()
        if role_norm not in ("liner", "medium", "carton_board"):
            return status_missing
        flute_norm = (flute or "").strip().lower()

        Product = self.env["product.product"].sudo()
        Category = self.env["product.category"].sudo()
        icp = self.env["ir.config_parameter"].sudo()

        # Tolerances for GSM (percent of target) and BF (reuses same percent).
        tol_pct_param = icp.get_param("corr_mrp_bridge.auto_map_tolerance_pct")
        try:
            tol_pct = float(tol_pct_param) if tol_pct_param is not None else None
        except Exception:
            tol_pct = None
        if tol_pct is None:
            plant = self.env["corr.plant.param"].sudo().search([], limit=1)
            tol_pct = float((plant and plant.gsm_tolerance_pct) or 5.0)

        try:
            gsm_val = float(gsm or 0.0)
        except Exception:
            gsm_val = 0.0
        tol_abs_gsm = abs(gsm_val) * (tol_pct / 100.0) if gsm_val > 0.0 else None

        if role_norm == "carton_board":
            mapped = self.find_match(role_norm, gsm=gsm_val if gsm_val > 0.0 else None)
            mapped_product = mapped[:1].product_id if mapped else False
            if mapped_product:
                return {
                    "status": "exact",
                    "product": mapped_product,
                    "uom": mapped[:1].uom_id or mapped_product.uom_id,
                    "candidate_ids": [mapped_product.id],
                    "debug": {
                        "initial_product_search_count": 0,
                        "after_category_filter": 1,
                        "after_color": 1,
                        "after_flute": 1,
                        "after_gsm": 1,
                        "after_bf": 1,
                        "after_deckle": 1,
                        "material_template_id": None,
                        "final_candidate_ids": [mapped_product.id],
                        "first_failed_stage": "",
                        "candidates": [],
                        "mapped_by": "corr.material.product.map",
                        "mapping_kind": "carton_board",
                        "mapping_id": mapped[:1].id,
                    },
                }

        strict_bf = False
        try:
            def _as_bool(val):
                try:
                    return str(val).strip().lower() in ("1", "true", "yes", "y") if val is not None else False
                except Exception:
                    return False

            strict_bf = _as_bool(icp.get_param("planning.strict_bf_match"))
        except Exception:
            strict_bf = False

        tol_abs_bf = None
        if bf is not None:
            try:
                bf_f = float(bf)
                if bf_f > 0.0:
                    tol_abs_bf = abs(bf_f) * (tol_pct / 100.0)
            except Exception:
                tol_abs_bf = None
        if strict_bf and tol_abs_bf is None:
            # BF requested but unusable -> no match.
            return status_missing

        # Color normalisation and strict/soft handling, with aliases.
        try:
            def _as_bool(val):
                try:
                    return str(val).strip().lower() in ("1", "true", "yes", "y") if val is not None else False
                except Exception:
                    return False

            strict_color = _as_bool(icp.get_param("planning.strict_color_match"))
        except Exception:
            strict_color = False

        aliases_raw = icp.get_param("planning.color_aliases_json")
        try:
            import json as _json
            color_aliases = _json.loads(aliases_raw) if aliases_raw else {}
            if not isinstance(color_aliases, dict):
                color_aliases = {}
        except Exception:
            color_aliases = {}

        def _norm_color(val: str | None) -> str | None:
            if val is None:
                return None
            try:
                import re
                s = str(val).strip().lower()
                # remove parenthetical suffixes like (ns)
                s = re.sub(r"\(.*?\)", "", s)
                s = " ".join(s.split())
                mapped = color_aliases.get(s)
                if mapped:
                    s = str(mapped).strip().lower()
                return s
            except Exception:
                return str(val).strip().lower()

        default_color = icp.get_param("planning.default_color_name") or "Natural Shade"
        if not color:
            color = default_color
        want_color_norm = _norm_color(color)

        # Deckle requirement (cm). When provided, candidate deckle must be >= required.
        try:
            deckle_required = float(deckle_cm) if deckle_cm is not None else None
        except Exception:
            deckle_required = None

        try:
            deckle_tol_param = icp.get_param("planning.deckle_tolerance_cm")
            deckle_exact_tol = float(deckle_tol_param) if deckle_tol_param is not None else 0.5
        except Exception:
            deckle_exact_tol = 0.5

        # Base domain; refined in Python using attributes.
        base_domain = [("active", "=", True)]
        # Initial search before category scoping for debug purposes only.
        initial_candidates = Product.search(base_domain)

        domain = list(base_domain)
        try:
            material_template_id = int(material_template_id or 0)
        except Exception:
            material_template_id = 0
        if material_template_id > 0:
            domain.append(("product_tmpl_id", "=", material_template_id))
        if role_norm in ("liner", "medium"):
            cat_name = icp.get_param("planning.paper_roll_category_name")
            if cat_name:
                cat = Category.search([("name", "=ilike", cat_name.strip())], limit=1)
                if cat:
                    domain.append(("product_tmpl_id.categ_id", "child_of", cat.id))
        elif role_norm == "carton_board":
            mapped = self.find_match(role_norm, gsm=gsm_val if gsm_val > 0.0 else None)
            mapped_product_ids = [rec.product_id.id for rec in mapped if rec.product_id]
            if mapped_product_ids:
                domain.append(("id", "in", mapped_product_ids))

        candidates = Product.search(domain)

        debug = {
            "initial_product_search_count": len(initial_candidates),
            "after_category_filter": len(candidates),
            "material_template_id": material_template_id or None,
            "after_color": 0,
            "after_flute": 0,
            "after_gsm": 0,
            "after_bf": 0,
            "after_deckle": 0,
            "final_candidate_ids": [],
            "first_failed_stage": "",
            "candidates": [],
        }

        def get_attr(prod, name):
            for v in prod.product_template_attribute_value_ids:
                if v.attribute_id and (v.attribute_id.name or "").lower() == name.lower():
                    return v.name
            return None

        def get_attr_from_list(prod, names):
            for nm in names:
                val = get_attr(prod, nm)
                if val is not None:
                    return val
            return None

        def to_float(val):
            if val is None:
                return None
            try:
                return float(val)
            except Exception:
                try:
                    import re
                    m = re.search(r"[-+]?\\d*\\.?\\d+", str(val))
                    if m:
                        return float(m.group(0))
                except Exception:
                    pass
            return None

        medium_flute_filter_active = False
        if role_norm == "medium" and flute_norm:
            for prod in candidates:
                prod_flute_norm = (get_attr(prod, "Flute") or "").strip().lower()
                if prod_flute_norm == flute_norm:
                    medium_flute_filter_active = True
                    break

        usable_infos = []
        for prod in candidates:
            if role_norm == "liner" and get_attr(prod, "Flute"):
                # Avoid mediums when looking for liners.
                continue

            cand_debug = {
                "product_id": prod.id,
                "default_code": getattr(prod, "default_code", "") or "",
                "name": prod.display_name or "",
                "gsm": None,
                "bf": None,
                "color_raw": None,
                "color_norm": None,
                "required_color_norm": want_color_norm,
                "flute_raw": None,
                "flute_norm": None,
                "required_flute_norm": flute_norm or None,
                "flute_filter_active": medium_flute_filter_active,
                "deckle_cm": None,
                "first_failed_step": None,
                "rejection_reason": "",
                "final_status": "pending",
                "filters": {
                    "color_ok": None,
                    "flute_ok": None,
                    "gsm_ok": None,
                    "bf_ok": None,
                    "deckle_ok": None,
                    "final_used": False,
                },
            }

            # Color handling.
            col_raw = get_attr(prod, "Color")
            col_norm = _norm_color(col_raw)
            cand_debug["color_raw"] = col_raw
            cand_debug["color_norm"] = col_norm
            color_exact = False
            color_ok = True
            if want_color_norm:
                if strict_color:
                    color_ok = (col_norm is not None and col_norm == want_color_norm)
                else:
                    color_ok = True
                color_exact = (col_norm is not None and col_norm == want_color_norm)
            cand_debug["filters"]["color_ok"] = bool(color_ok)
            if not color_ok:
                cand_debug["first_failed_step"] = "color"
                cand_debug["rejection_reason"] = "strict color mismatch" if strict_color else "color preference mismatch"
                cand_debug["final_status"] = "rejected"
                debug["candidates"].append(cand_debug)
                continue

            # Medium papers are flute-specific. Liners deliberately ignore
            # flute and already exclude products that carry a Flute attribute.
            if role_norm == "medium":
                flute_raw = get_attr(prod, "Flute")
                prod_flute_norm = (flute_raw or "").strip().lower()
                cand_debug["flute_raw"] = flute_raw
                cand_debug["flute_norm"] = prod_flute_norm or None
                if medium_flute_filter_active and prod_flute_norm != flute_norm:
                    cand_debug["filters"]["flute_ok"] = False
                    cand_debug["first_failed_step"] = "flute"
                    cand_debug["rejection_reason"] = "medium flute mismatch"
                    cand_debug["final_status"] = "rejected"
                    debug["candidates"].append(cand_debug)
                    continue
            cand_debug["filters"]["flute_ok"] = True
            debug["after_flute"] += 1

            # GSM filter.
            if gsm_val > 0.0 and tol_abs_gsm is not None:
                gv = to_float(get_attr(prod, "GSM"))
                cand_debug["gsm"] = gv
                if gv is None or abs(gv - gsm_val) > tol_abs_gsm:
                    cand_debug["filters"]["gsm_ok"] = False
                    cand_debug["first_failed_step"] = "gsm"
                    cand_debug["rejection_reason"] = "GSM outside tolerance"
                    cand_debug["final_status"] = "rejected"
                    debug["candidates"].append(cand_debug)
                    continue
                diff_gsm = abs(gv - gsm_val)
            else:
                gv = None
                diff_gsm = 0.0
            cand_debug["filters"]["gsm_ok"] = True

            # BF filter (only when strict and a usable BF tolerance exists).
            bf_requested = tol_abs_bf is not None
            if bf_requested:
                bv_for_exact = to_float(get_attr(prod, "BF"))
                cand_debug["bf"] = bv_for_exact
            else:
                bv_for_exact = None

            if tol_abs_bf is not None and strict_bf:
                bv = bv_for_exact
                cand_debug["bf"] = bv
                if bv is None:
                    cand_debug["filters"]["bf_ok"] = False
                    cand_debug["first_failed_step"] = "bf"
                    cand_debug["rejection_reason"] = "BF missing on product"
                    cand_debug["final_status"] = "rejected"
                    debug["candidates"].append(cand_debug)
                    continue
                try:
                    diff_bf = abs(bv - float(bf))
                except Exception:
                    diff_bf = 1e9
                if diff_bf > tol_abs_bf:
                    cand_debug["filters"]["bf_ok"] = False
                    cand_debug["first_failed_step"] = "bf"
                    cand_debug["rejection_reason"] = "BF outside tolerance"
                    cand_debug["final_status"] = "rejected"
                    debug["candidates"].append(cand_debug)
                    continue
                cand_debug["filters"]["bf_ok"] = True
            else:
                try:
                    diff_bf = abs(float(bv_for_exact) - float(bf)) if bf_requested and bv_for_exact is not None else (1e9 if bf_requested else 0.0)
                except Exception:
                    diff_bf = 1e9 if bf_requested else 0.0
                cand_debug["filters"]["bf_ok"] = None

            # Preview/BoM can run in exact-only mode for deckle-sensitive papers.
            # Wider compatible substitutions belong to a separate controlled
            # manufacturing/planning deviation workflow, not automatic engine
            # resolution.
            deckle_val = None
            deckle_overage = 0.0
            if deckle_required is not None and deckle_required > 0.0:
                dv_raw = get_attr_from_list(
                    prod,
                    [
                        "Deckle (cm)",
                        "Deckle",
                        "Deckle(cm)",
                        "Reel Width (cm)",
                        "Reel Width",
                    ],
                )
                deckle_val = to_float(dv_raw)
                cand_debug["deckle_cm"] = deckle_val
                if deckle_val is None or deckle_val + 1e-6 < deckle_required:
                    # Variant is narrower than required deckle.
                    cand_debug["filters"]["deckle_ok"] = False
                    cand_debug["first_failed_step"] = "deckle"
                    cand_debug["rejection_reason"] = "deckle below required minimum"
                    cand_debug["final_status"] = "rejected"
                    debug["candidates"].append(cand_debug)
                    continue
                deckle_overage = deckle_val - deckle_required
                if exact_resolution_only and abs(deckle_overage) > float(deckle_exact_tol or 0.5):
                    cand_debug["filters"]["deckle_ok"] = False
                    cand_debug["first_failed_step"] = "deckle"
                    cand_debug["rejection_reason"] = "exact deckle variant required"
                    cand_debug["final_status"] = "rejected"
                    debug["candidates"].append(cand_debug)
                    continue
                cand_debug["filters"]["deckle_ok"] = True
            else:
                cand_debug["filters"]["deckle_ok"] = None

            rank = (
                0 if color_exact else 1,
                deckle_overage,
                diff_gsm,
                diff_bf,
                prod.id,
            )
            usable_infos.append(
                {
                    "product": prod,
                    "uom": prod.uom_id,
                    "rank": rank,
                    "color_exact": color_exact,
                    "deckle_val": deckle_val,
                    "deckle_overage": deckle_overage,
                    "diff_gsm": diff_gsm,
                    "diff_bf": diff_bf,
                    "debug": cand_debug,
                }
            )
            cand_debug["final_status"] = "accepted"
            debug["candidates"].append(cand_debug)

        if not usable_infos:
            # Populate aggregate counts for easier debugging.
            debug["after_color"] = sum(1 for c in debug["candidates"] if c["filters"]["color_ok"])
            debug["after_gsm"] = sum(
                1
                for c in debug["candidates"]
                if c["filters"]["color_ok"] and c["filters"]["gsm_ok"]
            )
            debug["after_bf"] = sum(
                1
                for c in debug["candidates"]
                if c["filters"]["color_ok"] and c["filters"]["gsm_ok"] and c["filters"]["bf_ok"] is not False
            )
            debug["after_deckle"] = sum(
                1
                for c in debug["candidates"]
                if c["filters"]["color_ok"]
                and c["filters"]["gsm_ok"]
                and c["filters"]["bf_ok"] is not False
                and c["filters"]["deckle_ok"] is not False
            )
            debug["final_candidate_ids"] = []
            # First stage where everything is dropped.
            if debug["after_color"] == 0:
                debug["first_failed_stage"] = "color"
            elif debug["after_gsm"] == 0:
                debug["first_failed_stage"] = "gsm"
            elif debug["after_bf"] == 0:
                debug["first_failed_stage"] = "bf"
            elif debug["after_deckle"] == 0:
                debug["first_failed_stage"] = "deckle"
            missing = dict(status_missing)
            missing["debug"] = debug
            return missing

        # Sort by rank and detect ambiguity (ties on rank tuple).
        usable_infos.sort(key=lambda info: info["rank"])
        best = usable_infos[0]
        best_rank = best["rank"]
        ambiguous = any(info is not best and info["rank"] == best_rank for info in usable_infos)

        candidate_ids = [info["product"].id for info in usable_infos]

        # Mark which candidate ended up in the usable list.
        for info in usable_infos:
            info["debug"]["filters"]["final_used"] = True

        # Populate aggregate counts now that usable_infos is known.
        debug["after_color"] = sum(1 for c in debug["candidates"] if c["filters"]["color_ok"])
        debug["after_gsm"] = sum(
            1
            for c in debug["candidates"]
            if c["filters"]["color_ok"] and c["filters"]["gsm_ok"]
        )
        debug["after_bf"] = sum(
            1
            for c in debug["candidates"]
            if c["filters"]["color_ok"] and c["filters"]["gsm_ok"] and c["filters"]["bf_ok"] is not False
        )
        debug["after_deckle"] = sum(
            1
            for c in debug["candidates"]
            if c["filters"]["color_ok"]
            and c["filters"]["gsm_ok"]
            and c["filters"]["bf_ok"] is not False
            and c["filters"]["deckle_ok"] is not False
        )
        debug["final_candidate_ids"] = candidate_ids
        if debug["after_deckle"] == 0:
            # All dropped at or before deckle even though usable_infos is non-empty
            debug["first_failed_stage"] = ""

        if ambiguous:
            if exact_resolution_only:
                debug["first_failed_stage"] = "exact_resolution"
                return {
                    "status": "missing",
                    "product": False,
                    "uom": False,
                    "candidate_ids": candidate_ids,
                    "debug": debug,
                }
            return {
                "status": "ambiguous",
                "product": False,
                "uom": False,
                "candidate_ids": candidate_ids,
                "debug": debug,
            }

        # Exact vs usable: deckle close to required (within deckle_exact_tol)
        # and GSM / color match tightly.
        deckle_exact = False
        if deckle_required is not None and best["deckle_val"] is not None:
            deckle_exact = abs(best["deckle_val"] - deckle_required) <= float(deckle_exact_tol or 0.5)
        gsm_exact = best["diff_gsm"] <= 1e-6
        color_exact = best["color_exact"]

        try:
            bf_requested = bf is not None and float(bf) > 0.0
        except Exception:
            bf_requested = False
        bf_exact = (not bf_requested) or best["diff_bf"] <= 1e-6

        status = "exact" if deckle_exact and gsm_exact and bf_exact and color_exact else "usable"
        if exact_resolution_only and status != "exact":
            debug["first_failed_stage"] = "exact_resolution"
            return {
                "status": "missing",
                "product": False,
                "uom": False,
                "candidate_ids": candidate_ids,
                "debug": debug,
            }

        return {
            "status": status,
            "product": best["product"],
            "uom": best["uom"],
            "candidate_ids": candidate_ids,
            "debug": debug,
        }

    @api.model
    def auto_find_variant(self, role: str, gsm: float | None = None, flute: str | None = None):
        """Auto-resolve product.variant by product attributes.

        - role liner: match GSM within tolerance
        - role medium: match Flute exact + GSM within tolerance
        Returns first closest match or None.
        """
        if role not in ("liner", "medium", "carton_board"):
            return None
        if gsm is None:
            return None

        # tolerance: system param -> plant -> 5
        tol_pct_param = icp.get_param("corr_mrp_bridge.auto_map_tolerance_pct")
        try:
            tol_pct = float(tol_pct_param) if tol_pct_param is not None else None
        except Exception:
            tol_pct = None
        if tol_pct is None:
            plant = self.env["corr.plant.param"].sudo().search([], limit=1)
            tol_pct = float(plant.gsm_tolerance_pct or 5.0)
        tol_abs = abs(float(gsm)) * (tol_pct / 100.0)

        Product = self.env["product.product"].sudo()
        domain = [("active", "=", True)]
        # Filter by Flute when medium
        if role == "medium" and flute:
            domain += [
                ("product_template_attribute_value_ids.attribute_id.name", "=", "Flute"),
                ("product_template_attribute_value_ids.name", "=", flute),
            ]
        candidates = Product.search(domain)

        def get_attr(prod, name):
            pavs = prod.product_template_attribute_value_ids
            for v in pavs:
                if (v.attribute_id and (v.attribute_id.name or "").lower() == name.lower()):
                    return v.name
            return None

        best = None
        for p in candidates:
            # Flute check explicit again (case-insensitive)
            if role == "medium" and flute:
                fv = get_attr(p, "Flute")
                if not fv or fv.lower() != (flute or "").lower():
                    continue
            # When resolving a liner, ignore variants that carry a Flute attribute
            # (i.e., medium papers). This avoids ambiguity when GSM exists on both
            # liners and mediums (e.g., GSM 150).
            if role == "liner":
                if get_attr(p, "Flute"):
                    continue
            gv = get_attr(p, "GSM")
            try:
                gvf = float(gv) if gv is not None else None
            except Exception:
                gvf = None
            if gvf is None:
                continue
            diff = abs(gvf - float(gsm))
            if diff <= tol_abs:
                if best is None or diff < best[0]:
                    best = (diff, p)
                elif best and abs(diff - best[0]) < 1e-9:
                    # ambiguous; keep as ambiguous by marking None
                    best = (best[0], None)
        if best and best[1]:
            prod = best[1]
            return {"product": prod, "uom": prod.uom_id}
        return None
