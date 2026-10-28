from __future__ import annotations

import math
from typing import Any, Tuple

from .spec_input import EngineSpecInput
from .plant_profiles import PlantProfile
from . import EngineMetrics


def _float_attr(obj: Any, name: str) -> float:
    """Safely read a float attribute from an arbitrary object."""

    try:
        raw = getattr(obj, name, None)
    except Exception:
        raw = None
    if raw in (None, ""):
        return 0.0
    try:
        if isinstance(raw, str):
            raw = raw.replace(",", ".").strip()
        return float(raw or 0.0)
    except Exception:
        return 0.0


def resolve_splitting(
    spec_input: EngineSpecInput,
    plant_profile: PlantProfile,
    metrics: EngineMetrics,
) -> Tuple[int, float]:
    """Resolve automatic blank splitting based on machine width limits.

    This helper runs after style resolution and before layout. It:
      - inspects blank_width_mm from the spec,
      - reads width limits from the plant (corrugator/printer/die-cut),
      - computes pieces_per_box = ceil(blank_width / effective_limit),
      - adjusts spec_input.blank_width_mm only for layout/slitting by
        setting an effective blank width per piece,
      - records pieces_per_box in metrics.extra.

    It does NOT change metrics.kg_per_box or m2_per_box; those remain
    based on the full blank dimensions.
    """

    # Original blank width used for metrics and RM calculations.
    blank_width_mm = _float_attr(spec_input, "blank_width_mm")
    if blank_width_mm <= 0.0:
        metrics.extra.setdefault("pieces_per_box", 1)
        return 1, blank_width_mm

    try:
        style_capabilities = getattr(spec_input, "style_capabilities", None) or {}
    except Exception:
        style_capabilities = {}
    if style_capabilities and not bool(style_capabilities.get("allows_width_split", True)):
        metrics.extra["pieces_per_box"] = 1
        metrics.extra["split_strategy"] = "style_width_split_disabled"
        metrics.extra["style_capabilities"] = dict(style_capabilities)
        return 1, blank_width_mm

    # New V2 split behaviour is driven by PlantProfile.max_machine_width_mm
    # and related constraints when present. Legacy width attributes are
    # preserved as a fallback for existing tests and configs.
    max_machine_width_mm = _float_attr(plant_profile, "max_machine_width_mm")
    split_mode = ""
    try:
        split_mode = str(getattr(plant_profile, "split_mode", "") or "").strip().lower()
    except Exception:
        split_mode = ""

    min_piece_width_mm = _float_attr(plant_profile, "min_piece_width_mm")
    max_piece_width_mm = _float_attr(plant_profile, "max_piece_width_mm")

    def _record(pieces: int, effective_width: float, strategy: str) -> None:
        try:
            metrics.extra["pieces_per_box"] = int(pieces)
        except Exception:
            pass
        try:
            metrics.extra["split_strategy"] = strategy
        except Exception:
            pass
        # Preserve the original width for debugging and clarity.
        try:
            if not hasattr(spec_input, "blank_width_mm_full"):
                spec_input.blank_width_mm_full = blank_width_mm
        except Exception:
            pass
        # Adjust the blank width used by downstream layout/slitting logic.
        try:
            spec_input.blank_width_mm = effective_width
        except Exception:
            # If spec_input is somehow immutable, keep going without
            # affecting core metrics.
            pass

    # ------------------------------------------------------------------
    # V2 path: use max_machine_width_mm + split_mode when configured.
    # ------------------------------------------------------------------
    if max_machine_width_mm > 0.0:
        # When split_mode is explicitly "none", do not split even if the
        # blank exceeds the machine limit.
        if split_mode == "none":
            _record(1, blank_width_mm, "none")
            return 1, blank_width_mm

        effective_limit = max_machine_width_mm

        # Respect optional max_piece_width constraint.
        if max_piece_width_mm > 0.0:
            effective_limit = min(effective_limit, max_piece_width_mm)

        # If even the full blank width fits within the effective limit,
        # keep pieces_per_box = 1.
        if blank_width_mm <= effective_limit:
            _record(1, blank_width_mm, "none")
            return 1, blank_width_mm

        # Compute equal split as the primary strategy.
        pieces_equal = int(math.ceil(blank_width_mm / effective_limit))
        if pieces_equal < 1:
            pieces_equal = 1
        piece_width_equal = blank_width_mm / float(pieces_equal)

        def _within_constraints(width: float) -> bool:
            if width <= 0.0:
                return False
            if min_piece_width_mm > 0.0 and width < min_piece_width_mm:
                return False
            if max_piece_width_mm > 0.0 and width > max_piece_width_mm:
                return False
            if width > max_machine_width_mm:
                return False
            return True

        if split_mode in ("equal_only", "uneven_allowed") and _within_constraints(
            piece_width_equal
        ):
            _record(pieces_equal, piece_width_equal, "equal")
            return pieces_equal, piece_width_equal

        # Uneven split with multi-piece support when allowed and equal
        # split does not satisfy constraints.
        if split_mode == "uneven_allowed":
            # We search for N-piece partitions where N starts from the
            # minimum required to keep each piece within the machine
            # width, and we enforce:
            #   min_piece_width_mm <= piece_width <= max_piece_width_mm
            #   piece_width <= max_machine_width_mm
            #
            # For each feasible N we can guarantee that a partition
            # exists if the total width lies between N * w_min and
            # N * w_max. We then expose an effective_blank_width based
            # on the best possible maximum piece width for that N.

            # Upper bound on width for any piece.
            w_max = max_machine_width_mm
            if max_piece_width_mm > 0.0:
                w_max = min(w_max, max_piece_width_mm)

            if w_max <= 0.0:
                _record(1, blank_width_mm, "none")
                return 1, blank_width_mm

            w_min = min_piece_width_mm if min_piece_width_mm > 0.0 else 0.0

            # Minimum piece count required so that each piece can be
            # kept within the machine width limit.
            n_start = int(math.ceil(blank_width_mm / max_machine_width_mm))
            if n_start < 1:
                n_start = 1

            # Upper bound on piece count based on minimum width, with a
            # safety cap when w_min is zero.
            if w_min > 0.0:
                n_upper = int(math.floor(blank_width_mm / w_min)) or n_start
                if n_upper < n_start:
                    n_upper = n_start
                # Avoid unreasonably high piece counts.
                n_upper = min(n_upper, n_start + 50)
            else:
                n_upper = n_start + 10

            for n in range(n_start, n_upper + 1):
                min_sum = n * w_min
                max_sum = n * w_max
                if blank_width_mm < min_sum or blank_width_mm > max_sum:
                    continue

                # For a feasible partition, the minimal possible maximum
                # piece width is at least blank_width_mm / n and at
                # least w_min. Use that as the effective width, while
                # ensuring it honours all upper bounds.
                eff_width = max(blank_width_mm / float(n), w_min)
                if eff_width > w_max or eff_width > max_machine_width_mm:
                    continue

                _record(n, eff_width, "uneven")
                return n, eff_width

        # If constraints prevent a feasible split, fall back to no split
        # while keeping metrics consistent.
        _record(1, blank_width_mm, "none")
        return 1, blank_width_mm

    # ------------------------------------------------------------------
    # Legacy path: use individual machine width limits when present.
    # ------------------------------------------------------------------
    # Machine width limits: ignore non-positive values.
    width_limits = []
    for attr in ("max_corrugator_width_mm", "max_printer_width_mm", "max_diecut_width_mm"):
        val = _float_attr(plant_profile, attr)
        if val and val > 0.0:
            width_limits.append(val)

    if not width_limits:
        # No applicable width limits; no splitting.
        metrics.extra.setdefault("pieces_per_box", 1)
        return 1, blank_width_mm

    effective_limit = min(width_limits)
    if effective_limit <= 0.0 or blank_width_mm <= effective_limit:
        metrics.extra.setdefault("pieces_per_box", 1)
        return 1, blank_width_mm

    # Compute number of pieces per finished box required to respect
    # the effective width limit.
    pieces_per_box = int(math.ceil(blank_width_mm / effective_limit))
    if pieces_per_box < 1:
        pieces_per_box = 1

    effective_blank_width = blank_width_mm / float(pieces_per_box)

    _record(pieces_per_box, effective_blank_width, "legacy")

    return pieces_per_box, effective_blank_width



