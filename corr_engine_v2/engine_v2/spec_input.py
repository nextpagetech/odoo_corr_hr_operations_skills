from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class GeometrySpec:
    """Finished-good geometry and style information.

    This structure is derived from the Engine V2 spec model
    (corr.engine.v2.spec) but is intentionally independent of Odoo.

    In the V2 world, style_id is the canonical identity for geometry/
    style rules; string style_code remains available only for backwards
    compatibility via EngineSpecInput.style_code.
    """

    style_id: Optional[int] = None
    box_length_mm: float = 0.0
    box_width_mm: float = 0.0
    box_height_mm: float = 0.0
    glue_flap_override_mm: float = 0.0
    plant_code: str = ""
    plant_id: Optional[int] = None


@dataclass
class BoardLayerInput:
    """Single board layer line as captured on the spec.

    Roles follow the Engine V2 semantics:
    - outer_liner
    - medium
    - inner_liner
    - center_liner
    - carton_board
    - other
    """

    sequence: int
    role: str
    gsm: float
    bf: float = 0.0
    color: str = ""
    flute_type_code: str = ""
    flute_tuf: float = 0.0
    material_code: str = ""
    material_product_id: Optional[int] = None
    material_product_name: str = ""
    material_product_uom_id: Optional[int] = None
    material_product_uom_name: str = ""
    material_template_id: Optional[int] = None
    material_template_name: str = ""
    gsm_value_id: Optional[int] = None
    bf_value_id: Optional[int] = None
    color_value_id: Optional[int] = None
    source_type: str = ""  # "manual" or "recipe"


@dataclass
class BoardRecipeInput:
    """Optional board recipe template referenced by the spec."""

    code: str = ""
    name: str = ""
    ply_type: str = ""
    layers: List[BoardLayerInput] = field(default_factory=list)


@dataclass
class SurfaceProfileInput:
    """Surface treatment profile (printing, top sheet, lamination)."""

    printing_required: bool = False
    print_process: str = ""
    print_colors: int = 0
    print_coverage_percent: float = 0.0
    # Optional printing side for future ink/finishing rules.
    print_side: str = ""  # top / bottom / both / custom
    top_sheet_mode: str = "none"  # none / roll / unit_sheet
    top_sheet_product_id: Optional[int] = None
    # Optional GSM for unit-sheet top sheets derived from the selected
    # product. When present and positive, RM rules treat this as the
    # authoritative GSM for unit_sheet mode and fall back to
    # EngineSpecInput.top_sheet_gsm only when this value is not usable.
    top_sheet_gsm: float = 0.0
    lamination_required: bool = False
    lamination_type_id: Optional[int] = None
    lamination_side: str = ""  # top / bottom / both


@dataclass
class LayoutPolicyInput:
    """Layout policy for blank placement and sheet sourcing."""

    allow_rotation: bool = True
    preferred_ups: int = 0
    max_ups: int = 0
    layout_mode: str = "optimize"  # strict / optimize / prefer_target
    sheet_source: str = "plant_default"  # manual / plant_default / derived
    trim_margin_mm: float = 0.0
    gripper_margin_mm: float = 0.0


@dataclass
class RoutingStageInput:
    """Single routing stage in a routing profile."""

    sequence: int
    stage_code: str
    stage_type: str  # material / operation
    stage_semantic: str = ""  # corrugation / printing / lamination / conversion / fg / auxiliary
    output_kind: str = ""  # fg / box / board / sheet / roll / generic
    can_be_terminal: bool = False
    is_fg_stage: bool = False
    waste_percent: float = 0.0


@dataclass
class RoutingProfileInput:
    """Routing profile for the job (ordered stage list)."""

    name: str = ""
    stages: List[RoutingStageInput] = field(default_factory=list)


@dataclass
class EngineOverrides:
    """Optional spec-level overrides for sheet and UPS behaviour."""

    manual_sheet_length_mm: float = 0.0
    manual_sheet_width_mm: float = 0.0
    forced_ups: Optional[int] = None
    ups_enforcement: str = ""  # soft / hard


@dataclass
class PieceSpecInput:
    """One intentional carton piece required for a finished good."""

    piece_key: str
    piece_name: str
    piece_index: int
    piece_qty_per_fg: float
    piece_width_mm: float
    piece_length_mm: float
    split_axis: str = ""  # width / length
    split_mode: str = ""  # equal / uneven / manual
    split_group_key: str = "main"
    stack_source: str = "inherited"  # inherited / override
    board_recipe_key: str = ""
    flute_profile_key: str = ""
    surface_source: str = "inherited"  # inherited / override
    surface_profile_key: str = ""
    top_sheet_source: str = "inherited"  # inherited / override
    top_sheet_mode: str = ""
    top_sheet_product_key: str = ""
    routing_profile_key: str = ""
    merge_stage_key: str = "seaming"


@dataclass
class PiecePlanInput:
    """Intentional multi-piece manufacturing plan for one FG."""

    enabled: bool = False
    source: str = ""  # explicit_multi_piece_design / auto_suggested_split / accepted_auto_split
    status: str = ""  # draft / suggested / accepted / rejected
    is_design_intent: bool = False
    materialization_allowed: bool = False
    piece_count: int = 0
    split_axis: str = ""  # width / length
    split_mode: str = ""  # equal / uneven / manual
    split_method: str = ""  # equal / ratio / primary / manual
    multi_piece_strategy: str = "independent_routed_piece"
    split_after_stage_key: str = ""
    source_width_mm: float = 0.0
    source_length_mm: float = 0.0
    merge_stage_key: str = "seaming"
    split_sizes_mm: List[float] = field(default_factory=list)
    ratio_a: float = 0.0
    ratio_b: float = 0.0
    primary_piece_key: str = "piece_a"
    primary_size_mm: float = 0.0
    pieces: List[PieceSpecInput] = field(default_factory=list)


@dataclass
class EngineSpecInput:
    """Normalized spec input for engine_v2.

    This class intentionally does not depend on Odoo models. It is
    constructed from an arbitrary object that exposes the expected
    attributes (typically corr.spec.template or corr.engine.v2.spec).

    The scalar fields at the top are the original EngineSpecInput
    contract and remain the primary inputs for the current engine
    slice. The structured attributes (geometry, board_recipe, etc.)
    are additive and will be consumed by richer style/plant rules
    in later phases.
    """

    # Core scalar fields used by the existing engine implementation.
    blank_length_mm: float
    blank_width_mm: float
    liner_gsm: float
    medium_gsm: float
    style_code: str
    sfg_mode: str
    spec_id: int = 0
    spec_name: str = ""

    # Optional structured domains (populated by adapters when available).
    geometry: Optional[GeometrySpec] = None
    board_recipe: Optional[BoardRecipeInput] = None
    surface_profile: Optional[SurfaceProfileInput] = None
    layout_policy: Optional[LayoutPolicyInput] = None
    routing_profile: Optional[RoutingProfileInput] = None
    overrides: Optional[EngineOverrides] = None
    joining_type: str = "none"
    # Spec-specific board layers; this mirrors stack_line_ids using
    # BoardLayerInput entries and keeps richer role semantics than the
    # simple liner/medium aggregation.
    board_layers_v2: List[BoardLayerInput] = field(default_factory=list)
    # Optional intentional multi-piece manufacturing plan. This is
    # distinct from machine-limit splitting/slitting: these pieces are
    # required by the carton design and may become real SFG branches.
    piece_plan: Optional[PiecePlanInput] = None

    @classmethod
    def from_spec(cls, spec: Any) -> "EngineSpecInput":
        """Build an EngineSpecInput from a raw spec object.

        Uses getattr defensively so it can operate on simple mocks in
        tests or different spec models without failing.
        """

        def _f(name: str) -> float:
            try:
                return float(getattr(spec, name, 0.0) or 0.0)
            except Exception:
                return 0.0

        def _s(name: str) -> str:
            try:
                return str(getattr(spec, name, "") or "")
            except Exception:
                return ""

        blank_length_mm = _f("blank_length_mm")
        blank_width_mm = _f("blank_width_mm")
        liner_gsm = _f("liner_gsm")
        medium_gsm = _f("medium_gsm")

        style_code = _s("style")
        sfg_mode = _s("sfg_mode")

        try:
            spec_id = int(getattr(spec, "id", 0) or 0)
        except Exception:
            spec_id = 0
        spec_name = _s("name")

        return cls(
            blank_length_mm=blank_length_mm,
            blank_width_mm=blank_width_mm,
            liner_gsm=liner_gsm,
            medium_gsm=medium_gsm,
            style_code=style_code,
            sfg_mode=sfg_mode,
            spec_id=spec_id,
            spec_name=spec_name,
        )



