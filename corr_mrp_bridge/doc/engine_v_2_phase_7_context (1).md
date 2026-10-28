# Engine V2 – Phase 7 Context (Post Routing Cleanup)

---

# 📌 Current Status

## Completed

### Core Engine
- Spec → Preview → raw_json → BoM flow ✅
- StageContext-based preview & BoM bridge ✅
- Feature-flag rollout ✅

### Materials
- Paper resolution (exact + fallback) ✅
- Material mapping (liner, medium, top_sheet) ✅
- Variant-aware costing ✅

### Top Sheet Handling
- Roll mode ✅
- Unit sheet mode ✅
- Stack-line logic stable ✅

### Consumables
- Adhesive 3-stage model ✅
- Stage-aware allocation (with fallback merge) ✅

### Routing (Phase 7A – Completed)
- Routing profile drives stage order ✅
- Operation stages included in stage graph ✅
- Final stage derived from routing (not hardcoded fg) ✅
- RM attachment uses route semantics ✅
- Legacy hardcoding (2ply/board/fg) isolated to fallback only ✅

---

# ⚠️ Current Limitations

## 1. Quantity Graph is still legacy
- Fixed chain:
  raw_material → board → sheet → blanks → finished_goods
- Not fully aligned with dynamic routing

## 2. BoM Operation Modeling is legacy
- Operations derived from fixed assumptions
- Not fully route-driven yet

## 3. Single Result Engine
- Engine still produces only one layout/result
- No multi-option support yet

## 4. Plant still carries feasibility logic
- Machine constraints not yet separated

---

# 🎯 Next Phase Goal

## Phase 7B — Quantity & BoM Cleanup

Goal:
Align quantity graph and BoM operation modeling with routed stage chain.

---

# 🧠 Target Direction

## From
- Fixed quantity chain
- Hardcoded operation buckets

## To
- Route-aware quantity flow
- Stage-driven BoM operations

---

# 🔧 What needs to be done

## 1. Quantity Graph Refactor

### Current
- raw_material → board → sheet → blanks → finished_goods

### Target
- Derived from routing profile

Example:

Route:
2ply → board → ts_print → seaming

Quantity flow should reflect:
- board created at board stage
- sheet processed at ts_print
- final units created at seaming

---

## 2. BoM Operation Cleanup

### Current
- Hardcoded operations (corrugator, printing, diecut)

### Target
- Operations derived from routed stages

---

## 3. Keep Safe Boundaries

Do NOT change:
- raw_json structure
- preview payload contract
- RM attachment logic

---

# 🚀 Future Phases (Do NOT implement yet)

## Phase 7C — Work Center Feasibility
- Machine-level constraints
- Replace plant-level feasibility

## Phase 7D — Option Engine
- Multi-option generation
- UPS variations
- Split logic

---

# 📊 Current Architecture Snapshot

## Stable Layers
- Spec
- Routing Profile
- Stage Templates
- RM Resolution
- StageContext Preview

## Transitional Layers
- Quantity Graph ⚠️
- BoM Adapter ⚠️

## Future Layers
- Option Engine
- Work Center Engine

---

# 🧪 Validation Checklist

Before moving forward ensure:

- raw_json stages match routing profile
- Stage Flow matches routing profile
- RM attaches correctly
- Final stage is correct (no fg leakage)

---

# 💬 What to tell next session

Use this:

"Use this Engine V2 Phase 7 context.

Routing cleanup is complete.

Next focus is Phase 7B — quantity graph and BoM operation cleanup.

Do NOT implement option engine or work-center feasibility yet.

Start with audit of current quantity graph and BoM modeling, then propose safe refactor."

---

# 🧠 Key Principle

👉 Keep current engine as evaluator
👉 Build next layers incrementally
👉 Avoid breaking preview → BoM flow

---

Even after routing cleanup:

StageContext preview is route-driven ✅
Core compute() stage graph is now route-aware ✅
RM attachment is correct and stable ✅

But still legacy:

Quantity graph is fixed-node:
raw_material → board → sheet → blanks → finished_goods
BoM operation modeling is based on legacy assumptions
Some internal bridges still depend on legacy structures

👉 So next phase is refactor, NOT redesign

# ✅ End State Vision

- Fully route-driven engine
- Stage-aware RM + quantities
- Machine-aware planning
- Multi-option output

---

