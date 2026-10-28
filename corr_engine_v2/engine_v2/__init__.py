from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .spec_input import EngineSpecInput, PiecePlanInput, PieceSpecInput
from .plant_profiles import PlantProfile, resolve_plant_profile
from .ups import resolve_all_ups
from .styles.contract import normalize_style_result, require_registered_style
from .styles.registry import STYLE_REGISTRY


StageKey = str
ProductKey = str

# Engine version identifier, useful for compatibility and debugging.
ENGINE_VERSION = "2.0"

# Product role constants to avoid string typos.
ROLE_FG = "fg"
ROLE_SFG = "sfg"
ROLE_RM = "rm"
ROLE_PIECE = "piece"
ROLE_WASTE = "waste"

# Canonical stage definitions: key -> (display_name, logical_role)
STAGE_DEFS: Dict[StageKey, Tuple[str, str]] = {
    "ts_cut": ("Top Sheet Cut", "conversion"),
    "ts_print": ("Top Sheet Printing", "printing"),
    "lamination": ("Film Lamination", "lamination"),
    "2ply": ("2-ply Corrugation", "corrugation"),
    "board": ("Board Conversion", "conversion"),
    "diecut": ("Diecut", "conversion"),
    "fg": ("Finished Box", "finishing"),
}

MATERIAL_STAGE_SEMANTICS: Set[str] = {"corrugation", "conversion", "fg"}
OPERATION_STAGE_SEMANTICS: Set[str] = {"printing", "lamination", "diecut", "auxiliary"}


@dataclass
class Piece:
    """Logical piece produced by slitting or cutting a parent sheet.

    This stays internal to the engine. The bridge layer decides whether
    a Piece maps to its own SFG product or is aggregated elsewhere.
    """

    key: str
    parent_product_key: ProductKey
    ratio: float
    width_mm: Optional[float] = None
    length_mm: Optional[float] = None
    description: str = ""


@dataclass
class EngineProduct:
    """Logical product used in the flow (FG, SFG, or RM)."""

    key: ProductKey
    role: str  # ROLE_FG, ROLE_SFG, ROLE_RM, ROLE_PIECE, ROLE_WASTE
    description: str
    uom: str  # "unit", "kg", "m2", etc.


@dataclass
class Stage:
    """One logical stage in the *material* StageGraph.

    This structure describes the canonical manufacturing chain used by
    the engine for:
      - SFG flow (material stages),
      - high-level routing semantics (attached operations).

    Attributes
    ----------
    key:
        Stable stage code (e.g. "2ply", "board", "fg").
    name:
        Human-readable name for display / diagnostics.
    stage_type:
        Distinguishes material vs process stages:
        - "material": transforms or produces a distinct material/SFG and
          participates in StageGraph.flows.
        - "process": reserved for future use; in the current engine
          slice all created Stage instances are material stages and
          process-only steps are captured in ``Stage.operations``.
    logical_role:
        High-level category such as "corrugation", "conversion",
        "printing", "lamination", "finishing".
    operations:
        List of process / routing operation codes that conceptually run
        at this material stage (e.g. ["ts_print", "diecut"]). These do
        not create additional material nodes.
    output_kind:
        Optional output classification from routing metadata.
    is_terminal:
        Marks the logical final material stage when routing metadata
        explicitly identifies it or when the engine infers it from the
        final material sink.
    plant_profile:
        Optional plant profile code this stage was derived from.
    """

    key: StageKey
    name: str
    stage_type: str  # "material" or "process"
    logical_role: str = ""
    operations: List[str] = field(default_factory=list)
    output_kind: str = ""
    is_terminal: bool = False
    plant_profile: str = ""
    base_stage_key: str = ""
    piece_key: Optional[str] = None
    stage_kind: str = ""  # normal / piece_branch / merge
    merge_policy: str = ""


@dataclass
class FlowEdge:
    """One edge in the stage graph for SFG movement."""

    from_stage: Optional[StageKey]  # None if produced directly from RM
    to_stage: StageKey
    product_key: ProductKey
    qty_per_fg: float
    piece_key: Optional[str] = None  # optional reference to a Piece.key


@dataclass
class RmRequirement:
    """Raw-material requirement per finished good unit."""

    product_key: ProductKey
    role: str  # "liner", "medium", "adhesive", "ink", ...
    stage_key: Optional[StageKey]
    layer_no: Optional[int]
    qty_per_fg: float
    qty_uom: str
    # How this RM quantity is interpreted per finished good:
    # - "area": qty_per_fg is mass/volume derived from m2_per_box (e.g. paper).
    # - "unit": qty_per_fg is a discrete count per box (e.g. labels).
    # - "length": qty_per_fg is a linear length per box (e.g. tape, glue).
    consumption_type: str = "area"
    # Optional metadata for downstream adapters (e.g. gsm, layer info).
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineMetrics:
    """Key scalar metrics derived from the spec."""

    kg_per_box: float = 0.0
    m2_per_box: float = 0.0
    waste_pct: float = 0.0
    ups: float = 1.0
    printing_ups: int = 1
    diecut_ups: int = 1
    effective_ups: int = 1
    # Extra metrics and diagnostics; keys may map to scalars or small
    # nested dicts (e.g., layout, waste breakdown).
    extra: Dict[str, object] = field(default_factory=dict)


@dataclass
class StageGraph:
    """Material StageGraph used by EngineResult.

    This graph describes the *logical material chain* for the engine:
    which SFGs exist, how they flow, and where process operations are
    attached. It is distinct from the quantity StageGraph implemented
    in :mod:`engine_v2.stage_graph`, which models order-level quantities
    across a fixed RM â†’ board â†’ sheet â†’ blanks â†’ FG flow.
    """

    stages: List[Stage] = field(default_factory=list)
    flows: List[FlowEdge] = field(default_factory=list)

    def index_by_key(self) -> Dict[StageKey, Stage]:
        return {stage.key: stage for stage in self.stages}

    def outgoing(self, stage_key: StageKey) -> List[FlowEdge]:
        """All edges going out of the given stage."""
        return [e for e in self.flows if e.from_stage == stage_key]

    def incoming(self, stage_key: StageKey) -> List[FlowEdge]:
        """All edges coming into the given stage."""
        return [e for e in self.flows if e.to_stage == stage_key]

    def validate(self) -> None:
        """Basic sanity checks, including a DAG check for the stage graph.

        Raises ValueError if:
        - flows reference unknown stage keys, or
        - a cycle is detected between stages.
        """

        stage_keys: Set[StageKey] = {s.key for s in self.stages}

        # Check that all referenced stages exist.
        for edge in self.flows:
            if edge.from_stage and edge.from_stage not in stage_keys:
                raise ValueError(f"Flow from unknown stage {edge.from_stage!r}")
            if edge.to_stage not in stage_keys:
                raise ValueError(f"Flow to unknown stage {edge.to_stage!r}")

        # Detect cycles using Kahn's algorithm (topological sort).
        adj: Dict[StageKey, List[StageKey]] = {k: [] for k in stage_keys}
        indegree: Dict[StageKey, int] = {k: 0 for k in stage_keys}
        for edge in self.flows:
            if edge.from_stage and edge.from_stage in stage_keys and edge.to_stage in stage_keys:
                adj[edge.from_stage].append(edge.to_stage)
                indegree[edge.to_stage] += 1

        # Nodes with no incoming edges.
        queue: List[StageKey] = [k for k, d in indegree.items() if d == 0]
        visited = 0

        while queue:
            current = queue.pop()
            visited += 1
            for nxt in adj[current]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)

        if visited != len(stage_keys):
            raise ValueError("StageGraph contains a cycle; stage configuration or mapping is invalid.")

    def stage_order(self) -> List[StageKey]:
        """Return a topologically sorted list of stage keys.

        Assumes the graph is a DAG; callers should run validate() first
        if they need a strict guarantee.
        """

        stage_keys: Set[StageKey] = {s.key for s in self.stages}
        adj: Dict[StageKey, List[StageKey]] = {k: [] for k in stage_keys}
        indegree: Dict[StageKey, int] = {k: 0 for k in stage_keys}
        for edge in self.flows:
            if edge.from_stage and edge.from_stage in stage_keys and edge.to_stage in stage_keys:
                adj[edge.from_stage].append(edge.to_stage)
                indegree[edge.to_stage] += 1

        queue: List[StageKey] = [k for k, d in indegree.items() if d == 0]
        order: List[StageKey] = []

        while queue:
            current = queue.pop()
            order.append(current)
            for nxt in adj[current]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)

        return order

    def final_stage_key(self) -> Optional[StageKey]:
        """Return the logical final material stage key, if any.

        Prefer an explicitly marked terminal stage when present.
        Otherwise use the last sink node in the material stage chain.
        This keeps the engine robust when routings rename the FG stage
        or end on a custom final material stage.
        """

        if not self.stages:
            return None

        explicit_terminal = [
            s.key
            for s in self.stages
            if getattr(s, "is_terminal", False)
            and str(getattr(s, "stage_type", "") or "").strip().lower() == "material"
        ]
        if explicit_terminal:
            return explicit_terminal[-1]

        material_stages = [
            s
            for s in self.stages
            if str(getattr(s, "stage_type", "") or "").strip().lower() == "material"
        ]
        if not material_stages:
            return None

        stage_keys: Set[StageKey] = {s.key for s in material_stages}
        non_sinks: Set[StageKey] = set()
        for edge in self.flows:
            if edge.from_stage and edge.from_stage in stage_keys:
                non_sinks.add(edge.from_stage)
        sinks: List[StageKey] = [s.key for s in material_stages if s.key not in non_sinks]
        if sinks:
            # Prefer the last material sink in the declared stage order.
            return sinks[-1]
        # Fallback: last declared material stage.
        return material_stages[-1].key


@dataclass
class EngineResult:
    """Pure calculation result returned by the engine.

    The bridge layer (corr_mrp_bridge) is responsible for turning this
    into concrete Odoo products, BoMs and operations.
    """

    spec_id: int
    style_code: str
    sfg_mode: str
    products: List[EngineProduct]
    stage_graph: StageGraph
    rm_requirements: List[RmRequirement]
    metrics: EngineMetrics
    engine_version: str = ENGINE_VERSION
    pieces: List[Piece] = field(default_factory=list)
    piece_plan: Optional[PiecePlanInput] = None
    snapshot: Dict[str, object] = field(default_factory=dict)
    trace: List[Tuple[str, Dict[str, object]]] = field(default_factory=list)

    def validate(self) -> None:
        """Run basic consistency checks on the engine output.

        Intended for use in tests and during early rollout to catch
        misconfigurations or implementation bugs early.
        """

        product_keys: Set[ProductKey] = {p.key for p in self.products}
        for edge in self.stage_graph.flows:
            key = str(getattr(edge, "product_key", "") or "")
            if key.startswith("sfg_") and key not in product_keys:
                product_keys.add(key)
                self.products.append(
                    EngineProduct(
                        key=key,
                        role=ROLE_SFG,
                        description="SFG for %s" % key[4:].replace("__", " / "),
                        uom="unit",
                    )
                )
        product_keys: Set[ProductKey] = {p.key for p in self.products}

        # All flows and RM requirements should reference known products.
        for edge in self.stage_graph.flows:
            if edge.product_key not in product_keys:
                key = str(edge.product_key or "")
                if key.startswith("sfg_"):
                    product_keys.add(key)
                    self.products.append(
                        EngineProduct(
                            key=key,
                            role=ROLE_SFG,
                            description="SFG for %s" % key[4:].replace("__", " / "),
                            uom="unit",
                        )
                    )
                    continue
                raise ValueError(f"Flow uses unknown product_key {edge.product_key!r}")
        for rm in self.rm_requirements:
            if rm.product_key not in product_keys:
                raise ValueError(f"RM requirement uses unknown product_key {rm.product_key!r}")

        # Validate the stage graph (including DAG property).
        self.stage_graph.validate()

    def product_index(self) -> Dict[ProductKey, EngineProduct]:
        """Convenience index for quick product lookups by key."""
        return {p.key: p for p in self.products}


# Late imports to avoid circular dependencies during module initialisation.
from .slitting import resolve_slitting  # noqa: E402
from .waste import resolve_waste  # noqa: E402
from .rm_rules import resolve_rm_inputs  # noqa: E402
from .layout import resolve_layout  # noqa: E402
from .splitting import resolve_splitting  # noqa: E402
from .multi_piece import resolve_piece_plan, piece_plan_to_dict  # noqa: E402


# ---------------------------------------------------------------------------
# Internal pipeline helpers
# ---------------------------------------------------------------------------


def resolve_spec(spec) -> EngineSpecInput:
    """Normalise a raw spec or EngineSpecInput into EngineSpecInput."""
    if isinstance(spec, EngineSpecInput):
        return spec
    return EngineSpecInput.from_spec(spec)


def resolve_plant(plant_profile, options=None) -> PlantProfile:
    """Normalise an arbitrary plant profile into PlantProfile."""
    return resolve_plant_profile(plant_profile, options=options or {})


def resolve_ups_for_engine(plant: PlantProfile, options=None) -> Tuple[int, int, int]:
      """Resolve all UPS values for metrics/stage sizing.

      Semantics:
      - printing_ups / diecut_ups come from plant/spec (after any hints/overrides).
      - effective_ups is the machine-limited UPS (min(printing_ups, diecut_ups)
        plus any enforcement), used for routing and StageGraph edge ratios.
      - Layout may further limit boxes_per_sheet geometrically; in derived-sheet
        mode that geometric UPS is exposed separately in metrics.extra.
      """
      return resolve_all_ups(plant, options=options or {})


def _project_geometry_into_spec_input(spec_input: EngineSpecInput) -> None:
    """Ensure GeometrySpec fields are reflected on the spec input.

    This keeps GeometrySpec as the primary source of truth for V2 specs
    while preserving backwards compatibility with existing style and
    layout code that reads attributes directly from EngineSpecInput.
    """

    try:
        geometry = getattr(spec_input, "geometry", None)
    except Exception:
        geometry = None
    if geometry is None:
        return

    def _set_if_missing(attr: str, value) -> None:
        try:
            current = getattr(spec_input, attr, None)
        except Exception:
            current = None
        # Only backfill when the attribute is missing or effectively zero.
        if current in (None, ""):
            try:
                setattr(spec_input, attr, value)
            except Exception:
                pass
        else:
            try:
                # For numeric attributes treat 0/0.0 as "missing".
                if isinstance(current, (int, float)) and float(current) == 0.0:
                    setattr(spec_input, attr, value)
            except Exception:
                pass

    _set_if_missing("box_length_mm", getattr(geometry, "box_length_mm", 0.0))
    _set_if_missing("box_width_mm", getattr(geometry, "box_width_mm", 0.0))
    _set_if_missing("box_height_mm", getattr(geometry, "box_height_mm", 0.0))

    glue_override = getattr(geometry, "glue_flap_override_mm", 0.0)
    if glue_override and glue_override > 0.0:
        _set_if_missing("glue_flap_mm", glue_override)


def _stage_terms(stage: Stage) -> Set[str]:
    values = {
        getattr(stage, "key", ""),
        getattr(stage, "logical_role", ""),
        getattr(stage, "output_kind", ""),
    }
    try:
        values.update(getattr(stage, "operations", []) or [])
    except Exception:
        pass
    terms: Set[str] = set()
    for value in values:
        try:
            text = str(value or "").strip().lower()
        except Exception:
            text = ""
        if text:
            terms.add(text)
    return terms


def _yield_label_for_stage(
    stage_graph: StageGraph,
    style_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve generic FG-per-sheet yield labels from route metadata.

    This is intentionally keyed by the style contract's yield metadata,
    not by any specific style code.
    """

    if (style_metadata or {}).get("yield_basis") != "fg_per_sheet":
        return {}

    stages = list(getattr(stage_graph, "stages", []) or [])
    if not stages:
        return {
            "yield_stage_kind": "terminal",
            "yield_output_label": "usable blanks",
        }

    peeling_aliases = {
        str(item or "").strip().lower()
        for item in (style_metadata.get("peeling_aliases") or [])
        if str(item or "").strip()
    }
    peeling_aliases.update({"peeling", "stripping", "waste stripping", "peel-off", "peeloff"})

    diecut_aliases = {"diecut", "slot/die", "slot_die", "slot die"}
    conversion_aliases = {
        "seaming",
        "gluing",
        "glue",
        "stitching",
        "conversion",
        "finishing",
        "fg",
        "final",
    }

    diecut_index = None
    peeling_index = None
    conversion_index = None
    for index, stage in enumerate(stages):
        terms = _stage_terms(stage)
        if diecut_index is None and terms & diecut_aliases:
            diecut_index = index
        if peeling_index is None and terms & peeling_aliases:
            peeling_index = index
        if conversion_index is None and terms & conversion_aliases:
            conversion_index = index

    if diecut_index is not None:
        stage = stages[diecut_index]
        has_later_peeling = peeling_index is not None and peeling_index > diecut_index
        return {
            "yield_stage_key": stage.key,
            "yield_stage_kind": "diecut",
            "yield_output_label": "gross blanks" if has_later_peeling else "usable blanks",
            "yield_downstream_stage_key": stages[peeling_index].key if has_later_peeling else "",
            "yield_downstream_output_label": "usable blanks" if has_later_peeling else "",
        }

    if peeling_index is not None:
        stage = stages[peeling_index]
        return {
            "yield_stage_key": stage.key,
            "yield_stage_kind": "peeling",
            "yield_output_label": "usable blanks",
            "yield_warning": "No diecut stage found. FG-per-sheet yield is applied at the first conversion/terminal stage.",
        }

    if conversion_index is not None:
        stage = stages[conversion_index]
        return {
            "yield_stage_key": stage.key,
            "yield_stage_kind": "conversion",
            "yield_output_label": "Yield applied before %s" % (stage.name or stage.key),
            "yield_warning": "No diecut stage found. FG-per-sheet yield is applied at the first conversion/terminal stage.",
        }

    stage = stages[-1]
    return {
        "yield_stage_key": stage.key,
        "yield_stage_kind": "terminal",
        "yield_output_label": "usable blanks",
        "yield_warning": "No diecut stage found. FG-per-sheet yield is applied at the first conversion/terminal stage.",
    }


def _normalize_stage_semantic(value: object) -> str:
    try:
        text = str(value or "").strip().lower()
    except Exception:
        text = ""
    if text == "finishing":
        return "fg"
    return text


def _stage_def_for_code(stage_code: StageKey) -> Tuple[str, str]:
    return STAGE_DEFS.get(stage_code, (stage_code, ""))


def build_stage_graph(
    spec_input: EngineSpecInput, plant: PlantProfile, effective_ups: int
) -> StageGraph:
    """Build the *material* StageGraph from routing / plant configuration.

    There are two sources of stage semantics:

    1. Routing profile present (spec_input.routing_profile not None)
       - ``routing_profile.stages[*].stage_code`` defines the ordered
         stage sequence.
       - ``stage_type`` on each routing stage is authoritative for
         distinguishing material vs operation stages:
           * "material"  â†’ becomes a Stage node and participates in
             material flows.
           * "operation" â†’ attached to the nearest preceding material
             stage via ``Stage.operations``.
       - The plant profile ``plant.stages`` is ignored for ordering in
         this mode; it may still influence other rules (e.g. RM routing).

    2. No routing profile
       - ``plant.stages`` defines the canonical ordered stage sequence.
       - Each stage code is matched against ``STAGE_DEFS``:
           * known stage codes are treated as material stages;
           * unknown codes are interpreted as process-only steps and
             attached as operations to the most recent material stage.

    The resulting StageGraph:
      - always contains at least a minimal material chain
        ``[\"2ply\", \"board\", \"fg\"]`` when no configuration is
        provided;
      - uses the routing-selected terminal material stage when a route
        is selected, otherwise falls back to ``\"fg\"`` in legacy mode;
      - creates one FlowEdge per consecutive pair of material stages,
        with the final edge into the terminal stage using
        ``1 / effective_ups`` to represent SFG-per-FG when UPS is
        present.
    """

    # Canonical ordered stage keys from either a spec-level routing
    # profile (when present) or the plant profile; if empty, fall back
    # to a minimal material chain.
    routing_profile = getattr(spec_input, "routing_profile", None)
    has_routing_profile = routing_profile is not None
    stage_sequence: List[StageKey] = []
    # When a RoutingProfileInput is present, its metadata is the
    # authoritative source for execution type, stage semantics, and
    # terminal-stage hints.
    explicit_stage_types: Dict[StageKey, str] = {}
    explicit_stage_semantics: Dict[StageKey, str] = {}
    explicit_output_kinds: Dict[StageKey, str] = {}
    explicit_terminal_hints: Dict[StageKey, bool] = {}

    if has_routing_profile:
        try:
            rp_stages = getattr(routing_profile, "stages", None) or []
        except Exception:
            rp_stages = []
        try:
            rp_list = list(rp_stages)
        except TypeError:
            rp_list = [rp_stages] if rp_stages else []
        if rp_list:
            for st in rp_list:
                try:
                    code = str(getattr(st, "stage_code", "") or "").strip()
                except Exception:
                    code = ""
                if not code:
                    continue
                stage_sequence.append(code)
                try:
                    st_type = str(getattr(st, "stage_type", "") or "").strip().lower()
                except Exception:
                    st_type = ""
                semantic = _normalize_stage_semantic(getattr(st, "stage_semantic", "") or "")
                output_kind = _normalize_stage_semantic(getattr(st, "output_kind", "") or "")
                terminal_hint = bool(
                    getattr(st, "is_fg_stage", False)
                    or getattr(st, "can_be_terminal", False)
                    or output_kind in ("fg", "box")
                    or semantic == "fg"
                )
                if st_type in ("material", "operation"):
                    explicit_stage_types[code] = st_type
                elif semantic in MATERIAL_STAGE_SEMANTICS:
                    explicit_stage_types[code] = "material"
                elif semantic in OPERATION_STAGE_SEMANTICS:
                    explicit_stage_types[code] = "operation"
                if semantic:
                    explicit_stage_semantics[code] = semantic
                if output_kind:
                    explicit_output_kinds[code] = output_kind
                if terminal_hint:
                    explicit_terminal_hints[code] = True

    # When no routing profile is attached or it provides no usable
    # stages, fall back to plant.stages and finally to a minimal
    # canonical chain. This keeps legacy behaviour intact while
    # letting routing_profile be fully authoritative when present.
    if not stage_sequence:
        try:
            stage_sequence = [str(k) for k in (plant.stages or [])]
        except Exception:
            stage_sequence = []
    if not stage_sequence:
        stage_sequence = ["2ply", "board", "fg"]

    # Recognised *default* material stages for legacy plant-only
    # configuration are limited to the canonical SFG chain. Routing-
    # profile mode does not consult this set; selected-route material
    # stages come only from routing metadata / stage_type resolution.
    LEGACY_FALLBACK_MATERIAL_STAGE_KEYS: Set[StageKey] = {"2ply", "board", "fg"}

    # material_keys stores the ordered list of material stage codes.
    # operations_by_material maps each material stage to the routing
    # operations that conceptually execute there. When a routing
    # profile is present, stage_graph.stages still includes the full
    # ordered route; operations_by_material is kept so existing RM
    # attachment logic can continue to resolve printing/cutting owners
    # from the nearest material stage.
    material_keys: List[StageKey] = []
    operations_by_material: Dict[StageKey, List[str]] = {}
    current_material: Optional[StageKey] = None
    # Operations that appear before the first material stage in the
    # declared sequence are attached to the first material stage. This
    # allows printing-first routes such as:
    #   ts_cut -> ts_print -> 2ply -> board -> diecut -> fg
    # to keep ts_cut / ts_print associated with the 2ply material stage.
    pending_ops: List[str] = []

    for key in stage_sequence:
        resolved_type = explicit_stage_types.get(key, "")
        # When an explicit stage_type is provided via the routing
        # profile, it takes precedence over the canonical mapping from
        # STAGE_DEFS / plant stages.
        if resolved_type == "material":
            material_keys.append(key)
            current_material = key
            # Attach any operations that appeared before the first
            # material stage to this stage.
            if pending_ops:
                operations_by_material.setdefault(key, []).extend(pending_ops)
                pending_ops = []
            else:
                operations_by_material.setdefault(key, [])
        elif resolved_type == "operation":
            if current_material:
                operations_by_material.setdefault(current_material, []).append(key)
            else:
                pending_ops.append(key)
        else:
            # Fallback when no explicit routing stage_type is provided.
            if has_routing_profile:
                # With an explicit routing profile, untyped stages are
                # interpreted as process-only steps attached as
                # operations to the nearest material stage.
                if current_material:
                    operations_by_material.setdefault(current_material, []).append(key)
                else:
                    pending_ops.append(key)
            else:
                # Plant-only configuration: only the legacy canonical
                # SFG stages become material nodes; all other stage
                # codes are interpreted as process-only and attached as
                # operations.
                if key in LEGACY_FALLBACK_MATERIAL_STAGE_KEYS:
                    material_keys.append(key)
                    current_material = key
                    if pending_ops:
                        operations_by_material.setdefault(key, []).extend(pending_ops)
                        pending_ops = []
                    else:
                        operations_by_material.setdefault(key, [])
                else:
                    # Process-only stage: attach as operation to most
                    # recent material stage (if any).
                    if current_material:
                        operations_by_material.setdefault(current_material, []).append(key)

    # Ensure there is at least one material stage. When a routing
    # profile is present, it is fully authoritative: we never inject
    # default stages or force a specific terminal stage. For legacy
    # plant-only configurations, keep the minimal canonical chain and
    # append an explicit 'fg' stage when missing.
    if not material_keys:
        if has_routing_profile:
            # Degenerate routing: treat the entire flow as a single FG
            # material stage so downstream consumers still have a sink.
            material_keys = ["fg"]
        else:
            material_keys = ["2ply", "board", "fg"]
    if (not has_routing_profile) and material_keys[-1] != "fg":
        material_keys.append("fg")

    if has_routing_profile:
        terminal_candidates = [key for key in material_keys if explicit_terminal_hints.get(key)]
        final_stage_key = terminal_candidates[-1] if terminal_candidates else material_keys[-1]
    else:
        final_stage_key = "fg" if "fg" in material_keys else material_keys[-1]

    # Optionally gate routing operations using the attached surface
    # profile when present. This keeps the StageGraph material chain
    # stable while allowing spec-level flags (e.g. printing_required)
    # to suppress process-only operations.
    try:
        surface = getattr(spec_input, "surface_profile", None)
    except Exception:
        surface = None
    printing_required_flag: Optional[bool] = None
    lamination_required_flag: Optional[bool] = None
    if surface is not None:
        try:
            printing_required_flag = bool(getattr(surface, "printing_required", False))
        except Exception:
            printing_required_flag = None
        try:
            lamination_required_flag = bool(getattr(surface, "lamination_required", False))
        except Exception:
            lamination_required_flag = None

    if printing_required_flag is not None or lamination_required_flag is not None:
        for mat_key, ops in list(operations_by_material.items()):
            filtered_ops: List[str] = []
            for op in ops:
                op_norm = str(op or "").strip().lower()
                op_semantic = explicit_stage_semantics.get(op) or _stage_def_for_code(op)[1]
                if printing_required_flag is False and (
                    op_semantic == "printing" or op_norm in ("ts_print", "printing")
                ):
                    continue
                if lamination_required_flag is False and (
                    op_semantic == "lamination" or op_norm in ("lamination",)
                ):
                    continue
                filtered_ops.append(op)
            operations_by_material[mat_key] = filtered_ops

    stages: List[Stage] = []
    plant_code = plant.code or ""

    if has_routing_profile:
        # Full ordered route for core trace/runtime alignment. Material
        # stages keep their attached operations for RM resolution;
        # operation stages are represented explicitly in the stage list
        # even though they do not generate their own logical products.
        full_stage_keys = list(stage_sequence or material_keys)
    else:
        # Legacy plant-only mode remains material-stage driven.
        full_stage_keys = list(material_keys)

    stage_type_by_key: Dict[StageKey, str] = {}
    for key in full_stage_keys:
        if has_routing_profile:
            stage_type_by_key[key] = explicit_stage_types.get(
                key, "material" if key in material_keys else "operation"
            )
        else:
            stage_type_by_key[key] = "material"

    for key in full_stage_keys:
        name, default_role = _stage_def_for_code(key)
        role = explicit_stage_semantics.get(key) or default_role
        stage_type = stage_type_by_key.get(key, "material")
        ops = operations_by_material.get(key, []) if stage_type == "material" else []
        stages.append(
            Stage(
                key=key,
                name=name,
                stage_type=stage_type,
                logical_role=role,
                operations=ops,
                output_kind=explicit_output_kinds.get(key, ""),
                is_terminal=(stage_type == "material" and key == final_stage_key),
                plant_profile=plant_code,
            )
        )

    flows: List[FlowEdge] = []
    if has_routing_profile:
        material_stage_by_index: Dict[int, Optional[StageKey]] = {}
        next_material_by_index: Dict[int, Optional[StageKey]] = {}
        last_material: Optional[StageKey] = None
        for idx, key in enumerate(full_stage_keys):
            if stage_type_by_key.get(key) == "material":
                last_material = key
            material_stage_by_index[idx] = last_material

        next_material: Optional[StageKey] = None
        for idx in range(len(full_stage_keys) - 1, -1, -1):
            key = full_stage_keys[idx]
            if stage_type_by_key.get(key) == "material":
                next_material = key
            next_material_by_index[idx] = next_material

        prev_key: Optional[StageKey] = None
        for idx, key in enumerate(full_stage_keys):
            if prev_key is None:
                prev_key = key
                continue

            stage_type = stage_type_by_key.get(key, "operation")
            if key == final_stage_key:
                product_key = "fg_box"
                qty = 1.0 / float(effective_ups or 1)
            else:
                carrier_material = (
                    key
                    if stage_type == "material"
                    else material_stage_by_index.get(idx) or next_material_by_index.get(idx)
                )
                if carrier_material == final_stage_key:
                    product_key = "fg_box"
                elif carrier_material:
                    product_key = f"sfg_{carrier_material}"
                else:
                    product_key = "fg_box"
                qty = 1.0

            flows.append(
                FlowEdge(
                    from_stage=prev_key,
                    to_stage=key,
                    product_key=product_key,
                    qty_per_fg=qty,
                )
            )
            prev_key = key
    else:
        # Legacy material flow: consecutive material stages only.
        prev_key: Optional[StageKey] = None
        for key in material_keys:
            if prev_key is not None:
                if key == final_stage_key:
                    product_key = "fg_box"
                    qty = 1.0 / float(effective_ups or 1)
                else:
                    product_key = f"sfg_{key}"
                    qty = 1.0
                flows.append(
                    FlowEdge(
                        from_stage=prev_key,
                        to_stage=key,
                        product_key=product_key,
                        qty_per_fg=qty,
                    )
                )
            prev_key = key

    graph = StageGraph(stages=stages, flows=flows)
    piece_plan = getattr(spec_input, "piece_plan", None)
    if isinstance(piece_plan, PiecePlanInput) and piece_plan.enabled:
        graph = _build_multi_piece_stage_graph(graph, piece_plan, effective_ups)
    return graph


def _build_multi_piece_stage_graph(
    base_graph: StageGraph,
    piece_plan: PiecePlanInput,
    effective_ups: int,
) -> StageGraph:
    """Expand a normal StageGraph into piece branches that merge later."""

    base_stages = list(base_graph.stages or [])
    if not base_stages:
        return base_graph
    merge_stage_key = str(piece_plan.merge_stage_key or "seaming").strip() or "seaming"
    final_stage_key = base_graph.final_stage_key() or base_stages[-1].key
    stage_index = {stage.key: idx for idx, stage in enumerate(base_stages)}

    if merge_stage_key not in stage_index:
        insert_at = stage_index.get(final_stage_key, len(base_stages))
        merge_stage = Stage(
            key=merge_stage_key,
            name=_stage_def_for_code(merge_stage_key)[0],
            stage_type="material",
            logical_role="finishing",
            operations=[],
            output_kind="box",
            is_terminal=False,
            plant_profile=base_stages[0].plant_profile if base_stages else "",
            base_stage_key=merge_stage_key,
            stage_kind="merge",
            merge_policy="all_required",
        )
        base_stages = base_stages[:insert_at] + [merge_stage] + base_stages[insert_at:]
        stage_index = {stage.key: idx for idx, stage in enumerate(base_stages)}

    merge_idx = stage_index.get(merge_stage_key)
    if merge_idx is None or merge_idx <= 0:
        return base_graph

    pre_merge = base_stages[:merge_idx]
    merge_template = base_stages[merge_idx]
    post_merge = base_stages[merge_idx + 1 :]
    pieces = list(piece_plan.pieces or [])
    if len(pieces) < 2:
        return base_graph

    stages: List[Stage] = []
    flows: List[FlowEdge] = []

    for piece in pieces:
        prev_stage_key: Optional[str] = None
        for template in pre_merge:
            stage_key = _piece_stage_key(template.key, piece.piece_key)
            stages.append(_clone_piece_stage(template, stage_key, piece))
            if prev_stage_key is not None:
                flows.append(
                    FlowEdge(
                        from_stage=prev_stage_key,
                        to_stage=stage_key,
                        product_key=_product_key_for_stage(stage_key, final_stage_key=""),
                        qty_per_fg=float(piece.piece_qty_per_fg or 1.0),
                        piece_key=piece.piece_key,
                    )
                )
            prev_stage_key = stage_key

        if prev_stage_key is not None:
            flows.append(
                FlowEdge(
                    from_stage=prev_stage_key,
                    to_stage=merge_stage_key,
                    product_key=_product_key_for_stage(prev_stage_key, final_stage_key=""),
                    qty_per_fg=float(piece.piece_qty_per_fg or 1.0),
                    piece_key=piece.piece_key,
                )
            )

    stages.append(
        Stage(
            key=merge_stage_key,
            name=merge_template.name,
            stage_type="material",
            logical_role=merge_template.logical_role or "finishing",
            operations=list(merge_template.operations or []),
            output_kind=merge_template.output_kind or "box",
            is_terminal=bool(merge_stage_key == final_stage_key and not post_merge),
            plant_profile=merge_template.plant_profile,
            base_stage_key=merge_template.base_stage_key or merge_template.key,
            stage_kind="merge",
            merge_policy="all_required",
        )
    )

    prev_shared = merge_stage_key
    for template in post_merge:
        is_final = template.key == final_stage_key
        stages.append(
            Stage(
                key=template.key,
                name=template.name,
                stage_type=template.stage_type,
                logical_role=template.logical_role,
                operations=list(template.operations or []),
                output_kind=template.output_kind,
                is_terminal=is_final,
                plant_profile=template.plant_profile,
                base_stage_key=template.base_stage_key or template.key,
                stage_kind=template.stage_kind or "normal",
                merge_policy=template.merge_policy,
            )
        )
        flows.append(
            FlowEdge(
                from_stage=prev_shared,
                to_stage=template.key,
                product_key="fg_box" if is_final else _product_key_for_stage(template.key, final_stage_key),
                qty_per_fg=(1.0 / float(effective_ups or 1)) if is_final else 1.0,
            )
        )
        prev_shared = template.key

    return StageGraph(stages=stages, flows=flows)


def _clone_piece_stage(template: Stage, stage_key: str, piece: PieceSpecInput) -> Stage:
    return Stage(
        key=stage_key,
        name="%s - Piece %s" % (template.name, piece.piece_name or piece.piece_key),
        stage_type=template.stage_type,
        logical_role=template.logical_role,
        operations=list(template.operations or []),
        output_kind=template.output_kind,
        is_terminal=False,
        plant_profile=template.plant_profile,
        base_stage_key=template.base_stage_key or template.key,
        piece_key=piece.piece_key,
        stage_kind="piece_branch",
    )


def _piece_stage_key(base_stage_key: str, piece_key: str) -> str:
    return "%s__%s" % (base_stage_key, piece_key)


def _product_key_for_stage(stage_key: str, final_stage_key: str) -> str:
    if stage_key == final_stage_key:
        return "fg_box"
    return "sfg_%s" % stage_key


def generate_products(spec_input: EngineSpecInput, stage_graph: StageGraph) -> List[EngineProduct]:
    """Generate logical products for RM, SFGs and FG."""

    products: List[EngineProduct] = []
    product_keys: Set[ProductKey] = set()

    def _add_product(key: str, role: str, desc: str, uom: str) -> None:
        if key in product_keys:
            return
        product_keys.add(key)
        products.append(EngineProduct(key=key, role=role, description=desc, uom=uom))

    # RM products used by current routing rules.
    _add_product("rm_liner", ROLE_RM, "Liner paper", "kg")
    _add_product("rm_medium", ROLE_RM, "Medium paper", "kg")
    _add_product("rm_carton_board", ROLE_RM, "Carton board", "kg")
    _add_product("rm_top_roll", ROLE_RM, "Top-sheet roll", "kg")
    _add_product("rm_top_sheet_external", ROLE_RM, "External printed top sheet", "kg")
    _add_product("rm_lamination_film", ROLE_RM, "Lamination film", "m2")
    # Consumable RMs (Phase 1: adhesive). These are always part of the
    # logical product index so that RM requirements for adhesives can
    # be validated when the feature flag is enabled, while remaining
    # unused when consumables are disabled.
    _add_product("rm_adhesive_corrugation", ROLE_RM, "Corrugation adhesive", "kg")
    _add_product("rm_adhesive_board", ROLE_RM, "Board adhesive", "kg")
    _add_product("rm_adhesive_fg", ROLE_RM, "Finished-good adhesive", "kg")
    # Ink consumable used at printing stage.
    _add_product("rm_ink", ROLE_RM, "Printing ink", "kg")
    # Wire consumable used for stitched locking at FG stage.
    _add_product("rm_wire", ROLE_RM, "Stitching wire", "kg")

    # SFG products for each material stage except FG.
    final_stage_key = stage_graph.final_stage_key()
    for stage in stage_graph.stages:
        if stage.stage_type != "material":
            continue
        if stage.key == final_stage_key:
            continue
        sfg_key = f"sfg_{stage.key}"
        desc = f"SFG for {stage.name}"
        _add_product(sfg_key, ROLE_SFG, desc, "unit")

    # Routed multi-piece graphs can carry an existing material product
    # through cloned operation stages. Register any SFG key referenced by
    # a flow so validation stays aligned with the graph shape.
    for edge in stage_graph.flows:
        key = str(getattr(edge, "product_key", "") or "")
        if key.startswith("sfg_"):
            _add_product(key, ROLE_SFG, "SFG for %s" % key[4:].replace("__", " / "), "unit")

    # Finished good
    fg_name = getattr(spec_input, "spec_name", "") or "Finished box"
    _add_product("fg_box", ROLE_FG, fg_name, "unit")

    return products


def _ensure_flow_products_registered(
    products: List[EngineProduct], stage_graph: StageGraph
) -> List[EngineProduct]:
    product_keys: Set[ProductKey] = {product.key for product in products}
    completed = list(products)
    for edge in stage_graph.flows:
        key = str(getattr(edge, "product_key", "") or "")
        if not key or key in product_keys:
            continue
        if key.startswith("sfg_"):
            product_keys.add(key)
            completed.append(
                EngineProduct(
                    key=key,
                    role=ROLE_SFG,
                    description="SFG for %s" % key[4:].replace("__", " / "),
                    uom="unit",
                )
            )
    return completed


def _expand_rm_requirements_for_piece_plan(
    rm_requirements: List[RmRequirement],
    piece_plan: PiecePlanInput,
    *,
    source_m2_per_box: float,
) -> List[RmRequirement]:
    """Split area-based RM rows into per-piece rows for preview/materialization."""

    if not piece_plan or not piece_plan.enabled or source_m2_per_box <= 0.0:
        return rm_requirements

    expanded: List[RmRequirement] = []
    area_roles = {"liner", "medium", "top_roll", "top_sheet_external", "lamination_film"}
    for rm in rm_requirements or []:
        consumption_type = str(getattr(rm, "consumption_type", "") or "").strip().lower()
        role = str(getattr(rm, "role", "") or "").strip().lower()
        if consumption_type != "area" or role not in area_roles:
            expanded.append(rm)
            continue

        base_stage_key = _base_stage_key(getattr(rm, "stage_key", None))
        for piece in piece_plan.pieces or []:
            piece_area = (
                float(piece.piece_width_mm or 0.0)
                * float(piece.piece_length_mm or 0.0)
                / 1_000_000.0
            )
            ratio = piece_area / float(source_m2_per_box or 1.0)
            qty = float(getattr(rm, "qty_per_fg", 0.0) or 0.0) * ratio
            metadata = dict(getattr(rm, "metadata", {}) or {})
            metadata.update(
                {
                    "piece_key": piece.piece_key,
                    "piece_name": piece.piece_name,
                    "piece_index": piece.piece_index,
                    "piece_qty_per_fg": piece.piece_qty_per_fg,
                    "piece_width_mm": piece.piece_width_mm,
                    "piece_length_mm": piece.piece_length_mm,
                    "piece_area_m2": piece_area,
                    "source_area_m2": source_m2_per_box,
                    "multi_piece": True,
                }
            )
            expanded.append(
                RmRequirement(
                    product_key=rm.product_key,
                    role=rm.role,
                    stage_key=_piece_stage_key(base_stage_key, piece.piece_key)
                    if base_stage_key
                    else rm.stage_key,
                    layer_no=rm.layer_no,
                    qty_per_fg=qty * float(piece.piece_qty_per_fg or 1.0),
                    qty_uom=rm.qty_uom,
                    consumption_type=rm.consumption_type,
                    metadata=metadata,
                )
            )
    return expanded


def _base_stage_key(stage_key: Optional[str]) -> str:
    text = str(stage_key or "").strip()
    if "__piece_" in text:
        return text.split("__piece_", 1)[0]
    return text


def calculate_metrics(
    spec_input: EngineSpecInput,
    printing_ups: int,
    diecut_ups: int,
    effective_ups: int,
) -> EngineMetrics:
    """Compute basic sheet and weight metrics."""

    blank_length_mm = float(getattr(spec_input, "blank_length_mm", 0.0) or 0.0)
    blank_width_mm = float(getattr(spec_input, "blank_width_mm", 0.0) or 0.0)

    m2_per_box = max(0.0, (blank_length_mm * blank_width_mm) / 1_000_000.0)
    try:
        style_contract = getattr(spec_input, "style_contract", {}) or {}
        style_metadata = dict(style_contract.get("metadata") or {})
        area_m2_per_fg = float(style_metadata.get("area_m2_per_fg") or 0.0)
    except Exception:
        area_m2_per_fg = 0.0
    if area_m2_per_fg > 0.0:
        m2_per_box = area_m2_per_fg

    liner_gsm = float(getattr(spec_input, "liner_gsm", 0.0) or 0.0)
    medium_gsm = float(getattr(spec_input, "medium_gsm", 0.0) or 0.0)

    kg_per_box = ((liner_gsm + medium_gsm) * m2_per_box) / 1000.0 if m2_per_box > 0 else 0.0

    return EngineMetrics(
        kg_per_box=kg_per_box,
        m2_per_box=m2_per_box,
        waste_pct=0.0,
        ups=float(effective_ups or 1),
        printing_ups=printing_ups or 1,
        diecut_ups=diecut_ups or 1,
        effective_ups=effective_ups or 1,
    )


def calculate_rm_requirements(spec_input: EngineSpecInput, metrics: EngineMetrics) -> List[RmRequirement]:
    """Compute basic liner + medium RM requirements per FG."""

    m2_per_box = float(getattr(metrics, "m2_per_box", 0.0) or 0.0)
    liner_gsm = float(getattr(spec_input, "liner_gsm", 0.0) or 0.0)
    medium_gsm = float(getattr(spec_input, "medium_gsm", 0.0) or 0.0)

    liner_kg = (liner_gsm * m2_per_box) / 1000.0 if m2_per_box > 0 else 0.0
    medium_kg = (medium_gsm * m2_per_box) / 1000.0 if m2_per_box > 0 else 0.0

    return [
        RmRequirement(
            product_key="rm_liner",
            role="liner",
            stage_key="2ply",
            layer_no=1,
            qty_per_fg=liner_kg,
            qty_uom="kg",
            consumption_type="area",
            metadata={"gsm": liner_gsm, "layer_no": 1, "type": "liner"},
        ),
        RmRequirement(
            product_key="rm_medium",
            role="medium",
            stage_key="2ply",
            layer_no=1,
            qty_per_fg=medium_kg,
            qty_uom="kg",
            consumption_type="area",
            metadata={"gsm": medium_gsm, "layer_no": 1, "type": "medium"},
        ),
    ]


def compute(spec, plant_profile, stage_mapping=None, options=None) -> EngineResult:
    """Main entry point for the new corrugation engine.

    This function MUST remain sideâ€‘effect free:
    - no ORM writes,
    - no mrp.bom or product creation,
    - no commits.

    It should only inspect the given spec / configuration and return an
    EngineResult describing stages, flows, products, RM requirements and
    metrics. The existing engine remains the source of truth until this
    function is fully implemented and validated.
    """

    _ = stage_mapping  # reserved for future use

    # 1) Resolve spec + plant profile
    spec_input = resolve_spec(spec)
    plant = resolve_plant(plant_profile, options=options)

    # Backfill dynamic attributes from GeometrySpec when available so
    # that style rules and layout logic can rely on a consistent view
    # of box dimensions and glue flap.
    _project_geometry_into_spec_input(spec_input)

    spec_id = spec_input.spec_id
    style_code = spec_input.style_code or ""
    sfg_mode = spec_input.sfg_mode or ""

    trace: List[Tuple[str, Dict[str, object]]] = []
    trace.append(
        (
            "compute_started",
            {
                "spec_id": spec_id,
                "style_code": style_code,
            },
        )
    )
    trace.append(
        (
            "plant_profile_loaded",
            {
                "plant_code": plant.code,
                "plant_name": plant.name,
                "stages": list(plant.stages or []),
            },
        )
    )

    # 1b) Apply style rule to derive blank size, if any.
    # Style-specific blank size resolution via registry. When no handler
    # is registered for the given style_code (or style_code is empty),
    # we leave any existing blank dimensions unchanged.
    handler = None
    style_result = None
    code_norm = (style_code or "").strip().upper()
    if code_norm:
        handler = require_registered_style(code_norm, STYLE_REGISTRY)
    if handler:
        style_result = normalize_style_result(handler(spec_input, plant))
        if style_result is not None:
            new_len = style_result.get("blank_length_mm")
            new_wid = style_result.get("blank_width_mm")
            if new_len is not None:
                spec_input.blank_length_mm = float(new_len or 0.0)
            if new_wid is not None:
                spec_input.blank_width_mm = float(new_wid or 0.0)
            try:
                spec_input.style_contract = style_result.as_dict()
                spec_input.style_capabilities = style_result.capabilities()
            except Exception:
                pass
    trace.append(
        (
            "style_resolved",
            {
                "style_code": style_code,
                "blank_length_mm": getattr(spec_input, "blank_length_mm", 0.0),
                "blank_width_mm": getattr(spec_input, "blank_width_mm", 0.0),
            },
        )
    )

    piece_plan = resolve_piece_plan(spec_input, options=options or {})
    spec_input.piece_plan = piece_plan
    trace.append(
        (
            "piece_plan_resolved",
            piece_plan_to_dict(piece_plan),
        )
    )

    # 2) Resolve UPS
    # Merge any UPS-related overrides from the spec (EngineOverrides,
    # LayoutPolicyInput) into an options dict for UPS resolution.
    ups_options: Dict[str, object] = dict(options or {})
    layout_policy = getattr(spec_input, "layout_policy", None)
    overrides = getattr(spec_input, "overrides", None)

    if layout_policy is not None:
        try:
            if getattr(layout_policy, "preferred_ups", None):
                ups_options.setdefault("preferred_ups", layout_policy.preferred_ups)
        except Exception:
            pass
        try:
            if getattr(layout_policy, "max_ups", None):
                ups_options.setdefault("max_ups", layout_policy.max_ups)
        except Exception:
            pass

    if overrides is not None:
        try:
            if getattr(overrides, "forced_ups", None):
                ups_options["forced_ups"] = overrides.forced_ups
        except Exception:
            pass
        try:
            if getattr(overrides, "ups_enforcement", None):
                ups_options["ups_enforcement"] = overrides.ups_enforcement
        except Exception:
            pass

    # Optional spec-level Printing UPS hint:
    # when present and positive on the spec input, treat it as a
    # best-effort override for the plant's printing_ups and include it
    # in UPS resolution options. This keeps plant defaults intact while
    # allowing per-spec control of derived sheet layout.
    try:
        spec_printing_raw = getattr(spec_input, "printing_ups", None)
    except Exception:
        spec_printing_raw = None
    printing_ups_hint = 0
    if spec_printing_raw not in (None, "", 0, 0.0):
        try:
            printing_ups_hint = int(spec_printing_raw)
        except Exception:
            printing_ups_hint = 0
    if printing_ups_hint and printing_ups_hint > 0:
        try:
            setattr(plant, "printing_ups", printing_ups_hint)
        except Exception:
            # Plant profile is a pure dataclass; failure to mutate must
            # not abort compute(), options fallback still applies.
            pass
        ups_options.setdefault("printing_ups", printing_ups_hint)

    printing_ups, diecut_ups, effective_ups = resolve_ups_for_engine(plant, options=ups_options)
    trace.append(
        (
            "ups_resolved",
            {
                "printing_ups": printing_ups,
                "diecut_ups": diecut_ups,
                "effective_ups": effective_ups,
            },
        )
    )

    # 3) Stage graph + products
    stage_graph = build_stage_graph(spec_input, plant, effective_ups)
    material_flow = [(e.from_stage, e.to_stage, e.product_key) for e in stage_graph.flows]
    material_flow_detailed = [
        {
            "from_stage": e.from_stage,
            "to_stage": e.to_stage,
            "product_key": e.product_key,
            "piece_key": e.piece_key,
        }
        for e in stage_graph.flows
    ]
    stage_chain = [s.key for s in stage_graph.stages]
    trace.append(
        (
            "stage_chain_resolved",
            {
                "stages": stage_chain,
                "material_flow": material_flow,
                "material_flow_detailed": material_flow_detailed,
            },
        )
    )
    trace.append(
        (
            "stage_graph_created",
            {
                "stages": stage_chain,
                "flows": material_flow,
                "flows_detailed": material_flow_detailed,
            },
        )
    )
    style_yield_metadata: Dict[str, Any] = {}
    if style_result is not None:
        try:
            style_yield_metadata = _yield_label_for_stage(
                stage_graph,
                dict((style_result.as_dict().get("metadata") or {})),
            )
        except Exception:
            style_yield_metadata = {}
    if style_yield_metadata:
        trace.append(("style_yield_resolved", dict(style_yield_metadata)))

    products = generate_products(spec_input, stage_graph)

    # 4) Metrics and RM requirements
    metrics = calculate_metrics(spec_input, printing_ups, diecut_ups, effective_ups)
    trace.append(
        (
            "metrics_calculated",
            {
                "m2_per_box": metrics.m2_per_box,
                "kg_per_box": metrics.kg_per_box,
                "printing_ups": metrics.printing_ups,
                "diecut_ups": metrics.diecut_ups,
                "effective_ups": metrics.effective_ups,
            },
        )
    )

    # Attach core geometry metrics when style handlers provide them so
    # that downstream logic (e.g. wire consumables) has a single,
    # well-defined source of truth. When style_result includes
    # "seam_mm", it is interpreted as the effective seam length (mm)
    # per finished box. For styles without a seam this key may be
    # absent or zero.
    try:
        geom = getattr(metrics, "extra", None) or {}
        geometry_payload = dict(geom.get("geometry", {}) or {})
        geometry_payload.setdefault(
            "box_length_mm", float(getattr(spec_input, "box_length_mm", 0.0) or 0.0)
        )
        geometry_payload.setdefault(
            "box_width_mm", float(getattr(spec_input, "box_width_mm", 0.0) or 0.0)
        )
        geometry_payload.setdefault(
            "box_height_mm", float(getattr(spec_input, "box_height_mm", 0.0) or 0.0)
        )
        if style_result is not None:
            style_payload = style_result.as_dict()
            if "blank_length_mm" in style_payload:
                geometry_payload.setdefault(
                    "blank_length_mm", float(style_payload["blank_length_mm"] or 0.0)
                )
            if "blank_width_mm" in style_payload:
                geometry_payload.setdefault(
                    "blank_width_mm", float(style_payload["blank_width_mm"] or 0.0)
                )
            if "seam_mm" in style_payload:
                geometry_payload.setdefault(
                    "seam_mm", float(style_payload["seam_mm"] or 0.0)
                )
        try:
            joining_type = str(getattr(spec_input, "joining_type", "") or "").strip().lower()
        except Exception:
            joining_type = ""
        if joining_type in {"gluing", "stitching", "gluing_stitching", "tape"}:
            try:
                seam_current = float(geometry_payload.get("seam_mm") or 0.0)
            except Exception:
                seam_current = 0.0
            if seam_current <= 0.0:
                seam_override = 0.0
                seam_source = "joining_glue_flap_override"
                try:
                    geometry = getattr(spec_input, "geometry", None)
                    seam_override = float(
                        getattr(geometry, "glue_flap_override_mm", 0.0) or 0.0
                    )
                except Exception:
                    seam_override = 0.0
                if seam_override <= 0.0:
                    try:
                        seam_override = float(getattr(spec_input, "glue_flap_mm", 0.0) or 0.0)
                    except Exception:
                        seam_override = 0.0
                if seam_override <= 0.0:
                    try:
                        seam_override = float(getattr(plant, "default_glue_lap_mm", 0.0) or 0.0)
                    except Exception:
                        seam_override = 0.0
                    if seam_override > 0.0:
                        seam_source = "joining_plant_default_glue_lap"
                if seam_override <= 0.0:
                    seam_override = 20.0
                    seam_source = "joining_default_seam_fallback"
                if seam_override > 0.0:
                    geometry_payload["seam_mm"] = seam_override
                    geometry_payload["seam_source"] = seam_source
        geom = dict(geom)
        if geometry_payload:
            geom["geometry"] = geometry_payload
        if style_result is not None:
            style_payload = style_result.as_dict()
            style_metadata = dict(style_payload.get("metadata") or {})
            if style_yield_metadata:
                style_metadata.update(style_yield_metadata)
            geom["style"] = {
                "metadata": style_metadata,
                "capabilities": style_result.capabilities(),
                "score_lines": list(style_payload.get("score_lines") or []),
                "fold_lines": list(style_payload.get("fold_lines") or []),
                "flap_mm": float(style_payload.get("flap_mm") or 0.0),
            }
        metrics.extra = geom
    except Exception:
        # Geometry diagnostics are best-effort and must not affect
        # core metrics or RM behaviour when unavailable.
        pass

    # 4b) RM inputs (routing to stages)
    rm_requirements, board_struct = resolve_rm_inputs(spec_input, plant, stage_graph, metrics)
    if piece_plan.enabled:
        rm_requirements = _expand_rm_requirements_for_piece_plan(
            rm_requirements,
            piece_plan,
            source_m2_per_box=float(metrics.m2_per_box or 0.0),
        )
        metrics.extra["multi_piece"] = piece_plan_to_dict(piece_plan)
    trace.append(
        (
            "rm_inputs_resolved",
            {
                "stages": [s.key for s in stage_graph.stages],
                "rm_inputs": [
                    (rm.role, rm.product_key, rm.stage_key) for rm in rm_requirements
                ],
                "rm_inputs_detailed": [
                    {
                        "role": rm.role,
                        "product_key": rm.product_key,
                        "stage_key": rm.stage_key,
                        "qty_per_fg": getattr(rm, "qty_per_fg", 0.0),
                        "qty_uom": getattr(rm, "qty_uom", ""),
                        "consumption_type": getattr(rm, "consumption_type", "area"),
                        "metadata": getattr(rm, "metadata", {}) or {},
                    }
                    for rm in rm_requirements
                ],
            },
        )
    )

    # 4c) Optional blank splitting based on machine width limits. Intentional
    # multi-piece manufacturing is already represented by PiecePlan branches,
    # so avoid mutating blank geometry through the older machine-limit helper.
    if piece_plan.enabled:
        pieces_per_box = int(piece_plan.piece_count or len(piece_plan.pieces or []) or 1)
        effective_blank_width = float(getattr(spec_input, "blank_width_mm", 0.0) or 0.0)
        metrics.extra["pieces_per_box"] = pieces_per_box
        metrics.extra["split_strategy"] = "intentional_multi_piece"
    else:
        pieces_per_box, effective_blank_width = resolve_splitting(spec_input, plant, metrics)
    trace.append(
        (
            "splitting_resolved",
            {
                "pieces_per_box": int(pieces_per_box or 1),
                "effective_blank_width_mm": float(effective_blank_width or 0.0),
            },
        )
    )

    # 4d) Layout (sheet utilisation based on plant sheet size and blank size)
    layout_info = resolve_layout(spec_input, plant)
    metrics.extra["layout"] = layout_info

    # Expose core layout + splitting interaction metrics.
    layout_blanks_per_sheet = int(layout_info.get("blanks_per_sheet", 0) or 0)
    metrics.extra["layout_blanks_per_sheet"] = layout_blanks_per_sheet

    # Determine sheet source for downstream semantics (derived vs plant/manual).
    try:
        layout_policy = getattr(spec_input, "layout_policy", None)
    except Exception:
        layout_policy = None
    sheet_source = None
    if layout_policy is not None:
        try:
            sheet_source = (layout_policy.sheet_source or "").strip() or None
        except Exception:
            sheet_source = None
    if not sheet_source:
        try:
            sheet_source = (
                str(getattr(plant, "default_sheet_behavior", "") or "").strip()
                or "plant_default"
            )
        except Exception:
            sheet_source = "plant_default"

    # In derived-sheet mode, validate that the resolved sheet dimensions
    # used for layout are physically feasible against plant sheet-length/
    # width constraints. This validation occurs after layout and before
    # any BoM creation; infeasible configurations must fail fast rather
    # than silently adjusting UPS or sheet size.
    if sheet_source == "derived":
        derived_len = float(layout_info.get("sheet_length_mm") or 0.0)
        derived_wid = float(layout_info.get("sheet_width_mm") or 0.0)
        allow_infeasible_derived_sheet_preview = bool(
            (options or {}).get("allow_infeasible_derived_sheet_preview")
        )

        # Only enforce feasibility when layout resolved a positive sheet
        # size and plant provides explicit sheet length/width constraints.
        if derived_len > 0.0 and derived_wid > 0.0:
            try:
                max_len = float(getattr(plant, "sheet_length_mm", 0.0) or 0.0)
            except Exception:
                max_len = 0.0
            try:
                max_wid = float(getattr(plant, "sheet_width_mm", 0.0) or 0.0)
            except Exception:
                max_wid = 0.0

            violations: List[str] = []
            if max_len > 0.0 and derived_len - max_len > 1e-6:
                violations.append(
                    f"length {derived_len:.1f} mm exceeds plant sheet_length_mm {max_len:.1f} mm"
                )
            if max_wid > 0.0 and derived_wid - max_wid > 1e-6:
                violations.append(
                    f"width {derived_wid:.1f} mm exceeds plant sheet_width_mm {max_wid:.1f} mm"
                )

            if violations:
                try:
                    plant_label = (
                        str(getattr(plant, "code", "") or "")
                        or str(getattr(plant, "name", "") or "")
                        or "unknown"
                    )
                except Exception:
                    plant_label = "unknown"
                msg = (
                    f"Derived sheet {derived_len:.1f} x {derived_wid:.1f} mm is not feasible "
                    f"for plant {plant_label}: " + "; ".join(violations)
                )
                if allow_infeasible_derived_sheet_preview:
                    metrics.extra["derived_sheet_feasibility"] = {
                        "status": "infeasible",
                        "message": msg,
                        "violations": list(violations),
                        "plant": plant_label,
                        "derived_sheet_length_mm": derived_len,
                        "derived_sheet_width_mm": derived_wid,
                    }
                    trace.append(
                        (
                            "derived_sheet_infeasible",
                            dict(metrics.extra["derived_sheet_feasibility"]),
                        )
                    )
                else:
                    raise ValueError(msg)

    # Also mirror UPS values in the extra metrics for convenience.
    # Keep metrics.effective_ups as the machine-limited UPS used by
    # routing/BoM, but in derived-sheet mode surface the geometric UPS
    # (layout blanks per sheet) in extra["effective_ups"] for clarity.
    # Preserve the machine-limited effective_ups for diagnostics.
    metrics.extra["effective_ups_machine"] = int(metrics.effective_ups or 0)
    if sheet_source == "derived" and layout_blanks_per_sheet > 0:
        # In derived mode, expose the geometric UPS as the primary
        # effective_ups in the extra dict so that raw_json reflects the
        # final layout count (while the EngineMetrics.effective_ups
        # field retains the plant/machine limit for internal use).
        metrics.extra["effective_ups"] = int(layout_blanks_per_sheet)
    else:
        metrics.extra["effective_ups"] = int(metrics.effective_ups or 0)

    metrics.extra.setdefault("printing_ups", int(metrics.printing_ups or 0))
    metrics.extra.setdefault("diecut_ups", int(metrics.diecut_ups or 0))

    # Boxes per sheet reflect both geometric capacity and any blank
    # splitting required by machine limits. When pieces_per_box > 1,
    # each finished box consumes multiple blank pieces.
    try:
        pieces_per_box = int(metrics.extra.get("pieces_per_box", 1) or 1)
    except Exception:
        pieces_per_box = 1
    if pieces_per_box <= 0:
        pieces_per_box = 1

    if layout_blanks_per_sheet > 0:
        boxes_per_sheet = layout_blanks_per_sheet / float(pieces_per_box)
    else:
        boxes_per_sheet = 0.0
    metrics.extra["boxes_per_sheet"] = boxes_per_sheet

    # Propagate layout quantities into the stage graph so that the
    # final material transformation (sheet -> box) reflects the number
    # of pieces required per box. Quantity is expressed in units of the
    # final material stage's SFG per finished box.
    final_stage_key = stage_graph.final_stage_key()
    if final_stage_key:
        if layout_blanks_per_sheet > 0:
            qty_per_fg = float(pieces_per_box) / float(layout_blanks_per_sheet)
        else:
            qty_per_fg = 1.0
        for edge in stage_graph.flows:
            if edge.to_stage == final_stage_key:
                edge.qty_per_fg = qty_per_fg

    trace.append(
        (
            "layout_resolved",
            {
                **layout_info,
                "layout_blanks_per_sheet": layout_blanks_per_sheet,
                "boxes_per_sheet": boxes_per_sheet,
            },
        )
    )
    trace.append(
        (
            "board_structure_resolved",
            {
                "layers": [
                    {"type": l.type, "gsm": l.gsm, "layer_no": l.layer_no}
                    for l in board_struct.layers
                ],
            },
        )
    )

    # Waste: always compute, apply conditionally
    options_dict = options or {}
    waste_info = resolve_waste(spec_input, plant, metrics, options=options_dict)
    metrics.extra.update(waste_info)

    include_waste = bool(options_dict.get("include_waste"))
    total_waste_pct = float(waste_info.get("total_waste_pct", 0.0) or 0.0)
    if include_waste:
        kg_with_waste = metrics.kg_per_box * (1.0 + total_waste_pct)
    else:
        kg_with_waste = metrics.kg_per_box
    metrics.extra["kg_per_box_with_waste"] = float(kg_with_waste or 0.0)

    trace.append(
        (
            "waste_applied",
            {
                "include_waste": include_waste,
                "total_waste_pct": total_waste_pct,
            },
        )
    )

    # 5) Slitting (Pieces)
    if piece_plan.enabled:
        pieces = [
            Piece(
                key=piece.piece_key,
                parent_product_key="fg_box",
                ratio=(
                    (float(piece.piece_width_mm or 0.0) * float(piece.piece_length_mm or 0.0))
                    / (
                        float(piece_plan.source_width_mm or 0.0)
                        * float(piece_plan.source_length_mm or 0.0)
                        or 1.0
                    )
                ),
                width_mm=piece.piece_width_mm,
                length_mm=piece.piece_length_mm,
                description="Intentional carton piece %s" % (piece.piece_name or piece.piece_key),
            )
            for piece in piece_plan.pieces
        ]
    else:
        pieces = resolve_slitting(spec_input, plant, metrics)
    piece_keys = [p.key for p in pieces]
    pieces_per_sheet = len(piece_keys)
    # For no-slit cases, represent the sheet as a single logical piece
    # in the trace to avoid a misleading "0" while still keeping the
    # pieces list empty.
    if pieces_per_sheet <= 0:
        pieces_per_sheet = 1
    trace.append(
        (
            "slitting_resolved",
            {
                "pieces": piece_keys,
                "pieces_per_sheet": pieces_per_sheet,
            },
        )
    )

    # 5) Build EngineResult
    result = EngineResult(
        spec_id=spec_id,
        style_code=style_code,
        sfg_mode=sfg_mode,
        products=products,
        stage_graph=stage_graph,
        rm_requirements=rm_requirements,
        metrics=metrics,
        pieces=pieces,
        piece_plan=piece_plan if piece_plan.enabled else None,
        snapshot={},
        trace=trace,
    )

    # Attach quantity StageGraph (order-level quantities) when possible.
    try:
        options_dict_for_qty = options or {}
        order_qty = float(options_dict_for_qty.get("order_qty", 1.0) or 1.0)
    except Exception:
        order_qty = 1.0
    try:
        from .stage_graph import build_stage_graph as build_quantity_stage_graph

        result.quantity_stage_graph = build_quantity_stage_graph(result, order_qty)
    except Exception:
        # Quantity graph is optional and must not break compute().
        pass

    # Final validation
    result.products = _ensure_flow_products_registered(result.products, result.stage_graph)
    result.validate()
    return result



