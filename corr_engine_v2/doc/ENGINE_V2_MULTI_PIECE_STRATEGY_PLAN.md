# Engine V2 Multi-Piece And Split Strategy Plan

## Purpose

This document defines the planned architecture and phased implementation for
Engine V2 multi-piece carton manufacturing and automatic split suggestions.

The key decision is that **not every split is the same**.

Engine V2 must support two separate entry paths:

1. **Auto Split Path**
   - Starts from a normal single-piece box.
   - Engine suggests a split only when the full blank/layout is not feasible.
   - Initially limited to equal width or equal length split suggestions.

2. **Multi-Piece Design Path**
   - User explicitly defines the product as multi-piece before preview.
   - Supports unequal pieces, different stacks, different surfaces, different
     routes, and merge/assembly stages in future phases.

Internally, both paths may produce a piece plan, but they must carry different
   source/status metadata so preview, audit, costing, and materialization can
distinguish a machine workaround from true product design intent.

3. **Shared-Upstream Split Path**
   - User intentionally runs one or more upstream stages at full deckle/sheet
     width, then slits/splits the output into multiple pieces.
   - Example: run `2ply` on a 110 cm deckle, slit at `60:40`, then process
     Piece A and Piece B separately downstream and merge at seaming.
   - This is different from making Piece A and Piece B from separate reels.

---

## Core Principle

Keep these concepts separate:

- **Piece split**
  - Product/carton design geometry.
  - Example: blank width `900 = 500 + 400`.

- **Layout option**
  - Production sheet/run geometry.
  - Example: Piece A produced `2-up` on `1200 x 1000`.

- **Stack**
  - Material construction.
  - Example: Piece A is 5-ply BC, Piece B is 3-ply B.

- **Route**
  - Process flow.
  - Example: Piece A goes `2ply -> board -> diecut`, Piece B goes
    `board -> diecut`.

- **Merge**
  - Assembly step.
  - Example: `Diecut A + Diecut B -> Seaming -> FG`.

- **Split timing**
  - Where pieces become separate physical branches.
  - Example: `before_first_stage`, `after_2ply`, `after_board`,
    `after_printing`.

---

## Current Implementation Foundation

The first engine-side preview foundation has been started:

- `PieceSpecInput`
- `PiecePlanInput`
- multi-piece resolver
- equal width split
- uneven/manual width split
- equal length split
- branch/merge StageGraph metadata
- piece-tagged RM rows
- debug wizard preview-only fields
- Multi-Piece preview tab

Current implementation is **preview foundation only**.

Do not treat current multi-piece preview as final BoM materialization support.
Create BOM should remain guarded for multi-piece until piece SFG materialization
is implemented.

Phase D has added persistent preview-only multi-piece storage on
`corr.engine.v2.spec`. Accepted auto split choices can now be stored on the
Engine V2 spec and reused by preview without relying only on transient wizard
state.

---

## Entry Path 1: Auto Split Path

### Business Meaning

The product is entered as a normal single-piece box.

The user enters:

- Length
- Width
- Height
- Stack
- Surface
- Route

Then the user runs preview.

Engine derives:

- blank geometry
- layout options
- workcenter feasibility

If full blank/layout is feasible:

- show normal layout options
- no auto split required

If full blank/layout is not feasible:

- show no feasible layout for full blank
- generate split suggestions
- do not apply suggestion automatically

### Initial Auto Split Suggestions

Phase 1 suggestions:

- equal width split
- equal length split

Example:

```text
Full blank: 1200 x 900

Suggested width split:
Piece A: 1200 x 450
Piece B: 1200 x 450

Suggested length split:
Piece A: 600 x 900
Piece B: 600 x 900
```

### Auto Split Status

Auto split must have status:

```text
suggested
accepted
rejected
```

Rules:

- `suggested` is preview diagnostic only.
- `accepted` can rerun preview as a piece plan.
- only accepted split can be used for downstream materialization.

### Auto Split UI

In preview/layout tab:

```text
No feasible layout found for full blank.

Suggested Split Options
+--------+-------------+----------------+--------+
| Select | Split       | Piece Sizes    | Reason |
+--------+-------------+----------------+--------+
| [ ]    | Width-wise  | 450 + 450      | Fits width limit |
| [ ]    | Length-wise | 600 + 600      | Fits length limit |
+--------+-------------+----------------+--------+

[Apply Selected Split and Re-run Preview]
```

### Auto Split Scope For First Phase

Implement:

- detect infeasible full blank/layout
- propose equal width split
- propose equal length split
- show suggestions in preview
- allow user to apply one suggestion and rerun preview

Do not implement yet:

- uneven auto split
- cost-aware split ranking
- machine balancing
- automatic BoM creation from suggestion without user acceptance

---

## Entry Path 2: Multi-Piece Design Path

### Business Meaning

The product is intentionally designed as multiple pieces and merged later.

Examples:

- uneven design split: `900 = 500 + 400`
- different board stack per piece
- different flute per piece
- different top sheet per piece
- different surface profile per piece
- different piece routing
- different merge method

### Spec Form UI

Add a dedicated section/tab:

```text
Manufacturing Strategy
```

Top-level mode:

```text
Manufacturing Mode
( ) Single Piece
( ) Multi-Piece Design
```

When Single Piece:

```text
No piece configuration shown.
```

When Multi-Piece Design:

```text
Multi-Piece Configuration

Piece Count: [2]

Split Axis:
( ) Width-wise
( ) Length-wise

Split Method:
( ) Equal
( ) Ratio
( ) Primary Piece Size
( ) Manual Pieces

Merge Method:
[ Seaming v ]
```

### Piece Size Input Modes

#### Equal

User enters no piece dimensions.

Engine derives:

```text
Width split: 900 -> 450 + 450
Length split: 1200 -> 600 + 600
```

#### Ratio

User enters ratio:

```text
A : B = 60 : 40
```

Engine derives:

```text
900 -> 540 + 360
```

#### Primary Piece Size

User enters one known piece size.

Example width split:

```text
Piece A width = 500
Piece B width = auto = 400
```

Example length split:

```text
Piece A length = 700
Piece B length = auto = 500
```

#### Manual Pieces

Advanced mode.

User enters both piece sizes and engine validates total.

```text
Piece A width = 500
Piece B width = 400
Total must equal source blank width 900
```

### Piece Lines UI

For phase 1, show placeholders for future overrides but keep them same as FG:

```text
+-------+----------+--------+--------+------------+------------+-------+
| Piece | Qty / FG | Length | Width  | Stack      | Surface    | Route |
+-------+----------+--------+--------+------------+------------+-------+
| A     | 1        | Auto   | 500    | Same as FG | Same as FG | Same  |
| B     | 1        | Auto   | 400    | Same as FG | Same as FG | Same  |
+-------+----------+--------+--------+------------+------------+-------+
```

### Preview UI

Preview should show:

```text
Manufacturing Strategy

Mode: Multi-Piece Design
Source: Explicit spec configuration
Split: Width-wise, Uneven
Merge: Seaming

Source Blank:
Length: 1200 mm
Width: 900 mm
```

Pieces:

```text
+-------+----------+--------+--------+----------+
| Piece | Qty / FG | Length | Width  | Area     |
+-------+----------+--------+--------+----------+
| A     | 1        | 1200   | 500    | 0.600 m2 |
| B     | 1        | 1200   | 400    | 0.480 m2 |
+-------+----------+--------+--------+----------+
```

Routing:

```text
Piece A:
2ply-A -> Board-A -> Diecut-A

Piece B:
2ply-B -> Board-B -> Diecut-B

Merge:
Diecut-A + Diecut-B -> Seaming -> FG
```

RM:

```text
Raw Material By Piece

Piece A
  Liner 180 GSM
  Medium 150 GSM
  Liner 180 GSM

Piece B
  Liner 180 GSM
  Medium 150 GSM
  Liner 180 GSM
```

---

## Entry Path 3: Shared-Upstream Split Path

### Business Meaning

Some multi-piece cartons are not produced from separate reels or separate
upstream runs. Instead, the factory produces a shared upstream sheet/deckle and
then slits it into multiple piece outputs.

Example:

```text
Total deckle: 110 cm = 1100 mm
Split ratio: 60 : 40

Piece A width: 672 mm
Piece B width: 448 mm
```

Manufacturing flow:

```text
110 cm reel
   |
2ply corrugation full width
   |
Slitting 60:40
   |              |
2ply Piece A   2ply Piece B
   |              |
Board A        Board B
   |              |
Diecut A       Diecut B
    \            /
     Seaming / Merge
          |
         FG
```

### Why This Is Different

This is not:

```text
Piece A uses separate 67.2 cm reel
Piece B uses separate 44.8 cm reel
```

It is:

```text
Both pieces share one 110 cm upstream run
Pieces become separate only after slitting
```

Therefore RM consumption for shared stages must be based on the full deckle.
Piece-level quantities after the slit are allocation/output quantities, not
separate reel consumption.

### Strategy Type

Add strategy type:

```text
split_after_stage
```

Important fields:

```text
split_after_stage_key = 2ply
shared_stage_keys = [2ply]
branch_stage_keys = [board, diecut]
merge_stage_key = seaming
source_deckle_width_mm = 1100
split_axis = width
split_method = ratio
ratio = 60:40
```

### StageGraph Shape

Instead of branches from the first stage:

```text
2ply__piece_a -> board__piece_a
2ply__piece_b -> board__piece_b
```

Use a shared upstream stage:

```text
2ply_full
   |
slitting
  / \
 A   B
```

Expanded:

```text
2ply_full
   |
slitting_2ply
   |--------------------|
   v                    v
2ply__piece_a       2ply__piece_b
   |                    |
board__piece_a      board__piece_b
   |                    |
diecut__piece_a     diecut__piece_b
    \                  /
       seaming -> FG
```

### RM Rules

For shared upstream stages:

```text
RM consumed once at full source deckle/sheet width.
```

Example:

```text
SFG 2Ply Full Deckle 110 cm
  consumes:
    110 cm liner reel
    110 cm medium reel
```

After slitting:

```text
Piece A width = 672 mm
Piece B width = 448 mm
```

Piece-level RM can still be displayed for costing/allocation, but the real
input product resolution should be based on the shared 110 cm deckle for the
shared stage.

### BoM Shape

Shared upstream BoM:

```text
BoM: SFG 2Ply Full Deckle 110cm
  Components:
    Liner reel 110cm
    Medium reel 110cm
  Operation:
    2-Ply Corrugation
```

Slitting/split output:

```text
Input:
  SFG 2Ply Full Deckle 110cm

Outputs:
  SFG 2Ply Piece A 672mm
  SFG 2Ply Piece B 448mm
```

Downstream piece BoMs:

```text
BoM: SFG Board Piece A
  consumes SFG 2Ply Piece A

BoM: SFG Board Piece B
  consumes SFG 2Ply Piece B
```

Final merge:

```text
BoM: FG
  consumes final Piece A SFG
  consumes final Piece B SFG
  operation: Seaming
```

### UI Requirement

In Manufacturing Strategy UI, add:

```text
Split Timing:
( ) Before first stage
( ) After 2-Ply
( ) After Board
( ) After Printing
```

For this scenario:

```text
Split Timing: After 2-Ply
Shared Source Width: 1100 mm / 110 cm
Split Method: Ratio
Ratio A:B = 60:40
```

Preview should show:

```text
Shared Stage
2ply full deckle: 110 cm

Slitting
Piece A: 672 mm
Piece B: 448 mm

Branch Stages
Piece A: Board -> Diecut
Piece B: Board -> Diecut

Merge
Seaming -> FG
```

---

## Layout Options And Multi-Piece

Selected production layout can vary by chosen layout option.

Therefore piece definition must be separated from piece layout.

Piece definition:

```text
Piece A: 1200 x 500
Piece B: 1200 x 400
```

Piece layout:

```text
Piece A: 2-up on 1200 x 1000
Piece B: 2-up on 1200 x 800
```

Future preview should support piece-level layout options:

```text
Piece Layout Options

Piece A
+--------+-----+-------------+
| Select | UPS | Sheet Size  |
+--------+-----+-------------+
| [x]    | 2   | 1200 x 1000 |
| [ ]    | 3   | 1200 x 1500 |

Piece B
+--------+-----+------------+
| Select | UPS | Sheet Size |
+--------+-----+------------+
| [x]    | 2   | 1200 x 800 |
| [ ]    | 3   | 1200 x 1200 |
```

Phase 1 can keep current global layout behavior for preview foundation, but
final materialization must use piece-level layout when pieces are real SFGs.

---

## Data Model Direction

Current `PiecePlanInput` should evolve into a broader manufacturing strategy
model.

Recommended future model:

```python
ManufacturingStrategyInput
  strategy_type: single_piece / multi_piece / split_after_stage / assembly
  source: explicit_design / auto_suggested_split / accepted_auto_split
  status: draft / suggested / accepted / rejected
  split_after_stage_key
  shared_stage_keys
  pieces: List[PieceDefinitionInput]
  merge_plan: MergePlanInput
```

Piece definition:

```python
PieceDefinitionInput
  piece_key
  piece_name
  piece_index
  piece_qty_per_fg
  piece_width_mm
  piece_length_mm
  split_axis
  split_mode
  board_layers_v2
  board_recipe_id/key
  surface_profile_id/key
  routing_profile_id/key
  selected_layout
```

Merge plan:

```python
MergePlanInput
  merge_stage_key
  merge_method
  input_piece_keys
  output_product_key
```

Important metadata:

```text
source = auto_suggested_split / accepted_auto_split / explicit_multi_piece_design
is_design_intent = true / false
materialization_allowed = true / false
```

---

## StageGraph Direction

Single-piece graph:

```text
2ply -> board -> diecut -> seaming -> fg
```

Multi-piece graph:

```text
2ply__piece_a -> board__piece_a -> diecut__piece_a --+
                                                      |
                                                   seaming -> fg
                                                      |
2ply__piece_b -> board__piece_b -> diecut__piece_b --+
```

Stage metadata:

```python
base_stage_key
piece_key
stage_kind: normal / piece_branch / merge
split_node_key
split_after_stage_key
merge_policy: all_required
```

Flow metadata:

```python
piece_key
qty_per_fg
```

---

## BoM Materialization Direction

### Single Piece

Current materialization remains valid.

### Suggested Auto Split

No BoM creation until user accepts the suggestion.

### Explicit Or Accepted Multi-Piece

Generate piece-specific SFG products and BoMs.

Example:

```text
BoM: 2ply Piece A
  consumes Piece A paper layers

BoM: Board Piece A
  consumes 2ply Piece A

BoM: Diecut Piece A
  consumes Board Piece A

BoM: 2ply Piece B
  consumes Piece B paper layers

BoM: Board Piece B
  consumes 2ply Piece B

BoM: Diecut Piece B
  consumes Board Piece B

BoM: FG
  consumes Diecut Piece A x 1
  consumes Diecut Piece B x 1
  operation: Seaming
```

Guard current create flow:

```text
If multi-piece preview exists and materialization support is incomplete:
  show warning
  block create
```

---

## Phase Roadmap

### Phase A: Preview Foundation

Status: implemented.

Scope:

- preview-only multi-piece controls in debug wizard
- `PiecePlanInput`
- piece branch StageGraph
- piece-tagged RM rows
- Multi-Piece preview tab
- no persistent spec fields yet
- no multi-piece BoM materialization yet

### Phase B: UI Refinement

Status: implemented in debug wizard preview.

Scope:

- replace Piece A/B manual fields with safer input modes:
  - equal
  - ratio
  - primary piece size
  - manual pieces
- clearly label split source as finished blank, not sheet size
- show derived Piece B size automatically
- show disabled/hidden tab message when multi-piece is off
- keep Create BOM guarded for multi-piece

Implemented details:

- debug wizard split method supports:
  - equal
  - ratio
  - primary piece size
  - manual pieces
- explicit preview payload includes:
  - `source = explicit_multi_piece_design`
  - `status = accepted`
  - `is_design_intent = true`
  - `materialization_allowed = false`
- Create BOM from Preview is blocked when multi-piece is present.

### Phase C: Auto Split Suggestions

Status: implemented in debug wizard preview.

Scope:

- detect no feasible layout/full blank condition
- generate equal width split suggestion
- generate equal length split suggestion
- show suggestions in Layout tab
- allow apply suggestion and rerun preview
- store suggestion source/status metadata

Implemented details:

- suggestions are generated only when the full-blank layout option engine ran
  and no feasible layout options were exposed;
- suggestions are preview-only until accepted;
- accepting a suggestion reruns preview with:
  - `source = accepted_auto_split`
  - `status = accepted`
  - `is_design_intent = false`
  - `materialization_allowed = false`
- Create BOM remains blocked for accepted auto split/multi-piece previews.

### Phase D: Persistent Multi-Piece Spec Design

Status: implemented for preview-only persistence.

Scope:

- add spec-level manufacturing mode
- add piece line model
- add merge method field
- support same-as-FG stack/surface/route
- support explicit uneven pieces
- preview reads from spec design instead of transient wizard fields

Implemented details:

- `corr.engine.v2.spec` stores:
  - `multi_piece_enabled`
  - `multi_piece_source`
  - `multi_piece_split_axis`
  - `multi_piece_split_mode`
  - source blank length/width
  - merge stage key
- `corr.engine.v2.spec.piece` stores:
  - piece code/name
  - length/width
  - quantity per FG
- the spec adapter maps persisted fields into `PiecePlanInput`;
- accepted auto split suggestions write the selected split to the Engine V2
  spec before rerunning preview;
- switching between width/length split updates the persisted split;
- Clear Accepted Split removes the persisted accepted split;
- Create BOM remains blocked for multi-piece previews.

Still not implemented in Phase D:

- piece SFG products
- piece BoMs
- piece-specific routing
- multi-piece materialization

### Phase E: Piece-Level Layout Options

Status: implemented as preview-only piece layout planning.

Scope:

- generate layout options per piece
- select layout per piece
- compute piece-level sheets/runs
- surface selected piece layouts in preview and raw JSON

Implemented details:

- preview builds `piece_layout_planning` for persisted/accepted multi-piece
  previews;
- each piece is evaluated independently using its piece blank length/width;
- existing StageContext slitting/layout feasibility logic is reused;
- output includes:
  - feasible options
  - rejected options
  - preferred option
  - selected option, currently same as preferred
  - rotation/workcenter feasibility details from the existing engine;
- UI shows a dedicated **Piece Layout Planning** tab;
- raw JSON includes `piece_layout_planning` for later Phase G use.

Ownership rule:

- each piece is planned only against one layout owner stage;
- owner is the first layout-owning stage in the active route;
- if a stage is explicitly marked as the layout-driving stage, it wins;
- otherwise the first eligible slitting/layout stage in route sequence wins;
- downstream stages inherit the chosen piece layout until a future re-layout
  phase is implemented.

Examples:

- `2-Ply Corrugation -> Board Formation -> Seaming`
  - layout owner: `2-Ply Corrugation`
- `Board Formation -> Seaming`
  - layout owner: `Board Formation`
- `Cutting -> Printing -> Board -> Seaming`
  - layout owner: `Cutting`

Main UI intentionally shows only owner-stage summary:

- piece
- layout owner stage
- UPS/slits
- sheet size
- orientation
- capability

Workcenter/candidate details remain in raw JSON.

Nested piece planning rule:

- accepted pieces are treated as final manufacturing blanks in Phase E;
- piece-level planning displays only the piece itself as `1-up`;
- example: accepted piece `3020 x 560` displays only `UPS 1`,
  sheet `3020 x 560`, orientation `Direct`;
- options such as `UPS 2 -> 3020 x 1120` are considered nested piece
  planning and are not shown/applied in Phase E;
- owner-stage engine diagnostics can still appear in raw JSON for debugging.

Still not implemented in Phase E:

- persistent selected layout per piece
- piece SFG products
- piece BoMs
- piece routing execution
- materialization

### Phase F: Piece-Specific Stack And Surface

Status: implemented as architecture preparation only.

Scope:

- board stack per piece
- flute per piece
- top sheet per piece
- roll/unit top sheet combinations
- printed/plain piece combinations

Implemented details:

- piece line model now carries override slots for:
  - stack source
  - board recipe / stack profile
  - flute profile
  - surface source
  - surface profile
  - top sheet source
  - top sheet mode
  - top sheet product
- current behavior is inheritance only:
  - all pieces inherit parent spec stack;
  - all pieces inherit parent spec surface/top-sheet settings;
  - piece override fields are preparation fields, not active behavior;
- Multi-Piece preview shows:
  - `Stack Source: Inherited`
  - effective inherited flute profile
  - `Surface Source: Inherited`
  - effective inherited surface profile
  - `Top Sheet Source: Inherited`
  - effective inherited top sheet mode/product
- raw JSON includes `piece_manufacturing_overrides` with parent inherited
  references and per-piece override slots.
- projected branch display is labelled **Projected Piece Flow** because actual
  piece routing execution is not implemented in Phase F.

Still not implemented in Phase F:

- editing different stack/surface per piece
- different RM behavior per piece
- piece SFG products
- piece BoMs
- piece routing execution
- materialization

### Phase G: Materialization

Status: complete for independent routed multi-piece manufacturing.

Scope:

- G1 terminal piece SFG products and merge BoM
- G2 routed piece stage chains
- merge BoM/FG BoM
- generic 3/5/7-ply routed piece handling
- grouped/distinct corrugated set handling
- RM Breakdown and costing rollup across pieces
- manufacturing-signature SFG reuse for branch SFGs
- FG-specific readable terminal piece products
- resolver/writer support for branch/merge manufacturing plans

Phase G1 implemented details:

- raw JSON now includes `phase_g1_multi_piece_materialization_dry_run`;
- the G1 dry-run builds terminal piece SFG product candidates:
  - `product:piece:A:sfg`
  - `product:piece:B:sfg`
- the G1 dry-run builds one piece BoM candidate per piece:
  - `bom:piece:A:terminal`
  - `bom:piece:B:terminal`
- each piece BoM consumes only RM rows whose metadata carries the matching
  `piece_key`;
- before G1 plan generation, piece-tagged paper RM rows are enriched with
  `required_deckle_cm` derived from the selected piece layout width;
- example: piece `3020 x 560 mm` produces `required_deckle_cm = 56`;
- when Odoo paper mapping is available, the debug wizard attempts exact paper
  product resolution for the piece deckle before running the G1 resolver dry-run;
- G1 piece paper resolution is propagated back into preview RM metadata,
  `paper_resolution`, and BoM preview component metadata before RM Breakdown,
  BOM Preview, costing, Raw JSON, and G1 materialization diagnostics are built;
- RM Breakdown, BOM Preview, and G1 now use the same resolved piece paper
  source of truth;
- raw JSON includes `g1_resolution_reconciliation` with side-by-side markers
  for:
  - `result.rm_requirements`;
  - `paper_resolution`;
  - `bom_struct.components`;
  - `phase5_preview_model.materials`;
- the G1 dry-run builds the FG merge BoM candidate:
  - `bom:fg:multi_piece_merge`
- the FG merge BoM consumes only previous-output piece SFG lines;
- the FG merge BoM does not consume raw paper lines directly;
- the FG merge BoM carries only the merge operation in G1;
- the plan is passed through the existing Phase 7B resolver dry-run in the
  debug wizard;
- the existing Phase 7B writer strategy/payload diagnostics are generated for
  inspection;
- Create BoM from Preview now routes multi-piece previews through the G1
  materialized multi-level writer path;
- the legacy flattened BoM writer is not used for multi-piece previews;
- actual writes still require the existing Phase 7B gates:
  - context flag: `corr_materialization_writer_enabled`
  - system parameter: `corr_mrp_bridge.materialization_writer_enabled`
- when gates are open and resolver has no blockers, the existing Phase 7B
  writer creates/reuses:
  - Piece A SFG product;
  - Piece B SFG product;
  - Piece A terminal BoM;
  - Piece B terminal BoM;
  - FG merge BoM.

G1 guardrails:

- exactly two pieces;
- piece keys must be `A` and `B`;
- merge stage is required;
- stack/surface/top-sheet sources must remain inherited;
- piece layout UPS must be `1`;
- shared-upstream/split-after-stage markers are blocked;
- each piece must have RM rows tagged with its `piece_key`;
- BoM creation remains routed through the guarded Phase 7B writer.

Not in Phase G scope:

- piece SFG stock movement;
- custom child MO orchestration;
- shared-upstream materialization.

Phase G2 implemented details:

- raw JSON now includes `phase_g2_routed_piece_materialization_dry_run`;
- the G2 dry-run segments the active route into:
  - branch stages before merge;
  - merge stage;
  - terminal piece stage;
- example route `2ply -> board -> seaming` resolves as:
  - branch stages: `2ply`, `board`;
  - merge stage: `seaming`;
  - terminal stage: `board`;
- the G2 dry-run builds piece-stage SFG product candidates:
  - `product:piece:A:stage:2ply`;
  - `product:piece:A:stage:board`;
  - `product:piece:A:sfg`;
  - same for Piece B;
  - `product:fg`;
- the G2 dry-run builds routed piece BoM candidates:
  - first branch stage consumes piece-tagged paper RM;
  - board stage consumes previous piece-stage SFG and unit top sheet when
    applicable;
  - terminal piece BoM consumes the previous branch-stage SFG;
  - FG merge BoM consumes only terminal Piece A/B SFGs and owns only the merge
    operation;
- the G2 path uses the existing Phase 7B resolver/writer diagnostics path;
- G2 writes are now enabled behind the existing Phase 7B gates when:
  - multi-piece is enabled;
  - G2 guard passes;
  - route segmentation succeeds;
  - the active route has branch stages and a merge stage.
- when those conditions pass, Create BoM prefers the G2 routed piece chain over
  G1 terminal-only materialization;
- if G2 is unavailable or blocked, the multi-piece path can still fall back to
  the G1 terminal-only plan.
- branch SFGs such as Board and 2-Ply use manufacturing-signature identity and
  can be reused across different FG products when dimensions, recipe, top sheet,
  and route-relevant manufacturing attributes match;
- terminal piece SFGs remain FG-specific and are named as:
  - `<FG Product Name> - Piece 1`;
  - `<FG Product Name> - Piece 2`.

G2 guardrails:

- exactly two pieces;
- inherited stack/surface/top-sheet only;
- no shared-upstream/split-after-stage markers;
- piece layout UPS must be `1`;
- merge stage must exist in the active route;
- branch stages before merge are required;
- FG merge BoM must not contain raw paper lines.

### Phase H: Shared-Upstream Split

Status: design document created; implementation not started.

Design document:

```text
ENGINE_V2_PHASE_H_SHARED_UPSTREAM_SPLIT_DESIGN.md
```

Scope:

- split after stage, for example:

```text
2ply full sheet -> slitting -> Piece A + Piece B
```

- shared upstream SFG product
- shared upstream BoM
- slitting/split node with multiple outputs
- piece branches after split node
- RM resolution based on full shared deckle
- piece-level allocation/reporting after slit
- allocation-based cost rollup
- preview-first planning before writes

### Phase I: Advanced Piece Scenarios

Scope:

- 3-piece boxes
- 4-piece/grid boxes
- piece qty per FG greater than 1
- piece-specific routing
- cost-aware and capacity-aware split recommendations

---

## Immediate Next Steps

1. Review and approve
   `ENGINE_V2_PHASE_H_SHARED_UPSTREAM_SPLIT_DESIGN.md`.

2. Start Phase H1 only after approval:
   - shared-upstream route segmentation;
   - shared/full-deckle layout planning;
   - split output planning;
   - branch feasibility validation;
   - raw JSON dry-run diagnostics;
   - Create BoM remains blocked.

3. Keep Phase G independent routed piece materialization unchanged while Phase H
   is designed and validated.



