# Engine V2 Phase 7B-D4 Guarded Writer Design

## Scope

D4 prepares the guarded real writer plan. It does not enable real ORM writes.

The legacy single-BoM writer remains the only active real BoM creation path.
The multi-level materialization writer remains diagnostic/planned-only.

## Required Gates

Real materialization writes must require both:

- context flag: `corr_materialization_writer_enabled`
- system parameter: `corr_mrp_bridge.materialization_writer_enabled`

Default behavior:

- context flag absent -> disabled
- system parameter absent -> disabled
- only one gate enabled -> disabled
- both gates enabled -> D4 can mark create steps as ready, but still does not execute ORM writes

## Product Writer Plan

SFG products:

- reuse by `existing_product_match.id`
- create only for `product_role=sfg`
- identity key is `corr_sfg_key`

FG products:

- reuse only
- no FG product creation in D4

## BoM Writer Plan

BoMs:

- reuse existing BoM when resolver says `reuse_bom`
- create BoM when resolver says `create_bom`
- block when resolver has blockers

Not supported in D4:

- archive stale BoMs
- delete BoMs
- create MOs
- create child MOs

## Legacy Fallback

`mode=single_bom` continues to use the legacy flattened writer path.

D4 does not reroute legacy creation buttons to the materialized writer. It only
exposes strategy and implementation-plan diagnostics so the next phase can
activate safely.

## Current Scaffold

`materialization_writer.py` now provides:

- `writer_config_from_env(env)`
- `build_guarded_writer_implementation_plan(...)`
- context/system-parameter guard diagnostics
- product write step plan
- BoM write step plan
- explicit `no_mo_creation=true`
- explicit `no_archive_delete=true`
- `real_orm_writes_enabled=false`

The debug raw JSON now includes:

- `phase7b_materialization_dry_run.writer_strategy`
- `phase7b_materialization_dry_run.writer_implementation_plan`




