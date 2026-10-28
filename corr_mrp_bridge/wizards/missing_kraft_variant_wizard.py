from odoo import _, fields, models
from odoo.exceptions import UserError


class CorrMissingKraftVariantWizard(models.TransientModel):
    _name = "corr.missing.kraft.variant.wizard"
    _description = "Create Missing Kraft Paper Variants from BoM Preview"

    product_template_id = fields.Many2one(
        "product.template",
        string="Product",
        required=True,
        readonly=True,
    )
    return_wizard_id = fields.Many2one(
        "corr.bom.generate.wizard",
        string="BoM Wizard",
        readonly=True,
    )
    line_ids = fields.One2many(
        "corr.missing.kraft.variant.wizard.line",
        "wizard_id",
        string="Missing Variants",
    )

    @staticmethod
    def _format_numeric_value(value):
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

    def _get_attribute_line(self, attribute_name):
        self.ensure_one()
        attr_line = self.product_template_id.attribute_line_ids.filtered(
            lambda line: (line.attribute_id.name or "").lower() == attribute_name.lower()
        )[:1]
        if not attr_line:
            raise UserError(
                _("The selected template must contain the '%s' attribute line.")
                % attribute_name
            )
        return attr_line

    def _find_or_create_attribute_value(self, attribute_name, raw_value):
        self.ensure_one()
        attr_line = self._get_attribute_line(attribute_name)
        if attribute_name in ("GSM", "BF", "Deckle"):
            value_name = self._format_numeric_value(raw_value)
        else:
            value_name = str(raw_value or "").strip()
        if not value_name:
            raise UserError(_("Missing value for attribute '%s'.") % attribute_name)

        Value = self.env["product.attribute.value"].sudo()
        candidates = Value.search([("attribute_id", "=", attr_line.attribute_id.id)])
        value = candidates.filtered(
            lambda val: (val.name or "").strip().lower() == value_name.lower()
        )[:1]
        if not value and attribute_name in ("GSM", "BF", "Deckle"):
            value = candidates.filtered(lambda val: self._numeric_equal(val.name, value_name))[:1]
        if not value:
            value = Value.create({
                "attribute_id": attr_line.attribute_id.id,
                "name": value_name,
            })

        if value not in attr_line.value_ids:
            # Do not trigger Odoo's full variant matrix generation for Kraft
            # Paper. This wizard creates only the requested missing combinations.
            attr_line.with_context(
                update_product_template_attribute_values=False,
                create_product_product=False,
            ).write({"value_ids": [(4, value.id)]})
        self._ensure_template_attribute_value(attr_line, value)
        return value

    def _ensure_template_attribute_value(self, attr_line, value):
        self.ensure_one()
        PTAV = self.env["product.template.attribute.value"].sudo()
        ptav = PTAV.with_context(active_test=False).search([
            ("product_tmpl_id", "=", self.product_template_id.id),
            ("attribute_id", "=", attr_line.attribute_id.id),
            ("product_attribute_value_id", "=", value.id),
        ], limit=1)
        if ptav:
            vals = {}
            if not ptav.ptav_active:
                vals["ptav_active"] = True
            if ptav.attribute_line_id != attr_line:
                vals["attribute_line_id"] = attr_line.id
            if vals:
                ptav.write(vals)
            return ptav
        return PTAV.create({
            "product_attribute_value_id": value.id,
            "attribute_line_id": attr_line.id,
            "price_extra": getattr(value, "default_extra_price", 0.0),
        })

    def _prepare_line_values(self, line):
        self.ensure_one()
        return [
            self._find_or_create_attribute_value("GSM", line.gsm),
            self._find_or_create_attribute_value("BF", line.bf),
            self._find_or_create_attribute_value("Color", line.color),
            self._find_or_create_attribute_value("Deckle", line.deckle),
        ]

    def _format_color_code(self, color_name):
        words = (color_name or "").strip().split()
        if not words:
            return ""
        if len(words) == 1:
            return words[0].upper()
        return "".join(word[0].upper() for word in words)

    def _action_return_to_bom_wizard(self):
        self.ensure_one()
        if self.return_wizard_id.exists():
            return self.return_wizard_id.action_prepare()
        return {"type": "ir.actions.act_window_close"}

    def action_cancel(self):
        self.ensure_one()
        return self._action_return_to_bom_wizard()

    def action_create_variants(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Please add at least one missing variant row."))

        Product = self.env["product.product"].sudo()
        existing_combinations = {
            tuple(sorted(variant.product_template_attribute_value_ids.mapped(
                "product_attribute_value_id"
            ).ids)): variant
            for variant in Product.search([
                ("product_tmpl_id", "=", self.product_template_id.id)
            ])
        }
        seen_combinations = set()

        for line in self.line_ids:
            values = self._prepare_line_values(line)
            value_ids = [value.id for value in values]
            combo_key = tuple(sorted(value_ids))
            if combo_key in seen_combinations:
                raise UserError(
                    _("Duplicate missing variant row detected: Deckle %(deckle)s, Color %(color)s, GSM %(gsm)s, BF %(bf)s.")
                    % {
                        "deckle": line.deckle,
                        "color": line.color,
                        "gsm": line.gsm,
                        "bf": line.bf,
                    }
                )
            seen_combinations.add(combo_key)

            if combo_key in existing_combinations:
                continue

            ptav_records = self.env["product.template.attribute.value"].sudo().search([
                ("product_tmpl_id", "=", self.product_template_id.id),
                ("product_attribute_value_id", "in", value_ids),
                ("ptav_active", "=", True),
            ])
            if len(ptav_records) != len(value_ids):
                raise UserError(
                    _("Some attribute values are not linked to template '%s'.")
                    % self.product_template_id.display_name
                )

            variant = self.product_template_id._get_variant_for_combination(ptav_records)
            if not variant:
                variant = Product.create({
                    "product_tmpl_id": self.product_template_id.id,
                    "product_template_attribute_value_ids": [(6, 0, ptav_records.ids)],
                    "default_code": "%s-%s-%s-%s-%s" % (
                        self.product_template_id.default_code or "KP",
                        values[0].name,
                        values[3].name,
                        self._format_color_code(values[2].name),
                        values[1].name,
                    ),
                })
            existing_combinations[combo_key] = variant

        return self._action_return_to_bom_wizard()


class CorrMissingKraftVariantWizardLine(models.TransientModel):
    _name = "corr.missing.kraft.variant.wizard.line"
    _description = "Missing Kraft Paper Variant Line"

    wizard_id = fields.Many2one(
        "corr.missing.kraft.variant.wizard",
        required=True,
        ondelete="cascade",
    )
    deckle = fields.Char(required=True)
    color = fields.Char(required=True)
    gsm = fields.Char(string="GSM", required=True)
    bf = fields.Char(string="BF", required=True)
