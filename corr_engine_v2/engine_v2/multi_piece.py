from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from .spec_input import EngineSpecInput, PiecePlanInput, PieceSpecInput


def resolve_piece_plan(
    spec_input: EngineSpecInput,
    *,
    options: Optional[Dict[str, Any]] = None,
) -> PiecePlanInput:
    """Resolve intentional multi-piece manufacturing from spec/options.

    Supported Phase 1 forms:
      - typed ``spec_input.piece_plan``;
      - ``options["multi_piece"]`` / ``options["piece_plan"]`` dict;
      - duck-typed ``spec_input.multi_piece_plan`` dict/object.

    Equal splits derive piece dimensions from the source blank. Uneven
    splits accept either a ``pieces`` list or axis-specific sizes.
    """

    raw = _first_plan_payload(spec_input, options=options)
    if raw is None:
        return PiecePlanInput(enabled=False)
    if isinstance(raw, PiecePlanInput):
        plan = raw
    else:
        plan = _plan_from_payload(raw)
    if not plan.enabled:
        return PiecePlanInput(enabled=False)

    source_length = _positive_float(plan.source_length_mm) or _source_length_from_spec_input(
        spec_input
    )
    source_width = _positive_float(plan.source_width_mm) or _source_width_from_spec_input(
        spec_input
    )
    split_axis = _norm(plan.split_axis)
    split_mode = _norm(plan.split_mode) or "equal"
    split_method = _norm(plan.split_method) or split_mode
    multi_piece_strategy = (
        _norm_key(getattr(plan, "multi_piece_strategy", ""))
        or "independent_routed_piece"
    )
    split_after_stage_key = _norm_key(getattr(plan, "split_after_stage_key", ""))
    merge_stage_key = _norm_key(plan.merge_stage_key) or "seaming"
    piece_count = int(plan.piece_count or len(plan.pieces or []) or 2)
    source = _norm_key(plan.source) or "explicit_multi_piece_design"
    status = _norm_key(plan.status) or "accepted"

    if piece_count != 2:
        raise ValueError("Engine V2 multi-piece Phase 1 supports exactly 2 pieces.")
    if split_axis not in ("width", "length"):
        raise ValueError("Engine V2 multi-piece split_axis must be 'width' or 'length'.")
    if split_mode not in ("equal", "uneven", "manual"):
        raise ValueError("Engine V2 multi-piece split_mode must be 'equal', 'uneven', or 'manual'.")
    if source_length <= 0.0 or source_width <= 0.0:
        raise ValueError("Engine V2 multi-piece requires positive source blank dimensions.")

    pieces = list(plan.pieces or [])
    if not pieces:
        pieces = _derive_phase1_pieces(
            plan,
            piece_count=piece_count,
            split_axis=split_axis,
            split_mode=split_mode,
            split_method=split_method,
            source_length=source_length,
            source_width=source_width,
            merge_stage_key=merge_stage_key,
        )
    else:
        pieces = [
            _normalize_piece(
                item,
                index=idx + 1,
                split_axis=split_axis,
                split_mode=split_mode,
                source_length=source_length,
                source_width=source_width,
                merge_stage_key=merge_stage_key,
            )
            for idx, item in enumerate(pieces)
        ]

    _validate_phase1_dimensions(
        pieces,
        split_axis=split_axis,
        source_length=source_length,
        source_width=source_width,
    )

    return PiecePlanInput(
        enabled=True,
        source=source,
        status=status,
        is_design_intent=bool(plan.is_design_intent or source == "explicit_multi_piece_design"),
        materialization_allowed=bool(plan.materialization_allowed),
        piece_count=piece_count,
        split_axis=split_axis,
        split_mode=split_mode,
        split_method=split_method,
        multi_piece_strategy=multi_piece_strategy,
        split_after_stage_key=split_after_stage_key,
        source_width_mm=source_width,
        source_length_mm=source_length,
        merge_stage_key=merge_stage_key,
        split_sizes_mm=list(plan.split_sizes_mm or []),
        ratio_a=float(plan.ratio_a or 0.0),
        ratio_b=float(plan.ratio_b or 0.0),
        primary_piece_key=plan.primary_piece_key or "piece_a",
        primary_size_mm=float(plan.primary_size_mm or 0.0),
        pieces=pieces,
    )


def piece_plan_to_dict(plan: PiecePlanInput) -> Dict[str, Any]:
    if not plan or not plan.enabled:
        return {"enabled": False}
    return asdict(plan)


def _source_length_from_spec_input(spec_input: EngineSpecInput) -> float:
    geometry = getattr(spec_input, "geometry", None)
    return (
        _positive_float(getattr(spec_input, "blank_length_mm", 0.0))
        or _positive_float(getattr(geometry, "blank_length_mm", 0.0))
        or _positive_float(getattr(geometry, "box_length_mm", 0.0))
        or _positive_float(getattr(spec_input, "box_length_mm", 0.0))
    )


def _source_width_from_spec_input(spec_input: EngineSpecInput) -> float:
    geometry = getattr(spec_input, "geometry", None)
    return (
        _positive_float(getattr(spec_input, "blank_width_mm", 0.0))
        or _positive_float(getattr(geometry, "blank_width_mm", 0.0))
        or _positive_float(getattr(geometry, "box_width_mm", 0.0))
        or _positive_float(getattr(spec_input, "box_width_mm", 0.0))
    )


def _first_plan_payload(spec_input: EngineSpecInput, *, options: Optional[Dict[str, Any]]) -> Any:
    for key in ("multi_piece", "piece_plan"):
        if isinstance(options, dict) and options.get(key):
            return options.get(key)
    raw = getattr(spec_input, "piece_plan", None)
    if raw:
        return raw
    return getattr(spec_input, "multi_piece_plan", None)


def _plan_from_payload(raw: Any) -> PiecePlanInput:
    if isinstance(raw, dict):
        data = raw
        pieces_raw = list(data.get("pieces") or [])
        pieces = [_piece_from_payload(item, idx + 1) for idx, item in enumerate(pieces_raw)]
        return PiecePlanInput(
            enabled=bool(data.get("enabled", True)),
            source=str(data.get("source") or ""),
            status=str(data.get("status") or ""),
            is_design_intent=bool(data.get("is_design_intent", False)),
            materialization_allowed=bool(data.get("materialization_allowed", False)),
            piece_count=int(data.get("piece_count") or len(pieces) or 0),
            split_axis=str(data.get("split_axis") or ""),
            split_mode=str(data.get("split_mode") or ""),
            split_method=str(data.get("split_method") or ""),
            multi_piece_strategy=str(data.get("multi_piece_strategy") or data.get("strategy") or "independent_routed_piece"),
            split_after_stage_key=str(data.get("split_after_stage_key") or ""),
            source_width_mm=_positive_float(data.get("source_width_mm")),
            source_length_mm=_positive_float(data.get("source_length_mm")),
            merge_stage_key=str(data.get("merge_stage_key") or "seaming"),
            split_sizes_mm=[_positive_float(item) for item in list(data.get("split_sizes_mm") or [])],
            ratio_a=_positive_float(data.get("ratio_a")),
            ratio_b=_positive_float(data.get("ratio_b")),
            primary_piece_key=str(data.get("primary_piece_key") or "piece_a"),
            primary_size_mm=_positive_float(data.get("primary_size_mm")),
            pieces=pieces,
        )

    pieces = list(getattr(raw, "pieces", []) or [])
    return PiecePlanInput(
        enabled=bool(getattr(raw, "enabled", True)),
        source=str(getattr(raw, "source", "") or ""),
        status=str(getattr(raw, "status", "") or ""),
        is_design_intent=bool(getattr(raw, "is_design_intent", False)),
        materialization_allowed=bool(getattr(raw, "materialization_allowed", False)),
        piece_count=int(getattr(raw, "piece_count", 0) or len(pieces) or 0),
        split_axis=str(getattr(raw, "split_axis", "") or ""),
        split_mode=str(getattr(raw, "split_mode", "") or ""),
        split_method=str(getattr(raw, "split_method", "") or ""),
        multi_piece_strategy=str(getattr(raw, "multi_piece_strategy", "independent_routed_piece") or "independent_routed_piece"),
        split_after_stage_key=str(getattr(raw, "split_after_stage_key", "") or ""),
        source_width_mm=_positive_float(getattr(raw, "source_width_mm", 0.0)),
        source_length_mm=_positive_float(getattr(raw, "source_length_mm", 0.0)),
        merge_stage_key=str(getattr(raw, "merge_stage_key", "seaming") or "seaming"),
        split_sizes_mm=[_positive_float(item) for item in list(getattr(raw, "split_sizes_mm", []) or [])],
        ratio_a=_positive_float(getattr(raw, "ratio_a", 0.0)),
        ratio_b=_positive_float(getattr(raw, "ratio_b", 0.0)),
        primary_piece_key=str(getattr(raw, "primary_piece_key", "piece_a") or "piece_a"),
        primary_size_mm=_positive_float(getattr(raw, "primary_size_mm", 0.0)),
        pieces=[_piece_from_payload(item, idx + 1) for idx, item in enumerate(pieces)],
    )


def _piece_from_payload(raw: Any, index: int) -> PieceSpecInput:
    if isinstance(raw, PieceSpecInput):
        return raw
    getter = raw.get if isinstance(raw, dict) else lambda name, default=None: getattr(raw, name, default)
    key = str(getter("piece_key", "") or "").strip() or "piece_%s" % _piece_letter(index).lower()
    name = str(getter("piece_name", "") or "").strip() or _piece_letter(index)
    return PieceSpecInput(
        piece_key=key,
        piece_name=name,
        piece_index=int(getter("piece_index", index) or index),
        piece_qty_per_fg=_positive_float(getter("piece_qty_per_fg", 1.0)) or 1.0,
        piece_width_mm=_positive_float(getter("piece_width_mm", 0.0)),
        piece_length_mm=_positive_float(getter("piece_length_mm", 0.0)),
        split_axis=str(getter("split_axis", "") or ""),
        split_mode=str(getter("split_mode", "") or ""),
        split_group_key=str(getter("split_group_key", "main") or "main"),
        stack_source=str(getter("stack_source", "inherited") or "inherited"),
        board_recipe_key=str(getter("board_recipe_key", "") or ""),
        flute_profile_key=str(getter("flute_profile_key", "") or ""),
        surface_source=str(getter("surface_source", "inherited") or "inherited"),
        surface_profile_key=str(getter("surface_profile_key", "") or ""),
        top_sheet_source=str(getter("top_sheet_source", "inherited") or "inherited"),
        top_sheet_mode=str(getter("top_sheet_mode", "") or ""),
        top_sheet_product_key=str(getter("top_sheet_product_key", "") or ""),
        routing_profile_key=str(getter("routing_profile_key", "") or ""),
        merge_stage_key=str(getter("merge_stage_key", "seaming") or "seaming"),
    )


def _derive_phase1_pieces(
    plan: PiecePlanInput,
    *,
    piece_count: int,
    split_axis: str,
    split_mode: str,
    split_method: str,
    source_length: float,
    source_width: float,
    merge_stage_key: str,
) -> List[PieceSpecInput]:
    axis_size = source_width if split_axis == "width" else source_length
    if split_method == "equal":
        sizes = [
            (source_width / float(piece_count)) if split_axis == "width" else source_width,
            (source_width / float(piece_count)) if split_axis == "width" else source_width,
        ]
        lengths = [
            source_length if split_axis == "width" else source_length / float(piece_count),
            source_length if split_axis == "width" else source_length / float(piece_count),
        ]
    else:
        raw_sizes = list(getattr(plan, "split_sizes_mm", []) or [])
        if not raw_sizes and split_method == "ratio":
            ratio_a = _positive_float(getattr(plan, "ratio_a", 0.0))
            ratio_b = _positive_float(getattr(plan, "ratio_b", 0.0))
            if ratio_a <= 0.0 or ratio_b <= 0.0:
                raise ValueError("Ratio multi-piece split requires positive ratio_a and ratio_b.")
            size_a = axis_size * ratio_a / (ratio_a + ratio_b)
            raw_sizes = [size_a, axis_size - size_a]
        if not raw_sizes and split_method == "primary":
            primary_size = _positive_float(getattr(plan, "primary_size_mm", 0.0))
            if primary_size <= 0.0 or primary_size >= axis_size:
                raise ValueError("Primary piece size must be positive and less than the source blank axis.")
            if _norm_key(getattr(plan, "primary_piece_key", "piece_a")) == "piece_b":
                raw_sizes = [axis_size - primary_size, primary_size]
            else:
                raw_sizes = [primary_size, axis_size - primary_size]
        if not raw_sizes:
            raise ValueError("Uneven multi-piece split requires explicit pieces or derivation inputs.")
        if len(raw_sizes) != piece_count:
            raise ValueError("Uneven multi-piece split_sizes_mm must contain exactly 2 values.")
        if split_axis == "width":
            sizes = [_positive_float(raw_sizes[0]), _positive_float(raw_sizes[1])]
            lengths = [source_length, source_length]
        else:
            sizes = [source_width, source_width]
            lengths = [_positive_float(raw_sizes[0]), _positive_float(raw_sizes[1])]

    pieces: List[PieceSpecInput] = []
    for idx in range(piece_count):
        letter = _piece_letter(idx + 1)
        pieces.append(
            PieceSpecInput(
                piece_key="piece_%s" % letter.lower(),
                piece_name=letter,
                piece_index=idx + 1,
                piece_qty_per_fg=1.0,
                piece_width_mm=sizes[idx],
                piece_length_mm=lengths[idx],
                split_axis=split_axis,
                split_mode=split_mode,
                stack_source="inherited",
                surface_source="inherited",
                top_sheet_source="inherited",
                merge_stage_key=merge_stage_key,
            )
        )
    return pieces


def _normalize_piece(
    item: PieceSpecInput,
    *,
    index: int,
    split_axis: str,
    split_mode: str,
    source_length: float,
    source_width: float,
    merge_stage_key: str,
) -> PieceSpecInput:
    width = _positive_float(item.piece_width_mm)
    length = _positive_float(item.piece_length_mm)
    if split_axis == "width" and length <= 0.0:
        length = source_length
    if split_axis == "length" and width <= 0.0:
        width = source_width
    return PieceSpecInput(
        piece_key=str(item.piece_key or "").strip() or "piece_%s" % _piece_letter(index).lower(),
        piece_name=str(item.piece_name or "").strip() or _piece_letter(index),
        piece_index=int(item.piece_index or index),
        piece_qty_per_fg=_positive_float(item.piece_qty_per_fg) or 1.0,
        piece_width_mm=width,
        piece_length_mm=length,
        split_axis=split_axis,
        split_mode=split_mode,
        split_group_key=item.split_group_key or "main",
        stack_source=item.stack_source or "inherited",
        board_recipe_key=item.board_recipe_key or "",
        flute_profile_key=item.flute_profile_key or "",
        surface_source=item.surface_source or "inherited",
        surface_profile_key=item.surface_profile_key or "",
        top_sheet_source=item.top_sheet_source or "inherited",
        top_sheet_mode=item.top_sheet_mode or "",
        top_sheet_product_key=item.top_sheet_product_key or "",
        routing_profile_key=item.routing_profile_key or "",
        merge_stage_key=merge_stage_key,
    )


def _validate_phase1_dimensions(
    pieces: List[PieceSpecInput],
    *,
    split_axis: str,
    source_length: float,
    source_width: float,
) -> None:
    if len(pieces) != 2:
        raise ValueError("Engine V2 multi-piece Phase 1 requires exactly 2 pieces.")
    tolerance = 1e-6
    if split_axis == "width":
        total_width = sum(_positive_float(piece.piece_width_mm) for piece in pieces)
        if abs(total_width - source_width) > tolerance:
            raise ValueError("Piece widths must add up to source blank width.")
        for piece in pieces:
            if abs(_positive_float(piece.piece_length_mm) - source_length) > tolerance:
                raise ValueError("Width split pieces must inherit the source blank length.")
    else:
        total_length = sum(_positive_float(piece.piece_length_mm) for piece in pieces)
        if abs(total_length - source_length) > tolerance:
            raise ValueError("Piece lengths must add up to source blank length.")
        for piece in pieces:
            if abs(_positive_float(piece.piece_width_mm) - source_width) > tolerance:
                raise ValueError("Length split pieces must inherit the source blank width.")


def _positive_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        result = float(value)
    except Exception:
        return 0.0
    return result if result > 0.0 else 0.0


def _norm(value: Any) -> str:
    try:
        return str(value or "").strip().lower()
    except Exception:
        return ""


def _norm_key(value: Any) -> str:
    return _norm(value).replace(" ", "_")


def _piece_letter(index: int) -> str:
    return chr(ord("A") + max(0, int(index or 1) - 1))



