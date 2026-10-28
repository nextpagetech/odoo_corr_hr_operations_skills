
# Corrugation BOM Engine V2 â€” WORKING CONTEXT

## PURPOSE
We are replacing the legacy BOM calculation logic with a new flexible **Engine V2**.

Engine V2 is a **pure Python manufacturing computation engine** responsible for:

- Layout calculation
- Split / slitting logic
- RM consumption
- Stage flow (StageGraph)
- Quantity propagation
- BOM structure preview

Odoo is only used for:
- Input capture
- Preview (Debug Wizard)
- Final BoM / routing record creation

Engine must remain decoupled from Odoo.

---

## CURRENT STATUS (ALREADY IMPLEMENTED)

- Engine V2 compute pipeline exists
- Style â†’ Split â†’ Layout â†’ RM â†’ StageGraph â†’ Result flow exists
- Debug Wizard preview implemented
- Supports multiâ€‘piece logic conceptually
- StageGraph drives quantity propagation
- BOM adapter exists (preview + creation boundary)
- UOM warning preview exists

---

## ARCHITECTURE PRINCIPLES (FROZEN)

We are moving from:

âŒ Fieldâ€‘driven legacy spec  
âœ… Profileâ€‘driven manufacturing engine

Engine input must be structured into domains:

1. Geometry Spec
2. Board Recipe
3. Surface Profile
4. Layout Policy
5. Routing Profile
6. Plant Profile
7. Optional Overrides

---

## SPEC (PRODUCT / JOB DEFINITION)

Spec represents only:

- style_id
- box_length_mm
- box_width_mm
- box_height_mm
- glue_flap_override_mm
- plant_id

Profile references:

- board_recipe_id
- surface_profile_id
- layout_policy_id
- routing_profile_id

Overrides:

- manual_sheet_length_mm
- manual_sheet_width_mm
- forced_ups
- ups_enforcement (soft / hard)

Spec must support **editable board layer lines**.

---

## BOARD RECIPE (DEFAULT TEMPLATE)

Reusable corrugated board construction template.

Contains:

- sequence
- role (outer_liner / medium / inner_liner etc)
- gsm
- bf
- color
- flute_type (for medium)
- optional material mapping

Behavior:

- Selecting recipe copies lines into spec
- Spec lines remain editable
- Manual board definition allowed
- Recipe change must NOT autoâ€‘update existing specs

---

## SURFACE PROFILE

Defines surface treatment:

- printing_required
- print_process
- print_colors
- print_coverage_percent

Top sheet:

- top_sheet_mode (none / roll / unit_sheet)
- top_sheet_product_id

Lamination:

- lamination_required
- lamination_type
- lamination_side

---

## LAYOUT POLICY

Controls layout behavior.

Fields:

- allow_rotation
- preferred_ups
- max_ups
- layout_mode (strict / optimize / prefer_target)
- sheet_source (manual / plant_default / derived)
- trim_margin_mm
- gripper_margin_mm

UPS behavior:

- soft â†’ engine may optimize
- hard â†’ engine must respect UPS

---

## ROUTING PROFILE

Defines manufacturing stage flow.

Contains ordered stage list:

- stage_code
- stage_type (material / operation)
- optional waste_percent

Any number of routing profiles supported.

---

## PLANT PROFILE

Defines physical constraints:

- max_machine_width_mm
- split_mode (none / equal_only / uneven_allowed)
- min_piece_width_mm
- max_piece_width_mm
- default_sheet_behavior

---

## SPLITTING BEHAVIOR (CONFIRMED)

Engine V2 must support:

- Normal box (no split)
- Equal split (2â€‘piece)
- Controlled uneven slitting (2â€‘piece initially)

Recommended split strategy:

1. Try no split
2. Try equal split
3. Try uneven split if required

---

## SHEET SOURCE MODES

Manual â†’ user enters sheet  
Plant default â†’ take plant sheet  
Derived â†’ engine optimizes layout  

---

## DEBUG WIZARD ROLE

Wizard is the **manufacturing preview console**.

Must show:

- blanks_per_sheet
- sheets_required
- split result
- RM breakdown
- stage flow
- BOM preview
- warnings
- raw JSON
- trace (future enhancement)

Wizard must NEVER create records.

---

## NEXT IMPLEMENTATION PHASE

1. Implement masters:
   - board_recipe
   - surface_profile
   - layout_policy
   - routing_profile

2. Update spec UI + relationships

3. Implement recipe â†’ spec line copy logic

4. Upgrade adapter to build clean EngineSpecInput

5. Enhance engine compute:
   - board recipe handling
   - uneven split rules
   - layout policy behavior
   - surface RM logic
   - routing profile StageGraph

6. Validate with real jobs:
   - simple box
   - 2â€‘piece oversized box
   - printed + laminated job

7. Add automated tests

8. Add â€œCreate BOM from Previewâ€ flow

---

## LONG TERM VISION

Engine V2 should enable:

- costing engine
- waste optimization
- routing simulation
- material substitution simulation
- plant capacity planning
- intelligent layout optimization

Engine V2 becomes manufacturing brain â€” not just BOM calculator.


