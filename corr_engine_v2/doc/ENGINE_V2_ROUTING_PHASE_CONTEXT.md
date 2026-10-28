# ðŸš€ ENGINE V2 â€“ ROUTING PHASE CONTEXT (PHASE E.3)

## ðŸ“Œ Current Status

We have successfully completed:

### âœ… Phase D â€“ Core Validation & Stability
- Engine V2 core stabilized
- StageGraph base structure working
- Preview â†’ BoM flow validated
- Material resolution (liner/medium) correct

---

### âœ… Phase E.1 â€“ Top Sheet Model

#### Unit Sheet Mode
- Product-first model implemented
- Sheet-based quantity:
  required_sheets = ceil(order_qty / boxes_per_sheet)
- Spec-driven product selection
- Preview, BoM, costing aligned

#### Roll Mode (FIXED)
- Top roll derived from stack (top liner)
- rm_top_roll emitted correctly
- kg/area-based quantity
- No dependency on manual GSM only

---

### âœ… Spec & Surface Profile Design

- Surface Profile is optional
- Acts as default template
- Spec is final source of truth
- Auto-fill + override working

---

## âš ï¸ Current Limitation

Routing is NOT yet fully active.

### Observed Issue
- Changing routing profile does NOT affect:
  - RM Breakdown stages
  - Stage Flow
  - BoM stage mapping

### Current Behavior
- Debug wizard uses legacy spec path
- routing_profile is NOT used in EngineSpecInput
- StageGraph falls back to:
  ["2ply", "board", "fg"]

---

## ðŸŽ¯ Goal â€“ Phase E.3 (Routing Activation)

Make routing **authoritative** for:

- RM stage assignment
- StageGraph construction
- BoM operation stages

---

## ðŸ”§ Required Implementation

### 1. Wire Routing Profile into Engine Input

- Ensure EngineSpecInput includes:
  routing_profile from Engine V2 Spec

- Modify:
  build_engine_spec_input()
  to include routing_profile_id

---

### 2. StageGraph must use routing profile

Current:
- fallback to plant.stages

Required:
- build StageGraph from routing_profile
- no implicit default stages

---

### 3. RM Stage Assignment

- RM Breakdown â€œStageâ€ must come from routing profile
- Example:
  if no 2ply â†’ no RM assigned to 2ply

---

### 4. BoM Operations Alignment

- BoM operations must match routing stages exactly
- No extra stages
- No missing stages

---

### 5. Support Flexible Scenarios

Must support:

#### Scenario 1 (Standard)
2ply â†’ board â†’ printing â†’ diecut â†’ fg

#### Scenario 2 (Printing First)
cutting â†’ printing â†’ corrugation â†’ board â†’ diecut â†’ fg

#### Scenario 3 (Fully Automated)
single stage â†’ all inputs

---

### 6. Backward Compatibility

- If no routing_profile:
  fallback to current default stages
- Existing specs must continue working

---

## ðŸ§ª Validation Required

### Test 1: Standard route
- confirm all stages appear correctly

### Test 2: No 2ply route
- no RM assigned to 2ply
- adhesive merged correctly

### Test 3: Custom route
- stages appear in correct order

### Test 4: Unit sheet + routing
- ensure no regression

### Test 5: Roll mode + routing
- ensure rm_top_roll still correct

---

## âš ï¸ Constraints

- Do NOT break:
  - top sheet behavior (roll + unit)
  - spec/profile system
  - preview â†’ BoM consistency

- Scope routing activation ONLY
- Do not implement new consumable features yet

---

## ðŸ§­ Implementation Order

1. Inject routing_profile into EngineSpecInput
2. Modify StageGraph to use routing_profile
3. Update RM stage mapping
4. Align BoM operations
5. Validate scenarios

---

## ðŸ Current Focus

ðŸ‘‰ Make routing profile control StageGraph and RM stages

---

## ðŸ§  Key Design Principles

- Routing must be authoritative
- No hidden/default stage injection
- Spec â†’ Routing â†’ StageGraph â†’ RM â†’ BoM
- Full flexibility for any plant flow

---

## ðŸ§¾ Instruction to Codex

1. Analyze current routing gap
2. Explain how StageGraph is built today
3. Show how routing_profile can drive it
4. Propose safe design
5. Implement step-by-step
6. Provide validation plan

---

**End of Context**



