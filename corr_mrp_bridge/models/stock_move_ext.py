from odoo import api, fields, models

ROLE_SELECTION = [
    ("liner", "Liner"),
    ("medium", "Medium"),
    ("adhesive", "Adhesive"),
    ("top_sheet", "Top Sheet"),
    ("ink", "Ink"),
    ("lamination", "Lamination Film"),
    ("wire", "Wire"),
    ("tape", "Tape"),
]


class StockMove(models.Model):
    _inherit = "stock.move"

    corr_role = fields.Selection(
        selection=ROLE_SELECTION,
        related="bom_line_id.corr_role",
        store=True,
        readonly=True,
    )
    corr_layer_no = fields.Integer(
        string="Layer #",
        related="bom_line_id.corr_layer_no",
        store=True,
        readonly=True,
    )
    # Planning metadata (used when BoM does not carry corr_role/layer)
    corr_planning_role = fields.Selection(
        selection=ROLE_SELECTION,
        string="Planning Role",
        help="Role as determined by the corrugation planning wizard when available.",
    )
    corr_planning_layer_no = fields.Integer(
        string="Planning Layer #",
        help="Layer index as determined by the corrugation planning wizard when available.",
    )
    corr_layer_label = fields.Char(
        string="Layer",
        compute="_compute_corr_layer_label",
        readonly=True,
    )
    corr_is_alt = fields.Boolean(string="Deviation Alternative", default=False)

    @api.depends(
        "corr_layer_no",
        "corr_role",
        "corr_planning_layer_no",
        "corr_planning_role",
        "corr_is_alt",
        "product_uom",
        "product_uom.name",
        "product_uom.category_id.name",
    )
    def _compute_corr_layer_label(self):
        role_label_map = {
            "liner": self.env._("Liner"),
            "medium": self.env._("Flute"),
            "adhesive": self.env._("Adhesive"),
            "top_sheet": self.env._("Top Sheet"),
            "ink": self.env._("Ink"),
            "lamination": self.env._("Lamination Film"),
            "wire": self.env._("Wire"),
            "tape": self.env._("Tape"),
        }

        def _top_sheet_label(move):
            uom = move.product_uom
            if not uom:
                return self.env._("Top Sheet")
            uom_name = (uom.name or "").strip().lower()
            cat_name = (uom.category_id.name or "").strip().lower()
            if any(token in cat_name for token in ("weight", "mass")) or uom_name in {"kg", "g", "lb", "lbs"}:
                return self.env._("Top Sheet (Roll)")
            if any(token in cat_name for token in ("unit", "units")) or any(
                token in uom_name for token in ("unit", "units", "sheet", "sheets", "pcs", "pc")
            ):
                return self.env._("Top Sheet (Units)")
            return self.env._("Top Sheet")

        for move in self:
            role_code = (move.corr_role or move.corr_planning_role or "" or "").lower()
            layer = int(move.corr_layer_no or move.corr_planning_layer_no or 0)
            label = ""
            base_role = move.corr_role or move.corr_planning_role or ""
            if layer:
                role_label = (
                    _top_sheet_label(move)
                    if role_code == "top_sheet"
                    else (role_label_map.get(role_code) or (base_role.title() if base_role else self.env._("Layer")))
                )
                label = self.env._("Layer %s - %s") % (layer, role_label)
            elif role_code:
                role_label = (
                    _top_sheet_label(move)
                    if role_code == "top_sheet"
                    else (role_label_map.get(role_code) or (base_role.title() if base_role else ""))
                )
                label = role_label
            if label and move.corr_is_alt:
                # Make it explicit that this component line comes from a deviation/alternative.
                label = self.env._("%s (Deviation)") % label
            move.corr_layer_label = label
