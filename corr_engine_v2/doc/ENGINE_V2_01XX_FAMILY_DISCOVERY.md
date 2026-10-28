# Engine V2 01xx Family Discovery

## Purpose

Discovery and architecture note for the FEFCO 01xx family before implementation.

Scope:

- 0100 roll products
- 0110 corrugated sheet products
- fit with current Engine V2
- reusable components
- required style parameters and family contracts
- recommended implementation order

No coding is included in this document.

## 01xx Family Summary

FEFCO 01xx covers commercial rolls and sheets. Industry references describe this family as the basic corrugated material form:

- 0100: commercial rolls of corrugated board
- 0110: corrugated sheet cuts / flat sheets

These are not erected box styles. They are sellable corrugated board products and can also be intermediate SFG outputs used by later conversion.

This matters because Engine V2 currently assumes many specs are finished boxes with:

- box length
- box width
- box height
- style geometry
- blank dimensions
- paper RM consumption based on blank area

For 01xx, the "blank" may be the finished product itself.

## 1. 0100 Roll Products

### Manufacturing Flow

Typical manufacturing flow:

```text
paper reels -> corrugator / single facer / board formation -> slit to roll width -> rewind/roll -> finished roll or sellable SFG
```

Possible process variants:

- 2-ply single-face roll
- 3-ply corrugated roll
- roll from continuous corrugator output
- roll converted into sheets later
- roll sold directly to customer

0100 should be treated as a roll/SFG product, not as a box.

### Geometry Model

Core geometry:

```text
roll_width_mm
roll_length_m
```

Optional geometry:

```text
roll_diameter_mm
core_diameter_mm
core_width_mm
area_m2
```

Primary consumption area:

```text
area_m2 = roll_width_mm * roll_length_m / 1000
```

Because:

```text
roll_width_mm / 1000 = roll_width_m
area_m2 = roll_width_m * roll_length_m
```

For a roll product, there is no meaningful:

- box height
- seam/glue flap
- flap depth
- blank length in box-conversion sense

### UoM Model

0100 needs an explicit UoM model because roll products may be sold and consumed differently from boxes.

Possible sale/manufacturing UoM:

- roll
- meter
- square meter
- kg

Recommended initial Engine V2 convention:

```text
primary production qty = roll count
style params define roll_width_mm and roll_length_m
derived area_m2_per_roll = roll_width_mm * roll_length_m / 1000
derived kg_per_roll from board recipe GSM/TUF/area
```

Alternative:

```text
primary qty = linear meters
roll_width_mm fixed by spec
area and kg scale by ordered meters
```

This is more flexible for partial rolls but more disruptive to the existing BoM quantity model.

### RM Consumption Model

RM consumption should be area-based:

```text
paper_kg = layer_effective_gsm * area_m2 / 1000
```

For 0100:

```text
m2_per_fg = area_m2_per_roll
kg_per_fg = sum(layer_gsm_adjusted * area_m2_per_roll / 1000)
```

If the FG UoM is "roll":

```text
qty_per_fg = kg per roll
```

If the FG UoM is "m":

```text
qty_per_fg = kg per meter
```

Roll products may also need:

- core material
- wrapping/packing material
- roll label
- palletization

Those should be separate RM/packaging rules, not part of the style geometry.

### Sellable SFG Model

0100 can be:

- finished sellable product
- semi-finished roll used by another converting process
- stockable intermediate material

Recommended model:

```text
product type: SFG or FG depending on spec/product
stock UoM: roll, m, m2, or kg depending on business setup
style family: roll
geometry: roll width + roll length or area basis
```

Engine V2 should allow 0100 to produce a BoM/materialization plan without requiring downstream box conversion.

### 0100 Risks

- UoM ambiguity is the main risk.
- Existing quantity planner assumes boxes/sheets in many places.
- Roll length, roll width, and roll count must be clearly separated.
- Product UoM and BoM UoM must match Odoo product setup.
- Partial-roll production may need a second phase.

## 2. 0110 Corrugated Sheet Products

### Manufacturing Flow

Typical manufacturing flow:

```text
paper reels -> corrugator / board formation -> slit/score if needed -> cut off to sheet length -> stack -> finished sheet or sellable SFG
```

FEFCO production references describe corrugator output being slit/scored and then cut to exact sheet lengths before stacking.

0110 is a direct fit for Engine V2 because it is a rectangular sheet product.

### Geometry Model

Core geometry:

```text
sheet_length_mm
sheet_width_mm
```

Derived:

```text
area_m2_per_sheet = sheet_length_mm * sheet_width_mm / 1_000_000
```

For 0110:

```text
blank_length_mm = sheet_length_mm
blank_width_mm  = sheet_width_mm
```

No box dimensions are needed.

Recommended interpretation:

- the 0110 "blank" is the finished sheet
- `m2_per_box` concept should become `m2_per_fg` internally over time, but existing metric can be reused initially if named carefully in UI/context

### Sheet Dimensions

Required:

- sheet length
- sheet width

Optional:

- trim margin
- grain/flute direction
- stack quantity
- pallet quantity
- squareness/tolerance

Validation:

- length > 0
- width > 0
- length/width within plant machine limits
- grain/flute direction compatible with plant/layout if enforced

### Ply Structures

0110 can use the existing board/stack model:

- 2-ply
- 3-ply
- 5-ply
- 7-ply if supported by board recipe/routing

Existing Engine V2 stack lines already describe:

- liner
- medium
- inner liner
- center liner
- flute type
- GSM
- BF
- color
- material product

0110 should reuse that stack structure. It should not introduce a separate sheet-material model.

### Sellable SFG Model

0110 is a strong candidate for immediate business value because sheets are often:

- sold directly to customers
- used as pallet separators
- used as covers
- used as cushioning/protection
- transferred to another converting plant
- consumed by later box-making operations

Recommended model:

```text
product type: SFG or FG depending on product template
stock UoM: sheet / unit, or m2 if business requires
geometry: sheet length + sheet width
area: derived from sheet dimensions
```

Initial implementation should likely support:

```text
order_qty = number of sheets
qty_per_fg = paper kg per sheet
```

Later expansion can support:

```text
order_qty = m2
sheet count derived from area
```

### 0110 Risks

- The current UI labels and metrics may say "box" in places.
- The spec form currently uses box length/width/height. 0110 needs a clean sheet-dimension UI.
- Finished product UoM may be sheet, unit, or m2.
- Quantity planner and layout should not try to place 0110 blanks into a parent sheet if the sheet is already the finished output.

## 3. How 01xx Fits Into Current Engine V2 Architecture

01xx can fit the current architecture if treated as a style family with non-box geometry.

Current reusable path:

```text
corr.engine.style
-> style handler
-> StyleResult
-> metrics
-> RM resolver
-> costing
-> preview
-> BoM/materialization
```

Needed conceptual change:

```text
box style -> product geometry style
box blank -> finished output blank/area
m2_per_box -> m2_per_fg
```

The current `StyleResult` can support 0110 immediately:

```text
blank_length_mm = sheet_length_mm
blank_width_mm  = sheet_width_mm
```

For 0100, `StyleResult` may need additional roll metadata:

```text
roll_width_mm
roll_length_m
area_m2_per_roll
uom_basis
```

This can be added in metadata first, while still populating area metrics from a normalized equivalent rectangle if needed.

## 4. Existing Components That Can Be Reused

### RM Resolver

Reusable for both 0110 and 0100 if area is available.

For 0110:

```text
m2_per_fg = sheet_length * sheet_width / 1_000_000
```

For 0100:

```text
m2_per_fg = roll_width_mm * roll_length_m / 1000
```

The RM resolver already uses:

```text
qty_kg = gsm * m2_per_box / 1000
```

This logic can be reused, but naming should eventually shift to `m2_per_fg`.

### Quantity Planner

Partially reusable.

For 0110:

- may not need layout optimization if finished sheet is direct output
- can still validate plant sheet size/machine constraints
- can plan number of output sheets

For 0100:

- current sheet-based planning is not enough
- roll count / linear meter planning may need new logic

### Costing

Reusable if costing consumes RM requirements and metrics area.

Needs care:

- cost per sheet for 0110
- cost per roll or per meter for 0100
- UoM labels in preview/cost summary

### Materialization

Reusable for 0110 with minimal conceptual changes:

- product
- BoM
- material components
- routing operations

For 0100:

- reusable if product UoM and BoM quantity are roll-based
- needs review if production qty is meter or m2
- may need roll-specific packaging/core components later

### Routing

Reusable at profile level.

Potential 0110 route:

```text
corrugation -> slitting/cutoff -> stacking
```

Potential 0100 route:

```text
corrugation/single facer -> slitting -> rewinding -> packing
```

Routing profiles should remain configurable; style should provide defaults/capabilities, not hardcode operations.

## 5. Required New Style Parameters

### 0110 Parameters

Recommended:

```text
sheet_length_mm
sheet_width_mm
```

Optional:

```text
grain_direction
trim_margin_mm
stack_qty
```

Question:

- Should `sheet_length_mm` and `sheet_width_mm` be style params, or should the main spec form support family-specific geometry fields?

Recommendation:

- For first implementation, use style params to avoid hardcoding 01xx fields into the main spec.
- Later, introduce family-aware UI sections if 01xx becomes heavily used.

### 0100 Parameters

Recommended:

```text
roll_width_mm
roll_length_m
uom_basis
```

Optional:

```text
roll_diameter_mm
core_diameter_mm
core_product_id
core_weight_kg
wrap_required
trim_margin_mm
grain_direction
```

`uom_basis` candidates:

```text
roll
meter
m2
kg
```

For first implementation, keep this narrow:

```text
uom_basis = roll
roll_width_mm required
roll_length_m required
```

## 6. Required New Style Family Contracts

Introduce `BaseRollSheetStyle` or equivalent before implementation.

### Required Contract Concepts

```text
family = roll / sheet
is_box_style = false
requires_box_height = false
uses_seam = false
uses_flaps = false
output_uom_basis = sheet / roll / meter / m2 / kg
area_m2_per_fg
```

### Proposed Result Metadata

For 0110:

```python
{
    "style_code": "0110",
    "family": "sheet",
    "blank_formula": "sheet_length*sheet_width",
    "sheet_length_mm": 1200,
    "sheet_width_mm": 1000,
    "area_m2_per_fg": 1.2,
    "uom_basis": "sheet",
}
```

For 0100:

```python
{
    "style_code": "0100",
    "family": "roll",
    "blank_formula": "roll_width*roll_length",
    "roll_width_mm": 1200,
    "roll_length_m": 100,
    "area_m2_per_fg": 120,
    "uom_basis": "roll",
}
```

### Capability Defaults

0110:

```text
width split: false
length split: false
shared upstream: true
rotation: conditional by grain/flute direction
```

0100:

```text
width split: true only if slitting from wider roll/board is modeled
length split: true only if cut-to-length is modeled
shared upstream: conditional
rotation: false
```

### UI Contract

The Engine V2 Spec UI needs family-aware display eventually:

For 0110:

```text
Sheet Length
Sheet Width
```

For 0100:

```text
Roll Width
Roll Length
UoM Basis
```

Avoid showing box height/seam/flap fields as required for 01xx.

## 7. Recommended Implementation Order: 0110 vs 0100

Recommended order:

```text
1. 0110 Corrugated Sheet Products
2. 0100 Roll Products
```

### Why 0110 First

0110 is lower risk and closer to current Engine V2:

- rectangular product
- direct area calculation
- reuses existing RM area logic
- reuses board stack/ply structure
- can use existing preview/RM/cost/BoM flow
- product quantity can be sheet/unit
- no roll UoM complexity

0110 should be the first 01xx implementation and the proving case for `BaseRollSheetStyle`.

### Why 0100 Second

0100 has high business value but needs more architecture decisions:

- roll vs meter vs m2 UoM
- roll length handling
- roll/core/packing materials
- possible rewinding operation
- partial roll support
- stock valuation and product UoM setup

Implementing 0100 before settling UoM rules risks contaminating shared quantity/materialization logic.

## Recommended Next Steps

Before coding 0110:

1. Confirm whether customers sell 0110 by sheet, m2, or kg.
2. Confirm required sheet dimensions and plant limits.
3. Confirm whether sheet dimensions should be style params or family-specific spec UI fields.
4. Confirm route profile for corrugated sheet production.
5. Confirm BoM quantity basis for one sheet.

Before coding 0100:

1. Confirm roll sales UoM.
2. Confirm roll production UoM.
3. Confirm whether partial roll production is required.
4. Confirm roll core and packaging components.
5. Confirm whether roll products are 2-ply only or can be 3/5-ply.

## Sources Checked

- FEFCO code overview: https://www.fefco.org/technical-information/fefco-code
- Packaging Warehouse FEFCO family overview: https://www.packaging-warehouse.com/en/magazine/fefco-types-explained-what-is-it-what-does-it-look-like-91
- THIMM FEFCO overview: https://www.thimm.com/en/packaging/fefco/
- Diamond Box 0100 rolls and sheets: https://www.diamond-box.co.uk/packaging-design/fefco-codes/fefco-codes-0100/
- Diamond Box 0110 commercial sheets: https://www.diamond-box.co.uk/packaging-design/fefco-codes/fefco-codes-0100/fefco-0110/
- Labelprint24 0110 corrugated sheets: https://www.labelprint24.com/en/products/corrugated-cardboard-sheets-989
- FEFCO production process: https://www.fefco.org/technical-information/production-process
- FEFCO production process details: https://www.fefco.org/technical-information/production-process/more-about-production-process



