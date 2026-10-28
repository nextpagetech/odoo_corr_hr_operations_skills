import json

from odoo import api, models, _
from odoo.exceptions import UserError
from odoo.tools import float_round


class MrpWorkorderCorrDuration(models.Model):
    _inherit = "mrp.workorder"

    @api.depends("operation_id", "workcenter_id", "qty_producing", "qty_production")
    def _compute_duration_expected(self):
        """Always recompute expected duration when op/WC/qty changes.

        This ensures that:
        - BoM-driven MOs stay consistent with their operations and work centers.
        - Manually edited MOs (operation/workcenter changes) recompute using
          mapping-aware `_get_duration_expected` when available, or standard
          Odoo logic otherwise.
        """
        for workorder in self:
            if workorder.state in ("done", "cancel"):
                continue
            workorder.duration_expected = workorder._get_duration_expected()

    def _corr_normalize_op_name(self):
        """Return the normalized operation name for mapping.

        Uses the work order `name` (what the user sees in the MO)
        as primary label, and falls back to `operation_id.name` when
        it is missing. This covers both BoM-driven operations and
        manually created work orders.
        """
        self.ensure_one()
        op_name = None
        # Prefer the WO display name (Operation column on the MO)
        if self.name:
            op_name = (self.name or "").strip()
        if (not op_name) and self.operation_id:
            op_name = (self.operation_id.name or "").strip()
        if not op_name:
            return None
        normalize = {
            "combine 4-ply": "Lamination",
            "lamination 5-ply": "Lamination",
            "die-cut": "Slot/Die",
            # Keep Peeling distinct
            "peeling": "Peeling",
            # Stage-related ops
            "cutting": "Cutting",
            "film lamination": "Film Lamination",
            "gluing": "Seaming",
            # QC + finishing
            "qc": "QC",
            "stitching": "Stitching",
            "folding": "Folding",
            "folding (manual)": "Folding (Manual)",
            "flap pasting": "Flap Pasting",
            "bottom lock": "Bottom Lock",
        }
        return normalize.get(op_name.lower(), op_name)

    # -----------------------------
    # Internal helpers for mapping
    # -----------------------------

    def _corr_get_metrics_for_map(self):
        """Return corrugation metrics dict for the current MO, if available.

        Preference order:
        1) BoM corr_result_snapshot (cached engine result from bridge).
        2) Fresh compute via corr.formula.engine for the BoM's corr_spec_id.
        3) Fresh compute via corr.formula.engine for the MO's product template.

        Always returns a dict (possibly empty) and never raises.
        """
        self.ensure_one()
        metrics = {}
        mo = False
        bom = False
        try:
            mo = self.production_id
            bom = mo.bom_id if mo else False
        except Exception:
            mo = False
            bom = False

        # 1) Use cached snapshot on BoM when present
        if bom:
            try:
                snapshot = getattr(bom, "corr_result_snapshot", False)
                if snapshot:
                    data = json.loads(snapshot)
                    metrics = (data or {}).get("metrics") or {}
            except Exception:
                metrics = {}
        if metrics:
            return metrics

        # 2) Compute from corr_spec_id or product template via engine
        try:
            Engine = self.env["corr.formula.engine"].sudo()
        except Exception:
            return {}

        spec_or_product = None
        try:
            if bom and getattr(bom, "corr_spec_id", False):
                spec_or_product = bom.corr_spec_id
        except Exception:
            spec_or_product = None

        if not spec_or_product and mo:
            try:
                # Prefer template; engine accepts either spec or template
                spec_or_product = mo.product_id.product_tmpl_id or mo.product_tmpl_id
            except Exception:
                spec_or_product = None

        if not spec_or_product:
            return {}

        try:
            result = Engine.compute_requirements(spec_or_product, 1.0)
            metrics = (result or {}).get("metrics") or {}
        except Exception:
            metrics = {}
        return metrics or {}

    def _corr_get_ups_from_metrics(self, metrics):
        """Resolve printing UPS from metrics; default 1.

        Mirrors wizard behaviour but without relying on a spec field.
        """
        try:
            layout = (metrics or {}).get("layout") or {}
            ups = layout.get("printing_ups")
            if ups is not None:
                ups = int(float(ups or 0)) or 1
                return max(1, ups)
        except Exception:
            pass
        return 1

    def _corr_compute_size_metrics(self, metrics):
        """Compute length/area/UPS from metrics for runtime conversion."""
        geo = (metrics or {}).get("geometry") or {}
        try:
            length_m = float(geo.get("length_mm") or 0.0) / 1000.0
        except Exception:
            length_m = 0.0
        try:
            deckle_m = float(geo.get("deckle_mm") or 0.0) / 1000.0
        except Exception:
            deckle_m = 0.0

        # Fallback: borrow dimensions from BoM-linked spec when geometry is missing
        if length_m <= 0.0 or deckle_m <= 0.0:
            try:
                bom = self.production_id.bom_id
                spec = getattr(bom, "corr_spec_id", False)
                if spec:
                    lm = getattr(spec, "length_mm", None)
                    wm = getattr(spec, "width_mm", None)
                    if (not length_m) and lm:
                        length_m = float(lm or 0.0) / 1000.0
                    if (not deckle_m) and wm:
                        deckle_m = float(wm or 0.0) / 1000.0
            except Exception:
                pass

        sheet_area_m2 = max(0.0, length_m * deckle_m)
        ups = self._corr_get_ups_from_metrics(metrics)
        unit_area_m2 = sheet_area_m2 / float(ups or 1)
        return {
            "length_m": length_m,
            "sheet_area_m2": sheet_area_m2,
            "unit_area_m2": unit_area_m2,
            "ups": ups,
        }

    def _corr_compute_run_minutes_from_map(self, op_map):
        """Return minutes per finished unit for the given mapping row.

        Supports all speed bases used by corr.operation.map:
        - Units per hour
        - Sheets per hour
        - Meters per minute
        - Square meters per hour

        Mirrors the logic from the BoM bridge wizard, adapted for MO context.
        """
        if not op_map:
            return 0.0

        metrics = self._corr_get_metrics_for_map()
        vals = self._corr_compute_size_metrics(metrics)
        length_m = vals["length_m"]
        sheet_area_m2 = vals["sheet_area_m2"]
        unit_area_m2 = vals["unit_area_m2"]
        ups = max(1, int(vals["ups"] or 1))

        basis = (getattr(op_map, "speed_basis", False) or "units_per_hour")
        base_speed = getattr(op_map, "base_speed", None)
        # Backward-compat: prefer legacy units/hour field when applicable
        try:
            if basis == "units_per_hour" and (op_map.speed_units_per_hour or 0.0) > 0:
                base_speed = op_map.speed_units_per_hour
        except Exception:
            pass
        if base_speed in (None, 0.0):
            base_speed = op_map.speed_units_per_hour or 0.0

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
                # Unknown basis: treat as units/hour for safety
                run_min = (60.0 / float(base_speed)) if base_speed > 0 else 0.0
        except Exception:
            run_min = 0.0

        # Optional scaling (reference / bucket rules)
        try:
            mode = (getattr(op_map, "scaling_mode", None) or "none")
            metric_vals = {
                "unit_area_m2": unit_area_m2,
                "sheet_area_m2": sheet_area_m2,
                "sheet_length_m": length_m,
            }
            mult = 1.0
            if mode == "reference":
                rm = getattr(op_map, "ref_metric", None)
                rv = getattr(op_map, "ref_value", None)
                if rm and rv and float(rv) > 0:
                    actual = float(metric_vals.get(rm) or 0.0)
                    if actual > 0:
                        mult = actual / float(rv)
            elif mode == "buckets":
                rules = getattr(op_map, "rule_ids", [])
                preferred = getattr(op_map, "ref_metric", None)
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
                lo = getattr(op_map, "scale_min", None)
                hi = getattr(op_map, "scale_max", None)
                if lo not in (None, "", 0, 0.0) and float(lo) > 0:
                    mult = max(float(lo), mult)
                if hi not in (None, "", 0, 0.0) and float(hi) > 0:
                    mult = min(float(hi), mult)
            except Exception:
                pass
            run_min *= mult
        except Exception:
            pass

        # Safety clamp: no negatives / NaN / infinities
        try:
            if run_min < 0:
                run_min = 0.0
            if run_min != run_min:  # NaN
                run_min = 0.0
        except Exception:
            run_min = 0.0

        return run_min

    def _get_duration_expected(self, alternative_workcenter=False, ratio=1):
        """Derive expected duration from corrugation operation mapping when available.

        This applies both to BoM-driven and manually created MOs:
        - If a corr.operation.map row exists for (operation name, work center),
          and it has a usable speed configured (any basis), we compute the
          duration based on that speed, metrics and the current quantity.
        - Otherwise, fallback to the standard MRP behavior.
        """
        self.ensure_one()

        # Fallback to standard behavior in all special cases first
        if alternative_workcenter or not self.workcenter_id:
            return super()._get_duration_expected(
                alternative_workcenter=alternative_workcenter, ratio=ratio
            )

        mapping_based_duration = None

        try:
            wc = self.workcenter_id
            norm = self._corr_normalize_op_name()
            if wc and norm:
                OpMap = self.env["corr.operation.map"].sudo()
                m = OpMap.search(
                    [
                        ("active", "=", True),
                        ("op_name", "=", norm),
                        ("workcenter_id", "=", wc.id),
                    ],
                    limit=1,
                )
                if m:
                    # Minutes per finished unit based on mapping (any basis)
                    run_min = self._corr_compute_run_minutes_from_map(m)
                    if run_min and run_min > 0:
                        # Compute cycles based on capacity
                        qty_production = self.production_id.product_uom_id._compute_quantity(
                            self.qty_producing or self.qty_production,
                            self.production_id.product_id.uom_id,
                        )
                        capacity = wc._get_capacity(self.product_id)
                        cycle_number = float_round(
                            qty_production / capacity,
                            precision_digits=0,
                            rounding_method="UP",
                        )
                        # Base expected duration from WC (setup / start/stop),
                        # plus any mapping-specific setup minutes.
                        try:
                            setup_extra = float(getattr(m, "setup_min", 0.0) or 0.0)
                        except Exception:
                            setup_extra = 0.0
                        base_wc = wc._get_expected_duration(self.product_id) + setup_extra
                        # Apply time efficiency the same way as core
                        mapping_based_duration = (
                            base_wc + cycle_number * run_min * 100.0 / wc.time_efficiency
                        )
        except Exception:
            # Never block MRP on mapping issues; just fall back.
            mapping_based_duration = None

        if mapping_based_duration is not None:
            return mapping_based_duration

        return super()._get_duration_expected(
            alternative_workcenter=alternative_workcenter, ratio=ratio
        )

    @api.onchange("workcenter_id", "operation_id", "name")
    def _onchange_corr_workcenter_mapping(self):
        """Validate work center choice against corr operation mapping.

        Behaviour:
        - If MO/BoM is corr-driven and the operation name is a known corr
          operation, require a mapping row for (op_name, workcenter_id).
        - If mapping exists but has no usable Units/hour speed, block and ask
          the user to configure it.
        - For non-corr MOs or non-corr operations, simply recompute expected
          duration without enforcing mapping.
        """
        OpMap = self.env["corr.operation.map"].sudo()
        for wo in self:
            if wo.state in ("done", "cancel"):
                continue

            wc = wo.workcenter_id
            if not wc:
                continue

            norm = wo._corr_normalize_op_name()
            if not norm:
                # No recognizable operation name; just recompute using core logic.
                wo.duration_expected = wo._get_duration_expected()
                continue

            # If there is no mapping anywhere for this op name, treat it as a
            # non-corr operation and don't enforce anything.
            any_for_op = OpMap.search(
                [("active", "=", True), ("op_name", "=", norm)], limit=1
            )
            if not any_for_op:
                wo.duration_expected = wo._get_duration_expected()
                continue

            # Placeholder work centers are allowed; they are already guarded
            # at start time by `workorder_placeholder_guard`.
            if getattr(wc, "corr_is_placeholder", False):
                wo.duration_expected = wo._get_duration_expected()
                continue

            # From here, we consider this a corr operation and expect a mapping
            # row for the chosen work center (for any MO using a known corr op).
            m = OpMap.search(
                [
                    ("active", "=", True),
                    ("op_name", "=", norm),
                    ("workcenter_id", "=", wc.id),
                ],
                limit=1,
            )
            if not m:
                # We have corr mappings for this operation name, but none for
                # the selected work center: treat as unsupported.
                raise UserError(
                    _(
                        "Work center '%(wc)s' is not configured in Corrugation Operation "
                        "Mapping for operation '%(op)s'.\n"
                        "Please add a corr.operation.map row for this operation and work "
                        "center, or choose a supported work center.",
                        wc=wc.display_name,
                        op=norm,
                    )
                )

            # Mapping exists: ensure it has a usable speed (any basis)
            run_min = wo._corr_compute_run_minutes_from_map(m)
            if run_min and run_min > 0:
                # Valid mapping: recompute using mapping-aware logic.
                wo.duration_expected = wo._get_duration_expected()
            else:
                # Mapping exists but can't be used to derive MO-level duration.
                raise UserError(
                    _(
                        "Corrugation Operation Mapping for '%(op)s' on work center '%(wc)s' "
                        "does not have a usable speed.\n"
                        "Please configure a positive Base Speed for the selected Speed "
                        "Basis (Units/hour, Sheets per hour, Meters per minute, or "
                        "Square meters per hour) and ensure the corrugation spec has "
                        "valid dimensions so runtime can be computed.",
                        wc=wc.display_name,
                        op=norm,
                    )
                )
