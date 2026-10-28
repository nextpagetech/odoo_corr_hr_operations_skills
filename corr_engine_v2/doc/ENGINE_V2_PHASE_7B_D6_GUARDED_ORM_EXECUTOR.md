# Engine V2 Phase 7B-D6 Guarded ORM Executor

## Scope

D6 adds a guarded executor scaffold for the materialized multi-level writer.
Default behavior remains disabled.

The executor can create records only when both gates are enabled:

- context: `corr_materialization_writer_enabled = true`
- system parameter: `corr_mrp_bridge.materialization_writer_enabled = true`

If either gate is missing, the executor raises `MaterializationWriterDisabledError`
before model access and before any `create()` call.

## Controlled Creates

When both gates are explicitly enabled, D6 can create:

- SFG `product.template` records from resolver `create_product` candidates
- `mrp.bom` records from resolver `create_bom` candidates
- `mrp.bom.line` records from materialized line candidates
- `mrp.routing.workcenter` records from local materialized operation candidates

The executor uses the D5 payload plan and maps candidate keys to created record
ids so BoMs, lines, and operations link to their parent candidates.

## Still Disabled

D6 does not implement:

- MO creation
- child MO creation
- archive/delete
- stale BoM archival
- legacy single-BoM replacement

`mode=single_bom` is returned as preserved legacy fallback and the materialized
executor does not create records for that mode.

## Safety Rules

- Resolver blockers stop execution.
- Archive candidates are not executed.
- MO models are not touched.
- Production remains disabled unless both gates are manually enabled.
- Existing legacy single-BoM creation path is preserved.

## Tests

Pure tests cover:

- disabled-by-default executor makes no create calls
- both gates enabled produce controlled product/BoM/line/operation creates
- legacy single-BoM fallback creates nothing in materialized executor
- archive and MO scope remain excluded




