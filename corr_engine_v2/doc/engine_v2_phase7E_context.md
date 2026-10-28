# Engine V2 â€” Slitting & Layout Engine (Phase 7B â†’ 7E)

---

# ðŸ“Œ Current Status Summary

Engine V2 has successfully evolved into a:

* âœ” Route-driven system
* âœ” Sheet-based feasibility engine
* âœ” Machine-aware layout validator
* âœ” Slitting option generator
* âœ” UI-integrated selection system

We have completed:

* Phase 7B â€” Routing + BoM alignment
* Phase 7C â€” Feasibility (sheet-based)
* Phase 7D â€” Slitting Option Engine + UI Selection

Now moving toward:

ðŸ‘‰ **Phase 7E â€” Layout Decision Integration & Execution**

---

# âœ… What Has Been Completed

## 1. Routing (Phase 7B)

* Dynamic routing implemented
* No hardcoded stage flow
* Supports:

  * full flow
  * no-2ply flow
  * pre-print flow
  * single-stage flow

---

## 2. BoM Alignment

* One operation per stage
* Stage-driven BoM
* Clean BoM rebuild
* Component binding fixed (no hardcoding fallback)

---

## 3. Feasibility Engine (Phase 7C)

* Sheet-based feasibility (not box/blank)
* Rotation support (direct + rotated)
* Workcenter-driven constraints
* Constraint precedence:

  1. operation map
  2. workcenter
  3. plant fallback

---

## 4. Slitting Option Engine (Phase 7D)

### Core Behavior

* Blank-based option generation
* Width-wise UPS expansion:

  * 1-up, 2-up, 3-up...
* Sheet derived as:

  * length = blank_length
  * width = blank_width Ã— UPS

### Feasibility

* Workcenter min/max width
* Workcenter min/max length
* Rotation supported
* Multi-workcenter evaluation

---

## 5. Ranking Logic

Deterministic ranking:

1. Higher UPS
2. Prefer direct over rotated
3. Smaller sheet area

---

## 6. Presentation Layer

Each stage exposes:

* preferred_option
* feasible_options
* rejected_options

UI shows:

* preferred option (highlighted)
* other feasible options
* rejected options hidden

---

## 7. UI Integration

* Layout Options page added in preview wizard
* Default selection = preferred option
* User can override selection
* Selected option stored as JSON
* Passed via context to BoM (not yet applied)

---

## 8. Configuration Improvements

### Stage Level

* `consider_slitting_options` (boolean)
* controls eligibility

### Workcenter Level

* `max_slitting_ups`
* machine-driven cap

### Global Parameter

* `corr_engine_v2.max_slitting_ups`

---

## 9. UPS Precedence (Final)

1. Workcenter max_slitting_ups
2. Global system parameter
3. Rejection boundary
4. Safety cap

---

## 10. Hardcoding Fix

* Introduced config-first eligibility
* Isolated fallback logic
* `_resolve_stage_slitting_option_eligibility()` used
* Hardcoded stage names now **fallback only**

---

## 11. Test Coverage

Fully covered scenarios:

* 1-up only
* multi-UPS
* rotated-only
* width rejection
* length rejection
* missing config
* multi-workcenter
* ranking/tie-breaking
* eligibility override behavior

âœ” ~29 tests passing

---

# âš ï¸ Current Issue Identified

## Problem

UI still shows slitting options **stage-wise**

Example:

* Corrugation â†’ options
* Board Formation â†’ options

ðŸ‘‰ This is incorrect business behavior

---

## Business Reality

* Slitting/layout is decided at **one stage**
* Not at every stage
* Downstream stages consume selected sheet

---

# ðŸ§  Required Concept (Next Step)

Introduce:

## ðŸ‘‰ Layout Driving Stage

Only ONE stage should:

* generate options
* be shown in UI
* drive final selection

---

# ðŸš§ What Needs to Be Implemented Next (Phase 7E)

## 1. Identify Primary Layout Stage

Add field:

* `is_primary_layout_stage`

### Behavior:

* True â†’ this stage drives layout decision
* Only this stage shows options

---

## 2. Fallback Logic

If no stage marked:

* use first eligible stage in route order

---

## 3. UI Refinement

Change from:

âŒ Stage-wise option display

To:

âœ” Single layout decision block

---

## Expected UI

```
Layout Options (Driving Stage: Corrugation)

âœ” 2-up | 3020 Ã— 2240 | Corrugator X | Preferred
  1-up | 3020 Ã— 1120 | Corrugator X
```

---

## 4. Downstream Behavior

* Selected option must flow to:

  * sheet dimensions
  * workcenter usage
  * BoM creation

---

## 5. Remove Stage-wise UI Noise

* Board Formation â†’ should NOT show options
* Peel-off â†’ should NOT show options
* FG â†’ should NOT show options

---

## 6. Keep Unchanged

* feasibility logic
* ranking logic
* raw_json structure (as much as possible)

---

# ðŸš€ Next Phase Goal

Move from:

âŒ Stage-wise option system

To:

âœ” Route-level layout decision system

---

# ðŸ Final Status

| Area                        | Status              |
| --------------------------- | ------------------- |
| Routing                     | âœ… Complete          |
| BoM Alignment               | âœ… Complete          |
| Feasibility                 | âœ… Complete          |
| Slitting Options            | âœ… Complete          |
| UI Selection                | âœ… Complete          |
| Config-driven Eligibility   | âœ… Complete          |
| Max UPS Control             | âœ… Complete          |
| Stage-wise UI               | âš ï¸ Needs refinement |
| Layout Decision Integration | â³ Next              |

---

# ðŸ’¬ Instructions for New Session

Use this:

```
Use this Engine V2 context.

Phase 7B, 7C, 7D are complete.

Current issue:
Slitting options are still shown stage-wise.

Business requirement:
Only one stage should drive layout/slitting decision.

Next focus:
- Introduce primary layout stage
- Show only one option set in UI
- downstream stages consume selected layout

Do NOT:
- change feasibility logic
- change ranking logic

Goal:
Move from stage-wise options to final layout decision system.
```

---

# ðŸ Conclusion

Engine V2 is now:

* âœ” Stable
* âœ” Config-driven
* âœ” Machine-aware
* âœ” Option-capable
* âœ” UI-integrated

Next step is:

ðŸ‘‰ **Make layout decision single, final, and production-driven**

---



