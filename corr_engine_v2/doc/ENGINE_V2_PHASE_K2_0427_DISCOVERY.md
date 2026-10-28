# Engine V2 Phase K2: FEFCO 0427 Business And Technical Discovery

## Purpose

This document is a discovery note only.

Do not implement FEFCO 0427 until production confirms the exact dieline convention, blank dimensions, and required style parameters.

## 1. Box Structure Explanation

FEFCO 0427 is a folder-type, die-cut, one-piece corrugated box with a hinged lid and self-locking features.

Common industry names include:

- hinged-lid box
- tray with hinged lid
- roll-end tuck-top mailer
- tuck-front mailer
- self-locking mailer box

Typical structural features:

- one-piece die-cut blank
- base/tray section
- hinged lid connected to the base
- side walls that roll/fold into locking walls
- front tuck flap or lid tuck feature
- locking tabs or slots
- double-wall or overlapped wall areas depending on the exact dieline
- no glue or tape required for many standard versions

The key difference from 020x styles is that 0427 is not a simple slotted carton. It is a die-cut folder/tray style, so its usable blank formula depends on lock/tuck/wall conventions, board thickness, and the exact dieline standard used by the plant.

## 2. Real-World Usage Examples

0427 has strong business value because it is widely used in e-commerce, retail, and presentation packaging.

Typical uses:

- postal mailer boxes
- e-commerce shipping boxes
- subscription boxes
- sample packs
- branded unboxing packaging
- cosmetics packaging
- clothing and accessories packaging
- food gift boxes
- small electronics or accessories
- lightweight to medium-weight retail shipments

Business value:

- premium opening experience
- good printable/branding surfaces
- compact flat-pack storage
- self-locking assembly
- stronger side/front walls than simple folding styles
- common customer-facing packaging style

## 3. Required Geometry Inputs

Minimum finished-product inputs:

- `L`: internal or finished length
- `W`: internal or finished width
- `H`: internal or finished height/depth

Additional technical inputs likely required:

- board thickness or flute thickness
- dimension basis: inside dimensions vs outside dimensions
- side wall fold/roll depth
- lid panel depth
- front tuck flap depth
- lock tab width
- lock tab count
- lock tab spacing/distance
- side tuck/wing depth
- clearance/tolerance
- slot allowance
- crease allowance
- corner relief allowance

Some public dieline generators expose only `L`, `W`, `H`, board type/thickness, and a few lock/flap controls. Production may use a richer internal convention.

## 4. Expected Style Parameters

The first implementation should avoid guessing defaults. Candidate style parameters are:

```text
board_thickness_mm
tuck_flap_depth_mm
side_wall_fold_depth_mm
lid_side_flap_depth_mm
front_lock_flap_depth_mm
lock_tab_width_mm
lock_tab_count
lock_tab_spacing_mm
slot_allowance_mm
corner_relief_mm
clearance_mm
```

Recommended first-pass parameter strategy:

- keep `L`, `W`, and `H` as normal spec dimensions
- use style parameters only for 0427-specific dieline features
- make only truly required values mandatory
- prefer production-confirmed defaults over generic internet formulas
- include board thickness only if the formula uses internal-to-external compensation

Possible minimal parameter set if production provides a standard plant template:

```text
tuck_flap_depth_mm
side_wall_fold_depth_mm
lock_tab_width_mm
clearance_mm
```

Possible no-parameter approach:

- only acceptable if production confirms a fixed formula based entirely on `L/W/H` and board thickness/flute.
- not recommended yet because 0427 lock/tuck conventions vary by source and tooling.

## 5. Blank Formula Candidates

These are candidates only. They are not approved formulas.

### Candidate A: Bounding-Rectangle Approximation

Treat 0427 as one rectangular blank using a bounding box:

```text
blank_length = base_length_panels + lid_panels + tuck/lock allowances
blank_width  = base_width_panels + side_wall/fold allowances
```

Example abstract form:

```text
blank_length = L + H + L + tuck_flap_depth + front_lock_flap_depth + clearance
blank_width  = W + (2 * side_wall_fold_depth) + (2 * lid_side_flap_depth)
```

Risk:

- likely too simplistic for accurate dieline cutting
- may be acceptable for RM estimation only if production validates the bounding blank size

### Candidate B: Plant Template Formula

Use the plant's actual 0427 template convention and calculate the rectangular bounding blank from its panels.

Expected structure:

```text
blank_length = sum of confirmed longitudinal panels and allowances
blank_width  = sum of confirmed transverse panels and allowances
```

This is the preferred option.

It requires one approved dieline or CAD sample with labeled panels.

### Candidate C: Parametric Generator Alignment

Align with a known parametric dieline generator input model.

Public generator examples show inputs such as:

- length
- width
- height/depth
- material thickness
- number of locks
- lock width
- lock distance
- flap width
- double lock option
- inside/outside dimension basis

This could be useful for parameter design, but it should not be used blindly because generator conventions may not match the plant's tooling.

### Candidate D: RM-Only Approximation First

For early quotation/RM estimation, support only bounding blank dimensions entered or derived externally.

Possible behavior:

- users choose 0427
- users provide confirmed blank length/width or selected dieline template output
- Engine V2 uses the style only for classification and downstream RM/BoM

Risk:

- less automated than other style handlers
- may conflict with the Phase K goal of style-specific geometry handlers unless positioned as a temporary validation mode

## 6. Risks And Assumptions

Risks:

- 0427 has no single safe universal formula from public descriptions.
- Different suppliers and generators use different lock/tuck/flap conventions.
- Board thickness matters if users enter inside dimensions.
- Die-cut tooling may add clearances, relief cuts, slot offsets, and lock geometry not captured by simple panel math.
- A simple bounding rectangle may estimate RM area but not produce a correct cut/crease design.
- If the blank width/length is wrong, RM, costing, layout, and BoM quantities will all be wrong.
- Overloading existing 020x flap/seam concepts would be misleading.
- Actual BoM materialization can stay generic only if 0427 returns a normalized single-blank `StyleResult`.

Assumptions for initial discovery:

- 0427 will be modeled as one FG style producing one rectangular blank area for RM and BoM consumption.
- Engine V2 will not generate a manufacturing-ready CAD/DXF dieline in the first version.
- The first supported handler only needs correct blank length/width, score/fold metadata, and capabilities.
- Any die-cut lock details should live in style metadata/parameters, not in StageGraph/RM/materialization internals.

## 7. Sample Dieline / Blank Examples Available

Publicly available examples are mostly parametric templates and configurators, not fixed production formulas.

Useful public references for discovery:

- Pacdora provides a customizable FEFCO 0427 template for manufacturing export and describes it as widely used in e-commerce packaging.
- Packaging Warehouse describes 0427 as a hinged-lid box configurable by dimensions and other specifications.
- PackMyMan's folding box generator exposes 0427-style parameters including length, width, height, number of locks, lock width, lock distance, and flap width.
- EasyPackMaker identifies 0427 under group 04 folder-type boxes/trays and exposes length, width, depth, board type, thickness, and inside/outside dimension basis.
- BoxFactory describes 0427 as a die-cut design where base tabs fold and lock, creating an area for lid wings to tuck into.
- BoxLab identifies 0427 as a parametric tray with hinged lid.
- Boxshot exposes inputs for length, width, height, paper thickness, inside/outside dimensions, and double lock.
- CBS Packaging describes 0427 as self-sealing with locking tabs into double overlapped sidewalls.

Production data still required:

- one actual approved 0427 dieline
- one actual cutting sheet or CAD export
- one sample with finished `L/W/H`
- board/flute/thickness
- expected blank length
- expected blank width
- lock/tuck/flap dimensions
- confirmation of whether dimensions are internal or external

## 8. Current Engine V2 Architecture Fit

Current Engine V2 can probably support first-stage 0427 unchanged if 0427 is represented as:

- one style handler
- one rectangular blank result
- one `StyleResult`
- style-specific parameters in `corr.engine.style.param`
- values mapped into `spec_input.style_params`

Expected unchanged areas:

- `STYLE_REGISTRY`
- style handler autoload
- dynamic style parameter UI
- preview geometry display
- RM breakdown
- layout options
- BoM preview
- actual BoM creation
- StageGraph
- RM resolver
- materialization writer
- multi-piece materialization
- shared-upstream logic

Potential architecture gaps:

- `StyleResult` currently supports score/fold metadata, but not a full die-cut contour.
- If users need a true dieline output, that is a separate design problem.
- If 0427 requires multiple pieces or optional inserts, current single-blank support may not be enough.
- If inside/outside dimension conversion is required, board thickness must be captured cleanly.

Recommendation:

Proceed with 0427 only after production provides one confirmed blank formula or template. The first implementation should target accurate RM/preview/BoM geometry, not CAD-grade dieline generation.

## Sources Checked

- FEFCO code overview: https://www.fefco.org/technical-information/fefco-code
- Pacdora FEFCO 0427 template: https://www.pacdora.com/tools/fefco-0427
- Packaging Warehouse FEFCO 0427 hinged-lid box: https://www.packaging-warehouse.com/en/category/fefco-0427-hinged-lid-box-78
- PackMyMan folding box generator: https://www.packmyman.com/boxes/folding-box
- EasyPackMaker FEFCO 0427: https://easypackmaker.com/chooser/final?templateIndex=fefco_0427
- BoxFactory FEFCO 0427: https://www.boxfactory.co.uk/fefco-box-designs/0427
- BoxLab FEFCO 0427: https://boxlab.io/models/corrugated/04/0427
- Boxshot FEFCO 0427 dieline editor: https://dielines.boxshot.com/make/fefco-0427/
- CBS Packaging FEFCO 0427: https://cbspackaging.co.uk/fefco/0427-boxes/



