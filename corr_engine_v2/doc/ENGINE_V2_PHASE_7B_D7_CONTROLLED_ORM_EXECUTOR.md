# Engine V2 Phase 7B-D7 Controlled ORM Executor

Phase 7B-D7 introduces the first guarded ORM create executor for materialized
multi-level BoMs. The default remains disabled.

## Gates

Real creates require both gates:

- Context: `corr_materialization_writer_enabled = true`
- System parameter: `corr_mrp_bridge.materialization_writer_enabled = true`

If either gate is missing, `execute_materialization_writer(...)` raises
`MaterializationWriterDisabledError` before any create call.

## Scope

Allowed only when both gates are open:

- Create or reuse SFG `product.template` records by `corr_sfg_key`.
- Create or reuse `mrp.bom` records by materialization candidate identity.
- Create `mrp.bom.line` records only for BoMs created in the current execution.
- Create `mrp.routing.workcenter` operations only for BoMs created in the
  current execution.

Still forbidden:

- No `mrp.production` creation.
- No automatic MO chaining.
- No archive/delete.
- No destructive updates to existing BoMs, lines, or operations.

## Idempotency

Product reuse key:

- `corr_sfg_key`

BoM reuse keys:

- `product_tmpl_id`
- `corr_engine_v2_signature`
- `corr_sfg_key`

When an existing BoM is reused, D7 does not mutate its lines or operations. The
line/operation payloads are reported as skipped with
`parent_bom_reused_existing_no_destructive_update`.

## Debug Visibility

Raw debug JSON exposes D7 diagnostics under:

- `phase7b_materialization_dry_run.guarded_orm_executor`
- `phase7b_materialization_dry_run.orm_writer_execution_diagnostics`

The D5 payload plan remains visible under
`phase7b_materialization_dry_run.orm_writer_payload_plan` because it is the
values-only payload source consumed by the D7 executor.



