# Engine V2 Phase J Completion Summary

## Status

Phase J is complete for the validated `independent_routed_piece` multi-piece path.

Covered sub-phases:

- J1: Piece-specific override setup
- J2: Piece-specific RM and costing preview
- J3: Piece-specific materialization

This summary describes the final implemented behavior after J1, J2, and J3 validation/fix passes.

---

## J1 Completed Features

### Persistent piece-level override model

Implemented on `corr.engine.v2.spec.piece`:

- `stack_source` (`inherited` / `custom`)
- `piece_stack_line_ids`
- `surface_source` (`inherited` / `custom`)
- `surface_profile_id`
- `top_sheet_source` (`inherited` / `custom`)
- `top_sheet_mode`
- `top_sheet_product_id`
- computed `recipe_signature`
- computed effective summaries for stack, surface, and top sheet

New model added:

- `corr.engine.v2.spec.piece.layer`

Stored per piece layer:

- `piece_id`
- `sequence`
- `role`
- `gsm`
- `bf`
- `color`
- `flute_type_id`
- `material_product_id`
- `source_type`
- `source_recipe_line_id`

### Piece editing UI

Multi-Piece tab now supports:

- compact list of pieces on spec form
- popup-style piece detail editing from the piece row
- inherited vs custom stack editing
- inherited vs custom surface editing
- inherited vs custom top sheet editing

### Effective piece manufacturing preview

Preview/raw JSON now exposes per-piece effective manufacturing values:

- `piece_manufacturing_overrides`
- `effective_piece_manufacturing`

Per piece payload includes:

- `piece_key`
- `piece_index`
- `stack_source`
- `surface_source`
- `top_sheet_source`
- `effective_stack_lines`
- `effective_surface_profile`
- `effective_top_sheet_mode`
- `effective_top_sheet_product`
- `recipe_signature`

### J1 Create BoM policy

At J1 stage, Create BoM was blocked whenever any piece used custom overrides.

That temporary block has since been superseded by J3 for validated cases, but the J1 setup and preview behavior remain the same.

---

## J2 Completed Features

### Piece-specific RM generation from effective recipe

RM rows are now derived from the effective piece recipe, not blindly from the parent FG spec.

Implemented behavior:

- inherited piece -> parent stack/surface/top sheet
- custom piece -> piece-specific stack/surface/top sheet

Generated RM rows carry piece-specific metadata:

- `piece_key`
- `piece_index`
- `stack_source`
- `surface_source`
- `top_sheet_source`
- `recipe_signature`
- stage-specific metadata such as layer/flute/GSM/BF/color

### Per-piece paper resolution

Paper variant resolution now uses effective piece recipe inputs:

- GSM
- BF
- color
- flute
- required deckle

This allows custom piece stacks to resolve variants independently of the parent spec.

### RM Breakdown grouped by piece

RM Breakdown preview now shows:

- Piece A RM
- Piece B RM
- FG Rollup

Behavior:

- piece rows are layer-level, not role-collapsed
- FG rollup is built from piece-effective RM rows
- custom top sheet and custom stack rows appear under the correct piece

### Piece-level costing preview

Cost preview now consumes the same piece-effective RM rows used by RM Breakdown.

Costing Summary now shows:

- piece-level material cost
- FG material rollup
- resolved product-based RM costing details
- cost detail popup aligned to piece-effective RM rows

### J2 Create BoM policy

Create BoM remained blocked for custom piece overrides during J2.

That block was intentionally removed only after J3 materialization was validated for supported cases.

---

## J3 Completed Features

### J3A: recipe-aware materialization plan

The existing Phase G2 `independent_routed_piece` plan was extended to use effective piece recipes.

Dry-run candidates now carry:

- `piece_key`
- `piece_index`
- `stack_source`
- `surface_source`
- `top_sheet_source`
- `recipe_signature`
- `effective_top_sheet_mode`
- `effective_stack_signature`
- `effective_surface_profile_id`
- `strategy = independent_routed_piece`

### J3B: recipe-aware reuse/signature resolution

Real product and BoM reuse now follows effective piece manufacturing identity.

Implemented reuse behavior:

- same stack + same top sheet + same surface -> reuse same routed SFG/BoM chain
- same stack + different top sheet -> reuse 2-Ply, separate Board
- different stack -> separate 2-Ply and separate Board
- different surface only -> 2-Ply may reuse; Board separates when board-stage recipe differs
- different flute groups inside one piece -> distinct 2-Ply outputs are separated by recipe identity

### J3C: Create BoM enabled for validated custom piece flows

Create BoM is now enabled for custom piece overrides only when all of the following are true:

- strategy = `independent_routed_piece`
- exactly 2 pieces
- no shared upstream split path
- no unequal split manufacturing path
- no piece-specific routing path
- effective piece recipe plan passes resolver/writer checks

Writer path:

- reuses existing Phase G2 writer path
- does not introduce a separate writer
- uses piece-effective RM rows as materialization input

### J3 materialization behavior

Created BoMs now follow effective piece recipe behavior:

- Piece A custom stack + Piece B inherited stack -> separate 2-Ply/Board chains
- same stack + different top sheet -> shared 2-Ply, separate Board
- same stack + same top sheet -> shared 2-Ply and shared Board where signatures match
- mixed flute corrugation sets inside one custom piece -> distinct 2-Ply outputs with distinct visible naming and TUF-driven quantities

### Operation duplication cleanup

When a BoM is reused by multiple branches/pieces:

- operations are not duplicated on the reused BoM

### 2-Ply naming cleanup

Visible 2-Ply names now prefer recipe-visible differentiation.

Example:

- `2-Ply 302x56 cm (C:160/160 GSM BF:18/18)`
- `2-Ply 302x56 cm (F:160/160 GSM BF:18/18)`

`SET1` / `SET2` are only needed when visible recipe text would otherwise collide.

---

## Final Supported Scenarios

### Supported multi-piece materialization paths

- 2-piece `independent_routed_piece`
- equal split preview/materialization
- inherited piece manufacturing
- custom stack on one piece
- custom stack on both pieces
- custom top sheet on one or both pieces
- custom surface on one or both pieces for preview/materialization signatures
- same custom recipe on both pieces with reuse
- different custom recipes with separate SFGs/BoMs
- same stack but different top sheet
- mixed flute corrugation sets inside a custom piece
- 3-ply / 5-ply / 7-ply routed piece handling
- RM breakdown by piece
- piece-level material cost preview
- FG RM rollup and costing rollup

### Supported shared-upstream path

Phase H shared-upstream implementation remains available for the previously validated equal-split inherited-recipe flow.

Phase J does not extend custom piece recipe materialization into shared-upstream.

---

## Blocked Scenarios

Still blocked by design:

- `shared_upstream_split` + custom piece recipes
- unequal split manufacturing materialization with piece-specific custom recipe behavior
- piece-specific routing
- more than 2 pieces
- nested split manufacturing
- custom recipe materialization on unsupported/shared-upstream branch paths

---

## Deferred Scenarios

Deferred to later phases / R&D:

- unequal split manufacturing execution
- shared-upstream + custom piece recipe materialization
- true multi-output/byproduct split manufacturing design
- piece-specific routing execution
- piece count > 2 materialization
- advanced branch-level MO orchestration
- stock/traceability model for more complex split-output flows
- deeper surface-stage differentiation rules where surface should branch earlier than board
- roll-mode custom top sheet resolution when the business wants explicit roll product selection beyond current preview/materialization assumptions

---

## Tests Added / Updated

Primary test file:

- `custom_addons/corr_mrp_bridge/tests/test_engine_v2_multi_piece_materialization.py`

Covered J-phase test themes include:

- J1 custom stack preview payload
- J1 custom surface preview payload
- J1 custom top sheet preview payload
- J1 effective recipe signature propagation
- J2 piece-effective RM row generation
- J2 per-piece paper resolution
- J2 FG RM rollup from piece-effective rows
- J2 costing alignment to piece-effective RM rows
- J3 custom Piece A / inherited Piece B dry-run/materialization
- J3 same custom recipe reuse
- J3 different stack creates separate routed SFGs
- J3 same stack + different top sheet splits Board only
- J3 blocked shared-upstream + custom recipe
- J3 stale reuse cleanup for piece-effective signatures
- J3 mixed flute custom piece handling
- J3 mixed flute quantity/TUF preservation
- J3 visible 2-Ply naming cleanup for distinct flute-driven outputs
- inherited flow unchanged regression coverage

---

## Final Architecture Notes

### 1. Effective piece recipe is now authoritative

For Phase J flows, the authoritative source is no longer the parent FG stack alone.

Authoritative materialization input is:

- effective piece stack
- effective piece surface
- effective piece top sheet
- effective piece recipe signature

### 2. Preview, costing, and materialization now share the same piece-effective base

Phase J closed the earlier gap where:

- preview showed custom piece behavior
- but materialization reused parent-stack logic

Now the intended direction is:

- preview RM rows
- costing rows
- dry-run candidates
- real materialization inputs

all derive from the same piece-effective manufacturing payload.

### 3. Phase G/Phase H remain the base architecture

Phase J does not replace prior phases.

It extends:

- Phase G independent routed piece materialization

and leaves these boundaries in place:

- shared upstream custom recipes are not yet supported
- unequal split manufacturing remains separate R&D

### 4. Reuse is manufacturing-signature driven

SFG/BoM reuse remains driven by actual manufacturing identity, not FG identity.

This is required to avoid:

- duplicate 2-Ply SFGs
- duplicate Board SFGs
- duplicate BoMs for identical manufacturing recipes

### 5. Piece terminal products remain FG-specific

Branch/routed SFGs are reusable by manufacturing signature.

Terminal piece products remain FG-specific and user-readable:

- `<FG Product Name> - Piece 1`
- `<FG Product Name> - Piece 2`

### 6. Mixed corrugation sets inside a single piece are now first-class

When one piece has multiple corrugation sets with different flute/material identity:

- they are modeled as distinct 2-Ply outputs
- visible naming reflects the recipe difference
- quantities follow TUF/recipe-specific calculation

---

## Recommended Next Step

Start Phase K only after deciding the next priority between:

- shared-upstream custom recipe extension, or
- unequal split manufacturing R&D, or
- piece-specific routing.



