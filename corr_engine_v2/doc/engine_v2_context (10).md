# Engine V2 â€“ Context File (Latest)

## Project: Corrugation Engine V2 (Odoo)

## Current Status
Engine V2 is now in **Phase 4 (Quantity Planner â€“ Shadow Mode)**  
Core architecture is stable and validated through multiple test scenarios.

---

## COMPLETED PHASES

### Phase 1: Stage Contract & Taxonomy
- Stage Template extended with contract fields
- Supporting masters created
- Seeder + normalization implemented
- Backward-compatible (no preview impact)

### Phase 2: StageContext (Runtime Model)
- StageContext introduced
- Debug-only exposure in raw_json
- Routing profile alignment fixed

### Phase 2.5: Cleanup Patch
- Route source fix
- Contract validator
- Seeder wizard
- Parent-material semantics cleanup

### Phase 3: RM Attachment (Shadow Mode)
- Contract-driven RM/consumable attachment
- StageContext enriched with attachments + warnings
- Ownership validated:
  - 2ply â†’ liner
  - board â†’ top sheet
  - ts_print â†’ ink
  - seaming â†’ FG adhesive

### Phase 4: Quantity Planner (Shadow Mode)
- Implemented quantity planner
- Added StageContext quantity fields
- Added quantity_report in raw_json

---

## VALIDATED SCENARIOS

### Simple Case
- order_qty = 1
- Correct chaining and quantities

### Non-trivial Case
- order_qty = 1234
- boxes_per_sheet = 6
- required_sheets = 206
- All stage quantities correct

---

## CURRENT STATE

Shadow engine supports:
- routing
- stage contracts
- RM attachment
- quantity planning
- chaining
- output descriptors

All visible in:
raw_json.phase8_shadow_runtime

Preview/BoM still use old engine.

---

## PENDING / NEXT STEPS

### Optional
- Validate split scenario (pieces_per_box = 2)

### Phase 5: Preview Switch
- Move preview to StageContext + quantity planner

### Phase 6: BoM Switch
- Generate BoM from StageContext

### Phase 7: Costing
- Align costing with new quantity model

---

## DESIGN PRINCIPLES

- Shadow-first implementation
- StageContext = future source of truth
- Routing-driven execution
- Contract-driven behavior

---

## NEXT SESSION PROMPT

Use the attached Engine V2 context.

Current status:
- Phase 4 implemented and validated
- Shadow engine working
- Preview/BoM still old engine

Next step:
Decide:
1) Validate split scenario
2) Start Phase 5 (Preview Switch design)

Do not implement yet. First review readiness.



