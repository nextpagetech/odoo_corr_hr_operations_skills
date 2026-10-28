# Engine V2 Context: Phase K Style Expansion

## Current Completed Status

Engine V2 is ready for the next style-expansion phase.

Completed and validated:

- 0201 baseline complete.
- FEFCO 0202 complete.
- FEFCO 0203 complete.
- FEFCO 0100 Roll Product implemented and UI validated.
- FEFCO 0110 Corrugated Sheet complete and UI validated.
- Style registry complete.
- Dynamic style parameters complete.
- UI-driven style parameter framework complete.
- Style-specific geometry handlers complete for supported styles.
- Preview integration complete for supported styles.
- RM integration complete for supported styles.
- BoM integration complete for supported styles.
- UI/spec/preview/RM/BoM validation complete.
- End users do not enter raw JSON style parameters.
- Style-specific behavior remains isolated to style handlers.
- No style leakage into StageGraph, RM resolver, materialization writer, multi-piece materialization, shared-upstream logic, quantity planner, or other shared engine layers.
- Full Engine V2 suite passed after 0100 validation: 419 tests.

The current rule for Phase K remains the same: new styles should be added through style handlers and style metadata, not by changing shared engine orchestration or materialization internals.

## Current Architecture Summary

### Style Handlers

Style handlers live under:

```text
custom_addons/corr_mrp_bridge/engine_v2/styles/
```

Each supported style owns its geometry and style-specific validation in its handler.

The handler output follows the `StyleResult` contract. Shared downstream code consumes the normalized result instead of branching on FEFCO code.

### STYLE_REGISTRY

`STYLE_REGISTRY` maps style codes to handlers.

The registry is the intended extension point for new style geometry. Adding a new style should normally mean:

- add a handler under `engine_v2/styles/`
- register the handler in `STYLE_REGISTRY`
- add tests for the handler and engine integration
- add style master and style parameter metadata where required

### StyleResult Contract

The `StyleResult` contract is the boundary between style-specific geometry and shared engine processing.

The result should expose normalized geometry and metadata needed by:

- preview
- RM breakdown
- layout calculations
- BoM preview
- actual BoM creation
- multi-piece flows

Shared code should consume the contract, not inspect individual style codes.

### Odoo Style Master

The style master is:

```text
corr.engine.style
```

It owns user-facing style metadata:

- code
- name
- family
- active
- description/help

Current completed style records:

- 0201: baseline regular slotted carton behavior, no extra parameters.
- 0202: overlap style, configured with `overlap_mm`.
- 0203: full-overlap slotted container behavior, no extra parameters.
- 0100: corrugated roll product behavior, using Roll Length in meters and Roll Width in mm with normalized geometry storage; no extra style parameters.
- 0110: corrugated sheet product behavior, using main geometry length/width as sheet length/width and no extra style parameters.

### Style Parameter Definitions

The style parameter definition model is:

```text
corr.engine.style.param
```

It defines per-style parameter metadata:

- key
- label
- type
- unit
- default
- required
- min/max
- help text
- sequence
- active

This allows future styles to add UI-driven parameters without adding hardcoded fields to the main spec form.

### Spec-Specific Style Parameter Values

Spec-specific values are stored in:

```text
corr.engine.v2.spec.style.param.value
```

When a user selects a style on an Engine V2 Spec:

- parameter lines are loaded from `corr.engine.style.param`
- defaults are prefilled
- users edit typed fields in Odoo UI
- required/min/max validation runs before preview
- handler-level validation remains the final safety net

### Adapter Mapping

The spec adapter collects spec-specific typed parameter values and maps them into:

```python
spec_input.style_params
```

Example for 0202:

```python
{"overlap_mm": 25}
```

No raw JSON entry is exposed to end users.

## Recommended Next Style Roadmap

Phase K roadmap status:

- Completed: K1: FEFCO 0203.
- Completed: K5b: FEFCO 0100 Roll Products.
- Completed: K5a: FEFCO 0110 Corrugated Sheet.
- Remaining: K2: FEFCO 0427.
- Remaining: K3: FEFCO 0409.
- Remaining: K4: 03xx telescope family.

This order started with styles closest to the existing 0201/0202 slotting model. 0110 and 0100 were pulled forward because sheet and roll products have immediate business value and proved the 01xx non-box family path.

## K1: FEFCO 0203

Status: complete.

### Business Value

0203 is a close follow-up to 0201/0202 and is commonly needed for slotted carton variants. It expands usable box coverage without forcing users into custom/manual blank calculations.

### Complexity

Low to medium.

It should be close to the existing 0201 style family, with limited additional geometry rules compared with 0427 or 03xx families.

### Required Parameters

None for the first supported implementation.

The supported formula is:

```text
blank_length_mm = 2 * (L + W) + seam_mm
blank_width_mm  = H + (2 * W)
```

### Formula Risk

Resolved for the first implementation. Production/UI validation is complete for the approved full-overlap interpretation.

### Multi-Piece / Shared-Upstream Impact

Should work unchanged if the handler returns normalized blank geometry through `StyleResult`.

No expected change to:

- StageGraph
- RM resolver
- materialization writer
- independent routed pieces
- shared-upstream split logic

## K2: FEFCO 0427

### Business Value

0427 is a high-value die-cut mailer/tray style. It is useful for e-commerce, display, and self-locking packaging use cases where slotted carton formulas are not enough.

### Complexity

High.

0427 introduces more complex blank geometry than 020x styles and may require locking/tuck/flap allowances depending on production rules.

### Required Parameters

Likely parameters:

- tuck flap depth
- locking flap allowance
- side flap allowance
- dust flap allowance
- optional corner/slot allowance
- optional clearance/tolerance

Parameter names and defaults should be driven by production examples, not guessed from generic diagrams.

### Formula Risk

High.

Formula risk is significant because 0427 can vary by tooling convention, lock design, and customer-specific dieline practice. It should not be implemented without sample dieline dimensions and expected blank size.

### Multi-Piece / Shared-Upstream Impact

Should work unchanged if represented as a single normalized blank.

Risk appears only if 0427 is later modeled as a multi-component/die-cut assembly. That should be treated as a separate phase, not part of initial 0427 support.

## K3: FEFCO 0409

### Business Value

0409 adds another folding/die-cut style family and extends Engine V2 into non-RSC packaging demand. It is useful when customers need wrap/folder style packaging rather than standard cartons.

### Complexity

Medium to high.

It is less close to 0201 than 0203 and may require style-specific allowances for fold panels, cover panels, or side retention features.

### Required Parameters

Likely parameters:

- cover flap depth
- side flap depth
- fold allowance
- optional clearance/tolerance
- optional lock/tuck allowance if used in the local production interpretation

### Formula Risk

Medium to high.

The risk is mainly around panel interpretation and whether the local implementation treats the style as a simple wrap/folder or as a more detailed die-cut layout.

### Multi-Piece / Shared-Upstream Impact

Should work unchanged for single-blank materialization.

No expected shared-upstream or multi-piece changes if the output remains a normalized blank.

## K4: 03xx Telescope Family

### Business Value

03xx telescope styles support two-piece boxes such as lid/base packaging. This is commercially important for premium, rigid-style, display, and telescoping package requirements.

### Complexity

High.

This is a family, not one isolated style. It may require two related blanks, lid/base sizing rules, clearance rules, and family-level conventions.

### Required Parameters

Likely parameters:

- lid height
- base height
- clearance between lid and base
- overlap/depth relationship
- optional fit tolerance
- optional separate lid/base board recipes if production requires different material

### Formula Risk

High.

The main risk is treating a multi-blank family as a single blank. Before implementation, decide whether initial 03xx support will be:

- one selected telescope style with two generated blanks
- a family framework
- a limited single-piece approximation

The first production-ready version should use real examples and expected lid/base blank dimensions.

### Multi-Piece / Shared-Upstream Impact

May not work unchanged if implemented as true two-piece lid/base output.

If represented through existing multi-piece definitions, independent routed pieces may be reusable. Shared-upstream behavior needs explicit review because lid/base pieces may not share the same upstream blank.

Do not modify shared-upstream or materialization internals until the style output model is agreed.

## K5: 01xx Sheet/Roll Family

### Business Value

01xx support helps model sheets, rolls, pads, and simpler converted board products. This expands Engine V2 beyond boxes and supports straightforward manufacturing definitions.

### Completed 0100 Status

FEFCO 0100 Roll Product is implemented and UI validated.

Validated behavior:

- Roll Length is entered/displayed in meters.
- Roll Width is entered/displayed in mm.
- Internal geometry reuses `box_length_mm` and `box_width_mm`.
- Height is hidden and ignored.
- Style Parameters section shows no extra parameters required.
- 0100 defines roll output geometry, not a specific board construction.
- Any valid Engine V2 stack configuration remains stack-driven.
- Product qty basis is 1 roll.
- UoM can remain Units for the MVP.
- BoM attaches to the sellable roll product.
- Operations come from selected route/stage contexts.
- No terminal SFG is created unless configured in a future phase.

Validated UI case:

```text
Roll Length = 100 m
Roll Width  = 600 mm
Area        = 60 m2

Stack:
- Medium 180 GSM, B flute
- Liner 160 GSM

RM quantities:
- Medium = 14.9 kg
- Liner  = 9.6 kg
```

The RM quantities are correct:

- Medium uses area x GSM x flute factor.
- Liner uses area x GSM.

Verification completed:

- Targeted 0100 regressions passed.
- 0110 regression passed.
- 0201, 0202, and 0203 regressions passed.
- Manufacturing plan/materialization regressions passed.
- Full Engine V2 suite passed: 419 tests.
- No 0100 style leakage was detected in StageGraph, RM resolver, materialization writer, materialization resolver, multi-piece materialization, shared-upstream logic, or quantity planner.

### Completed 0110 Status

FEFCO 0110 Corrugated Sheet is complete and UI validated.

Validated behavior:

- BoM is created for the sellable sheet product.
- BoM attaches directly to the spec finished product/product template.
- RM quantity is correct for sheet area.
- Selected route operations are respected.
- Lamination may remain an operation stage when no separate laminated SFG is required.
- No unexpected printing operation appears when printing is not in the selected route.
- Style Parameters section shows no extra parameters required.
- Main geometry length/width are reused as sheet length/width.
- Box height, seam, glue flap, and flap/overlap are not required for 0110.

Verification completed:

- Targeted 0110 regressions passed.
- 0201, 0202, and 0203 regressions passed.
- Manufacturing plan/materialization regressions passed.
- Full Engine V2 suite passed.
- No 0110 style leakage was detected in StageGraph, RM resolver, materialization writer, multi-piece materialization, materialization resolver, or shared-upstream logic.

### Complexity

Medium.

The geometry can be simple, but the product family may differ from carton-style assumptions around height, flaps, seam, UPS, and finished-good dimensions.

### Required Parameters

0110 completed parameters:

- no extra style parameters
- main geometry `box_length_mm` is displayed/used as sheet length
- main geometry `box_width_mm` is displayed/used as sheet width
- height/seam/flap inputs are not required

0100 completed parameters:

- no extra style parameters
- Roll Length is entered/displayed in meters
- Roll Width is entered/displayed in mm
- internal `box_length_mm = roll_length_m * 1000`
- internal `box_width_mm = roll_width_mm`
- height/seam/flap/overlap inputs are not required

Future optional 0100 parameters may include:

- trim margin
- core diameter
- core product
- wrapping
- packing fields

Some values may already exist as core spec or layout fields. Avoid duplicating existing fields as style params unless the parameter is truly style-specific.

### Formula Risk

Medium.

Risk is less about geometry and more about preserving clean contracts across box, sheet, roll, and pad families. The family field on `corr.engine.style` should be used to keep behavior explicit.

### Multi-Piece / Shared-Upstream Impact

Likely works unchanged for sheet/pad single-blank flows.

Roll MVP flows work through the same family/metadata-driven materialization pattern used by 0110. Future roll-specific changes such as partial rolls, non-Units UoM, cores, wrapping, or palletization should be handled as explicit later-phase scope.

## Testing Checklist For Every New Style

Every new style added in Phase K should include this validation pass before being marked complete:

- Style handler test.
- Style parameter UI/default test.
- Preview geometry test.
- RM breakdown test.
- Layout option test.
- BoM preview test.
- Actual BoM creation smoke test.
- 0201 regression test.
- 0202 regression test.

Minimum sample validation for each style should include:

- one normal dimension set
- one changed style parameter value
- one missing required parameter validation case
- one min/max validation case when applicable
- one preview-to-BoM path

## Phase K Guardrails

During Phase K, do not add new style-specific logic to:

- StageGraph
- RM resolver
- materialization writer
- multi-piece materialization
- shared-upstream logic
- generic layout/RM contracts unless the contract itself is intentionally being expanded

Preferred implementation path:

1. Confirm formula and expected blank dimensions.
2. Add style master and parameter definitions.
3. Add style handler.
4. Register in `STYLE_REGISTRY`.
5. Add focused handler and adapter tests.
6. Validate UI parameter loading and defaults.
7. Validate preview/RM/layout/BoM.
8. Re-run 0201 and 0202 regressions.

## Next Planned Style Recommendation

Recommended next style: FEFCO 0427.

Reason:

- 0201, 0202, and 0203 complete the immediate slotted-carton baseline.
- 0110 and 0100 complete the first useful 01xx non-box sheet/roll slice.
- 0427 has strong business value for mailer/tray/e-commerce packaging.
- 0427 will test the folder/tray family without starting the larger 03xx multi-piece telescope family yet.

Before coding 0427, prepare formula/design confirmation only:

- final supported 0427 interpretation
- required dimensions and style parameters
- sample dieline or production blank examples
- blank length and blank width formula
- lock/tuck/flap allowance defaults
- whether 0427 remains a single blank in MVP
- preview/RM/BoM validation checklist



