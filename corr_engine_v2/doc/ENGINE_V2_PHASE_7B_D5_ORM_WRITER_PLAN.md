# Engine V2 Phase 7B-D5 ORM Writer Plan

## Scope

D5 prepares the future ORM writer payloads. It still does not execute real
ORM writes in production or tests.

## Required Gates

No write may happen unless both are enabled:

- context: `corr_materialization_writer_enabled = true`
- system parameter: `corr_mrp_bridge.materialization_writer_enabled = true`

Even with both gates enabled, D5 returns values only and keeps
`real_orm_writes_enabled=false`.

## Planned ORM Payloads

D5 prepares value payloads for:

- `product.template` SFG creation by `corr_sfg_key`
- `mrp.bom` creation/reuse from resolver candidates
- `mrp.bom.line` creation from materialized component/previous-output lines
- `mrp.routing.workcenter` operation creation with local BoM operation scope

The payload planner does not call:

- `create`
- `write`
- `unlink`
- archive actions
- MO creation

## Product Rules

- SFG `create_product` prepares product values.
- SFG `reuse_product` uses resolver existing match.
- FG product remains reuse-only.
- FG product creation is not supported in D5.

## BoM Rules

- `create_bom` prepares BoM values and child line/operation values.
- `reuse_bom` remains a reuse action.
- `archive_bom_candidate` is not supported in D5.
- no delete/archive payloads are generated.

## Operation Scope

Operations are prepared from each materialized BoM candidate's own
`operations` list.

For multi-level materialization:

- SFG BoM operations stay on the SFG BoM.
- FG BoM operations stay on the FG BoM.
- operations are not flattened into one FG BoM.

## Legacy Fallback

`mode=single_bom` still belongs to the existing legacy flattened writer.
D5 does not replace that path.

## Current Scaffold

`materialization_writer.build_orm_writer_payload_plan(...)` returns:

- `product_create_vals`
- `bom_create_vals`
- `bom_line_create_vals`
- `bom_operation_create_vals`
- `no_create_calls=true`
- `no_write_calls=true`
- `no_archive_delete=true`
- `no_mo_creation=true`
- `real_orm_writes_enabled=false`




