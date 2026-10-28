# Corrugation Engine v2 â€“ Plan and Data Model

This document summarises the new `engine_v2` design for `corr_mrp_bridge`.
The goal is to separate pure calculation from Odoo BoM rendering and make
the corrugation engine easier to extend (styles, plants, slitting, etc.).

## EngineSpecInput

`engine_v2/spec_input.py` defines `EngineSpecInput`, a normalised view of a
corrugation spec. It does **not** depend on Odoo models.

Key fields:

- `blank_length_mm`, `blank_width_mm` â€“ finished blank dimensions.
- `liner_gsm`, `medium_gsm` â€“ paper grammage for liner and medium.
- `style_code` â€“ e.g. `0210`, `0203`, `BMC`, future styles such as honeycomb.
- `sfg_mode` â€“ engine SFG mode (`none`, `phantom`, `staged`, etc.).
- `spec_id`, `spec_name` â€“ identity/label only.

`EngineSpecInput.from_spec(spec)` reads a raw Odoo spec object using
`getattr` and casts values defensively so it can be used in tests and in
future spec models.

## StageGraph

`StageGraph` represents the material flow as a directed acyclic graph.

Core pieces:

- `Stage` â€“ a process stage:
  - `key`: e.g. `2ply`, `board`, `fg`.
  - `stage_type`: `"material"` or `"process"`.
    - **Material** stages produce/transform distinct SFGs.
    - **Process** stages perform operations only and do **not** create
      new flow edges.
  - `logical_role`: `"corrugation"`, `"conversion"`, `"finishing"`, etc.
  - `operations`: list of process steps to attach as work orders
    (e.g. `["printing", "diecut"]`).
- `FlowEdge` â€“ a connection between material stages:
  - `from_stage`, `to_stage`.
  - `product_key`: which logical product flows (e.g. `sfg_2ply`).
  - `qty_per_fg`: quantity of that product per finished box.
  - optional `piece_key` for slitting outputs.

`StageGraph.validate()` ensures:

- All `from_stage` / `to_stage` keys exist.
- The graph is a DAG (no cycles) using a topological sort.

Helpers:

- `incoming(stage_key)`, `outgoing(stage_key)`.
- `stage_order()` â€“ returns a topological order of stage keys.

## EngineResult

`EngineResult` is the top-level return type of `engine_v2.compute()`:

- `spec_id`, `style_code`, `sfg_mode`, `engine_version`.
- `products: List[EngineProduct]`:
  - logical products with `key`, `role` (`fg`, `sfg`, `rm`, `piece`,
    `waste`), description and uom.
- `stage_graph: StageGraph` â€“ material stages and flows.
- `rm_requirements: List[RmRequirement]`:
  - per-FG RM usage with `product_key`, `role`, `stage_key`, `layer_no`,
    `qty_per_fg`, `qty_uom`.
- `metrics: EngineMetrics`:
  - `kg_per_box`, `m2_per_box`, `waste_pct`, `ups`, plus `extra`.
- `pieces: List[Piece]`:
  - slit or cut pieces derived from a parent sheet (for unequal slitting,
    multi-piece boxes).
- `snapshot` â€“ arbitrary JSON-like dict for debugging.
- `trace` â€“ list of `(step_name, payload)` entries for debug/analysis.

`EngineResult.validate()` checks product references and calls
`stage_graph.validate()` to prevent invalid configurations.

## Current Implemented Flow (Minimal Slice)

The first implementation slice has:

- Material stages: `2ply -> board -> fg`.
  - `2ply`: corrugation stage, `logical_role="corrugation"`.
  - `board`: conversion stage, `logical_role="conversion"`.
  - `fg`: finished box, `logical_role="finishing"`.
- Process operations:
  - Any additional entries in `plant_profile.stages` that are not one of
    `2ply`, `board`, `fg` are attached to the nearest material stage as
    `Stage.operations` (e.g. `board (printing, diecut)`).
- Flows:
  - `2ply -> board [sfg_2ply]` with `qty_per_fg=1.0`.
  - `board -> fg [fg_box]` with `qty_per_fg=1.0`.
- Products:
  - `rm_liner` (ROLE_RM, kg).
  - `rm_medium` (ROLE_RM, kg).
  - `sfg_2ply` (ROLE_SFG, unit).
  - `sfg_board` (ROLE_SFG, unit) â€“ reserved for future logic.
  - `fg_box` (ROLE_FG, unit).
- Metrics:
  - `m2_per_box = (blank_length_mm * blank_width_mm) / 1_000_000`.
  - `kg_per_box = (liner_gsm + medium_gsm) * m2_per_box / 1000`.
- Raw-material requirements:
  - `rm_liner` and `rm_medium` at stage `2ply`, layer 1, with quantities
    derived from `m2_per_box` and GSM.

The BoM wizard can run `engine_v2.compute()` in **debug mode** via the
`engine_v2_debug` context flag. It logs stages (with operations), flows,
metrics and RM requirements but still uses the existing engine to build
BoMs.

A basic unit test (`tests/test_engine_v2_basic.py`) verifies stages,
flows, metrics and RM requirements for a simple spec.

## Future Goals

The intention is to gradually move all corrugation logic into `engine_v2`
and keep `corr_mrp_bridge` as a thin adapter that renders `EngineResult`
into Odoo products and BoMs.

Planned directions:

- **Styles**
  - Introduce `engine_v2.styles` with style rules for:
    - current 0210/0203 styles,
    - existing BCM custom style,
    - future styles (e.g. honeycomb).
  - Style rules will build stage graphs, products and RM usage based on
    `EngineSpecInput`.

- **Plant Profiles**
  - Introduce `engine_v2.plants` with plant profiles describing which
    stages and operations exist for a plant (fully-automated, semi,
    manual).
  - `compute()` will combine style rules + plant profile to create the
    final `StageGraph`.

- **Slitting & Multi-piece Boxes**
  - Use `Piece` + `FlowEdge.piece_key` to model:
    - unequal slitting (70â€“30, 40â€“60, etc.),
    - multi-piece boxes where multiple pieces from one sheet feed
      different downstream stages.

- **Bridge Adapter**
  - Implement a new rendering path in `corr_mrp_bridge` that:
    - calls `engine_v2.compute()`,
    - creates/updates SFG products and BoMs based on `EngineResult`,
    - eventually replaces the current BoM-mutation-based engine once
      parity and confidence are achieved.

The long-term goal is a clear, testable engine layer that can support new
styles, plants and rules without deep refactoring or BoM-side hacks.




