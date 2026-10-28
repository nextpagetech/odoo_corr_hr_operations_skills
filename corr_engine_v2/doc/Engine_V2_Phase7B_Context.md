# Engine V2 â€“ Phase 7B Context

## Current Status
Engine V2 has successfully completed:
- Dynamic routing
- StageContext runtime
- Preview backend migration
- Shadow BoM backend
- Layout/slitting feasibility foundation
- Exact RM resolution logic
- Workcenter flute validation
- Top-sheet mode normalization
- Costing summary foundation

Current active milestone:
# Phase 7B â€“ Costing UX + Manufacturing Architecture Preparation

---

# Completed Major Areas

## 1. StageContext Runtime
- StageContext is now authoritative runtime layer.
- Preview and Shadow BoM both consume StageContext.
- Routed stages validated across multiple route profiles.
- Route source priority implemented and validated.

## 2. Routing + Operations
Validated:
- 2ply â†’ board â†’ seaming
- Printing before board
- Single-stage automated flow
- Multi-stage dynamic routes

Operation display names now follow routed stage template names.

---

# 3. Layout / UPS / Feasibility

## Implemented
- Workcenter-based feasibility
- Flute capability filtering
- Min/max width validation
- Orientation support
- UPS option generation
- Selected layout persistence

## Fixed
- Invalid UPS values filtered
- Wrong flute workcenters excluded
- Workcenter names removed from UI
- Preferred vs selected layout separation

## Remaining Future Work
- Half-up / 2-piece manufacturing
- Uneven slitting
- Multi-machine optimization
- Cost-aware feasibility ranking

---

# 4. RM Resolution

## Implemented
- Exact deckle matching
- No wider-roll fallback in preview
- Missing/Unresolved state when exact variant missing
- Selected layout drives RM resolution
- Separate RM rows per role/layer

## Top Sheet Logic

### Roll Mode
Expected:
- liner
- medium
- top_roll

### Unit Sheet Mode
Expected:
- liner
- medium
- top_sheet_external

Now generalized for:
- 3ply
- 5ply
- 7ply
- N-ply

Removed:
- duplicate liner rows
- phantom external sheet rows
- zero-quantity rows

---

# 5. Workcenter Logic

## Implemented
- Flute support validation
- Multi-flute support
- Auto WC assignment toggle capability design
- WC filtering during feasibility

## Future
- Multi-corrugator split routing
- Stage-level WC recommendations
- Runtime WC balancing

---

# 6. Costing

## Implemented
- Cost source selection:
  - standard_price
  - last_purchase_price
- Plant rounding digits integration
- Raw material total
- Operation cost total
- Final manufacturing cost
- Cost per unit

## New UX Direction
Stage-wise costing table replaced conceptually by:

### Raw Material Cost Summary
Single total with detail popup:
- layer
- qty
- UoM
- cost source
- unit cost
- total

### Operation Cost Summary
Uses Plant operation costing table:
- machine cost
- labor cost
- quantity basis

### Final Manufacturing Cost
- RM total
- operation total
- manufacturing total
- cost per unit

## Future Costing
Potential future cost sources:
- moving average
- forecast purchase cost
- supplier quotations
- simulation costing

---

# 7. Shadow BoM

## Implemented
- StageContext-driven BoM
- Correct operation generation
- Correct consumed-in-operation mapping
- Legacy row cleanup
- Stable component keys

## Current Limitation
Single MO / WO chain only.

---

# Next Major Milestone

# Phase 7B â€“ Manufacturing Architecture Expansion

## Goal
Support:
- Stage-wise SFG flow
- Multi-level manufacturing
- Inter-stage semi-finished goods
- Recursive production chains

---

# Planned Manufacturing Models

## Current
Single BoM:
RM â†’ operations â†’ FG

## Future
Example:
2ply SFG â†’ board SFG â†’ FG

### Example Chain
MO1:
liner + medium â†’ 2ply board

MO2:
2ply board + top sheet â†’ board

MO3:
board â†’ diecut/seaming â†’ FG

---

# Future Areas

## Phase 7C
- Runtime simulation
- Multi-option costing comparison
- Capacity-aware routing

## Phase 8
- Routing templates
- Plant defaults
- Suggested manufacturing strategies

## Phase 9
- AI/suggestion engine
- Scrap optimization
- Alternative manufacturing proposals

---

# Important Design Decisions

## Preview Philosophy
Preview should:
- never silently substitute
- never hide missing variants
- always reflect exact manufacturing intent

## Manufacturing Philosophy
Manufacturing may later:
- allow deviations
- allow wider substitutions
- allow operator overrides

---

# Key Technical Files

## Runtime
- engine_v2_runtime.py
- engine_v2/__init__.py
- quantity_planner.py

## Preview
- preview_adapter.py

## BoM
- bom_adapter.py

## Feasibility
- layout.py
- slitting.py

## Routing
- stage_graph.py

---

# Important Current Concerns

## Still Legacy-Influenced
- stage_graph quantity chain
- some preview aggregation
- some operation family normalization

## Must Avoid
- hardcoded stage names
- hardcoded role fallbacks
- hidden substitutions
- stage-family assumptions

---

# Recommended Next Session Start Prompt

â€œRead this entire Phase7B context first before implementing anything. Continue from Phase 7B manufacturing architecture planning. Do not modify costing logic unless requested.â€



