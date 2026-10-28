from __future__ import annotations

from typing import Any, Dict, Tuple


def resolve_printing_ups(plant_profile: Any, options: Dict | None = None) -> int:
    """Resolve printing UPS from plant profile or options.

    No hard-coded defaults beyond a safe minimum of 1.
    """

    opts = options or {}
    try:
        raw = getattr(plant_profile, "printing_ups", None)
    except Exception:
        raw = None
    if raw in (None, "", 0, 0.0):
        raw = opts.get("printing_ups", 1)
    try:
        val = int(raw)
    except Exception:
        val = 1
    return max(1, val)


def resolve_diecut_ups(plant_profile: Any, options: Dict | None = None) -> int:
    """Resolve die-cut UPS from plant profile or options."""

    opts = options or {}
    try:
        raw = getattr(plant_profile, "diecut_ups", None)
    except Exception:
        raw = None
    if raw in (None, "", 0, 0.0):
        raw = opts.get("diecut_ups", 1)
    try:
        val = int(raw)
    except Exception:
        val = 1
    return max(1, val)


def resolve_effective_ups(printing_ups: int, diecut_ups: int, options: Dict | None = None) -> int:
    """Resolve an effective UPS value from printing / diecut UPS.

    Baseline behaviour:
    - effective_ups = min(printing_ups, diecut_ups) for positive values.

    V2 overrides (all optional, backwards-compatible):
    - options.preferred_ups: soft target; clamps effective_ups downwards.
    - options.max_ups: absolute upper bound; clamps effective_ups downwards.
    - options.forced_ups + options.ups_enforcement:
        - ups_enforcement == "soft":
            best-effort clamp to forced_ups without exceeding plant limits.
        - ups_enforcement == "hard":
            requires forced_ups <= effective plant capacity, otherwise raises
            ValueError to signal an incompatible configuration.
    """

    opts = options or {}

    candidates = [u for u in (printing_ups, diecut_ups) if u and u > 0]
    if not candidates:
        base = 1
    else:
        base = max(1, min(candidates))

    # Optional clamping by preferred_ups / max_ups (layout policy hints).
    try:
        preferred_raw = opts.get("preferred_ups")
        preferred_ups = int(preferred_raw) if preferred_raw not in (None, "", 0, 0.0) else None
    except Exception:
        preferred_ups = None

    try:
        max_raw = opts.get("max_ups")
        max_ups = int(max_raw) if max_raw not in (None, "", 0, 0.0) else None
    except Exception:
        max_ups = None

    effective = base
    if max_ups is not None and max_ups > 0:
        effective = max(1, min(effective, max_ups))
    if preferred_ups is not None and preferred_ups > 0:
        # Treat preferred_ups as a soft target that cannot exceed the
        # clamped plant capacity.
        effective = max(1, min(effective, preferred_ups))

    # Forced UPS behaviour.
    try:
        forced_raw = opts.get("forced_ups")
        forced_ups = int(forced_raw) if forced_raw not in (None, "", 0, 0.0) else None
    except Exception:
        forced_ups = None

    enforcement = str(opts.get("ups_enforcement", "") or "").strip().lower()

    if not forced_ups or forced_ups <= 0:
        return effective

    if enforcement == "hard":
        # Hard enforcement: forced_ups must not exceed the plant-derived
        # capacity (base). Otherwise the configuration is invalid.
        if forced_ups > base:
            raise ValueError(
                f"Forced UPS {forced_ups} exceeds plant capacity {base} "
                f"(printing_ups={printing_ups}, diecut_ups={diecut_ups})."
            )
        return max(1, forced_ups)

    # Soft enforcement (or unknown flag): treat forced_ups as a target
    # that cannot exceed plant capacity.
    return max(1, min(effective, forced_ups))


def resolve_all_ups(plant_profile: Any, options: Dict | None = None) -> Tuple[int, int, int]:
    """Convenience helper returning (printing_ups, diecut_ups, effective_ups)."""

    printing = resolve_printing_ups(plant_profile, options=options)
    diecut = resolve_diecut_ups(plant_profile, options=options)
    effective = resolve_effective_ups(printing, diecut, options=options)
    return printing, diecut, effective



