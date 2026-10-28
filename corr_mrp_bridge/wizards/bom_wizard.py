import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

try:
    # Engine V2 integration (pure engine + adapters).
    from odoo.addons.corr_engine_v2.engine_v2 import compute as engine_v2_compute
    from odoo.addons.corr_engine_v2.engine_v2.styles.contract import (
        MissingStyleParameterError,
    )
    from odoo.addons.corr_engine_v2.spec_adapter import build_engine_spec_input
    from odoo.addons.corr_engine_v2.plant_adapter import build_plant_profile
    from odoo.addons.corr_mrp_bridge import bom_adapter as engine_v2_bom_adapter
except Exception:  # pragma: no cover - defensive fallback
    engine_v2_compute = None
    MissingStyleParameterError = None
    build_engine_spec_input = None
    build_plant_profile = None
    engine_v2_bom_adapter = None

_logger = logging.getLogger(__name__)


def _archive_operations(operations):
    """Archive BoM operations instead of deleting history-linked rows."""

    if not operations:
        return
    if "active" not in operations._fields:
        operations.unlink()
        return
    try:
        operations.action_archive()
    except Exception:
        operations.write({"active": False})


class CorrBomGenerateWizard(models.TransientModel):
    _name = "corr.bom.generate.wizard"
    _description = "Create/Update BoM from Corrugation Spec"

    spec_id = fields.Many2one("corr.spec.template", required=True)
    target_product_tmpl_id = fields.Many2one("product.template", string="Finished Product")
    remove_obsolete = fields.Boolean(default=True)
    preview = fields.Text(readonly=True)
    missing_mappings = fields.Text(readonly=True)
    has_missing_variants = fields.Boolean(readonly=True)
    missing_variant_payload = fields.Text(readonly=True)
    include_operations = fields.Boolean(string="Include Operations", default=False)
    include_waste_in_bom = fields.Boolean(
        string="Include Waste in BoM",
        help="When enabled, BoM component quantities use the engine 'with waste' values instead of net quantities.",
        default=False,
    )
    include_scrap_byproduct = fields.Boolean(string="Include Scrap By-product", default=False)
    overhead_per_1000 = fields.Float(string="Overhead per 1,000 units", default=0.0)
    include_sfg_phantom = fields.Boolean(string="Use SFG (phantom) stage", default=False)
    sfg_mode = fields.Selection([
        ("none", "No SFG"),
        ("flat_single_wo", "Flat FG (Single Work Order)"),
        ("phantom", "SFG Phantom"),
        ("staged", "Staged Stockable SFGs"),
    ], string="SFG Mode", default=lambda self: self.env["ir.config_parameter"].sudo().get_param("corr_mrp_bridge.sfg_mode") or "staged")

    # ---- Debug (read-only) to visualize gating for sheet-level staging ----
    debug_style = fields.Char(string="Debug: Style", readonly=True)
    debug_printing_ups = fields.Integer(string="Debug: Printing UPS", readonly=True)
    debug_flag_enabled = fields.Boolean(string="Debug: Sheet Staging Flag", readonly=True)
    debug_sheet_flag = fields.Boolean(string="Debug: Sheet Path Active", readonly=True)
    debug_mode_label = fields.Char(string="Debug: Effective Mode", readonly=True)
    debug_fg_base_qty_target = fields.Float(string="Debug: FG Base Qty (target)", readonly=True)
    debug_board_top_sheet_qty = fields.Float(string="Debug: Board Top Sheet Qty", readonly=True)
    debug_stage_text = fields.Text(string="Debug: Staged Mapping", readonly=True)

    def _extract_result_qty(self, result):
        """Best-effort read of the compute quantity from engine decision log."""
        for entry in (result.get("decision_log") or []):
            try:
                raw = str(entry or "").strip()
            except Exception:
                raw = ""
            if not raw.lower().startswith("input qty:"):
                continue
            try:
                return float(raw.split(":", 1)[1].strip())
            except Exception:
                return None
        return None

    @api.onchange("spec_id")
    def _onchange_spec_set_sfg_mode(self):
        """Auto-select SFG mode for simple sheet/pad specs.

        When the user picks a SHEET/PAD style spec, default SFG Mode to
        'No SFG' so we do not create staged/phantom structures by default.
        Users can still manually override the mode afterwards if needed.
        """
        for rec in self:
            spec = rec.spec_id
            if not spec:
                continue
            try:
                style_val = (spec.style or "").upper()
            except Exception:
                style_val = ""
            if style_val in {"SHEET", "PAD"}:
                rec.sfg_mode = "none"

    @api.onchange('spec_id','sfg_mode')
    def _onchange_debug_compute(self):
        spec = self.spec_id
        icp = self.env["ir.config_parameter"].sudo()
        flag = icp.get_param("corr_mrp_bridge.sheet_staging_bmc", default="0") in ("1", "true", "True")
        style = (getattr(spec, 'style', '') or '')
        ups = 0
        try:
            xp = (getattr(spec, 'extra_params_json', None) or {})
            pv = xp.get('printing_ups') or xp.get('printing_ups_ui')
            if pv is not None:
                ups = int(float(pv or 0))
        except Exception:
            ups = 0
        sheet_ok = (self.sfg_mode == 'staged') and flag and ("BMC" in (style or '').upper()) and (ups and ups > 0)
        self.debug_style = style
        self.debug_printing_ups = int(ups or 0)
        self.debug_flag_enabled = bool(flag)
        self.debug_sheet_flag = bool(sheet_ok)
        self.debug_mode_label = "Staged (BMC sheet-level)" if sheet_ok else ("Staged (per-box)" if self.sfg_mode == 'staged' else self.sfg_mode or 'n/a')
        self.debug_fg_base_qty_target = float(ups or 1) if sheet_ok else 1.0
        # Board top sheet qty: 1 per sheet when sheet path active; otherwise 1/UPS per box
        self.debug_board_top_sheet_qty = 1.0 if sheet_ok else ( (1.0/float(ups)) if ups else 0.0 )

    def _load_result(self, spec):
        if spec.last_result_json:
            try:
                snapshot = json.loads(spec.last_result_json)
            except Exception:
                snapshot = None
            if snapshot:
                metrics = snapshot.get("metrics") or {}
                ply_detail = metrics.get("ply_detail") or []
                needs_refresh = False
                for ply in ply_detail:
                    role = (ply.get("role") or "").lower()
                    if role not in ("liner", "medium"):
                        continue
                    bf_val = ply.get("bf")
                    if bf_val is None:
                        needs_refresh = True
                        break
                    try:
                        if float(bf_val) == 0.0:
                            needs_refresh = True
                            break
                    except Exception:
                        needs_refresh = True
                        break
                snapshot_sig = snapshot.get("signature")
                current_sig = spec.signature
                signature_ok = bool(snapshot_sig and current_sig and snapshot_sig == current_sig)
                if not signature_ok:
                    needs_refresh = True
                # BoM generation must always use a base quantity of 1 so the
                # saved BoM definition remains normalized and later quantity
                # scaling is deterministic from that base.
                snapshot_qty = self._extract_result_qty(snapshot)
                if snapshot_qty != 1.0:
                    needs_refresh = True
                if not needs_refresh:
                    return snapshot
        # fallback compute with qty=1
        result = self.env["corr.formula.engine"].compute_requirements(spec, 1)
        # do not overwrite spec snapshot here; wizard remains non-destructive
        return result

    def _prefers_upper_liner_for_unit_top(self, spec, layers_fg=None):
        """Option 2 for unit-top stacks: medium-first stacks pair medium->next liner.

        Keep existing lower-first pairing for Roll mode and for legacy
        liner-first Unit-mode stacks. Only switch to upper-first when the
        spec is in Unit/Sheet mode *and* the saved stack actually starts
        with a medium.
        """
        try:
            xp = getattr(spec, "extra_params_json", None) or {}
            if isinstance(xp, str):
                try:
                    xp = json.loads((xp or "{}").strip())
                except Exception:
                    xp = {}
            mode_val = getattr(spec, "top_layer_mode_ui", False) or xp.get("top_layer_mode") or xp.get("top_layer") or ""
            mode_norm = str(mode_val).strip().lower()
        except Exception:
            mode_norm = ""
        use_unit_top = mode_norm in ("unit", "units", "sheet", "printed", "printed_sheet")
        if not use_unit_top and getattr(spec, "top_sheet_product_id", False):
            use_unit_top = True
        if not use_unit_top:
            return False

        layers = list(layers_fg or [])
        if not layers:
            return False
        try:
            first_role = str((layers[0] or {}).get("role") or "").strip().lower()
        except Exception:
            first_role = ""
        return first_role == "medium"

    def _select_liner_for_medium(self, layers_fg, mid_idx, *, prefer_upper=False):
        lower_idx = mid_idx - 1
        upper_idx = mid_idx + 1
        preferred = (upper_idx, lower_idx) if prefer_upper else (lower_idx, upper_idx)
        for idx in preferred:
            for lyr in layers_fg:
                if lyr["idx"] == idx and lyr["role"] == "liner":
                    return lyr
        return None

    def _build_2ply_pairs_from_layers(self, spec, layers_fg):
        prefer_upper = self._prefers_upper_liner_for_unit_top(spec, layers_fg)
        medium_idxs = [x["idx"] for x in layers_fg if x["role"] == "medium"]
        pairs = []
        for m_idx in medium_idxs:
            med = next((x for x in layers_fg if x["idx"] == m_idx), None)
            lin = self._select_liner_for_medium(layers_fg, m_idx, prefer_upper=prefer_upper)
            if med and lin:
                pairs.append((lin, med))
        return pairs

    # ---- Operation timing helpers ----
    def _get_ups_from_metrics(self, metrics):
        """Resolve printing UPS from metrics or the attached spec; default 1.
        Works for both FG and staged temp specs.
        """
        # 1) metrics.layout.printing_ups
        try:
            layout = (metrics or {}).get("layout") or {}
            ups = layout.get("printing_ups")
            if ups is not None:
                ups = int(float(ups or 0)) or 1
                return max(1, ups)
        except Exception:
            pass
        # 2) Do not fallback to spec UPS here: staged SFG computations
        #    intentionally omit UPS and should be treated as sheet-level (UPS=1).
        return 1

    def _compute_size_metrics(self, metrics):
        """Compute key size metrics: sheet length/area and unit area in SI."""
        geo = (metrics or {}).get("geometry") or {}
        try:
            length_m = float(geo.get("length_mm") or 0.0) / 1000.0
        except Exception:
            length_m = 0.0
        try:
            deckle_m = float(geo.get("deckle_mm") or 0.0) / 1000.0
        except Exception:
            deckle_m = 0.0
        # Robust fallback: if stage metrics lack geometry, borrow from the attached spec
        if (length_m <= 0.0 or deckle_m <= 0.0):
            try:
                spec = self.spec_id.sudo()
                lm = getattr(spec, "length_mm", None)
                wm = getattr(spec, "width_mm", None)
                if (not length_m) and lm:
                    length_m = float(lm or 0.0) / 1000.0
                if (not deckle_m) and wm:
                    deckle_m = float(wm or 0.0) / 1000.0
            except Exception:
                pass
        sheet_area_m2 = max(0.0, length_m * deckle_m)
        ups = self._get_ups_from_metrics(metrics)
        unit_area_m2 = sheet_area_m2 / float(ups or 1)
        return {
            "length_m": length_m,
            "sheet_area_m2": sheet_area_m2,
            "unit_area_m2": unit_area_m2,
            "ups": ups,
        }

    def _compute_runtime_from_map(self, m, metrics):
        """Return minutes per finished unit from a mapping row and metrics.
        Applies basis conversion and optional scaling.
        """
        if not m:
            return 0.0
        vals = self._compute_size_metrics(metrics)
        length_m = vals["length_m"]
        sheet_area_m2 = vals["sheet_area_m2"]
        unit_area_m2 = vals["unit_area_m2"]
        ups = max(1, int(vals["ups"] or 1))

        basis = (getattr(m, "speed_basis", False) or "units_per_hour")
        base_speed = getattr(m, "base_speed", None)
        # Strong backward-compat for Units/h rows added before upgrade:
        # prefer legacy field when basis is Units/h and it has a value.
        try:
            if basis == "units_per_hour" and (m.speed_units_per_hour or 0.0) > 0:
                base_speed = m.speed_units_per_hour
        except Exception:
            pass
        if base_speed in (None, 0.0):
            # Generic fallback
            base_speed = m.speed_units_per_hour or 0.0
        run_min = 0.0
        try:
            if basis == "units_per_hour":
                run_min = (60.0 / float(base_speed)) if base_speed > 0 else 0.0
            elif basis == "sheets_per_hour":
                per_sheet = (60.0 / float(base_speed)) if base_speed > 0 else 0.0
                run_min = per_sheet / float(ups or 1)
            elif basis == "meters_per_min":
                per_sheet = (length_m / float(base_speed)) if base_speed > 0 else 0.0
                run_min = per_sheet / float(ups or 1)
            elif basis == "sqm_per_hour":
                per_unit = ((unit_area_m2 / float(base_speed)) * 60.0) if base_speed > 0 else 0.0
                run_min = per_unit
            else:
                run_min = (60.0 / float(base_speed)) if base_speed > 0 else 0.0
        except Exception:
            run_min = 0.0

        # Scaling
        try:
            mode = (getattr(m, "scaling_mode", None) or "none")
            # Gather metric values
            metric_vals = {
                "unit_area_m2": unit_area_m2,
                "sheet_area_m2": sheet_area_m2,
                "sheet_length_m": length_m,
            }
            mult = 1.0
            if mode == "reference":
                rm = getattr(m, "ref_metric", None)
                rv = getattr(m, "ref_value", None)
                if rm and rv and float(rv) > 0:
                    actual = float(metric_vals.get(rm) or 0.0)
                    if actual > 0:
                        mult = actual / float(rv)
            elif mode == "buckets":
                # choose first matching rule by sequence
                rules = getattr(m, "rule_ids", [])
                preferred = getattr(m, "ref_metric", None)
                best = None
                try:
                    rules_iter = sorted(list(rules), key=lambda x: x.sequence or 0)
                except Exception:
                    rules_iter = list(rules)
                for r in rules_iter:
                    mx = r.metric or preferred or "unit_area_m2"
                    actual = float(metric_vals.get(mx) or 0.0)
                    lo = r.min_value if r.min_value not in (None,) else None
                    hi = r.max_value if r.max_value not in (None,) else None
                    if (lo is None or actual >= float(lo)) and (hi is None or actual < float(hi)):
                        best = r
                        break
                if best:
                    mult = float(best.multiplier or 1.0)
            # Clamp multiplier
            try:
                lo = getattr(m, "scale_min", None)
                hi = getattr(m, "scale_max", None)
                # Treat 0 or negative as not set; only apply positive clamps
                if lo not in (None, "", 0, 0.0) and float(lo) > 0:
                    mult = max(float(lo), mult)
                if hi not in (None, "", 0, 0.0) and float(hi) > 0:
                    mult = min(float(hi), mult)
            except Exception:
                pass
            run_min *= mult
        except Exception:
            pass

        # Safety clamp: no negatives, avoid inf
        try:
            if run_min < 0:
                run_min = 0.0
            if run_min != run_min:  # NaN
                run_min = 0.0
        except Exception:
            run_min = 0.0
        return run_min

    def _component_key(self, role, layer_no, product_id):
        return f"role:{role}|layer:{layer_no or 0}|product:{product_id}"

    def _component_sequence(self, role, layer_no):
        role_order = {
            "top_sheet": 0,
            "liner": 1,
            "medium": 2,
            "adhesive": 50,
            "ink": 60,
            "lamination": 70,
            "wire": 80,
            "tape": 90,
        }
        try:
            layer_idx = int(layer_no or 0)
        except Exception:
            layer_idx = 0
        base = (layer_idx * 10) if layer_idx > 0 else 1000
        return base + role_order.get(role or "", 99)

    def _match_roll_top_liner_candidates(self, comp, metrics, mapped, bf):
        target_gsm = None
        try:
            gsm_val = comp.get("gsm")
            if gsm_val is None and mapped:
                gsm_val = mapped.get("gsm")
            if gsm_val is not None:
                target_gsm = float(gsm_val)
        except Exception:
            target_gsm = None

        target_bf = None
        explicit_bf = comp.get("bf") is not None
        explicit_layer = bool(comp.get("layer_no"))
        try:
            if (explicit_bf or explicit_layer) and bf is not None:
                target_bf = float(bf)
        except Exception:
            target_bf = None

        target_color = None
        try:
            target_color = (comp.get("color") or "").strip().lower() or None
        except Exception:
            target_color = None

        candidates = []
        for ply in (metrics.get("ply_detail") or []):
            try:
                idx = int(ply.get("idx") or 0)
            except Exception:
                idx = 0
            if idx <= 0 or (ply.get("role") or "").lower() != "liner":
                continue
            try:
                ply_gsm = float(ply.get("gsm") or 0.0)
            except Exception:
                ply_gsm = 0.0
            if target_gsm is not None and abs(ply_gsm - target_gsm) > 1e-6:
                continue
            if target_bf is not None:
                try:
                    ply_bf = float(ply.get("bf"))
                except Exception:
                    continue
                if abs(ply_bf - target_bf) > 1e-6:
                    continue
            if target_color:
                try:
                    ply_color = (ply.get("color") or "").strip().lower()
                except Exception:
                    ply_color = ""
                if ply_color != target_color:
                    continue
            try:
                ply_tuf = float(ply.get("tuf_applied") or 0.0)
            except Exception:
                ply_tuf = 0.0
            candidates.append({
                "idx": idx,
                "eff_gsm": ply_gsm * (1.0 + ply_tuf),
                "context_key": self._liner_medium_context_key(metrics, idx),
            })
        return candidates

    @staticmethod
    def _paper_effective_gsm(ply):
        try:
            gsm_val = float(ply.get("gsm") or 0.0)
        except Exception:
            gsm_val = 0.0
        role = (ply.get("role") or "").lower()
        if role == "medium":
            try:
                tuf_val = float(ply.get("tuf_applied") or 0.0)
            except Exception:
                tuf_val = 0.0
            if tuf_val > 0:
                return gsm_val * tuf_val
        return gsm_val

    def _match_grouped_paper_candidates(self, comp, metrics, mapped, bf):
        role = (comp.get("role") or "").lower()
        if role not in ("liner", "medium"):
            return []

        target_gsm = None
        try:
            gsm_val = comp.get("gsm")
            if gsm_val is None and mapped:
                gsm_val = mapped.get("gsm")
            if gsm_val is not None:
                target_gsm = float(gsm_val)
        except Exception:
            target_gsm = None

        target_bf = None
        explicit_bf = comp.get("bf") is not None
        explicit_layer = bool(comp.get("layer_no"))
        try:
            if (explicit_bf or explicit_layer) and bf is not None:
                target_bf = float(bf)
        except Exception:
            target_bf = None

        target_color = None
        try:
            target_color = (comp.get("color") or "").strip().lower() or None
        except Exception:
            target_color = None

        target_flute = None
        if role == "medium":
            try:
                target_flute = (
                    (comp.get("flute") or "")
                    or ((mapped or {}).get("flute") or "")
                ).strip().upper() or None
            except Exception:
                target_flute = None

        candidates = []
        for ply in (metrics.get("ply_detail") or []):
            try:
                idx = int(ply.get("idx") or 0)
            except Exception:
                idx = 0
            ply_role = (ply.get("role") or "").lower()
            if idx <= 0 or ply_role != role:
                continue
            try:
                ply_gsm = float(ply.get("gsm") or 0.0)
            except Exception:
                ply_gsm = 0.0
            if target_gsm is not None and abs(ply_gsm - target_gsm) > 1e-6:
                continue
            if role == "medium" and target_flute:
                ply_flute = ((ply.get("flute_code") or ply.get("flute") or "")).strip().upper()
                if ply_flute != target_flute:
                    continue
            if target_bf is not None:
                try:
                    ply_bf = float(ply.get("bf"))
                except Exception:
                    continue
                if abs(ply_bf - target_bf) > 1e-6:
                    continue
            if target_color:
                try:
                    ply_color = (ply.get("color") or "").strip().lower()
                except Exception:
                    ply_color = ""
                if ply_color != target_color:
                    continue
            candidates.append({
                "idx": idx,
                "eff_gsm": self._paper_effective_gsm(ply),
            })
        return candidates

    def _pair_signature_for_layer(self, metrics, layer_idx, role):
        try:
            target_idx = int(layer_idx or 0)
        except Exception:
            target_idx = 0
        if target_idx <= 0:
            return ()

        ply_by_idx = {}
        for ply in (metrics.get("ply_detail") or []):
            try:
                idx = int(ply.get("idx") or 0)
            except Exception:
                idx = 0
            if idx > 0:
                ply_by_idx[idx] = ply

        role_norm = (role or "").lower()
        ordered_layers = sorted(ply_by_idx.items(), key=lambda item: item[0])
        medium_first = bool(
            ordered_layers
            and (ordered_layers[0][1].get("role") or "").lower() == "medium"
        )
        liner = None
        medium = None
        if medium_first and role_norm == "liner":
            liner = ply_by_idx.get(target_idx)
            prev_ply = ply_by_idx.get(target_idx - 1)
            if prev_ply and (prev_ply.get("role") or "").lower() == "medium":
                medium = prev_ply
        elif medium_first and role_norm == "medium":
            medium = ply_by_idx.get(target_idx)
            next_ply = ply_by_idx.get(target_idx + 1)
            if next_ply and (next_ply.get("role") or "").lower() == "liner":
                liner = next_ply
        elif role_norm == "liner":
            liner = ply_by_idx.get(target_idx)
            next_ply = ply_by_idx.get(target_idx + 1)
            if next_ply and (next_ply.get("role") or "").lower() == "medium":
                medium = next_ply
        elif role_norm == "medium":
            medium = ply_by_idx.get(target_idx)
            prev_ply = ply_by_idx.get(target_idx - 1)
            if prev_ply and (prev_ply.get("role") or "").lower() == "liner":
                liner = prev_ply

        if not liner or not medium:
            return ()

        def _fnum(val):
            try:
                return float(val)
            except Exception:
                return 0.0

        return (
            ("liner",
             _fnum(liner.get("gsm")),
             _fnum(liner.get("bf")) if liner.get("bf") is not None else 0.0,
             (liner.get("color") or "").strip().lower()),
            ("medium",
             (medium.get("flute_code") or medium.get("flute") or "").strip().upper(),
             _fnum(medium.get("gsm")),
             _fnum(medium.get("bf")) if medium.get("bf") is not None else 0.0,
             (medium.get("color") or "").strip().lower()),
        )

    @staticmethod
    def _liner_medium_context_key(metrics, layer_idx):
        try:
            target_idx = int(layer_idx or 0)
        except Exception:
            target_idx = 0
        if target_idx <= 0:
            return ()

        layers = []
        for ply in (metrics.get("ply_detail") or []):
            try:
                idx = int(ply.get("idx") or 0)
            except Exception:
                idx = 0
            if idx > 0:
                layers.append((idx, ply))
        layers.sort(key=lambda item: item[0])
        pos = None
        for seq, (idx, _ply) in enumerate(layers):
            if idx == target_idx:
                pos = seq
                break
        if pos is None:
            return ()

        signatures = []
        for neighbor_pos in (pos - 1, pos + 1):
            if neighbor_pos < 0 or neighbor_pos >= len(layers):
                continue
            neighbor = layers[neighbor_pos][1]
            if (neighbor.get("role") or "").lower() != "medium":
                continue
            signatures.append((
                (neighbor.get("flute_code") or neighbor.get("flute") or "").strip().upper(),
                float(neighbor.get("gsm") or 0.0),
                float(neighbor.get("bf") or 0.0) if neighbor.get("bf") is not None else 0.0,
                (neighbor.get("color") or "").strip().lower(),
            ))
        return tuple(sorted(set(signatures)))

    @staticmethod
    def _split_qty_by_effective_gsm(total_qty, candidates):
        total_eff_gsm = sum(float(c.get("eff_gsm") or 0.0) for c in candidates)
        if total_eff_gsm <= 0:
            return None
        result = []
        for cand in candidates:
            eff_gsm = float(cand.get("eff_gsm") or 0.0)
            if eff_gsm <= 0:
                continue
            share_qty = total_qty * (eff_gsm / total_eff_gsm)
            result.append({
                **cand,
                "qty": share_qty,
            })
        if not result:
            return None
        return result

    def _resolve_roll_top_layer_idx(self, comp, metrics, mapped, bf, roll_top_layer_no):
        role = (comp.get("role") or "").lower()
        if role != "liner" or not roll_top_layer_no:
            return (mapped or {}).get("layer_idx") or 0
        try:
            raw_layer = int(comp.get("layer_no") or 0)
        except Exception:
            raw_layer = 0
        if raw_layer > 0:
            return raw_layer

        candidates = [c.get("idx") for c in self._match_roll_top_liner_candidates(comp, metrics, mapped, bf)]
        if roll_top_layer_no in candidates:
            return roll_top_layer_no
        return (mapped or {}).get("layer_idx") or 0

    def _split_roll_top_liner_component(self, comp, metrics, mapped, bf, roll_top_layer_no):
        role = (comp.get("role") or "").lower()
        if role != "liner" or not roll_top_layer_no:
            return None
        try:
            raw_layer = int(comp.get("layer_no") or 0)
        except Exception:
            raw_layer = 0
        if raw_layer > 0:
            return None
        try:
            total_qty = float(comp.get("qty") or 0.0)
        except Exception:
            total_qty = 0.0
        if total_qty <= 0:
            return None

        candidates = self._match_roll_top_liner_candidates(comp, metrics, mapped, bf)
        candidate_ids = [c.get("idx") for c in candidates]
        if len(candidate_ids) <= 1 or roll_top_layer_no not in candidate_ids:
            return None

        total_eff_gsm = sum(float(c.get("eff_gsm") or 0.0) for c in candidates)
        if total_eff_gsm <= 0:
            return None
        top_eff_gsm = sum(
            float(c.get("eff_gsm") or 0.0)
            for c in candidates
            if int(c.get("idx") or 0) == int(roll_top_layer_no or 0)
        )
        if top_eff_gsm <= 0:
            return None

        top_qty = total_qty * (top_eff_gsm / total_eff_gsm)
        remaining_qty = total_qty - top_qty
        if top_qty <= 0 or remaining_qty <= 0:
            return None

        non_top_candidates = [
            int(c.get("idx") or 0)
            for c in candidates
            if int(c.get("idx") or 0) != int(roll_top_layer_no or 0)
        ]
        return {
            "top_qty": top_qty,
            "remaining_qty": remaining_qty,
            "top_layer_no": int(roll_top_layer_no or 0),
            "remaining_layer_no": min(non_top_candidates) if non_top_candidates else ((mapped or {}).get("layer_idx") or 0),
        }

    def _split_liner_component_for_bom(self, comp, metrics, mapped, bf, roll_top_layer_no):
        return self._split_paper_component_for_bom(comp, metrics, mapped, bf, roll_top_layer_no)

    def _split_medium_component_for_bom(self, comp, metrics, mapped, bf, roll_top_layer_no):
        return self._split_paper_component_for_bom(comp, metrics, mapped, bf, roll_top_layer_no)

    def _split_paper_component_for_bom(self, comp, metrics, mapped, bf, roll_top_layer_no):
        role = (comp.get("role") or "").lower()
        if role not in ("liner", "medium"):
            return None
        try:
            raw_layer = int(comp.get("layer_no") or 0)
        except Exception:
            raw_layer = 0
        if raw_layer > 0:
            return None
        try:
            total_qty = float(comp.get("qty") or 0.0)
        except Exception:
            total_qty = 0.0
        if total_qty <= 0:
            return None

        candidates = self._match_grouped_paper_candidates(comp, metrics, mapped, bf)
        candidate_ids = [int(c.get("idx") or 0) for c in candidates if int(c.get("idx") or 0) > 0]
        if len(candidate_ids) <= 1:
            return None

        allocated = self._split_qty_by_effective_gsm(total_qty, candidates)
        if not allocated:
            return None

        grouped_rows = {}
        for cand in allocated:
            idx = int(cand.get("idx") or 0)
            if idx <= 0:
                continue
            if role == "liner" and roll_top_layer_no and idx == int(roll_top_layer_no or 0):
                group_key = ("top_sheet", idx)
                corr_role = "top_sheet"
            else:
                pair_sig = self._pair_signature_for_layer(metrics, idx, role)
                group_key = (role, pair_sig or idx)
                corr_role = role

            row = grouped_rows.setdefault(group_key, {
                "corr_role": corr_role,
                "layer_no": idx,
                "qty": 0.0,
            })
            row["layer_no"] = min(int(row.get("layer_no") or idx), idx)
            row["qty"] += float(cand.get("qty") or 0.0)

        split_rows = list(grouped_rows.values())
        if len(split_rows) <= 1 and not any((row.get("corr_role") == "top_sheet") for row in split_rows):
            return None
        split_rows.sort(
            key=lambda row: (
                0 if row.get("corr_role") == "top_sheet" else 1,
                int(row.get("layer_no") or 0),
            )
        )
        return split_rows

    def _corr_qty_uom_preserving_small_weight(self, qty, uom):
        try:
            qty = float(qty or 0.0)
        except Exception:
            qty = 0.0
        if not uom:
            return qty, uom
        try:
            kg = self.env.ref("uom.product_uom_kgm")
        except Exception:
            return qty, uom
        if kg and kg.category_id == uom.category_id:
            return uom._compute_quantity(qty, kg, round=False), kg
        return qty, uom

    @staticmethod
    def _format_layer_label(role, layer_idx):
        role = role or ""
        try:
            layer_idx = int(layer_idx or 0)
        except Exception:
            layer_idx = 0
        prefix_map = {"liner": "Liner", "medium": "Flute", "top_sheet": "TopSheet"}
        if layer_idx:
            prefix = prefix_map.get(role) or (role.title() if role else "Layer")
            prefix = prefix.replace(" ", "")
            return f"{prefix}-Layer{layer_idx}"
        return prefix_map.get(role) or (role.title() if role else "")

    def _map_component(self, comp, metrics):
        Map = self.env["corr.material.product.map"]
        map_spec = self.spec_id
        map_stack_json = self.env.context.get("corr_map_stack_json")
        if not isinstance(map_stack_json, (list, tuple)):
            map_stack_json = []
        try:
            ctx_spec_id = int(self.env.context.get("corr_map_spec_id") or 0)
        except Exception:
            ctx_spec_id = 0
        if ctx_spec_id:
            ctx_spec = self.env["corr.spec.template"].sudo().browse(ctx_spec_id).exists()
            if ctx_spec:
                map_spec = ctx_spec
        role = (comp.get("role") or "").lower()
        layer_no = comp.get("layer_no")
        try:
            layer_idx = int(layer_no or 0)
        except Exception:
            layer_idx = 0
        gsm = None
        flute = None
        bf = None
        color = None
        explicit_color = False

        def _overlay_stack_attrs(idx):
            nonlocal bf, color, explicit_color
            try:
                idx = int(idx or 0)
            except Exception:
                idx = 0
            if idx <= 0:
                return
            try:
                if map_stack_json and 1 <= idx <= len(map_stack_json):
                    layer = map_stack_json[idx - 1] or {}
                    if bf is None:
                        try:
                            bf_layer = layer.get("bf")
                            if bf_layer is not None and float(bf_layer) > 0:
                                bf = float(bf_layer)
                        except Exception:
                            pass
                    try:
                        col = layer.get("color") or None
                        if col:
                            color = col
                            explicit_color = True
                    except Exception:
                        pass
                    return
            except Exception:
                pass
            try:
                spec = map_spec.sudo()
                lines = spec.stack_line_ids.sorted(key=lambda l: l.sequence or 0) if spec else self.env["corr.spec.stack.line"]
                if spec and lines and 1 <= idx <= len(lines):
                    sl = lines[idx - 1]
                    if bf is None:
                        bf_line = getattr(sl, "bf", None)
                        try:
                            if bf_line is not None and float(bf_line) > 0:
                                bf = float(bf_line)
                        except Exception:
                            pass
                    if (bf is None) and spec and spec.product_tmpl_id:
                        try:
                            bf_prod = getattr(spec.product_tmpl_id, f"corr_layer{idx}_bf", None)
                            if bf_prod is not None and float(bf_prod) > 0:
                                bf = float(bf_prod)
                        except Exception:
                            pass
                    try:
                        col = getattr(sl, "color", None)
                        if col:
                            color = col
                            explicit_color = True
                    except Exception:
                        pass
            except Exception:
                pass

        if role in ("liner", "medium"):
            if layer_no:
                for p in (metrics.get("ply_detail") or []):
                    idx_val = int(p.get("idx") or 0)
                    if idx_val == int(layer_no or 0):
                        gsm = float(p.get("gsm") or 0.0)
                        flute = (p.get("flute") or None)
                        try:
                            if p.get("bf") is not None:
                                bf = float(p.get("bf"))
                        except Exception:
                            bf = None
                        layer_idx = idx_val
                        break
            # Overlay BF from stack context/spec only when engine did not
            # provide it, but always prefer explicit per-layer color.
            _overlay_stack_attrs(layer_idx)
            # Fallback: allow rm line to carry gsm/flute for grouped outputs
            if gsm in (None, 0.0):
                try:
                    gsm_val = comp.get("gsm")
                    if gsm_val is not None:
                        gsm = float(gsm_val)
                except Exception:
                    pass
            if not flute:
                flute = comp.get("flute") or flute
            # Try to infer BF when layer_no is absent (aggregated lines)
            if bf is None:
                try:
                    ply = list(metrics.get("ply_detail") or [])
                    cand = [x for x in ply if (x.get("role") or "").lower() == role]
                    if role == "medium" and flute:
                        cand = [x for x in cand if (x.get("flute") or "").lower() == (flute or "").lower()]
                    def _to_float(v):
                        try:
                            return float(v)
                        except Exception:
                            return None
                    chosen = None
                    if gsm not in (None, 0.0):
                        icp = self.env["ir.config_parameter"].sudo()
                        try:
                            tol_pct = float(icp.get_param("corr_mrp_bridge.auto_map_tolerance_pct") or "")
                        except Exception:
                            tol_pct = None
                        if tol_pct is None:
                            plant = self.env["corr.plant.param"].sudo().search([], limit=1)
                            tol_pct = float((plant and plant.gsm_tolerance_pct) or 5.0)
                        tol_abs = abs(float(gsm)) * (tol_pct / 100.0)
                        best = None
                        for x in cand:
                            gx = _to_float(x.get("gsm"))
                            if gx is None:
                                continue
                            diff = abs(gx - float(gsm))
                            if tol_abs and diff > tol_abs:
                                continue
                            key = (diff,)
                            if best is None or key < best[0]:
                                best = (key, x)
                        chosen = best and best[1]
                    if not chosen and cand:
                        for x in cand:
                            if x.get("bf") is not None:
                                chosen = x
                                break
                    if chosen and chosen.get("bf") is not None:
                        try:
                            bf = float(chosen.get("bf"))
                        except Exception:
                            bf = None
                    if chosen and chosen.get("idx"):
                        try:
                            layer_idx = int(chosen.get("idx") or 0)
                        except Exception:
                            pass
                    if chosen and not color:
                        try:
                            color = chosen.get("color") or None
                        except Exception:
                            color = color or None
                except Exception:
                    pass
            _overlay_stack_attrs(layer_idx)
            # Color: prefer layer color from spec/engine; else fallback to default
            icp = self.env["ir.config_parameter"].sudo()
            default_color = icp.get_param("planning.default_color_name") or "Natural Shade"
            if not color:
                color = comp.get("color") or None
            if not color:
                color = default_color
        # Compute deckle (cm) target from engine geometry and spec UPS for paper roles.
        # For BMC-Custom we use liner slitting UPS; for other styles
        # we derive the slitting target deckle from per-box blank deckle and Slitting UPS
        # so that, e.g., slitting_ups=2 -> target deckle ≈ 2 * blank_deckle.
        deckle_total_cm = None
        deckle_tol_cm = 0.0
        if role in ("liner", "medium"):
            try:
                geo = (metrics.get("geometry") or {})
                deckle_mm = float(geo.get("deckle_mm") or 0.0)
                style = (map_spec.style or "") if map_spec else ""
                xp = (map_spec and map_spec.extra_params_json) or {}
                if isinstance(xp, str):
                    try:
                        xp = json.loads((xp or "{}").strip() or "{}")
                    except Exception:
                        xp = {}
                deckle_total_cm = None
                if deckle_mm > 0:
                    base_cm = deckle_mm / 10.0
                    if style == "BMC-Custom":
                        su = 1
                        try:
                            su_raw = xp.get("liner_slitting_ups") or xp.get("liner_slitting_ups_ui")
                            if su_raw is not None:
                                su = int(float(su_raw)) or 1
                        except Exception:
                            su = 1
                        if su <= 0:
                            su = 1
                        deckle_total_cm = base_cm * su
                    else:
                        su = 1
                        try:
                            su_raw = xp.get("liner_slitting_ups") or xp.get("liner_slitting_ups_ui")
                            if su_raw is not None:
                                su = int(float(su_raw)) or 1
                        except Exception:
                            su = 1
                        if su <= 0:
                            su = 1
                        deckle_total_cm = base_cm * su
            except Exception:
                deckle_total_cm = None
            # For BoM preview/apply, paper deckle must match exactly so the
            # shown product and the applied product stay fully aligned to the
            # computed target deckle. Keep this scoped to the wizard mapping
            # path only; compute and other planners are unaffected.
            deckle_tol_cm = 0.0

        def _paper_variant_matches_request(product, *, role, gsm, flute, bf, color):
            if role not in ("liner", "medium") or not product:
                return True

            def _get_attr(prod, name):
                for v in prod.product_template_attribute_value_ids:
                    if v.attribute_id and (v.attribute_id.name or "").strip().lower() == name.strip().lower():
                        return v.name
                return None

            def _get_attr_from_list(prod, names):
                for nm in names:
                    val = _get_attr(prod, nm)
                    if val not in (None, ""):
                        return val
                return None

            def _to_float(val):
                if val is None:
                    return None
                try:
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

            def _norm(val):
                return str(val or "").strip().lower()

            prod_name = "%s %s" % (product.display_name or "", product.name or "")
            gsm_attr = _get_attr_from_list(product, ["GSM", "Paper GSM", "Grammage", "Gsm"])
            gsm_val = _to_float(gsm_attr)
            if gsm_val is None:
                try:
                    import re
                    m = re.search(r"\(([-+]?\d*\.?\d+)\s*,", prod_name)
                    if m:
                        gsm_val = float(m.group(1))
                except Exception:
                    gsm_val = None
            if gsm not in (None, 0, 0.0) and gsm_val is not None and abs(float(gsm_val) - float(gsm)) > 0.000001:
                return False

            if role == "medium" and flute:
                flute_attr = _get_attr_from_list(product, ["Flute", "Flute Type", "Flute Code"])
                if flute_attr and _norm(flute_attr) != _norm(flute):
                    return False

            color_attr = _get_attr_from_list(product, ["Color", "Shade", "Paper Color"])
            if color and color_attr:
                requested = _norm(color)
                actual = _norm(color_attr)
                if requested and actual and requested not in actual and actual not in requested:
                    return False

            bf_attr = _get_attr_from_list(product, ["BF", "Burst Factor", "Burst Factor (BF)"])
            bf_val = _to_float(bf_attr)
            if bf_val is None:
                try:
                    import re
                    inner = re.search(r"\(([^)]*)\)", prod_name)
                    if inner:
                        nums = re.findall(r"[-+]?\d*\.?\d+", inner.group(1))
                        if len(nums) >= 3:
                            bf_val = float(nums[-1])
                except Exception:
                    bf_val = None
            if bf not in (None, 0, 0.0) and bf_val is not None:
                try:
                    strict_bf = self.env["ir.config_parameter"].sudo().get_param("planning.strict_bf_match") in ("1", "true", "True")
                except Exception:
                    strict_bf = False
                if strict_bf and abs(float(bf_val) - float(bf)) > 0.000001:
                    return False

            return True

        # 1) explicit mapping (deckle-aware for paper roles)
        prod = Map.find_match(role, gsm=gsm, flute=flute)
        if prod and role in ("liner", "medium") and deckle_total_cm:
            # Ensure the mapped product's reel width matches the engine deckle
            # within tolerance; otherwise, treat as unmapped so that auto-mapping
            # (which is also deckle-aware) can run and/or the line is marked missing.
            mapped = prod[0]

            def _get_attr(prod, name):
                for v in prod.product_template_attribute_value_ids:
                    if v.attribute_id and (v.attribute_id.name or "").lower() == name.lower():
                        return v.name
                return None

            def _get_attr_from_list(prod, names):
                for nm in names:
                    val = _get_attr(prod, nm)
                    if val is not None:
                        return val
                return None

            def _to_float(val):
                if val is None:
                    return None
                try:
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

            width_raw = _get_attr_from_list(
                mapped.product_id,
                [
                    "Deckle (cm)",
                    "Deckle",
                    "Deckle(cm)",
                    "Reel Width (cm)",
                    "Reel Width",
                ],
            )
            width_cm = _to_float(width_raw)
            # Only enforce deckle matching when the roll carries an
            # explicit deckle/width attribute. When that attribute is
            # missing, accept the mapping rather than treating it as a
            # mismatch so that simple GSM-based bands remain usable.
            if width_cm is not None and abs(width_cm - float(deckle_total_cm)) > float(deckle_tol_cm or 0.0) + 1e-6:
                prod = False

        if prod and role in ("liner", "medium"):
            mapped_product = prod[0].product_id
            if not _paper_variant_matches_request(
                mapped_product,
                role=role,
                gsm=gsm,
                flute=flute,
                bf=bf,
                color=color,
            ):
                prod = False

        if prod:
            prod = prod[0]
            uom = prod.uom_id or prod.product_id.uom_id
            return {"product": prod.product_id, "uom": uom, "gsm": gsm, "flute": flute, "auto": False, "layer_idx": layer_idx}, gsm, flute, bf

        # 2) auto-map fallback (attributes, deckle-aware for liner/medium only)
        if role in ("liner", "medium"):
            # Respect BF only when strict BF matching is enabled; otherwise, do not
            # constrain auto-map by BF (catalogs often omit BF on variants).
            try:
                strict_bf = self.env["ir.config_parameter"].sudo().get_param("planning.strict_bf_match") in ("1", "true", "True")
            except Exception:
                strict_bf = False
            bf_arg = bf if strict_bf else None

            auto = Map.auto_find_variant_v2(
                role,
                flute=flute,
                gsm=gsm,
                bf=bf_arg,
                color=color,
                deckle_total_cm=deckle_total_cm,
                deckle_tolerance_cm=deckle_tol_cm,
                # When the spec explicitly specifies a color on the stack
                # line, treat color as strict for this component: if no
                # matching roll exists for that color, the component should
                # be considered missing rather than silently falling back.
                strict_color_override=bool(explicit_color and color),
            )
            # Fallback to the default planning color only when the spec did
            # not set a per-layer color (explicit_color is False). This keeps
            # historical specs (which relied on the global default color)
            # working, while making explicit layer colors strict.
            if not auto and not explicit_color:
                try:
                    icp = self.env["ir.config_parameter"].sudo()
                    default_color = icp.get_param("planning.default_color_name") or "Natural Shade"
                except Exception:
                    default_color = "Natural Shade"
                if default_color and (not color or str(color) != str(default_color)):
                    auto = Map.auto_find_variant_v2(
                        role,
                        flute=flute,
                        gsm=gsm,
                        bf=bf_arg,
                        color=default_color,
                        deckle_total_cm=deckle_total_cm,
                        deckle_tolerance_cm=deckle_tol_cm,
                    )
            if auto:
                return {"product": auto["product"], "uom": auto["uom"], "gsm": gsm, "flute": flute, "auto": True, "layer_idx": layer_idx}, gsm, flute, bf
        # 3) Top sheet: use explicit product from RM line or spec (no auto mapping)
        if role == "top_sheet":
            Product = self.env["product.product"].sudo()
            Uom = self.env["uom.uom"].sudo()
            product = False
            prod_id = comp.get("product_id")
            if prod_id:
                try:
                    prod_id = int(prod_id)
                except Exception:
                    prod_id = False
                if prod_id:
                    product = Product.browse(prod_id)
                    if not product.exists():
                        product = False
            if not product and self.spec_id and self.spec_id.top_sheet_product_id:
                product = self.spec_id.top_sheet_product_id
            if not product:
                return None, None, None, None
            uom = False
            uom_id = comp.get("uom_id")
            if uom_id:
                try:
                    uom_id = int(uom_id)
                except Exception:
                    uom_id = False
                if uom_id:
                    uom = Uom.browse(uom_id)
                    if not uom.exists():
                        uom = False
            if not uom:
                uom = product.uom_id
            return {"product": product, "uom": uom, "gsm": None, "flute": None, "auto": False, "layer_idx": layer_idx}, None, None, None

        # 4) Non-paper consumables: resolve via system params or name heuristic
        if role in ("adhesive", "ink", "lamination", "wire", "tape"):
            Product = self.env["product.product"].sudo()
            Category = self.env["product.category"].sudo()
            icp = self.env["ir.config_parameter"].sudo()
            code = icp.get_param(f"planning.{role}_product_code")
            if code:
                cand = Product.search([("active", "=", True), ("default_code", "=", code.strip())], limit=1)
                if cand:
                    return {"product": cand, "uom": cand.uom_id, "gsm": None, "flute": None, "auto": True, "layer_idx": layer_idx}, gsm, flute, None
            cat_name = icp.get_param(f"planning.{role}_category_name")
            if cat_name:
                cat = Category.search([("name", "=ilike", (cat_name or "").strip())], limit=1)
                if cat:
                    cand = Product.search([
                        ("active", "=", True),
                        ("product_tmpl_id.categ_id", "child_of", cat.id),
                    ], limit=1)
                    if cand:
                        return {"product": cand, "uom": cand.uom_id, "gsm": None, "flute": None, "auto": True, "layer_idx": layer_idx}, gsm, flute, None
            name_hint = role.capitalize()
            cand = Product.search([("active", "=", True), ("name", "ilike", name_hint)], limit=1)
            if cand:
                return {"product": cand, "uom": cand.uom_id, "gsm": None, "flute": None, "auto": True, "layer_idx": layer_idx}, gsm, flute, None
            if role == "ink":
                ProductTemplate = self.env["product.template"].sudo()
                try:
                    kg_uom = self.env.ref("uom.product_uom_kgm")
                except Exception:
                    kg_uom = self.env.ref("uom.product_uom_unit")
                auto_code = "AUTO-INK"
                cand = Product.search([("active", "=", True), ("default_code", "=", auto_code)], limit=1)
                if not cand:
                    tmpl =   ProductTemplate.create({
                        "name": "Ink",
                        "default_code": auto_code,
                        "type": "consu",
                        "is_storable": True,
                        "uom_id": kg_uom.id,
                        "uom_po_id": kg_uom.id,
                        "purchase_ok": True,
                        "sale_ok": False,
                        "product_role": "raw_material",
                    })
                    cand = tmpl.product_variant_id
                return {"product": cand, "uom": cand.uom_id, "gsm": None, "flute": None, "auto": True, "layer_idx": layer_idx}, gsm, flute, None
        return None, gsm, flute, bf

    @staticmethod
    def _format_kraft_numeric_value(value):
        try:
            number = float(value)
        except Exception:
            return str(value or "").strip()
        if abs(number - round(number)) < 0.000001:
            return str(int(round(number)))
        return f"{number:.6f}".rstrip("0").rstrip(".")

    @staticmethod
    def _numeric_equal(left, right):
        try:
            return abs(float(left) - float(right)) < 0.000001
        except Exception:
            return False

    def _get_kraft_template(self):
        template = self.env["product.template"].sudo().search(
            [("name", "=", "Kraft Paper")],
            limit=1,
        )
        if not template:
            raise UserError(_("Kraft Paper product template was not found."))
        return template

    def _get_missing_variant_color(self, layer_idx):
        self.ensure_one()
        color = None
        try:
            if layer_idx and self.spec_id:
                lines = self.spec_id.sudo().stack_line_ids.sorted(key=lambda line: line.sequence or 0)
                if 1 <= int(layer_idx) <= len(lines):
                    color = getattr(lines[int(layer_idx) - 1], "color", None)
        except Exception:
            color = None
        if not color:
            try:
                color = self.env["ir.config_parameter"].sudo().get_param("planning.default_color_name")
            except Exception:
                color = None
        return color or "Natural Shade"

    def _resolve_missing_variant_color(self, comp, metrics, role, gsm, bf, flute, layer_idx):
        comp = comp or {}
        color = (comp.get("color") or "").strip()
        if color:
            return color

        try:
            if layer_idx and self.spec_id:
                color = self._get_missing_variant_color(layer_idx)
                if color:
                    return color
        except Exception:
            pass

        role_norm = (role or "").lower()
        candidates = [
            ply for ply in ((metrics or {}).get("ply_detail") or [])
            if (ply.get("role") or "").lower() == role_norm
        ]
        if role_norm == "medium" and flute:
            flute_norm = str(flute or "").strip().lower()
            flute_matches = [
                ply for ply in candidates
                if str(ply.get("flute") or ply.get("flute_code") or "").strip().lower() == flute_norm
            ]
            if flute_matches:
                candidates = flute_matches

        def _num(val):
            try:
                return float(val)
            except Exception:
                return None

        gsm_value = _num(gsm)
        bf_value = _num(bf)
        if candidates and (gsm_value is not None or bf_value is not None):
            best = None
            for ply in candidates:
                ply_gsm = _num(ply.get("gsm"))
                ply_bf = _num(ply.get("bf"))
                if gsm_value is not None and (ply_gsm is None or abs(ply_gsm - gsm_value) > 1e-6):
                    continue
                if bf_value is not None and (ply_bf is None or abs(ply_bf - bf_value) > 1e-6):
                    continue
                key = (int(ply.get("idx") or 0) or 9999)
                if best is None or key < best[0]:
                    best = (key, ply)
            if best and best[1]:
                color = (best[1].get("color") or "").strip()
                if color:
                    return color
                try:
                    idx = int(best[1].get("idx") or 0)
                except Exception:
                    idx = 0
                if idx:
                    return self._get_missing_variant_color(idx)

        return self._get_missing_variant_color(None)

    def _prepare_missing_variant_row(self, role, descriptor, gsm, bf, layer_idx, target_deckle_cm, comp=None, metrics=None, flute=None):
        if role not in ("liner", "medium"):
            return None
        try:
            gsm_value = float(gsm)
        except Exception:
            gsm_value = 0.0
        try:
            bf_value = float(bf)
        except Exception:
            bf_value = 0.0
        try:
            deckle_value = float(target_deckle_cm)
        except Exception:
            deckle_value = 0.0
        if gsm_value <= 0.0 or bf_value <= 0.0 or deckle_value <= 0.0:
            return None
        return {
            "descriptor": descriptor,
            "gsm": gsm_value,
            "bf": bf_value,
            "color": self._resolve_missing_variant_color(comp, metrics, role, gsm, bf, flute, layer_idx),
            "deckle": deckle_value,
        }

    def action_open_missing_variants(self):
        self.ensure_one()
        payload = []
        if self.missing_variant_payload:
            try:
                payload = json.loads(self.missing_variant_payload)
            except Exception:
                payload = []
        if not payload:
            self.action_prepare()
            if self.missing_variant_payload:
                try:
                    payload = json.loads(self.missing_variant_payload)
                except Exception:
                    payload = []
        if not payload:
            raise UserError(_("No missing Kraft Paper variants were found in the current preview."))

        template = self._get_kraft_template()
        line_commands = []
        seen = set()
        for row in payload:
            key = (
                self._format_kraft_numeric_value(row.get("deckle")),
                str(row.get("color") or "").strip().lower(),
                self._format_kraft_numeric_value(row.get("gsm")),
                self._format_kraft_numeric_value(row.get("bf")),
            )
            if key in seen:
                continue
            seen.add(key)
            line_commands.append((0, 0, {
                "deckle": self._format_kraft_numeric_value(row.get("deckle")),
                "color": str(row.get("color") or "").strip(),
                "gsm": self._format_kraft_numeric_value(row.get("gsm")),
                "bf": self._format_kraft_numeric_value(row.get("bf")),
            }))

        if not line_commands:
            raise UserError(_("No missing Kraft Paper variants were found in the current preview."))

        wizard = self.env["corr.missing.kraft.variant.wizard"].sudo().create({
            "product_template_id": template.id,
            "return_wizard_id": self.id,
            "line_ids": line_commands,
        })
        action = self.env.ref("corr_mrp_bridge.action_corr_missing_kraft_variant_wizard").read()[0]
        action.update({
            "name": _("Missing Variants"),
            "res_id": wizard.id,
            "target": "new",
        })
        return action

    def action_prepare(self):
        self.ensure_one()
        spec = self.spec_id
        if not (self.target_product_tmpl_id or spec.product_tmpl_id):
            raise UserError(_("Select a Finished Product (product.template) to attach the BoM."))

        result = self._load_result(spec)
        metrics = result.get("metrics") or {}
        # Choose net vs gross RM lines for preview based on wizard option
        if self.include_waste_in_bom and result.get("rm_lines_gross"):
            rm_lines = result.get("rm_lines_gross") or []
        else:
            rm_lines = result.get("rm_lines") or []

        # --- Sheet-level staging gating (BMC only, UPS-based) for preview and FG base qty target
        icp = self.env["ir.config_parameter"].sudo()
        style_val = (getattr(spec, "style", "") or "").upper()
        # Resolve printing UPS from spec extra params (UI field may be saved as printing_ups or printing_ups_ui)
        ups_val = 0
        try:
            xp_src = getattr(spec, "extra_params_json", None) or {}
            pv = xp_src.get("printing_ups") or xp_src.get("printing_ups_ui")
            if pv is not None:
                ups_val = int(float(pv or 0))
            elif (metrics.get("layout") or {}).get("printing_ups"):
                ups_val = int(float((metrics.get("layout") or {}).get("printing_ups") or 0))
        except Exception:
            ups_val = 0
        sheet_flag = (self.sfg_mode == "staged") and (icp.get_param("corr_mrp_bridge.sheet_staging_bmc", default="0") in ("1", "true", "True")) and ("BMC" in style_val) and (ups_val and ups_val > 0)

        # Populate debug fields so user sees the gating outcome before Apply
        try:
            self.update({
                "debug_style": getattr(spec, "style", "") or "",
                "debug_printing_ups": int(ups_val or 0),
                "debug_flag_enabled": icp.get_param("corr_mrp_bridge.sheet_staging_bmc", default="0") in ("1", "true", "True"),
                "debug_sheet_flag": bool(sheet_flag),
                "debug_mode_label": "Staged (BMC sheet-level)" if sheet_flag else ("Staged (per-box)" if self.sfg_mode == "staged" else (self.sfg_mode or "n/a")),
                "debug_fg_base_qty_target": float(ups_val or 1) if sheet_flag else 1.0,
                "debug_board_top_sheet_qty": 1.0 if sheet_flag else ((1.0/float(ups_val)) if ups_val else 0.0),
            })
        except Exception:
            pass

        # Build a staged debug report so we can see what the wizard will map per stage
        try:
            def _sf(v, d=0.0):
                try:
                    return float(v or d)
                except Exception:
                    return d
            engine = self.env["corr.formula.engine"].sudo().with_context(skip_corr_stack_check=True)
            ply = (metrics.get("ply_detail") or [])
            # Order layers
            layers_fg = [{
                "idx": int(_sf(p.get("idx"), 0)),
                "role": (p.get("role") or "").lower(),
                "gsm": _sf(p.get("gsm"), 0.0),
                "bf": _sf(p.get("bf"), 0.0),
                "flute": p.get("flute") or None,
                "color": p.get("color") or None,
            } for p in ply if p.get("idx")]
            layers_fg.sort(key=lambda x: x["idx"])
            try:
                stack_lines = spec.stack_line_ids.sorted(key=lambda l: l.sequence or 0)
                for layer in layers_fg:
                    idx = int(layer.get("idx") or 0)
                    if 1 <= idx <= len(stack_lines):
                        color = getattr(stack_lines[idx - 1], "color", None)
                        if color:
                            layer["color"] = color
            except Exception:
                pass

            # Mediums and adjacent liners (2-ply pairs)
            pairs = self._build_2ply_pairs_from_layers(spec, layers_fg)
            def _temp(layer_defs, include_top_sheet=False):
                Spec = self.env["corr.spec.template"].sudo().with_context(skip_corr_stack_check=True)
                tmp = Spec.create({
                    "name": "TmpDbg",
                    "product_tmpl_id": (self.target_product_tmpl_id or spec.product_tmpl_id).id,
                    "style": "SHEET",
                    "length_mm": _sf(getattr(spec, "length_mm", 0.0)),
                    "width_mm": _sf(getattr(spec, "width_mm", 0.0)),
                    "height_mm": 0.0,
                    "ply_count": len(layer_defs),
                    "stack_json": [{
                        "role": (ld.get("role") or "").lower(),
                        "gsm": _sf(ld.get("gsm"), 0.0),
                        **({"flute_code": ld.get("flute")} if (ld.get("role") == "medium" and ld.get("flute")) else {}),
                        **({"color": ld.get("color")} if (ld.get("color") not in (None, "")) else {}),
                    } for ld in layer_defs],
                    **({"top_sheet_product_id": spec.top_sheet_product_id.id} if include_top_sheet and getattr(spec, "top_sheet_product_id", False) else {}),
                    **({
                        "extra_params_json": (lambda xp: (
                            (lambda d: (d.update({}) or d))(
                                (lambda dd: (
                                    [dd.pop(k, None) for k in ("top_layer_mode","top_sheet_product_id","top_sheet_code","top_sheet_ups","printing_ups")] or dd
                                ) if not include_top_sheet else dd)(dict(xp))
                            )
                        ))(spec.extra_params_json)
                    } if getattr(spec, "extra_params_json", False) else {}),
                })
                try:
                    return engine.compute_requirements(tmp, 1.0)
                finally:
                    try:
                        tmp.unlink()
                    except Exception:
                        pass
            # Compose debug lines
            dbg = []
            # 2-ply
            for idx, (lin, med) in enumerate(pairs, start=1):
                r2 = _temp([lin, med], include_top_sheet=False)
                dbg.append(f"-- 2-ply Pair {idx}")
                for ln in (r2.get("rm_lines") or []):
                    role = (ln.get("role") or "").lower()
                    ln2 = dict(ln)
                    if role == "paper":
                        # Translate to liner/medium using ply_detail
                        try:
                            ln_idx = int(ln.get("layer_no") or 0)
                        except Exception:
                            ln_idx = 0
                        r_eff = None
                        for p in (r2.get("metrics") or {}).get("ply_detail") or []:
                            try:
                                if int(p.get("idx") or 0) == ln_idx:
                                    r_eff = (p.get("role") or "").lower()
                                    break
                            except Exception:
                                continue
                        if r_eff in ("liner", "medium"):
                            ln2["role"] = r_eff
                            role = r_eff
                    m, gsm, flute, bf = self._map_component(ln2, r2.get("metrics") or {})
                    prod = (m and m.get("product") and m["product"].display_name) or "[MISSING]"
                    qty = _sf(ln.get("qty"), 0.0)
                    dbg.append(f"  {role} -> {prod} : {qty}")
            # Board
            layers_board = [x for x in layers_fg if x["role"] in ("liner", "medium")]
            rb = _temp(layers_board, include_top_sheet=True)
            dbg.append("-- Board (pre die-cut)")
            for ln in (rb.get("rm_lines") or []):
                role = (ln.get("role") or "").lower()
                ln2 = dict(ln)
                if role == "paper":
                    try:
                        ln_idx = int(ln.get("layer_no") or 0)
                    except Exception:
                        ln_idx = 0
                    r_eff = None
                    for p in (rb.get("metrics") or {}).get("ply_detail") or []:
                        try:
                            if int(p.get("idx") or 0) == ln_idx:
                                r_eff = (p.get("role") or "").lower()
                                break
                        except Exception:
                            continue
                    if r_eff in ("liner", "medium"):
                        ln2["role"] = r_eff
                        role = r_eff
                m, gsm, flute, bf = self._map_component(ln2, rb.get("metrics") or {})
                prod = (m and m.get("product") and m["product"].display_name) or "[MISSING]"
                qty = _sf(ln.get("qty"), 0.0)
                dbg.append(f"  {role} -> {prod} : {qty}")
            self.debug_stage_text = "\n".join(dbg)
        except Exception:
            # Swallow debug errors; they don't block preview or apply
            self.debug_stage_text = ""

        missing = []
        missing_variant_rows = []
        missing_variant_keys = set()
        preview_lines = []

        def _preview_stage_has_any(*op_names):
            mapping = getattr(spec, "stage_mapping_id", False)
            if not mapping:
                return True
            wanted = {
                (name or "").strip().lower()
                for name in op_names
                if (name or "").strip()
            }
            for map_line in mapping.line_ids.filtered("enabled").sorted(
                key=lambda l: (l.sequence or 0, l.id)
            ):
                if map_line.skip_policy == "ignore" or not map_line.operation_type_id:
                    continue
                for op_type in (map_line.operation_type_id, map_line.merge_into_operation_type_id):
                    if not op_type:
                        continue
                    name = (op_type.display_name or op_type.name or "").strip().lower()
                    if name in wanted:
                        return True
            return False

        def _preview_role_required(role_name):
            role_name = (role_name or "").strip().lower()
            if role_name == "lamination":
                return _preview_stage_has_any("Lamination", "Film Lamination")
            if role_name == "ink":
                return _preview_stage_has_any("Printing")
            if role_name == "wire":
                return _preview_stage_has_any("Stitching")
            if role_name == "tape":
                return _preview_stage_has_any("Seaming", "Taping")
            return True

        target_cm = 0.0
        # Show target deckle. For non-BMC-Custom styles, derive the slitting
        # target deckle from per-box blank deckle and Slitting UPS. For
        # BMC-Custom, use liner slitting UPS as well.
        try:
            geo = (metrics.get("geometry") or {})
            deckle_mm = float(geo.get("deckle_mm") or 0.0)
            target_cm = 0.0
            style = (spec.style or "")
            xp = (spec and spec.extra_params_json) or {}
            if isinstance(xp, str):
                try:
                    xp = json.loads((xp or "{}").strip() or "{}")
                except Exception:
                    xp = {}
            if deckle_mm > 0:
                if style == "BMC-Custom":
                    su = 1
                    try:
                        su_raw = xp.get("liner_slitting_ups") or xp.get("liner_slitting_ups_ui")
                        if su_raw is not None:
                            su = int(float(su_raw)) or 1
                    except Exception:
                        su = 1
                    if su <= 0:
                        su = 1
                    target_cm = (deckle_mm / 10.0) * su
                else:
                    # Non-BMC-Custom: slitting target deckle = blank deckle × slitting UPS
                    su = 1
                    try:
                        su_raw = xp.get("liner_slitting_ups") or xp.get("liner_slitting_ups_ui")
                        if su_raw is not None:
                            su = int(float(su_raw)) or 1
                    except Exception:
                        su = 1
                    if su <= 0:
                        su = 1
                    target_cm = (deckle_mm / 10.0) * su
                preview_lines.append(f"Target Deckle: {target_cm:.2f} cm")
        except Exception:
            pass
        for line in rm_lines:
            role = line.get("role")
            if role not in ("liner", "medium", "adhesive", "ink", "lamination", "wire", "tape", "top_sheet"):
                continue
            if not _preview_role_required(role):
                continue
            mapped, gsm, flute, bf = self._map_component(line, metrics)
            qty = float(line.get("qty") or 0.0)
            qty_uom = line.get("qty_uom") or "kg"
            raw_layer = line.get("layer_no")
            try:
                layer_from_line = int(raw_layer or 0)
            except Exception:
                layer_from_line = 0
            layer_idx = layer_from_line
            if mapped and (not layer_idx):
                layer_idx = mapped.get("layer_idx") or 0
            label = self._format_layer_label(role, layer_idx)
            descriptor = label or (role.title() if role else "Component")
            if not mapped:
                missing.append(
                    f"Missing mapping for {descriptor} gsm={gsm} bf={bf} flute={flute}"
                )
                missing_variant = self._prepare_missing_variant_row(
                    role,
                    descriptor,
                    gsm,
                    bf,
                    layer_idx,
                    target_cm,
                    comp=line,
                    metrics=metrics,
                    flute=flute,
                )
                if missing_variant:
                    key = (
                        self._format_kraft_numeric_value(missing_variant.get("deckle")),
                        str(missing_variant.get("color") or "").strip().lower(),
                        self._format_kraft_numeric_value(missing_variant.get("gsm")),
                        self._format_kraft_numeric_value(missing_variant.get("bf")),
                    )
                    if key not in missing_variant_keys:
                        missing_variant_keys.add(key)
                        missing_variant_rows.append(missing_variant)
                preview_lines.append(
                    f"[MISSING] {descriptor}: {qty} {qty_uom} (gsm={gsm} bf={bf} flute={flute})"
                )
            else:
                tag = "[AUTO] " if mapped.get("auto") else ""
                preview_lines.append(
                    f"{descriptor} -> {tag}{mapped['product'].display_name}: {qty} {qty_uom}")


        # SFG preview
        if self.include_sfg_phantom:
            geo = (metrics.get("geometry") or {})
            deckle_mm = float(geo.get("deckle_mm") or 0.0)
            length_mm = float(geo.get("length_mm") or 0.0)
            sfg_name = f"Corrugated Sheet {int(round(deckle_mm))}x{int(round(length_mm))} mm"
            preview_lines.append("")
            preview_lines.append("SFG/Phantom:")
            preview_lines.append(f"SFG product: {sfg_name} (phantom BoM)")
            preview_lines.append("- Phantom includes: liners, mediums, adhesive")
            if self.include_operations:
                preview_lines.append("- Corrugation op moved to SFG phantom")
            preview_lines.append("- Final BoM will consume 1 SFG unit and keep other components (ink/lamination/wire/tape)")

        # By-products preview (Scrap)
        if self.include_scrap_byproduct:
            waste = (metrics.get("waste") or {})
            kg_per_box = float(metrics.get("kg_per_box") or 0.0)
            process_pct = float(waste.get("process_pct") or 0.0)
            scrap_kg_per_unit = kg_per_box * (process_pct / 100.0)
            preview_lines.append("")
            preview_lines.append("By-products:")
            preview_lines.append(f"Scrap (process): {scrap_kg_per_unit:.6f} kg per unit")

        # Overhead preview (snapshot only)
        if self.overhead_per_1000 and self.overhead_per_1000 > 0:
            per_unit = float(self.overhead_per_1000) / 1000.0
            preview_lines.append("")
            preview_lines.append("Overheads:")
            preview_lines.append(f"Overhead: {self.overhead_per_1000:.6f} per 1000 => {per_unit:.6f} per unit")

        self.preview = "\n".join(preview_lines)
        self.missing_mappings = "\n".join(missing)
        self.missing_variant_payload = json.dumps(missing_variant_rows)
        self.has_missing_variants = bool(missing_variant_rows)
        return {
            "type": "ir.actions.act_window",
            "res_model": "corr.bom.generate.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }

    def action_apply(self):
        self.ensure_one()
        spec = self.spec_id
        product_tmpl = self.target_product_tmpl_id or spec.product_tmpl_id
        if not product_tmpl:
            raise UserError(_("Finished Product is required to create BoM."))

        ctx = self.env.context or {}
        active_engine = (
            ctx.get("active_engine")
            or self.env["ir.config_parameter"].sudo().get_param("corr_mrp_bridge.active_engine", "old")
            or "old"
        )
        use_engine_v2 = bool(ctx.get("engine_v2_enabled") or active_engine == "v2")

        # When enabled, use Engine V2 + BoM adapter to build the BoM for
        # this FG instead of the legacy corr.formula.engine path.
        if use_engine_v2 and not (
            engine_v2_compute
            and build_engine_spec_input
            and build_plant_profile
            and engine_v2_bom_adapter
        ):
            raise UserError(_("Corrugation Engine v2 is selected but the corr_engine_v2 addon is not available."))

        if use_engine_v2:
            backend = self._get_engine_v2_general_bom_backend()
            fg_product = product_tmpl.product_variant_id
            if not fg_product:
                raise UserError(_("Finished Product is required to create BoM."))

            if backend == "stagecontext_shadow":
                plant_param = self._resolve_engine_v2_plant_param()
                if not plant_param:
                    raise UserError(
                        _(
                            "A plant is required for the StageContext-backed general BoM path.\n"
                            "Please configure a plant or switch the general BoM backend to legacy."
                        )
                    )
                order_qty = self._get_engine_v2_preview_order_qty()
                diagnostics = {}
                try:
                    bom, _payload, _preview_wiz = engine_v2_bom_adapter.create_bom_from_debug_preview(
                        self.env,
                        spec,
                        plant_param,
                        fg_product,
                        order_qty=order_qty,
                        bom_backend=backend,
                        preview_backend="stagecontext",
                        diagnostics_sink=diagnostics,
                    )
                except Exception:
                    _logger.exception(
                        "ENGINE V2 StageContext general BoM backend failed for spec %s",
                        getattr(spec, "display_name", None) or spec.id,
                    )
                    raise UserError(
                        _(
                            "StageContext-backed Engine V2 BoM creation failed.\n"
                            "Please switch the general BoM backend flag to legacy or correct the preview/runtime configuration."
                        )
                    )

                if diagnostics.get("paper_resolution_fallback_used"):
                    _logger.warning(
                        "ENGINE V2 general BoM used paper-resolution fallback for spec %s plant %s: %s",
                        getattr(spec, "display_name", None) or spec.id,
                        getattr(plant_param, "code", None) or plant_param.id,
                        diagnostics.get("paper_resolution_fallbacks"),
                    )

                return {
                    "type": "ir.actions.act_window",
                    "res_model": "mrp.bom",
                    "view_mode": "form",
                    "res_id": bom.id,
                    "target": "current",
                }

            try:
                # Build EngineSpecInput from the spec.
                spec_input = build_engine_spec_input(spec, sfg_mode=self.sfg_mode or "staged")

                # Resolve plant parameters and build PlantProfile for the engine.
                plant_param = self._resolve_engine_v2_plant_param()
                if not plant_param:
                    # Minimal fallback plant configuration.
                    class _FallbackPlantConfig:
                        def __init__(self):
                            self.stages = ["2ply", "board", "fg"]
                            self.code = "DEFAULT"
                            self.name = "Default Plant"

                    plant_profile = build_plant_profile(_FallbackPlantConfig())
                else:
                    plant_profile = build_plant_profile(plant_param)

                # Run Engine V2.
                engine_result = engine_v2_compute(spec_input, plant_profile)

                # Use the BoM adapter to create/update the BoM for the FG.
                bom = engine_v2_bom_adapter.build_bom_from_engine(self.env, engine_result, fg_product)

                return {
                    "type": "ir.actions.act_window",
                    "res_model": "mrp.bom",
                    "view_mode": "form",
                    "res_id": bom.id,
                    "target": "current",
                }
            except (UserError, ValidationError) as exc:
                raise exc
            except Exception as exc:
                if MissingStyleParameterError and isinstance(exc, MissingStyleParameterError):
                    raise UserError(str(exc)) from exc
                _logger.exception("ENGINE V2 BoM adapter failed; falling back to legacy path.")

         # Debug hook: run engine_v2 without affecting BoM creation.
        if ctx.get("engine_v2_debug") and engine_v2_compute and spec:
            try:
                # Minimal plant profile stub for the current prototype:
                class _DebugPlantProfile:
                    def __init__(self):
                        self.stages = ["2ply", "board"]
                        self.code = "debug"
                        self.name = "Debug Profile"

                plant_profile = _DebugPlantProfile()
                # Use the spec adapter when available so that EngineSpecInput
                # receives board_layers and top_sheet_gsm derived from the spec.
                if build_engine_spec_input:
                    spec_input = build_engine_spec_input(spec, sfg_mode=self.sfg_mode or "staged")
                else:
                    spec_input = spec
                result = engine_v2_compute(spec_input, plant_profile)

                # Stages with attached process operations
                stage_lines = []
                for s in result.stage_graph.stages:
                    if s.operations:
                        ops = ", ".join(s.operations)
                        stage_lines.append(f"{s.key} ({ops})")
                    else:
                        stage_lines.append(s.key)
                stages_desc = "\n".join(stage_lines) or "(none)"

                # Flows in readable format
                flow_lines = [
                    f"{e.from_stage or 'RM'} -> {e.to_stage} [{e.product_key}]"
                    for e in result.stage_graph.flows
                ]
                flows_desc = "\n".join(flow_lines) or "(none)"

                metrics = result.metrics or {}

                # RM requirements, grouped by role for readability
                rm_lines = []
                for rm in result.rm_requirements:
                    rm_lines.append(f"{rm.role}: {rm.qty_per_fg:.6f} {rm.qty_uom} ({rm.product_key})")
                rm_desc = "\n".join(rm_lines) or "(none)"

                _logger.info(
                    "ENGINE V2 DEBUG\n"
                    "Spec: %s (%s)\n"
                    "Stages:\n%s\n"
                    "Flows:\n%s\n"
                    "Metrics: m2_per_box=%.6f, kg_per_box=%.6f\n"
                    "RM:\n%s",
                    getattr(spec, "name", "") or spec.id,
                    getattr(spec, "style", "") or "",
                    stages_desc,
                    flows_desc,
                    getattr(metrics, "m2_per_box", 0.0),
                    getattr(metrics, "kg_per_box", 0.0),
                    rm_desc,
                )
            except Exception:
                _logger.exception("ENGINE V2 DEBUG: compute() failed for spec %s", spec.id)

        result = self._load_result(spec)
        metrics = result.get("metrics") or {}
        # Choose net vs gross RM lines for BoM based on wizard option
        if self.include_waste_in_bom and result.get("rm_lines_gross"):
            rm_lines = result.get("rm_lines_gross") or []
        else:
            rm_lines = result.get("rm_lines") or []

        # Include overhead info in result snapshot (no BoM lines)
        if self.overhead_per_1000 and self.overhead_per_1000 > 0:
            result = dict(result)  # shallow copy to avoid mutating spec snapshot accidentally
            result["bridge_overhead"] = {
                "mode": "per_1000",
                "per_1000": float(self.overhead_per_1000),
                "per_unit": float(self.overhead_per_1000) / 1000.0,
            }

        # Resolve effective SFG mode. For SHEET/PAD, always treat as No SFG
        # so that we build a single-stage BoM (corrugation only).
        mode = (self.sfg_mode or ("phantom" if self.include_sfg_phantom else "none"))
        try:
            style_val = (spec.style or "").upper()
        except Exception:
            style_val = ""
        if style_val in {"SHEET", "PAD"}:
            mode = "none"
            self.sfg_mode = "none"

        # If staged SFG mode is selected, run the staged path
        if mode == "staged":
            # Always let the staged engine build the canonical SFG structure
            # first. When a Stage Mapping is selected on the Spec Template,
            # apply a structural overlay on top of that output without
            # touching engine logic (component splitting, N-ply, SFG naming).
            action = self._action_apply_staged(spec, product_tmpl, result)
            stage_mapping = getattr(spec, "stage_mapping_id", False)
            if stage_mapping:
                self._apply_stage_mapping_overlay(spec, product_tmpl, result, stage_mapping)
            self._normalize_diecut_sheet_staged_yield_boms(spec)
            return action

        # Check mappings again and split into final vs SFG components (legacy/phantom/none)
        missing = []
        comps_final = []
        comps_sfg = []
        sfg_roles = {"liner", "medium", "adhesive"}
        roll_top_mode = False
        roll_top_layer_no = 0
        try:
            xp = getattr(spec, "extra_params_json", None) or {}
            if isinstance(xp, str):
                try:
                    xp = json.loads((xp or "{}").strip())
                except Exception:
                    xp = {}
            raw_top_mode = xp.get("top_layer_mode")
            top_mode = str(raw_top_mode).strip().lower() if raw_top_mode is not None else ""
            roll_top_mode = ("roll" in top_mode)
        except Exception:
            roll_top_mode = False
        if roll_top_mode:
            try:
                liners = [p for p in (metrics.get("ply_detail") or []) if (p.get("role") or "").lower() == "liner" and p.get("idx")]
                if liners:
                    roll_top_layer_no = max(int(p.get("idx") or 0) for p in liners)
            except Exception:
                roll_top_layer_no = 0
        for line in rm_lines:
            role = (line.get("role") or "").lower()
            if role not in ("liner", "medium", "adhesive", "ink", "lamination", "wire", "tape", "top_sheet"):
                continue
            mapped, gsm, flute, bf = self._map_component(line, metrics)
            if not mapped:
                missing.append(
                    f"Missing mapping for {role} layer={line.get('layer_no')} gsm={gsm} bf={bf} flute={flute}"
                )
                continue
            qty = float(line.get("qty") or 0.0)
            qty_uom = line.get("qty_uom") or "kg"
            raw_layer = line.get("layer_no")
            try:
                layer_from_line = int(raw_layer or 0)
            except Exception:
                layer_from_line = 0
            layer_idx = layer_from_line
            if mapped and not layer_idx:
                layer_idx = mapped.get("layer_idx") or 0
            prod = mapped["product"]
            uom = mapped["uom"]
            split_roll_top = None
            if role == "liner" and roll_top_mode and roll_top_layer_no:
                split_roll_top = self._split_liner_component_for_bom(line, metrics, mapped, bf, roll_top_layer_no)
                if not split_roll_top:
                    layer_idx = self._resolve_roll_top_layer_idx(line, metrics, mapped, bf, roll_top_layer_no)
            elif role == "liner":
                split_roll_top = self._split_liner_component_for_bom(line, metrics, mapped, bf, 0)
            elif role == "medium":
                split_roll_top = self._split_medium_component_for_bom(line, metrics, mapped, bf, roll_top_layer_no if roll_top_mode else 0)

            recs_to_add = []
            if split_roll_top:
                for split_row in split_roll_top:
                    corr_role = split_row["corr_role"]
                    layer_no = split_row["layer_no"]
                    split_product = prod
                    split_uom = uom
                    split_flute = mapped["flute"]
                    split_gsm = mapped["gsm"]
                    if role in ("liner", "medium") and layer_no:
                        split_hint = dict(line)
                        split_hint["role"] = role
                        split_hint["layer_no"] = layer_no
                        split_mapped, _split_gsm, _split_flute, _split_bf = self._map_component(split_hint, metrics)
                        if split_mapped:
                            split_product = split_mapped["product"]
                            split_uom = split_mapped["uom"]
                            split_flute = split_mapped["flute"]
                            split_gsm = split_mapped["gsm"]
                    recs_to_add.append({
                        "role": role,
                        "corr_role": corr_role,
                        "layer_no": layer_no,
                        "flute": split_flute,
                        "gsm": split_gsm,
                        "product": split_product,
                        "uom": split_uom,
                        "qty": split_row["qty"],
                        "qty_uom": qty_uom,
                        "sequence": self._component_sequence(corr_role, layer_no),
                        "layer_val": layer_no,
                    })
            else:
                sequence = self._component_sequence(role, layer_idx)
                layer_val = layer_idx
                corr_role = role
                if role == "liner" and roll_top_mode and roll_top_layer_no and layer_idx == roll_top_layer_no:
                    corr_role = "top_sheet"
                recs_to_add.append({
                    "role": role,
                    "corr_role": corr_role,
                    "layer_no": layer_idx,
                    "flute": mapped["flute"],
                    "gsm": mapped["gsm"],
                    "product": prod,
                    "uom": uom,
                    "qty": qty,
                    "qty_uom": qty_uom,
                    "sequence": sequence,
                    "layer_val": layer_val,
                })

            for rec in recs_to_add:
                if self.include_sfg_phantom and role in sfg_roles:
                    comps_sfg.append(rec)
                else:
                    comps_final.append(rec)
        if missing:
            raise UserError(_("Missing mappings:\n%s") % "\n".join(missing))

        # Upsert BoM
        Bom = self.env["mrp.bom"]
        existing = Bom.search([("corr_spec_id", "=", spec.id)], limit=1)
        if not existing:
            existing = Bom.with_context(skip_corr_bom_qty_sync=True).create({
                "product_tmpl_id": product_tmpl.id,
                # In non-staged modes, keep FG base quantity at 1.0
                "product_qty": 1.0,
                "type": "normal",
                "corr_spec_id": spec.id,
                "corr_signature": result.get("signature"),
                "corr_result_snapshot": json.dumps(result, indent=2),
                "corr_use_gross_qty": bool(self.include_waste_in_bom),
                "consumption": "warning",
            })
        else:
            existing.with_context(skip_corr_bom_qty_sync=True).write({
                "product_tmpl_id": product_tmpl.id,
                "product_qty": 1.0,
                "corr_signature": result.get("signature"),
                "corr_result_snapshot": json.dumps(result, indent=2),
                "corr_use_gross_qty": bool(self.include_waste_in_bom),
                "consumption": "warning",
            })

        # Build keys and upsert component lines (Final BoM)
        keep_keys = set()
        for c in comps_final:
            prod = c["product"]
            key = self._component_key(c["role"], c.get("layer_no"), prod.id)
            keep_keys.add(key)
            line = existing.bom_line_ids.filtered(lambda l: l.corr_component_key == key)
            vals = {
                "product_id": prod.id,
                "product_qty": c["qty"],
                "corr_base_qty": c["qty"],
                "product_uom_id": c["uom"].id,
                "corr_component_key": key,
                "corr_role": c.get("corr_role", c["role"]),
                "corr_flute_code": c.get("flute"),
                "corr_gsm": c.get("gsm") or 0.0,
                "corr_layer_no": c.get("layer_val", 0),
                "sequence": c.get("sequence"),
            }
            if line:
                line.write(vals)
            else:
                vals["bom_id"] = existing.id
                self.env["mrp.bom.line"].create(vals)

        # If SFG is enabled, ensure an SFG component line exists on the final BoM
        sfg_prod = None
        if self.include_sfg_phantom:
            sfg_prod = self._get_or_create_sfg_product(metrics)
            sfg_key = self._component_key("sfg", 0, sfg_prod.id)
            keep_keys.add(sfg_key)
            sfg_line = existing.bom_line_ids.filtered(lambda l: l.corr_component_key == sfg_key)
            vals = {
                "product_id": sfg_prod.id,
                "product_qty": 1.0,
                "corr_base_qty": 1.0,
                "product_uom_id": sfg_prod.uom_id.id,
                "corr_component_key": sfg_key,
            }
            if sfg_line:
                sfg_line.write(vals)
            else:
                vals["bom_id"] = existing.id
                self.env["mrp.bom.line"].create(vals)

        if self.remove_obsolete:
            to_unlink = existing.bom_line_ids.filtered(lambda l: l.corr_component_key and l.corr_component_key not in keep_keys)
            # If BoM locks manual lines, do not remove lines without key or marked manual
            if existing.lock_manual_lines:
                to_unlink = to_unlink.filtered(lambda l: l.corr_manual is False)
            to_unlink.unlink()

        # Upsert BoM operations if requested
        if self.include_operations:
            ops_final = list(result.get("ops") or [])
            flat_primary = False
            # When SFG phantom is enabled, Corrugation moves to SFG; drop from FG ops
            if self.include_sfg_phantom:
                ops_final = [o for o in ops_final if (o or "") != "Corrugation"]

            placeholder_only = False
            try:
                style_val = (spec.style or "").upper()
            except Exception:
                style_val = ""

            # Flat FG with single work order: collapse to a single operation
            if mode == "flat_single_wo":
                primary = None
                for o in ops_final:
                    name = (o or "").strip()
                    if name:
                        primary = name
                        break
                if not primary:
                    primary = "Corrugation"
                flat_primary = primary
                ops_final = [primary]

            # For SHEET/PAD with no SFG mode, keep a single Corrugation operation.
            # Slitting/Cutting and QC are not needed as separate stages in this case.
            if mode == "none" and style_val in {"SHEET", "PAD"}:
                ops_final = [o for o in ops_final if (o or "").strip().lower() == "corrugation"]
                placeholder_only = True

            # Append any extra finishing operations configured on the Spec Template
            try:
                extra_ops = []
                spec_extra = getattr(spec, "corr_extra_finishing_operation_ids", False)
                if spec_extra:
                    spec_extra = spec_extra.sorted(key=lambda o: o.sequence or 0)
                    for op_type in spec_extra:
                        label = (op_type.code or op_type.name or "").strip()
                        if not label:
                            continue
                        extra_ops.append(label)
                if extra_ops:
                    seen = { (o or "").strip().lower() for o in ops_final }
                    for label in extra_ops:
                        key = (label or "").strip().lower()
                        if key and key not in seen:
                            ops_final.append(label)
                            seen.add(key)
            except Exception:
                # Finishing ops are optional; never block BoM creation
                pass
            # Reuse unified mapping/normalization and cleanup logic
            self._upsert_ops(existing, ops_final, metrics=metrics, placeholder_only=placeholder_only)

            # Flat FG (Single Work Order): all auto-generated corrugation
            # components should be consumed in the collapsed primary operation.
            # Keep this scoped to flat_single_wo so other modes preserve their
            # existing per-stage/per-role linking behavior.
            if mode == "flat_single_wo":
                target_op = self.env["mrp.routing.workcenter"]
                if flat_primary:
                    target_op = existing.operation_ids.filtered(
                        lambda o, n=flat_primary: (o.name or "") == n
                    )[:1]
                if not target_op:
                    target_op = existing.operation_ids[:1]
                if target_op:
                    for bl in existing.bom_line_ids:
                        if bl.corr_component_key:
                            bl.operation_id = target_op.id

            # For SHEET/PAD with no SFG, consume board and adhesive components in Corrugation.
            if mode == "none" and style_val in {"SHEET", "PAD"}:
                try:
                    corr_op = existing.operation_ids.filtered(lambda o: (o.name or "").lower() == "corrugation")[:1]
                except Exception:
                    corr_op = self.env["mrp.routing.workcenter"]
                if corr_op:
                    for bl in existing.bom_line_ids:
                        role = (getattr(bl, "corr_role", "") or "").lower()
                        key = (bl.corr_component_key or "") or ""
                        if role in {"liner", "medium", "adhesive"} or key.startswith("adhesive:"):
                            bl.operation_id = corr_op.id

        # Upsert By-product (Scrap) if requested
        if self.include_scrap_byproduct:
            waste = (metrics.get("waste") or {})
            kg_per_box = float(metrics.get("kg_per_box") or 0.0)
            process_pct = float(waste.get("process_pct") or 0.0)
            scrap_kg_per_unit = kg_per_box * (process_pct / 100.0)

            # Skip if negligible or zero
            if scrap_kg_per_unit > 0:
                # Resolve/create scrap product and its UoM (kg)
                scrap_product = self._get_or_create_scrap_product()

                # Link to Corrugation operation if present
                op_link = False
                corr_ops = existing.operation_ids.filtered(lambda o: (o.name or "").lower() == "corrugation")
                if corr_ops:
                    op_link = corr_ops[:1].id

                byp = existing.byproduct_ids.filtered(lambda b: b.product_id.id == scrap_product.id)[:1]
                scrap_qty, scrap_uom = self._corr_qty_uom_preserving_small_weight(
                    scrap_kg_per_unit,
                    scrap_product.uom_id,
                )
                vals = {
                    "bom_id": existing.id,
                    "product_id": scrap_product.id,
                    "product_qty": scrap_qty,
                    "product_uom_id": scrap_uom.id,
                    "operation_id": op_link,
                    "sequence": 10,
                    "cost_share": 0.0,
                }
                if byp:
                    byp.write(vals)
                else:
                    self.env["mrp.bom.byproduct"].create(vals)

        # Create/Update SFG phantom BoM
        if self.include_sfg_phantom and sfg_prod:
            sfg_bom = self.env["mrp.bom"].search([
                ("product_tmpl_id", "=", sfg_prod.product_tmpl_id.id),
                ("type", "=", "phantom"),
            ], limit=1)
            if not sfg_bom:
                sfg_bom = self.env["mrp.bom"].create({
                    "product_tmpl_id": sfg_prod.product_tmpl_id.id,
                    "product_qty": 1.0,
                    "type": "phantom",
                    "corr_spec_id": spec.id,
                    "corr_signature": result.get("signature"),
                    "corr_result_snapshot": json.dumps(result, indent=2),
                    "consumption": "warning",
                })
            else:
                sfg_bom.write({
                    "corr_spec_id": spec.id,
                    "corr_signature": result.get("signature"),
                    "corr_result_snapshot": json.dumps(result, indent=2),
                    "consumption": "warning",
                })

            # Upsert SFG component lines (liners/mediums/adhesive only)
            keep_sfg = set()
            for c in comps_sfg:
                prod = c["product"]
                key = self._component_key(c["role"], c.get("layer_no"), prod.id)
                keep_sfg.add(key)
                line = sfg_bom.bom_line_ids.filtered(lambda l: l.corr_component_key == key)
                vals = {
                    "product_id": prod.id,
                    "product_qty": c["qty"],
                    "product_uom_id": c["uom"].id,
                    "corr_component_key": key,
                    "corr_role": c.get("corr_role", c["role"]),
                    "corr_flute_code": c.get("flute"),
                    "corr_gsm": c.get("gsm") or 0.0,
                    "corr_layer_no": c.get("layer_val", 0),
                    "sequence": c.get("sequence"),
                    "bom_id": sfg_bom.id,
                }
                if line:
                    line.write(vals)
                else:
                    self.env["mrp.bom.line"].create(vals)
            if self.remove_obsolete:
                to_unlink = sfg_bom.bom_line_ids.filtered(lambda l: l.corr_component_key and l.corr_component_key not in keep_sfg)
                to_unlink.unlink()

            # SFG operations: only Corrugation if requested
            if self.include_operations:
                opmap = self.env["corr.operation.map"].search([("active", "=", True)])
                desired = []
                corr_ops = [o for o in (result.get("ops") or []) if (o or "") == "Corrugation"]
                for idx, op in enumerate(corr_ops, start=1):
                    m = opmap.filtered(lambda r, n=op: r.op_name == n)[:1]
                    if not m or not m.workcenter_id:
                        continue
                    run_min = self._compute_runtime_from_map(m, metrics)
                    desired.append({
                        "name": op,
                        "sequence": m.sequence or (idx * 10),
                        "workcenter_id": m.workcenter_id.id,
                        "time_mode": "manual",
                        "time_cycle_manual": run_min,
                    })
                existing_ops = sfg_bom.operation_ids
                keep_names = set()
                for d in desired:
                    keep_names.add(d["name"])
                    op_line = existing_ops.filtered(lambda o, n=d["name"]: o.name == n)[:1]
                    vals = d.copy()
                    vals["bom_id"] = sfg_bom.id
                    if op_line:
                        op_line.write(vals)
                    else:
                        self.env["mrp.routing.workcenter"].create(vals)
                obsolete_ops = existing_ops.filtered(lambda o: o.name not in keep_names)
                _archive_operations(obsolete_ops)

        # Open BoM
        return {
            "type": "ir.actions.act_window",
            "res_model": "mrp.bom",
            "view_mode": "form",
            "res_id": existing.id,
            "target": "current",
        }

    # ------------------------
    # Staged SFG implementation
    # ------------------------

    def apply_stage_mapping(self, stage_boms, mapped_stages, fg_bom):
        """Apply Stage Mapping as a pure routing overlay.

        This helper ONLY rewires SFG dependencies between existing stage BoMs
        and the FG BoM. It never modifies raw-material components, never
        creates new SFG products, and never regenerates BoMs.

        :param stage_boms: dict(stage_key -> mrp.bom recordset)
        :param mapped_stages: ordered list of stage keys selected by mapping
        :param fg_bom: mrp.bom for the finished product
        """
        import logging
        _logger = logging.getLogger(__name__)

        self.ensure_one()
        BomLine = self.env["mrp.bom.line"].sudo()

        # 0) Sanity and basic debug info
        if not fg_bom or not fg_bom.exists():
            _logger.warning("Stage Mapping overlay: FG BoM not found, skipping.")
            return

        mapped_stages = [k for k in (mapped_stages or []) if k in stage_boms]
        if not mapped_stages:
            _logger.info("Stage Mapping overlay: no mapped stages with BoMs, skipping.")
            return

        _logger.info(
            "Stage Mapping overlay: mapped chain = %s",
            " -> ".join(mapped_stages),
        )

        # Cache existing stage_sfg quantities so rewiring can preserve
        # engine‑calculated ratios whenever possible.
        # Primary key: (bom_id, product_id) -> qty
        qty_cache = {}
        # Secondary index: product_id -> set((bom_id, qty)) so that when we
        # create a *new* stage link for an SFG product, we can still reuse any
        # previously computed ratio for that SFG instead of defaulting to 1.0.
        qty_by_product = {}
        for boms in list(stage_boms.values()) + [[fg_bom]]:
            for bom in (boms or self.env["mrp.bom"]):
                if not bom or not bom.exists():
                    continue
                for line in bom.bom_line_ids:
                    # Always remember at least one ratio per SFG product so we
                    # can reuse it later when rewiring, even if the original
                    # line was not tagged as stage_sfg* (e.g. plain SFG line on
                    # the FG BoM).
                    qty = line.product_qty or 1.0
                    qty_by_product.setdefault(line.product_id.id, set()).add((bom.id, qty))
                    # Only cache keyed by (bom_id, product_id) for explicit
                    # stage_sfg* links, since those are the ones we overwrite.
                    if (line.corr_component_key or "").startswith("stage_sfg"):
                        key = (bom.id, line.product_id.id)
                        qty_cache[key] = qty

        # 1) Disable skipped stages (routing only)
        all_keys = list(stage_boms.keys())
        skipped = [k for k in all_keys if k not in mapped_stages]
        for key in skipped:
            boms = stage_boms.get(key) or self.env["mrp.bom"]
            for bom in boms:
                if not bom.exists():
                    continue
                if bom.active:
                    _logger.debug("Disabling skipped stage BoM: %s (%s)", key, bom.display_name)
                    bom.active = False

        # Collect SFG variant ids for skipped stages so we can drop any
        # stage_sfg:* references to them from the mapped chain and FG BoM.
        skipped_sfg_ids = set()
        for key in skipped:
            boms = stage_boms.get(key) or self.env["mrp.bom"]
            for bom in boms:
                if not bom or not bom.exists():
                    continue
                prod = bom.product_tmpl_id.product_variant_id
                if prod:
                    skipped_sfg_ids.add(prod.id)

        def _remove_skipped_stage_refs(bom):
            """Remove stage references that point to skipped stage SFGs.

            - Drops any stage_sfg* or stage_sfg_final* lines whose product is a
              skipped-stage SFG.
            - Also drops direct SFG components whose product is a skipped-stage
              SFG (product_role == 'sfg').

            Raw-material lines and non-SFG components remain untouched.
            """
            if not bom or not bom.exists() or not skipped_sfg_ids:
                return
            lines = bom.bom_line_ids.filtered(
                lambda l: (
                    l.product_id.id in skipped_sfg_ids
                    and (
                        (l.corr_component_key or "").startswith("stage_sfg")
                        or getattr(l.product_id, "product_role", "") == "sfg"
                    )
                )
            )
            if lines and getattr(bom, "lock_manual_lines", False):
                lines = lines.filtered(lambda l: not getattr(l, "corr_manual", False))
            if lines:
                _logger.debug(
                    "Removing %d skipped stage_sfg* refs from BoM %s",
                    len(lines),
                    bom.display_name,
                )
                lines.unlink()

        # 2) Helper to ensure a single stage_sfg:* input on a BoM
        def _set_stage_input(bom, sfg_prod):
            """Ensure bom has exactly one stage_sfg:* line pointing to sfg_prod."""
            if not bom or not bom.exists() or not sfg_prod:
                return

            # Remove existing stage_sfg:* lines; keep RM lines intact.
            lines = bom.bom_line_ids.filtered(
                lambda l: (l.corr_component_key or "").startswith("stage_sfg:")
            )
            if lines:
                if getattr(bom, "lock_manual_lines", False):
                    lines = lines.filtered(lambda l: not getattr(l, "corr_manual", False))
                if lines:
                    _logger.debug(
                        "Removing %d old stage_sfg lines from BoM %s",
                        len(lines),
                        bom.display_name,
                    )
                    lines.unlink()

            key = f"stage_sfg:{sfg_prod.id}"
            existing = bom.bom_line_ids.filtered(
                lambda l, k=key: (l.corr_component_key or "") == k
            )[:1]
            # Prefer exact cached quantity for this (BoM, SFG) pair; if not
            # available, reuse any existing ratio for this SFG from another
            # BoM before falling back to 1.0. This keeps Stage Mapping as a
            # pure routing overlay without inflating quantities.
            qty = qty_cache.get((bom.id, sfg_prod.id))
            if qty is None:
                candidates = list(qty_by_product.get(sfg_prod.id, []))
                if candidates:
                    # Pick the first known ratio for this SFG.
                    _any_bom_id, qty = candidates[0]
                else:
                    qty = 1.0
            try:
                bom_stage = (getattr(bom, "corr_stage", "") or "").strip().lower()
                sfg_stage = (getattr(sfg_prod.product_tmpl_id, "corr_stage", "") or "").strip().lower()
                if bom_stage == "board" and sfg_stage in {"2-ply", "2ply"}:
                    qty = 1.0
            except Exception:
                pass
            vals = {
                "bom_id": bom.id,
                "product_id": sfg_prod.id,
                "product_qty": qty,
                "product_uom_id": sfg_prod.uom_id.id,
                "corr_component_key": key,
                "sequence": 10,
            }
            if existing:
                existing.write(vals)
            else:
                BomLine.create(vals)

        # 3) Reconnect dependencies in mapped order
        prev_key = None
        for key in mapped_stages:
            boms = stage_boms.get(key) or self.env["mrp.bom"]
            if not boms:
                _logger.debug("Stage %s has no BoMs, skipping.", key)
                prev_key = key
                continue

            # Reactivate mapped stage BoMs
            for bom in boms:
                if bom.exists() and not bom.active:
                    _logger.debug("Re-enabling mapped stage BoM: %s (%s)", key, bom.display_name)
                    bom.active = True

            if prev_key:
                # Current stage consumes previous stage's SFG
                prev_boms = stage_boms.get(prev_key) or self.env["mrp.bom"]
                prev_bom = prev_boms[:1]
                if not prev_bom:
                    _logger.warning(
                        "Stage Mapping overlay: previous stage %s has no BoM; "
                        "cannot wire %s correctly.",
                        prev_key,
                        key,
                    )
                else:
                    sfg_prod = prev_bom.product_tmpl_id.product_variant_id
                    _logger.debug(
                        "Wiring stage %s to consume SFG from %s (%s)",
                        key,
                        prev_key,
                        sfg_prod.display_name,
                    )
                    for bom in boms:
                        _remove_skipped_stage_refs(bom)
                        _set_stage_input(bom, sfg_prod)
            else:
                # First mapped stage: strip any upstream stage_sfg:* dependency.
                for bom in boms:
                    lines = bom.bom_line_ids.filtered(
                        lambda l: (l.corr_component_key or "").startswith("stage_sfg:")
                    )
                    if lines:
                        if getattr(bom, "lock_manual_lines", False):
                            lines = lines.filtered(lambda l: not getattr(l, "corr_manual", False))
                        if lines:
                            _logger.debug(
                                "First stage %s: removing %d upstream stage_sfg lines from %s",
                                key,
                                len(lines),
                                bom.display_name,
                            )
                            lines.unlink()
                        _remove_skipped_stage_refs(bom)

            prev_key = key

        # 4) FG must consume final mapped stage SFG
        final_key = mapped_stages[-1]
        final_boms = stage_boms.get(final_key) or self.env["mrp.bom"]
        final_bom = final_boms[:1]
        if not final_bom:
            _logger.warning(
                "Stage Mapping overlay: final stage %s has no BoM; FG wiring skipped.",
                final_key,
            )
            return

        final_sfg = final_bom.product_tmpl_id.product_variant_id
        fg_key = f"stage_sfg_final:{final_sfg.id}"

        # Drop any final-stage refs that point to skipped SFGs, then any old
        # stage_sfg_final:* line so we can insert the new one.
        _remove_skipped_stage_refs(fg_bom)
        fg_lines = fg_bom.bom_line_ids.filtered(
            lambda l: (l.corr_component_key or "").startswith("stage_sfg_final:")
        )
        if fg_lines:
            if getattr(fg_bom, "lock_manual_lines", False):
                fg_lines = fg_lines.filtered(lambda l: not getattr(l, "corr_manual", False))
            if fg_lines:
                _logger.debug(
                    "Removing %d old stage_sfg_final lines from FG BoM %s",
                    len(fg_lines),
                    fg_bom.display_name,
                )
                fg_lines.unlink()

        # Also drop any plain SFG lines for the same final SFG product to avoid
        # duplicate final-stage rows on FG when the engine already added one.
        plain_sfg_lines = fg_bom.bom_line_ids.filtered(
            lambda l, pid=final_sfg.id: (
                l.product_id.id == pid
                and not (l.corr_component_key or "").startswith("stage_sfg_final:")
                and not (l.corr_component_key or "").startswith("stage_sfg:")
            )
        )
        if plain_sfg_lines and getattr(fg_bom, "lock_manual_lines", False):
            plain_sfg_lines = plain_sfg_lines.filtered(lambda l: not getattr(l, "corr_manual", False))
        if plain_sfg_lines:
            _logger.debug(
                "Removing %d plain final-stage SFG lines from FG BoM %s",
                len(plain_sfg_lines),
                fg_bom.display_name,
            )
            plain_sfg_lines.unlink()

        fg_existing = fg_bom.bom_line_ids.filtered(
            lambda l, k=fg_key: (l.corr_component_key or "") == k
        )[:1]
        # Same quantity policy as for intermediate stages: reuse an existing
        # FG->SFG ratio whenever possible so Stage Mapping does not change
        # required quantities, only routing.
        qty_fg = qty_cache.get((fg_bom.id, final_sfg.id))
        if qty_fg is None:
            candidates_fg = list(qty_by_product.get(final_sfg.id, []))
            if candidates_fg:
                _any_bom_id, qty_fg = candidates_fg[0]
            else:
                qty_fg = 1.0
        fg_vals = {
            "bom_id": fg_bom.id,
            "product_id": final_sfg.id,
            "product_qty": qty_fg,
            "product_uom_id": final_sfg.uom_id.id,
            "corr_component_key": fg_key,
            "sequence": 10,
        }
        if fg_existing:
            fg_existing.write(fg_vals)
        else:
            BomLine.create(fg_vals)

        _logger.info(
            "Stage Mapping overlay applied. Final chain: %s. FG now consumes %s.",
            " -> ".join(mapped_stages),
            final_sfg.display_name,
        )

    def fix_stage_dependencies(self, stage_boms, mapped_stages):
        """Fix stage_sfg dependencies according to Stage Mapping.

        Engine responsibilities (SFG creation, RM lines, N‑ply logic, etc.)
        remain untouched. This helper only adjusts stage_sfg* lines and
        enables/disables stage BoMs.

        :param stage_boms: dict(stage_key -> mrp.bom recordset)
                           e.g. {"ts_cut": bom_cut, "ts_print": bom_print, ...}
        :param mapped_stages: ordered list of stage keys from mapping
                              e.g. ["ts_cut", "ts_print", "2ply", "board", "seaming"]
        """
        import logging
        _logger = logging.getLogger(__name__)

        self.ensure_one()
        BomLine = self.env["mrp.bom.line"].sudo()

        if not stage_boms:
            _logger.info("fix_stage_dependencies: no stage BoMs provided.")
            return

        mapped_stages = mapped_stages or []
        _logger.info(
            "fix_stage_dependencies: mapped stages = %s",
            " -> ".join(mapped_stages),
        )

        # 1) Disable skipped stages (routing only)
        for key, boms in stage_boms.items():
            if key not in mapped_stages:
                for bom in (boms or self.env["mrp.bom"]):
                    if bom.exists() and bom.active:
                        _logger.debug(
                            "Disabling skipped stage %s BoM: %s",
                            key,
                            bom.display_name,
                        )
                        bom.active = False

        # Convenience: first BoM per canonical key
        def _first_bom(key):
            boms = stage_boms.get(key) or self.env["mrp.bom"]
            return boms[:1] if boms else self.env["mrp.bom"]

        bom_ts_cut = _first_bom("ts_cut")
        bom_ts_print = _first_bom("ts_print")
        bom_2ply = _first_bom("2ply")
        bom_board = _first_bom("board")

        # Build a lightweight cache of existing stage_sfg ratios so we can
        # reuse engine‑calculated quantities when adding dependencies here.
        qty_by_product = {}
        for boms in stage_boms.values():
            for bom in (boms or self.env["mrp.bom"]):
                if not bom or not bom.exists():
                    continue
                for line in bom.bom_line_ids:
                    qty = line.product_qty or 1.0
                    qty_by_product.setdefault(line.product_id.id, set()).add((bom.id, qty))

        # Helper: remove ONLY stage_sfg* lines (do not touch RM components)
        def _clear_stage_sfg_lines(bom):
            if not bom or not bom.exists():
                return
            lines = bom.bom_line_ids.filtered(
                lambda l: (getattr(l, "corr_component_key", "") or "").startswith("stage_sfg")
            )
            if not lines:
                return
            if getattr(bom, "lock_manual_lines", False):
                lines = lines.filtered(lambda l: not getattr(l, "corr_manual", False))
            if lines:
                _logger.debug(
                    "Removing %d stage_sfg* lines from BoM %s",
                    len(lines),
                    bom.display_name,
                )
                lines.unlink()

        # Helper: add a stage_sfg line using the given SFG product.
        def _add_stage_sfg_line(bom, sfg_prod, sequence):
            if not bom or not bom.exists() or not sfg_prod:
                return
            _logger.debug(
                "Adding stage_sfg dependency on BoM %s (product %s, seq=%s)",
                bom.display_name,
                sfg_prod.display_name,
                sequence,
            )
            # Reuse any existing quantity ratio for this SFG when wiring
            # Board/2‑ply/top‑sheet dependencies; fall back to 1.0 only if
            # nothing is known for this product.
            qty = 1.0
            candidates = list(qty_by_product.get(sfg_prod.id, []))
            if candidates:
                _any_bom_id, qty = candidates[0]
            try:
                bom_stage = (getattr(bom, "corr_stage", "") or "").strip().lower()
                sfg_stage = (getattr(sfg_prod.product_tmpl_id, "corr_stage", "") or "").strip().lower()
                if bom_stage == "board" and sfg_stage in {"2-ply", "2ply"}:
                    qty = 1.0
            except Exception:
                pass
            BomLine.create({
                "bom_id": bom.id,
                "product_id": sfg_prod.id,
                "product_qty": qty,
                "product_uom_id": sfg_prod.uom_id.id,
                "sequence": sequence,
            })

        # 2) Corrugation (2ply) must NOT consume printed sheet
        if bom_2ply:
            _logger.info(
                "fix_stage_dependencies: stripping stage_sfg inputs from 2-ply BoM %s",
                bom_2ply.display_name,
            )
            _clear_stage_sfg_lines(bom_2ply)

        # 3) Board must consume 2-ply + printed top sheet
        if bom_board:
            _logger.info(
                "fix_stage_dependencies: fixing Board dependencies for BoM %s",
                bom_board.display_name,
            )
            _clear_stage_sfg_lines(bom_board)

            seq = 10
            # 3a) Board consumes 2-ply SFG
            if bom_2ply:
                prod_2ply = bom_2ply.product_tmpl_id.product_variant_id
                if prod_2ply:
                    _add_stage_sfg_line(bom_board, prod_2ply, seq)
                    seq += 10

            # 3b) Board also consumes printed top sheet SFG
            if bom_ts_print:
                prod_ts_print = bom_ts_print.product_tmpl_id.product_variant_id
                if prod_ts_print:
                    _add_stage_sfg_line(bom_board, prod_ts_print, seq)
                    seq += 10

        _logger.info("fix_stage_dependencies: stage_sfg dependencies updated.")

    def _upsert_ops_basic(self, bom, op_names, metrics=None):
        """Lightweight wrapper to assign simple stage operations.

        Reuses the existing _upsert_ops helper so that workcenter assignment
        and normalization stay consistent with the rest of the engine.
        """
        self._upsert_ops(bom, op_names, metrics=metrics, placeholder_only=False)

    def _apply_stage_mapping_overlay(self, spec, product_tmpl, result, stage_mapping):
        """Overlay Stage Mapping on top of canonical staged engine output.

        Assumes _action_apply_staged has already run. Does NOT create or change
        SFG products or RM components. Only:
        - selects which engine stages are active,
        - orders them according to mapping,
        - reconnects SFG dependencies between mapped stages,
        - ensures FG consumes only the final mapped stage SFG,
        - updates operations per stage/FG.
        Skipped stages are deactivated (active=False) but not deleted.
        """
        Bom = self.env["mrp.bom"].sudo()
        metrics = result.get("metrics") or {}

        fg_bom = Bom.search(
            [("corr_spec_id", "=", spec.id), ("product_tmpl_id", "=", product_tmpl.id)],
            limit=1,
        )
        if not fg_bom:
            return

        # Discover engine-created stage BoMs reachable from the FG BoM.
        stage_boms = {
            "2ply": self.env["mrp.bom"],
            "board": self.env["mrp.bom"],
            "diecut": self.env["mrp.bom"],
            "peeloff": self.env["mrp.bom"],
            "seaming": self.env["mrp.bom"],
            "ts_print": self.env["mrp.bom"],
            "ts_cut": self.env["mrp.bom"],
        }
        visited = set()
        stack = [fg_bom]
        while stack:
            bom = stack.pop()
            if not bom or bom.id in visited:
                continue
            visited.add(bom.id)
            name = (bom.product_tmpl_id.name or "").strip()
            if name.startswith("2-ply "):
                stage_boms["2ply"] |= bom
            elif name.startswith("Board "):
                stage_boms["board"] |= bom
            elif name.startswith("Die-cut"):
                stage_boms["diecut"] |= bom
            elif name.startswith("Peeloff"):
                stage_boms["peeloff"] |= bom
            elif name.startswith("Seaming"):
                stage_boms["seaming"] |= bom
            elif name.startswith("Top-sheet Printed"):
                stage_boms["ts_print"] |= bom
            elif name.startswith("Top-sheet Cut"):
                stage_boms["ts_cut"] |= bom
            comp_tmpls = bom.bom_line_ids.mapped("product_id.product_tmpl_id")
            if comp_tmpls:
                child_boms = Bom.search([("product_tmpl_id", "in", comp_tmpls.ids)])
                for cb in child_boms:
                    if cb.id not in visited:
                        stack.append(cb)

        # Map Operation Types to engine stage keys.
        OP_TO_STAGE = {
            "Cutting": "ts_cut",
            "Printing": "ts_print",
            "Film Lamination": "ts_print",
            "Corrugation": "2ply",
            "2-Ply": "2ply",
            "Board": "board",
            "Lamination": "board",
            "Slot/Die": "diecut",
            "Die-cut": "diecut",
            "Peeling": "peeloff",
            "Peeloff": "peeloff",
            "Seaming": "seaming",
        }

        lines = stage_mapping.line_ids.sorted(key=lambda l: l.sequence or 0)
        if not lines:
            return

        mapped_keys = []
        op_by_key = {}
        for line in lines:
            op_type = line.operation_type_id
            op_name = (op_type.name or "").strip()
            if not op_name:
                continue
            key = OP_TO_STAGE.get(op_name)
            if not key:
                continue
            if key not in stage_boms or not stage_boms[key]:
                continue
            if key not in mapped_keys:
                mapped_keys.append(key)
            op_by_key[key] = op_name

        if not mapped_keys:
            return

        # Delegate pure routing rewiring to the generic helper.
        self.apply_stage_mapping(stage_boms, mapped_keys, fg_bom)
        # Apply additional stage dependency fixes specific to corrugation/board
        # (2-ply must not consume printed sheet; Board consumes both 2-ply and
        # printed top sheet).
        self.fix_stage_dependencies(stage_boms, mapped_keys)

        # Operations: per-stage from mapping, FG from final mapped line.
        if self.include_operations:
            for key in mapped_keys:
                op_label = op_by_key.get(key)
                if not op_label:
                    continue
                boms = stage_boms.get(key) or self.env["mrp.bom"]
                for bom in boms:
                    _archive_operations(bom.operation_ids)
                    self._upsert_ops_basic(bom, [op_label], metrics=metrics)

            final_line = lines[-1]
            final_op = final_line.operation_type_id
            _archive_operations(fg_bom.operation_ids)
            if final_op and final_op.name:
                self._upsert_ops_basic(fg_bom, [final_op.name], metrics=metrics)

        # Debug: active mapped chain + disabled engine stages.
        try:
            active_lines = []
            for key in mapped_keys:
                boms = stage_boms.get(key) or self.env["mrp.bom"]
                bom = boms[:1]
                if not bom:
                    continue
                prod = bom.product_tmpl_id
                active_lines.append(f"[ACTIVE] {key} -> {prod.display_name}")

            inactive_lines = []
            for key, boms in stage_boms.items():
                if key in mapped_keys:
                    continue
                for bom in boms:
                    if not bom:
                        continue
                    prod = bom.product_tmpl_id
                    status = "inactive" if not bom.active else "active"
                    inactive_lines.append(f"[ENGINE {status}] {key} -> {prod.display_name}")

            segments = list(active_lines)
            if inactive_lines:
                segments.append("---- Disabled Engine Stages ----")
                segments.extend(inactive_lines)
            self.debug_stage_text = "\n".join(segments)
        except Exception:
            # Never block BoM generation because of debug text.
            pass

    def _normalize_diecut_sheet_staged_yield_boms(self, spec):
        """Keep DIECUT_SHEET staged manufacturing BoMs whole-sheet readable.

        The staged writer reuses SFG products/BoMs by corr_sfg_key. Reusing an
        older SFG BoM can carry legacy batch quantities into a new FG BoM even
        after the FG BoM itself was deleted. For manual diecut sheet yield, the
        only batch yield boundary is Slot/Die; upstream sheet stages stay 1:1.
        """

        style_tokens = []
        try:
            style_tokens.append(getattr(spec, "style", "") or "")
        except Exception:
            pass
        try:
            style = getattr(spec, "style_id", False)
            if style:
                style_tokens.append(getattr(style, "code", "") or "")
                style_tokens.append(getattr(style, "family", "") or "")
        except Exception:
            pass
        style_val = " ".join(str(token or "") for token in style_tokens).upper()
        if "DIECUT_SHEET" not in style_val and "DIECUT_SHEET" not in style_val.replace("-", "_"):
            if "DIECUT" not in style_val or "SHEET" not in style_val:
                return

        def _text(record, *names):
            values = []
            for name in names:
                try:
                    values.append(getattr(record, name, "") or "")
                except Exception:
                    pass
            return " ".join(str(value or "") for value in values).strip().lower()

        def _is_board_token(text):
            return "board" in (text or "")

        def _is_two_ply_token(text):
            norm = (text or "").replace("_", "-")
            return "2-ply" in norm or "2ply" in norm or "2 ply" in norm

        def _is_stage(record, stage):
            stage_text = _stage(record)
            product_text = ""
            try:
                product_text = _text(record.product_tmpl_id, "name", "corr_stage")
            except Exception:
                pass
            text = "%s %s" % (stage_text, product_text)
            if stage == "board":
                return _is_board_token(text)
            if stage == "2ply":
                return _is_two_ply_token(text)
            return

        Bom = self.env["mrp.bom"].sudo()
        fg_bom = Bom.search([("corr_spec_id", "=", spec.id)], limit=1)
        if not fg_bom:
            return

        def _stage(record):
            try:
                return (getattr(record, "corr_stage", "") or "").strip().lower()
            except Exception:
                return ""

        def _write_product_qty_one(bom):
            if not bom or not bom.exists():
                return
            try:
                if float(bom.product_qty or 0.0) != 1.0:
                    bom.with_context(skip_corr_bom_qty_sync=True).write({"product_qty": 1.0})
            except Exception:
                pass

        def _child_boms_for_product(product):
            try:
                tmpl = product.product_tmpl_id
                if not tmpl:
                    return Bom.browse()
                return Bom.search([
                    ("type", "=", "normal"),
                    "|",
                    ("product_id", "=", product.id),
                    "&",
                    ("product_id", "=", False),
                    ("product_tmpl_id", "=", tmpl.id),
                ])
            except Exception:
                return Bom.browse()

        def _merge_board_two_ply_lines(bom):
            if not bom or not bom.exists():
                return
            grouped = {}
            for line in bom.bom_line_ids:
                product = line.product_id
                if not product:
                    continue
                product_stage = _stage(product.product_tmpl_id)
                product_text = _text(product.product_tmpl_id, "name", "corr_stage")
                component_key = (line.corr_component_key or "").strip().lower()
                if not (
                    product_stage in {"2-ply", "2ply"}
                    or _is_two_ply_token(product_text)
                    or component_key.startswith(("sfg2:", "stage_sfg:"))
                ):
                    continue
                grouped.setdefault(product.id, self.env["mrp.bom.line"])
                grouped[product.id] |= line

            for product_id, lines in grouped.items():
                if not lines:
                    continue
                total_qty = sum(float(item.product_qty or 0.0) for item in lines)
                keeper = lines[0]
                preferred = lines.filtered(lambda item: (item.corr_component_key or "").startswith("sfg2:"))[:1]
                if preferred:
                    keeper = preferred
                keep_key = (keeper.corr_component_key or "").strip() or f"sfg2:{product_id}"
                vals = {
                    "product_qty": total_qty or 1.0,
                    "corr_component_key": keep_key,
                }
                try:
                    if float(keeper.product_qty or 0.0) != float(vals["product_qty"]) or (keeper.corr_component_key or "").strip() != keep_key:
                        keeper.write(vals)
                except Exception:
                    keeper.write(vals)
                extras = lines - keeper
                if extras:
                    extras.unlink()

        queue = [fg_bom]
        seen = set()
        while queue:
            bom = queue.pop(0)
            if not bom or not bom.exists() or bom.id in seen:
                continue
            seen.add(bom.id)

            bom_stage = _stage(bom)
            bom_is_board = _is_stage(bom, "board")
            bom_is_two_ply = _is_stage(bom, "2ply")
            if bom_is_board or bom_is_two_ply:
                _write_product_qty_one(bom)
            if bom_is_board:
                _merge_board_two_ply_lines(bom)

            for line in bom.bom_line_ids:
                product = line.product_id
                if not product:
                    continue
                child_boms = _child_boms_for_product(product)
                for child_bom in child_boms:
                    if child_bom and child_bom.exists():
                        queue.append(child_bom)

    def _action_apply_staged(self, spec, product_tmpl, result):
        """Create staged stockable SFGs (2-ply/4-ply/final board/die-cut) and FG."""
        def _safe_float(val, d=0.0):
            try:
                return float(val or d)
            except Exception:
                return d

        engine = self.env["corr.formula.engine"].sudo().with_context(skip_corr_stack_check=True)
        metrics = result.get("metrics") or {}
        # Sheet-level operation times (top-sheet Cutting/Printing/Film) should be computed
        # per-sheet without dividing by UPS. Force UPS=1 by removing layout.printing_ups
        # from a copy of metrics for those specific calls.
        try:
            metrics_no_ups = dict(metrics or {})
            # Build a shallow copy of layout and drop printing_ups
            lay = dict((metrics_no_ups.get("layout") or {}))
            if "printing_ups" in lay:
                lay = dict(lay)
                lay.pop("printing_ups", None)
            metrics_no_ups["layout"] = lay
        except Exception:
            metrics_no_ups = {**(metrics or {}), "layout": {}}

        # Helper: ensure stage metrics carry geometry; if missing/zero, borrow from fallback
        def _metrics_with_geometry(base, fallback):
            try:
                base = dict(base or {})
                g = dict((base.get("geometry") or {}))
                gf = dict(((fallback or {}).get("geometry") or {}))
                def _nz(v):
                    try:
                        return float(v or 0.0) > 0.0
                    except Exception:
                        return False
                updated = False
                if not _nz(g.get("length_mm")) and _nz(gf.get("length_mm")):
                    g["length_mm"] = gf.get("length_mm")
                    updated = True
                if not _nz(g.get("deckle_mm")) and _nz(gf.get("deckle_mm")):
                    g["deckle_mm"] = gf.get("deckle_mm")
                    updated = True
                if updated:
                    base["geometry"] = g
                return base
            except Exception:
                return base or {}
        # Base RM lines from the FG compute (net vs gross driven by wizard switch)
        if self.include_waste_in_bom and result.get("rm_lines_gross"):
            rm_lines = result.get("rm_lines_gross") or []
        else:
            rm_lines = result.get("rm_lines") or []

        # Abort early if any component is missing a mapping
        missing = []
        for line in rm_lines:
            role = (line.get("role") or "").lower()
            if role not in ("liner", "medium", "adhesive", "ink", "lamination", "wire", "tape", "top_sheet"):
                continue
            mapped, gsm, flute, bf = self._map_component(line, metrics)
            if not mapped:
                missing.append(
                    f"Missing mapping for {role} layer={line.get('layer_no')} gsm={gsm} bf={bf} flute={flute}"
                )
        if missing:
            raise UserError(_("Missing mappings:\n%s") % "\n".join(missing))

        # Resolve corrugation staging mode (full vs board-only).
        # Default is to keep the existing behavior (build 4-ply when applicable).
        stage_mode = "full"
        try:
            # Prefer spec-level override when available
            mode_spec = (getattr(spec, "corr_corr_stage_mode", "") or "inherit").strip()
            if mode_spec and mode_spec != "inherit":
                stage_mode = mode_spec
            else:
                mode_val = (getattr(product_tmpl, "corr_corr_stage_mode", "") or "inherit").strip()
                if mode_val and mode_val != "inherit":
                    stage_mode = mode_val
        except Exception:
            stage_mode = "full"

        # Local helper: attach BoM lines to a specific operation on the BoM
        # so the "Consumed in Operation" column is populated. We match lines by
        # corr_component_key prefix (e.g., 'liner:', 'medium:', 'sfg2:', 'top_sheet:', 'adhesive:').
        def _link_lines_to_op(bom, key_prefixes, op_label):
            try:
                if not self.include_operations or not bom or not bom.exists():
                    return
                # Normalize name the same way as _upsert_ops
                normalize = {
                    "combine 4-ply": "Lamination",
                    "lamination 5-ply": "Lamination",
                    "die-cut": "Slot/Die",
                    # Keep Peeling link distinct
                    "peeling": "Peeling",
                    "gluing": "Seaming",
                    "qc": "Quality Check",
                    # Finishing ops
                    "stitching": "Stitching",
                    "folding": "Folding",
                    "folding (manual)": "Folding (Manual)",
                    "flap pasting": "Flap Pasting",
                    "bottom lock": "Bottom Lock",
                }
                name = normalize.get((op_label or "").strip().lower(), op_label)
                op = bom.operation_ids.filtered(lambda o, n=name: o.name == n)[:1]
                if not op:
                    return
                prefixes = tuple(key_prefixes or [])
                if not prefixes:
                    return
                for bl in bom.bom_line_ids:
                    k = (bl.corr_component_key or "")
                    if k.startswith(prefixes):
                        bl.operation_id = op.id
            except Exception:
                # Never block the wizard if linking fails
                pass

        # Base geometry for keying and temp specs
        geo = (metrics.get("geometry") or {})
        deckle_mm = _safe_float(geo.get("deckle_mm"), 0.0)
        length_mm = _safe_float(geo.get("length_mm"), 0.0)
        height_mm = _safe_float(geo.get("height_mm"), 0.0)
        if not height_mm:
            try:
                hm_spec = getattr(spec, "height_mm", None)
                if hm_spec:
                    height_mm = _safe_float(hm_spec, 0.0)
            except Exception:
                pass
        deck = int(round(deckle_mm))
        leng = int(round(length_mm))
        # Friendly cm values for display
        def _fmt_cm(mm):
            try:
                cm = float(mm or 0.0) / 10.0
                # Print without trailing .0
                return ("{:.1f}".format(cm)).rstrip('0').rstrip('.')
            except Exception:
                return str(mm)
        deck_cm = _fmt_cm(deckle_mm)
        leng_cm = _fmt_cm(length_mm)
        # Compute sheet-level dimensions (cm) when sheet naming is requested.
        sheet_deck_cm = deck_cm
        sheet_len_cm = leng_cm
        sheet_deck_mm = deckle_mm
        sheet_len_mm = length_mm
        if 'sheet_flag' in locals() and (sheet_flag or ('force_sheet_naming' in locals() and force_sheet_naming)) and ups_val and ups_val > 0:
            lay = (metrics.get("layout") or {})
            def _to_int(x):
                try:
                    return int(float(x))
                except Exception:
                    return None
            cols = _to_int(lay.get("ups_cols") or lay.get("cols") or lay.get("ups_x"))
            rows = _to_int(lay.get("ups_rows") or lay.get("rows") or lay.get("ups_y"))
            if cols and rows and cols > 0 and rows > 0 and (cols * rows) == int(ups_val):
                c, r = cols, rows
            else:
                # Choose a near-square factorization of UPS when explicit layout is missing
                u = int(ups_val)
                best = None
                for c in range(1, u + 1):
                    if u % c != 0:
                        continue
                    r = u // c
                    try:
                        # Prefer sheet aspect close to 1: (deckle*c)/(length*r) ~ 1
                        ratio = abs(((deckle_mm or 1.0) * c) / max((length_mm or 1.0) * r, 1e-6) - 1.0)
                    except Exception:
                        ratio = abs((c / max(r, 1)) - 1.0)
                    key = (ratio, abs(c - r), -min(c, r))
                    if best is None or key < best[0]:
                        best = (key, (c, r))
                c, r = best[1] if best else (u, 1)
            # Convert to cm strings
            sheet_deck_mm = deckle_mm * c
            sheet_len_mm = length_mm * r
            sheet_deck_cm = _fmt_cm(sheet_deck_mm)
            sheet_len_cm = _fmt_cm(sheet_len_mm)

        def _fmt_size_label(len_mm_val, width_mm_val, height_mm_val=None):
            parts = [
                _fmt_cm(len_mm_val),
                _fmt_cm(width_mm_val),
            ]
            h_val = _safe_float(height_mm_val, 0.0)
            if h_val:
                parts.append(_fmt_cm(h_val))
            return "x".join(parts) + " cm"

        # Build ply map from FG spec
        ply_detail = metrics.get("ply_detail") or []
        layers_fg = [
            {
                "idx": int(_safe_float(p.get("idx"), 0)),
                "role": (p.get("role") or "").lower(),
                "gsm": _safe_float(p.get("gsm"), 0.0),
                "bf": _safe_float(p.get("bf"), 0.0),
                "flute": p.get("flute") or None,
                "flute_tuf": _safe_float(p.get("flute_tuf") or p.get("tuf_applied"), 0.0),
                "color": p.get("color") or None,
            }
            for p in ply_detail if p.get("idx")
        ]
        layers_fg.sort(key=lambda x: x["idx"])  # ensure order bottom->top
        try:
            stack_lines = spec.stack_line_ids.sorted(key=lambda l: l.sequence or 0)
            for layer in layers_fg:
                idx = int(layer.get("idx") or 0)
                if 1 <= idx <= len(stack_lines):
                    color = getattr(stack_lines[idx - 1], "color", None)
                    if color:
                        layer["color"] = color
        except Exception:
            pass

        medium_idxs = [x["idx"] for x in layers_fg if x["role"] == "medium"]
        if not medium_idxs:
            raise UserError(_("No medium layers detected in spec; cannot build staged SFGs."))

        # Helper: create a temporary spec with given layers (list of dicts with role/gsm/flute)
        def _temp_spec_for_layers(layer_defs, include_top_sheet=False):
            # Create a short-lived spec while bypassing strict stack-line validation
            # (the staged wizard supplies stack_json only and unlinks the temp spec).
            Spec = self.env["corr.spec.template"].sudo().with_context(skip_corr_stack_check=True)
            vals = {
                "name": f"Temp Staged {len(layer_defs)}-ply",
                "product_tmpl_id": product_tmpl.id,
                "style": "SHEET",
                "length_mm": spec.length_mm,
                "width_mm": spec.width_mm,
                "height_mm": 0.0,
                "ply_count": len(layer_defs),
                "stack_json": [{
                    "role": (ld.get("role") or "").lower(),
                    "gsm": _safe_float(ld.get("gsm"), 0.0),
                    **({"flute_code": ld.get("flute")} if (ld.get("role") == "medium" and ld.get("flute")) else {}),
                    **({"bf": _safe_float(ld.get("bf"), 0.0)} if (ld.get("bf") not in (None, "")) else {}),
                    **({"color": ld.get("color")} if (ld.get("color") not in (None, "")) else {}),
                } for ld in layer_defs],
                "printing_required": False,
                "print_colors": 0,
            }
            # Carry top sheet binding into temp spec when needed
            if include_top_sheet and getattr(spec, "top_sheet_product_id", False):
                vals["top_sheet_product_id"] = spec.top_sheet_product_id.id
            # Carry extra parameters (dimensions_mode, UPS, slitting, waste) for accurate compute
            try:
                if getattr(spec, "extra_params_json", False):
                    xp = dict(spec.extra_params_json or {})
                    if not include_top_sheet:
                        # Strip top-sheet context from 2-ply/4-ply temp specs to avoid leaking top_sheet into 2-ply compute
                        for k in (
                            "top_layer_mode",
                            "top_sheet_product_id",
                            "top_sheet_code",
                            "top_sheet_ups",
                            "printing_ups",
                        ):
                            xp.pop(k, None)
                    vals["extra_params_json"] = xp
            except Exception:
                pass
            tmp = Spec.create(vals)
            try:
                res = engine.compute_requirements(tmp, 1.0)
            finally:
                try:
                    tmp.unlink()
                except Exception:
                    pass
            return res

        # Identify 2-ply pairs from the stack. Roll-mode keeps the historic
        # lower-first pairing. Unit-top medium-first stacks pair medium->next
        # liner so the entered base order maps directly to staged 2-ply inputs.
        pairs_2ply = self._build_2ply_pairs_from_layers(spec, layers_fg)

        if not pairs_2ply:
            raise UserError(_("Unable to determine 2-ply pairs from the stack."))

        # Compute sheet requirements for 2-ply, 4-ply, and final board (N-ply) to split adhesives
        # 2-ply results per pair
        res_2ply = [
            _temp_spec_for_layers([lin, med], include_top_sheet=False)
            for (lin, med) in pairs_2ply
        ]
        # 4-ply: combine first 2 pairs (double-wall); for single-wall, 4-ply is just None.
        # When stage_mode is 'board_only', skip explicit 4-ply staging: treat all
        # combine adhesive as part of the final board stage instead of a separate SFG.
        layers_4ply = []
        if len(pairs_2ply) >= 2 and stage_mode != "board_only":
            lin1, med1 = pairs_2ply[0]
            lin2, med2 = pairs_2ply[1]
            layers_4ply = [lin1, med1, lin2, med2]
        res_4ply = _temp_spec_for_layers(layers_4ply, include_top_sheet=False) if layers_4ply else {"rm_lines": []}

        # Final board before die-cut: all layers without top printed sheet (if printed sheet exists, it's added at final step via include_top_sheet)
        layers_board = [x for x in layers_fg if x["role"] in ("liner", "medium")]
        # Compute both without and with top sheet to split adhesives generically for N-ply
        res_board_no_top = _temp_spec_for_layers(layers_board, include_top_sheet=False)
        res_board = _temp_spec_for_layers(layers_board, include_top_sheet=True)

        # Helper: pick net vs gross RM lines for staged sub-computes
        def _iter_rm(res_obj):
            if self.include_waste_in_bom and res_obj.get("rm_lines_gross"):
                return res_obj.get("rm_lines_gross") or []
            return res_obj.get("rm_lines") or []

        def _sum_adh(res):
            return sum(
                _safe_float(l.get("qty"), 0.0)
                for l in _iter_rm(res)
                if (l.get("role") or "").lower() == "adhesive"
            )

        adh_2_list = [_sum_adh(r) for r in res_2ply]
        adh_4 = _sum_adh(res_4ply) if res_4ply else 0.0
        adh_board_no_top = _sum_adh(res_board_no_top)
        adh_board_with_top = _sum_adh(res_board)
        # FG adhesives total (includes locking)
        res_fg = engine.compute_requirements(spec, 1.0)
        adh_fg = _sum_adh(res_fg)

        # Split adhesives for N-ply
        adh_2_tot = sum(adh_2_list)
        # Adhesive to combine first two pairs into 4-ply (if any)
        adh_combine_first = max(0.0, (adh_4 - (sum(adh_2_list[:2]) if len(adh_2_list) >= 2 else 0.0))) if layers_4ply else 0.0
        # Adhesive to combine all pairs into board (excludes top-sheet)
        adh_combine_total = max(0.0, adh_board_no_top - adh_2_tot)
        # Remaining combine after first 4-ply combine (for 3rd+ pairs)
        adh_combine_remaining = max(0.0, adh_combine_total - adh_combine_first)
        # Top lamination adhesive (top sheet onto board)
        adh_top = max(0.0, adh_board_with_top - adh_board_no_top)
        # Locking (FG) adhesives if engine reports extra
        adh_lock = max(0.0, adh_fg - adh_board_with_top)

        # Fallback for 4-ply combine adhesive when engine delta is zero.
        # Uses per-spec factor "adhesive_factor_combine" (kg/m^2) or ICP
        # default "corr_mrp_bridge.default_adhesive_factor_combine".
        try:
            if adh_combine_first <= 0 and layers_4ply:
                xp_src = getattr(spec, "extra_params_json", None) or {}
                factor = None
                if xp_src.get("adhesive_factor_combine") is not None:
                    try:
                        factor = float(xp_src.get("adhesive_factor_combine") or 0.0)
                    except Exception:
                        factor = None
                if (factor is None) or (factor <= 0):
                    icp = self.env["ir.config_parameter"].sudo()
                    raw = icp.get_param("corr_mrp_bridge.default_adhesive_factor_combine")
                    try:
                        factor = float(raw) if raw is not None else 0.0
                    except Exception:
                        factor = 0.0
                if factor and factor > 0:
                    area_per_box = None
                    lay = (metrics.get("layout") or {})
                    geo = (metrics.get("geometry") or {})
                    try:
                        area_per_box = float(lay.get("area_per_box_m2") or 0.0)
                    except Exception:
                        area_per_box = None
                    if (area_per_box is None) or (area_per_box == 0.0):
                        try:
                            area_per_box = float(geo.get("blank_per_box_m2") or 0.0)
                        except Exception:
                            area_per_box = 0.0
                    calc = factor * float(area_per_box or 0.0)
                    if calc > 0:
                        adh_combine_first = max(adh_combine_first, calc)
        except Exception:
            pass

        # Fallback for top-sheet lamination adhesive when engine returns zero.
        # If a top sheet exists but the engine didn't provide an incremental
        # adhesive for the board stage, compute it from a factor x area.
        # Order of precedence for factor (kg/m^2):
        #   1) spec.extra_params_json["adhesive_factor_top_sheet"]
        #   2) ICP param: corr_mrp_bridge.default_adhesive_factor_top_sheet
        try:
            if adh_top <= 0:
                # Only try a fallback if a top sheet is actually present in the
                # board-level compute (res_board). This avoids adding adhesive
                # when there is nothing to laminate.
                has_top_sheet = any(
                    ((x.get("role") or "").lower() == "top_sheet") for x in (res_board.get("rm_lines") or [])
                )
                if has_top_sheet:
                    xp_src = getattr(spec, "extra_params_json", None) or {}
                    factor = None
                    # 1) Spec override
                    if xp_src.get("adhesive_factor_top_sheet") is not None:
                        try:
                            factor = float(xp_src.get("adhesive_factor_top_sheet") or 0.0)
                        except Exception:
                            factor = None
                    # 2) System default
                    if (factor is None) or (factor <= 0):
                        icp = self.env["ir.config_parameter"].sudo()
                        raw = icp.get_param("corr_mrp_bridge.default_adhesive_factor_top_sheet")
                        try:
                            factor = float(raw) if raw is not None else 0.0
                        except Exception:
                            factor = 0.0
                    if factor and factor > 0:
                        # Area per finished box; prefer layout metric when available,
                        # else geometry fallback.
                        area_per_box = None
                        lay = (metrics.get("layout") or {})
                        geo = (metrics.get("geometry") or {})
                        try:
                            area_per_box = float(lay.get("area_per_box_m2") or 0.0)
                        except Exception:
                            area_per_box = None
                        if (area_per_box is None) or (area_per_box == 0.0):
                            try:
                                area_per_box = float(geo.get("blank_per_box_m2") or 0.0)
                            except Exception:
                                area_per_box = 0.0
                        # Interpret factor as kg per m^2 for top lamination
                        calc = factor * float(area_per_box or 0.0)
                        # Only raise adh_top when the computed fallback is positive
                        if calc > 0:
                            adh_top = max(adh_top, calc)
        except Exception:
            # Do not fail the wizard on fallback errors
            pass

        # Sheet-level staging gating (BMC only, driven by printing_ups)
        icp = self.env["ir.config_parameter"].sudo()
        try:
            peeling_sfg_enabled = icp.get_param("corr_mrp_bridge.enable_peeling_sfg", default="0") in (
                "1",
                "true",
                "True",
            )
        except Exception:
            peeling_sfg_enabled = False
        style_val = (getattr(spec, "style", "") or "").upper()
        def _fg_per_sheet_from_spec_or_metrics():
            for getter in (
                lambda: ((metrics or {}).get("extra") or {}).get("style", {}).get("metadata", {}).get("fg_per_sheet"),
                lambda: ((metrics or {}).get("extra") or {}).get("style", {}).get("metadata", {}).get("diecut_ups"),
                lambda: ((metrics or {}).get("style") or {}).get("metadata", {}).get("fg_per_sheet"),
                lambda: ((metrics or {}).get("style") or {}).get("metadata", {}).get("diecut_ups"),
            ):
                try:
                    value = getter()
                    if value not in (None, ""):
                        value_int = int(float(value or 0))
                        if value_int > 0:
                            return value_int
                except Exception:
                    pass
            try:
                collector = getattr(spec, "collect_style_params", None)
                params = collector() if callable(collector) else {}
            except Exception:
                params = {}
            for key in ("fg_per_sheet", "diecut_ups"):
                try:
                    value = params.get(key)
                    if value not in (None, ""):
                        value_int = int(float(value or 0))
                        if value_int > 0:
                            return value_int
                except Exception:
                    pass
            try:
                xp = getattr(spec, "extra_params_json", None) or {}
            except Exception:
                xp = {}
            for key in ("fg_per_sheet", "diecut_ups"):
                try:
                    value = xp.get(key)
                    if value not in (None, ""):
                        value_int = int(float(value or 0))
                        if value_int > 0:
                            return value_int
                except Exception:
                    pass
            return 0

        try:
            ups_val = 0
            xp_src = getattr(spec, "extra_params_json", None) or {}
            if xp_src and xp_src.get("printing_ups"):
                ups_val = int(float(xp_src.get("printing_ups") or 0))
            elif (metrics.get("layout") or {}).get("printing_ups"):
                ups_val = int(float((metrics.get("layout") or {}).get("printing_ups") or 0))
        except Exception:
            ups_val = 0
        diecut_yield_sheet_flag = "DIECUT_SHEET" in style_val
        if diecut_yield_sheet_flag and not ups_val:
            ups_val = _fg_per_sheet_from_spec_or_metrics()
        sheet_flag = (
            (icp.get_param("corr_mrp_bridge.sheet_staging_bmc", default="0") in ("1", "true", "True"))
            and ("BMC" in style_val)
            and (ups_val and ups_val > 0)
        ) or (diecut_yield_sheet_flag and ups_val and ups_val > 0)
        # Optional naming override: force sheet-dimension naming when UPS>0,
        # even if sheet staging is not active (non-BMC styles). This only
        # affects names, not quantity logic.
        try:
            force_sheet_naming = icp.get_param("corr_mrp_bridge.force_sheet_naming", default="0") in ("1", "true", "True")
        except Exception:
            force_sheet_naming = False
        name_sheet_flag = (sheet_flag and ups_val and ups_val > 0) or (force_sheet_naming and ups_val and ups_val > 0)

        def _per_sheet(qty):
            try:
                q = float(qty or 0.0)
            except Exception:
                q = 0.0
            try:
                u = float(ups_val or 0.0)
            except Exception:
                u = 0.0
            if not (sheet_flag and u):
                return q
            if diecut_yield_sheet_flag:
                return q * u
            # Configurable basis: some engines return per-up preview; others per-box.
            # corr_mrp_bridge.preview_qty_is_per_up = 1 => per-sheet = per-up * UPS
            # corr_mrp_bridge.preview_qty_is_per_up = 0 => per-sheet = per-box / UPS
            preview_per_up = True
            try:
                icp = self.env["ir.config_parameter"].sudo()
                preview_per_up = icp.get_param("corr_mrp_bridge.preview_qty_is_per_up", default="1") in ("1", "true", "True")
            except Exception:
                preview_per_up = True
            return (q * u) if preview_per_up else (q / u)

        def _flute_tuf_for_layer(layer):
            try:
                tuf = float((layer or {}).get("flute_tuf") or 0.0)
            except Exception:
                tuf = 0.0
            if tuf > 0.0:
                return tuf
            flute_code = ""
            try:
                flute_code = str((layer or {}).get("flute") or "").strip()
            except Exception:
                flute_code = ""
            if flute_code:
                try:
                    flute = self.env["corr.flute"].sudo().search(
                        ["|", ("code", "=", flute_code), ("name", "=", flute_code)],
                        limit=1,
                    )
                    tuf = float(getattr(flute, "tuf", 0.0) or 0.0) if flute else 0.0
                except Exception:
                    tuf = 0.0
            return tuf if tuf > 0.0 else 1.0

        def _whole_sheet_paper_qty(layer):
            """Return one full sheet's paper kg for sheet-yield staged BoMs."""
            try:
                gsm = float((layer or {}).get("gsm") or 0.0)
            except Exception:
                gsm = 0.0
            if gsm <= 0.0:
                return 0.0
            try:
                area_m2 = (float(sheet_deck_mm or 0.0) * float(sheet_len_mm or 0.0)) / 1000000.0
            except Exception:
                area_m2 = 0.0
            if area_m2 <= 0.0:
                return 0.0
            role = str((layer or {}).get("role") or "").strip().lower()
            factor = _flute_tuf_for_layer(layer) if role == "medium" else 1.0
            return (gsm * area_m2 * factor) / 1000.0

        # Ensure sheet-dimension strings reflect current UPS and layout when
        # sheet naming is active. This runs after sheet_flag/ups_val are known.
        try:
            if name_sheet_flag:
                lay = (metrics.get("layout") or {})
                def _to_int(x):
                    try:
                        return int(float(x))
                    except Exception:
                        return None
                cols = _to_int(lay.get("ups_cols") or lay.get("cols") or lay.get("ups_x"))
                rows = _to_int(lay.get("ups_rows") or lay.get("rows") or lay.get("ups_y"))
                if cols and rows and cols > 0 and rows > 0 and (cols * rows) == int(ups_val or 0):
                    c, r = cols, rows
                else:
                    u = int(ups_val or 1)
                    best = None
                    for c in range(1, u + 1):
                        if u % c != 0:
                            continue
                        r = u // c
                        try:
                            ratio = abs(((deckle_mm or 1.0) * c) / max((length_mm or 1.0) * r, 1e-6) - 1.0)
                        except Exception:
                            ratio = abs((c / max(r, 1)) - 1.0)
                            key = (ratio, abs(c - r), -min(c, r))
                            if best is None or key < best[0]:
                                best = (key, (c, r))
                    c, r = best[1] if best else (u, 1)
                # Update the cm strings used by SFG names
                sheet_deck_mm = deckle_mm * c
                sheet_len_mm = length_mm * r
                sheet_deck_cm = _fmt_cm(sheet_deck_mm)
                sheet_len_cm = _fmt_cm(sheet_len_mm)
        except Exception:
            pass

        # Derive effective per-slit deckle for naming when slitting UPS is used.
        # For BMC/sheet cases, the sheet deckle represents the total deckle before
        # slitting, so divide by slitting_ups to get the width of each slit.
        slitting_ups = 1
        try:
            xp = getattr(spec, "extra_params_json", None) or {}
            if isinstance(xp, str):
                try:
                    xp = json.loads((xp or "{}").strip() or "{}")
                except Exception:
                    xp = {}
            su_raw = xp.get("liner_slitting_ups") or xp.get("liner_slitting_ups_ui")
            if su_raw is not None:
                slitting_ups = int(float(su_raw) or 1)
        except Exception:
            slitting_ups = 1
        if slitting_ups <= 0:
            slitting_ups = 1

        # Naming convention for SFG names: show a sheet-based size derived
        # from per-box dimensions and UPS settings, expressed as
        # Width x Length (cm).
        #
        # - When printing_ups > 1 (multi-up printing), use a symmetric
        #   slitting expansion on both axes to approximate full sheet:
        #       width_display  = deckle_mm  * slitting_ups
        #       length_display = length_mm * slitting_ups
        # - When printing_ups == 1 and slitting_ups > 1 (pure slitting),
        #   use per-slit width and expanded length:
        #       width_display  = deckle_mm  / slitting_ups
        #       length_display = length_mm * slitting_ups
        # - Otherwise, fall back to the box blank size.
        try:
            pu = float(ups_val or 0.0)
        except Exception:
            pu = 0.0
        try:
            su = float(slitting_ups or 1.0)
        except Exception:
            su = 1.0
        if su <= 0.0:
            su = 1.0

        try:
            if pu > 1.0:
                eff_w_mm = float(deckle_mm or 0.0) * su
                eff_l_mm = float(length_mm or 0.0) * su
            elif pu <= 1.0 and su > 1.0:
                eff_w_mm = float(deckle_mm or 0.0) / su
                eff_l_mm = float(length_mm or 0.0)
            else:
                eff_w_mm = float(deckle_mm or 0.0)
                eff_l_mm = float(length_mm or 0.0)
        except Exception:
            eff_w_mm = deckle_mm
            eff_l_mm = length_mm

        size_label_box = _fmt_size_label(eff_w_mm, eff_l_mm, height_mm)
        size_label_sheet = size_label_box
        use_sheet_label = True

        # Mapping helper
        # Build FG layer->product map once (hybrid: reuse Preview SKUs for staged BoMs)
        fg_layer_map = {}
        fg_layers_by_role = {"liner": [], "medium": []}
        try:
            for p in (metrics.get("ply_detail") or []):
                idx = int(_safe_float(p.get("idx"), 0))
                role = (p.get("role") or "").lower()
                if not idx or role not in ("liner", "medium"):
                    continue
                attrs = {
                    "idx": idx,
                    "role": role,
                    "gsm": _safe_float(p.get("gsm"), 0.0),
                    "bf": _safe_float(p.get("bf"), 0.0),
                    "flute": p.get("flute") or None,
                    "color": (p.get("color") or None),
                }
                try:
                    stack_lines = spec.stack_line_ids.sorted(key=lambda l: l.sequence or 0)
                    if 1 <= idx <= len(stack_lines):
                        color = getattr(stack_lines[idx - 1], "color", None)
                        if color:
                            attrs["color"] = color
                except Exception:
                    pass
                comp_hint = {"role": role, "layer_no": idx}
                m, _gsm, _flute, _bf = self._map_component(comp_hint, metrics)
                if m and m.get("product") and m.get("uom"):
                    fg_layer_map[(role, idx)] = {"product": m["product"], "uom": m["uom"]}
                    attrs["product"] = m["product"]
                    attrs["uom"] = m["uom"]
                fg_layers_by_role[role].append(attrs)
        except Exception:
            fg_layer_map = {}
            fg_layers_by_role = {"liner": [], "medium": []}

        # Build FG preview-basis qty map per layer so staged SFGs reuse the
        # same quantities as the Preview (then convert to per-sheet via
        # _per_sheet later). This avoids discrepancies between the 2-ply temp
        # spec compute and the FG compute for a given layer.
        fg_qty_preview_by_layer = {}
        try:
            for ln in (rm_lines or []):
                r = (ln.get("role") or "").lower()
                # Translate engine 'paper' to liner/medium via FG ply_detail
                if r == "paper":
                    try:
                        ln_idx = int(ln.get("layer_no") or 0)
                    except Exception:
                        ln_idx = 0
                    r_eff = None
                    for p in (metrics.get("ply_detail") or []):
                        try:
                            if int(p.get("idx") or 0) == ln_idx:
                                r_eff = (p.get("role") or "").lower()
                                break
                        except Exception:
                            continue
                    if r_eff in ("liner", "medium"):
                        r = r_eff
                if r not in ("liner", "medium"):
                    continue
                # Determine effective layer index from line.layer_no, or via
                # mapping fallback when layer_no is missing.
                try:
                    idx = int(ln.get("layer_no") or 0)
                except Exception:
                    idx = 0
                if not idx:
                    try:
                        m_try, _g, _f, _b = self._map_component(ln, metrics)
                        if m_try and m_try.get("layer_idx"):
                            idx = int(m_try.get("layer_idx") or 0)
                    except Exception:
                        idx = 0
                if not idx:
                    # Final coarse inference: match by flute then gsm distance
                    try:
                        fl = (ln.get("flute") or "").strip().lower()
                    except Exception:
                        fl = ""
                    plyd = (metrics.get("ply_detail") or [])
                    cand = [p for p in plyd if (p.get("role") or "").lower() == r]
                    if fl:
                        cand2 = [p for p in cand if str(p.get("flute") or "").strip().lower() == fl]
                        cand = cand2 or cand
                    def _to_float(v):
                        try:
                            return float(v)
                        except Exception:
                            return None
                    g = _to_float(ln.get("gsm"))
                    if g is not None and cand:
                        best = None
                        for p in cand:
                            gv = _to_float(p.get("gsm")) or 0.0
                            diff = abs(gv - g)
                            keyb = (diff, int(p.get("idx") or 0))
                            if best is None or keyb < best[0]:
                                best = (keyb, p)
                        if best and best[1]:
                            try:
                                idx = int(best[1].get("idx") or 0)
                            except Exception:
                                idx = 0
                if not idx:
                    continue
                try:
                    fg_qty_preview_by_layer[(r, idx)] = _safe_float(ln.get("qty"), 0.0)
                except Exception:
                    continue
        except Exception:
            fg_qty_preview_by_layer = {}

        def _map_rm_lines(res_obj):
            mapped = []
            for source_idx, line in enumerate(_iter_rm(res_obj), start=1):
                gsm = None
                flute = None
                bf = None
                role = (line.get("role") or "").lower()
                # Accept engine 'paper' and translate to liner/medium via ply_detail
                if role == "paper":
                    try:
                        ln = int(line.get("layer_no") or 0)
                    except Exception:
                        ln = 0
                    r_eff = None
                    metrics_obj = (res_obj.get("metrics") or {})
                    plyd = metrics_obj.get("ply_detail") or []
                    # 1) Direct by layer index when available
                    if ln:
                        for p in plyd:
                            try:
                                if int(p.get("idx") or 0) == ln:
                                    r_eff = (p.get("role") or "").lower()
                                    break
                            except Exception:
                                continue
                    # 2) Fallback inference when layer_no missing/mismatch: use flute/gsm clues
                    if r_eff not in ("liner", "medium"):
                        try:
                            fl = (line.get("flute") or "").strip().lower()
                        except Exception:
                            fl = ""
                        if fl:
                            # Any medium layer with same flute -> medium
                            for p in plyd:
                                try:
                                    if (p.get("role") or "").lower() == "medium" and (str(p.get("flute") or "").strip().lower() == fl):
                                        r_eff = "medium"
                                        break
                                except Exception:
                                    continue
                        if r_eff not in ("liner", "medium"):
                            # Compare GSM distance against nearest liner/medium
                            def _to_float(v):
                                try:
                                    return float(v)
                                except Exception:
                                    return None
                            g = _to_float(line.get("gsm"))
                            if g is not None:
                                meds = [p for p in plyd if (p.get("role") or "").lower() == "medium"]
                                lins = [p for p in plyd if (p.get("role") or "").lower() == "liner"]
                                def _diff(p):
                                    gv = _to_float(p.get("gsm"))
                                    return abs((gv or 0.0) - g)
                                try:
                                    d_med = min([_diff(p) for p in meds]) if meds else 1e9
                                except Exception:
                                    d_med = 1e9
                                try:
                                    d_lin = min([_diff(p) for p in lins]) if lins else 1e9
                                except Exception:
                                    d_lin = 1e9
                                r_eff = "medium" if d_med <= d_lin else ("liner" if (d_lin < 1e9) else None)
                    if r_eff in ("liner", "medium"):
                        line = dict(line)
                        line["role"] = r_eff
                        role = r_eff
                if role not in ("liner", "medium", "adhesive", "ink", "lamination", "wire", "tape", "top_sheet"):
                    continue
                # Prefer FG mapping (by layer) to guarantee consistent SKUs with Preview
                try:
                    ln_try = int(line.get("layer_no") or 0)
                except Exception:
                    ln_try = 0
                m = None
                if role in ("liner", "medium") and ln_try and fg_layer_map.get((role, ln_try)):
                    picked = fg_layer_map[(role, ln_try)]
                    m = {"product": picked["product"], "uom": picked["uom"]}
                    gsm = None
                    flute = None
                    bf = None
                else:
                    # Single-layer fallback: if only one FG layer of this role, reuse it even when layer_no is missing
                    if role in ("liner", "medium") and not ln_try and len(fg_layers_by_role.get(role, [])) == 1:
                        only = fg_layers_by_role[role][0]
                        if only.get("product") and only.get("uom"):
                            m = {"product": only["product"], "uom": only["uom"]}
                            gsm = only.get("gsm")
                            flute = only.get("flute")
                            bf = only.get("bf")
                    # If still not resolved, attempt staged match but carry FG signals to help strict matching
                    if not m:
                        enriched = dict(line)
                        # If staged line lacks flute/bf/color/gsm, try to infer from closest FG layer of same role
                        def _closest_fg_layer():
                            layers = fg_layers_by_role.get(role, [])
                            if not layers:
                                return None
                            # Match by flute when present, else by smallest gsm diff
                            try:
                                g = float(enriched.get("gsm") or 0.0)
                            except Exception:
                                g = 0.0
                            fl = (enriched.get("flute") or None)
                            best = None
                            for a in layers:
                                if fl and a.get("flute") and str(a.get("flute")).lower() != str(fl).lower():
                                    continue
                                diff = abs((a.get("gsm") or 0.0) - g) if g else 0.0
                                key = (diff, a.get("idx") or 0)
                                if best is None or key < best[0]:
                                    best = (key, a)
                            return best and best[1]
                        cand = _closest_fg_layer()
                        if cand:
                            enriched.setdefault("flute", cand.get("flute"))
                            enriched.setdefault("bf", cand.get("bf"))
                            enriched.setdefault("gsm", cand.get("gsm"))
                            enriched.setdefault("color", cand.get("color"))
                        m, gsm, flute, bf = self._map_component(enriched, res_obj.get("metrics") or {})
                if not m:
                    continue
                # Prefer FG preview-basis qty when available (by role+layer),
                # else use the staged compute qty from res_obj. Final caller
                # will convert to per-sheet via _per_sheet.
                qty = _safe_float(line.get("qty"), 0.0)
                if role in ("liner", "medium"):
                    picked_idx = None
                    # 1) Try direct layer index match
                    if ln_try:
                        key0 = (role, ln_try)
                        if key0 in fg_qty_preview_by_layer:
                            qty = _safe_float(fg_qty_preview_by_layer[key0], 0.0)
                            picked_idx = ln_try
                    # 2) If no direct match (2-ply indices often differ from FG),
                    # pick the closest FG layer of same role by flute match, then gsm distance
                    if picked_idx is None:
                        try:
                            layers = fg_layers_by_role.get(role, [])
                            def _to_float(v):
                                try:
                                    return float(v)
                                except Exception:
                                    return None
                            g = _to_float(line.get("gsm")) or 0.0
                            fl = (line.get("flute") or None)
                            best = None
                            for a in layers:
                                if fl and a.get("flute") and str(a.get("flute")).lower() != str(fl).lower():
                                    continue
                                diff = abs((a.get("gsm") or 0.0) - g) if g else 0.0
                                keyb = (diff, a.get("idx") or 0)
                                if best is None or keyb < best[0]:
                                    best = (keyb, a)
                            if best and best[1]:
                                idx_eff = best[1].get("idx")
                                if idx_eff:
                                    key_eff = (role, idx_eff)
                                    if key_eff in fg_qty_preview_by_layer:
                                        qty = _safe_float(fg_qty_preview_by_layer[key_eff], 0.0)
                        except Exception:
                            pass
                mapped.append({
                    "role": role,
                    "product": m["product"],
                    "uom": m["uom"],
                    "qty": qty,
                    "gsm": gsm,
                    "flute": flute,
                    "bf": bf,
                    "layer_no": ln_try,
                    "source_idx": source_idx,
                })
            return mapped

        def _preview_mapped_lines():
            return _map_rm_lines({"rm_lines": rm_lines, "metrics": metrics})

        def _preview_role_lines(role_name):
            role_name = (role_name or "").lower()
            return [
                mapped
                for mapped in _preview_mapped_lines()
                if (mapped.get("role") or "").lower() == role_name
            ]

        def _stage_mapping_index(op_name):
            mapping = getattr(spec, "stage_mapping_id", False)
            if not mapping:
                return None
            target = (op_name or "").strip().lower()
            sequence = 0
            for line in mapping.line_ids.filtered("enabled").sorted(
                key=lambda l: (l.sequence or 0, l.id)
            ):
                if line.skip_policy == "ignore" or not line.operation_type_id:
                    continue
                name = (
                    line.operation_type_id.display_name
                    or line.operation_type_id.name
                    or ""
                ).strip().lower()
                if name == target:
                    return sequence
                sequence += 1
            return None

        def _stage_mapping_has_any(*op_names):
            mapping = getattr(spec, "stage_mapping_id", False)
            if not mapping:
                return True
            wanted = {
                (name or "").strip().lower()
                for name in op_names
                if (name or "").strip()
            }
            for line in mapping.line_ids.filtered("enabled").sorted(
                key=lambda l: (l.sequence or 0, l.id)
            ):
                if line.skip_policy == "ignore" or not line.operation_type_id:
                    continue
                for op_type in (line.operation_type_id, line.merge_into_operation_type_id):
                    if not op_type:
                        continue
                    name = (op_type.display_name or op_type.name or "").strip().lower()
                    if name in wanted:
                        return True
            return False

        def _printing_after_slot_die():
            printing_idx = _stage_mapping_index("Printing")
            slot_idx = _stage_mapping_index("Slot/Die")
            return (
                printing_idx is not None
                and slot_idx is not None
                and printing_idx > slot_idx
            )

        def _qty_uom_preserving_small_weight(qty, uom):
            return self._corr_qty_uom_preserving_small_weight(qty, uom)

        preview_lamination_lines = (
            _preview_role_lines("lamination")
            if _stage_mapping_has_any("Lamination", "Film Lamination")
            else []
        )
        preview_ink_lines = (
            _preview_role_lines("ink") if _stage_mapping_has_any("Printing") else []
        )
        ink_on_die_stage = bool(preview_ink_lines and _printing_after_slot_die())
        lamination_material_op = (
            "Film Lamination"
            if _stage_mapping_has_any("Film Lamination")
            else "Lamination"
        )

        # Build SFG keys and products/BoMs
        def _fmt_num_compact(val):
            """Format GSM/BF numbers nicely for SFG naming."""
            try:
                f = float(val)
            except Exception:
                return str(val)
            if float(int(f)) == f:
                return str(int(f))
            return ("{:.1f}".format(f)).rstrip("0").rstrip(".")

        def _stack_sig_2ply(lin, med):
            """Return the standard 2-ply stack signature, e.g. 'B:140/125 GSM BF:20/23'."""
            try:
                flute = (med.get("flute") or "") if isinstance(med, dict) else ""
            except Exception:
                flute = ""
            try:
                gsm_lin = lin.get("gsm") if isinstance(lin, dict) else None
                gsm_med = med.get("gsm") if isinstance(med, dict) else None
            except Exception:
                gsm_lin = gsm_med = None
            try:
                bf_lin = lin.get("bf") if isinstance(lin, dict) else None
                bf_med = med.get("bf") if isinstance(med, dict) else None
            except Exception:
                bf_lin = bf_med = None
            g_vals = [v for v in (gsm_lin, gsm_med) if v not in (None, "", 0, 0.0)]
            b_vals = [v for v in (bf_lin, bf_med) if v not in (None, "", 0, 0.0)]
            parts = []
            if g_vals:
                parts.append(f"{'/'.join(_fmt_num_compact(v) for v in g_vals)} GSM")
            if b_vals:
                parts.append(f"BF:{'/'.join(_fmt_num_compact(v) for v in b_vals)}")
            if not parts:
                return ""
            label = flute or "Stack"
            return f"{label}:{' '.join(parts)}"

        def _norm_sig_text(value):
            try:
                txt = str(value or "").strip()
            except Exception:
                txt = ""
            return txt.lower()

        def _norm_sig_num(value):
            try:
                return _fmt_num_compact(value)
            except Exception:
                return str(value or "")

        def _norm_sig_ref(value):
            try:
                if hasattr(value, "id"):
                    return str(int(value.id or 0))
            except Exception:
                pass
            try:
                return str(int(value or 0))
            except Exception:
                return str(value or "")

        def _stack_sig_2ply_key(lin, med, mapped_lin=None, mapped_med=None):
            """Return a stable manufacturable signature for dedupe/reuse decisions."""
            lin_color = (lin.get("color") if isinstance(lin, dict) else "") or ""
            med_color = (med.get("color") if isinstance(med, dict) else "") or ""
            lin_sig = [
                f"tmpl:{_norm_sig_ref((lin.get('material_template_id') if isinstance(lin, dict) else None) or 0)}",
                f"prod:{_norm_sig_ref((lin.get('material_product_id') if isinstance(lin, dict) else None) or 0)}",
                f"gsm:{_norm_sig_num((lin.get('gsm') if isinstance(lin, dict) else None) or 0)}",
                f"bf:{_norm_sig_num((lin.get('bf') if isinstance(lin, dict) else None) or 0)}",
                f"color:{_norm_sig_text(lin_color)}",
            ]
            med_sig = [
                f"tmpl:{_norm_sig_ref((med.get('material_template_id') if isinstance(med, dict) else None) or 0)}",
                f"prod:{_norm_sig_ref((med.get('material_product_id') if isinstance(med, dict) else None) or 0)}",
                f"gsm:{_norm_sig_num((med.get('gsm') if isinstance(med, dict) else None) or 0)}",
                f"bf:{_norm_sig_num((med.get('bf') if isinstance(med, dict) else None) or 0)}",
                f"flute:{_norm_sig_text((med.get('flute') if isinstance(med, dict) else '') or '')}",
                f"color:{_norm_sig_text(med_color)}",
            ]
            return f"L[{ '|'.join(lin_sig) }]|M[{ '|'.join(med_sig) }]"

        def _stack_sig_4ply_from_pairs(pairs):
            """Signature for 4-ply using up to first two 2-ply pairs, grouped by flute."""
            sig_parts = []
            for lin, med in (pairs or [])[:2]:
                s = _stack_sig_2ply(lin, med)
                if s:
                    sig_parts.append(s)
            return "; ".join(sig_parts)

        def _get_or_create_sfg(name, stage, key):
            Product = self.env["product.template"].sudo().with_context(skip_corr_autobom=True)
            Unit = self.env.ref("uom.product_uom_unit")
            stage_type = self.env["corr.stage.type"].find_from_code(stage)
            stage_type_vals = {"stage_type_id": stage_type.id} if stage_type else {}

            def _manufacture_route():
                Route = self.env["stock.route"].sudo()
                for xmlid in [
                    "mrp.route_warehouse0_manufacture",
                    "mrp.route_manufacture",
                ]:
                    try:
                        rec = self.env.ref(xmlid)
                        if rec:
                            return rec
                    except Exception:
                        pass
                r = Route.search([("name", "ilike", "Manufacture"), ("product_selectable", "=", True)], limit=1)
                return r

            def _mto_route():
                Route = self.env["stock.route"].sudo()
                for xmlid in [
                    "stock.route_warehouse0_mto",
                    "stock.route_mto",
                ]:
                    try:
                        rec = self.env.ref(xmlid)
                        if rec:
                            return rec
                    except Exception:
                        pass
                r = Route.search([("name", "ilike", "Make To Order"), ("product_selectable", "=", True)], limit=1)
                return r

            try:
                icp = self.env["ir.config_parameter"].sudo()
                enable_mto = icp.get_param("corr_mrp_bridge.enable_mto_for_sfg", default="0") in ("1", "true", "True")
            except Exception:
                enable_mto = False

            man_route = _manufacture_route()
            mto_route = _mto_route()

            tmpl = Product.search([("corr_sfg_key", "=", key)], limit=1)
            if not tmpl:
                vals_create = {
                    "name": name,
                    # This DB uses 'consu' + is_storable for stock tracking
                    "type": "consu",
                    "is_storable": True,
                    "uom_id": Unit.id,
                    "uom_po_id": Unit.id,
                    "purchase_ok": False,
                    # Default: staged SFGs are not sold directly; product managers can override.
                    "sale_ok": False,
                    "product_role": "sfg",
                    "corr_sfg_key": key,
                    "corr_stage": stage,
                    **stage_type_vals,
                }
                route_ids = []
                if man_route:
                    route_ids.append(man_route.id)
                if enable_mto and mto_route:
                    route_ids.append(mto_route.id)
                if route_ids:
                    vals_create["route_ids"] = [(6, 0, route_ids)]
                tmpl = Product.create(vals_create)
            else:
                # Optionally update the SFG product name to reflect current naming.
                # For top-sheet SFGs in Roll mode, always keep the name in sync so
                # spec details (e.g., '180 gsm, BF 18') remain visible.
                try:
                    icp = self.env["ir.config_parameter"].sudo()
                    do_rename_cfg = icp.get_param("corr_mrp_bridge.update_sfg_name_on_apply", default="0") in (
                        "1",
                        "true",
                        "True",
                    )
                except Exception:
                    do_rename_cfg = False
                current_name = (tmpl.name or "").strip()
                generated_template_name = current_name.lower().startswith("template:")
                do_rename = bool(
                    do_rename_cfg
                    or diecut_yield_sheet_flag
                    or generated_template_name
                    or stage in ("top_sheet_cut", "top_sheet_printed", "top_sheet_film", "cutting", "ts_cut")
                )
                vals_up = {"corr_stage": stage, **stage_type_vals}
                # Ensure existing SFGs are classified and stock-tracked
                try:
                    if getattr(tmpl, "product_role", None) != "sfg":
                        vals_up["product_role"] = "sfg"
                    if not bool(getattr(tmpl, "is_storable", False)):
                        vals_up["is_storable"] = True
                    route_cmds = []
                    if man_route and man_route not in tmpl.route_ids:
                        route_cmds.append((4, man_route.id))
                    if mto_route:
                        if enable_mto and mto_route not in tmpl.route_ids:
                            route_cmds.append((4, mto_route.id))
                        if not enable_mto and mto_route in tmpl.route_ids:
                            route_cmds.append((3, mto_route.id))
                    if route_cmds:
                        vals_up["route_ids"] = route_cmds
                except Exception:
                    pass
                if do_rename and (tmpl.name or "") != (name or ""):
                    vals_up["name"] = name
                if vals_up:
                    tmpl.write(vals_up)
            prod = tmpl.product_variant_id
            Bom = self.env["mrp.bom"].sudo()
            bom = Bom.search([("product_tmpl_id", "=", tmpl.id), ("type", "=", "normal")], limit=1)
            if not bom:
                bom = Bom.create({
                    "product_tmpl_id": tmpl.id,
                    "product_qty": 1.0,
                    "type": "normal",
                    "consumption": "warning",
                    "corr_stage": stage,
                    "corr_sfg_key": key,
                    **stage_type_vals,
                })
            else:
                bom.write({
                    "corr_stage": stage,
                    "corr_sfg_key": key,
                    "consumption": "warning",
                    **stage_type_vals,
                })
            return prod, bom

        # Keys use mapped product ids where possible + geometry
        def _key_2ply(lin, med):
            # Try to map to products (liner/medium)
            lin_detail = {
                "idx": 1,
                "role": "liner",
                "gsm": lin.get("gsm"),
                "bf": lin.get("bf"),
                "color": lin.get("color"),
            }
            med_detail = {
                "idx": 2,
                "role": "medium",
                "gsm": med.get("gsm"),
                "bf": med.get("bf"),
                "flute": med.get("flute"),
                "color": med.get("color"),
            }
            m_lin, _d1, _d2, _d3 = self._map_component(
                {
                    "role": "liner",
                    "layer_no": 1,
                    "gsm": lin.get("gsm"),
                    "bf": lin.get("bf"),
                    "color": lin.get("color"),
                },
                {"ply_detail": [lin_detail]},
            )
            m_med, _d4, _d5, _d6 = self._map_component(
                {
                    "role": "medium",
                    "layer_no": 2,
                    "gsm": med.get("gsm"),
                    "bf": med.get("bf"),
                    "flute": med.get("flute"),
                    "color": med.get("color"),
                },
                {"ply_detail": [med_detail]},
            )
            pair_sig = _stack_sig_2ply_key(lin, med, mapped_lin=m_lin, mapped_med=m_med)
            return f"sfg2|{pair_sig}|{deck}x{leng}"

        def _key_4ply(k2a, k2b):
            kset = sorted([k2a, k2b])
            return f"sfg4|{kset[0]}+{kset[1]}|{deck}x{leng}"

        def _key_board(k4):
            top_sku = getattr(spec.top_sheet_product_id, "id", 0) or 0
            icp = self.env["ir.config_parameter"].sudo()
            try:
                ups = int(float((spec.extra_params_json or {}).get("printing_ups") or icp.get_param("corr_mrp_bridge.default_printing_ups") or 1))
            except Exception:
                ups = 1
            return f"sfgB|{k4}|TOP:{top_sku}|UPS:{ups}|{deck}x{leng}"

        def _key_die(kb):
            tool = 0
            try:
                tool = getattr(product_tmpl, "primary_diecut_id", False) and product_tmpl.primary_diecut_id.code or 0
            except Exception:
                tool = 0
            return f"sfgD|{kb}|TOOL:{tool}|{deck}x{leng}"

        def _key_peeling(kd):
            return f"sfgP|{kd}"

        # Build 2-ply SFGs (dedupe identical pairs)
        # Compute how many times each 2-ply key is used in the stack so that
        # per-sheet BoM quantities can be normalized when a 2-ply SFG is
        # reused (e.g., 5-ply/7-ply/9-ply stacks). This keeps total paper
        # consumption aligned with engine per-box results while leaving
        # single-use cases (e.g., 3-ply) unchanged.
        uses_2ply = {}
        for (lin_tmp, med_tmp) in (pairs_2ply or []):
            try:
                k_tmp = _key_2ply(lin_tmp, med_tmp)
            except Exception:
                continue
            uses_2ply[k_tmp] = uses_2ply.get(k_tmp, 0) + 1

        keys_2ply = []
        products_2ply = []
        boms_2ply = []
        for (lin, med), r2 in zip(pairs_2ply, res_2ply):
            key2 = _key_2ply(lin, med)
            if key2 in keys_2ply:
                # Reuse existing product/bom
                idx = keys_2ply.index(key2)
                products_2ply.append(products_2ply[idx])
                boms_2ply.append(boms_2ply[idx])
                # Also append the duplicate key so downstream (4-ply/board keying)
                # can still reference two 2-ply inputs without IndexError
                keys_2ply.append(key2)
                continue
            # Human-readable size label for naming
            size_label_2 = size_label_sheet if use_sheet_label else size_label_box
            # Option B: include GSM/BF grouped by flute so 2-ply SFGs with
            # different stacks but same dimensions get distinct names.
            sig2 = _stack_sig_2ply(lin, med)
            if sig2:
                name2 = f"2-ply {size_label_2} ({sig2})"
            else:
                name2 = f"2-ply {size_label_2}"
            prod2, bom2 = _get_or_create_sfg(name2, "2-ply", key2)
            try:
                if float(bom2.product_qty or 0.0) != 1.0:
                    bom2.with_context(skip_corr_bom_qty_sync=True).write({"product_qty": 1.0})
            except Exception:
                pass
            # Normalization factor: number of times this 2-ply SFG is used per FG.
            uses = max(1, uses_2ply.get(key2, 1))
            # Upsert BoM lines for 2-ply
            # 1) Paper (liner/medium): always use FG preview-basis qty by role+layer
            #    to keep 2-ply consistent with the Preview, then convert to per-sheet.
            keep = set()
            paper_added = set()
            for role, source_layer in (("liner", lin), ("medium", med)):
                lyr = source_layer.get("idx") if isinstance(source_layer, dict) else 0
                try:
                    lyr = int(lyr or 0)
                except Exception:
                    lyr = 0
                # Resolve product/uom from FG layer map (same SKU as Preview)
                prod = uom = None
                hint = fg_layer_map.get((role, lyr))
                if hint:
                    prod = hint.get("product")
                    uom = hint.get("uom")
                if not (prod and uom):
                    # Fallback: map by attributes if FG map missing
                    m, _g, _f, _b = self._map_component({"role": role, "layer_no": lyr}, metrics)
                    if m:
                        prod = m.get("product")
                        uom = m.get("uom")
                # Resolve qty from FG preview lines
                qty_base = fg_qty_preview_by_layer.get((role, lyr))
                qty_for_bom = None
                if diecut_yield_sheet_flag and sheet_flag:
                    qty_for_bom = _whole_sheet_paper_qty(source_layer)
                elif qty_base is not None:
                    qty_for_bom = _per_sheet(qty_base) / uses
                if (prod and uom) and qty_for_bom is not None:
                    key = f"{role}:{lyr}:{prod.id}"
                    keep.add(key)
                    line = bom2.bom_line_ids.filtered(lambda l, k=key: (l.corr_component_key or "") == k)
                    qty_line, uom_line = _qty_uom_preserving_small_weight(
                        qty_for_bom,
                        uom,
                    )
                    vals = {
                        "product_id": prod.id,
                        # Normalize by number of uses so that total
                        # consumption across all uses matches engine
                        # per-box totals, while keeping layer-specific keys.
                        "product_qty": qty_line,
                        "product_uom_id": uom_line.id,
                        "corr_component_key": key,
                        "corr_role": role,
                        "corr_flute_code": (hint and hint.get("flute")) or (med.get("flute") if role == "medium" and isinstance(med, dict) else None),
                        "corr_gsm": (hint and hint.get("gsm")) or (lin.get("gsm") if role == "liner" and isinstance(lin, dict) else (med.get("gsm") if role == "medium" and isinstance(med, dict) else 0.0)) or 0.0,
                        "corr_layer_no": lyr,
                        "bom_id": bom2.id,
                    }
                    if line:
                        line.write(vals)
                    else:
                        self.env["mrp.bom.line"].create(vals)
                    paper_added.add(role)

            # 2) Adhesive: keep from 2-ply staged compute (r2). r2 is per-sheet
            for mapped in _map_rm_lines(r2):
                if mapped["role"] != "adhesive":
                    continue
                key = f"adhesive:{mapped['product'].id}"
                keep.add(key)
                line = bom2.bom_line_ids.filtered(lambda l, k=key: (l.corr_component_key or "") == k)
                vals = {
                    "product_id": mapped["product"].id,
                    "product_qty": float(mapped["qty"] or 0.0),
                    "product_uom_id": mapped["uom"].id,
                    "corr_component_key": key,
                    "corr_role": "adhesive",
                    "bom_id": bom2.id,
                }
                if line:
                    line.write(vals)
                else:
                    self.env["mrp.bom.line"].create(vals)

            # 3) Paper fallback: if any of liner/medium was not added from FG
            #    preview map (e.g., missing layer indices), fallback to the
            #    staged 2-ply compute lines for those roles to ensure SFG has
            #    both papers.
            if paper_added != {"liner", "medium"}:
                for mapped in _map_rm_lines(r2):
                    r = mapped.get("role")
                    if r not in ("liner", "medium"):
                        continue
                    if r in paper_added:
                        continue
                    source_layer = lin if r == "liner" else med
                    if diecut_yield_sheet_flag and sheet_flag:
                        fallback_qty = _whole_sheet_paper_qty(source_layer)
                    else:
                        fallback_qty = float(mapped["qty"] or 0.0) / uses
                    layer_no = int(mapped.get("layer_no") or 0)
                    key = f"{r}:{layer_no}:{mapped['product'].id}" if layer_no else f"{r}:{mapped['product'].id}"
                    keep.add(key)
                    line = bom2.bom_line_ids.filtered(lambda l, k=key: (l.corr_component_key or "") == k)
                    qty_line, uom_line = _qty_uom_preserving_small_weight(
                        fallback_qty,
                        mapped["uom"],
                    )
                    vals = {
                        "product_id": mapped["product"].id,
                        # r2 paper qty is per-sheet already; normalize by uses
                        "product_qty": qty_line,
                        "product_uom_id": uom_line.id,
                        "corr_component_key": key,
                        "corr_role": r,
                        "corr_flute_code": mapped.get("flute"),
                        "corr_gsm": mapped.get("gsm") or 0.0,
                        "bom_id": bom2.id,
                    }
                    if line:
                        line.write(vals)
                    else:
                        self.env["mrp.bom.line"].create(vals)
            # Remove obsolete
            to_unlink = bom2.bom_line_ids.filtered(lambda l: (l.corr_component_key or "")[0:3] in {"lin", "med", "adh"} and (l.corr_component_key or "") not in keep)
            to_unlink.unlink()
            # Operations
            if self.include_operations:
                # Use the medium's flute for selection; deckle from geometry
                try:
                    flute_val = (med.get("flute") if isinstance(med, dict) else None) or (lin.get("flute") if isinstance(lin, dict) else None)
                except Exception:
                    flute_val = None
                m2 = (r2.get("metrics") if isinstance(r2, dict) else {})
                m2 = _metrics_with_geometry(m2, metrics)
                self._upsert_ops(bom2, ["Corrugation"], flute=flute_val, deckle_cm=(deckle_mm/10.0 if deckle_mm else None), metrics=m2)
                _link_lines_to_op(bom2, ["liner:", "medium:", "adhesive:"], "Corrugation")
            keys_2ply.append(key2)
            products_2ply.append(prod2)
            boms_2ply.append(bom2)

        # Build 4-ply SFG (if applicable)
        prod4 = bom4 = None
        if layers_4ply:
            # Be robust when both 2-ply pairs resolve to the same 2-ply key
            # (identical liner/medium pairs). In that case, still build a 4-ply
            # by referencing the same 2-ply SFG twice.
            try:
                k2a = keys_2ply[0]
            except Exception:
                k2a = ""
            try:
                k2b = keys_2ply[1]
            except Exception:
                k2b = k2a
            key4 = _key_4ply(k2a, k2b)
            # Human-readable size label for naming
            size_label_4 = size_label_sheet if use_sheet_label else size_label_box
            # Option B: include GSM/BF grouped by flute using first two 2-ply
            # pairs so 4-ply SFGs with different stacks but same dimensions
            # get distinct names.
            sig4 = _stack_sig_4ply_from_pairs(pairs_2ply)
            if sig4:
                name4 = f"4-ply {size_label_4} ({sig4})"
            else:
                name4 = f"4-ply {size_label_4}"
            prod4, bom4 = _get_or_create_sfg(name4, "4-ply", key4)
            # Lines: 2 x 2-ply SFG + combine adhesive
            keep4 = set()
            # Two 2-ply inputs: aggregate counts so identical 2-ply product
            # results in a single BoM line with qty 2.0 instead of two lines
            # clobbering each other by the same corr_component_key.
            counts = {}
            for p2 in products_2ply[:2]:
                counts[p2.id] = counts.get(p2.id, 0) + 1
            for pid, cnt in counts.items():
                k = f"sfg2:{pid}"
                keep4.add(k)
                line = bom4.bom_line_ids.filtered(lambda l, kk=k: (l.corr_component_key or "") == kk)
                # Fetch product record from any of the prepared products_2ply
                p2 = next((x for x in products_2ply if x.id == pid), None)
                if not p2:
                    # Fallback browse if not found (defensive)
                    p2 = self.env["product.product"].browse(pid)
                qty_needed = float(cnt)
                vals = {
                    "product_id": pid,
                    "product_qty": qty_needed,
                    "product_uom_id": p2.uom_id.id if p2 else False,
                    "corr_component_key": k,
                    "bom_id": bom4.id,
                }
                if line:
                    line.write(vals)
                else:
                    self.env["mrp.bom.line"].create(vals)
            # Combine adhesive for first two pairs (single product line)
            if adh_combine_first > 0:
                # Map an adhesive product via defaults
                mp, _d7, _d8, _d9 = self._map_component({"role": "adhesive"}, {})
                pid = mp and mp.get("product") and mp["product"].id or False
                uom = mp and mp.get("uom") and mp["uom"].id or False
                if pid:
                    k = f"adhesive:{pid}"
                    keep4.add(k)
                    line = bom4.bom_line_ids.filtered(lambda l, kk=k: (l.corr_component_key or "") == kk)
                    vals = {
                        "product_id": pid,
                        "product_qty": _per_sheet(adh_combine_first),
                        "product_uom_id": uom,
                        "corr_component_key": k,
                        "bom_id": bom4.id,
                    }
                    if line:
                        line.write(vals)
                    else:
                        self.env["mrp.bom.line"].create(vals)
            # Remove obsolete
            to_unlink = bom4.bom_line_ids.filtered(lambda l: (l.corr_component_key or "") not in keep4)
            to_unlink.unlink()
            if self.include_operations:
                self._upsert_ops(bom4, ["Combine 4-ply"], deckle_cm=(deckle_mm/10.0 if deckle_mm else None), metrics=(res_4ply.get("metrics") if isinstance(res_4ply, dict) else {}))
                _link_lines_to_op(bom4, ["sfg2:", "adhesive:"], "Lamination")

        # Build final board SFG (5-ply/7-ply etc.)
        keyB = _key_board(_key_4ply(keys_2ply[0], keys_2ply[1]) if len(keys_2ply) >= 2 else (keys_2ply[0] if keys_2ply else ""))
        # Base size label (sheet-level when active, otherwise box-level)
        size_label = size_label_sheet if use_sheet_label else size_label_box
        # Enrich board name with top-sheet info so boards for different top sheets
        # are easy to distinguish even when geometry and stack are identical.
        top_label = None
        try:
            # Prefer explicit roll code when Top Layer Mode is Roll
            xp = getattr(spec, "extra_params_json", None) or {}
            if isinstance(xp, str):
                try:
                    xp = json.loads((xp or "{}").strip())
                except Exception:
                    xp = {}
            top_mode = str(xp.get("top_layer_mode") or "").strip().lower()
            is_roll = "roll" in top_mode

            if is_roll:
                roll_code = (getattr(spec, "top_sheet_roll_code", "") or "").strip()
                if roll_code:
                    top_label = roll_code

            ts_prod = getattr(spec, "top_sheet_product_id", False)
            if not top_label and ts_prod:
                top_label = (ts_prod.default_code or ts_prod.name or "").strip() or None
            if not top_label:
                code = (getattr(spec, "top_sheet_code", "") or "").strip()
                if code:
                    top_label = code
        except Exception:
            top_label = None
        if top_label:
            # Example: "Board 45x70.5 cm (top-10-897-751-0022)"
            nameB = f"Board {size_label} ({top_label})"
        else:
            nameB = f"Board {size_label}"
        prodB, bomB = _get_or_create_sfg(nameB, "board", keyB)
        try:
            if float(bomB.product_qty or 0.0) != 1.0:
                bomB.with_context(skip_corr_bom_qty_sync=True).write({"product_qty": 1.0})
        except Exception:
            pass
        keepB = set()
        # Always include available 2-ply inputs directly on the board BoM
        # (even when a 4-ply SFG has been built), so the board takes 2-ply
        # stages + top sheet instead of a separate 4-ply stage. Aggregate
        # duplicates so identical 2-ply products get qty 2.0, 3.0, etc.
        counts_2ply = {}
        for p2 in (products_2ply or []):
            counts_2ply[p2.id] = counts_2ply.get(p2.id, 0) + 1
        for pid, cnt in counts_2ply.items():
            kk = f"sfg2:{pid}"
            keepB.add(kk)
            line = bomB.bom_line_ids.filtered(lambda l, k=kk: (l.corr_component_key or "") == k)
            # Fetch product record from any of the prepared products_2ply
            p2 = next((x for x in products_2ply if x.id == pid), None)
            vals = {
                "product_id": pid,
                "product_qty": float(cnt),
                "product_uom_id": p2.uom_id.id if p2 else False,
                "corr_component_key": kk,
                "bom_id": bomB.id,
            }
            line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
        # Top sheet staged pipeline (Roll -> Sheet -> Printed [-> Film])
        # Use the same Roll detection semantics as single-FG flows so staged
        # two-piece specs don't drop the top-roll component when the UI field
        # is set but the JSON payload is missing or stale.
        def _is_roll_pipeline():
            try:
                xp = getattr(spec, "extra_params_json", None) or {}
                if isinstance(xp, str):
                    try:
                        xp = json.loads((xp or "{}").strip())
                    except Exception:
                        xp = {}
                raw = xp.get("top_layer_mode")
                if raw is not None and "roll" in str(raw).strip().lower():
                    return True
                ui_mode = getattr(spec, "top_layer_mode_ui", False)
                if ui_mode and "roll" in str(ui_mode).strip().lower():
                    return True
                raw_fallback = xp.get("top_layer")
                if raw_fallback is not None and "roll" in str(raw_fallback).strip().lower():
                    return True
                return False
            except Exception:
                return False
        pipeline = _is_roll_pipeline()
        printing_needed = bool(getattr(spec, "printing_required", False))
        prodTS_A = bomTS_A = None
        prodTS_B = bomTS_B = None
        top_sheet_stage_prod = None
        top_sheet_stage_bom = None
        top_sheet_stage_key_prefix = None
        top_sheet_stage_source_tag = None
        direct_top_sheet_added = False
        size_label_top = size_label_sheet if use_sheet_label else size_label_box
        sig_top = ""
        boxes_per_sheet = 1
        try:
            layout_m = ((metrics or {}).get("layout") or {})
            boxes_per_sheet = int(layout_m.get("boxes_per_sheet") or 1)
        except Exception:
            boxes_per_sheet = 1
        if boxes_per_sheet <= 0:
            boxes_per_sheet = 1

        def _resolve_roll_top_sheet():
            # Resolve top sheet roll product:
            # 1) explicit spec binding
            # 2) attribute-based picker
            # 3) fallback to mapped outermost liner product from FG stack
            top_prod = getattr(spec, "top_sheet_product_id", False)
            top_liner_idx_val = 0
            if not top_prod:
                # Attempt auto-pick by attributes (GSM/Color/BF) and deckle, scoped to roll category
                try:
                    # Attribute targets from spec extra params, else fallback to outermost liner
                    xp = getattr(spec, "extra_params_json", None) or {}
                    ts_gsm = None
                    ts_color = None
                    ts_bf = None
                    try:
                        if xp.get("top_sheet_gsm") is not None:
                            ts_gsm = float(xp.get("top_sheet_gsm"))
                    except Exception:
                        ts_gsm = None
                    try:
                        valc = xp.get("top_sheet_color")
                        ts_color = str(valc) if valc is not None else None
                    except Exception:
                        ts_color = None
                    try:
                        if xp.get("top_sheet_bf") is not None:
                            ts_bf = float(xp.get("top_sheet_bf"))
                    except Exception:
                        ts_bf = None
                    if ts_gsm is None or not ts_color:
                        # Fallback: outermost liner (highest idx) from ply_detail
                        try:
                            liners = fg_layers_by_role.get("liner") or []
                            if not liners:
                                plys = list((metrics.get("ply_detail") or []))
                                liners = [
                                    p
                                    for p in plys
                                    if (p.get("role") or "").lower() == "liner"
                                    and p.get("idx")
                                ]
                            if liners:
                                top = max(liners, key=lambda p: int(p.get("idx") or 0))
                                if ts_gsm is None:
                                    ts_gsm = float(top.get("gsm") or 0.0)
                                if not ts_color:
                                    ts_color = top.get("color") or None
                                if ts_bf is None and top.get("bf") is not None:
                                    ts_bf = float(top.get("bf") or 0.0)
                        except Exception:
                            pass
                    # Compute deckle target (cm) across slitting UPS
                    deckle_total_cm = None
                    try:
                        su_raw = xp.get("liner_slitting_ups") or xp.get("liner_slitting_ups_ui")
                        slitting_ups = int(float(su_raw or 1)) if su_raw is not None else 1
                        if slitting_ups <= 0:
                            slitting_ups = 1
                        deckle_total_cm = (float(deckle_mm or 0.0) / 10.0) * float(slitting_ups)
                    except Exception:
                        deckle_total_cm = None
                    deckle_tol_cm = 0.5
                    try:
                        icp = self.env["ir.config_parameter"].sudo()
                        v = icp.get_param("planning.deckle_tolerance_cm")
                        if v is not None:
                            deckle_tol_cm = float(v)
                    except Exception:
                        pass
                    # Use attribute-based picker (liner role) with category scoping for rolls
                    Map = self.env["corr.material.product.map"].sudo()
                    auto = None
                    if ts_gsm is not None or ts_color is not None:
                        auto = Map.auto_find_variant_v2(
                            "liner",
                            flute=None,
                            gsm=ts_gsm,
                            bf=ts_bf,
                            color=ts_color,
                            deckle_total_cm=deckle_total_cm,
                            deckle_tolerance_cm=deckle_tol_cm,
                        )
                    if auto and auto.get("product"):
                        top_prod = auto.get("product")
                except Exception:
                    top_prod = getattr(spec, "top_sheet_product_id", False)
            if not top_prod:
                # Fallback: reuse the mapped outermost liner SKU as top-sheet roll
                try:
                    liners_fg = fg_layers_by_role.get("liner", [])
                    if liners_fg:
                        # Prefer the outermost liner that actually resolved to a
                        # product. When the true top liner has no exact product
                        # match (for example BF differs from available variants),
                        # fall back to the nearest mapped liner instead of
                        # dropping the top-roll component entirely.
                        for outer in sorted(liners_fg, key=lambda a: a.get("idx") or 0, reverse=True):
                            p = outer.get("product")
                            if p:
                                top_prod = p
                                break
                except Exception:
                    top_prod = getattr(spec, "top_sheet_product_id", False)
            # Enrich top-sheet SFG names with paper signature (e.g. '180 GSM BF:18')
            sig_val = ""
            try:
                # Derive spec suffix purely from ply_detail (outermost liner)
                gsm_val = None
                bf_val = None
                try:
                    plyd = (metrics.get("ply_detail") or [])
                    liners = [p for p in plyd if (p.get("role") or "").lower() == "liner" and p.get("idx")]
                    if liners:
                        top_liner = max(liners, key=lambda p: int(p.get("idx") or 0))
                        try:
                            top_liner_idx_val = int(top_liner.get("idx") or 0)
                        except Exception:
                            top_liner_idx_val = 0
                        if top_liner.get("gsm") is not None:
                            gsm_val = float(top_liner.get("gsm"))
                        if top_liner.get("bf") is not None:
                            bf_val = float(top_liner.get("bf"))
                except Exception:
                    gsm_val = bf_val = None
                parts = []
                if gsm_val is not None:
                    parts.append(f"{_fmt_num_compact(gsm_val)} GSM")
                if bf_val is not None:
                    parts.append(f"BF:{_fmt_num_compact(bf_val)}")
                sig_val = " ".join(parts)
            except Exception:
                sig_val = ""
            # Derive kg-per-sheet roll consumption from the same RM line used
            # by the compute preview so Board and staged SFG quantities stay aligned.
            qty_top = 1.0
            try:
                top_total_kg = 0.0
                # When the engine aggregates all liners into one RM line, split
                # that line back into lower-pair vs roll-top quantities using
                # the same layer matching logic as the flat BoM path.
                try:
                    board_metrics = (res_board.get("metrics") if isinstance(res_board, dict) else {}) or {}
                    for rm_line in _iter_rm(res_board):
                        if (rm_line.get("role") or "").lower() != "liner":
                            continue
                        mapped_line, _gsm, _flute, bf_line = self._map_component(rm_line, board_metrics)
                        if not mapped_line:
                            continue
                        split_rows = self._split_liner_component_for_bom(
                            rm_line,
                            board_metrics,
                            mapped_line,
                            bf_line,
                            top_liner_idx_val,
                        )
                        top_row = next(
                            (row for row in (split_rows or []) if (row.get("corr_role") or "").lower() == "top_sheet"),
                            None,
                        )
                        if top_row and _safe_float(top_row.get("qty"), 0.0) > 0.0:
                            if not top_prod:
                                top_prod = mapped_line.get("product")
                            top_total_kg = _safe_float(top_row.get("qty"), 0.0)
                            break
                        prod_m = mapped_line.get("product")
                        if prod_m and top_prod and prod_m.id == top_prod.id:
                            top_total_kg = _safe_float(rm_line.get("qty"), 0.0)
                            break
                except Exception:
                    top_total_kg = 0.0
                try:
                    qty_fg = float(self.quantity or 1.0)
                except Exception:
                    qty_fg = 1.0
                if qty_fg <= 0:
                    qty_fg = 1.0
                top_kg_per_box = top_total_kg / qty_fg if top_total_kg > 0 else 0.0
                qty_calc = top_kg_per_box * boxes_per_sheet
                if qty_calc > 0:
                    qty_top = qty_calc
            except Exception:
                qty_top = 1.0
            return top_prod, qty_top, sig_val, top_liner_idx_val

        roll_top_prod = False
        roll_top_qty = 1.0
        roll_top_layer_no = 0
        if pipeline:
            roll_top_prod, roll_top_qty, sig_top, roll_top_layer_no = _resolve_roll_top_sheet()

        if pipeline and roll_top_prod:
            keyTA = f"sfgTA|TOP:{roll_top_prod.id}|UPS:{int(ups_val or 1)}|{deck}x{leng}"
            nameTA = f"Top-sheet Cut {size_label_top}" + (f" ({sig_top})" if sig_top else "")
            prodTS_A, bomTS_A = _get_or_create_sfg(nameTA, "top_sheet_cut", keyTA)
            # Lines: top sheet product. For Roll mode, derive kg-per-sheet
            # from the same per-box figure used in the compute preview so
            # that BoM quantities stay aligned with engine output.
            keepTA = set()
            qty_top = roll_top_qty
            k = f"top_sheet:{roll_top_prod.id}"
            keepTA.add(k)
            line = bomTS_A.bom_line_ids.filtered(lambda l, kk=k: (l.corr_component_key or "") == kk)
            vals = {
                "product_id": roll_top_prod.id,
                "product_qty": qty_top,
                "product_uom_id": roll_top_prod.uom_id.id,
                "corr_component_key": k,
                "corr_role": "top_sheet",
                "corr_layer_no": roll_top_layer_no,
                "bom_id": bomTS_A.id,
            }
            line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
            # Remove obsolete
            to_unlink = bomTS_A.bom_line_ids.filtered(lambda l: (l.corr_component_key or "") not in keepTA)
            to_unlink.unlink()
            # Operation: Cutting
            if self.include_operations:
                # Cutting is a sheet operation; compute per-sheet (UPS=1)
                self._upsert_ops(bomTS_A, ["Cutting"], deckle_cm=(deckle_mm/10.0 if deckle_mm else None), metrics=metrics_no_ups)
                _link_lines_to_op(bomTS_A, ["top_sheet:"], "Cutting")
            top_sheet_stage_prod = prodTS_A
            top_sheet_stage_bom = bomTS_A
            top_sheet_stage_key_prefix = "sfgTA"
            top_sheet_stage_source_tag = "CUT"

            if printing_needed:
                # Stage B: Sheet -> Printed Sheet
                keyTB = f"sfgTB|{keyTA}|PRINT|UPS:{int(ups_val or 1)}|{deck}x{leng}"
                nameTB = f"Top-sheet Printed {size_label_top}" + (f" ({sig_top})" if sig_top else "")
                prodTS_B, bomTS_B = _get_or_create_sfg(nameTB, "top_sheet_printed", keyTB)
                keepTB = set()
                k2 = f"sfgTA:{prodTS_A.id}"
                keepTB.add(k2)
                line = bomTS_B.bom_line_ids.filtered(lambda l, kk=k2: (l.corr_component_key or "") == kk)
                vals = {
                    "product_id": prodTS_A.id,
                    "product_qty": 1.0,
                    "product_uom_id": prodTS_A.uom_id.id,
                    "corr_component_key": k2,
                    "bom_id": bomTS_B.id,
                }
                line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
                # Add ink consumption per printed sheet when available
                try:
                    ink_per_sheet = 0.0
                    ink_prod = ink_uom = None
                    for mapped in _map_rm_lines(res_board):
                        if (mapped.get("role") or "").lower() != "ink":
                            continue
                        ink_per_box = _safe_float(mapped.get("qty"), 0.0)
                        ink_per_sheet = ink_per_box * boxes_per_sheet
                        mp_ink, _g, _f, _b = self._map_component({"role": "ink"}, metrics)
                        if mp_ink:
                            ink_prod = mp_ink.get("product")
                            ink_uom = mp_ink.get("uom")
                        break
                    if ink_prod and ink_uom and ink_per_sheet > 0:
                        k_ink = f"ink:{ink_prod.id}"
                        keepTB.add(k_ink)
                        line = bomTS_B.bom_line_ids.filtered(lambda l, kk=k_ink: (l.corr_component_key or "") == kk)
                        qty_line, uom_line = _qty_uom_preserving_small_weight(
                            ink_per_sheet, ink_uom
                        )
                        vals = {
                            "product_id": ink_prod.id,
                            "product_qty": qty_line,
                            "product_uom_id": uom_line.id,
                            "corr_component_key": k_ink,
                            "corr_role": "ink",
                            "bom_id": bomTS_B.id,
                        }
                        line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
                except Exception:
                    pass
                to_unlink = bomTS_B.bom_line_ids.filtered(lambda l: (l.corr_component_key or "") not in keepTB)
                to_unlink.unlink()
                if self.include_operations:
                    # Printing is a sheet operation; compute per-sheet (UPS=1)
                    self._upsert_ops(bomTS_B, ["Printing"], deckle_cm=(deckle_mm/10.0 if deckle_mm else None), metrics=metrics_no_ups)
                    _link_lines_to_op(bomTS_B, ["sfgTA:", "ink:"], "Printing")
                top_sheet_stage_prod = prodTS_B
                top_sheet_stage_bom = bomTS_B
                top_sheet_stage_key_prefix = "sfgTB"
                top_sheet_stage_source_tag = "PRINT"

        # If pipeline created, consume the last staged top-sheet SFG on Board;
        # otherwise fall back to the raw top-sheet product line.
        prodTS_C = bomTS_C = None
        if top_sheet_stage_prod and top_sheet_stage_bom:
            # Optional Stage C: Film Lamination on printed sheet when a film is selected on spec
            has_film = bool(preview_lamination_lines)
            if has_film:
                keyTC = f"sfgTC|{top_sheet_stage_source_tag}:{top_sheet_stage_prod.id}|FILM|UPS:{int(ups_val or 1)}|{deck}x{leng}"
                nameTC = f"Top-sheet Film Laminated {size_label_top}" + (f" ({sig_top})" if sig_top else "")
                prodTS_C, bomTS_C = _get_or_create_sfg(nameTC, "top_sheet_film", keyTC)
                keepTC = set()
                k3 = f"{top_sheet_stage_key_prefix}:{top_sheet_stage_prod.id}"
                keepTC.add(k3)
                line = bomTS_C.bom_line_ids.filtered(lambda l, kk=k3: (l.corr_component_key or "") == kk)
                vals = {
                    "product_id": top_sheet_stage_prod.id,
                    "product_qty": 1.0,
                    "product_uom_id": top_sheet_stage_prod.uom_id.id,
                    "corr_component_key": k3,
                    "bom_id": bomTS_C.id,
                }
                line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
                # Add film consumption per laminated sheet when available
                try:
                    lam_per_sheet = 0.0
                    film_prod = film_uom = None
                    film_sources = [
                        mapped
                        for mapped in _map_rm_lines(res_board)
                        if (mapped.get("role") or "").lower() == "lamination"
                    ] or preview_lamination_lines
                    for mapped in film_sources:
                        if (mapped.get("role") or "").lower() != "lamination":
                            continue
                        lam_per_box = _safe_float(mapped.get("qty"), 0.0)
                        lam_per_sheet = lam_per_box * boxes_per_sheet
                        film_prod = mapped.get("product")
                        film_uom = mapped.get("uom")
                        if not (film_prod and film_uom):
                            mp_film, _g, _f, _b = self._map_component({"role": "lamination"}, metrics)
                            if mp_film:
                                film_prod = mp_film.get("product")
                                film_uom = mp_film.get("uom")
                        break
                    if film_prod and film_uom and lam_per_sheet > 0:
                        k_film = f"film:{film_prod.id}"
                        keepTC.add(k_film)
                        line = bomTS_C.bom_line_ids.filtered(lambda l, kk=k_film: (l.corr_component_key or "") == kk)
                        qty_line, uom_line = _qty_uom_preserving_small_weight(
                            lam_per_sheet, film_uom
                        )
                        vals = {
                            "product_id": film_prod.id,
                            "product_qty": qty_line,
                            "product_uom_id": uom_line.id,
                            "corr_component_key": k_film,
                            "corr_role": "lamination",
                            "bom_id": bomTS_C.id,
                        }
                        line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
                except Exception:
                    pass
                to_unlink = bomTS_C.bom_line_ids.filtered(lambda l: (l.corr_component_key or "") not in keepTC)
                to_unlink.unlink()
                if self.include_operations:
                    # Film lamination is a sheet operation; compute per-sheet (UPS=1)
                    self._upsert_ops(bomTS_C, [lamination_material_op], deckle_cm=(deckle_mm/10.0 if deckle_mm else None), metrics=metrics_no_ups)
                    _link_lines_to_op(bomTS_C, [f"{top_sheet_stage_key_prefix}:", "film:"], lamination_material_op)
            # Board consumes stage C if present else B
            if prodTS_C and bomTS_C:
                kk = f"sfgTC:{prodTS_C.id}"
                keepB.add(kk)
                line = bomB.bom_line_ids.filtered(lambda l, k=kk: (l.corr_component_key or "") == k)
                vals = {"product_id": prodTS_C.id, "product_qty": 1.0, "product_uom_id": prodTS_C.uom_id.id, "corr_component_key": kk, "bom_id": bomB.id}
                line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
            else:
                kk = f"{top_sheet_stage_key_prefix}:{top_sheet_stage_prod.id}"
                keepB.add(kk)
                line = bomB.bom_line_ids.filtered(lambda l, k=kk: (l.corr_component_key or "") == k)
                vals = {
                    "product_id": top_sheet_stage_prod.id,
                    "product_qty": 1.0,
                    "product_uom_id": top_sheet_stage_prod.uom_id.id,
                    "corr_component_key": kk,
                    "bom_id": bomB.id,
                }
                line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
        else:
            # Fallback: add raw top sheet directly on Board.
            # Unit mode still uses the existing top_sheet RM line. Roll mode
            # resolves the raw roll product directly without creating the
            # intermediate Cut/Printed SFG stages.
            if pipeline and roll_top_prod:
                kk = f"top_sheet:{roll_top_prod.id}"
                keepB.add(kk)
                line = bomB.bom_line_ids.filtered(lambda l, k=kk: (l.corr_component_key or "") == k)
                vals = {
                    "product_id": roll_top_prod.id,
                    "product_qty": roll_top_qty,
                    "product_uom_id": roll_top_prod.uom_id.id,
                    "corr_component_key": kk,
                    "corr_role": "top_sheet",
                    "corr_layer_no": roll_top_layer_no,
                    "bom_id": bomB.id,
                }
                line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
                direct_top_sheet_added = True
            else:
                for mapped in _map_rm_lines(res_board):
                    if mapped["role"] != "top_sheet":
                        continue
                    kk = f"top_sheet:{mapped['product'].id}"
                    keepB.add(kk)
                    line = bomB.bom_line_ids.filtered(lambda l, k=kk: (l.corr_component_key or "") == k)
                    vals = {
                        "product_id": mapped["product"].id,
                        "product_qty": (1.0 if 'sheet_flag' in locals() and sheet_flag else mapped["qty"]),
                        "product_uom_id": mapped["uom"].id,
                        "corr_component_key": kk,
                        "corr_role": "top_sheet",
                        "bom_id": bomB.id,
                    }
                    line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
                    direct_top_sheet_added = True

        if not (prodTS_C and bomTS_C):
            for mapped in preview_lamination_lines:
                film_product = mapped.get("product")
                film_uom = mapped.get("uom")
                if not (film_product and film_uom):
                    continue
                kk = f"film:{film_product.id}"
                keepB.add(kk)
                line = bomB.bom_line_ids.filtered(lambda l, k=kk: (l.corr_component_key or "") == k)
                qty_line, uom_line = _qty_uom_preserving_small_weight(
                    _per_sheet(mapped.get("qty")), film_uom
                )
                vals = {
                    "product_id": film_product.id,
                    "product_qty": qty_line,
                    "product_uom_id": uom_line.id,
                    "corr_component_key": kk,
                    "corr_role": "lamination",
                    "bom_id": bomB.id,
                }
                line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
        if preview_ink_lines and not ink_on_die_stage and not (prodTS_B and bomTS_B):
            for mapped in preview_ink_lines:
                ink_product = mapped.get("product")
                ink_uom = mapped.get("uom")
                if not (ink_product and ink_uom):
                    continue
                kk = f"ink:{ink_product.id}"
                keepB.add(kk)
                line = bomB.bom_line_ids.filtered(lambda l, k=kk: (l.corr_component_key or "") == k)
                qty_line, uom_line = _qty_uom_preserving_small_weight(
                    _per_sheet(mapped.get("qty")), ink_uom
                )
                vals = {
                    "product_id": ink_product.id,
                    "product_qty": qty_line,
                    "product_uom_id": uom_line.id,
                    "corr_component_key": kk,
                    "corr_role": "ink",
                    "bom_id": bomB.id,
                }
                line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
        # Board-stage adhesive: additional combine (beyond first 4-ply) + top lamination
        adh_board_stage = max(0.0, (adh_combine_remaining or 0.0)) + max(0.0, (adh_top or 0.0))
        if adh_board_stage > 0:
            mp, _d10, _d11, _d12 = self._map_component({"role": "adhesive"}, {})
            pid = mp and mp.get("product") and mp["product"].id or False
            uom = mp and mp.get("uom") and mp["uom"].id or False
            if pid:
                kk = f"adhesive:{pid}"
                keepB.add(kk)
                line = bomB.bom_line_ids.filtered(lambda l, k=kk: (l.corr_component_key or "") == k)
                vals = {"product_id": pid, "product_qty": _per_sheet(adh_board_stage), "product_uom_id": uom, "corr_component_key": kk, "bom_id": bomB.id}
                line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
        # Cleanup + ops
        to_unlink = bomB.bom_line_ids.filtered(lambda l: (l.corr_component_key or "") not in keepB)
        to_unlink.unlink()
        if self.include_operations:
            has_top_sheet = direct_top_sheet_added or bool(top_sheet_stage_prod and top_sheet_stage_bom) or any(((x.get("role") or "").lower() == "top_sheet") for x in (res_board.get("rm_lines") or []))
            board_ops = ["Lamination 5-ply" if len(layers_board) == 5 else "Lamination"]
            if preview_ink_lines and not ink_on_die_stage and not (prodTS_B and bomTS_B):
                board_ops.append("Printing")
            self._upsert_ops(bomB, board_ops, deckle_cm=(deckle_mm/10.0 if deckle_mm else None), top_sheet=has_top_sheet, metrics=(res_board.get("metrics") if isinstance(res_board, dict) else {}))
            _link_lines_to_op(bomB, ["sfg4:", "sfg2:", "top_sheet:", "adhesive:", "film:"], "Lamination")
            _link_lines_to_op(bomB, ["ink:"], "Printing")

        # Build Die-cut SFG
        keyD = _key_die(keyB)
        # Base size label (sheet-level when active, otherwise box-level)
        size_label_die = size_label_sheet if use_sheet_label else size_label_box
        # Include top-sheet and diecut tool so die SFGs are distinct across
        # different top sheets or tools even when geometry is identical.
        top_label_die = None
        try:
            xp_die = getattr(spec, "extra_params_json", None) or {}
            if isinstance(xp_die, str):
                try:
                    xp_die = json.loads((xp_die or "{}").strip())
                except Exception:
                    xp_die = {}
            top_mode_die = str(xp_die.get("top_layer_mode") or "").strip().lower()
            is_roll_die = "roll" in top_mode_die

            if is_roll_die:
                roll_code_die = (getattr(spec, "top_sheet_roll_code", "") or "").strip()
                if roll_code_die:
                    top_label_die = roll_code_die

            ts_prod = getattr(spec, "top_sheet_product_id", False)
            if not top_label_die and ts_prod:
                top_label_die = (ts_prod.default_code or ts_prod.name or "").strip() or None
            if not top_label_die:
                code = (getattr(spec, "top_sheet_code", "") or "").strip()
                if code:
                    top_label_die = code
        except Exception:
            top_label_die = None
        tool_label = None
        try:
            tool_rec = getattr(product_tmpl, "primary_diecut_id", False)
            if tool_rec:
                tool_label = (tool_rec.code or tool_rec.name or "").strip() or None
        except Exception:
            tool_label = None
        # Assemble final die-cut name: "Die-cut 45x70.5 cm (top-..., Tool DC-001)"
        base_die_name = f"Die-cut {size_label_die}"
        detail_parts = []
        if top_label_die:
            detail_parts.append(top_label_die)
        if tool_label:
            detail_parts.append(f"Tool {tool_label}")
        if detail_parts:
            nameD = f"{base_die_name} ({', '.join(detail_parts)})"
        else:
            nameD = base_die_name
        prodD, bomD = _get_or_create_sfg(nameD, "die-cut", keyD)
        if diecut_yield_sheet_flag and sheet_flag and ups_val:
            try:
                die_base_qty = float(ups_val or 1.0)
                if float(bomD.product_qty or 0.0) != die_base_qty:
                    bomD.write({"product_qty": die_base_qty})
            except Exception:
                pass
        keepD = set()
        kk = f"board:{prodB.id}"
        keepD.add(kk)
        line = bomD.bom_line_ids.filtered(lambda l, k=kk: (l.corr_component_key or "") == k)
        vals = {"product_id": prodB.id, "product_qty": 1.0, "product_uom_id": prodB.uom_id.id, "corr_component_key": kk, "bom_id": bomD.id}
        line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
        if preview_ink_lines and ink_on_die_stage and not (prodTS_B and bomTS_B):
            for mapped in preview_ink_lines:
                ink_product = mapped.get("product")
                ink_uom = mapped.get("uom")
                if not (ink_product and ink_uom):
                    continue
                kk_ink = f"ink:{ink_product.id}"
                keepD.add(kk_ink)
                line = bomD.bom_line_ids.filtered(lambda l, k=kk_ink: (l.corr_component_key or "") == k)
                qty_line, uom_line = _qty_uom_preserving_small_weight(
                    _per_sheet(mapped.get("qty")), ink_uom
                )
                vals = {
                    "product_id": ink_product.id,
                    "product_qty": qty_line,
                    "product_uom_id": uom_line.id,
                    "corr_component_key": kk_ink,
                    "corr_role": "ink",
                    "bom_id": bomD.id,
                }
                line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
        # Optional scrap by-product on die-cut
        if self.include_scrap_byproduct:
            waste = (metrics.get("waste") or {})
            kg_per_box = _safe_float(metrics.get("kg_per_box"), 0.0)
            process_pct = _safe_float(waste.get("process_pct"), 0.0)
            scrap_kg_per_unit = kg_per_box * (process_pct / 100.0)
            if scrap_kg_per_unit > 0:
                scrap_product = self._get_or_create_scrap_product()
                byp = bomD.byproduct_ids.filtered(lambda b: b.product_id.id == scrap_product.id)[:1]
                scrap_qty, scrap_uom = _qty_uom_preserving_small_weight(
                    scrap_kg_per_unit,
                    scrap_product.uom_id,
                )
                vals_bp = {
                    "bom_id": bomD.id,
                    "product_id": scrap_product.id,
                    "product_qty": scrap_qty,
                    "product_uom_id": scrap_uom.id,
                    "sequence": 10,
                    "cost_share": 0.0,
                }
                byp.write(vals_bp) if byp else self.env["mrp.bom.byproduct"].create(vals_bp)
        # Ops
        if self.include_operations:
            die_ops = ["Die-cut"]
            if preview_ink_lines and ink_on_die_stage and not (prodTS_B and bomTS_B):
                die_ops.append("Printing")
            self._upsert_ops(bomD, die_ops, deckle_cm=(deckle_mm/10.0 if deckle_mm else None), metrics=metrics)
            _link_lines_to_op(bomD, ["board:"], "Die-cut")
            _link_lines_to_op(bomD, ["ink:"], "Printing")

        prodP = bomP = None
        if peeling_sfg_enabled:
            keyP = _key_peeling(keyD)
            peel_suffix = f"({top_label_die})" if top_label_die else ""
            nameP = f"Peeloff {peel_suffix}".strip()
            prodP, bomP = _get_or_create_sfg(nameP, "peeling", keyP)
            # Peeling yields UPS pieces per die-cut sheet: set BoM base to UPS to keep integers
            try:
                base_qty_peel = float(ups_val or 1) if (sheet_flag and ups_val) else 1.0
            except Exception:
                base_qty_peel = 1.0
            try:
                if bomP.product_qty != base_qty_peel:
                    bomP.product_qty = base_qty_peel
            except Exception:
                try:
                    bomP.write({"product_qty": base_qty_peel})
                except Exception:
                    pass
            keepP = set()
            kp = f"sfgD:{prodD.id}"
            keepP.add(kp)
            line = bomP.bom_line_ids.filtered(lambda l, k=kp: (l.corr_component_key or "") == k)
            # Consume whole die-cut sheets; BoM base quantity carries UPS.
            die_per_piece = base_qty_peel if diecut_yield_sheet_flag else 1.0
            vals = {
                "product_id": prodD.id,
                "product_qty": die_per_piece,
                "product_uom_id": prodD.uom_id.id,
                "corr_component_key": kp,
                "bom_id": bomP.id,
            }
            line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
            to_unlink = bomP.bom_line_ids.filtered(lambda l: (l.corr_component_key or "") not in keepP)
            to_unlink.unlink()
            if self.include_operations:
                self._upsert_ops(bomP, ["Peeling"], deckle_cm=(deckle_mm/10.0 if deckle_mm else None), metrics=metrics)
                _link_lines_to_op(bomP, ["sfgD:"], "Peeling")

        # Upsert FG BoM: Die-cut SFG + locking component(s)
        Bom = self.env["mrp.bom"].sudo()
        existing = Bom.search([("corr_spec_id", "=", spec.id)], limit=1)
        if not existing:
            fg_base_qty = float(ups_val or 1.0) if (diecut_yield_sheet_flag and sheet_flag and ups_val and not peeling_sfg_enabled) else 1.0
            existing = Bom.create({
                "product_tmpl_id": product_tmpl.id,
                "product_qty": fg_base_qty,
                "type": "normal",
                "corr_spec_id": spec.id,
                "corr_signature": result.get("signature"),
                "corr_result_snapshot": json.dumps(result, indent=2),
                "corr_use_gross_qty": bool(self.include_waste_in_bom),
                "consumption": "warning",
            })
        else:
            fg_base_qty = float(ups_val or 1.0) if (diecut_yield_sheet_flag and sheet_flag and ups_val and not peeling_sfg_enabled) else 1.0
            vals_write = {
                "product_tmpl_id": product_tmpl.id,
                "product_qty": fg_base_qty,
                "corr_signature": result.get("signature"),
                "corr_result_snapshot": json.dumps(result, indent=2),
                "corr_use_gross_qty": bool(self.include_waste_in_bom),
                "consumption": "warning",
            }
            existing.write(vals_write)

        keepFG = set()
        # SFG input into FG: Die-cut or Peeling SFG based on feature toggle
        if peeling_sfg_enabled and prodP:
            kfg = f"sfgP:{prodP.id}"
            fg_sfg_prod = prodP
        else:
            kfg = f"sfgD:{prodD.id}"
            fg_sfg_prod = prodD
        keepFG.add(kfg)
        line = existing.bom_line_ids.filtered(lambda l, k=kfg: (l.corr_component_key or "") == k)
        vals = {
            "product_id": fg_sfg_prod.id,
            "product_qty": float(ups_val or 1.0) if (diecut_yield_sheet_flag and sheet_flag and ups_val and not peeling_sfg_enabled) else 1.0,
            "product_uom_id": fg_sfg_prod.uom_id.id,
            "corr_component_key": kfg,
            "bom_id": existing.id,
        }
        line.write(vals) if line else self.env["mrp.bom.line"].create(vals)
        # Locking component selection: support glue, wire/tape, or both (config-driven)
        # Determine requested locking method(s) from spec
        def _get_locking_method():
            try:
                lt = getattr(spec, "locking_type_id", False)
                if lt and getattr(lt, "method", None):
                    return (lt.method or "").strip().lower()
            except Exception:
                pass
            return None
        locking_method = _get_locking_method()  # expected values: 'glue','wire','tape'

        # Derive finishing ops from spec extra params (ordered list)
        finishing_ops = []
        try:
            xp = getattr(spec, "extra_params_json", None) or {}
            raw_ops = xp.get("finishing_ops")
            if isinstance(raw_ops, (list, tuple)):
                finishing_ops = [str(x or "").strip().lower() for x in raw_ops if x is not None]
        except Exception:
            finishing_ops = []

        # System parameter to allow combined locking (both glue + wire)
        icp = self.env["ir.config_parameter"].sudo()
        combined_mode = False
        try:
            combined_mode = (icp.get_param("corr_mrp_bridge.locking_mode", default="exclusive") or "exclusive").strip().lower() == "combined"
        except Exception:
            combined_mode = False

        # Decide which consumables to add at FG
        want_glue = False
        want_wire = False
        want_tape = False
        if finishing_ops:
            # explicit list drives inclusion
            want_glue = any((op.startswith("gluing") or op == "glue") for op in finishing_ops)
            want_wire = any((op.startswith("stitch") or op == "wire") for op in finishing_ops)
            want_tape = any((op.startswith("tape")) for op in finishing_ops)
        else:
            # legacy behavior: exclusive method
            if locking_method == "wire":
                want_wire = True
            elif locking_method == "tape":
                want_tape = True
            elif locking_method == "both":
                want_glue = True
                want_wire = True
            else:
                want_glue = True
            # combined flag optionally adds both
            if combined_mode:
                want_glue = True
                want_wire = True

        # Aggregate engine-provided wire/tape quantities (kg per unit)
        def _sum_role(role_name):
            try:
                total = 0.0
                for ln in (rm_lines or []):
                    if (ln.get("role") or "").lower() != role_name:
                        continue
                    try:
                        qty = float(ln.get("qty") or 0.0)
                    except Exception:
                        qty = 0.0
                    total += qty
                return float(total or 0.0)
            except Exception:
                return 0.0

        wire_kg = _sum_role("wire")
        tape_kg = _sum_role("tape")
        if wire_kg > 0:
            want_wire = True
        if tape_kg > 0:
            want_tape = True
        if not _stage_mapping_has_any("Seaming", "Gluing"):
            want_glue = False
        if not _stage_mapping_has_any("Stitching"):
            want_wire = False
        if not _stage_mapping_has_any("Seaming", "Taping"):
            want_tape = False

        # Decide final method: prefer engine explicit wire/tape; else spec method; else default glue
        method_final = None
        if wire_kg > 0:
            method_final = "wire"
        elif tape_kg > 0:
            method_final = "tape"
        elif locking_method:
            method_final = locking_method
        else:
            method_final = "glue"

        # Compose line for the chosen method
        # Add WIRE when requested (engine qty or fallback per unit)
        if want_wire:
            qty_wire = wire_kg
            if qty_wire <= 0:
                try:
                    val = icp.get_param("corr_mrp_bridge.locking_wire_kg_per_unit")
                    qty_wire = float(val or 0.0)
                except Exception:
                    qty_wire = 0.0
            # Optional scaling when combined
            try:
                if want_glue and combined_mode:
                    scale_w = float(icp.get_param("corr_mrp_bridge.locking_wire_scale_pct_when_combined") or 100.0)
                    qty_wire = qty_wire * max(scale_w, 0.0) / 100.0
            except Exception:
                pass
            if qty_wire > 0:
                mp, _d13, _d14, _d15 = self._map_component({"role": "wire"}, {})
                pid = mp and mp.get("product") and mp["product"].id or False
                uom = mp and mp.get("uom") and mp["uom"].id or False
                if pid:
                    kw = f"wire:{pid}"
                    keepFG.add(kw)
                    line = existing.bom_line_ids.filtered(lambda l, k=kw: (l.corr_component_key or "") == k)
                    vals = {"product_id": pid, "product_qty": qty_wire, "product_uom_id": uom, "corr_component_key": kw, "bom_id": existing.id}
                    line.write(vals) if line else self.env["mrp.bom.line"].create(vals)

        # Add TAPE when requested and available from engine
        if want_tape and tape_kg > 0:
            mp, _d16, _d17, _d18 = self._map_component({"role": "tape"}, {})
            pid = mp and mp.get("product") and mp["product"].id or False
            uom = mp and mp.get("uom") and mp["uom"].id or False
            if pid:
                kt = f"tape:{pid}"
                keepFG.add(kt)
                line = existing.bom_line_ids.filtered(lambda l, k=kt: (l.corr_component_key or "") == k)
                vals = {"product_id": pid, "product_qty": tape_kg, "product_uom_id": uom, "corr_component_key": kt, "bom_id": existing.id}
                line.write(vals) if line else self.env["mrp.bom.line"].create(vals)

        # Add GLUE when requested
        if want_glue and adh_lock > 0:
            # Locking adhesive is per finished unit; no UPS conversion needed here.
            qty_glue = adh_lock
            # Optional scaling when combined
            try:
                if want_wire and combined_mode:
                    scale_g = float(icp.get_param("corr_mrp_bridge.locking_glue_scale_pct_when_combined") or 100.0)
                    qty_glue = qty_glue * max(scale_g, 0.0) / 100.0
            except Exception:
                pass
            mp, _d19, _d20, _d21 = self._map_component({"role": "adhesive"}, {})
            pid = mp and mp.get("product") and mp["product"].id or False
            uom = mp and mp.get("uom") and mp["uom"].id or False
            if pid:
                kgl = f"adhesive:{pid}"
                keepFG.add(kgl)
                line = existing.bom_line_ids.filtered(lambda l, k=kgl: (l.corr_component_key or "") == k)
                vals = {"product_id": pid, "product_qty": qty_glue, "product_uom_id": uom, "corr_component_key": kgl, "bom_id": existing.id}
                line.write(vals) if line else self.env["mrp.bom.line"].create(vals)

        # Remove obsolete FG lines created by previous runs (preserve manual if locked)
        to_unlink = existing.bom_line_ids.filtered(lambda l: (l.corr_component_key or "") and (l.corr_component_key not in keepFG))
        if existing.lock_manual_lines:
            to_unlink = to_unlink.filtered(lambda l: l.corr_manual is False)
        to_unlink.unlink()

        # FG operations: Peeling + configurable finishing (legacy) or Seaming-only when Peeling SFG is enabled,
        # plus any extra finishing operations configured on the Spec Template.
        if self.include_operations:
            base_ops = ["Gluing"] if peeling_sfg_enabled else ["Peeling", "Gluing"]
            try:
                extra_ops = []
                spec_extra = getattr(spec, "corr_extra_finishing_operation_ids", False)
                if spec_extra:
                    spec_extra = spec_extra.sorted(key=lambda o: o.sequence or 0)
                    for op_type in spec_extra:
                        label = (op_type.code or op_type.name or "").strip()
                        if not label:
                            continue
                        extra_ops.append(label)
                if extra_ops:
                    seen = { (o or "").strip().lower() for o in base_ops }
                    for label in extra_ops:
                        key = (label or "").strip().lower()
                        if key and key not in seen:
                            base_ops.append(label)
                            seen.add(key)
            except Exception:
                # Finishing ops are optional; never block BoM creation
                pass

            self._upsert_ops(
                existing,
                base_ops,
                deckle_cm=(deckle_mm / 10.0 if deckle_mm else None),
                metrics=metrics,
            )
            # Maintain existing component-to-operation links (core ops only)
            if peeling_sfg_enabled:
                _link_lines_to_op(existing, ["sfgP:"], "Gluing")
            else:
                _link_lines_to_op(existing, ["sfgD:"], "Gluing")
            _link_lines_to_op(existing, ["adhesive:", "wire:", "tape:"], "Gluing")

        return {
            "type": "ir.actions.act_window",
            "res_model": "mrp.bom",
            "view_mode": "form",
            "res_id": existing.id,
            "target": "current",
        }

    def _upsert_ops(self, bom, op_names, *, flute=None, deckle_cm=None, top_sheet=False, metrics=None, placeholder_only=False):
        opmap = self.env["corr.operation.map"].search([("active", "=", True)])
        placeholder_wc = self.env["mrp.workcenter"].search([("corr_is_placeholder", "=", True)], limit=1)
        icp = self.env["ir.config_parameter"].sudo()
        try:
            assign_wc = icp.get_param("corr_mrp_bridge.assign_workcenter", "1") in ("1", "true", "True")
        except Exception:
            assign_wc = True
        # Normalize requested operation names to configured map names
        normalize = {
            "combine 4-ply": "Lamination",
            "lamination 5-ply": "Lamination",
            "die-cut": "Slot/Die",
            # Keep Peeling as its own op; do not normalize to Slot/Die
            "peeling": "Peeling",
            # New operations used in top-sheet staging
            "cutting": "Cutting",
            "film lamination": "Film Lamination",
            "gluing": "Seaming",
            # Match selection value in Operation Map (stored as 'QC')
            "qc": "QC",
            # Finishing operations (post-peeling)
            "stitching": "Stitching",
            "folding": "Folding",
            "folding (manual)": "Folding (Manual)",
            "flap pasting": "Flap Pasting",
            "bottom lock": "Bottom Lock",
        }
        desired = []
        for idx, op in enumerate(op_names, start=1):
            raw = (op or "").strip()
            norm = normalize.get(raw.lower(), raw)
            # When assignment is disabled, force placeholder/unassigned WC and skip mapping
            if not assign_wc:
                wc_id = placeholder_wc.id if placeholder_wc else False
                desired.append({
                    "name": norm,
                    "sequence": idx * 10,
                    "workcenter_id": wc_id,
                    "time_mode": "manual",
                    "time_cycle_manual": 0.0,
                })
                continue
            # For some flows (e.g., SHEET/PAD with no SFG), always fall back to
            # the placeholder work center so planners can later reassign WCs.
            if placeholder_only and placeholder_wc:
                desired.append({
                    "name": norm,
                    "sequence": idx * 10,
                    "workcenter_id": placeholder_wc.id,
                    "time_mode": "manual",
                    "time_cycle_manual": 0.0,
                })
                continue
            # Select best mapping row by rules: name match, flute/deckle/top-sheet preferences
            candidates = opmap.filtered(lambda r, n=norm: r.op_name == n and r.workcenter_id)
            best = None
            for r in candidates:
                wc = r.workcenter_id
                # Effective rules: mapping overrides, else workcenter capabilities
                eff_flute = (r.flute_code or "").strip() or (wc and (wc.corr_flutes_supported or "").split(",")[0].strip())
                try:
                    eff_min = r.deckle_min_cm if r.deckle_min_cm not in (None,) else (wc and wc.corr_deckle_min_cm)
                except Exception:
                    eff_min = wc and wc.corr_deckle_min_cm
                try:
                    eff_max = r.deckle_max_cm if r.deckle_max_cm not in (None,) else (wc and wc.corr_deckle_max_cm)
                except Exception:
                    eff_max = wc and wc.corr_deckle_max_cm
                eff_top_only = bool(r.lamination_top_only or (wc and wc.corr_lamination_top_only))
                # Flute rule applies mainly to Corrugation
                flute_pen = 0
                if flute and eff_flute:
                    flute_pen = 0 if str(eff_flute).strip().lower() == str(flute).strip().lower() else 1
                elif eff_flute:
                    # mapping has flute but we don't: small penalty
                    flute_pen = 2
                # Deckle rule: skip if outside explicit range
                in_range = True
                if deckle_cm is not None:
                    if eff_min and deckle_cm < eff_min:
                        in_range = False
                    if eff_max and deckle_cm > eff_max:
                        in_range = False
                # Lamination top-sheet preference
                lam_pen = 0
                if eff_top_only:
                    lam_pen = 0 if top_sheet else 2
                if not in_range:
                    continue
                rank = (flute_pen, lam_pen, r.sequence or 9999, r.id)
                if best is None or rank < best[0]:
                    best = (rank, r)
            m = best and best[1]
            if (not m) and candidates:
                # Fallback: pick a generic row (no constraints) or first by sequence
                generic = candidates.filtered(lambda r: not r.flute_code and not r.deckle_min_cm and not r.deckle_max_cm and not r.lamination_top_only)
                m = generic[:1] or candidates.sorted(key=lambda r: r.sequence)[:1]
                m = m and m[0] or False
            if not m:
                # No explicit mapping found; optionally fall back to placeholder work center
                if placeholder_wc:
                    desired.append({
                        "name": norm,
                        "sequence": idx * 10,
                        "workcenter_id": placeholder_wc.id,
                        "time_mode": "manual",
                        # Unknown runtime at this stage; planner can adjust later.
                        "time_cycle_manual": 0.0,
                    })
                continue
            run_min = self._compute_runtime_from_map(m, metrics or {})
            desired.append({
                "name": norm,
                "sequence": m.sequence or (idx * 10),
                "workcenter_id": m.workcenter_id.id,
                "time_mode": "manual",
                "time_cycle_manual": run_min,
            })
        existing_ops = bom.operation_ids
        keep_names = set()
        for d in desired:
            keep_names.add(d["name"])
            op_line = existing_ops.filtered(lambda o, n=d["name"]: o.name == n)[:1]
            vals = d.copy()
            vals["bom_id"] = bom.id
            op_line.write(vals) if op_line else self.env["mrp.routing.workcenter"].create(vals)
        obsolete_ops = existing_ops.filtered(lambda o: o.name not in keep_names)
        _archive_operations(obsolete_ops)

    # ------------------------
    # Helpers
    # ------------------------
    def _get_uom_kg(self):
        Uom = self.env["uom.uom"].sudo()
        # 1) Try standard XMLID for kilogram
        try:
            kg = self.env.ref("uom.product_uom_kgm")
            if kg:
                return kg
        except Exception:
            pass
        # 2) Try category-based reference search (using known XMLID for weight category)
        try:
            cat = self.env.ref("uom.product_uom_categ_kgm")
        except Exception:
            cat = False
        if cat:
            uom = Uom.search([("category_id", "=", cat.id), ("uom_type", "=", "reference")], limit=1)
            if uom:
                return uom[0]
        # 3) Name heuristic
        uom = Uom.search([("name", "ilike", "kg"), ("uom_type", "=", "reference")], limit=1)
        if uom:
            return uom[0]
        uom = Uom.search([("name", "ilike", "kg")], limit=1)
        if uom:
            return uom[0]
        # 4) Fallback to any existing UoM
        return Uom.search([], limit=1)

    def _get_or_create_scrap_product(self):
        Product = self.env["product.product"].sudo()
        kg_uom = self._get_uom_kg()
        prod = Product.search([("name", "=", "Corr Scrap (kg)")], limit=1)
        if prod:
            return prod
        # Create a new product variant (non-stocked or stocked, keep as stockable for visibility)
        Template = self.env["product.template"].sudo().with_context(skip_corr_autobom=True)
        tmpl = Template.create({
            "name": "Corr Scrap (kg)",
            # Use 'consu' for broad compatibility (no 'product' in this DB)
            "type": "consu",
            "uom_id": kg_uom.id,
            "uom_po_id": kg_uom.id,
            "purchase_ok": False,
            "sale_ok": False,
        })
        return tmpl.product_variant_id

    def _get_or_create_sfg_product(self, metrics):
        Product = self.env["product.product"].sudo()
        Template = self.env["product.template"].sudo().with_context(skip_corr_autobom=True)
        Unit = self.env.ref("uom.product_uom_unit")
        geo = (metrics.get("geometry") or {})
        deckle_mm = float(geo.get("deckle_mm") or 0.0)
        length_mm = float(geo.get("length_mm") or 0.0)
        name = f"Corrugated Sheet {int(round(deckle_mm))}x{int(round(length_mm))} mm"

        try:
            icp = self.env["ir.config_parameter"].sudo()
            enable_mto = icp.get_param("corr_mrp_bridge.enable_mto_for_sfg", default="0") in ("1", "true", "True")
        except Exception:
            enable_mto = False

        def _route(xmlids, name_like):
            for xid in xmlids:
                try:
                    rec = self.env.ref(xid)
                    if rec:
                        return rec
                except Exception:
                    pass
            return self.env["stock.route"].sudo().search([("name", "ilike", name_like), ("product_selectable", "=", True)], limit=1)

        man = _route(["mrp.route_warehouse0_manufacture", "mrp.route_manufacture"], "Manufacture")
        mto = _route(["stock.route_warehouse0_mto", "stock.route_mto"], "Make To Order")

        prod = Product.search([("name", "=", name)], limit=1)
        if prod:
            try:
                if prod.product_tmpl_id:
                    tmpl = prod.product_tmpl_id
                    vals = {}
                    if not bool(getattr(tmpl, "is_storable", False)):
                        vals["is_storable"] = True
                    if getattr(tmpl, "product_role", None) in (None, False):
                        vals["product_role"] = "sfg"
                    route_cmds = []
                    if man and man not in tmpl.route_ids:
                        route_cmds.append((4, man.id))
                    if mto:
                        if enable_mto and mto not in tmpl.route_ids:
                            route_cmds.append((4, mto.id))
                        if not enable_mto and mto in tmpl.route_ids:
                            route_cmds.append((3, mto.id))
                    if route_cmds:
                        vals["route_ids"] = route_cmds
                    if vals:
                        tmpl.write(vals)
            except Exception:
                pass
            return prod

        vals = {
            "name": name,
            "type": "consu",
            "is_storable": True,
            "uom_id": Unit.id,
            "uom_po_id": Unit.id,
            "purchase_ok": False,
            "sale_ok": False,
            "product_role": "sfg",
        }
        route_ids = []
        if man:
            route_ids.append(man.id)
        if enable_mto and mto:
            route_ids.append(mto.id)
        if route_ids:
            vals["route_ids"] = [(6, 0, route_ids)]
        tmpl = Template.create(vals)
        return tmpl.product_variant_id


