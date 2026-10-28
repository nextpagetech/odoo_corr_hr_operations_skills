# Engine V2 — Phase 7B Completion & Phase 7C Continuation Context

---

# 📌 Current State (IMPORTANT)

This is a continuation of Engine V2 implementation.

The engine is now:

* Fully route-aware (Preview → BoM)
* Stable for Spec → Preview → raw_json → Create BoM
* Using StageContext-based architecture

We are transitioning from **Phase 7B (Routing + BoM alignment)** to **Phase 7C (Feasibility foundation)**.

---

# ✅ Phase 7B — COMPLETED

## 1. Routing Integration

* Selected route fully drives:

  * Stage flow
  * RM attachment
  * Operation sequence
* No hardcoded stage sequences
* Supports dynamic flows:

  * Standard flow
  * No-2ply flow
  * Pre-print flow
  * Single-stage flow

---

## 2. Routed Operations (BoM Alignment)

* `routed_operations` added to:

  * preview_model
  * writer_payload
* Final BoM creation now prefers routed operations
* One BoM operation per stage instance
* Sequence preserved

---

## 3. Operation Display Naming

* BoM operations now use:

  * `stage_template.name`
* Removed hardcoded generic labels (Corrugation, Printing, etc.)
* Improved user readability

Example:

* 2ply → 2-Ply Corrugation
* board → Board Formation
* seaming → Seaming / Jointing

---

## 4. Top Sheet Resolution (Mode-Aware)

### Roll Mode

* Uses stack paper variant
* Quantity in kg

### Unit Mode

* Uses selected top-sheet product

* Quantity in units

* Removed incorrect fallback to mapping for top_sheet

---

## 5. Component → Operation Binding (Major Fix)

### Old Problem

* Binding assumed:

  * 2ply → board fallback
* Caused:

  * Empty “Consumed in Operation”
  * Incorrect stage binding

### New Behavior

Binding is now **route-aware and generic**:

For each component:

1. Try intended stage
2. If missing:

   * Walk forward in routed stage sequence
   * Bind to next valid material stage
3. If no anchor exists:

   * Bind to first available material stage
4. If no material stage exists:

   * Controlled empty behavior

### Validated Cases

* With 2ply → binds to 2-Ply Corrugation
* Without 2ply → binds to Board Formation
* Single-stage route → binds to Seaming

---

## 6. Stale BoM Cleanup

* Old bridge-generated rows removed properly
* Ensures clean BoM recreation

---

## 7. Validation Completed

Tested scenarios:

1. Full flow:

   * 2ply → board → diecut → seaming

2. No-2ply flow:

   * board → diecut → seaming

3. Pre-print flow:

   * printing before board

4. Single-stage flow:

   * seaming only

5. Top sheet modes:

   * roll
   * unit

All flows working as expected.

---

# 🎯 Phase 7B Final Status

✅ Routing — Complete
✅ BoM operation alignment — Complete
✅ Display naming — Complete
✅ Component binding — Complete
✅ Top sheet behavior — Complete

➡️ Phase 7B is **CLOSED**

---

# 🚧 Phase 7C — NEXT IMPLEMENTATION

## Objective

Build **machine / workcenter feasibility foundation**.

NOT full option engine yet.

---

# 🔧 Phase 7C Scope

## 1. Stage-Level Feasibility

For each routed stage:

* Identify candidate machines/workcenters
* Validate feasibility using:

  * width
  * length
  * machine limits
  * plant constraints

---

## 2. Constraint Evaluation

Introduce validation for:

* Max width / deckle
* Max length
* Split rules / multi-up feasibility
* UPS feasibility (basic level)

---

## 3. Feasibility Output

Extend preview/raw_json/debug to include:

* feasible machines per stage
* rejected machines with reasons
* constraint evaluation results

---

## 4. Keep It Additive

* Do NOT modify existing routing
* Do NOT modify quantity graph
* Do NOT modify BoM creation
* Only add feasibility layer

---

# 🚫 Do NOT Implement Yet

* Full option engine
* Multi-option generation
* Recommendation UI
* Machine selection UI
* Cost optimization engine

---

# 🧠 Design Principles

* Routing remains the source of truth
* Feasibility is layered on top
* No breaking changes
* Prefer additive logic
* Keep backward compatibility

---

# 💬 Instructions for New Session

Use this exactly:

---

"Use this Engine V2 Phase 7B completion context.

Phase 7B is complete and validated.

Engine is stable for:

* Spec → Preview → BoM
* Route-aware operations
* Route-aware component binding

Next focus:
Phase 7C — machine/workcenter feasibility foundation.

Important:

* Do not redesign routing
* Do not change quantity graph
* Do not implement full option engine

Only implement feasibility evaluation per routed stage."

---

# 🚀 End Goal

* Fully route-driven engine
* Stage-aware BoM
* Correct component ownership
* Feasibility-ready architecture
* Foundation for option engine (Phase 7D)

---
