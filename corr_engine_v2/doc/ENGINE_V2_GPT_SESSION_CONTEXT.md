# ENGINE V2 â€“ GPT SESSION CONTEXT (PHASE 8)

## Project: Corrugation Engine V2  
## Purpose: Architecture Design (Production-Ready System)

---

# 1. CURRENT STATUS

## Phase 6 â€“ COMPLETE
- Quantity engine stable
- Material costing implemented
- Variant-based costing working
- No regression in BoM or quantity logic

## Phase 7 â€“ COMPLETE
- Operation costing (per_unit) added
- Stage-wise costing available (preview)
- Total production cost working
- RM Breakdown, Stage Costing, Stage Flow, BOM Preview aligned

## Phase 7.5 â€“ COMPLETE
- Top-sheet routing fixed
- Dynamic stage mapping improved
- Cost aggregation corrected
- Tests passing (engine + preview)

---

# 2. CURRENT LIMITATIONS

- UI uses quantity buckets instead of real process stages
- Dynamic stages (2ply, ts_print, fg) are mapped internally
- Not yet process-stage-first system
- Per-hour costing not implemented

---

# 3. OBJECTIVE

Design Engine V2 as a **production-ready dynamic workflow engine**

---

# 4. DESIGN GOAL

Engine should:
- Support ANY manufacturing flow
- Not depend on predefined scenarios
- Be fully configurable and extensible

---

# 5. REQUIRED CAPABILITIES

Must support:
- Standard multi-stage flow
- Printing before board
- Fully automated plant (single stage)
- Semi-automated plants
- Optional stages (film lamination, peeloff, QC)
- Future unknown flows

---

# 6. CORE PRINCIPLE

Engine should NOT know scenarios  
Engine should know stages + rules + mappings

---

# 7. ARCHITECTURE REQUIREMENTS

## Stage Layer
- dynamic stage definition
- configurable attributes
- input/output logic

## Routing Layer
- flexible stage sequencing
- optional/missing stages
- plant-specific flows

## Material Layer
- rule-based attachment
- no hardcoding

## Quantity Layer
- internal calculation support
- not user-facing

## Costing Layer
- stage-level costing
- extensible (future models)

## Preview Layer
- process-stage-first UI
- business-friendly output

## BoM Layer
- stage-to-operation mapping
- stable integration

---

# 8. CONSTRAINTS

- Do NOT break Phase 6 logic
- Do NOT break quantity logic
- Do NOT break BoM creation
- Maintain backward compatibility

---

# 9. EXPECTATION FROM GPT SESSION

Design a production-ready architecture including:

- Stage model
- Routing model
- Material attachment model
- Quantity model
- Costing model
- UI/preview model
- BoM translation model

Also include:
- data flow
- extensibility strategy
- migration risks

---

# 10. IMPORTANT

- Do NOT write code
- Focus on architecture only
- Ensure future-proof design



