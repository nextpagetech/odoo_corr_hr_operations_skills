# Engine V2 Phase K: FEFCO 0203 Formula Confirmation

## Purpose

This note confirms the first Engine V2 interpretation to support for FEFCO 0203 before implementation.

No code should be written until this formula is accepted.

## 1. Final Style Interpretation To Support First

Support FEFCO 0203 as a Full Overlap Slotted Container.

Initial supported interpretation:

- same body-panel wrap as 0201 and 0202
- same seam/glue flap behavior as 0201 and 0202
- one rectangular blank
- symmetric top and bottom full-overlap flaps
- each full-overlap flap depth equals the box width `W`
- no user-entered style parameter for the first version

This means 0203 is treated as a fixed full-overlap style, not as a tunable 0202 variant.

## 2. Exact Blank Length Formula

```text
blank_length_mm = 2 * (L + W) + seam_mm
```

Where:

- `L` = finished box length
- `W` = finished box width
- `seam_mm` = glue flap / manufacturer's joint allowance

Seam precedence should remain consistent with existing 0201/0202 behavior:

1. `glue_flap_override_mm`
2. legacy/spec glue flap value if available
3. plant default glue lap
4. engine fallback

## 3. Exact Blank Width Formula

```text
blank_width_mm = H + (2 * W)
```

Where:

- `H` = finished box height
- `W` = finished box width

Interpretation:

```text
top_flap_depth_mm    = W
bottom_flap_depth_mm = W
blank_width_mm       = top_flap_depth_mm + H + bottom_flap_depth_mm
```

No extra overlap or clearance allowance is included in the first implementation.

## 4. Required Style Params

None for the first implementation.

0203 should behave like 0201 in the style parameter UI:

```text
No extra parameters required.
```

## 5. Default Values

No 0203-specific defaults are required.

The only default/fallback behavior should come from existing shared geometry inputs:

- seam/glue flap fallback from existing 0201/0202 convention
- standard finished box dimensions from the spec

Do not add:

- `overlap_mm`
- `flap_depth_mm`
- `full_overlap_clearance_mm`
- hardcoded 0203-specific allowance

Those can be introduced later only if production confirms they are required.

## 6. Sample Calculation

Input:

```text
L = 600 mm
W = 300 mm
H = 180 mm
seam = 30 mm
```

Blank length:

```text
blank_length = 2 * (L + W) + seam
blank_length = 2 * (600 + 300) + 30
blank_length = 2 * 900 + 30
blank_length = 1830 mm
```

Blank width:

```text
blank_width = H + (2 * W)
blank_width = 180 + (2 * 300)
blank_width = 180 + 600
blank_width = 780 mm
```

## 7. Expected Blank Length And Width

For:

```text
L=600, W=300, H=180, seam=30
```

Expected:

```text
blank_length_mm = 1830
blank_width_mm  = 780
```

## 8. Differences From 0201 And 0202

### Difference From 0201

0201 current baseline:

```text
blank_length = 2 * (L + W) + seam
blank_width  = W + H + flap_add
```

0203 confirmed first formula:

```text
blank_length = 2 * (L + W) + seam
blank_width  = H + (2 * W)
```

For the sample dimensions and seam:

```text
0201 blank_length = 1830
0201 blank_width  = 500 if flap_add=20

0203 blank_length = 1830
0203 blank_width  = 780
```

0203 uses much more board in the width direction because both flaps fully overlap.

### Difference From 0202

0202 current formula:

```text
blank_length = 2 * (L + W) + seam
blank_width  = H + W + (2 * overlap_mm)
```

0203 confirmed first formula:

```text
blank_length = 2 * (L + W) + seam
blank_width  = H + (2 * W)
```

For the sample dimensions:

```text
0202 with overlap=25:
blank_width = 180 + 300 + 50 = 530

0203:
blank_width = 180 + 600 = 780
```

0202 is partial overlap controlled by `overlap_mm`.

0203 is full overlap with no user-entered overlap parameter in the first implementation.

## 9. Test Cases To Add

Add focused tests before marking 0203 complete.

### Style Handler Tests

- `0203` computes expected blank length and width for `L=600, W=300, H=180, seam=30`.
- `0203` returns `None` or the existing safe fallback behavior when required dimensions are missing or zero.
- `0203` exposes expected geometry metadata:
  - `style_code = 0203`
  - `blank_formula = 2*(L+W)+seam by H+2*W`
  - `top_flap_depth_mm = W`
  - `bottom_flap_depth_mm = W`

### Style Registry Tests

- `STYLE_REGISTRY` resolves `0203`.
- Existing `0201` and `0202` registry behavior remains unchanged.

### Style Master / Parameter Tests

- `corr.engine.style` includes active `0203`.
- `0203` has no active `corr.engine.style.param` records for the first implementation.
- Engine V2 Spec UI shows `No extra parameters required.` for 0203.

### Adapter Tests

- Selecting 0203 produces no required `style_params`.
- Adapter builds `EngineSpecInput` for 0203 without raw JSON or missing-parameter errors.

### Preview Geometry Tests

- Preview geometry for sample input returns:
  - `blank_length_mm = 1830`
  - `blank_width_mm = 780`
- Preview exposes geometry in metrics/debug payload consistently with 0201/0202.

### RM Breakdown Tests

- RM quantities use `1830 x 780`, not 0201 width and not 0202 overlap width.
- RM line count and layer behavior remain unchanged from comparable 0201/0202 stack setup.

### Layout Tests

- Layout calculations consume the larger 0203 blank width.
- Rotation/UPS behavior follows normal rectangular blank rules unless a layout policy overrides it.

### BoM Tests

- BoM preview works for 0203.
- Actual BoM creation smoke test creates expected material components.
- Component quantities are based on the 0203 expanded blank dimensions.

### Regression Tests

- 0201 sample remains unchanged.
- 0202 sample remains unchanged, including required `overlap_mm`.
- Missing 0202 `overlap_mm` still raises the existing clear validation message.

## 10. Risk Or Production Confirmation Needed

Risks:

- Some production standards may use a small clearance/shortfall instead of exact full-width flap depth.
- Some plants may encode full-overlap behavior using a legacy `flap_add` or plant allowance.
- The current 0201 handler comment mentions legacy geometry for `0201/0203`; that should not override this confirmed 0203 formula unless production says the legacy behavior was intentional.
- RM usage will increase significantly versus 0201 and 0202. Users should expect higher material consumption.
- If production later needs adjustable full-overlap clearance, a style parameter can be added without changing shared engine architecture.

Confirm before implementation:

- Production accepts `blank_width = H + (2 * W)` for first 0203 support.
- No full-overlap clearance/shortfall is required for first release.
- Top and bottom flap depths are symmetric.
- Seam/glue flap uses the same source and precedence as 0201/0202.
- 0203 should show no extra parameters in the UI for now.
- Sample expected output is accepted:

```text
L=600, W=300, H=180, seam=30
blank_length=1830
blank_width=780
```

## Implementation Decision

If this note is approved, implementation can proceed with:

- new `style_0203.py` handler
- `STYLE_REGISTRY` registration
- active `corr.engine.style` record for 0203
- no active 0203 style params
- tests listed above

Do not add 0203-specific logic to StageGraph, RM resolver, materialization writer, multi-piece materialization, or shared-upstream logic.



