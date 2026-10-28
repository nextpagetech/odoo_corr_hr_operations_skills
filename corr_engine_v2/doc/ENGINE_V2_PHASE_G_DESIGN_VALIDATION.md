# Engine V2 Phase G Design Validation

## Purpose

This document validates how multi-piece materialization should integrate with
the existing Phase 7B SFG materialization architecture before implementation.

It splits Phase G into two implementation stages:

- **Phase G1**: piece products, piece BoMs, FG merge BoM, cost rollup.
- **Phase G2**: routed piece materialization, piece stage chains,
  branch/merge manufacturing flow.

No implementation is prescribed here beyond design validation.

---

## Existing Phase 7B Architecture To Reuse

Phase 7B already provides the important writer foundations:

- materialization plan payload;
- product candidates;
- BoM candidates;
- deterministic signatures;
- dry-run resolver;
- guarded ORM writer;
- multi-level BoM strategy selection;
- product/BoM create-or-reuse planning;
- strict write gates.

Relevant current components:

```text
engine_v2/materialization_resolver.py
engine_v2/materialization_writer.py
engine_v2/manufacturing_plan.py
```

Current resolver behavior:

- reads `products`;
- reads `boms`;
- resolves existing product matches;
- resolves existing BoM signature matches;
- produces blockers/warnings;
- performs no writes.

Current writer behavior:

- builds guarded writer steps;
- chooses `materialized_multi_level` when SFG BoMs exist;
- can create products/BoMs only when explicit gates are open;
- does not create MOs;
- does not archive/delete by default.

Validation:

```text
Phase G should not create a separate materialization writer stack.
It should extend the existing Phase 7B materialization plan shape so the
existing resolver and guarded writer remain the single owner of product/BoM
creation.
```

---

## Current Multi-Piece Inputs

Phase D-F now provide these inputs:

- persistent multi-piece spec;
- piece lines with dimensions and qty per FG;
- accepted split source/status;
- piece layout planning;
- inherited stack/surface/top-sheet structure;
- projected branch flow;
- Create BoM still blocked.

Important current limits:

- exactly 2 pieces for first implementation;
- piece layout UPS is 1;
- stack/surface/top sheet are inherited;
- no shared-upstream split;
- no piece-specific routing execution;
- no piece-specific material behavior yet.

---

## Core Validation Question

Can multi-piece materialization be represented using the existing Phase 7B
materialized multi-level SFG plan?

Answer:

```text
Yes for Phase G1.
Partially for Phase G2.
```

G1 can be expressed as:

```text
piece SFG products
piece BoMs
FG merge BoM
```

This maps well to existing Phase 7B product and BoM candidate concepts.

G2 needs richer route-chain construction:

```text
piece stage chains
branch graph ownership
terminal piece stage discovery
branch/merge operation placement
```

This should still output Phase 7B-compatible product/BoM candidates, but it
needs new planning logic before feeding the existing resolver/writer.

---

## Recommended Split

## Phase G1: Minimal Independent Piece Materialization

### Goal

Create real products and BoMs for independent pieces without implementing full
routed piece branch chains.

### Scope

G1 should implement:

- one terminal piece SFG product per piece;
- one piece BoM per piece;
- one FG merge BoM;
- cost rollup from piece BoMs to FG merge BoM;
- deterministic signatures;
- resolver dry-run support;
- guarded writer integration;
- Create BoM unblocked only for this narrow strategy.

### Product Shape

For a 2-piece box:

```text
[SFG] FG Box / Piece A
[SFG] FG Box / Piece B
FG Box
```

G1 should not create every stage SFG yet.

Reason:

```text
G1 validates the multi-piece ownership and merge model with the least routing
complexity. Full routed chains belong to G2.
```

### BoM Shape

```text
BoM: Piece A SFG
  consumes Piece A raw materials
  local operations: owner-stage or inherited compact operations

BoM: Piece B SFG
  consumes Piece B raw materials
  local operations: owner-stage or inherited compact operations

BoM: FG
  consumes Piece A SFG x piece_qty_per_fg
  consumes Piece B SFG x piece_qty_per_fg
  operation: merge stage
```

Important:

```text
The FG merge BoM must not consume raw papers directly.
```

### Materialization Plan Shape

G1 should generate a Phase 7B-compatible plan:

```json
{
  "mode": "multi_piece_independent_g1",
  "strategy_type": "independent_piece_manufacturing",
  "products": [
    {
      "candidate_key": "product:piece:A",
      "product_role": "sfg",
      "corr_sfg_key": "engine_v2:spec:12:piece:A:terminal"
    },
    {
      "candidate_key": "product:piece:B",
      "product_role": "sfg",
      "corr_sfg_key": "engine_v2:spec:12:piece:B:terminal"
    },
    {
      "candidate_key": "product:fg",
      "product_role": "fg"
    }
  ],
  "boms": [
    {
      "bom_key": "bom:piece:A",
      "bom_role": "sfg",
      "product_candidate_key": "product:piece:A"
    },
    {
      "bom_key": "bom:piece:B",
      "bom_role": "sfg",
      "product_candidate_key": "product:piece:B"
    },
    {
      "bom_key": "bom:fg:merge",
      "bom_role": "fg",
      "product_candidate_key": "product:fg"
    }
  ]
}
```

The existing Phase 7B resolver can inspect these candidates.

The existing Phase 7B writer can create/reuse products and BoMs when gates are
enabled.

### Piece BoM Lines

Piece BoM raw material lines should use the already piece-tagged RM
requirements from preview.

For Piece A:

```text
all rm_requirements where metadata.piece_key == A
```

For Piece B:

```text
all rm_requirements where metadata.piece_key == B
```

This avoids recomputing RM during materialization.

### FG Merge BoM Lines

FG merge BoM lines:

```text
Piece A SFG x piece_qty_per_fg
Piece B SFG x piece_qty_per_fg
```

For FG order quantity, Odoo will multiply normally through the parent BoM.

### Operations In G1

G1 should be conservative.

Recommended:

- piece BoMs can carry compact local operations from the projected flow, but
  should not claim full routed stage-chain semantics;
- merge BoM carries the merge operation;
- no custom child MO orchestration.

Alternative:

- piece BoMs carry no operations in G1 except the layout owner operation.

Recommendation:

```text
Use minimal local operation metadata needed for cost visibility, but avoid
pretending G1 has full piece routing execution.
```

### Cost Rollup In G1

Cost should roll:

```text
Piece A raw/material + local operation cost
+ Piece B raw/material + local operation cost
+ merge operation cost
= FG cost
```

Implementation should store/report:

- piece material cost;
- piece operation cost;
- piece total cost;
- merge operation cost;
- FG total cost.

Do not double count:

- raw material on FG merge BoM;
- piece operation cost at merge level.

### Quantity Semantics In G1

Let:

```text
Q = FG quantity
Pq = piece_qty_per_fg
```

Then:

```text
piece_required_qty = Q * Pq
```

Since Phase E treats piece blanks as final 1-up manufacturing blanks:

```text
piece_layout_ups = 1
piece_run_qty = piece_required_qty
```

G1 should enforce:

```text
piece_layout_ups == 1
```

Nested piece UPS is not allowed.

### G1 Guardrails

Create BoM may be unblocked only when:

- strategy is `independent_piece_manufacturing`;
- phase mode is `multi_piece_independent_g1`;
- piece count is exactly 2;
- all pieces have positive dimensions;
- all pieces have positive qty per FG;
- piece layout UPS is 1;
- stack source is inherited;
- surface source is inherited;
- top sheet source is inherited;
- no shared-upstream fields are active;
- merge stage is present;
- FG product exists;
- Phase 7B writer gates are respected.

If any guard fails, Create BoM remains blocked.

---

## Phase G2: Routed Piece Materialization

### Goal

Materialize the true branch/merge manufacturing graph.

G2 extends G1 from terminal piece SFGs to routed piece stage chains.

### Scope

G2 should implement:

- stage-level piece SFGs;
- piece stage BoMs;
- branch StageGraph to materialization-plan mapping;
- terminal piece stage discovery;
- merge BoM consuming terminal piece stage outputs;
- branch/merge manufacturing flow diagnostics;
- later support for piece-specific routes.

### Product Shape

Example route:

```text
2ply -> board -> diecut -> seaming
```

G2 products:

```text
2ply Piece A
Board Piece A
Diecut Piece A
2ply Piece B
Board Piece B
Diecut Piece B
FG Box
```

### BoM Shape

```text
BoM: 2ply Piece A
  consumes Piece A papers
  operation: 2ply

BoM: Board Piece A
  consumes 2ply Piece A
  operation: board

BoM: Diecut Piece A
  consumes Board Piece A
  operation: diecut

BoM: FG Box
  consumes Diecut Piece A
  consumes Diecut Piece B
  operation: seaming
```

### Integration With Phase 7B

G2 should still output normal Phase 7B candidates:

```text
products[]
boms[]
lines[]
operations[]
signatures
```

But G2 needs a new planner layer before the existing resolver/writer:

```text
multi_piece_branch_materialization_planner
  -> product candidates
  -> BoM candidates
  -> resolver dry-run
  -> guarded writer
```

The Phase 7B resolver/writer should not learn branch graph semantics directly.

Reason:

```text
Resolver/writer should stay generic: resolve candidates and write candidates.
Branch graph semantics belong to the planning layer.
```

### G2 Quantity Semantics

For each piece branch:

```text
stage_output_qty = FG_qty * piece_qty_per_fg
```

For now, no nested UPS:

```text
piece_layout_ups = 1
```

Future G2+ may allow:

```text
stage_input_qty != stage_output_qty
```

when stage yield, nested layout, or piece-specific route logic is implemented.

### G2 Cost Rollup

Cost rolls through the piece chain:

```text
2ply Piece A cost
-> Board Piece A cost
-> Diecut Piece A cost
```

Terminal piece cost is consumed by FG merge BoM.

FG merge cost is added once.

### G2 Guardrails

G2 must block:

- shared-upstream split;
- split-after-stage;
- multi-output slitting;
- piece-specific routes unless explicitly implemented;
- piece-specific stack/surface if not implemented;
- nested piece UPS unless explicitly implemented.

---

## Phase G1 vs G2 Comparison

| Topic | Phase G1 | Phase G2 |
|---|---|---|
| Product creation | Terminal piece SFGs only | Stage-level piece SFGs |
| BoM creation | Piece BoMs + FG merge BoM | Full branch chain + merge BoM |
| Uses Phase 7B resolver/writer | Yes | Yes |
| Needs new planner | Small | Significant |
| Child MO structure | Odoo procurement from terminal piece BoMs | Odoo procurement through full stage chain |
| Route execution fidelity | Limited | High |
| Cost rollup | Piece-level + merge | Stage-level piece chain + merge |
| Implementation risk | Medium | Higher |
| Good first release | Yes | No |

---

## FG Ownership Validation

FG ownership should not change in G1 or G2.

The FG product owns:

- customer-facing product identity;
- final BoM;
- merge operation;
- final cost rollup.

Piece products are internal SFGs.

They should be created/reused by deterministic `corr_sfg_key`.

They should not replace the FG product.

---

## Child MO Validation

Phase 7B writer currently does not create MOs.

That remains correct.

Recommended:

```text
Create BoM only.
Let normal Odoo procurement/manufacturing rules create child MOs.
```

G1 expected MO result:

```text
MO FG
  consumes Piece A SFG
  consumes Piece B SFG
```

Child piece MOs are created only if Odoo procurement rules require the SFGs.

G2 expected MO result:

```text
MO FG
  child MO terminal Piece A
    optional child MO upstream Piece A stages
  child MO terminal Piece B
    optional child MO upstream Piece B stages
```

No custom child MO orchestration should be added in G1.

---

## Shared-Upstream Compatibility Validation

Phase H shared-upstream split requires a different strategy:

```text
strategy_type = split_after_stage
```

G1/G2 independent piece materialization must not accidentally materialize
shared-upstream scenarios.

Reserve but block:

- `split_after_stage_key`;
- `shared_stage_keys`;
- `split_node_key`;
- `source_deckle_width_mm`;
- `split_allocation_method`;
- `shared_upstream_group_key`.

If any shared-upstream marker is present:

```text
Raise/block with:
"Shared-upstream multi-piece materialization is reserved for Phase H."
```

This preserves future compatibility without corrupting RM ownership.

---

## Recommended Implementation Order

### Phase G1

1. Build a G1 materialization plan from current preview:
   - products: Piece A SFG, Piece B SFG, FG;
   - BoMs: Piece A BoM, Piece B BoM, FG merge BoM.
2. Add G1 guard validation.
3. Add resolver dry-run output for G1.
4. Keep writer disabled by default, following Phase 7B gates.
5. Enable guarded create only through existing Phase 7B writer.
6. Add tests:
   - product candidate signatures;
   - piece BoM lines filtered by piece key;
   - FG merge BoM consumes terminal piece SFGs;
   - Create BoM remains blocked when guards fail;
   - shared-upstream markers block.

### Phase G2

1. Add branch materialization planner.
2. Map branch StageGraph to staged piece SFG candidates.
3. Identify terminal piece stage per piece.
4. Generate full staged piece BoM chain.
5. Generate FG merge BoM consuming terminal piece SFGs.
6. Extend tests for branch-chain quantities and signatures.

---

## Final Validation Recommendation

Implement Phase G as two stages:

```text
Phase G1: terminal piece SFGs + piece BoMs + FG merge BoM + cost rollup
Phase G2: routed piece stage chains + branch/merge manufacturing flow
```

G1 should be implemented first because it validates:

- piece product ownership;
- FG merge ownership;
- piece quantity semantics;
- cost rollup;
- Phase 7B resolver/writer compatibility.

G2 should follow after G1 because routed piece chains require a dedicated
branch materialization planner, but should still reuse the existing Phase 7B
resolver/writer once candidates are produced.



