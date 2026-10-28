# Engine V2 Style Family Architecture

## Purpose

Design the style-family architecture before adding more FEFCO styles.

Goal:

- support dozens of future styles without duplicating geometry, validation, and capability logic
- keep style-specific behavior isolated under `engine_v2/styles/`
- keep shared downstream systems style-agnostic
- avoid style leakage into StageGraph, RM resolver, materialization writer, multi-piece materialization, and shared-upstream logic

No implementation is included in this document.

## Current Baseline

Current completed styles:

- 0201: Regular Slotted Container
- 0202: Overlap Slotted Container
- 0203: Full Overlap Slotted Container

Current architecture:

- individual style handlers under `engine_v2/styles/`
- `STYLE_REGISTRY`
- `StyleResult`
- `corr.engine.style`
- `corr.engine.style.param`
- spec-specific style parameter values
- adapter mapping into `spec_input.style_params`

The next architecture step should introduce family-level helpers/classes so new styles can reuse common panel math, seam rules, capability defaults, score/fold metadata, and validation.

## Proposed Package Shape

Recommended future package structure:

```text
engine_v2/styles/
  contract.py
  registry.py
  family_base.py
  families/
    rolls_sheets.py
    slotted.py
    telescope.py
    folder.py
    ready_glued.py
  style_0100.py
  style_0110.py
  style_0201.py
  style_0202.py
  style_0203.py
  style_0204.py
  style_0209.py
  style_0215.py
  style_0216.py
  style_0300.py
  style_0320.py
  style_0409.py
  style_0427.py
  style_0700.py
  style_0713.py
```

Recommended conceptual class hierarchy:

```text
BaseStyle
 |-- BaseRollSheetStyle
 |   |-- 0100
 |   `-- 0110
 |-- BaseSlottedStyle
 |   |-- 0201
 |   |-- 0202
 |   |-- 0203
 |   |-- 0204
 |   |-- 0209
 |   |-- 0215
 |   `-- 0216
 |-- BaseTelescopeStyle
 |   |-- 0300
 |   |-- 0301
 |   |-- 0306
 |   `-- 0320
 |-- BaseFolderStyle
 |   |-- 0409
 |   |-- 0411
 |   |-- 0413
 |   `-- 0427
 `-- BaseReadyGluedStyle
     |-- 0700
     |-- 0711
     |-- 0713
     |-- 0715
     `-- 0759
```

Implementation can remain function-based externally:

```python
@register_style("0204")
def compute_blank(spec_input, plant_profile):
    return Style0204().compute(spec_input, plant_profile)
```

That keeps `STYLE_REGISTRY` unchanged while allowing family classes internally.

## Shared BaseStyle Responsibilities

`BaseStyle` should centralize:

- geometry input extraction from `GeometrySpec` or legacy attributes
- style parameter extraction from `spec_input.style_params`
- seam/glue flap precedence where relevant
- numeric validation helpers
- required parameter validation
- min/max validation
- standard `StyleResult` construction helpers
- metadata naming conventions
- score/fold line payload helpers
- capability defaults

Suggested base methods:

```text
compute(spec_input, plant_profile) -> StyleResult | None
read_geometry(spec_input)
read_style_params(spec_input)
read_param(name, default=None, required=False)
read_seam(spec_input, plant_profile)
validate_positive_dimensions(...)
build_result(...)
capabilities()
metadata()
```

## 1. 01xx - Rolls And Sheets

### Family Description

01xx covers commercial rolls and sheets. These are basic corrugated material forms rather than erected boxes.

Typical examples:

- 0100 commercial rolls
- 0110 sheet cuts
- free corrugated sheets
- pads or simple sheet products when mapped to this family

### Common Geometry Pattern

Geometry is usually direct:

```text
sheet blank:
blank_length = sheet_length
blank_width  = sheet_width

roll:
blank_width = roll_width
roll_length or area basis determines consumption
```

Unlike box styles, there may be no `L/W/H`, no seam, and no flap geometry.

### Common Parameters

Likely parameters:

- sheet_length_mm
- sheet_width_mm
- roll_width_mm
- roll_length_m
- sheet_count
- pad_count
- trim_margin_mm
- grain/flute direction
- area_basis_m2

Some of these already exist as spec/layout fields. Do not duplicate fields as style params unless they are truly style-specific.

### Common Validation Rules

- sheet length must be positive
- sheet width must be positive
- roll width must be positive
- roll length or area basis must be positive for roll products
- height should not be required
- seam/glue flap should not be required
- box-specific flap fields should be ignored
- UoM must be explicit for roll products

### Common Capabilities

Suggested defaults:

```text
width split:       false for finished sheets, maybe true for parent roll slitting
length split:      false unless cut-to-length roll process is modeled
shared upstream:   true for sheet/pad flows; review for roll/slitting
rotation:          true for sheets if grain/flute direction allows; false if grain-locked
```

### Suggested Base Class

```text
BaseRollSheetStyle
 â”œâ”€ DirectSheetStyle
 â”œâ”€ RollStyle
 â””â”€ PadStyle
```

Shared helpers:

- direct rectangular blank builder
- sheet/roll dimension validation
- optional grain-axis capability
- non-box metadata

### Quick Styles Once Family Exists

- 0110 sheet cuts
- simple pads
- simple rectangular sheet products

### Production Confirmation Required

- 0100 roll products
- roll-to-sheet consumption rules
- slitting/cut-to-length behavior
- UoM conversion rules

## 2. 02xx - Slotted Boxes

### Family Description

02xx covers slotted boxes. They are usually one-piece boxes with a manufacturer's joint and top/bottom flaps. They are shipped flat and closed by the provided flaps.

Current completed styles:

- 0201
- 0202
- 0203

### Common Geometry Pattern

Most 02xx styles share a body-panel wrap:

```text
body_wrap_length = 2 * (L + W)
blank_length     = body_wrap_length + seam
```

Width direction varies by flap model:

```text
0201: blank_width = H + W + flap_add
0202: blank_width = H + W + (2 * overlap)
0203: blank_width = H + (2 * W)
```

Other 02xx styles vary by missing flaps, partial flaps, locking base/tuck top, or flange/hand-hole features.

### Common Parameters

Common parameters:

- seam_mm or glue flap override
- flap_add_mm
- overlap_mm
- top_flap_depth_mm
- bottom_flap_depth_mm
- tuck_flap_depth_mm
- lock_tab_width_mm
- flange_depth_mm
- slot_allowance_mm
- clearance_mm

Existing seam precedence should remain shared:

1. `glue_flap_override_mm`
2. legacy/spec glue flap
3. plant default glue lap
4. fallback

### Common Validation Rules

- `L`, `W`, `H` must be positive
- seam must be positive for jointed styles
- required flap/overlap/lock params must be positive
- top/bottom flap depths must not exceed production-confirmed limits
- partial-open styles must explicitly define missing top/bottom flaps
- unsupported asymmetric flap variants should fail clearly

### Common Capabilities

Suggested defaults:

```text
width split:       true
length split:      false
shared upstream:   true
rotation:          true unless grain/layout policy disallows
```

Exceptions:

- hand-hole/flange/lock styles may restrict rotation
- styles with orientation-sensitive printing may restrict rotation through layout policy
- some lock-base styles may need more cautious split support

### Suggested Base Class

```text
BaseSlottedStyle
 â”œâ”€ RegularSlottedMixin
 â”œâ”€ OverlapFlapMixin
 â”œâ”€ FullOverlapFlapMixin
 â”œâ”€ HalfSlottedMixin
 â”œâ”€ LockBottomMixin
 â””â”€ FlangeMixin
```

Shared helpers:

- body wrap length
- seam calculation
- flap depth calculation
- standard 02xx score/fold lines
- rectangular `StyleResult`
- default capabilities

Example:

```text
BaseSlottedStyle
 â”œâ”€ 0201
 â”œâ”€ 0202
 â”œâ”€ 0203
 â”œâ”€ 0204
 â”œâ”€ 0209
 â”œâ”€ 0215
 â””â”€ 0216
```

### Quick Styles Once Family Exists

Likely quick after formula confirmation:

- 0200 half slotted container
- 0204 overlap/slotted variant if formula is close to 0202/0203
- 0209 flange variant if flange depth is parameterized

Moderate:

- 0215 / 0216 if lock/tuck geometry is production-confirmed

### Production Confirmation Required

- any 02xx lock-bottom or self-locking style
- flange/hand-hole allowances
- asymmetric top/bottom flap styles
- local meaning of legacy formulas for older styles

## 3. 03xx - Telescope Boxes

### Family Description

03xx covers telescope-type boxes. These usually consist of more than one piece, such as a separate top and bottom that fit over each other.

Typical examples:

- lid and base boxes
- shoe-box-style telescoping packs
- full telescope containers
- partial telescope containers

### Common Geometry Pattern

The key geometry pattern is multi-blank:

```text
base blank = base panels + base side walls + allowances
lid blank  = lid panels + lid side walls + fit clearance + allowances
```

Some 03xx styles are essentially two related 02xx-style parts. Others are tray/lid structures.

### Common Parameters

Likely parameters:

- base_height_mm
- lid_height_mm
- lid_clearance_mm
- telescope_depth_mm
- lid_overlap_mm
- base_flap_depth_mm
- lid_flap_depth_mm
- board_thickness_mm
- separate lid/base board recipe
- separate lid/base surface profile

### Common Validation Rules

- `L`, `W`, base height, and lid height must be positive
- lid dimensions must include clearance over base
- clearance must be non-negative
- lid/base overlap must be production-feasible
- two-piece output must have valid quantities per FG
- if different materials are used, piece-level recipes must be explicit

### Common Capabilities

Suggested defaults:

```text
width split:       per piece, not globally assumed
length split:      per piece, rarely default
shared upstream:   false by default for true lid/base outputs
rotation:          per piece, depending on grain/layout policy
```

If represented as independent routed pieces, existing multi-piece architecture may help. Shared upstream should be opt-in only after validation because lid and base may not share the same source blank.

### Suggested Base Class

```text
BaseTelescopeStyle
 â”œâ”€ BaseTwoPieceStyle
 â”œâ”€ LidBaseStyle
 â”œâ”€ FullTelescopeStyle
 â””â”€ PartialTelescopeStyle
```

Shared helpers:

- create piece geometry for lid/base
- derive lid clearance
- expose piece-level blank dimensions
- generate parent rollup metadata
- enforce multi-piece capabilities

### Quick Styles Once Family Exists

Only quick if the architecture supports two related blanks:

- simple lid/base tray styles with equal material
- simple full telescope variants with production formula

### Production Confirmation Required

Most 03xx styles require confirmation:

- exact lid/base relationship
- clearance rules
- separate material/surface rules
- whether the style should be generated as multi-piece Engine V2 output

## 4. 04xx - Folder / Tray / Mailer Boxes

### Family Description

04xx covers folder-type boxes and trays. These are usually one-piece blanks where the bottom panel is hinged to form side walls and often a cover/lid. Locking tabs, handles, display panels, and mailer features may be included.

Key Phase K target:

- 0427 hinged-lid / self-locking mailer

Other examples:

- 0409 five-panel wrap
- 0411 / 0413 folder or wrap variants
- 0423 / 0426 / 0427 tray and mailer variants

### Common Geometry Pattern

Unlike 02xx styles, 04xx geometry is usually panel-chain based:

```text
blank_length = sum of panels along folding direction + tuck/lock allowances
blank_width  = product width panel + side wall / wing / lock allowances
```

Some styles are simple wraps. Others are die-cut mailers with locks and double walls.

### Common Parameters

Likely parameters:

- panel_depth_mm
- cover_panel_depth_mm
- fifth_panel_width_mm
- side_wall_height_mm
- side_flap_depth_mm
- tuck_flap_depth_mm
- lock_tab_width_mm
- lock_tab_count
- lock_tab_spacing_mm
- slot_allowance_mm
- corner_relief_mm
- clearance_mm
- board_thickness_mm

### Common Validation Rules

- `L`, `W`, `H` must be positive unless the style is flat-wrap-only
- panel chain must produce positive blank length/width
- lock/tuck params must be positive when required
- board thickness must be positive if used in inside/outside dimension conversion
- style must declare whether it is RM-estimation-only or production-dieline-grade
- public/internet formulas should not be accepted without production validation

### Common Capabilities

Suggested defaults:

```text
width split:       true for simple rectangular single blanks, review for die-cut mailers
length split:      false by default
shared upstream:   true if represented as one blank; review for complex die-cut nesting
rotation:          true for RM/layout if rectangular bounding blank, but grain/print may restrict
```

For CAD-grade die-cut support, capabilities may need to include contour/nesting support later. That is outside the current `StyleResult` scope.

### Suggested Base Class

```text
BaseFolderStyle
 â”œâ”€ BaseWrapStyle
 â”œâ”€ BaseTrayStyle
 â”œâ”€ BaseMailerStyle
 â””â”€ BaseDieCutFolderStyle
```

Example:

```text
BaseFolderStyle
 â”œâ”€ 0409
 â”œâ”€ 0411
 â”œâ”€ 0413
 â””â”€ 0427
```

Shared helpers:

- panel-chain blank builder
- tuck/lock parameter validation
- bounding rectangle output
- optional die-cut metadata
- default folder/tray capabilities

### Quick Styles Once Family Exists

Likely quick:

- 0409 five-panel wrap, after formula confirmation
- simple wrap/folder styles with panel-chain formulas

Moderate/high:

- 0427
- self-locking trays
- mailers with double walls/locks

### Production Confirmation Required

Required for most 04xx die-cut styles:

- 0427
- 0426
- locking tray variants
- handle/display variants
- any style where true die-cut contour affects area or layout

## 5. 07xx - Ready-Glued Boxes

### Family Description

07xx covers ready-glued cases. These are usually one-piece boxes that are pre-glued, shipped flat, and erected quickly by simple assembly. They include auto-bottom, crash-lock, folding-bottom, and similar fast-erect styles.

Common examples:

- 0700 open container with automatic lock bottom
- 0713 crash-lock / auto-bottom styles
- 0715 beverage-style ready-glued packs
- 0759 collapsible tray with hinged lid

### Common Geometry Pattern

Geometry is a hybrid:

- body panels may resemble 02xx slotted wrap geometry
- bottom panels include pre-glued automatic lock geometry
- top may be open, tuck top, hinged lid, or flap closure
- glue zones and fold sequence matter

Possible abstract formula:

```text
blank_length = body_wrap + glue_joint + lock/glue panel allowances
blank_width  = H + top_closure_depth + bottom_auto_lock_depth
```

But exact formulas vary heavily by sub-style.

### Common Parameters

Likely parameters:

- glue_lap_mm
- auto_bottom_panel_depth_mm
- crash_lock_depth_mm
- bottom_lock_allowance_mm
- tuck_top_depth_mm
- top_flap_depth_mm
- glue_zone_width_mm
- diagonal_fold_allowance_mm
- board_thickness_mm
- clearance_mm

### Common Validation Rules

- `L`, `W`, `H` must be positive
- glue zones must be positive
- auto-bottom/lock dimensions must be production-confirmed
- board thickness must be compatible with folding/erection
- too-thick board may be blocked or warned for certain styles
- top closure parameters must match the selected sub-style
- pre-glued styles must declare glue operation metadata

### Common Capabilities

Suggested defaults:

```text
width split:       review per style
length split:      false by default
shared upstream:   true if one blank; review if glue-stage SFGs are modeled
rotation:          usually true for RM bounding blank, but print/glue/grain may restrict
```

Ready-glued styles may need additional operation metadata later for glue stages, but that should remain style metadata consumed generically.

### Suggested Base Class

```text
BaseReadyGluedStyle
 â”œâ”€ BaseAutoBottomStyle
 â”œâ”€ BaseCrashLockStyle
 â”œâ”€ BaseFoldingBottomStyle
 â””â”€ BaseReadyGluedMailerStyle
```

Shared helpers:

- body wrap reuse from slotted family where applicable
- glue zone validation
- auto-bottom panel validation
- glue operation metadata
- bounding blank output

### Quick Styles Once Family Exists

Potentially quick only after one confirmed auto-bottom formula:

- simple 0700-style open top with automatic lock bottom

Moderate/high:

- 0713 crash-lock styles
- 0715 beverage packs
- 0759 hinged-lid ready-glued tray

### Production Confirmation Required

Most 07xx styles require confirmation because ready-glued bottom geometry varies by plant/tooling:

- automatic lock bottom formulas
- glue zone widths
- fold sequence
- board thickness limits
- operation routing for pre-gluing

## Cross-Family Capability Defaults

| Family | Width Split | Length Split | Shared Upstream | Rotation |
|---|---:|---:|---:|---:|
| 01xx Rolls/Sheets | conditional | conditional | conditional | conditional |
| 02xx Slotted | yes | no | yes | yes |
| 03xx Telescope | per piece | per piece | no by default | per piece |
| 04xx Folder/Tray/Mailer | conditional | no by default | yes if one blank | conditional |
| 07xx Ready-Glued | conditional | no by default | conditional | conditional |

The family class should provide defaults, but each style must be able to override capabilities.

## Recommended Base Class Contract

Base class output should still be a normal `StyleResult`.

Suggested internal contract:

```text
class BaseStyle:
    style_code
    style_name
    family
    contract_version

    def compute(spec_input, plant_profile) -> StyleResult | None
    def geometry(spec_input) -> GeometryInputs
    def params(spec_input) -> dict
    def validate(inputs, params) -> None
    def dimensions(inputs, params, plant_profile) -> BlankDimensions
    def score_lines(inputs, params, dimensions) -> list[dict]
    def fold_lines(inputs, params, dimensions) -> list[dict]
    def capabilities(inputs, params) -> dict
    def metadata(inputs, params, dimensions) -> dict
```

Recommended data objects:

```text
GeometryInputs:
  box_length_mm
  box_width_mm
  box_height_mm
  glue_flap_override_mm
  plant_code

BlankDimensions:
  blank_length_mm
  blank_width_mm
  seam_mm
  flap_mm
  pieces optional
```

## Roadmap

### Step 1: Refactor Existing 02xx Into BaseSlottedStyle

Do this before adding many more 02xx styles.

Targets:

- 0201
- 0202
- 0203

Expected benefit:

- one seam implementation
- one body-panel wrap implementation
- one default capability block
- less duplicate score/fold metadata

### Step 2: Add Quick 02xx Styles

Only after BaseSlottedStyle exists and formulas are confirmed.

Likely candidates:

- 0200
- 0204
- 0209

### Step 3: Add BaseFolderStyle Before 0409/0427

This should happen before implementing 0427.

Start with:

- panel-chain builder
- bounding rectangle result
- style params for tuck/lock/clearance

Then implement:

- 0409 if business priority is simple wrap/folder
- 0427 if business priority is e-commerce/mailer packaging and production data is ready

### Step 4: Design Two-Piece Support For 03xx

Do not implement true 03xx styles as a one-blank shortcut unless production explicitly approves approximation.

Use or extend existing multi-piece concepts carefully.

### Step 5: Design 01xx And 07xx As Separate Family Contracts

01xx needs non-box geometry and UoM clarity.

07xx needs glue/auto-bottom operation metadata and confirmed production formulas.

## Quick Implementation Candidates After Family Base Exists

Likely quick:

- 0200
- 0204
- 0209
- 0110 sheet
- 0409, if formula is confirmed

Moderate:

- 0215
- 0216
- 0700
- simple 030x lid/base after two-piece support

High risk:

- 0427
- 0713 crash-lock
- 0759
- 03xx family without clear lid/base model

## Styles Requiring Production Formula Confirmation

Always require production confirmation:

- 0427
- most 04xx locking tray/mailer styles
- 03xx telescope styles
- 07xx ready-glued/crash-lock styles
- any style with locks, handles, relief cuts, die-cut contours, auto-bottoms, or glue zones

May be implemented quickly after basic confirmation:

- simple 02xx slotted variants
- simple 01xx sheets/pads
- simple 0409-style wraps

## Sources Checked

- FEFCO code overview: https://www.fefco.org/technical-information/fefco-code
- FEFCO family overview: https://www.packaging-warehouse.com/en/magazine/fefco-types-explained-what-is-it-what-does-it-look-like-91
- Kite Packaging FEFCO style guide: https://www.kitepackaging.co.uk/mcp/box-style-guide/
- THIMM FEFCO overview: https://www.thimm.com/en/packaging/fefco/
- Diamond Box 0700 ready-glued cases: https://www.diamond-box.co.uk/packaging-design/fefco-codes/fefco-codes-0700/
- EasyPackMaker 0700: https://easypackmaker.com/chooser/final?templateIndex=fefco_0700
- Pakd FEFCO families: https://pakd.com/pakd-explains/pakd-explains-fefco-cardboard-box-and-packaging-styles/
- Superbox 0700 ready-glued cases: https://superbox.fi/en/1489-0700-ready-glued-cases



