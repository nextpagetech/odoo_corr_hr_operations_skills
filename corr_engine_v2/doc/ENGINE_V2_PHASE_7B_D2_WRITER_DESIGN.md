# Engine V2 Phase 7B-D2 Writer Design

## Scope

Phase 7B-D2 prepares the multi-level materialization writer design only.
Real ORM writes remain disabled.

Validated D1 input:

- resolver blockers are empty for Route1
- SFG products are create/reuse candidates keyed by `corr_sfg_key`
- SFG and FG BoMs are create/reuse/archive candidates
- FG product is supplied by the existing flow
- metadata still reports `writes_enabled=false`, `creates_enabled=false`, `archives_enabled=false`

## Non-Goals

- Do not create real products.
- Do not create real BoMs.
- Do not archive real BoMs.
- Do not create real MOs.
- Do not hardcode route or stage names such as 2ply, board, or seaming.
- Do not change stage mapping.

## Writer Inputs

The writer must consume resolver dry-run output, not raw StageContext rows.
The resolver is the gatekeeper for:

- product candidate action: `reuse_product`, `create_product`, `blocked_product`
- BoM candidate action: `reuse_bom`, `create_bom`, `archive_bom_candidate`, `blocked_bom`
- component product lookup and UoM compatibility
- stale signature detection
- company compatibility

If resolver blockers are not empty, the writer must refuse execution.

## Product Writer Design

SFG product behavior:

- `reuse_product`: use `existing_product_match.id`
- `create_product`: create a storable/consumable SFG product from candidate metadata
- key identity is `corr_sfg_key`
- the product must persist `corr_sfg_key` and materialization signatures where fields exist

FG product behavior:

- never create FG product in D2
- reuse the FG product supplied by the existing flow
- block if FG product is missing when real writer is enabled

The writer must not derive product identity from stage names.

## BoM Writer Design

For every resolver BoM candidate:

- `reuse_bom`: return existing BoM id
- `create_bom`: create BoM for candidate output product
- `archive_bom_candidate`: archive stale BoM then create a new BoM version
- `blocked_bom`: refuse execution

BoM levels come from candidate product roles and previous-output lines:

- `semi_finished` output -> SFG product + SFG BoM + future child MO candidate
- `finished` output -> FG BoM
- `non_stockable` output -> no product/BoM boundary; keep as operation/workorder inside parent BoM

No stage code or stage name may decide this. Only `stock_behavior` / candidate roles may decide boundaries.

## Single-BoM Fallback

The existing single-BoM writer path remains the fallback.

D2 writer activation must keep:

- `mode=single_bom` routed to existing single-BoM payload/writer behavior
- multi-level writer disabled by default
- feature flag required before any multi-level create/archive call

## Activation Gates

Real writes require all of:

- explicit writer flag enabled
- explicit creates flag enabled
- explicit archive flag enabled before stale BoM archival
- resolver blockers empty
- test suite covering create/reuse/archive/block paths
- manual dry-run comparison showing unchanged single-BoM fallback behavior

Until then, `materialization_writer.py` must return planned steps only or raise `MaterializationWriterDisabledError`.

## Test Plan Before Real Writes

Required pure tests:

- SFG create candidate produces product writer step keyed by `corr_sfg_key`
- existing SFG product produces reuse writer step
- FG product is reuse-only
- SFG BoM create follows SFG product step
- FG BoM create follows child SFG BoM steps
- stale BoM candidate plans archive-before-create
- blocked resolver output refuses execution
- single-BoM fallback remains separate
- non-stockable operation does not create product/BoM step

Required Odoo integration tests before enablement:

- create SFG product with `corr_sfg_key`
- reuse SFG product by `corr_sfg_key`
- create SFG BoM with component lines
- create FG BoM using previous SFG as component
- stale BoM archive and replacement behavior
- multi-company safety
- rollback/savepoint behavior on partial failure

## Current D2 Scaffold

`engine_v2/materialization_writer.py` is intentionally disabled.

It can:

- build a planned writer step list from resolver dry-run output
- preserve product-before-BoM ordering
- preserve dry-run metadata and disabled flags
- raise `MaterializationWriterDisabledError` for execution attempts

It cannot:

- create records
- write records
- archive records
- call real product or BoM ORM create APIs




