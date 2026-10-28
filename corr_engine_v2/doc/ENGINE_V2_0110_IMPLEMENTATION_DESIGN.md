# Engine V2 0110 Corrugated Sheet Implementation Design

## Purpose

Design note for FEFCO 0110 Corrugated Sheet after UI review.

0110 is a sheet product. Sheet length and sheet width are product geometry, not style-specific parameters.

## Approved Direction

Implement 0110 as:

- corrugated sheet, not roll
- one finished sheet per FG unit
- no 0110-specific style parameters
- no raw JSON
- no required box height
- no required seam/glue flap
- no required flap/overlap
- reuse existing stack/RM/costing/preview/BoM flow

Dynamic style parameters remain reserved for true style-specific values.

Examples:

- 0202 `overlap_mm` is a style parameter.
- 0110 length/width are product geometry and reuse the main geometry fields.

## 1. Geometry

Use existing main geometry fields:

```text
box_length_mm -> sheet_length_mm
box_width_mm  -> sheet_width_mm
```

Do not use:

```text
box_height_mm
glue_flap_override_mm
flap_add_mm
overlap_mm
```

Geometry formulas:

```text
blank_length_mm = box_length_mm
blank_width_mm  = box_width_mm
area_m2_per_fg  = box_length_mm * box_width_mm / 1_000_000
```

Interpretation:

- the finished product is the sheet
- the Engine V2 blank is the finished sheet
- there is no box body wrap
- there are no flaps
- there is no seam/glue lap

Recommended metadata:

```python
{
    "style_code": "0110",
    "style_name": "Corrugated Sheet",
    "family": "sheet",
    "blank_formula": "sheet_length*sheet_width",
    "sheet_length_mm": box_length_mm,
    "sheet_width_mm": box_width_mm,
    "area_m2_per_fg": area_m2_per_fg,
    "uom_basis": "sheet",
}
```

## 2. UI Behavior

When user selects:

```text
Style = 0110
```

The main geometry section should show sheet terminology:

```text
Finished Box Size -> Sheet Size
Box Length        -> Sheet Length
Box Width         -> Sheet Width
```

The UI should hide or make optional:

```text
Box Height
```

The Style Parameters section should show:

```text
No extra parameters required.
```

Required user input:

```text
Sheet Length
Sheet Width
```

Not required:

```text
Box Height
Seam / Glue Flap
Flap settings
Overlap settings
```

## 3. Style Params Vs Product Geometry

0110 should not define these as style params:

```text
sheet_length_mm
sheet_width_mm
```

They are product geometry and are represented by:

```text
box_length_mm
box_width_mm
```

Reason:

- avoids duplicating length/width in Style Parameters
- keeps the UI simpler for users
- keeps dynamic style params focused on true style-specific values
- lets existing adapter geometry mapping continue to work

Future family-specific UI may rename underlying model fields or introduce sheet-specific aliases, but that is not required for first 0110 support.

## 4. RM Calculation Example

Example:

```text
box_length_mm = 1200
box_width_mm  = 1000
```

Shown to user as:

```text
Sheet Length = 1200
Sheet Width  = 1000
```

Area:

```text
area_m2_per_fg = 1200 * 1000 / 1_000_000
area_m2_per_fg = 1.2 m2
```

For a 180 GSM liner:

```text
kg_per_sheet = area_m2_per_fg * gsm / 1000
kg_per_sheet = 1.2 * 180 / 1000
kg_per_sheet = 0.216 kg
```

For a 120 GSM medium:

```text
kg_per_sheet = 1.2 * 120 / 1000
kg_per_sheet = 0.144 kg
```

Existing RM resolver logic is reusable because it already calculates paper kg from area and GSM.

## 5. Routing

Expected route:

```text
corrugation / board formation -> slitting/cutoff -> stacking
```

Possible routing stages:

- corrugation or 2-ply formation
- board formation
- slitting / cutoff
- stacking
- finished sheet / packing

Routing should remain profile-driven. The style handler should not hardcode operations.

## 6. BoM Behavior

First version BoM convention:

```text
product_qty = 1 sheet
```

RM quantities:

```text
qty_per_fg = paper kg per sheet
```

Expected support:

- 2-ply
- 3-ply
- 5-ply
- 7-ply, if existing stack/routing setup supports it

BoM behavior should reuse existing materialization:

- one finished product or SFG sheet product
- paper RM lines based on stack layers
- quantities based on `area_m2_per_fg`
- no box-specific seam/wire/glue consumable required by the style

## 7. Validation

Required validation:

```text
box_length_mm > 0  # shown as Sheet Length
box_width_mm > 0   # shown as Sheet Width
```

Not required:

```text
box_height_mm
seam_mm
glue_flap_override_mm
flap_add_mm
overlap_mm
style_params
```

Suggested clear messages:

```text
FEFCO 0110 requires Sheet Length > 0.
FEFCO 0110 requires Sheet Width > 0.
```

## 8. Tests

### Style Handler Test

Use main geometry:

```text
box_length_mm = 1200
box_width_mm  = 1000
box_height_mm = 0
```

Expected:

```text
blank_length_mm = 1200
blank_width_mm  = 1000
area_m2_per_fg  = 1.2
```

Assert:

- `StyleResult` returned
- `seam_mm = 0`
- `flap_mm = 0`
- metadata has `style_code = 0110`
- metadata has `family = sheet`
- metadata has `uom_basis = sheet`
- no style params are required

### Style Param / UI Test

Style master should include active 0110.

0110 should have no active style params.

Engine V2 Spec UI should:

- show Sheet Size for 0110
- show Sheet Length and Sheet Width labels
- hide Box Height for 0110
- show "No extra parameters required" in Style Parameters

### Preview Geometry Test

Preview should expose:

```text
blank_length_mm = 1200
blank_width_mm  = 1000
area_m2_per_fg  = 1.2
```

Where existing payload uses:

```text
m2_per_box
```

Expected value remains:

```text
1.2
```

### RM Breakdown Test

For 180 GSM liner:

```text
0.216 kg per sheet
```

For 120 GSM medium:

```text
0.144 kg per sheet
```

Assert RM quantities use sheet area, not box height or style params.

### BoM Preview Test

BoM preview should:

- show style 0110
- show material lines
- use product quantity of 1 sheet for per-FG basis
- carry RM quantities per sheet

### Actual BoM Smoke Test

Actual BoM creation smoke should verify:

- BoM can be generated for a 0110 spec
- component lines are created
- component quantities match per-sheet RM
- no box height/seam/flap validation blocks BoM creation

### Regression Tests

Re-run:

- 0201 regression
- 0202 regression
- 0203 regression

Confirm:

- 0201 dimensions unchanged
- 0202 still requires `overlap_mm`
- 0203 dimensions unchanged
- no 0110 logic leaks into shared downstream modules

## Implementation Guardrails

Do not add 0110-specific logic to:

- StageGraph
- RM resolver
- materialization writer
- multi-piece materialization
- shared-upstream logic

Preferred implementation path:

1. Add/update style metadata for 0110.
2. Ensure 0110 has no style params.
3. Add/update `style_0110.py`.
4. Register through normal style autoload.
5. Ensure handler returns a normal `StyleResult`.
6. Add focused tests.
7. Validate UI/spec/preview/RM/BoM.



