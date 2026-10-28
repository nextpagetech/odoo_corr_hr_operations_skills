import logging
import re

from odoo import SUPERUSER_ID, api, fields, models


_logger = logging.getLogger(__name__)


class CorrOperationType(models.Model):
    _name = "corr.operation.type"
    _description = "Corrugation Operation Type"
    _order = "sequence, id"

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        index=True,
        help="Leave empty for a global operation type shared across companies.",
    )
    is_finishing = fields.Boolean(
        string="Is Finishing Operation",
        default=False,
        help="Classification flag only; does not affect any planning or runtime logic.",
    )
    use_in_workorder = fields.Boolean(
        string="Use in Work Orders",
        default=True,
        help="When disabled, the operation is hidden from work order selection.",
    )

    _sql_constraints = [
        (
            "corr_operation_type_code_company_unique",
            "unique(code, company_id)",
            "Each operation code must be unique per company.",
        )
    ]

    def name_get(self):
        result = []
        for rec in self:
            name = rec.name or rec.code
            if rec.company_id:
                name = "%s (%s)" % (name, rec.company_id.display_name)
            result.append((rec.id, name))
        return result

    # ---- Synchronisation with existing corrugation operation selection ----

    def _table_exists(self):
        self.env.cr.execute("SELECT to_regclass(%s)", (self._table,))
        result = self.env.cr.fetchone()
        return bool(result and result[0])

    @api.model
    def _corr_selection_source(self):
        """Return the corrugation operation selection used in corr.operation.map.

        This keeps the operation type master seeded from the same canonical list
        used by the mapping model, without enforcing strict one-way sync. Newly
        created operation types are not deleted if the selection changes.
        """
        try:
            CorrOperationMap = self.env["corr.operation.map"]
        except Exception:
            return []
        field = CorrOperationMap._fields.get("op_name")
        return list(field.selection or []) if field and field.selection else []

    @api.model
    def sync_from_corr_selection(self):
        """Ensure a CorrOperationType exists for each configured corr operation.

        Idempotent and safe to call multiple times.
        """
        # On a fresh install or during module init, the table might not
        # exist yet; in that case simply skip seeding for now.
        if not self._table_exists():
            return
        selection = self._corr_selection_source()
        if not selection:
            return

        existing = {
            (t.code, t.company_id.id or 0): t
            for t in self.search([("company_id", "=", False)])
        }
        sequence = 10
        for code, label in selection:
            key = (code, 0)
            rec = existing.get(key) or self.search(
                [("code", "=", code), ("company_id", "=", False)], limit=1
            )
            vals = {
                "name": label or code,
                "code": code,
                "sequence": sequence,
                "active": True,
            }
            sequence += 10
            if rec:
                rec.write(
                    {
                        field: value
                        for field, value in vals.items()
                        if value is not None and rec[field] != value
                    }
                )
            else:
                self.create(vals)

    @api.model
    def _setup_complete(self):
        res = super()._setup_complete()
        # Data seeding is handled in post_init_hook / _register_hook via
        # migrate_workorder_operation_types to avoid schema upgrade issues.
        return res

    # ---- Helpers for mapping from free-text labels ----

    @staticmethod
    def _slugify(text):
        """Generate a simple code from a free-text label."""
        text = (text or "").strip().lower()
        if not text:
            return ""
        # Replace non-alphanumeric characters with underscores, collapse repeats.
        text = re.sub(r"[^a-z0-9]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        return text or "legacy"

    @api.model
    def find_from_label(self, label, company=None):
        """Find an operation type from a free-text label.

        Matches on code or name, case-insensitive. Returns an empty recordset
        when no match is found.
        """
        if not label:
            return self.env["corr.operation.type"]
        text = (label or "").strip()
        if not text:
            return self.env["corr.operation.type"]
        text_lower = text.lower()

        domain = [
            ("active", "=", True),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", (company or self.env.company).id),
        ]
        candidates = self.search(domain)
        for rec in candidates:
            if (rec.code or "").strip().lower() == text_lower:
                return rec
        for rec in candidates:
            if (rec.name or "").strip().lower() == text_lower:
                return rec
        return self.env["corr.operation.type"]

    # ---- Migration of existing work orders ----

    @api.model
    def migrate_workorder_operation_types(self):
        """Map existing work order Operation text to corr.operation.type.

        - For each mrp.workorder without corr_operation_type_id, derive an
          operation type from work order name (or linked routing operation
          name as fallback).
        - Create new operation types when no suitable one exists yet.
        - Idempotent: running multiple times won't duplicate types or
          assignments.
        """
        # Guard against running repeatedly on every registry load.
        ICP = self.env["ir.config_parameter"].sudo()
        if ICP.get_param("corr_mrp_bridge.workorder_optype_migrated"):
            return

        OpType = self.with_context(active_test=False)
        Workorder = self.env["mrp.workorder"].with_context(active_test=False)

        # Ensure the master list at least reflects the corr.operation.map selection.
        try:
            OpType.sync_from_corr_selection()
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning(
                "corr_mrp_bridge migration: sync_from_corr_selection failed: %s", exc
            )

        workorders = Workorder.search([("corr_operation_type_id", "=", False)])
        if not workorders:
            ICP.set_param("corr_mrp_bridge.workorder_optype_migrated", "1")
            return

        _logger.info(
            "corr_mrp_bridge migration: migrating corr_operation_type_id on %s existing work orders.",
            len(workorders),
        )

        # Build an index of existing types by lowercase name/code for quick lookup.
        types = OpType.search([])
        index = {}
        for t in types:
            key_code = (t.code or "").strip().lower()
            key_name = (t.name or "").strip().lower()
            if key_code:
                index.setdefault(key_code, t)
            if key_name:
                index.setdefault(key_name, t)

        def _get_or_create_type(label):
            if not label:
                return OpType.browse()
            raw = (label or "").strip()
            if not raw:
                return OpType.browse()
            key = raw.lower()
            if key in index:
                return index[key]
            code = OpType._slugify(raw)
            # Ensure uniqueness of the generated code (company-agnostic types).
            unique_code = code
            suffix = 1
            while OpType.search_count(
                [("code", "=", unique_code), ("company_id", "=", False)]
            ):
                suffix += 1
                unique_code = "%s_%s" % (code, suffix)
            new_type = OpType.create(
                {
                    "name": raw,
                    "code": unique_code,
                    "company_id": False,
                    "use_in_workorder": True,
                    "active": True,
                }
            )
            index[key] = new_type
            return new_type

        for wo in workorders:
            label = (wo.name or "").strip()
            if not label and wo.operation_id:
                label = (wo.operation_id.name or "").strip()
            op_type = _get_or_create_type(label)
            if op_type:
                wo.corr_operation_type_id = op_type.id

        ICP.set_param("corr_mrp_bridge.workorder_optype_migrated", "1")

    def _register_hook(self):
        """Run one-off migration on registry load."""
        super()._register_hook()
        try:
            self.env["corr.operation.type"].migrate_workorder_operation_types()
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning(
                "corr_mrp_bridge _register_hook: failed to migrate work order operation types: %s",
                exc,
            )
