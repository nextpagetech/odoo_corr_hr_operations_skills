# ENGINE V2 â€“ CURRENT IMPLEMENTATION CONTEXT
**Date:** 2026-04-09  
**Phase:** Routing activation audit complete â†’ next phase is generic stage execution design

---

# 1. Current Objective

We paused implementation to first understand:

- what is already implemented in Engine V2
- what is only partially implemented
- what is still missing for true dynamic routing
- what should be done next without breaking the current stable work

This file is the continuation context for the next chat.

---

# 2. What We Have Completed So Far

## A. Core Engine Stability
Engine V2 core is stable enough for structured continuation.

Completed earlier:
- Engine V2 core stabilized
- Preview â†’ BoM flow working
- material resolution working
- StageGraph base exists
- top sheet unit-sheet model implemented
- top sheet roll-mode issue fixed
- spec/profile pattern stable

---

## B. Top Sheet Handling
### Unit Sheet Mode
Implemented:
- product-first unit sheet behavior
- quantity based on required sheets
- preview / BoM / costing alignment

### Roll Mode
Implemented and fixed:
- top roll derived from stack top liner logic
- rm_top_roll emitted correctly
- no dependency on manually entered GSM alone
- stack-driven behavior working

---

## C. Consumables / Adhesive Layer
Implemented:
- consumable framework
- adhesive 3-stage model
- Create-BoM adhesive mapping fix
- adhesive validation work completed to a useful level

Business intent already established:
- corrugation adhesive
- board adhesive
- FG adhesive
- stage-wise allocation should be correct
- if a stage does not exist, merge into nearest valid stage

---

## D. Stage Template Master / Routing Profile UI Fix
This was an important correction.

Now implemented:
- routing profile stage lines use **Engine Stage Templates**
- user selects stage from dropdown
- user no longer manually types raw stage codes
- internal stage code comes from selected stage template
- stage type is auto-derived from stage template
- backfill/migration exists for old rows using raw stage_code

This is the correct design direction.

---

# 3. Routing Activation â€“ What Was Implemented

## A. Routing now enters EngineSpecInput
Implemented:
- `build_engine_spec_input()` now reads routing profile
- routing stage lines flow into engine input
- stage template code/type are used internally

Meaning:
- routing is no longer UI-only
- engine can now see routing information

---

## B. StageGraph is now routing-aware
Implemented:
- when routing_profile exists, routing stage order is used
- routing can have any number of stages in any order
- material stages become StageGraph nodes
- operation stages attach to nearest material stage
- plant fallback is used only when routing is absent

Important:
- in routing-enabled mode, Codex says default stage injection like `["2ply","board","fg"]` is no longer forced
- in legacy/no-routing path, canonical fallback still exists

---

## C. RM placement became more routing-aware
Implemented partially/improved:
- liner/medium are assigned based on available material stages
- top roll / top sheet / ink try to attach around printing ownership or board-like stage
- adhesives merge to nearest valid material stage if preferred stage is absent
- routing-aware stage assignment is better than before

This is progress, but still not fully generic.

---

# 4. Current Audit Result â€“ Exact Status

After asking Codex for a current-state audit, the result is:

## Already Implemented
- routing profile can have **any number of stages in any order**
- routing profile is integrated with Stage Templates
- StageGraph uses routing when routing exists
- material vs operation distinction exists
- StageGraph linear flow follows routing material stage order
- RM placement is partially routing-aware
- backward compatibility path still exists for no-routing legacy behavior

## Partially Implemented
- stage-to-stage chain exists only as a **linear material StageGraph**
- operations are attached under material stages
- printing-first style routes are partially handled
- no-2ply route is partially handled
- single-stage FG route is partially handled
- BoM operation order is influenced by routing, but not truly 1:1 routing-driven

## Not Yet Implemented
- true generic stage-by-stage execution model
- generic per-stage input/output semantics
- generic stage-local RM/consumable rules from stage definitions
- quantity graph driven by routing/stage templates
- 1:1 routing stage â†’ BoM/workorder
- explicit intermediate stage outputs for arbitrary flows
- capability-based routing behavior instead of canonical assumptions

---

# 5. Most Important Current Insight

## Engine V2 today is:
**routing-aware but still canonical**

That means:
- it respects routing better than before
- but internally still uses canonical ideas like:
  - corrugation
  - board
  - printing
  - FG

It is **not yet** a fully generic stage-execution engine.

This is the most important conclusion.

---

# 6. Business Requirement Confirmed

The engine must support dynamic manufacturing flows like:

## Example 1
- board: liner + medium + gum + top sheet + gum -> board
- printing: board output + ink -> printed board
- seaming: printed board output + adhesive/wire -> FG

## Example 2
- board: liner + medium + gum + top sheet + gum -> board
- seaming: board output + adhesive/wire -> FG

## Example 3
- cutting: top paper roll -> cutting paper
- board: liner + medium + gum + cutting paper + gum -> board
- seaming: board output + adhesive/wire -> FG

## Example 4
- cutting: top paper roll -> cutting paper
- printing: cutting paper + ink -> printed sheets
- board: liner + medium + gum + printed sheets + gum -> board
- seaming: board output + adhesive/wire -> FG

## Example 5
- cutting: top paper roll -> cutting paper
- printing: cutting paper + ink -> printed sheets
- 2ply: liner + medium + gum -> 2ply board
- board: 2ply board + printed sheets + gum -> board
- seaming: board output + adhesive/wire -> seamed box
- manual folding: seamed box -> FG

## Example 6
- cutting: top paper roll -> cutting paper
- printing: cutting paper + ink -> printed sheets
- 2ply: liner + medium + gum -> 2ply board
- board: 2ply board + printed sheets + gum -> board
- manual gluing: board output + adhesive/wire -> seamed box
- manual folding: seamed box -> FG

## Example 7
- cutting: top paper roll -> cutting paper
- printing: cutting paper + ink -> printed sheets
- 2ply: liner + medium + gum -> 2ply board
- board: 2ply board + printed sheets + gum -> board
- manual gluing: board output + adhesive/wire -> FG

## Example 8
- FG: all required components (liner, medium, top sheets/roll, gum, stitching wire etc.) -> FG

These are valid requirements and must be supported.

---

# 7. Correct Target Architecture

The correct target behavior is:

For each routing stage in order:

- stage takes **previous stage output** if needed
- stage takes **local RM / consumables** if needed
- stage performs its own transformation
- stage produces **output for next stage**
- final stage produces **FG**

So the engine should support:

- arbitrary number of stages
- arbitrary stage order
- stages with only previous-output input
- stages with only fresh RM input
- stages with both
- stages with no new RM
- final stage decided by routing behavior, not hardcoded stage names

---

# 8. Important Clarifications Reached

## A. StageGraph today
Current StageGraph mainly does:
- build material-stage backbone
- attach operation stages under material stages
- provide linear stage sequence
- help RM grouping

So StageGraph is useful, but it is not yet the full generic execution layer.

## B. Stage Type meaning
Current practical meaning:
- `material` = main stage node
- `operation` = process step attached to material stage

This is useful but too simple for long-term full generic modeling.

## C. Better future direction
We should separate:
- **execution type**: material / operation
- **business capability / role**: cutting / printing / corrugation / board / gluing / seaming / folding / finished goods / etc.

This will avoid overloading one field.

---

# 9. What Is Still Missing

To support the real target model, the next missing architecture is:

## Generic Stage Contract
Each stage should eventually define:

- stage identity
- execution class (material / operation)
- business capability / role
- allowed input kinds
- allowed local RM / consumables
- output kind
- whether it can be final / terminal stage

Without this, the engine remains partially hardcoded.

---

# 10. Recommended Next Phase

## Next phase should NOT be random bug fixing.
Next phase should be:

# Phase E.4 / Next Architecture Phase
## Generic Stage Execution Design

We should ask Codex to design first, not implement immediately.

Design should cover:

### 1. Stage contract / capability model
Define for each stage:
- allowed inputs
- local RM/consumables
- output kind
- terminal/final-output flag
- execution type

### 2. How this affects engine layers
- StageGraph
- quantity graph
- RM attachment
- BoM operations / workorders

### 3. Safe phased migration
Move from current:
- routing-aware but canonical

To future:
- fully generic stage-by-stage execution engine

---

# 11. What To Tell The Next Chat / Codex

Use this as the continuation summary:

## Current truth
- Engine V2 routing profile + stage template integration is implemented
- routing-aware StageGraph is implemented
- RM placement is improved and partially routing-aware
- but Engine V2 is still **routing-aware but canonical**
- it is not yet a true generic stage execution engine

## Next focus
Design the next architecture for:
- generic stage contract
- explicit stage input/output chaining
- stage-local RM rules
- routing-driven quantity graph
- 1:1 routing stage â†’ BoM/workorder where appropriate

## Important instruction
Do not jump into implementation immediately.
First produce a design proposal for the missing generic stage-execution layer.

---

# 12. Suggested Prompt For Next Chat

```text
Use the attached Engine V2 context file fully before doing anything.

Current Engine V2 status:
- core is stable
- top sheet unit-sheet and roll-mode are working
- routing profile now uses Stage Templates
- routing is wired into EngineSpecInput
- StageGraph is routing-aware
- RM stage assignment is improved

However:
- Engine V2 is still routing-aware but canonical
- it is NOT yet a fully generic stage-by-stage execution engine

Business requirement:
Engine must support arbitrary manufacturing flows such as:
- board -> printing -> seaming
- board -> seaming
- cutting -> board -> seaming
- cutting -> printing -> board -> seaming
- cutting -> printing -> 2ply -> board -> seaming -> manual folding
- single-stage FG
and similar dynamic flows with any number of stages and any order.

Target behavior:
For each routing stage:
- take previous stage output if needed
- take local RM / consumables if needed
- perform stage transformation
- produce output for next stage
- final stage produces FG

Your task now:
1. Do a design proposal for the missing generic stage execution layer
2. Define a generic stage contract model:
   - execution type
   - business capability/role
   - allowed inputs
   - local RM/consumables
   - output kind
   - terminal/final-output flag
3. Explain how this should affect:
   - StageGraph
   - quantity graph
   - RM attachment
   - BoM/workorder generation
4. Propose safe phased implementation from current state to target state

Do not implement immediately.
First provide the architecture/design proposal only.
```

---

# 13. Final Summary

## Done
- core stabilized
- top sheet handling working
- routing profile uses stage templates
- routing enters engine input
- StageGraph is routing-aware
- RM placement improved

## Current truth
- implementation is better
- but still partially canonical

## Next
- define generic stage-execution architecture
- then implement in safe phases

---

**End of context**



