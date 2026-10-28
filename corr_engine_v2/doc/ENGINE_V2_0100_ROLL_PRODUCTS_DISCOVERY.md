# Engine V2 0100 Roll Products Discovery

## Purpose

Discovery and design note for FEFCO 0100 Roll Products before implementation.

No coding is included in this document.

## 1. Business Meaning Of 0100

In Engine V2, 0100 should represent a Roll Product style, not a box, not a cut sheet, and not a specific board construction.

Business examples:

- single-face corrugated rolls sold to customers
- protective wrapping rolls
- roll stock transferred to another converting process
- internal roll SFG consumed later by sheet cutting or box conversion

0100 defines the output geometry and product form:

```text
continuous roll output = roll length + roll width
```

It must not artificially restrict the board stack. Engine V2 should allow any valid stack configuration already supported by the stack engine, including:

- 2-ply roll
- 3-ply roll
- 5-ply roll
- 7-ply roll
- future custom stack configurations

Reason:

- Engine V2 already supports stack-driven manufacturing.
- 0100 should define output geometry, not board construction.
- Avoid future redesign when customers require multi-ply roll products.

The key business difference from 0110 is quantity basis:

- 0110 is countable sheet output
- 0100 is continuous roll output with width and length

The first implementation should treat 0100 as a roll product with a fixed width and fixed length per roll.

## 2. Required Input Fields

Recommended first-version fields:

- Roll Length, entered/displayed in meters
- Roll Width, entered/displayed in millimeters

Recommended first-version default:

```text
product qty basis = 1 roll
UoM can remain Units
```

Optional later fields:

- `roll_diameter_mm`
- `core_diameter_mm`
- `core_product_id`
- `core_weight_kg`
- `wrap_required`
- `trim_margin_mm`
- `roll_label_required`
- `packing_qty_per_pallet`

Do not require:

- box height
- seam / glue flap
- flap depth
- overlap
- box-style blank panels

Final MVP UI decision:

- Do not add separate dynamic style params for `roll_width_mm` and `roll_length_m`.
- Reuse the same main geometry pattern that worked for 0110.
- `box_length_mm` is the internal field for Roll Length after converting meters to millimeters.
- `box_width_mm` is the internal field for Roll Width.
- `box_height_mm` is hidden and not required.

Style-specific UI labels:

- Length displays as Roll Length and uses meters.
- Width displays as Roll Width and uses millimeters.
- Height is hidden for 0100.

Reason:

- avoids duplicate geometry fields
- follows the successful 0110 pattern
- keeps existing area-based RM, costing, preview, and BoM logic reusable
- keeps style parameters only for future optional roll-specific values like core diameter, wrapping, trim margin, or packing rules

## 3. Geometry Formula

0100 is a continuous rectangle represented by roll length and roll width using the existing main geometry fields.

Formula:

```text
box_length_mm = roll_length_m * 1000
box_width_mm  = roll_width_mm

roll_length_m  = box_length_mm / 1000
roll_width_mm  = box_width_mm
```

Internal style result:

```text
box_length_mm = roll_length_m * 1000
box_width_mm  = roll_width_mm

blank_length_mm = box_length_mm
blank_width_mm  = box_width_mm
area_m2_per_fg  = roll_length_m * roll_width_mm / 1000
```

This equivalent rectangle is for calculation only. UI and product terminology should say roll, not blank or box.

Example:

```text
User enters Roll Length = 100 m
User enters Roll Width  = 1200 mm

Internal box_length_mm = 100000
Internal box_width_mm  = 1200

area_m2_per_roll = 100 * 1200 / 1000
area_m2_per_roll = 120 m2
```

## 4. RM Calculation

RM consumption should be area based.

For each paper layer:

```text
paper_kg_per_roll = effective_gsm * area_m2_per_roll / 1000
```

For fluted medium:

```text
medium_kg_per_roll = medium_gsm * flute_tuf * area_m2_per_roll / 1000
```

Example:

```text
roll_length_m = 100
roll_width_mm = 1200
area_m2_per_roll = 120

180 GSM liner:
180 * 120 / 1000 = 21.6 kg per roll

150 GSM medium with TUF 1.4:
150 * 1.4 * 120 / 1000 = 25.2 kg per roll
```

Potential additional RM:

- paper core
- roll wrapping
- label
- pallet or strap

These are not mandatory for the MVP. Later they should be modeled as optional style parameters, packaging, or consumable rules, not as required roll geometry.

## 5. Expected BoM Behavior

First-version BoM behavior should be:

- Product qty = 1 roll
- Product UoM can remain Units for now
- BoM attaches directly to the selected/spec finished roll product when 0100 is sellable FG
- Components are paper RM quantities per roll
- Operations come only from selected routing profile/stage contexts
- No box conversion operations should be implied
- Do not create a separate terminal SFG product unless explicitly configured later

Expected route examples:

```text
single facer / corrugation -> slitting -> rewinding -> packing
```

or:

```text
corrugation -> slitting -> rewind
```

If the route has only corrugation and rewind stages, the BoM operations should show only those selected operations.

## 6. FG, SFG, Or Roll-Stock Product

0100 can be any of these depending on business setup:

- FG: sellable roll product delivered to customer
- SFG: internal stockable roll used by later sheet cutting/conversion
- roll-stock material: stock input purchased or produced for later processes

Recommended first implementation:

```text
0100 = FG by default when created from a sellable Engine V2 spec
```

Rationale:

- matches the 0110 rule where the non-box output is the sellable product
- keeps BoM linkage clear
- avoids creating an unexpected terminal SFG product

Allow later configuration:

```text
product_role = fg | sfg
```

Do not create a separate terminal SFG for 0100 unless explicitly configured.

MVP exclusions:

- no partial rolls
- no mandatory core component
- no mandatory wrapping component
- no mandatory pallet component

## 7. Architecture Fit

Current Engine V2 architecture should support 0100 mostly unchanged if the style handler returns normalized area metadata.

Reusable:

- style registry
- style handler contract
- RM resolver area logic
- costing based on RM quantities
- preview model
- BoM materialization
- selected route operations

Needs confirmation before coding:

- whether `area_m2_per_fg` can safely drive existing `m2_per_box` paths for roll styles
- whether existing main geometry UI relabeling should exactly mirror the 0110 implementation
- whether future optional roll fields should be style params or family fields

Guardrail:

- no 0100-specific branching should be added to StageGraph, RM resolver, materialization writer, materialization resolver, multi-piece materialization, shared-upstream logic, or quantity planner unless a formal family contract expansion is approved.
- shared behavior must come through style family metadata, `StyleResult` metadata, the existing geometry contract, or routing/stage context metadata.

## 8. Questions Before Coding

Required decisions:

1. Which route stages should be expected in the first UI validation: corrugation, slitting, rewinding, packing?
2. Should roll length be production length before trimming or finished sellable length after trimming?
3. Should future roll core/wrap/trim options become dynamic style params or family-specific fields?
4. Should a later phase support product-role selection between FG and SFG?
5. Should a later phase support roll, meter, m2, or kg UoM instead of Units?

## 9. Recommended First-Version Scope

Recommended 0100 MVP:

- style code `0100`
- family `roll`
- no dynamic style params for roll length or roll width
- reuse `box_length_mm` as internal Roll Length storage after converting user-entered meters to millimeters
- reuse `box_width_mm` as Roll Width in millimeters
- hide / do not require `box_height_mm`
- Product qty = 1 roll
- UoM can remain Units for now
- UI/display terminology should say Roll
- 0100 defaults to FG when created from a sellable Engine V2 spec
- no box height/seam/flap requirement
- area calculation from width and length
- RM quantity per roll using existing stack logic
- any valid Engine V2 stack configuration is allowed; do not restrict 0100 to single-face rolls
- BoM attached to sellable roll product
- selected route operations only
- no partial-roll support
- no mandatory core/wrap/pallet components
- no terminal SFG unless explicitly configured later
- no 0100-specific logic leakage into StageGraph, RM resolver, materialization writer/resolver, multi-piece, or shared-upstream logic

Recommended test checklist:

- style handler test
- missing roll width validation
- missing roll length validation
- hidden/not-required height validation
- RM breakdown test for 1 roll
- BoM preview test
- actual BoM smoke test
- selected route operation test
- 0110 regression
- 0201/0202/0203 regression
- full Engine V2 suite

## 10. Detailed 0100 Implementation Plan

This plan is for the next coding phase only. Do not implement until explicitly approved.

### Files And Classes To Modify

Expected files:

- `custom_addons/corr_mrp_bridge/engine_v2/styles/style_0100.py`
- `custom_addons/corr_mrp_bridge/engine_v2/styles/__init__.py`
- `custom_addons/corr_mrp_bridge/models/engine_style.py`
- `custom_addons/corr_mrp_bridge/models/engine_v2_spec.py`
- `custom_addons/corr_mrp_bridge/spec_adapter.py`
- `custom_addons/corr_mrp_bridge/views/engine_v2_spec_views.xml`
- `custom_addons/corr_mrp_bridge/tests/test_engine_v2_style_0100.py`
- relevant existing regression tests for style framework, spec adapter, preview adapter, manufacturing plan, and BoM materialization

Expected classes/contracts:

- `StyleResult`
- `STYLE_REGISTRY`
- `corr.engine.style`
- `corr.engine.v2.spec`
- `EngineSpecInput`
- spec adapter geometry mapping

No first-version changes should be needed in:

- StageGraph
- RM resolver
- materialization writer
- materialization resolver
- multi-piece materialization
- shared-upstream logic
- quantity planner

### UI Changes

When selected style is 0100:

- show finished size terminology as Roll Size where possible
- show Length label as Roll Length
- show Length unit as meters
- show Width label as Roll Width
- show Width unit as mm
- hide Height
- do not require Height
- hide/avoid seam, glue flap, flap, and overlap requirements
- Style Parameters section should show no extra parameters required

Internal UI storage:

```text
box_length_mm = user_roll_length_m * 1000
box_width_mm = user_roll_width_mm
box_height_mm = ignored
```

The UI should avoid showing Roll Length as 100000 mm because that is not usable for roll-product entry.

### Validation Rules

Required:

- Roll Length in meters must be greater than 0.
- Roll Width in mm must be greater than 0.
- Height must not block save/preview.
- Height must be hidden and ignored.
- Seam must not be validated.
- Glue flap must not be validated.
- Overlap must not be validated.
- Box-panel rules must not be validated.

Recommended error wording:

- Missing length: Roll Length is required.
- Missing width: Roll Width is required.

Do not validate first-version 0100 against:

- roll diameter
- core diameter
- partial roll length
- roll weight limits
- pallet quantity
- wrapping/core settings

### Style Handler Changes

Add `style_0100.py`.

Handler input:

```text
GeometrySpec.box_length_mm = internal roll length in mm
GeometrySpec.box_width_mm  = roll width in mm
```

Handler formula:

```text
roll_length_m = box_length_mm / 1000
roll_width_mm = box_width_mm

blank_length_mm = box_length_mm
blank_width_mm  = box_width_mm
area_m2_per_fg  = roll_length_m * roll_width_mm / 1000
```

Expected metadata:

```python
{
    "style_code": "0100",
    "family": "roll",
    "uom_basis": "roll",
    "roll_length_m": 100,
    "roll_width_mm": 1200,
    "area_m2_per_fg": 120,
    "requires_box_height": False,
    "uses_seam": False,
    "uses_flaps": False,
}
```

Register handler in `STYLE_REGISTRY`.

Add active `corr.engine.style` record for 0100:

- code: `0100`
- name: Roll Product
- family: roll
- active: true

Do not add required 0100 style params for roll length or roll width.

The style handler should work with normalized geometry only. UI-specific meter-to-millimeter conversion should happen before the handler where practical, so the handler receives `GeometrySpec.box_length_mm` and `GeometrySpec.box_width_mm` in the existing geometry contract.

### RM And BoM Preview Expectations

RM preview should:

- calculate paper by roll area
- show quantities per roll
- use the selected board stack/ply structure
- support any valid Engine V2 stack configuration already supported by the stack engine
- show roll terminology where UI supports it
- not require layout options intended for cartons

Example expectation:

```text
Roll Length = 100 m
Roll Width = 1200 mm
Area = 120 m2

180 GSM liner:
120 * 180 / 1000 = 21.6 kg per roll
```

BoM preview should:

- Product qty = 1 roll
- components = paper RM per roll
- operations = selected route/stage contexts only
- no unexpected printing operation unless selected route includes printing
- no box/seaming operation unless selected route includes it
- no generated terminal SFG unless explicitly configured later

### BoM Materialization Behavior

First-version materialization should follow 0110 terminal semantics:

- 0100 is sellable FG by default when created from a sellable Engine V2 spec.
- BoM attaches directly to the spec finished product/template.
- Product smart button should show the created BoM.
- Product UoM can remain Units for MVP.
- BoM product quantity is 1 roll.
- Do not create a separate terminal SFG product for 0100 unless future configuration explicitly asks for SFG output.

If route has material stages only, the final material output should be treated as the FG roll output for sellable specs.

### Tests To Add

Add focused tests:

- `test_style_0100_uses_main_geometry_length_width`
- `test_style_0100_converts_internal_length_mm_to_roll_length_m`
- `test_style_0100_area_formula`
- `test_style_0100_rejects_missing_roll_length`
- `test_style_0100_rejects_missing_roll_width`
- `test_style_0100_does_not_require_height`
- `test_style_0100_has_no_required_style_params`
- `test_0100_spec_adapter_converts_roll_length_m_to_box_length_mm` if UI/adapter conversion is implemented outside the handler
- `test_0100_preview_rm_breakdown_uses_roll_area`
- `test_0100_bom_preview_product_qty_one_roll`
- `test_0100_actual_bom_attaches_to_sellable_roll_product`
- `test_0100_selected_route_operations_only`
- `test_0100_no_printing_operation_without_printing_stage`
- `test_0100_allows_2ply_stack`
- `test_0100_allows_3ply_stack`
- `test_0100_allows_5ply_stack`
- `test_0100_allows_7ply_stack`
- `test_0100_does_not_restrict_stack_family`

Regression tests:

- 0110 still uses main length/width as sheet dimensions.
- 0110 still hides/ignores height.
- 0201 unchanged.
- 0202 unchanged.
- 0203 unchanged.
- style registry still resolves all active styles.
- dynamic style params still work for 0202.

### Regression Checklist

Before marking 0100 complete:

- style handler tests pass
- spec adapter tests pass
- preview geometry tests pass
- RM breakdown tests pass
- BoM preview tests pass
- actual BoM smoke test passes
- product BoM smart button links to sellable roll product
- selected route operations match selected route exactly
- no unexpected Printing operation appears
- 0110 UI/regression still passes
- 0201/0202/0203 regressions pass
- full Engine V2 suite passes

### Leakage Guardrails

Do not add `0100` style-specific logic to:

- StageGraph
- RM resolver
- materialization writer
- materialization resolver
- multi-piece materialization
- shared-upstream split logic
- quantity planner

Allowed shared changes:

- family-aware UI labels
- generic non-box family metadata
- generic terminal-product semantics for roll/sheet families if implemented through style family metadata, not hardcoded style code checks

Any required shared behavior must be expressed through:

- `corr.engine.style.family`
- `StyleResult.metadata`
- existing geometry fields
- selected route/stage context data

## 11. Final Implementation Approval Summary

0100 MVP is approved for implementation planning and coding with these decisions:

- 0100 is a Roll Product style.
- 0100 defines roll output geometry only.
- 0100 does not define or restrict board construction.
- Any valid Engine V2 stack configuration supported by the stack engine is allowed.
- User enters Roll Length in meters.
- User enters Roll Width in mm.
- Internal storage reuses `box_length_mm = roll_length_m * 1000`.
- Internal storage reuses `box_width_mm = roll_width_mm`.
- `box_height_mm` is hidden and ignored.
- Product qty basis is 1 roll.
- UoM can remain Units for MVP.
- 0100 defaults to FG when created from a sellable Engine V2 spec.
- No terminal SFG is created unless explicitly configured later.
- No partial rolls in MVP.
- No mandatory core/wrap/pallet components in MVP.
- Operations must come only from selected route/stage contexts.
- No 0100-specific logic should leak into StageGraph, RM resolver, materialization writer/resolver, multi-piece logic, shared-upstream logic, or quantity planner.

Implementation can proceed once coding is explicitly requested.

## 12. Do Not Implement Until Requested

0100 MVP is ready for implementation planning and coding. Do not code it until implementation is explicitly requested.



