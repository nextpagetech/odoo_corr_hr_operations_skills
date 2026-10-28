# Engine V2 Custom Diecut Sheet Discovery

## Purpose

Discovery and design note for a custom diecut-layout style before implementation.

No coding is included in this document.

## 1. Style Identity

Style code:

```text
DIECUT_SHEET
```

Display name:

```text
Custom Diecut Sheet
```

Family:

```text
diecut_sheet
```

Reason:

- The style is defined by manually entered sheet geometry and diecut yield.
- It is not a standard FEFCO box formula.
- It is not multi-piece logic.
- It should remain stack/routing compatible with normal Engine V2 flows.

## 2. Business Meaning

Custom Diecut Sheet is for products where the user already knows the manufacturing sheet size and the number of finished goods produced from each sheet.

Business interpretation:

```text
one board/sheet -> multiple same FG blanks
```

Example:

```text
Diecut UPS = 2
1 sheet produces 2 FG blanks
```

This is different from multi-piece manufacturing:

- Diecut UPS means one sheet produces multiple identical FG units.
- Multi-piece means different pieces merge into one FG.

Therefore, Custom Diecut Sheet should not use or trigger multi-piece logic.

## 3. Difference From 0201 / 0202 / 0203

0201, 0202, and 0203 derive blank geometry from box dimensions and style formulas.

Custom Diecut Sheet does not.

Differences:

- No box formula.
- No box height required.
- No seam required.
- No glue flap required.
- No flap depth required.
- No overlap required.
- Sheet size is entered directly.
- Diecut UPS / FG per Sheet controls yield.

The style should still reuse:

- stack model
- RM resolver
- costing
- routing profile
- StageContext preview
- BoM preview
- BoM materialization
- selected route operations

## 4. Required Inputs

Required first-version fields:

- Sheet Length mm
- Sheet Width mm
- Diecut UPS / FG per Sheet

Recommended internal mapping:

```text
box_length_mm = sheet_length_mm
box_width_mm  = sheet_width_mm
box_height_mm = ignored
```

Diecut UPS should be exposed as a user-facing field/parameter:

```text
diecut_ups = FG per Sheet
```

Final design direction:

- Treat `diecut_ups` as style/family yield metadata.
- Do not overload multi-piece fields.
- Keep naming user-facing: FG per Sheet or Diecut UPS.
- Do not make an explicit diecut stage mandatory.

## 5. Hidden / Not Required Fields

Hide or do not require:

- Height
- Seam
- Glue flap
- Flap depth
- Overlap
- Box panel formula fields

These fields should not block save, preview, or BoM creation for this style.

## 6. Geometry Formula

The user enters sheet size directly.

Formula:

```text
blank_length_mm = sheet_length_mm
blank_width_mm  = sheet_width_mm
area_m2_per_sheet = sheet_length_mm * sheet_width_mm / 1_000_000
fg_per_sheet = diecut_ups
area_m2_per_fg = area_m2_per_sheet / diecut_ups
```

Validation:

```text
sheet_length_mm > 0
sheet_width_mm > 0
diecut_ups >= 1
```

Example:

```text
Sheet Length = 1000 mm
Sheet Width  = 700 mm
Diecut UPS   = 2

area_m2_per_sheet = 1000 * 700 / 1_000_000
area_m2_per_sheet = 0.7 m2

area_m2_per_fg = 0.7 / 2
area_m2_per_fg = 0.35 m2
```

## 7. Diecut UPS / Yield Formula

Diecut UPS is the yield from one sheet to finished-good blanks.

Formula:

```text
required_sheets = ceil(order_qty / diecut_ups)
```

Example:

```text
Order Qty = 1000 FG
Diecut UPS = 2

Required Sheets = ceil(1000 / 2)
Required Sheets = 500
```

Yield rule:

- Upstream stages before yield consume required sheet quantity.
- Yield is applied at the selected yield stage.
- Downstream stages after yield consume FG-level blank quantity.
- If no explicit diecut stage exists, do not block preview.

## 8. Stage-Wise Yield Behavior

Example route:

```text
2ply -> board -> diecut -> peeling -> seaming
```

If:

```text
FG qty = 1000
Diecut UPS = 2
```

Expected stage quantities:

```text
2ply output    = 500 sheets
board output   = 500 board sheets
diecut output  = 1000 gross diecut blanks / cavities
peeling output = 1000 usable blanks after waste removal
seaming output = 1000 FG
```

If peeling/stripping does not exist:

```text
2ply -> board -> diecut -> seaming
```

Expected stage quantities:

```text
2ply output    = 500 sheets
board output   = 500 board sheets
diecut output  = 1000 usable blanks
seaming output = 1000 FG
```

Rules:

- If the route contains a diecut stage/capability, apply FG-per-sheet yield at diecut.
- If peeling/stripping exists after diecut, diecut output should be labelled as gross blanks.
- If peeling/stripping exists after diecut, peeling output should be labelled as usable blanks.
- If peeling/stripping does not exist after diecut, diecut output should be labelled as usable blanks directly.
- If no diecut stage exists but peeling/stripping exists, apply yield at peeling/stripping and label output as usable blanks.
- If no diecut or peeling exists but a downstream conversion/finishing stage exists, apply yield before/at that first conversion stage.
- If only material stages exist, apply yield at terminal output.
- Downstream stages after yield consume FG-level blank quantity, not sheet quantity.
- Upstream stages before yield consume required sheet quantity, not FG quantity.

## 9. Peeling / No-Peeling Behavior

Peeling present:

```text
diecut output = gross blanks
peeling output = usable blanks
```

Peeling absent:

```text
diecut output = usable blanks
```

No diecut, peeling present:

```text
peeling output = usable blanks
```

No diecut or peeling, first downstream conversion stage present:

```text
yield label = Yield applied before <stage name>
```

Only material stages present:

```text
terminal output = FG quantity
upstream material stages = required sheet quantity
```

Peeling should not be implied by the style.

The operation/stage must appear only if selected in the routing profile.

Accepted peeling aliases:

- peeling
- stripping
- waste stripping
- peel-off
- peeloff

## 10. RM Calculation

RM consumption should be based on finished-good share of the sheet.

Per-FG area:

```text
area_m2_per_fg = area_m2_per_sheet / diecut_ups
```

Paper per FG:

```text
paper_kg_per_fg = gsm * area_m2_per_fg / 1000
```

Medium per FG:

```text
medium_kg_per_fg = gsm * flute_tuf * area_m2_per_fg / 1000
```

Total order RM should equal sheet-area consumption for required sheets:

```text
total_order_area = area_m2_per_sheet * required_sheets
```

For exact multiples:

```text
total_order_area = area_m2_per_fg * order_qty
```

For non-exact multiples:

```text
required_sheets = ceil(order_qty / diecut_ups)
total_order_area = required_sheets * area_m2_per_sheet
```

Final order-level RM rule:

- BoM per FG uses `area_m2_per_fg`.
- Order-level RM uses whole required sheets.
- If FG qty is not divisible by FG per Sheet, total order RM must include the extra full sheet.

## 11. BoM Behavior

Expected BoM behavior:

- BoM attaches to the sellable FG product.
- BoM product qty can remain 1 FG.
- Component quantities represent one FG share of the sheet.
- Operations must come only from selected routing profile/stage contexts.
- Do not imply diecut, printing, peeling, gluing, stitching, or seaming operations unless the selected route includes them.

Example:

```text
Sheet size = 1000 x 700 mm
Sheet area = 0.7 m2
Diecut UPS = 2
Area per FG = 0.35 m2
```

BoM line quantities should represent:

```text
paper kg per 1 FG = gsm * 0.35 / 1000
```

The BoM should not create multiple products for the same-FG yield. This is yield, not multi-piece.

## 12. Routing Behavior

Routing must be selected-route driven.

Allowed examples:

```text
2ply -> board -> diecut -> peeling -> seaming
```

```text
2ply -> board -> diecut -> seaming
```

```text
2ply -> board -> diecut
```

```text
2ply -> board -> peeling -> seaming
```

```text
2ply -> board -> seaming
```

```text
2ply -> board
```

Rules:

- If route contains diecut stage/capability, apply FG-per-sheet yield at diecut.
- If no diecut stage exists but peeling/stripping exists, apply yield at peeling/stripping.
- If no diecut or peeling exists but a downstream conversion/finishing stage exists, apply yield before/at that first conversion stage and show: `Yield applied before <stage name>`.
- If only material stages exist, apply yield at terminal output.
- If no explicit diecut stage exists, do not block preview.
- If no explicit diecut stage exists, show info/warning: `No diecut stage found. FG-per-sheet yield is applied at the first conversion/terminal stage.`
- Peeling is optional and must not be implied.
- Printing is optional and must not be implied.
- Seaming/gluing/stitching is optional and must not be implied.
- Operations must come only from selected routing profile/stage contexts.

## 13. Validation Rules

MVP validation:

- Sheet Length mm > 0.
- Sheet Width mm > 0.
- Diecut UPS / FG per Sheet must be an integer >= 1.
- Height is hidden and ignored.
- Seam is not required.
- Glue flap is not required.
- Flap depth is not required.
- Overlap is not required.
- Box panel formula fields are not required.
- Route does not need an explicit diecut stage.

Recommended error wording:

- Sheet Length is required.
- Sheet Width is required.
- FG per Sheet must be at least 1.

## 14. Architecture Guardrails

Do not add style-specific logic into:

- StageGraph
- RM resolver
- Materialization Writer
- Materialization Resolver
- Multi-piece
- Shared Upstream
- Quantity Planner

Use:

- `StyleResult.metadata`
- style family metadata
- existing geometry contract
- routing/stage context metadata

This is not:

- a new stack process
- a new routing process
- a multi-piece process
- a shared-upstream split process

It is:

```text
manual sheet geometry + diecut UPS yield
```

Any shared behavior should be generalized as family/yield metadata, not hardcoded by style code.

## 15. Tests To Add

Style handler tests:

- style registry resolves `DIECUT_SHEET`
- blank length equals sheet length
- blank width equals sheet width
- area per sheet formula
- area per FG formula
- missing sheet length fails
- missing sheet width fails
- missing/zero diecut UPS fails
- height is not required
- seam/glue/flap/overlap are not required

Preview/RM tests:

- RM per FG uses `area_m2_per_sheet / diecut_ups`
- medium uses flute TUF
- total exact-order RM equals area per FG times order qty
- total sheet requirement uses `ceil(order_qty / diecut_ups)`
- non-divisible order qty includes the extra full sheet at order level

Stage yield tests:

- route with peeling: diecut output labelled gross blanks, peeling output labelled usable blanks
- route without peeling: diecut output labelled usable blanks
- route without diecut but with peeling: yield applies at peeling
- route without diecut/peeling but with conversion: yield applies before/at first conversion stage
- route with only material stages: yield applies at terminal output
- missing diecut warning is shown when yield falls back to conversion/terminal stage
- upstream stages use required sheet quantity
- downstream stages use FG quantity
- diecut yield applies at diecut stage when diecut exists

BoM/materialization tests:

- BoM attaches to sellable FG product
- BoM product qty remains 1 FG
- component qty is one-FG share of sheet
- operations match selected route only
- no printing operation unless selected
- no peeling operation unless selected
- no seaming/gluing/stitching unless selected

Regression tests:

- 0100 remains unchanged
- 0110 remains unchanged
- 0201 remains unchanged
- 0202 remains unchanged
- 0203 remains unchanged
- dynamic style params still work for 0202
- no style leakage into guardrailed modules

## 16. Open Questions Before Implementation

1. Should Diecut UPS be a dynamic style parameter or a family-specific UI field?
2. Should diecut output labels be UI-only metadata, or should they affect StageContext output kind?
3. Should diecut waste/stripping waste be supported in MVP or later?
4. Should FG per Sheet support only integers, or can fractional yield be used for special layouts?

## 17. Recommended MVP Position

Recommended MVP:

- style code: `DIECUT_SHEET`
- display name: Custom Diecut Sheet
- family: `diecut_sheet`
- manual sheet geometry from main length/width fields
- no height/seam/flap/overlap requirements
- FG per Sheet / Diecut UPS required
- diecut route stage is not required
- area per FG = sheet area / diecut UPS
- BoM component qty = one FG share
- order planning/debug should expose required sheets using ceil
- no-divisible order qty consumes the extra full sheet at order level
- no multi-piece behavior
- no implied operations
- yield stage is selected from route metadata: diecut, then peeling, then first conversion/finishing stage, then terminal output

## 18. Final Implementation Plan

This plan is for a future coding phase. Do not code until implementation is explicitly requested.

### Files / Areas To Modify

Expected additions:

- `engine_v2/styles/style_diecut_sheet.py`
- style registry autoload through existing `engine_v2/styles/__init__.py`
- style master seed/XML for `DIECUT_SHEET`
- tests for style handler and style metadata

Likely UI/model work:

- `models/engine_style.py`
- `models/engine_v2_spec.py`
- `views/engine_style_views.xml`
- `views/engine_v2_spec_views.xml`

Likely adapter/preview work:

- spec adapter mapping for sheet length/width and FG per Sheet
- preview metadata for `required_sheets`
- preview warning when no diecut stage exists
- route/stage context metadata for selected yield stage

Only generalized shared behavior is allowed:

- family/yield metadata
- `StyleResult.metadata`
- existing geometry contract
- routing/stage context metadata

Do not add `DIECUT_SHEET` style-code branching into guardrailed core modules.

### Style Handler

Handler responsibilities:

- read normalized sheet geometry
- read FG per Sheet / Diecut UPS
- validate required values
- emit blank dimensions equal to sheet dimensions
- emit area per sheet
- emit area per FG
- emit yield metadata

Expected metadata:

```python
{
    "style_code": "DIECUT_SHEET",
    "family": "diecut_sheet",
    "sheet_length_mm": 1000,
    "sheet_width_mm": 700,
    "area_m2_per_sheet": 0.7,
    "fg_per_sheet": 2,
    "area_m2_per_fg": 0.35,
    "yield_basis": "fg_per_sheet",
    "yield_stage_policy": "route_selected",
}
```

### UI Behavior

User sees:

- Sheet Length mm
- Sheet Width mm
- FG per Sheet / Diecut UPS

User does not need:

- Height
- Seam
- Glue flap
- Flap depth
- Overlap
- Box panel formula fields

Style Parameters section may contain FG per Sheet if implemented as a dynamic style parameter.

If implemented as a family-specific field, Style Parameters should show no extra parameters required.

### Yield Stage Selection

General yield-stage resolver should use route/stage metadata, not style-code checks.

Selection order:

1. diecut stage/capability
2. peeling/stripping stage
3. first downstream conversion/finishing stage
4. terminal output

If no diecut stage exists:

```text
No diecut stage found. FG-per-sheet yield is applied at the first conversion/terminal stage.
```

This should be an informational warning, not a blocker.

### Quantity Behavior

Given:

```text
fg_qty
fg_per_sheet
```

Compute:

```text
required_sheets = ceil(fg_qty / fg_per_sheet)
```

Before selected yield stage:

```text
stage output qty = required_sheets
```

At/after selected yield stage:

```text
stage output qty = fg_qty
```

BoM product qty:

```text
1 FG
```

BoM component qty:

```text
one FG share of sheet
```

### RM / Costing Behavior

Per FG:

```text
area_m2_per_fg = area_m2_per_sheet / fg_per_sheet
```

Paper:

```text
paper_kg_per_fg = gsm * area_m2_per_fg / 1000
```

Medium:

```text
medium_kg_per_fg = gsm * flute_tuf * area_m2_per_fg / 1000
```

Order-level reporting:

```text
total_area = required_sheets * area_m2_per_sheet
```

When order qty is not divisible by FG per Sheet, order-level RM/cost reporting must include the extra full sheet.

### BoM / Materialization Behavior

BoM behavior:

- attach to sellable FG product
- product qty remains 1 FG
- component qty is one FG share of sheet
- operations come only from selected routing profile/stage contexts
- do not imply diecut/peeling/printing/seaming

Materialization should not create multi-piece products because this is not multi-piece logic.

### Tests To Add For Implementation

Style tests:

- handler registered as `DIECUT_SHEET`
- style master exists with family `diecut_sheet`
- sheet length/width formula
- area per sheet
- area per FG
- FG per Sheet validation
- height/seam/flap/overlap not required

Yield tests:

- route with diecut and peeling
- route with diecut and no peeling
- route with peeling and no diecut
- route with conversion and no diecut/peeling
- route with only material stages
- no-diecut warning emitted
- upstream quantity uses required sheets
- downstream quantity uses FG qty

RM tests:

- paper per FG uses area per FG
- medium per FG uses TUF
- order-level reporting uses whole required sheets
- non-divisible FG qty includes extra full sheet

BoM tests:

- BoM attaches to FG product
- BoM product qty is 1
- component qty is one-FG sheet share
- operations exactly match selected route
- no implied diecut/peeling/printing/seaming

Regression tests:

- 0100 unchanged
- 0110 unchanged
- 0201 unchanged
- 0202 unchanged
- 0203 unchanged
- dynamic style params still work
- no style leakage into guardrailed modules

### MVP Exclusions

- No mixed FG outputs from one sheet.
- No fractional FG per Sheet.
- No diecut waste/stripping waste calculation.
- No die tool master.
- No rotary/flatbed machine filtering unless already available through route/workcenter.

### Implementation Blockers To Resolve

Before coding, confirm:

1. FG per Sheet storage approach: dynamic style parameter or family-specific field.
2. Whether yield labels should affect StageContext output kind or remain preview metadata.
3. Whether non-divisible order-level RM affects costing only or also generated MO quantity planning.



