# ENGINE V2 â€“ PHASE 8 CONTEXT FILE

## Project: Corrugation Engine V2  
## Current Stage: Transition from Phase 7.5 â†’ Phase 8 (Production Architecture)

---

# 1. CURRENT STATUS

## Phase 6 â€“ COMPLETE
- Quantity rollup stable
- Material costing implemented
- Variant-based costing working
- No regression in BoM or quantity logic

## Phase 7 â€“ COMPLETE (Baseline)
- Operation costing (per_unit) implemented
- Stage-wise costing added (preview-only)
- Total production cost computed
- RM Breakdown, Stage Costing, Stage Flow, BOM Preview consistent

## Phase 7.5 â€“ COMPLETE (Bridge Improvements)
- Top-sheet routing fixed (in-house vs external)
- Dynamic stage_key mapping improved
- Stage material cost aggregation fixed
- Tests passing
- Operation cost config supports dynamic stage keys

---

# 2. KNOWN LIMITATIONS

- Stage Costing uses quantity buckets (raw_material, board, sheet, blanks, finished_goods)
- Dynamic stages are folded into buckets
- UI is not process-stage-first yet
- Per-hour costing not implemented

---

# 3. OBJECTIVE

Build Engine V2 as a production-ready dynamic flow engine.

---

# 4. TARGET CAPABILITIES

- Standard Flow
- Printing Before Board
- Fully Automated Plant
- Semi-Automated Plant
- Optional Stages (film lamination, etc.)
- Future unknown flows

---

# 5. CORE PRINCIPLE

Engine should not know scenarios.  
Engine should know stages + rules + mappings.

---

# 6. PHASE 8 PLAN

## Stage Definition Model
- stage_code
- stage_type
- sequence
- quantity_basis
- cost_basis
- optional/required

## Routing Model
- dynamic stage sequences
- optional/missing/merged stages

## Material Attachment
- rule-driven stage attachment

## Quantity Model
- internal graph
- stage-level basis

## Costing Model
- stage-level costing
- support future costing methods

## Preview/UI
- process-stage-first view
- quantity buckets as debug

## BoM Translation
- map stages to operations

## Extensibility
- support new stages without code changes

---

# 7. CONSTRAINTS

- Do not break Phase 6
- Do not break quantity logic
- Do not break BoM
- Keep backward compatibility

---

# 8. NEXT TASK

Start Phase 8 with architecture planning and controlled implementation.



