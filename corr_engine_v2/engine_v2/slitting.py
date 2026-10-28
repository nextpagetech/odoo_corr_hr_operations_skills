from __future__ import annotations

import math
from typing import Any, List

from . import Piece
from .spec_input import EngineSpecInput
from .plant_profiles import PlantProfile
from . import EngineMetrics


def resolve_slitting(
    spec_input: EngineSpecInput, plant_profile: PlantProfile, metrics: EngineMetrics
) -> List[Piece]:
    """Resolve simple sheet slitting into logical pieces.

    Simplified rule:
    - If plant_profile.sheet_width_mm is defined and > blank_width_mm,
      compute pieces_per_sheet = floor(sheet_width_mm / blank_width_mm).
    - If pieces_per_sheet > 1, create that many Piece objects, each
      representing one equal-width blank cut from the board sheet.

    This helper is pure and does not alter the StageGraph topology; it
    only returns logical Piece objects that may be used by the bridge or
    future pipeline steps.
    """

    # Read dimensions from inputs
    try:
        sheet_width_mm = float(getattr(plant_profile, "sheet_width_mm", 0.0) or 0.0)
    except Exception:
        sheet_width_mm = 0.0

    try:
        blank_width_mm = float(getattr(spec_input, "blank_width_mm", 0.0) or 0.0)
    except Exception:
        blank_width_mm = 0.0

    try:
        blank_length_mm = float(getattr(spec_input, "blank_length_mm", 0.0) or 0.0)
    except Exception:
        blank_length_mm = 0.0

    if sheet_width_mm <= 0.0 or blank_width_mm <= 0.0:
        return []

    pieces_per_sheet = int(math.floor(sheet_width_mm / blank_width_mm))
    if pieces_per_sheet <= 1:
        return []

    ratio = 1.0 / float(pieces_per_sheet)
    pieces: List[Piece] = []
    for idx in range(pieces_per_sheet):
        pieces.append(
            Piece(
                key=f"piece_{idx + 1}",
                parent_product_key="sfg_board",
                ratio=ratio,
                width_mm=blank_width_mm,
                length_mm=blank_length_mm,
                description="Slit board piece",
            )
        )

    return pieces




