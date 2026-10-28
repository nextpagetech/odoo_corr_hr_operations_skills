# Engine V2 Phase K: FEFCO 0203 Formula Validation Note

## Purpose

This note is for formula validation only.

Do not implement FEFCO 0203 until production confirms the exact blank formula, required parameters, and expected sample output.

## 1. Style Description

FEFCO 0203 is generally described as a Full Overlap Slotted Container, often abbreviated as FOL.

It belongs to the 02xx slotted box family. Its body-panel wrap is similar to a regular slotted container, but the closing flaps are extended so the outer flaps fully overlap when the box is closed.

Practical use cases:

- heavier products
- rough handling
- extra top/bottom protection
- extra stacking or cushioning strength from doubled flap coverage

External references consistently describe 0203 as a full-overlap slotted container:

- FEFCO describes the FEFCO code as the standard coding system for common corrugated packaging designs.
- Packsize describes 0203 as a Full Overlap Slotted Container where all flaps have the same length and the outer flaps completely overlap.
- The Box Factory describes 0203 as a full overlap box and a slotted FEFCO style.
- EasyPackMaker describes 0203 as a Full Overlap Slotted Container with full overlapping top and bottom flaps.
- Fibre Box Association / IoPP material describes 0203 as an RSC with both sets of outer flaps overlapping to the full width of the container.

## 2. Difference From 0201 And 0202

### Compared With 0201

0201 is the baseline regular slotted carton.

Current Engine V2 0201 behavior:

```text
blank_length = 2 * (L + W) + seam
blank_width  = W + H + flap_add
```

In normal RSC interpretation, the flaps meet around the centerline of the top/bottom opening rather than fully overlapping.

0203 differs by requiring full-overlap top and bottom flap coverage. This usually increases the blank width compared with a 0201 for the same L/W/H/seam.

### Compared With 0202

0202 is the partial overlap slotted container already implemented in Engine V2.

Current Engine V2 0202 behavior:

```text
top_flap_depth    = W / 2 + overlap_mm
bottom_flap_depth = W / 2 + overlap_mm
blank_length      = 2 * (L + W) + seam
blank_width       = H + W + (2 * overlap_mm)
```

0203 is the full-overlap version. Instead of a small additional `overlap_mm`, the flap depth may need to reach approximately the full box width, depending on the production interpretation.

Key distinction:

- 0202: partial overlap, adjustable by overlap allowance.
- 0203: full overlap, likely governed by full flap depth and any clearance/shortfall allowance.

## 3. Possible Formula Options

These are candidate formulas only. Production must confirm which one matches the plant's 0203 dieline convention.

### Option A: Full Flap Depth Equals Width

Interpretation:

- top flap depth = W
- bottom flap depth = W
- full overlap is achieved by each outer flap covering the full opening width

Candidate formula:

```text
blank_length = 2 * (L + W) + seam
blank_width  = H + (2 * W)
```

Optional allowance version:

```text
blank_width = H + (2 * W) + top_bottom_allowance_mm
```

This is the cleanest mathematical interpretation of full overlap.

### Option B: Full Overlap With Shortfall / Clearance

Some box references describe full overlap slotted containers as having flaps that nearly fully overlap, sometimes with a small gap or shortfall.

Candidate formula:

```text
flap_depth  = W - clearance_mm
blank_width = H + (2 * flap_depth)
```

Where:

```text
blank_width = H + (2 * (W - clearance_mm))
```

This avoids assuming perfect full-width flap depth if production normally leaves a closing gap.

### Option C: Extend Current 0202 Model To Full Overlap

Current 0202 formula:

```text
blank_width = H + W + (2 * overlap_mm)
```

Full overlap would be reached when each flap depth becomes approximately W:

```text
W / 2 + overlap_mm = W
overlap_mm = W / 2
```

So:

```text
blank_width = H + W + (2 * (W / 2))
blank_width = H + (2 * W)
```

This lands at the same result as Option A, but the parameter model would be different.

Do not expose this as `overlap_mm` unless the business wants 0203 to be tunable like 0202. For 0203, a clearer parameter may be `flap_depth_mm`, `full_overlap_clearance_mm`, or no parameter at all if the formula is fixed.

### Option D: Legacy 0201/0203 Compatibility Formula

The current 0201 handler comment says it mirrors legacy geometry used for 0201/0203:

```text
blank_length = 2 * (L + W) + seam
blank_width  = W + H + flap_add
```

This should not be accepted automatically as the real 0203 formula.

It may reflect older system behavior where 0203 was treated like 0201 for estimation, or where `flap_add` encoded an additional plant-specific allowance. Production must confirm whether this was intentional or only a legacy shortcut.

## 4. Required Style Parameters

The likely parameter set depends on which formula option production confirms.

### If Option A Is Confirmed

Required style parameters:

- none, if full overlap is always exactly `W`

Optional style parameters:

- `full_overlap_allowance_mm`
- `full_overlap_clearance_mm`

Recommended default:

```text
full_overlap_clearance_mm = 0
```

### If Option B Is Confirmed

Required style parameters:

- `full_overlap_clearance_mm`

Suggested metadata:

```text
key: full_overlap_clearance_mm
label: Full overlap clearance
type: float
unit: mm
default: production-confirmed value, possibly 0 or 25.4
required: false or true depending on plant standard
min_value: 0
```

### If Option C Is Confirmed

Required style parameters:

- normally none, because full overlap can be derived as `W / 2` additional overlap

Optional style parameters:

- `overlap_mm`, only if 0203 is intentionally treated as an adjustable overlap style

Risk:

- This may confuse users because 0202 already uses `overlap_mm` for partial overlap. 0203 should not look like a user-entered 0202 variant unless the business explicitly wants that.

### If Option D Is Confirmed

Required style parameters:

- likely none, if it remains legacy-compatible

But production must confirm what `flap_add` means for 0203:

- fixed plant flap allowance
- additional full-overlap allowance
- legacy approximation
- incorrect historical behavior

## 5. Sample Calculation

Sample dimensions:

```text
L = 600 mm
W = 300 mm
H = 180 mm
```

Assume seam/glue flap:

```text
seam = 30 mm
```

### Common Blank Length

All candidate options keep the same body-panel wrap length:

```text
blank_length = 2 * (L + W) + seam
blank_length = 2 * (600 + 300) + 30
blank_length = 1830 mm
```

### Option A: Exact Full Overlap

```text
blank_width = H + (2 * W)
blank_width = 180 + (2 * 300)
blank_width = 780 mm
```

Expected blank:

```text
1830 x 780 mm
```

### Option B: Full Overlap With Clearance

Example only, using `clearance_mm = 25`:

```text
flap_depth = W - clearance
flap_depth = 300 - 25
flap_depth = 275 mm

blank_width = H + (2 * flap_depth)
blank_width = 180 + (2 * 275)
blank_width = 730 mm
```

Expected blank:

```text
1830 x 730 mm
```

### Option C: 0202-Derived Full Overlap

Full overlap through 0202-style math:

```text
overlap_mm = W / 2
overlap_mm = 150 mm

blank_width = H + W + (2 * overlap_mm)
blank_width = 180 + 300 + (2 * 150)
blank_width = 780 mm
```

Expected blank:

```text
1830 x 780 mm
```

This matches Option A but has a less clear user parameter story.

### Option D: Current Legacy 0201/0203-Like Formula

Using current 0201 fallback-style flap add of 20 mm:

```text
blank_width = W + H + flap_add
blank_width = 300 + 180 + 20
blank_width = 500 mm
```

Expected blank:

```text
1830 x 500 mm
```

This is probably too small for true full-overlap 0203 unless `flap_add` is not the actual 0203 full-overlap allowance. Treat this as a legacy compatibility candidate only.

## 6. Risks And Assumptions

Key risks:

- FEFCO descriptions identify 0203 as full overlap, but they do not by themselves prove the exact production blank formula used by this plant.
- Some industry references describe full overlap as complete overlap; others describe near-complete overlap or shortfall.
- The current code comment mentioning legacy 0201/0203 geometry may represent an old approximation rather than correct 0203 support.
- If the formula is implemented as `H + 2W`, RM usage will increase substantially compared with 0201.
- If the formula is implemented with a clearance/shortfall, the parameter must be named clearly and validated.
- Reusing `overlap_mm` from 0202 may confuse users unless the business explicitly defines 0203 as an adjustable full-overlap variant.
- Top and bottom flap behavior is assumed symmetric unless production confirms otherwise.
- No change should be made to StageGraph, RM resolver, materialization writer, multi-piece logic, or shared-upstream logic.

Working assumptions until confirmed:

- 0203 uses the same body panel wrap as 0201/0202.
- 0203 uses the same seam/glue flap precedence as 0201/0202.
- 0203 returns one rectangular blank through `StyleResult`.
- RM, layout, preview, BoM preview, actual BoM creation, and multi-piece flows should consume the resulting blank dimensions unchanged.

## 7. Production Data Required Before Implementation

Before implementing 0203, confirm:

- Official production interpretation of FEFCO 0203 used by the plant.
- Whether full overlap means exactly `flap_depth = W`.
- Whether there is a standard shortfall/clearance from full overlap.
- Whether top and bottom flaps are symmetric.
- Whether inner and outer flaps are the same length in the plant's 0203 layout.
- Whether 0203 uses the same seam/glue flap field as 0201/0202.
- Whether any additional slot, trim, or die allowance is required.
- Whether the current legacy 0201/0203 formula was intentional.
- One approved sample with:
  - L
  - W
  - H
  - seam/glue flap
  - any clearance/allowance
  - expected blank length
  - expected blank width
- One actual historical BoM or cutting-sheet example, if available.
- Whether users need to edit a style parameter in the UI, or whether 0203 should have no extra parameters.

## Recommendation

Do not implement 0203 yet.

Recommended decision path:

1. Ask production to validate whether 0203 blank width is `H + 2W`.
2. If not, capture the exact clearance/allowance rule.
3. Choose the style parameter model:
   - no params for exact full overlap
   - `full_overlap_clearance_mm` for shortfall
   - avoid `overlap_mm` unless the business wants 0203 to behave like a tunable 0202 variant
4. Confirm sample expected output for `L=600, W=300, H=180, seam=30`.
5. Only then add the handler, registry entry, style master record, optional style params, and tests.



