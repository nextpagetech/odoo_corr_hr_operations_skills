# Engine V2 Phase G2 Routed Piece Materialization Design

## Purpose

Phase G2 converts the accepted Phase G1 terminal-piece model into a routed
piece-stage chain.

G1 is accepted as the minimum multi-piece materialization structure:

```text
PieceA:terminal -> Paper
PieceB:terminal -> Paper
FG -> PieceA:terminal + PieceB:terminal + Merge operation
```

G2 keeps the FG merge contract but changes what sits behind each terminal
piece SFG:

```text
PieceA:2ply -> Paper
PieceA:board -> PieceA:2ply + board-stage inputs
PieceA:terminal -> PieceA:board

PieceB:2ply -> Paper
PieceB:board -> PieceB:2ply + board-stage inputs
PieceB:terminal -> PieceB:board

FG -> PieceA:terminal + PieceB:terminal + Merge operation
```

This is a design document only. It does not enable G2 writes.

---

## Current G1 Contract

G1 creates:

- one terminal SFG product per piece;
- one terminal piece BoM per piece;
- one FG merge BoM;
- no routed piece stage products;
- no shared-upstream split;
- no piece-specific stack/surface behavior.

Current G1 candidate keys:

```text
product:piece:A:sfg
product:piece:B:sfg
bom:piece:A:terminal
bom:piece:B:terminal
bom:fg:multi_piece_merge
```

Current G1 BoM shape:

```text
BoM: Piece A Terminal
  consumes Piece A paper RM rows

BoM: Piece B Terminal
  consumes Piece B paper RM rows

BoM: FG Merge
  consumes Piece A Terminal SFG x Piece A qty/FG
  consumes Piece B Terminal SFG x Piece B qty/FG
  operation: merge stage
```

G2 must remain compatible with this external contract:

```text
FG merge BoM still consumes terminal Piece A/B SFGs.
```

Only the upstream contents of each terminal piece SFG change.

---

## G2 Goal

For the selected routing profile:

```text
2-Ply Corrugation -> Board Formation -> Seaming / Jointing
```

G2 should materialize:

```text
Piece A
  2ply-A SFG
    consumes Piece A paper rows

  board-A SFG
    consumes 2ply-A SFG
    consumes board-stage inputs such as unit top sheet where applicable

  terminal-A SFG
    consumes board-A SFG

Piece B
  2ply-B SFG
    consumes Piece B paper rows

  board-B SFG
    consumes 2ply-B SFG
    consumes board-stage inputs such as unit top sheet where applicable

  terminal-B SFG
    consumes board-B SFG

FG
  consumes terminal-A SFG
  consumes terminal-B SFG
  operation: Seaming / Jointing
```

For routes with additional stages, the same rule applies:

```text
each piece stage output becomes an internal SFG
each next piece stage BoM consumes the previous piece stage SFG
the final piece stage output becomes the terminal piece SFG consumed by FG
```

---

## Non-Goals

G2 must not implement:

- shared-upstream split;
- split-after-2ply using one full-deckle upstream SFG;
- slitting node with multiple outputs;
- byproduct-based split outputs;
- piece-specific routing;
- piece-specific stack/surface/top-sheet overrides;
- nested piece UPS;
- multi-piece count greater than two;
- custom MO orchestration.

Those remain Phase H/I scope.

---

## Core Design Decision

G2 should not replace the Phase 7B resolver/writer.

Instead, G2 adds a planner layer:

```text
G2 routed piece planner
  -> Phase 7B-compatible materialization plan
  -> existing resolver dry-run
  -> existing guarded writer
```

The resolver/writer should continue to know only:

- product candidates;
- BoM candidates;
- component lines;
- operations;
- signatures;
- create/reuse/update diagnostics.

Branch semantics belong in the G2 planner, not in the generic writer.

---

## Route Segmentation

G2 must split the active route into:

1. **Piece branch stages**
   - stages before the merge stage;
   - executed separately for each piece.

2. **Merge stage**
   - the configured merge stage, for example `seaming`;
   - owned by the FG BoM.

3. **Post-merge stages**
   - out of scope for first G2 unless already represented as final FG
     operations in existing routing logic.

Example:

```text
2ply -> board -> seaming
```

Branch stages:

```text
2ply
board
```

Merge stage:

```text
seaming
```

Terminal piece stage:

```text
board
```

Example:

```text
2ply -> board -> diecut -> seaming
```

Branch stages:

```text
2ply
board
diecut
```

Terminal piece stage:

```text
diecut
```

---

## Stage Eligibility

Only material/manufacturing stages before merge should become piece SFG stages.

Include:

- corrugation / 2ply;
- board formation / lamination;
- cutting;
- printing, if it produces or transforms a piece SFG;
- diecut, if it is before merge and materialized as an SFG output.

Exclude from piece chain:

- final merge stage;
- non-material diagnostics;
- costing-only stages;
- inactive seeded attachment stages;
- shared-upstream split nodes.

The first G2 implementation should use the active route order already exposed
by StageGraph / StageContext, not invent a separate stage order.

---

## Product Model

### Product Candidate Keys

G2 should introduce stage-level piece products while keeping the G1 terminal
product keys stable.

Recommended product candidates:

```text
product:piece:A:stage:2ply
product:piece:A:stage:board
product:piece:A:sfg

product:piece:B:stage:2ply
product:piece:B:stage:board
product:piece:B:sfg
product:fg
```

The terminal piece product remains:

```text
product:piece:{piece_key}:sfg
```

That preserves the FG merge BoM contract.

If the terminal stage is the final branch stage, its display name may include
the terminal stage:

```text
[SFG] FG Box / Piece A / Terminal Board
```

But the candidate key should remain stable:

```text
product:piece:A:sfg
```

Reason:

```text
FG merge BoM should not need to change when G1 becomes G2.
```

### Product Metadata

Every G2 piece SFG product candidate should carry:

- phase: `G2`;
- strategy type: `independent_routed_piece`;
- parent spec id;
- FG product template id;
- piece key;
- piece name;
- piece index;
- piece qty per FG;
- piece length/width;
- stage key;
- stage sequence;
- stage role/capability;
- route profile/source;
- effective inherited stack signature;
- effective inherited surface signature;
- selected piece layout;
- terminal flag.

---

## BoM Model

### BoM Candidate Keys

Recommended BoM keys:

```text
bom:piece:A:stage:2ply
bom:piece:A:stage:board
bom:piece:A:terminal

bom:piece:B:stage:2ply
bom:piece:B:stage:board
bom:piece:B:terminal

bom:fg:multi_piece_merge
```

The terminal BoM key remains:

```text
bom:piece:{piece_key}:terminal
```

G2 changes its lines from raw paper to previous-stage SFG.

### G2 BoM Line Rules

For the first stage in a piece branch:

```text
BoM: piece:{key}:stage:first
  consumes piece-tagged RM rows assigned to that stage
  owns that stage operation
```

For intermediate stages:

```text
BoM: piece:{key}:stage:{stage}
  consumes previous piece stage SFG
  consumes RM/consumable rows assigned to this stage and piece
  owns this stage operation
```

For terminal piece BoM:

```text
BoM: piece:{key}:terminal
  consumes previous piece stage SFG
  owns no merge operation
  may own terminal transformation operation only if terminal is a real route stage
```

For FG merge BoM:

```text
BoM: FG
  consumes Piece A terminal SFG x Piece A qty/FG
  consumes Piece B terminal SFG x Piece B qty/FG
  owns merge operation only
  consumes no raw paper
```

---

## Component Routing Rules

### Paper Rows

Paper rows must be consumed only by the first paper-consuming stage for that
piece.

For the route:

```text
2ply -> board -> seaming
```

Paper goes to:

```text
Piece A 2ply BoM
Piece B 2ply BoM
```

Paper must not also appear on:

```text
Piece A board BoM
Piece A terminal BoM
FG merge BoM
```

### Unit Top Sheet Rows

For unit top sheet mode:

```text
top_sheet_external
```

should be consumed at the board/lamination stage when that stage exists.

Example:

```text
Piece A board BoM
  consumes Piece A 2ply SFG
  consumes top_sheet_external
```

If no board/lamination stage exists, G2 should block until a deterministic
fallback rule is defined.

### Consumables

Consumables should follow their assigned stage.

Examples:

```text
corrugation adhesive -> 2ply stage
board adhesive -> board stage
seaming adhesive -> FG merge stage
```

First G2 may keep non-paper consumables conservative:

- include only stage-assigned consumables with clear stage ownership;
- block ambiguous consumables instead of silently attaching them to terminal.

---

## Operation Model

Each piece-stage BoM should carry the operation for its own stage only.

Example:

```text
BoM: Piece A 2ply
  operation: 2-Ply Corrugation

BoM: Piece A Board
  operation: Board Formation

BoM: FG Merge
  operation: Seaming / Jointing
```

Do not put all piece operations on the FG BoM.

Do not put merge operation on a piece BoM.

Operation metadata should preserve:

- base stage key;
- routed stage template id;
- stage contract code;
- workcenter capability;
- operation sequence;
- piece key for branch operations;
- merge flag for FG operation.

---

## Quantity Semantics

Let:

```text
Q = FG order quantity
Pq = piece_qty_per_fg
U = selected piece layout UPS
```

Current G2 first implementation keeps:

```text
U = 1
```

Therefore:

```text
terminal_piece_qty = Q * Pq
piece_stage_output_qty = terminal_piece_qty
```

For a two-piece box where A and B each have `qty_per_fg = 1`:

```text
Q = 100 FG
Piece A terminal = 100
Piece B terminal = 100
```

If future piece B qty is 2:

```text
Q = 100 FG
Piece A terminal = 100
Piece B terminal = 200
```

For G2, every upstream piece stage should produce the quantity required by
the next stage unless explicit yield/waste is implemented.

---

## Cost Rollup

G2 cost should roll through each piece chain:

```text
Piece A 2ply material + operation
  -> Piece A board component cost + board operation + board inputs
  -> Piece A terminal cost

Piece B 2ply material + operation
  -> Piece B board component cost + board operation + board inputs
  -> Piece B terminal cost

FG = Piece A terminal cost
   + Piece B terminal cost
   + merge operation cost
```

Rules:

- FG merge BoM must not count raw paper directly.
- Piece terminal BoM must not duplicate paper already consumed by first stage.
- Board-stage inputs must not be counted at 2ply.
- Merge consumables must be counted once at FG merge.

---

## Resolver / Writer Integration

G2 should emit the same materialization plan structure consumed by the existing
Phase 7B resolver/writer:

```json
{
  "mode": "materialized_multi_level",
  "strategy_type": "independent_routed_piece",
  "products": [],
  "boms": []
}
```

The G2 planner should construct:

- products;
- BoMs;
- BoM lines;
- operations;
- metadata;
- deterministic signatures.

Then existing services handle:

- product resolution;
- BoM resolution;
- dry-run diagnostics;
- guarded writes.

Do not create a separate writer.

---

## Signature Strategy

### Stage Piece Product Signature

Include:

- phase `G2`;
- strategy type;
- spec id;
- FG product id;
- piece key;
- piece dimensions;
- piece qty per FG;
- stage key;
- route signature;
- inherited stack/surface/top-sheet signatures;
- piece layout signature.

### Stage Piece BoM Signature

Include:

- output product candidate key;
- previous-stage component candidate key, if any;
- RM component keys and quantities;
- stage operation signature;
- piece key;
- route signature;
- strategy type.

### Terminal BoM Signature

Include:

- terminal piece product candidate key;
- previous branch stage product candidate key;
- terminal stage key;
- piece key;
- strategy type.

### FG Merge BoM Signature

Keep G1-compatible merge signature but include:

- strategy type `independent_routed_piece`;
- terminal piece product candidate keys;
- piece qty per FG;
- merge stage key;
- merge operation signature.

---

## Migration From G1 To G2

G1 terminal products should be reused.

Before G2:

```text
bom:piece:A:terminal
  -> paper
```

After G2:

```text
bom:piece:A:stage:2ply
  -> paper

bom:piece:A:stage:board
  -> Piece A 2ply SFG
  -> top sheet / board inputs

bom:piece:A:terminal
  -> Piece A board SFG
```

The FG merge BoM remains:

```text
bom:fg:multi_piece_merge
  -> Piece A terminal SFG
  -> Piece B terminal SFG
  -> merge operation
```

### Compatibility Options

There are two safe migration behaviors:

#### Option 1: Update Existing Terminal BoM

When G2 is enabled, update/reuse the existing terminal BoM key:

```text
bom:piece:A:terminal
```

and replace its direct RM lines with previous-stage SFG lines.

Pros:

- stable terminal BoM identity;
- FG merge BoM remains untouched;
- simple product ownership.

Cons:

- existing G1 BoM content changes after G2 enablement.

#### Option 2: Create G2 Terminal BoM Variant

Use a new signature/version while keeping the logical candidate key stable:

```text
bom:piece:A:terminal
metadata.materialization_phase = G2
```

The writer updates when signature differs.

Pros:

- deterministic upgrade path;
- resolver can explain old-vs-new signature.

Cons:

- requires careful stale BoM handling.

Recommended:

```text
Use Option 2 with deterministic signature replacement diagnostics.
```

The resolver should report:

```text
existing terminal BoM found;
signature differs because G1 direct-RM terminal changed to G2 routed terminal;
writer will update if gates allow.
```

---

## Guardrails

G2 must pass all G1 guardrails plus these:

- strategy type must be `independent_routed_piece`;
- active route must contain a merge stage;
- at least one branch material stage must exist before merge;
- terminal piece stage must be discovered;
- each piece must have a complete route chain;
- each piece first stage must receive resolvable piece-tagged paper rows;
- each non-first stage must have exactly one previous-stage piece SFG input;
- FG merge BoM must consume terminal piece SFGs only;
- no raw paper on FG merge BoM;
- no shared-upstream markers;
- no split-after-stage markers;
- no nested piece UPS;
- no piece-specific stack/surface overrides until implemented;
- no ambiguous top-sheet stage ownership.

If any guard fails, writer remains blocked and raw JSON must explain the
reason.

---

## Preview / Raw JSON Diagnostics

G2 dry-run should expose a dedicated section:

```text
phase_g2_routed_piece_materialization_dry_run
```

Recommended payload:

```json
{
  "phase": "G2",
  "strategy_type": "independent_routed_piece",
  "guard": {
    "passed": true,
    "blockers": []
  },
  "route_segmentation": {
    "branch_stage_keys": ["2ply", "board"],
    "merge_stage_key": "seaming",
    "terminal_stage_key": "board"
  },
  "piece_chains": [
    {
      "piece_key": "A",
      "stages": [
        {
          "stage_key": "2ply",
          "product_candidate_key": "product:piece:A:stage:2ply",
          "bom_candidate_key": "bom:piece:A:stage:2ply",
          "components": ["paper"]
        },
        {
          "stage_key": "board",
          "product_candidate_key": "product:piece:A:stage:board",
          "bom_candidate_key": "bom:piece:A:stage:board",
          "components": ["product:piece:A:stage:2ply", "top_sheet_external"]
        },
        {
          "stage_key": "terminal",
          "product_candidate_key": "product:piece:A:sfg",
          "bom_candidate_key": "bom:piece:A:terminal",
          "components": ["product:piece:A:stage:board"]
        }
      ]
    }
  ],
  "merge": {
    "bom_candidate_key": "bom:fg:multi_piece_merge",
    "components": ["product:piece:A:sfg", "product:piece:B:sfg"],
    "operation_stage_key": "seaming"
  }
}
```

---

## UI Impact

No new editing UI is required for first G2.

Preview should add or extend a diagnostics section:

```text
Routed Piece Materialization

Piece A
  2-Ply Corrugation -> SFG
  Board Formation -> SFG
  Terminal Piece -> SFG

Piece B
  2-Ply Corrugation -> SFG
  Board Formation -> SFG
  Terminal Piece -> SFG

FG Merge
  Piece A Terminal + Piece B Terminal -> Seaming -> FG
```

Create BoM should remain behind the existing Phase 7B gates.

---

## Tests Required Before Write Enablement

### Planner Tests

- route segmentation finds branch stages before merge;
- terminal stage is the last branch stage;
- `2ply -> board -> seaming` produces 2 stage products plus terminal product
  per piece;
- `2ply -> board -> diecut -> seaming` produces 3 stage products plus terminal
  product per piece;
- shared-upstream flags block G2.

### Product Candidate Tests

- Piece A/B stage product candidate keys are deterministic;
- terminal product candidate keys remain G1-compatible;
- stage metadata includes piece key and stage key.

### BoM Candidate Tests

- first stage BoM consumes only piece-tagged paper rows;
- board BoM consumes previous 2ply SFG and board-stage inputs;
- terminal BoM consumes previous branch SFG;
- FG merge BoM consumes only terminal piece SFGs;
- FG merge BoM has no raw paper lines.

### Operation Tests

- 2ply operation appears only on piece 2ply BoM;
- board operation appears only on piece board BoM;
- merge operation appears only on FG merge BoM.

### Compatibility Tests

- G1 terminal products are reused by G2;
- G2 terminal BoM signature differs from G1 direct-RM terminal BoM;
- resolver reports update/reuse diagnostics clearly;
- Phase 7B gates still block writes when disabled.

---

## Implementation Sequence Recommendation

1. Add G2 planner in dry-run only.
2. Build route segmentation from active StageContext/StageGraph.
3. Generate piece chain candidates without writing.
4. Generate resolver dry-run diagnostics.
5. Add raw JSON diagnostics and tests.
6. Validate planner output in UI.
7. Enable writer path only after dry-run is accepted.

Do not enable actual G2 writes in the first design-to-code step.

---

## Final Design Position

G2 should be implemented as:

```text
independent routed piece materialization
```

It upgrades G1 from terminal direct-RM piece BoMs to routed piece stage chains
while preserving:

- terminal Piece A/B SFG products;
- FG merge BoM ownership;
- existing Phase 7B resolver/writer;
- current shared-upstream guardrails.

Shared-upstream splitting remains a separate Phase H/I strategy because it
requires full-deckle RM ownership, slitting/multi-output semantics, and cost
allocation rules that are not part of G2.



