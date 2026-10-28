# Corrugation Engine V2 â€“ Architecture and Pipeline

This document describes the Engine V2 architecture inside
`custom_addons/corr_mrp_bridge/engine_v2` and how it is integrated and
tested from the Odoo side.

The intent is to keep the core engine pure, predictable, and easy to
evolve, while all Odoo/BoM logic lives in `corr_mrp_bridge`.

---

## 1. Engine Pipeline (High-Level Flow)

The Engine V2 pipeline, as used from `engine_v2.compute`, is:

1. **Spec adapter (`spec_adapter.build_engine_spec_input`)**
   - Runs in `corr_mrp_bridge` (Odoo layer).
   - Converts a `corr.spec.template` record into a pure
     `EngineSpecInput`:
     - copies basic dimensions and GSM,
     - builds `board_layers` from `corr.spec.stack.line`,
     - reads `top_sheet_gsm` and other flags from `extra_params_json`.

2. **Style resolution (`engine_v2.styles`)**
   - Module: `engine_v2/styles/` and `engine_v2/styles/registry.py`.
   - `compute()` looks up a style handler from `STYLE_REGISTRY`
     using `EngineSpecInput.style_code`.
   - A style handler (e.g. `style_0201.compute_blank`) may:
     - compute `blank_length_mm`, `blank_width_mm` from box
       dimensions,
     - return a dict with new blank dims which are written back to
       `spec_input`.

3. **Splitting (`engine_v2/splitting.py`)**
   - Runs after style and before layout.
   - `resolve_splitting(spec_input, plant_profile, metrics)`:
     - reads machine width limits from the plant profile
       (`max_corrugator_width_mm`, `max_printer_width_mm`,
       `max_diecut_width_mm`),
     - computes an effective width limit,
     - when `blank_width_mm` exceeds this limit:
       - computes `pieces_per_box = ceil(blank_width_mm / limit)`,
       - sets an **effective blank width** per piece for layout by
         updating `spec_input.blank_width_mm`,
       - records `pieces_per_box` in `metrics.extra`.

4. **Layout (`engine_v2/layout.py`)**
   - `resolve_layout(spec_input, plant_profile)`:
     - uses `blank_length_mm`, `blank_width_mm` (possibly adjusted by
       splitting) and plant `sheet_length_mm`, `sheet_width_mm`,
     - evaluates normal and rotated orientations when
       `allow_blank_rotation` is true,
     - returns:
       - `orientation`,
       - `blanks_per_row`, `blanks_per_column`,
       - `blanks_per_sheet`,
       - `sheet_usage_percent`, `waste_percent`.
   - `compute()` stores:
     - `layout_blanks_per_sheet` in `metrics.extra`,
     - `boxes_per_sheet = layout_blanks_per_sheet / pieces_per_box`
       in `metrics.extra["boxes_per_sheet"]`.

5. **Board structure (`engine_v2/board_structure.py`)**
   - `resolve_board_structure(spec_input)`:
     - if `board_layers` is present on `spec_input`, converts it into
       a `BoardStructure` with typed `BoardLayer` entries,
     - otherwise, falls back to legacy `liner_gsm` and `medium_gsm`
       as a simple 2â€‘ply stack.

6. **Raw-material routing (`engine_v2/rm_rules.py`)**
   - `resolve_rm_inputs(spec_input, plant_profile, stage_graph, metrics)`:
     - builds RM requirements from `BoardStructure` and plant/stage
       configuration:
       - corrugation RMs (liner/medium) routed to `2ply` when present,
       - topâ€‘sheet RMs (roll vs external sheet) routed to `ts_print`
         or `board` based on the stage chain.
     - returns:
       - `rm_requirements: List[RmRequirement]`,
       - `board_struct: BoardStructure`.

7. **StageGraph construction (`engine_v2.__init__.build_stage_graph`)**
   - Uses `PlantProfile.stages` to create:
     - `Stage` records for material stages (`2ply`, `board`, `fg`, etc),
     - `FlowEdge` records between material stages.
   - Nonâ€‘material steps are attached as `Stage.operations` on the
     nearest material stage.
   - `StageGraph.final_stage_key()` determines the logical FG stage
     (sink node).

8. **StageGraph quantity propagation**
   - After layout and splitting:
     - `layout_blanks_per_sheet` and `pieces_per_box` are read from
       `metrics.extra`.
     - Boxes per sheet:
       `boxes_per_sheet = layout_blanks_per_sheet / pieces_per_box`
       is stored in `metrics.extra["boxes_per_sheet"]`.
   - The final material edge (`from` last material stage into the
     final stage) is assigned:
     - `qty_per_fg = pieces_per_box / layout_blanks_per_sheet`
       when layout data exists, or
     - `qty_per_fg = 1.0` as a fallback.
   - This expresses â€œhow many units of the final SFG per finished
     boxâ€ and matches splitting+layout behavior.

9. **EngineResult assembly**
   - `engine_v2.compute` returns an `EngineResult` containing:
     - `spec_id`, `style_code`, `sfg_mode`, `engine_version`,
     - `products`,
     - `stage_graph`,
     - `rm_requirements`,
     - `metrics` (with `extra`),
     - `pieces` from slitting,
     - `trace` (list of `(step_name, payload)`).

BoM and routing generation is then handled in `corr_mrp_bridge`
(`bom_adapter.build_bom_from_engine`, wizard integration) using
`EngineResult` as input.

---

## 2. Key Metrics Produced by the Engine

Important metrics and derived values:

- `metrics.m2_per_box`
  - net area per finished box, based on full blank dimensions
    (before splitting).

- `metrics.kg_per_box`
  - net mass per box from liner/medium GSMs and `m2_per_box`.

- `metrics.extra["layout_blanks_per_sheet"]`
  - number of blank pieces per sheet from layout.

- `metrics.extra["pieces_per_box"]`
  - number of blank pieces required per finished box due to splitting.
  - `1` when no split is required.

- `metrics.extra["boxes_per_sheet"]`
  - effective boxes per sheet:
    - `boxes_per_sheet = layout_blanks_per_sheet / pieces_per_box`.

- `metrics.extra["layout"]`
  - full layout dict: orientation, rows, columns, sheet usage, waste,
    etc.

- **UPS-related semantics**
  - `metrics.printing_ups`
    - printing UPS resolved from plant/spec (after any overrides or hints).
  - `metrics.diecut_ups`
    - die-cut UPS resolved from plant/spec.
  - `metrics.effective_ups`
    - **machine-limited UPS**, defined as `min(printing_ups, diecut_ups)`
      (after applying any enforcement rules). This value is used by routing
      rules and the StageGraph (e.g. final SFGâ†’FG edge ratios) and is not
      adjusted by layout geometry.
  - `metrics.extra["effective_ups"]`
    - in **plant_default/manual** sheet modes: mirrors `metrics.effective_ups`;
    - in **derived** sheet mode: equals the **layout/geometric UPS**,
      i.e. `layout_blanks_per_sheet`, so that raw JSON reflects the actual
      blanks-per-sheet used for layout even when machine UPS limits differ.
  - `metrics.extra["effective_ups_machine"]`
    - always carries the machine-limited UPS (`metrics.effective_ups`) for
      diagnostics and downstream consumers that care about machine capacity.
  - `metrics.extra["layout_blanks_per_sheet"]`
    - number of blank pieces per sheet from layout (geometric capacity).
  - `metrics.extra["boxes_per_sheet"]`
    - user-facing boxes per sheet:
      - `boxes_per_sheet = layout_blanks_per_sheet / pieces_per_box`,
      - this is the primary value surfaced in Engine V2 debug previews.

- **Slitting trace normalization**
  - `splitting_resolved` trace step:
    - always reports `pieces_per_box` and `effective_blank_width_mm` from
      the splitting phase.
  - `slitting_resolved` trace step:
    - `pieces` is the list of logical `Piece.key` values created by slitting;
    - `pieces_per_sheet` is the logical piece count per sheet:
      - when slitting creates pieces: `pieces_per_sheet = len(pieces)`,
      - when **no slitting** applies: `pieces_per_sheet` is normalized to `1`
        even though `pieces` is empty; this avoids a misleading `0` and is
        purely a trace convention (it does **not** change layout, UPS, or
        StageGraph behavior).

- `rm_requirements`
  - a list of `RmRequirement` entries:
    - `product_key`: logical RM key (e.g. `"rm_liner"`),
    - `role`: `"liner"`, `"medium"`, `"top_roll"`, `"top_sheet_external"`, etc,
    - `stage_key`: which stage this RM feeds (`"2ply"`, `"ts_print"`,
      `"board"`, â€¦),
    - `layer_no`: layer index for multiâ€‘ply boards,
    - `qty_per_fg`: quantity per finished box,
    - `qty_uom`: unit of measure (`"kg"`, `"m2"`, â€¦).

- `stage_graph`
  - material stages (`Stage`) and flows (`FlowEdge`):
    - `Stage.key`, `Stage.logical_role`, `Stage.stage_type` (`material` vs `process`),
      `Stage.operations`.
    - `FlowEdge` edges with `qty_per_fg` to describe SFG movement.

These metrics are consumed by the BoM adapter and Odoo tests and are
also used in golden snapshot tests to detect regressions.

---

## 3. Role of Each Engine V2 Module

All modules under `custom_addons/corr_mrp_bridge/engine_v2/` are pure
Python and Odooâ€‘agnostic.

- `__init__.py`
  - Defines core types:
    - `EngineSpecInput` (via import),
    - `EngineMetrics`,
    - `Stage`, `FlowEdge`, `StageGraph`,
    - `EngineResult`.
  - Implements:
    - `build_stage_graph`,
    - `calculate_metrics`,
    - StageGraph validation and quantity propagation,
    - `compute` pipeline orchestration.

- `spec_input.py`
  - Defines `EngineSpecInput`.
  - Implements `EngineSpecInput.from_spec(spec)` for duckâ€‘typed Odoo
    specs.

- `plant_profiles.py`
  - Defines `PlantProfile`:
    - stages, code, name,
    - `printing_ups`, `diecut_ups`,
    - `allow_blank_rotation`,
    - `sheet_length_mm`, `sheet_width_mm`.
  - Implements `resolve_plant_profile` from arbitrary plant config.

- `ups.py`
  - Resolves:
    - `printing_ups`,
    - `diecut_ups`,
    - `effective_ups = min(printing_ups, diecut_ups)`.

- `styles/`
  - `styles/registry.py`: `STYLE_REGISTRY` and `@register_style`.
  - `styles/__init__.py`:
    - autoloads all `style_*` modules via `pkgutil.iter_modules`.
  - `styles/style_0201.py`:
    - example style handler for 0201 RSC blank geometry.

- `board_structure.py`
  - Converts `EngineSpecInput` board data into a `BoardStructure`
    with `BoardLayer` entries.

- `rm_rules.py`
  - Maps `BoardStructure` + plant stages into `rm_requirements`
    with correct stage routing and layer alignment.

- `layout.py`
  - Computes sheet layout based on blank dimensions and sheet size,
    including optional rotation.

- `splitting.py`
  - Applies machine width limits to determine `pieces_per_box` and
    effective blank width for layout/slitting.

- `slitting.py`
  - Derives `Piece` objects representing slit pieces from a sheet.

- `waste.py`
  - Computes waste percentages and optionally applies them to
    `kg_per_box_with_waste` in `metrics.extra`.

---

## 4. Rules and Separation of Concerns

To keep Engine V2 maintainable and testable, follow these rules:

1. **Engine V2 must remain pure Python**
   - No imports of `odoo`, `models`, `fields`, or any ORM APIs inside
     `engine_v2`.
   - All external integration happens via adapters in
     `corr_mrp_bridge` (e.g. `spec_adapter`, `plant_adapter`,
     `bom_adapter`).

2. **No Odoo imports inside `engine_v2`**
   - Engine modules may import from:
     - the Python standard library,
     - other `engine_v2` modules.
   - Engine code must be importable and runnable under plain `python`
     / `unittest` without a database.

3. **BoM and routing generation stay in `corr_mrp_bridge`**
   - `bom_adapter.build_bom_from_engine(env, engine_result, fg_product)`
     is the Odooâ€‘side adapter:
     - maps `rm_requirements` â†’ `mrp.bom.line` using
       `corr.material.product.map`,
     - derives `mrp.routing.workcenter` operations from `StageGraph`,
       layout and UPS,
     - marks engineâ€‘generated lines (`corr_engine_generated`) so they
       can be safely replaced.
   - The engine itself does not know about BoMs, products, or
     workcenters.

4. **Context flags gate Engine V2 usage**
   - The BoM wizard only uses Engine V2 + BoM adapter when the context
     flag `engine_v2_enabled` is set; otherwise it uses the legacy
     engine.

---

## 5. Golden Regression Snapshot Tests

Golden snapshot tests are used to detect unintended changes to core
Engine V2 behavior.

- Location:
  - Fixtures: `custom_addons/corr_mrp_bridge/tests/fixtures/engine_v2/`
  - Tests: `custom_addons/corr_mrp_bridge/tests/test_engine_v2_snapshots.py`

- Current snapshots:
  1. `0201_small_box.json`
     - Small 0201 box, no splitting.
     - Checks:
       - `layout_blanks_per_sheet`,
       - `boxes_per_sheet`,
       - `pieces_per_box`,
       - perâ€‘box RM requirements,
       - StageGraph material stages.

  2. `0201_large_box_split.json`
     - Large 0201 box that exceeds machine width limits and is split
       into multiple pieces.
     - Verifies:
       - `pieces_per_box > 1`,
       - layout capacity, boxes per sheet,
       - perâ€‘box RM usage,
       - StageGraph stays consistent.

  3. `0201_layout_gt_ups.json`
     - 0201 case where `layout_blanks_per_sheet > effective_ups`.
     - Ensures layout metrics and RM requirements remain stable even
       when UPS affects downstream routing.

- Serializer:
  - `_serialize_engine_result(result)` in
    `test_engine_v2_snapshots.py`:
    - reads `metrics.extra["layout_blanks_per_sheet"]`,
      `metrics.extra["boxes_per_sheet"]`, `metrics.extra["pieces_per_box"]`,
    - collects `rm_requirements` with:
      - `product_key`, `role`, `stage_key`, `layer_no`,
      - `qty_per_fg` (rounded to 6 decimals),
      - `qty_uom`,
    - collects `stage_graph.stages` (`key`, `logical_role`,
      `stage_type`, `operations`),
    - sorts RM entries into a deterministic order.

- How tests work:
  - Each test builds a specific `EngineSpecInput` and `PlantProfile`,
    runs `compute(spec_input, plant)`, serializes the result, and
    compares to the JSON fixture.
  - If any of the serialized fields differ, the test fails.

### Updating snapshots after intentional changes

If engine logic changes **intentionally** (e.g. a new style rule,
altered splitting policy, or StageGraph change), snapshot tests will
fail. To update them:

1. Identify which snapshot(s) fail:
   - Run:
     `python -m unittest discover custom_addons/corr_mrp_bridge/tests "test_engine_v2*.py"`

2. For each failing case:
   - Reâ€‘run the test in isolation (or reproduce the inputs):
     - Use the spec/plant configuration in the test
       (`test_engine_v2_snapshots.py`) to reconstruct the
       `EngineSpecInput` and `PlantProfile`.
     - Call `compute(spec_input, plant)` and serialize via
       `_serialize_engine_result`.

3. Inspect the differences:
   - Confirm that the new behavior is desired and consistent (e.g. a
     new style rule or splitting fix).

4. Update the fixture JSON:
   - Replace the contents of the corresponding `.json` file under
     `tests/fixtures/engine_v2/` with the new serialized output.
   - Keep numeric values rounded as done by the serializer (6
     decimals).

5. Reâ€‘run engine tests:
   - Confirm that:
     `python -m unittest discover custom_addons/corr_mrp_bridge/tests "test_engine_v2*.py"`
     passes.

Only update snapshots when the change is deliberate and you understand
its impact on layout, splitting, RM usage, or stage topology. If a
snapshot test fails unexpectedly, it is a strong signal that a recent
change may have altered core Engine V2 behavior.



