from __future__ import annotations

from typing import Any, Dict

from .spec_input import EngineSpecInput
from .plant_profiles import PlantProfile
from . import EngineMetrics


def _pct(source: Any, attr: str) -> float:
    """Safely read a percentage value from an object."""
    try:
        raw = getattr(source, attr, None)
    except Exception:
        raw = None
    if raw in (None, ""):
        return 0.0
    try:
        return float(raw) or 0.0
    except Exception:
        return 0.0


def resolve_waste(
    spec_input: EngineSpecInput,
    plant_profile: PlantProfile,
    metrics: EngineMetrics,
    options: Dict | None = None,
) -> Dict[str, float]:
    """Compute waste percentages for the current spec/plant.

    This helper is intentionally simple and data-driven:
    - Reads trim/setup/diecut waste percentages from spec_input first,
      then falls back to the plant_profile.
    - Computes total_waste_pct as their sum.

    It does NOT mutate metrics; callers are responsible for deciding
    whether to apply waste to effective material consumption.
    """

    _ = options  # reserved for future tuning knobs

    trim = _pct(spec_input, "trim_waste_pct") or _pct(plant_profile, "trim_waste_pct")
    setup = _pct(spec_input, "setup_waste_pct") or _pct(plant_profile, "setup_waste_pct")
    diecut = _pct(spec_input, "diecut_waste_pct") or _pct(plant_profile, "diecut_waste_pct")

    total = float(trim) + float(setup) + float(diecut)
    if total < 0.0:
        total = 0.0

    return {
        "trim_waste_pct": float(trim),
        "setup_waste_pct": float(setup),
        "diecut_waste_pct": float(diecut),
        "total_waste_pct": float(total),
    }




