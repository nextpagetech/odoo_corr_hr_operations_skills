# Bulk Upload Engine V2 Rollout Notes

## Feature Flag

System parameter:

`corr_mrp_bridge.bulk_upload_use_engine_v2`

- `False`: bulk upload continues through the legacy corrugation flow.
- `True`: bulk upload uses the native Engine V2 flow.

Recommended rollout position:

- keep `False` for customers still running the legacy upload path
- set `True` for pilot/migration customers who should create Engine V2 specs and BoMs from upload
- switch customer-by-customer, not globally by assumption

## Legacy Path

When the flag is OFF:

- bulk upload continues to use the legacy bulk-upload logic
- legacy `corr.spec.template` behavior remains available
- existing customer data and operational flow are not forced onto Engine V2

## Engine V2 Path

When the flag is ON:

- bulk upload creates or updates `product.template`
- bulk upload creates or updates `corr.engine.v2.spec` directly
- Engine V2 preview is executed from the V2 spec
- preview payload is persisted on the spec through `last_preview_raw_json`
- persisted preview payload is the source of truth for V2 BoM creation
- bulk upload does not create `corr.spec.template`
- re-upload of the same stable product key updates the existing product/spec/BoM flow instead of intentionally duplicating records

Operational source of truth in V2 mode:

- product import row creates or updates the product
- product diagnostics show row outcome and failure stage
- `corr.engine.v2.spec` holds the working manufacturing definition
- `last_preview_raw_json` on the spec is the persisted preview snapshot used for BoM creation

## What V2 Creates

With the flag ON, a valid row can create or update:

- `product.template`
- `corr.engine.v2.spec`
- preview snapshot fields on the V2 spec
- `mrp.bom` records from persisted preview payload

It does not create:

- legacy `corr.spec.template`

## Diagnostics

Product-level admin diagnostics are stored on `product.template`:

- `corr_autobom_status`
- `corr_autobom_backend`
- `corr_autobom_message`
- `corr_autobom_last_run_at`
- `corr_autobom_error_stage`
- `corr_autobom_error_payload`
- `corr_autobom_spec_id`
- `corr_autobom_preview_status`
- `corr_autobom_bom_status`

These fields are intended for rollout support, row-wise import diagnostics, and production troubleshooting.

Important implementation note:

- `corr_autobom_spec_id` is stored as a plain integer spec id in the bridge
- it is not a bridge-side many2one to `corr.engine.v2.spec`
- this avoids a circular dependency between `corr_mrp_bridge` and `corr_engine_v2`
- if clickable navigation to the V2 spec is needed, implement that as an action on the Engine V2 side

## Status Meanings

Expected values for `corr_autobom_status`:

- `idle`: no recent auto-BoM result recorded
- `success`: product, V2 spec, preview, and BoM flow completed successfully
- `validation_failed`: import row is missing required business input
- `spec_upsert_failed`: V2 spec create/update failed
- `preview_failed`: V2 preview failed before materialization
- `material_resolution_failed`: preview failed because required paper/material/variant/deckle resolution was not possible
- `bom_materialization_failed`: preview existed but BoM materialization could not produce the required structure
- `bom_write_failed`: BoM write/update failed during Odoo record creation or update

Expected values for `corr_autobom_error_stage`:

- `validation`
- `spec_upsert`
- `preview`
- `material_resolution`
- `bom_materialization`
- `bom_write`

Companion status fields:

- `corr_autobom_preview_status`: `pending` / `success` / `failed`
- `corr_autobom_bom_status`: `pending` / `success` / `failed`

## Admin Troubleshooting

When a row fails:

1. Open the product created or updated by the import.
2. Open the `Corr Auto BoM Diagnostics` tab.
3. Check:
   - backend
   - status
   - error stage
   - message
   - error payload
   - spec id
   - preview status
   - BoM status
4. If a spec id is present, open the matching `corr.engine.v2.spec` record and inspect:
   - board structure
   - routing profile
   - preview snapshot fields
   - `last_preview_raw_json`
   - `last_preview_error`

Typical operator response:

- fix missing master data, then re-upload the row
- fix invalid input columns, then re-upload the row
- do not manually create a legacy spec for V2-mode rows

## Common Failure Examples

- `Validation failed: missing routing profile`
- `Validation failed: missing board recipe or board structure`
- `Preview failed: no exact deckle roll found for required width`
- `Material resolution failed: no variant found for a required board layer`
- `BoM materialization failed: persisted preview payload is missing`
- `BoM write failed: unable to create/update BoM line`

## Rollout Guidance

- keep the flag OFF for legacy customers until Engine V2 parity is validated in their target flows
- enable the flag ON for migration/pilot customers who should use the V2 spec-preview-BoM path
- use the product-level diagnostics and persisted preview snapshot for failed-row analysis
- retain the legacy path during migration; do not remove it until V2 rollout is complete

## Current Validated Scope

The V2 bulk-upload path has been validated in this migration stream for:

- normal box flows such as 0201
- sheet and roll styles where supported by current V2 mapping
- DIECUT_SHEET including FG-per-sheet yield handling
- route-driven multi-level BoM creation
- peeloff-separated route behavior
- top sheet unit-sheet mapping
- top sheet roll mapping
- board recipe-based spec creation
- re-upload update flow without duplicate product/spec creation in the validated path

This document is rollout guidance, not a replacement for release notes or per-customer UAT.
