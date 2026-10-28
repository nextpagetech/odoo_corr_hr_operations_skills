from odoo import api, fields, models

from ..engine_style_display import style_display_name


class CorrEngineStyle(models.Model):
    _name = "corr.engine.style"
    _description = "Engine Style"
    _order = "code"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    style_display_name = fields.Char(
        string="Style",
        compute="_compute_style_display_name",
        store=False,
    )
    family = fields.Selection(
        [
            ("box", "Box"),
            ("sheet", "Sheet"),
            ("diecut_sheet", "Diecut Sheet"),
            ("roll", "Roll"),
            ("pad", "Pad"),
            ("mono_carton", "Mono Carton"),
        ],
        default="box",
        required=True,
    )
    active = fields.Boolean(default=True)
    description = fields.Text(string="Description / Help")
    param_ids = fields.One2many(
        "corr.engine.style.param",
        "style_id",
        string="Parameters",
    )

    @api.depends("name", "code")
    def _compute_display_name(self):
        for style in self:
            style.display_name = style_display_name(style.name, style.code)

    @api.depends("name", "code")
    def _compute_style_display_name(self):
        for style in self:
            style.style_display_name = style_display_name(style.name, style.code)

    @api.model
    def seed_default_styles(self):
        """Idempotently seed default style masters and parameter definitions."""

        imd = self.env["ir.model.data"].sudo()

        def _bind_xmlid(xmlid_name, record):
            xmlid = imd.search(
                [
                    ("module", "=", "corr_engine_v2"),
                    ("name", "=", xmlid_name),
                    ("model", "=", record._name),
                ],
                limit=1,
            )
            if xmlid:
                xmlid.res_id = record.id
            else:
                imd.create(
                    {
                        "module": "corr_engine_v2",
                        "name": xmlid_name,
                        "model": record._name,
                        "res_id": record.id,
                        "noupdate": False,
                    }
                )

        def _style(code, vals, xmlid_name):
            records = self.search([("code", "=", code)], order="id")
            if records:
                primary = records[0]
                primary.write(vals)
                duplicates = records - primary
                if duplicates:
                    self.env["corr.engine.v2.spec"].search(
                        [("style_id", "in", duplicates.ids)]
                    ).write({"style_id": primary.id})
                    self.env["corr.engine.v2.spec.style.param.value"].search(
                        [("style_id", "in", duplicates.ids)]
                    ).write({"style_id": primary.id})
                    duplicates.write({"active": False})
            else:
                primary = self.create({"code": code, **vals})
            _bind_xmlid(xmlid_name, primary)
            return primary

        style_0201 = _style(
            "0201",
            {
                "name": "Regular Slotted Container",
                "family": "box",
                "description": "FEFCO 0201 baseline box style. No extra style parameters required.",
                "active": True,
            },
            "corr_engine_style_0201",
        )
        style_0100 = _style(
            "0100",
            {
                "name": "Roll Product",
                "family": "roll",
                "description": "FEFCO 0100 corrugated roll product. Uses Roll Length and Roll Width from main geometry. No extra style parameters required.",
                "active": True,
            },
            "corr_engine_style_0100",
        )
        style_0110 = _style(
            "0110",
            {
                "name": "Corrugated Sheet",
                "family": "sheet",
                "description": "FEFCO 0110 corrugated sheet product. Uses main geometry length and width as sheet size. No extra style parameters required.",
                "active": True,
            },
            "corr_engine_style_0110",
        )
        style_diecut_sheet = _style(
            "DIECUT_SHEET",
            {
                "name": "Custom Diecut Sheet",
                "family": "diecut_sheet",
                "description": "Manual sheet size with FG-per-sheet yield. Uses selected Engine V2 routing; diecut stage is optional.",
                "active": True,
            },
            "corr_engine_style_diecut_sheet",
        )
        style_0202 = _style(
            "0202",
            {
                "name": "Overlap Slotted Container",
                "family": "box",
                "description": "FEFCO 0202 overlap slotted container. Requires overlap depth.",
                "active": True,
            },
            "corr_engine_style_0202",
        )
        style_0203 = _style(
            "0203",
            {
                "name": "Full Overlap Slotted Container",
                "family": "box",
                "description": "FEFCO 0203 full overlap slotted container. No extra style parameters required.",
                "active": True,
            },
            "corr_engine_style_0203",
        )
        style_mono_ste = _style(
            "MONO_STE",
            {
                "name": "Mono Carton - Straight Tuck End",
                "family": "mono_carton",
                "description": "Mono carton straight tuck end style. Uses glue flap, tuck flap, trim, bleed, and grain direction style parameters.",
                "active": True,
            },
            "corr_engine_style_mono_ste",
        )

        # Ensure parameter-free styles remain clean after upgrades without
        # deleting parameters referenced by existing spec parameter values.
        for style in (style_0100, style_0110, style_0201, style_0203):
            style.param_ids.write({"active": False})

        Param = self.env["corr.engine.style.param"].sudo()

        overlap = Param.search(
            [("style_id", "=", style_0202.id), ("key", "=", "overlap_mm")],
            limit=1,
        )
        vals = {
            "style_id": style_0202.id,
            "sequence": 10,
            "key": "overlap_mm",
            "label": "Overlap",
            "param_type": "float",
            "unit": "mm",
            "default_value": "25",
            "required": True,
            "min_value": 0.001,
            "help_text": "Overlap depth added to each flap side for FEFCO 0202.",
            "active": True,
        }
        if overlap:
            overlap.write(vals)
        else:
            overlap = Param.create(vals)
        _bind_xmlid("corr_engine_style_0202_param_overlap_mm", overlap)

        fg_per_sheet = Param.search(
            [("style_id", "=", style_diecut_sheet.id), ("key", "=", "fg_per_sheet")],
            limit=1,
        )
        vals = {
            "style_id": style_diecut_sheet.id,
            "sequence": 10,
            "key": "fg_per_sheet",
            "label": "FG per Sheet",
            "param_type": "integer",
            "unit": "fg/sheet",
            "default_value": "1",
            "required": True,
            "min_value": 1.0,
            "help_text": "Number of finished-good blanks produced from one manually entered sheet.",
            "active": True,
        }
        if fg_per_sheet:
            fg_per_sheet.write(vals)
        else:
            fg_per_sheet = Param.create(vals)
        _bind_xmlid("corr_engine_style_diecut_sheet_param_fg_per_sheet", fg_per_sheet)

        mono_params = [
            (
                "corr_engine_style_mono_ste_param_carton_board_gsm",
                {
                    "sequence": 5,
                    "key": "carton_board_gsm",
                    "label": "Carton Board GSM",
                    "param_type": "float",
                    "unit": "gsm",
                    "default_value": "300",
                    "required": True,
                    "min_value": 1.0,
                    "help_text": "GSM of the primary FBB/SBS/duplex carton board.",
                },
            ),
            (
                "corr_engine_style_mono_ste_param_carton_board_source_mode",
                {
                    "sequence": 6,
                    "key": "carton_board_source_mode",
                    "label": "Carton Board Source Mode",
                    "param_type": "selection",
                    "unit": "",
                    "default_value": "auto",
                    "required": True,
                    "min_value": 0.0,
                    "help_text": "Auto uses unit-sheet quantity when the selected carton board product UoM is Units; otherwise GSM area weight is used.",
                },
            ),
            (
                "corr_engine_style_mono_ste_param_carton_board_ups",
                {
                    "sequence": 7,
                    "key": "carton_board_ups",
                    "label": "Carton Board UPS",
                    "param_type": "integer",
                    "unit": "fg/sheet",
                    "default_value": "1",
                    "required": True,
                    "min_value": 1.0,
                    "help_text": "Number of mono carton blanks produced from one selected carton board sheet when the source is unit based.",
                },
            ),
            (
                "corr_engine_style_mono_ste_param_glue_flap_mm",
                {
                    "sequence": 10,
                    "key": "glue_flap_mm",
                    "label": "Glue Flap",
                    "param_type": "float",
                    "unit": "mm",
                    "default_value": "20",
                    "required": True,
                    "min_value": 0.001,
                    "help_text": "Side glue flap allowance for the mono carton blank.",
                },
            ),
            (
                "corr_engine_style_mono_ste_param_top_tuck_flap_mm",
                {
                    "sequence": 20,
                    "key": "top_tuck_flap_mm",
                    "label": "Top Tuck Flap",
                    "param_type": "float",
                    "unit": "mm",
                    "default_value": "0",
                    "required": False,
                    "min_value": 0.0,
                    "help_text": "Top tuck flap depth. Leave zero to use carton width.",
                },
            ),
            (
                "corr_engine_style_mono_ste_param_bottom_tuck_flap_mm",
                {
                    "sequence": 30,
                    "key": "bottom_tuck_flap_mm",
                    "label": "Bottom Tuck Flap",
                    "param_type": "float",
                    "unit": "mm",
                    "default_value": "0",
                    "required": False,
                    "min_value": 0.0,
                    "help_text": "Bottom tuck flap depth. Leave zero to use carton width.",
                },
            ),
            (
                "corr_engine_style_mono_ste_param_trim_allowance_mm",
                {
                    "sequence": 40,
                    "key": "trim_allowance_mm",
                    "label": "Trim Allowance",
                    "param_type": "float",
                    "unit": "mm",
                    "default_value": "0",
                    "required": False,
                    "min_value": 0.0,
                    "help_text": "Total trim allowance added to blank length and width.",
                },
            ),
            (
                "corr_engine_style_mono_ste_param_bleed_mm",
                {
                    "sequence": 50,
                    "key": "bleed_mm",
                    "label": "Bleed",
                    "param_type": "float",
                    "unit": "mm",
                    "default_value": "3",
                    "required": False,
                    "min_value": 0.0,
                    "help_text": "Print bleed allowance retained as style metadata.",
                },
            ),
            (
                "corr_engine_style_mono_ste_param_grain_direction",
                {
                    "sequence": 60,
                    "key": "grain_direction",
                    "label": "Grain Direction",
                    "param_type": "selection",
                    "unit": "",
                    "default_value": "height",
                    "required": True,
                    "min_value": 0.0,
                    "help_text": "Preferred carton board grain direction for layout/orientation rules.",
                },
            ),
        ]
        active_mono_keys = []
        for xmlid_name, vals in mono_params:
            active_mono_keys.append(vals["key"])
            values = {"style_id": style_mono_ste.id, "active": True, **vals}
            param = Param.search(
                [("style_id", "=", style_mono_ste.id), ("key", "=", vals["key"])],
                limit=1,
            )
            if param:
                param.write(values)
            else:
                param = Param.create(values)
            _bind_xmlid(xmlid_name, param)
        style_mono_ste.param_ids.filtered(lambda p: p.key not in active_mono_keys).write({"active": False})

        return True


class CorrEngineStyleParam(models.Model):
    _name = "corr.engine.style.param"
    _description = "Engine Style Parameter"
    _order = "style_id, sequence, id"

    style_id = fields.Many2one(
        "corr.engine.style",
        string="Style",
        required=True,
        ondelete="cascade",
    )
    key = fields.Char(required=True, index=True)
    label = fields.Char(required=True)
    param_type = fields.Selection(
        [
            ("float", "Float"),
            ("integer", "Integer"),
            ("boolean", "Boolean"),
            ("selection", "Selection"),
            ("text", "Text"),
        ],
        required=True,
        default="float",
    )
    unit = fields.Char()
    default_value = fields.Char()
    required = fields.Boolean(default=False)
    min_value = fields.Float()
    max_value = fields.Float()
    help_text = fields.Text()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)




