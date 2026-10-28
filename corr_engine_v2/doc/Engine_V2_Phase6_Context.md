# Engine V2 â€“ Phase 6 Context & Continuation File

## Project: Corrugation Engine V2
## Current Stage: Phase 6 (Quantity + Costing) â€“ Completed

---

## 1. Overview

Engine V2 uses a hybrid material resolution model:

### Paper (liner, medium)
- Resolved during preview
- Based on GSM, BF, Deckle, Color
- Stored in raw_json
- Used directly in BoM

### Non-paper (adhesive, ink, etc.)
- Uses corr.material.product.map
- Applied during BoM creation

---

## 2. Implemented

### Engine Core
- StageGraph
- UPS layout
- Feasibility checks

### Paper Resolution
- Variant-based
- Deckle â‰¥ required
- Status: exact, usable, missing, ambiguous

### Quantity Rollup
- qty_per_fg
- qty_for_order
- StageGraph totals

### Costing (Preview Only)
- Variant-based pricing
- Cost sources:
  - standard_price
  - last_purchase_price
- Fallback supported
- UoM conversion handled

### Debug Fields
- requested_cost_source
- effective_cost_source
- raw_standard_price
- raw_last_purchase_price
- converted_unit_cost
- fallback_reason

---

## 3. Validation

- Quantity: Verified
- Costing: Verified
- Variant pricing: Verified
- Fallback logic: Verified
- No regression: Verified

---

## 4. Limitations

- Preview-only costing
- No BoM persistence
- No operation/labor cost

---

## 5. Conclusion

Phase 6 is COMPLETE:
- Quantity Rollup: Done
- Material Costing: Done

---

## 6. Next Phase

Phase 7 â€“ Operation Costing

Goals:
- Machine cost
- Labor cost
- Stage-wise cost
- Total production cost

---

## 7. Rules

- Do not change quantity logic
- Do not change material resolution
- Do not change BoM creation
- Extend preview only

---

## 8. Next Session Prompt

Use this:

We completed Phase 6. Now implement Phase 7:
- Add operation costing
- Stage-wise costing
- Machine + labor cost
- Total cost rollup

Do not modify existing logic.



