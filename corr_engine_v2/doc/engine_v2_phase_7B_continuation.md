# Engine V2 â€“ Phase 7B Final Continuation Context

---

# ðŸ“Œ Current State (Very Important)

This is a **continuation of an ongoing Engine V2 implementation**.

The engine is already:

* Stable for **Spec â†’ Preview â†’ raw_json â†’ Create BoM**
* Using **StageContext-based architecture**
* Fully **route-aware at preview level**

We are now in a **controlled refactor phase**, not a redesign.

---

# âœ… Completed Work

## Core Engine

* Spec â†’ Preview â†’ raw_json â†’ BoM flow stable
* StageContext preview & BoM bridge implemented
* Feature-flag rollout completed

## Materials & Costing

* Paper resolution (exact + fallback) complete
* Material mapping (liner, medium, top_sheet) complete
* Variant-aware costing implemented

## Top Sheet Handling

* Roll mode working
* Unit sheet mode working
* Stack-line logic stable

## Consumables

* Adhesive 3-stage model implemented
* Stage-aware allocation with fallback merge working

---

# âœ… Phase 7A â€” Routing Cleanup (Completed)

* Selected-route path is fully route-driven
* Operation stages included in stage graph
* Final stage derived from routing (no hardcoded FG)
* Legacy hardcoding isolated to fallback only
* RM attachment aligned with route semantics

---

# ðŸš§ Phase 7B â€” Progress Summary

## 7B.1 â€” BoM Operation Alignment âœ…

* BoM operations are now route-aware
* No synthetic operation injection
* Final conversion stages handled correctly
* Legacy fallback preserved

---

## 7B.2 â€” Quantity Modeling Improvements (Hybrid)

### 7B.2.a â€” Routed Quantity Metadata âœ…

* Legacy quantity graph retained:
  raw_material â†’ board â†’ sheet â†’ blanks â†’ finished_goods
* Routed stage quantities added as metadata
* Preview consumes propagated quantities

### 7B.2.b â€” Stage-Owned Quantity Sourcing âœ…

* StageContext prefers stage-level preview quantities
* Grouped operation quantities used as fallback

### 7B.2.c â€” Grouped Layer Detail Preservation âœ…

* grouped operations include `stage_details`
* StageContext can recover per-stage quantities
* writer payload preserves per-stage contribution detail

---

## 7B.3 â€” Routed Operation Emission (Major Step) âœ…

### Added:

* preview_model["routed_operations"]
* writer_payload["routed_operations"]

### Behavior:

* Each routed stage = one operation instance
* Repeated same-type stages preserved
* Grouped operations still exist for compatibility

---

## 7B.3.a â€” First Stage Fix âœ…

* Routed operation rule updated:
  FROM: input_qty > 0 AND output_qty > 0
  TO:   output_qty > 0

### Result:

First material-producing stages now included.

Example:
2ply â†’ board â†’ seaming
Produces routed_operations:

* 2ply
* board
* seaming

---

# ðŸ§ª Validation Status

Tested route:
2ply â†’ board â†’ seaming

Results:

* Stage Flow: âœ… correct
* Grouped operations: âœ… correct
* Routed operations: âœ… stage-complete
* Parity warnings: âš ï¸ expected (hybrid model)

---

# âš ï¸ Current Limitation (Critical)

## Final BoM Creation Still Legacy

Even though:

* preview has routed_operations
* writer_payload has routed_operations

Final BoM creation still uses:

* grouped operations only

Location:
_create_bom_from_struct()

Effect:

* Routed-stage identity is lost at final step
* Operations still grouped by operation family

---

# ðŸŽ¯ Current Objective

## Phase 7B â€” Final Step

Make final BoM creation **route-aware**

---

# ðŸ”§ Next Implementation Task

### Modify:

_create_bom_from_struct()

### Required Behavior:

1. If routed_operations exists:

   * Use routed_operations to create BoM operations
   * One BoM operation per routed stage instance

2. If routed_operations not available:

   * Fallback to grouped operations (legacy behavior)

---

# âš ï¸ Must Preserve (Strict Rules)

DO NOT BREAK:

* raw_json structure
* preview payload
* grouped operations output
* Create BoM flow
* legacy no-route fallback

---

# ðŸš« Do NOT Do

* Do NOT redesign quantity graph
* Do NOT remove legacy nodes
* Do NOT change preview structure
* Do NOT redesign BoM builder
* Do NOT start work-center feasibility
* Do NOT start option engine

---

# ðŸ§  Architecture State

## Stable

* Spec
* Routing
* StageContext preview
* RM resolution

## Improved

* BoM operation alignment
* Routed operation emission
* Stage-level quantity ownership

## Transitional

* Quantity graph (still legacy)
* Final BoM creation âš ï¸

---

# ðŸš€ Future (Not Now)

## Phase 7C

* Work-center / machine feasibility

## Phase 7D

* Option engine (multi-option, UPS variations)

---

# ðŸ§  Key Principles

* Keep engine stable
* Refactor incrementally
* Avoid breaking preview â†’ BoM flow
* Prefer additive changes
* Maintain backward compatibility

---

# ðŸ’¬ Instructions for New Session

Use this exactly:

"Use this Engine V2 Phase 7B context.

This is a continuation of an ongoing implementation.

Important:

* Engine is stable for preview â†’ BoM
* Routing is fully implemented
* We are in controlled refactor phase

Current focus:
Make final BoM creation route-aware using routed_operations,
while keeping grouped operations as fallback.

Do NOT redesign architecture or change quantity graph."

---

# âœ… End Goal

* Fully route-driven operations
* Stage-instance-aware BoM creation
* Correct quantity ownership
* Extensible for future machine planning
* Ready for multi-option engine

---



