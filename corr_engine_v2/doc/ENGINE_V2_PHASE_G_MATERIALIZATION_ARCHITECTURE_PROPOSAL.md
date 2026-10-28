# Engine V2 Phase G Multi-Piece Materialization Architecture Proposal

## Purpose

Phase G is the first phase where Engine V2 multi-piece preview data may become
real manufacturing data.

The question is not only how to create BoMs. The deeper design question is:

```text
When one FG is made from multiple physical pieces, which products, BoMs,
operations, MOs, quantities, and costs own each part of the manufacturing flow?
```

This proposal compares two architectures:

- Option A: Independent Piece Manufacturing
- Option B: Shared Upstream Manufacturing

It recommends what Phase G should implement first and what should remain ready
for Phase H/shared-upstream support.

---

## Current State Before Phase G

Implemented preview foundation:

- persistent multi-piece definition on `corr.engine.v2.spec`;
- piece lines with length, width, qty per FG;
- accepted auto split stored on the Engine V2 spec;
- piece layout planning, currently planning-only;
- piece manufacturing override structure for stack/surface/top sheet,
  currently inherited only;
- branch/merge StageGraph preview;
- piece-tagged RM rows in preview;
- Create BoM is blocked for multi-piece.

Not implemented yet:

- piece SFG products;
- piece BoMs;
- merge BoM;
- child MOs;
- materialized piece routing;
- shared upstream split materialization.

---

## Core Concepts

### Finished Good

The customer-facing product.

Example:

```text
FG Box
```

FG ownership should remain with the parent Engine V2 spec and finished product.

### Piece

A physical part required to assemble the FG.

Example:

```text
Piece A = 3020 x 560
Piece B = 3020 x 560
```

Each piece has:

- piece key;
- piece dimensions;
- qty per FG;
- stack/surface/top-sheet effective definition;
- piece layout;
- piece route in future phases.

### Piece SFG

A real semi-finished product representing one piece at a materialized stage.

Example:

```text
FG Box / Piece A / Diecut
FG Box / Piece B / Diecut
```

### Merge

The operation that consumes piece outputs and creates the FG.

Example:

```text
Diecut Piece A + Diecut Piece B -> Seaming -> FG
```

---

## Option A: Independent Piece Manufacturing

### Summary

Each piece is treated as an independently manufactured branch from the first
layout-owning/material stage.

Example:

```text
Piece A:
2ply-A -> Board-A -> Diecut-A

Piece B:
2ply-B -> Board-B -> Diecut-B

Merge:
Diecut-A + Diecut-B -> Seaming -> FG
```

### Piece Materialization Model

Materialize real SFG products for each piece/stage output.

Recommended product key pattern:

```text
engine_v2:{spec_id}:piece:{piece_key}:stage:{stage_key}:signature:{hash}
```

Example display names:

```text
[SFG] FG Box / Piece A / 2-Ply
[SFG] FG Box / Piece A / Board
[SFG] FG Box / Piece A / Diecut
[SFG] FG Box / Piece B / 2-Ply
[SFG] FG Box / Piece B / Board
[SFG] FG Box / Piece B / Diecut
```

Each piece SFG should carry metadata:

- parent spec id;
- FG product id;
- piece key;
- piece name;
- piece index;
- piece dimensions;
- piece qty per FG;
- stage key;
- effective stack/surface signature;
- selected piece layout;
- merge stage key.

### Piece SFG Strategy

Phase G should materialize one terminal piece SFG per piece at minimum.

Example:

```text
Piece A Final SFG = Diecut Piece A
Piece B Final SFG = Diecut Piece B
```

There are two possible SFG depths:

#### A1. Terminal Piece SFG Only

Only final piece outputs become SFGs.

```text
BoM Piece A Final
  consumes raw paper/materials directly
  operations: 2ply, board, diecut
```

Pros:

- fewer products;
- fewer BoMs;
- simpler first implementation.

Cons:

- weaker stage-level traceability;
- less aligned with existing staged SFG materialization;
- harder to extend to piece-specific routing and child MOs.

#### A2. Staged Piece SFGs

Each material stage output becomes a piece SFG.

```text
BoM 2ply Piece A
BoM Board Piece A
BoM Diecut Piece A
```

Pros:

- consistent with existing staged SFG architecture;
- clearer child MO chain;
- better diagnostics;
- easier to support different piece routing later.

Cons:

- more records;
- more resolver/writer complexity.

Recommended for Phase G:

```text
A2 staged piece SFGs, but only for the active route stages already present in
the preview branch graph.
```

This matches Engine V2's existing staged materialization philosophy.

### Merge Strategy

Create a final FG BoM that consumes terminal piece SFGs.

Example:

```text
BoM: FG Box
  Components:
    Diecut Piece A x 1
    Diecut Piece B x 1
  Operation:
    Seaming
```

The merge BoM owns:

- merge operation;
- FG output;
- piece SFG component consumption;
- final assembly cost.

The merge BoM should not consume raw papers directly.

### FG Ownership

The FG remains the top-level product.

The FG BoM should be the parent/customer-facing BoM.

Recommended ownership:

```text
FG product owns final BoM.
Piece SFG products are internal components owned by the FG spec signature.
```

Do not create separate sellable products for Piece A/B unless explicitly
required later.

### Cost Rollup

Cost should roll up from:

```text
Piece A SFG cost
+ Piece B SFG cost
+ Merge operation cost
= FG manufacturing cost
```

Piece SFG cost includes:

- raw material consumption for that piece;
- operations on that piece branch;
- piece-specific waste;
- piece-specific layout assumptions.

FG cost includes:

- component cost from terminal piece SFGs;
- merge operation cost;
- merge waste if configured later.

Important:

```text
Do not double-count raw material at FG merge level.
```

### Child MO Structure

Recommended MO structure:

```text
Parent MO: FG Box
  consumes terminal Piece A SFG
  consumes terminal Piece B SFG
  operation: Seaming

Child MO: Diecut Piece A
  produces terminal Piece A SFG

Child MO: Diecut Piece B
  produces terminal Piece B SFG
```

If staged SFGs are used, the normal Odoo procurement/MO chain can create
additional child MOs for upstream SFGs.

Example:

```text
MO FG
  MO Diecut A
    MO Board A
      MO 2ply A
  MO Diecut B
    MO Board B
      MO 2ply B
```

This is record-heavy but explicit and auditable.

### Quantity Semantics

For order quantity `Q` FG:

```text
piece_required_qty = Q * piece_qty_per_fg
```

If Piece A qty per FG = 1 and Piece B qty per FG = 2:

```text
Q = 100 FG
Piece A = 100 units
Piece B = 200 units
```

Piece layout affects run quantity:

```text
piece_sheets = ceil(piece_required_qty / piece_layout_ups)
```

For Phase G, current accepted pieces are final blanks and Phase E does not
allow nested piece UPS, so:

```text
piece_layout_ups = 1
piece_sheets = piece_required_qty
```

Future nested planning may change this, but it should be a separate explicit
phase.

### Future Compatibility

Option A can support:

- uneven pieces;
- different stacks per piece;
- different surfaces per piece;
- different top sheets per piece;
- piece qty per FG;
- piece-specific route later;
- different merge method.

Option A does not naturally model:

- one shared 110 cm upstream deckle split after 2ply;
- shared raw-material consumption before the split node;
- multi-output slitting operation.

That belongs to Option B.

---

## Option B: Shared Upstream Manufacturing

### Summary

Pieces share one or more upstream stages, then split into branches later.

Example:

```text
110 cm reel
   |
2ply full deckle
   |
Slitting 60:40
   |              |
Piece A          Piece B
   |              |
Board A          Board B
   |              |
Diecut A         Diecut B
    \            /
      Seaming -> FG
```

### Piece Materialization Model

Materialize a shared upstream SFG first.

Example:

```text
[SFG] FG Box / 2-Ply Full Deckle 110cm
```

Then materialize split outputs:

```text
[SFG] FG Box / Piece A / 2-Ply Split Output
[SFG] FG Box / Piece B / 2-Ply Split Output
```

Then continue with piece branches.

### Piece SFG Strategy

Shared upstream strategy requires three product types:

```text
Shared upstream SFG
Split output piece SFGs
Downstream piece SFGs
```

This is more expressive but materially different from independent pieces.

### Merge Strategy

Same final merge model as Option A:

```text
Terminal Piece A + Terminal Piece B -> Merge Operation -> FG
```

The difference is upstream ownership, not final merge ownership.

### FG Ownership

FG ownership remains the same as Option A.

The parent FG BoM consumes terminal piece SFGs.

Shared upstream SFGs are internal child components in the piece chain.

### Cost Rollup

Cost rollup must avoid double-counting shared upstream RM.

Correct model:

```text
Shared upstream SFG cost consumed once
Allocated to Piece A and Piece B after split
Downstream piece costs added separately
FG merge cost added once
```

Allocation can be based on:

- width ratio;
- area ratio;
- configured split ratio;
- quantity-weighted area.

This must be explicit.

### Child MO Structure

Potential structure:

```text
MO FG
  MO Merge
    MO Diecut A
      MO Board A
        consumes Split Output A
    MO Diecut B
      MO Board B
        consumes Split Output B
    MO Slitting
      consumes 2ply Full Deckle
      outputs Split Output A and Split Output B
    MO 2ply Full Deckle
```

Problem:

Standard Odoo BoM/MO flows are not naturally multi-output. A slitting operation
that consumes one SFG and outputs two SFGs requires either:

- byproducts;
- custom split operation model;
- custom stock move generation;
- a controlled manufacturing writer extension.

### Quantity Semantics

For shared upstream:

```text
shared_upstream_qty != sum(piece_qty)
```

Instead:

```text
shared_upstream_qty = source full deckle/sheet run qty
piece_output_qty = shared_upstream_qty * split allocation
```

Example:

```text
Source width = 1100 mm
Piece A width = 672 mm
Piece B width = 448 mm

Piece A allocation = 672 / 1120
Piece B allocation = 448 / 1120
```

If the upstream source width includes trim/waste, allocation must account for
that explicitly.

### Future Compatibility

Option B is required for:

- split after 2ply;
- split after board;
- one reel/deckle shared by multiple pieces;
- slitting node with multiple outputs;
- shared upstream RM resolution;
- allocation of shared costs.

Option B is more correct for shared-deckle factories but much harder to
materialize safely.

---

## Comparison

| Topic | Option A: Independent Pieces | Option B: Shared Upstream |
|---|---|---|
| First implementation complexity | Medium | High |
| Fits current preview branch graph | Yes | Partially |
| Fits current BoM writer model | Yes | Needs multi-output support |
| Supports unequal pieces | Yes | Yes |
| Supports different stack per piece | Yes | Only after split; before split shared |
| Supports accepted auto split | Yes | Sometimes |
| Supports one shared 110 cm reel | No | Yes |
| Cost rollup complexity | Medium | High |
| Child MO complexity | Medium | High |
| Risk of wrong RM consumption | Lower | Higher if allocation is incomplete |
| Good Phase G candidate | Yes | No |
| Required for Phase H/I | Foundation only | Yes |

---

## Recommendation

Phase G should implement:

```text
Option A: Independent Piece Manufacturing
```

Reason:

- It matches the current accepted multi-piece preview shape.
- It fits the existing staged SFG materialization architecture.
- It avoids multi-output slitting/MO complexity.
- It supports the immediate business cases:
  - 2 pieces;
  - width split;
  - length split;
  - equal or uneven piece dimensions;
  - inherited stack/surface;
  - merge at seaming.
- It creates the correct foundations for later piece-specific stack/surface.

Phase G should not implement shared upstream manufacturing yet.

Shared upstream should remain explicitly modelled as a later strategy type:

```text
strategy_type = split_after_stage
```

This avoids mixing two different manufacturing meanings under one BoM writer.

---

## Recommended Phase G Scope

### In Scope

Implement independent piece materialization only.

Support:

- persisted multi-piece spec;
- exactly 2 pieces initially;
- piece qty per FG;
- inherited stack/surface/top sheet only;
- same route for both pieces;
- merge at configured merge stage;
- staged piece SFGs;
- final FG merge BoM;
- create/update/reuse signatures;
- cost rollup from piece SFGs to FG.

### Out Of Scope

Do not implement:

- shared upstream split;
- split after 2ply/board;
- multi-output slitting node;
- byproducts;
- different stack per piece;
- different surface per piece;
- piece-specific routing;
- nested piece layout UPS;
- 3 or 4 piece boxes.

---

## Proposed Phase G Data Model

### Materialization Strategy Payload

Add or extend preview materialization plan:

```json
{
  "strategy_type": "independent_piece_manufacturing",
  "source": "accepted_auto_split",
  "materialization_allowed": true,
  "merge": {
    "stage_key": "seaming",
    "method": "seaming",
    "input_piece_keys": ["A", "B"],
    "output": "fg"
  },
  "pieces": [
    {
      "piece_key": "A",
      "piece_name": "Piece A",
      "qty_per_fg": 1,
      "blank_length_mm": 3020,
      "blank_width_mm": 560,
      "layout": {
        "ups": 1,
        "sheet_length_mm": 3020,
        "sheet_width_mm": 560,
        "orientation": "direct"
      },
      "effective_stack_source": "parent_spec",
      "effective_surface_source": "parent_spec",
      "route_source": "parent_route"
    }
  ]
}
```

### Product Signature Inputs

Piece SFG product signature should include:

- parent FG product;
- spec id;
- spec signature;
- piece key;
- piece dimensions;
- piece qty per FG;
- stage key;
- effective stack signature;
- effective surface signature;
- selected piece layout;
- strategy type.

### BoM Signature Inputs

Piece BoM signature should include:

- output SFG signature;
- component products;
- quantities;
- operation stages;
- piece key;
- route version;
- layout planning version.

Merge BoM signature should include:

- FG product id;
- terminal piece SFG product ids/signatures;
- piece qty per FG;
- merge stage key;
- merge operation definition;
- strategy type.

---

## Proposed Phase G BoM Shape

Example with route:

```text
2ply -> board -> diecut -> seaming
```

Materialized records:

```text
BoM: 2ply Piece A
  consumes paper for Piece A
  operation: 2ply

BoM: Board Piece A
  consumes 2ply Piece A
  operation: board

BoM: Diecut Piece A
  consumes Board Piece A
  operation: diecut

BoM: 2ply Piece B
  consumes paper for Piece B
  operation: 2ply

BoM: Board Piece B
  consumes 2ply Piece B
  operation: board

BoM: Diecut Piece B
  consumes Board Piece B
  operation: diecut

BoM: FG Box
  consumes Diecut Piece A x 1
  consumes Diecut Piece B x 1
  operation: seaming
```

If the route is:

```text
2ply -> board -> seaming
```

Then terminal piece SFG is `Board Piece A/B`.

---

## Quantity Semantics For Phase G

Let:

```text
Q = FG manufacturing quantity
Pq = piece_qty_per_fg
U = piece layout UPS
```

Then:

```text
terminal_piece_required_qty = Q * Pq
piece_run_sheets = ceil(terminal_piece_required_qty / U)
```

Current Phase E rule:

```text
U = 1
```

Therefore:

```text
piece_run_sheets = terminal_piece_required_qty
```

For upstream staged SFGs in Option A, each upstream stage produces the same
piece quantity unless the stage has a configured yield/waste rule.

---

## Cost Rollup For Phase G

Recommended cost flow:

```text
raw material cost per piece SFG
+ piece operation cost
= terminal piece SFG cost

sum terminal piece SFG costs by qty per FG
+ merge operation cost
= FG cost
```

Preview and BoM cost should both carry:

- per piece material cost;
- per piece operation cost;
- terminal piece SFG cost;
- merge operation cost;
- total FG cost.

Cost rollup must keep these separate:

- branch cost;
- merge cost;
- parent FG final cost.

---

## Child MO Structure For Phase G

Recommended:

```text
Parent MO for FG
  consumes terminal Piece A SFG
  consumes terminal Piece B SFG
  executes merge operation
```

Piece SFG MOs are generated by normal procurement/manufacturing rules.

Potential tree:

```text
MO FG
  Child MO Terminal Piece A
  Child MO Terminal Piece B
```

If staged SFG procurement is enabled:

```text
MO FG
  MO Diecut Piece A
    MO Board Piece A
      MO 2ply Piece A
  MO Diecut Piece B
    MO Board Piece B
      MO 2ply Piece B
```

Phase G should avoid custom child-MO orchestration if Odoo procurement can
create the chain from BoMs.

---

## Shared-Upstream Compatibility Hooks

Even though Phase G should implement Option A first, it should reserve fields
for Option B.

Recommended reserved fields:

```text
strategy_type
split_timing
split_after_stage_key
shared_stage_keys
branch_stage_keys
split_node_key
source_deckle_width_mm
split_allocation_method
split_allocation_ratio
shared_upstream_group_key
```

Do not activate these fields in writer logic yet.

The materialization writer should branch explicitly:

```python
if strategy_type == "independent_piece_manufacturing":
    write_independent_piece_boms(...)
elif strategy_type == "split_after_stage":
    raise NotImplementedError / blocked UserError
```

This prevents accidental misuse of independent-piece materialization for
shared-upstream cases.

---

## Phase G Guardrails

Before enabling Create BoM for multi-piece, enforce:

- strategy type is `independent_piece_manufacturing`;
- piece count is exactly 2 for first implementation;
- every piece has positive length/width;
- every piece has positive qty per FG;
- piece layout UPS is 1;
- all piece stack/surface sources are inherited;
- no split-after-stage/shared-upstream flags are active;
- merge stage exists in route;
- terminal piece stage can be identified;
- terminal piece SFG product can be created/resolved;
- merge BoM can be created/resolved;
- signatures are deterministic.

If any guard fails:

```text
Block Create BoM with a clear user-facing reason.
```

---

## Final Recommendation

Implement Phase G in this order:

1. Build materialization plan payload for independent pieces.
2. Add resolver dry-run diagnostics for piece products/BoMs.
3. Create/reuse piece SFG products.
4. Create/reuse staged piece BoMs.
5. Create/reuse FG merge BoM.
6. Keep shared-upstream strategy blocked with explicit message.
7. Add tests for quantity, signatures, merge BoM lines, and Create BoM guard.

Do not implement shared-upstream materialization in Phase G.

Shared-upstream should be Phase H/I because it requires a split node,
multi-output semantics, allocation rules, and more careful MO/stock move design.



