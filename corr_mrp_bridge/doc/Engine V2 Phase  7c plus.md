# Engine V2 — Phase 7C Status & Next Steps

---

# 📌 Current Status Summary

We have successfully completed **Phase 7B (Routing + BoM alignment)** and implemented **Phase 7C (Feasibility Foundation — Refined)**.

The engine is now:

* Fully route-aware
* BoM aligned with routed stages
* Component binding dynamic (no hardcoding)
* Feasibility layer added (additive, non-breaking)

---

# ✅ What Has Been Completed

## 1. Routing (Phase 7B)

* Route drives:

  * Stage sequence
  * Operation sequence
  * Component binding
* Supports:

  * Full flow
  * No-2ply flow
  * Pre-print flow
  * Single-stage flow

---

## 2. BoM Alignment

* One operation per routed stage
* Stage names from stage templates
* Clean BoM recreation (no stale rows)

---

## 3. Component Binding (Major Fix)

* Removed hardcoded fallback (2ply → board)
* New logic:

  * Bind to intended stage
  * If missing → walk forward in route
  * If none → bind to first valid material stage

---

## 4. Top Sheet Logic

* Roll mode → uses stack paper (kg)
* Unit mode → uses product (units)
* Removed incorrect fallback mapping

---

# 🚧 Phase 7C — Feasibility Foundation (Implemented)

## Core Changes

### 1. Sheet-Based Feasibility

Feasibility now uses:

* sheet_width_mm
* sheet_length_mm

NOT:

* box dimensions
* blank dimensions (only fallback)

---

### 2. UPS Handling Fixed

* Removed direct UPS rejection
* UPS now:

  * used only for layout
  * affects feasibility indirectly via sheet size

---

### 3. Candidate Evaluation

For each routed stage:

* collect candidate workcenters
* evaluate:

  * width fit
  * length fit

---

### 4. Rotation Support

For each candidate:

* check direct orientation
* check rotated orientation (if allowed)

Rule:

```
If ANY orientation fits → candidate is feasible
```

---

### 5. Constraint Precedence

1. operation_map constraints
2. workcenter constraints
3. plant (fallback only)

---

### 6. Debug Visibility Added

Each candidate shows:

* dimension_checks
* used_orientation
* rotation_used
* resolved_constraints

---

# 🔍 Current Observations (Important)

## 1. Feasibility uses Sheet, not Blank

Example:

* Blank ≈ 3020 × 1120
* Sheet used = 4800 × 2400

Feasibility checks:

* 4800 × 2400 (direct)
* 2400 × 4800 (rotated)

✔ This is expected behavior in current design

---

## 2. Debug Payload Issue

Current payload shows:

* blank_length_mm = 700
* blank_width_mm = 800

❌ These are incorrect (box values, not blank)

👉 Needs fix for clarity

---

## 3. All-Stages Rejected Case Observed

Reason:

* sheet size too large for all machines
* workcenter width limits exceeded
* plant fallback also restrictive

✔ This confirms feasibility logic is working correctly

---

# 🧠 Final Architecture (After Phase 7C)

```
Spec → Box
↓
Style → Blank
↓
Layout → Sheet (UPS applied)
↓
Routing → Stage Chain
↓
Feasibility → Machine checks Sheet
↓
BoM → Operations + Components
```

---

# 🎯 What We Need to Implement Next

## Phase 7C.1 — Traceability & Clarity Fix

### 1. Fix Debug Payload

Ensure raw_json shows:

* box dimensions
* correct blank dimensions
* sheet dimensions
* chosen feasibility basis

---

### 2. Explain Sheet Source

Expose in debug:

* sheet_source (derived/manual/plant)
* layout inputs
* UPS used

---

### 3. Improve Dimension Transparency

For each stage:

* why sheet was chosen
* why blank not used

---

# 🚀 Phase 7D — Option Engine (Next Major Phase)

## Goal

Generate feasible production options.

---

## 1. Slitting Options

Example:

* blank width = 400
* machine max width = 900

Options:

* 1-up → feasible
* 2-up → feasible
* 3-up → rejected

---

## 2. Printing / Diecut UPS Options

* based on die/tooling
* validated via feasibility

---

## 3. Multi-Option Output

Engine should generate:

* multiple feasible layouts
* each with:

  * UPS
  * sheet size
  * machine feasibility

---

## 4. Future Enhancements

* recommendation engine
* cost comparison
* best option selection

---

# 🚫 What NOT to Do Now

* Do not redesign routing
* Do not change BoM
* Do not implement UI options
* Do not add cost optimization

---

# 💬 Instructions for New Session

Use this:

```
Use this Engine V2 Phase 7C context.

Phase 7B is complete.
Phase 7C feasibility is implemented and working.

Current focus:
Fix traceability and clarity issues in feasibility.

Important:
- Feasibility is sheet-dimension driven
- UPS is not a direct rejection factor
- Workcenter constraints are primary
- Plant is fallback only

Next:
- Fix debug payload (correct blank + sheet visibility)
- Expose sheet source and layout reasoning
- Prepare groundwork for Phase 7D (option generation)

Do NOT:
- change routing
- change BoM
- implement option engine yet
```

---

# 🧾 Final Status

| Area              | Status               |
| ----------------- | -------------------- |
| Routing           | ✅ Complete           |
| BoM Alignment     | ✅ Complete           |
| Component Binding | ✅ Complete           |
| Feasibility Layer | ✅ Implemented        |
| UPS Handling      | ✅ Corrected          |
| Debug Clarity     | ⚠️ Needs improvement |
| Option Engine     | ⏳ Next Phase         |

---

# 🏁 Summary

Engine V2 is now:

* Route-driven
* Sheet-based feasibility
* Machine-aware
* Ready for option generation phase

Next step is **clarity + option expansion**, not core redesign.

---
