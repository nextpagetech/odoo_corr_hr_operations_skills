# Engine V2 Style Onboarding Checklist

All style-specific geometry must live under `engine_v2/styles/` and be
registered through `STYLE_REGISTRY`. Do not add style-specific branches to
StageGraph, RM resolution, multi-piece materialization, shared-upstream
materialization, or BoM writer code.

## Required Style Definition

For every new style, document and test:

- Style code and name.
- Required input dimensions.
- Blank length formula.
- Blank width formula.
- Flap assumptions.
- Score/fold assumptions.
- Width split allowance.
- Length split allowance.
- Shared-upstream allowance.
- Rotation/orientation rules.
- Downstream impact, if any.

## Handler Contract

Each handler must return `StyleResult` from `contract.py`:

- `blank_length_mm`
- `blank_width_mm`
- `score_lines`
- `fold_lines`
- `flap_mm`
- `seam_mm`
- `allows_width_split`
- `allows_length_split`
- `allows_shared_upstream`
- `orientation_rules`
- `metadata`

If required dimensions are missing, return `None` so legacy/manual blank
dimensions can continue to be used only when the style handler cannot derive
geometry from the given input.

## Tests Required

Add focused tests for:

- Direct handler blank dimensions.
- GeometrySpec precedence over legacy dynamic fields.
- Missing required dimensions.
- Registry resolution.
- Compute-level metrics using resolved blank dimensions.
- Style metadata and capabilities exposed in `metrics.extra["style"]`.
- Layout compatibility.
- Splitting compatibility or split restrictions.
- BoM preview/materialization regression when the style is used by existing
  preview/materialization flows.

## 0201 Baseline

FEFCO 0201 is the baseline style. Its current formulas must remain stable:

```text
blank_length_mm = 2 * (L + W) + seam_mm
blank_width_mm  = W + H + flap_mm
```

0201 capabilities:

- Allows width split: yes
- Allows length split: no
- Allows shared upstream: yes
- Uses generic rectangular layout orientation rules

## MONO_STE Baseline

MONO_STE is the first mono-carton baseline style. It represents a Straight
Tuck End folding carton and keeps only geometry/orientation inside the
style handler.

Required input dimensions:

```text
L = carton length
W = carton width/depth
H = carton height
```

Style parameters:

```text
carton_board_gsm    required, default 300
glue_flap_mm        required, default 20
top_tuck_flap_mm    optional, zero means W
bottom_tuck_flap_mm optional, zero means W
trim_allowance_mm   optional, default 0
bleed_mm            optional, default 3
grain_direction     required, default height
```

Formula:

```text
blank_length_mm = glue_flap_mm + L + W + L + W + trim_allowance_mm
blank_width_mm  = top_tuck_flap_mm + H + bottom_tuck_flap_mm + trim_allowance_mm
```

MONO_STE capabilities:

- Allows width split: yes
- Allows length split: no
- Allows shared upstream: yes
- Primary material role: carton_board
- No corrugated liner/medium/flute assumptions in the style handler




