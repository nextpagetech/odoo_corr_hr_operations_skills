# Engine V2 â€“ Next Phase Context (Post Phase 6B)

---

# ðŸ“Œ 1. Current Status

## Completed
- Phase 1 â†’ Phase 5: Core engine, layout, preview âœ…
- Phase 6A: Debug wizard BoM switch (StageContext) âœ…
- Phase 6B: General BoM wizard rollout (feature-flag based) âœ…

## Current Architecture
- StageContext â†’ structure authority
- Hybrid quantity model (temporary)
- Paper resolution with fallback
- Preview â†” BoM aligned
- Feature flag for safe rollout

## Current Limitation
Engine is still:
- single-result driven
- sheet-layout oriented
- not fully aligned with real corrugation execution

---

# ðŸš¨ 2. Key Realizations (Critical)

## âŒ Current gaps
- â€œblanks per sheetâ€ logic misleading for corrugation
- plant used as machine â†’ incorrect abstraction
- slitting modeled incorrectly (plant-level instead of spec-driven)
- no support for:
  - 2-piece boxes (half-up)
  - uneven slitting
- engine forces one result (poor UX)

---

# âœ… 3. Target Direction (New Engine Model)

## Core Principle
Engine must evolve from:

ðŸ‘‰ single-result calculator  
to  
ðŸ‘‰ multi-option corrugation planning engine

---

# ðŸ§  4. New Architecture

## ðŸ”· Separation of Concerns

### Spec (Product-level)
Defines:
- box dimensions
- style
- top paper mode
- printing requirement
- locking type
- pieces_per_box (NEW)
- split requirements (derived)

### Plant (Global Defaults ONLY)
Should contain:
- glue flap defaults
- wastage defaults
- global policies
- fallback routing

### Work Centers (Machine-level)
Must contain:
- machine type
- min/max width
- min/max length
- supported flute types
- supports equal/uneven split
- max slit lanes
- printing capability
- locking capability

---

# ðŸ”· 5. Option-Based Model

Each option contains:
- option_type
- UPS / slit_pattern
- pieces_per_box
- required_width
- cut_length
- feasibility
- paper resolution
- RM summary
- waste
- cost
- recommended flag

---

# ðŸ”· 6. Preview

Show:
- options list
- recommended option
- per-option RM + paper
- diagnostics

---

# ðŸ”· 7. BoM Rule

ðŸ‘‰ One BoM = One selected option

---

# ðŸ”· 8. Flow

Spec â†’ Options â†’ Validate â†’ RM â†’ Select â†’ BoM

---

# ðŸŽ¯ Goal

Build a true corrugation planning engine.



