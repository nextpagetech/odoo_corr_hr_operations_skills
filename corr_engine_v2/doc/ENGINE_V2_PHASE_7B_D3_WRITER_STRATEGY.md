# Engine V2 Phase 7B-D3 Writer Strategy

## Current Finding

D1 dry-run validates the correct materialization shape, but the real ORM path
still calls the legacy flattened BoM writer:

- `build_bom_blueprint_from_stagecontext_preview_payload()`
- `writer_payload`
- `_create_bom_from_struct()`

That path writes one FG BoM and flattens routed operations into that BoM. This
is expected today because the real multi-level materialization writer is still
disabled.

## D3 Goal

Separate writer strategy before enabling any real writes.

The system needs two explicit writer families:

- `legacy_flattened_bom_writer`
- `materialized_bom_writer`

The legacy writer remains responsible for single-BoM fallback mode. The
materialized writer will eventually create/reuse SFG products and multi-level
BoMs from resolver candidates.

## Boundary Rules

No stage names may decide writer behavior.

Writer boundaries must come from materialization candidates derived from
`stock_behavior`:

- `semi_finished` -> SFG product boundary + independent SFG BoM
- `finished` -> FG BoM boundary
- `non_stockable` -> no product/BoM boundary; keep as operation inside parent BoM

## Operation Ownership

Legacy flattened writer:

- owns all operations inside the final FG BoM
- remains valid for `mode=single_bom`

Materialized multi-level writer:

- each BoM owns only its local operations
- previous-output lines connect BoM levels
- SFG BoMs do not leak their operations into FG BoM
- FG BoM owns only final-stage operations

## Strategy Selection

Initial D3 strategy selection is declarative and non-writing:

- `mode=single_bom` -> `legacy_single_bom_flattened`
- resolver/materialization contains SFG BoM candidates -> `materialized_multi_level`
- explicit strategy override may request legacy or materialized mode

Even when `materialized_multi_level` is selected, execution remains disabled.

## Activation Requirements

Before real ORM writes can be enabled:

- strategy planner must be covered by pure tests
- Odoo integration tests must prove SFG BoM operations stay local
- existing single-BoM fallback tests must remain unchanged
- real writer must refuse resolver outputs with blockers
- real writer must run in savepoint/rollback-safe sequence
- archive behavior must require explicit archive enablement

## Current D3 Scaffold

`materialization_writer.build_materialization_writer_strategy()` now returns:

- selected strategy
- preserved fallback status
- disabled real write flags
- per-BoM writer family
- operation ownership scope
- line and operation counts from materialization candidates

This is planning only. No ORM create/write/archive calls are made.




