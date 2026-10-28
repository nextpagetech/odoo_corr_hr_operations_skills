# Corrugation ↔ MRP Bridge (Odoo 18 CE)

Generate and maintain Manufacturing BoMs from corrugation specs. Provide compute previews on Product and MO, optional variance logging at MO confirm, and a simple deckle planning helper.

## Features

- Material mapping: map liners/mediums (gsm + flute) and consumables (adhesive/ink/film/wire/tape) to products.
- Auto‑map fallback: if no explicit row, resolves product variants by Attributes (Flute + GSM within tolerance).
- BoM upsert from Spec (per‑unit): idempotent by `corr_component_key`, stores engine snapshot and signature.
- BoM behavior:
  - `consumption = flexible`
  - `lock_manual_lines` on BoM + `corr_manual` on BoM line to preserve manual edits during sync
- Previews:
  - Product: Corr Compute Preview (qty=1)
  - MO: Corr Preview (qty = MO qty)
- MO variance logging (flagged): on confirm, recompute and log differences vs BoM (no auto‑edit)
- Deckle plan wizard: plan against default plant reel widths; writes note on MO

## Menus

- Corrugation ▸ Tools ▸ MRP Bridge ▸ Material Mapping
- Manufacturing ▸ Corrugation (Bridge) ▸ Material Mapping

## Flags (System Parameters)

- `corr_mrp_bridge.mo_variance_check` = `1` to enable variance logging at MO confirm
- `corr_mrp_bridge.auto_map_tolerance_pct` = percentage override for GSM matching (fallback). If not set, uses Plant `gsm_tolerance_pct`.

## Rollback & Safety

- The bridge never edits BoMs on MO confirm.
- Use `lock_manual_lines` + `corr_manual` to protect special lines from sync removal.
- Remove the bridge safely: it only adds fields/menus; BoMs remain usable.

## Tests (suggested)

- BoM sync: create 5‑ply spec + mappings → generate BoM; assert lines exist and totals > 0.
- Variance logging: distort a BoM line, enable flag, confirm MO → chatter contains variance lines.
