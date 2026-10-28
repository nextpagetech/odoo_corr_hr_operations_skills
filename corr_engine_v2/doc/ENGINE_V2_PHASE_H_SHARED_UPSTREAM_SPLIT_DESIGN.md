# Engine V2 Phase H - Shared Upstream Split Design

## Status

Phase H shared-upstream split is implemented through the equal-split H4 path.

Current supported materialization:

- `multi_piece_strategy = shared_upstream_split`;
- exactly two pieces;
- inherited stack/surface/top-sheet only;
- split after the first shared material stage;
- equal split only;
- shared upstream BoM chain is created/reused;
- Shared Upstream Flow visualization is available in preview.

Unequal shared-upstream split materialization is deferred as Phase H5/H6 R&D.
Do not implement unequal split manufacturing logic without a new architecture
decision.

Phase F and Phase G are considered complete for the independent multi-piece
manufacturing path:

- piece-specific manufacturing override structure exists;
- inherited stack/surface/top-sheet values are displayed;
- G1 terminal piece SFG materialization exists;
- G2 routed piece materialization exists;
- grouped/distinct corrugated-set logic exists;
- RM breakdown and costing roll up across pieces;
- SFG reuse is manufacturing-signature driven for branch SFGs;
- terminal piece SFGs remain FG-specific and have user-facing names.

Phase H introduces a different strategy:

```text
shared upstream manufacturing -> split/slitting -> independent piece branches -> merge FG
```

This must not be mixed silently with the existing G2 independent-piece strategy.

Implemented behavior:

- `multi_piece_strategy = shared_upstream_split` is supported in spec, wizard,
  payload, preview, and Raw JSON.
- `split_after_stage_key` is supported as the H1 split node selector.
- Raw JSON includes `phase_h_shared_upstream_split_dry_run`.
- H2 materialization dry-run produces shared upstream, split output, branch,
  terminal piece, and FG merge candidates.
- H3/H4 write path can create/reuse the equal-split shared upstream BoM chain.
- Equal split is represented with a real split-output BoM:

```text
2-Ply 302x56 cm
BoM quantity = 2 Units
Component:
  Shared 2-Ply 302x112 cm x 1 Unit
Operation:
  Slitting
```

- Existing `independent_routed_piece` G2 behavior remains the default.

Deferred:

- unequal split manufacturing materialization;
- byproduct/multi-output stock semantics;
- custom split stock moves;
- custom MO orchestration;
- piece quantities other than `1` for shared-upstream split.

---

## Business Scenario

Current G2 independent piece flow:

```text
Piece A 2-Ply -> Piece A Board -> Piece A Terminal
Piece B 2-Ply -> Piece B Board -> Piece B Terminal
Piece A Terminal + Piece B Terminal -> FG Merge
```

Shared-upstream target flow:

```text
Shared 2-Ply Full Deckle
          |
       Slitting
       /      \
Piece A      Piece B
  |            |
Board A      Board B
  |            |
Terminal A   Terminal B
       \      /
        FG Merge
```

Example:

- source blank/deckle: `3020 x 1120 mm`
- split width: `560 + 560 mm`
- first stage: `2-Ply Corrugation`
- shared upstream uses full `112 cm` deckle paper;
- slitting creates two piece blanks;
- board, diecut, and terminal piece stages run per piece;
- seaming merges terminal pieces into FG.

This differs from independent G2 because raw paper ownership belongs to the
shared upstream SFG, not to Piece A and Piece B independently.

---

## Phase H Scope

Phase H should support:

- exactly two pieces initially;
- split after one upstream stage;
- shared upstream stage initially limited to the first layout-owning material
  stage, usually `2ply` or `board`;
- width split and length split;
- equal split materialization in the current implementation;
- inherited stack/surface/top-sheet only;
- piece layout UPS must remain `1`;
- no nested piece planning;
- no multi-output Odoo stock move customization in the first write step.

Phase H should not yet support:

- 3-piece or 4-piece boxes;
- multiple split nodes;
- piece-specific stack before split;
- different flutes before split;
- shared upstream plus piece-specific recipes in the shared portion;
- automatic child MO orchestration beyond standard BoM/procurement behavior;
- physical byproduct accounting for split outputs unless a later phase approves
  it.
- unequal shared-upstream split materialization.

---

## Current Equal-Split Materialization

Equal split is the only shared-upstream split mode currently allowed for writes.

Example:

```text
Shared 2-Ply 302x112 cm
  -> full-deckle paper RM, 112 cm

2-Ply 302x56 cm
  BoM quantity = 2 Units
  -> Shared 2-Ply 302x112 cm x 1 Unit
  -> Slitting operation

Board 302x56 cm
  -> 2-Ply 302x56 cm x 1 Unit
  -> top sheet if unit top sheet mode applies

FG
  -> <FG Product> - Piece 1
  -> <FG Product> - Piece 2
```

Odoo BoM Overview normalizes the split BoM when exploding one piece branch, so
it may display:

```text
2-Ply 302x56 cm
  -> Shared 2-Ply 302x112 cm x 0.5
```

This is expected display math when the actual split BoM has quantity `2` and
component quantity `1`. The direct `2-Ply 302x56 cm` BoM record is the source of
truth.

---

## Deferred Phase H5/H6 R&D - Unequal Split

Unequal split manufacturing materialization is paused.

Reason: unequal split requires architecture decisions that affect:

- multi-output manufacturing;
- byproducts;
- cost allocation;
- MO generation;
- stock move behavior;
- traceability from a single full-deckle run to multiple differently sized split
  outputs.

Open design questions:

1. Should a full-deckle shared upstream run be a real stockable SFG, planning
   metadata only, or a byproduct-producing manufacturing step?
2. Should slitting be represented as a separate BoM/MO, a byproduct operation on
   the first material stage, or a custom logical split model?
3. For `302x120 -> 302x72 + 302x48`, how should Odoo generate procurement/MOs
   without double-consuming full-deckle RM?
4. Should cost allocation follow width ratio, area ratio, explicit percentages,
   or byproduct cost shares?
5. How should split output traceability be stored without introducing custom
   stock moves too early?
6. How should BoM Overview be made clear when Odoo's standard explosion cannot
   naturally show one run producing two different output products?

Deferred options to evaluate:

```text
Option A: First-stage multi-output/byproduct model
Option B: Dedicated split/slitting BoM with byproducts
Option C: Custom logical split model with planning/cost allocation only
```

Do not implement H5/H6 until these decisions are accepted.

---

## Strategy Types

Use explicit strategy names. Do not infer shared-upstream behavior from a normal
multi-piece flag.

Recommended values:

```text
independent_routed_piece
shared_upstream_split
```

Current G2 remains:

```text
strategy_type = independent_routed_piece
```

Phase H introduces:

```text
strategy_type = shared_upstream_split
split_timing = after_stage
split_after_stage_key = 2ply
split_node_key = slitting
```

---

## Data Model Additions

Add persistent fields to the Engine V2 spec/multi-piece structure only after the
design is accepted.

Recommended fields:

```text
multi_piece_strategy
split_timing
split_after_stage_key
split_node_key
shared_upstream_enabled
shared_upstream_stage_keys
source_blank_length_mm
source_blank_width_mm
source_deckle_width_mm
split_axis
split_mode
split_allocation_method
split_ratio_a
split_ratio_b
split_width_a_mm
split_width_b_mm
split_length_a_mm
split_length_b_mm
```

Piece line additions:

```text
piece_key
piece_index
piece_name
piece_qty_per_fg
piece_length_mm
piece_width_mm
split_output_key
split_output_qty
shared_upstream_group_key
post_split_route_stage_keys
```

Preview-only additions:

```text
shared_upstream_plan
shared_upstream_layout
split_node
split_outputs
allocation_debug
```

Materialization metadata additions:

```text
strategy_type = shared_upstream_split
shared_upstream_group_key
shared_stage_keys
split_after_stage_key
split_node_key
split_output_keys
source_deckle_width_mm
source_blank_length_mm
source_blank_width_mm
allocation_method
allocation_ratio
```

---

## StageGraph Extension

Current G2 branch/merge graph:

```text
Piece A stage chain -> Piece A Terminal --+
                                         FG Merge
Piece B stage chain -> Piece B Terminal --+
```

Phase H graph:

```text
Shared Stage Chain
        |
    Split Node
     /     \
Piece A   Piece B
Branch    Branch
     \     /
     FG Merge
```

StageGraph node types:

```text
shared_stage
split_node
piece_stage
terminal_piece
merge_stage
fg
```

Edges:

```text
shared_stage -> split_node
split_node -> piece_stage:A:first_post_split
split_node -> piece_stage:B:first_post_split
piece_stage:A:last -> terminal_piece:A
piece_stage:B:last -> terminal_piece:B
terminal_piece:A -> merge_stage
terminal_piece:B -> merge_stage
merge_stage -> fg
```

The split node is logical in Phase H. It should exist in preview/materialization
metadata even if no separate Odoo operation or stock move is created initially.

---

## Route Segmentation

Phase H route segmentation must identify:

```text
upstream shared stages
split stage/node
post-split branch stages
merge stage
terminal branch stage
```

Example route:

```text
2ply -> board -> diecut -> seaming
```

If split after `2ply`:

```text
shared stages = [2ply]
split node = slitting after 2ply
post-split branch stages = [board, diecut]
merge stage = seaming
terminal stage = diecut
```

If split after `board`:

```text
shared stages = [2ply, board]
split node = slitting after board
post-split branch stages = [diecut]
merge stage = seaming
terminal stage = diecut
```

If route is:

```text
board -> seaming
```

and split after `board`:

```text
shared stages = [board]
post-split branch stages = []
terminal pieces are split outputs from shared board
```

This last case should be allowed only if the split output can be treated as the
terminal piece input to merge.

---

## Planner Rules

### 1. Shared Layout Owner

The shared upstream layout owner is the first layout-owning material stage before
or equal to `split_after_stage_key`.

For:

```text
2ply -> board -> seaming
split_after_stage = 2ply
```

shared layout owner is `2ply`.

For:

```text
board -> seaming
split_after_stage = board
```

shared layout owner is `board`.

### 2. Feasibility

Planner must check:

- full source blank fits shared upstream workcenter;
- split outputs fit post-split branch layout owner/workcenters;
- merge stage exists and can consume all terminal piece outputs.

For width split:

```text
source = 3020 x 1120
Piece A = 3020 x 560
Piece B = 3020 x 560
```

Validation:

- shared stage checks `3020 x 1120`;
- branch stage checks `3020 x 560`;
- length split checks `1510 x 1120` for branch stage.

### 3. Split Candidate Validation

Do not suggest a shared-upstream split because it divides mathematically.

A candidate is valid only when:

- shared upstream stage can process the source blank;
- every split output can be processed by the first post-split branch stage;
- split outputs match piece dimensions;
- no nested UPS exists;
- piece quantities are supported.

### 4. Allocation

Initial allocation method:

```text
by_piece_area
```

For equal width split:

```text
Piece A cost share = 50%
Piece B cost share = 50%
```

For uneven width split:

```text
Piece A cost share = width_a / source_width
Piece B cost share = width_b / source_width
```

For length split:

```text
Piece A cost share = length_a / source_length
Piece B cost share = length_b / source_length
```

Future allocation methods:

```text
by_area
by_ratio
by_piece_qty
manual_percentage
```

---

## Materialization Model

Current Phase H4 equal-split BoM chain for `2ply -> board -> seaming`, split
after `2ply`:

```text
Shared 2-Ply SFG
  -> full-deckle paper RM

Split Output 2-Ply SFG
  BoM quantity = 2
  -> Shared 2-Ply SFG x 1
  -> Slitting operation

Piece A Board SFG
  -> Split Output 2-Ply SFG x 1
  -> top sheet if board stage needs unit top sheet

Piece A Terminal SFG
  -> Piece A Board SFG

Piece B Board SFG
  -> Split Output 2-Ply SFG x 1
  -> top sheet if board stage needs unit top sheet

Piece B Terminal SFG
  -> Piece B Board SFG

FG BoM
  -> Piece A Terminal SFG x qty_per_fg
  -> Piece B Terminal SFG x qty_per_fg
  -> merge operation only
```

The older allocation model:

```text
Piece A Board -> Shared 2-Ply x 0.5
Piece B Board -> Shared 2-Ply x 0.5
```

is no longer the target write model. If Odoo BoM Overview displays
`Shared 2-Ply x 0.5` under each branch, that should be the normalized explosion
of the split-output BoM quantity, not a direct branch BoM component.

---

## Product Strategy

### Shared Upstream SFG

Shared upstream SFGs should be manufacturing-signature driven and reusable across
FG products.

Identity includes:

- shared stage sequence;
- source blank dimensions;
- deckle width;
- paper recipe;
- flute/GSM/BF/color;
- routing up to split stage;
- top sheet only if top sheet is consumed before or at split stage;
- layout owner and selected shared layout.

Identity excludes:

- FG product name;
- piece key;
- customer-facing piece names;
- spec id unless spec id changes manufacturing behavior.

### Post-Split Branch SFGs

Branch SFG identity depends on:

- piece dimensions;
- post-split stage;
- consumed shared SFG key;
- branch-stage inputs such as top sheet;
- route stage flow after split.

If two different FGs share the same post-split manufacturing definition, branch
SFGs can be reused.

### Terminal Piece SFGs

Terminal pieces remain FG-specific and user-facing:

```text
<FG Product Name> - Piece 1
<FG Product Name> - Piece 2
```

These should not be reused across different FG products.

They should be reused when the same FG regenerates BoMs.

---

## BoM Strategy

### Shared Upstream BoM

Consumes raw materials at full source deckle:

```text
Shared 2-Ply 302x112 cm
  -> liner 112 cm deckle
  -> medium 112 cm deckle with TUF factor
  -> operation: 2-Ply Corrugation
```

### Split Node

Current H4 equal split represents the split node as a split-output BoM with a
Slitting operation.

The split still carries metadata for flow/debugging:

```text
split_node_key = split:after:2ply
split_axis = width
source_blank = 3020 x 1120
outputs:
  A = 3020 x 560
  B = 3020 x 560
allocation:
  A = 0.5
  B = 0.5
```

### Split Output BoM

For equal split:

```text
2-Ply 302x56 cm
  BoM quantity = 2 Units
  -> Shared 2-Ply 302x112 cm x 1 Unit
  -> Slitting operation
```

The direct BoM record must show `product_qty = 2` and shared upstream component
quantity `1`.

### Branch BoMs

Branch BoMs consume the split-output SFG, not the shared upstream SFG directly.

```text
Piece A Board
  -> 2-Ply 302x56 cm x 1
  -> top sheet A if applicable

Piece B Board
  -> 2-Ply 302x56 cm x 1
  -> top sheet B if applicable
```

Odoo BoM Overview may still show `Shared 2-Ply x 0.5` under each branch because
it normalizes the split-output BoM quantity from `2` to the branch demand of
`1`. That is display math, not a direct branch component.

Unequal split branch behavior is deferred to Phase H5/H6 R&D.

### FG Merge BoM

Same as G2:

```text
FG
  -> Piece 1 Terminal
  -> Piece 2 Terminal
  -> merge operation only
```

No raw paper lines on FG merge BoM.

---

## Quantity Semantics

Recommended internal quantities:

```text
source_output_qty = 1 full shared blank
piece_output_qty = piece_qty_per_fg
allocation_qty = piece_area / source_area
```

For equal 2-piece split:

```text
Shared 2-Ply BoM product_qty = 1 shared blank
Piece A branch consumes shared blank qty = 0.5
Piece B branch consumes shared blank qty = 0.5
```

For piece B qty per FG = 2:

```text
Piece A allocation = area(A) * 1 / total_area
Piece B allocation = area(B) * 2 / total_area
```

This must be validated carefully because quantity-per-FG greater than 1 changes
allocation math.

Initial Phase H guard can block `piece_qty_per_fg != 1` until validated.

---

## Cost Rollup

Cost rollup must avoid double-counting shared upstream RM.

Recommended approach:

1. Shared upstream SFG owns full upstream material and operation cost.
2. Each branch gets an allocated share of shared upstream cost.
3. Branch-specific material/operation cost is added on top.
4. Terminal piece cost rolls up from branch chain.
5. FG cost rolls up terminal pieces plus merge operation.

Cost diagnostics should show:

```text
Shared Upstream Cost
  2-Ply Shared: full cost

Allocation
  Piece A: 50%
  Piece B: 50%

Piece Cost
  Piece A = shared allocation + branch cost
  Piece B = shared allocation + branch cost

FG Cost
  Piece A + Piece B + merge
```

RM Breakdown should still show FG-level totals once, not duplicated per branch.

---

## Preview UI

Add a new section/tab only when strategy is shared-upstream:

```text
Shared Upstream Split
```

Display:

- strategy: Shared Upstream Split;
- split after stage;
- split axis/mode;
- source blank;
- source deckle;
- shared upstream routing;
- split outputs;
- allocation method;
- allocation percentages;
- post-split branch routing;
- merge stage;
- materialization status.

Example:

```text
Shared Upstream Split

Shared Stage:
2-Ply Corrugation

Source Blank:
3020 x 1120 mm

Split:
Width / Equal

Outputs:
Piece 1: 3020 x 560 mm, Allocation 50%
Piece 2: 3020 x 560 mm, Allocation 50%

Post-Split Flow:
Piece 1: Board -> Terminal
Piece 2: Board -> Terminal

Merge:
Seaming
```

Layout Options tab should distinguish:

```text
Full shared layout
Post-split piece layout
```

Raw JSON should include:

```text
phase_h_shared_upstream_split_dry_run
```

---

## Guardrails

Block Phase H if:

- multi-piece is disabled;
- strategy is not `shared_upstream_split`;
- piece count is not exactly 2;
- split after stage is missing;
- split after stage is not in active route;
- merge stage is missing;
- shared stage cannot process source blank;
- post-split branch stage cannot process piece blank;
- stack/surface/top-sheet is not inherited;
- piece layout UPS is not 1;
- nested split exists;
- piece qty per FG is not 1 in first implementation;
- allocation ratio does not sum to 1;
- FG merge BoM would contain raw paper;
- shared upstream RM cannot resolve full source deckle.

Do not fall back silently to independent G2 if the user selected
shared-upstream strategy. Show a clear blocker.

---

## Migration Impact

No migration is needed for existing G2 specs.

New fields should default to:

```text
multi_piece_strategy = independent_routed_piece
shared_upstream_enabled = False
split_timing = before_first_stage
```

Existing accepted auto-split specs should remain independent G2 unless the user
explicitly selects shared-upstream.

Existing SFGs and BoMs should not be renamed or deleted.

---

## Implementation Plan

### Phase H1 - Dry-Run Planner Only

- add strategy selection to payload/spec;
- build route segmentation for shared upstream;
- build split node preview model;
- validate shared and branch feasibility;
- generate `phase_h_shared_upstream_split_dry_run`;
- do not write products/BoMs;
- keep Create BoM blocked for shared-upstream.

### Phase H2 - Materialization Dry-Run

- generate shared upstream SFG product candidate;
- generate shared upstream BoM candidate;
- generate branch product candidates after split;
- generate terminal piece SFG candidates;
- generate FG merge BoM candidate;
- run existing Phase 7B resolver dry-run;
- expose writer payload diagnostics;
- still keep writes disabled until validated.

### Phase H3 - Write Enablement

- enable writes behind existing Phase 7B gates;
- reuse existing Phase 7B writer;
- create/reuse shared upstream SFG;
- create/reuse shared upstream BoM;
- create/reuse branch SFGs/BoMs;
- create/reuse terminal piece SFGs/BoMs;
- create/reuse FG merge BoM;
- no custom MO orchestration.

### Phase H4 - Equal Split BoM Chain

- equal split only;
- create/reuse split-output SFG;
- create/reuse split-output BoM with `product_qty = 2`;
- split-output BoM consumes shared upstream SFG qty `1`;
- split-output BoM carries Slitting operation;
- branch Board BoMs consume split-output SFG, not direct shared upstream
  allocation lines.

### Phase H5/H6 - Unequal Split R&D

Deferred until the rest of Engine V2 is complete enough to revisit the
manufacturing architecture.

- evaluate multi-output/byproduct manufacturing;
- evaluate first-stage direct split-output production;
- evaluate custom logical split model;
- define cost allocation;
- define MO/procurement behavior;
- define stock move behavior;
- define traceability.

---

## Test Matrix

Planner tests:

- `2ply -> board -> seaming`, split after `2ply`;
- `2ply -> board -> diecut -> seaming`, split after `2ply`;
- `2ply -> board -> diecut -> seaming`, split after `board`;
- `board -> seaming`, split after `board`;
- width split validates branch width;
- length split validates branch width remains unchanged;
- infeasible branch candidate is rejected with reason.

Materialization dry-run tests:

- shared upstream SFG consumes full-deckle RM;
- piece branch BoMs do not consume raw paper already owned upstream;
- branch BoMs consume shared upstream SFG;
- top sheet attaches to board stage when board is after split;
- FG merge BoM has terminal pieces only;
- no raw paper on FG merge BoM.

Reuse tests:

- same shared upstream manufacturing reuses shared SFG;
- same branch manufacturing reuses branch SFG;
- terminal pieces remain FG-specific;
- changed source deckle creates new shared SFG;
- changed top sheet after split creates new branch Board SFG.

Cost tests:

- shared upstream cost is counted once;
- allocated piece costs sum to shared upstream total;
- FG total equals allocated pieces plus branch and merge costs.

UI tests:

- preview clearly shows shared strategy;
- split outputs are visible;
- allocation percentages are visible;
- Create BoM blocked in H1/H2;
- materialization diagnostics visible in Raw JSON.

---

## Recommendation

Implement Phase H as a new strategy path:

```text
shared_upstream_split
```

Do not modify G2 independent routed piece materialization except to keep guards
clear.

Start with H1 dry-run planner only. Validate the preview and allocation model
before any write enablement.



