# ENGINE_V2_CONTEXT.md

## ðŸ“Œ Project: Corrugation Engine V2 (Odoo)

### Goal

Build a **world-class, stage-first, fully dynamic manufacturing engine**
that supports: - any plant configuration - any routing flow - dynamic
materials & consumables - accurate costing & BoM generation

------------------------------------------------------------------------

# âœ… CURRENT STATUS

## âœ” Phase 1--3: Core Engine (COMPLETE)

-   Quantity engine (StageGraph) stable
-   Dynamic routing (partial via existing engine)
-   Derived sheet layout (UPS-based)
-   Preview â†’ BoM workflow stable

## âœ” Phase 6--7: Materials & Costing (COMPLETE)

-   Material resolution working (liner, medium, etc.)
-   Variant-based costing working
-   Stage-level costing working
-   Consumables framework (adhesive, ink, etc.)

## âœ” Phase A: Config Models (COMPLETE)

Implemented: - Stage Templates - Stage IO Templates - Routing
Templates - Plant Stage Bindings - Material Profiles - Material
Attachment Rules - BoM Translation Rules

## âœ” Phase B: Seeding System (COMPLETE)

-   Manual seeding wizard implemented
-   Idempotent behavior
-   Ownership tracking:
    -   is_system_default
    -   seed_key
    -   seed_version
-   No auto-seeding on install/upgrade

## âœ” Phase C: Shadow Runtime Builder (COMPLETE)

Built runtime (shadow-only): - RoutingInstance - StageRun -
MaterialRequirement (shadow) - StageCostLine (shadow)

Integrated into debug: - raw_json.phase8_shadow_runtime

No changes to: - StageGraph - rm_rules.py - bom_adapter.py - preview /
costing / BoM

## âœ” Phase C (Refinement): Routing Selection (COMPLETE)

Routing precedence implemented:

1.  Spec override\
2.  Plant default\
3.  Global default

Fields added: - corr.engine.v2.spec.phase8_routing_template_id -
corr.plant.param.phase8_routing_template_id

Debug visibility: - selection_source - selection_details - warnings

## âœ” Phase D: Parity Validation (COMPLETE)

Parity report added to: phase8_shadow_runtime.parity_report

Includes: - Materials parity - Stage parity - Bucket parity - Summary &
warnings

------------------------------------------------------------------------

# ðŸ§  ARCHITECTURE MODEL

## Core Layers

### 1. Config Layer (Templates)

-   Stage Template
-   Routing Template
-   Material Rules
-   BoM Rules

### 2. Runtime Layer (Shadow)

-   RoutingInstance
-   StageRun
-   MaterialRequirement
-   StageCostLine

### 3. Execution Layer (Current Engine)

-   StageGraph (truth)
-   rm_rules.py
-   bom_adapter.py
-   costing engine

------------------------------------------------------------------------

# ðŸ” DATA FLOW

Spec Input â†’ StageGraph â†’ RoutingInstance â†’ StageRun â†’
MaterialRequirement â†’ StageCostLine â†’ Parity

------------------------------------------------------------------------

# ðŸ­ PLANT MODEL

## Plant = Manufacturing Configuration

Each plant represents: - machine capability - split rules - sheet size -
costing - process constraints

Multiple plants allowed per company: - PLANT_AUTO - PLANT_SEMI -
PLANT_MANUAL

Stage Templates are shared globally.

Plants bind to stages using: - corr.engine.plant.stage.binding

------------------------------------------------------------------------

# ðŸ”€ ROUTING MODEL

Routing Templates define logical stage flow.

Routing Resolution: Spec override â†’ Plant default â†’ Global default

Currently shadow-only.

------------------------------------------------------------------------

# ðŸ§ª PARITY VALIDATION (CURRENT FOCUS)

We validate: - Stage alignment - Material alignment - Bucket alignment -
Routing alignment

------------------------------------------------------------------------

# âš ï¸ CRITICAL CONSTRAINTS

DO NOT: - modify StageGraph - modify rm_rules.py - modify
bom_adapter.py - change preview JSON - change BoM logic - change costing

------------------------------------------------------------------------

# ðŸš€ NEXT STEP: PHASE D REFINEMENT

Tasks: - run parity scenarios - identify mismatches - fix config/runtime
alignment - improve parity accuracy

------------------------------------------------------------------------

# ðŸ”œ FUTURE (DO NOT IMPLEMENT YET)

Phase E: - switch to Phase 8 routing - replace rm_rules - replace BoM
adapter - full stage-first execution

------------------------------------------------------------------------

# ðŸ§­ NEXT SESSION INSTRUCTIONS

We are in Phase D (Parity Refinement).

Focus: - validate shadow vs current engine - fix mismatches

Do NOT change core engine behavior.



