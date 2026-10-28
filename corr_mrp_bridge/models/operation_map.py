from odoo import api, fields, models, _


class CorrOperationMap(models.Model):
    _name = "corr.operation.map"
    _description = "Corrugation Operation → Workcenter Mapping"
    _order = "sequence, id"
    _rec_name = "op_name"

    op_name = fields.Selection([
        ("Corrugation", "Corrugation"),
        ("Cutting", "Cutting"),
        ("Printing", "Printing"),
        ("Film Lamination", "Film Lamination"),
        ("Lamination", "Lamination"),
        ("Slot/Die", "Slot/Die"),
        ("Seaming", "Seaming"),
        ("Peeling", "Peeling"),
        ("Stitching", "Stitching"),
        ("Folding", "Folding"),
        ("Folding (Manual)", "Folding (Manual)"),
        ("Flap Pasting", "Flap Pasting"),
        ("Bottom Lock", "Bottom Lock"),
        ("QC", "Quality Check"),
    ], required=True, string="Operation")
    workcenter_id = fields.Many2one("mrp.workcenter", required=False)
    setup_min = fields.Float(string="Setup (min)", default=0.0)
    # Throughput basis and speed
    speed_basis = fields.Selection([
        ("units_per_hour", "Units per hour"),
        ("sheets_per_hour", "Sheets per hour"),
        ("meters_per_min", "Meters per minute"),
        ("sqm_per_hour", "Square meters per hour"),
    ], string="Speed Basis", default="units_per_hour")
    base_speed = fields.Float(string="Base Speed", default=600.0, help="Speed value in the selected basis.")
    # Backward-compat: keep old field, read-only mirror for legacy views/exports
    speed_units_per_hour = fields.Float(string="Speed (units/hour)", default=600.0,
                                        help="Legacy field. Mirrors when basis is Units/hour.", readonly=False)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    # Optional selection rules
    flute_code = fields.Char(string="Flute Code", help="Apply only for this flute (Corrugation)")
    deckle_min_cm = fields.Float(string="Deckle Min (cm)")
    deckle_max_cm = fields.Float(string="Deckle Max (cm)")
    lamination_top_only = fields.Boolean(string="Top-sheet Lamination Only", help="When set, mapping is used only for top-sheet lamination stage")

    # Optional size-based scaling
    scaling_mode = fields.Selection([
        ("none", "No scaling"),
        ("reference", "Reference-size scaling"),
        ("buckets", "Size buckets (multipliers)"),
    ], string="Scaling Mode", default="none")
    ref_metric = fields.Selection([
        ("unit_area_m2", "Unit area (m²)"),
        ("sheet_area_m2", "Sheet area (m²)"),
        ("sheet_length_m", "Sheet length (m)"),
    ], string="Reference Metric")
    ref_value = fields.Float(string="Reference Value")
    # Use None by default so '0' is not treated as an active clamp.
    scale_min = fields.Float(string="Min Multiplier", default=False,
                             help="Clamp: minimum overall multiplier applied after scaling. Leave empty to ignore.")
    scale_max = fields.Float(string="Max Multiplier", default=False,
                             help="Clamp: maximum overall multiplier applied after scaling. Leave empty to ignore.")

    rule_ids = fields.One2many(
        "corr.operation.size.rule",
        "map_id",
        string="Size Rules",
        help="Optional size buckets used when Scaling Mode = Buckets.")

    @api.onchange("workcenter_id")
    def _onchange_workcenter_prefill_rules(self):
        for rec in self:
            wc = rec.workcenter_id
            if not wc:
                continue
            # Only prefill if the mapping fields are not set
            if not rec.flute_code and wc.corr_flutes_supported:
                # take the first flute as default if multiple are set
                try:
                    vals = [x.strip() for x in (wc.corr_flutes_supported or "").split(",") if x.strip()]
                    rec.flute_code = vals[0] if vals else rec.flute_code
                except Exception:
                    rec.flute_code = rec.flute_code
            if not rec.deckle_min_cm and wc.corr_deckle_min_cm:
                rec.deckle_min_cm = wc.corr_deckle_min_cm
            if not rec.deckle_max_cm and wc.corr_deckle_max_cm:
                rec.deckle_max_cm = wc.corr_deckle_max_cm
            # For lamination rows, prefill top-only flag
            if rec.op_name == "Lamination" and not rec.lamination_top_only and wc.corr_lamination_top_only:
                rec.lamination_top_only = True

            # Prefill speed fields from legacy/compatible values
            # If user previously used units/hour, mirror into the new fields
            try:
                if (rec.speed_basis or "units_per_hour") == "units_per_hour" and (rec.base_speed in (0.0, None)):
                    if rec.speed_units_per_hour:
                        rec.base_speed = rec.speed_units_per_hour
                # Keep legacy mirror in sync for units/hour
                if (rec.speed_basis or "units_per_hour") == "units_per_hour" and rec.base_speed:
                    rec.speed_units_per_hour = rec.base_speed
            except Exception:
                pass

    @api.constrains("speed_units_per_hour")
    def _check_speed(self):
        for rec in self:
            if rec.speed_units_per_hour < 0:
                rec.speed_units_per_hour = 0.0

    def name_get(self):
        result = []
        selection = dict(self._fields["op_name"].selection)
        for record in self:
            label = selection.get(record.op_name, record.op_name)
            if record.workcenter_id:
                label = "%s - %s" % (label, record.workcenter_id.display_name)
            result.append((record.id, label))
        return result
    @api.constrains("base_speed")
    def _check_base_speed(self):
        for rec in self:
            if rec.base_speed is not None and rec.base_speed < 0:
                rec.base_speed = 0.0

    def _setup_complete(self):
        res = super()._setup_complete()
        self._ensure_throughput_columns()
        return res

    def _ensure_throughput_columns(self):
        cr = self.env.cr
        table = self._table
        if not self._table_exists(cr, table):
            return
        if not self._column_exists(cr, table, "speed_basis"):
            self._add_column(cr, table, "speed_basis", "varchar", "units_per_hour")
        if not self._column_exists(cr, table, "base_speed"):
            self._add_column(cr, table, "base_speed", "numeric", 0.0)
        if not self._column_exists(cr, table, "speed_units_per_hour"):
            self._add_column(cr, table, "speed_units_per_hour", "numeric", 0.0)

    @staticmethod
    def _table_exists(cr, table):
        cr.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_name = %s
            """,
            (table,),
        )
        return bool(cr.fetchone())

    @staticmethod
    def _column_exists(cr, table, column):
        cr.execute(
            """
            SELECT 1
              FROM information_schema.columns
             WHERE table_name = %s AND column_name = %s
            """,
            (table, column),
        )
        return bool(cr.fetchone())

    @staticmethod
    def _add_column(cr, table, column, definition, default=None):
        cr.execute('ALTER TABLE "%s" ADD COLUMN "%s" %s' % (table, column, definition))
        if default is not None:
            cr.execute(
                'UPDATE "%s" SET "%s" = %%s WHERE "%s" IS NULL' % (table, column, column),
                (default,),
            )


class CorrOperationSizeRule(models.Model):
    _name = "corr.operation.size.rule"
    _description = "Operation Size Rule (Multiplier Buckets)"
    _order = "sequence, id"

    map_id = fields.Many2one("corr.operation.map", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    metric = fields.Selection([
        ("unit_area_m2", "Unit area (m²)"),
        ("sheet_area_m2", "Sheet area (m²)"),
        ("sheet_length_m", "Sheet length (m)"),
    ], string="Metric", required=True, default="unit_area_m2")
    min_value = fields.Float(string="Min (>=)")
    max_value = fields.Float(string="Max (<)")
    multiplier = fields.Float(string="Multiplier", default=1.0)
