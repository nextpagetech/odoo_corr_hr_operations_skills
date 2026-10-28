from __future__ import annotations

import math
from typing import Any, Dict

from .spec_input import EngineSpecInput
from .plant_profiles import PlantProfile


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


def _derive_sheet_from_ups(
    blank_length_mm: float,
    blank_width_mm: float,
    printing_ups: int,
) -> (float, float):
    """Derive a minimal sheet size for a given UPS target.

    The current strategy is deliberately simple and stable:
    - treat UPS as the exact number of blanks per sheet,
    - search over factor pairs (rows, cols) where rows * cols == UPS,
    - choose the (rows, cols) combination that minimises sheet area
      for the given blank dimensions.

    Returns (sheet_length_mm, sheet_width_mm); when inputs are invalid
    the returned dimensions are zeros so that callers can fall back to
    their existing behaviour.
    """

    try:
        ups_val = int(printing_ups or 0)
    except Exception:
        ups_val = 0
    if ups_val <= 0 or blank_length_mm <= 0.0 or blank_width_mm <= 0.0:
        return 0.0, 0.0

    best_area = None
    best_rows = 1
    best_cols = ups_val

    for rows in range(1, ups_val + 1):
        if ups_val % rows != 0:
            continue
        cols = ups_val // rows
        sheet_len = rows * blank_length_mm
        sheet_wid = cols * blank_width_mm
        area = sheet_len * sheet_wid
        if best_area is None or area < best_area:
            best_area = area
            best_rows = rows
            best_cols = cols

    return best_rows * blank_length_mm, best_cols * blank_width_mm


def resolve_layout(
    spec_input: EngineSpecInput,
    plant_profile: PlantProfile,
) -> Dict[str, object]:
    """Resolve sheet layout for blanks on a corrugated sheet.

    Inputs (derived from spec_input and plant_profile):
    - blank_length_mm, blank_width_mm from spec_input
    - sheet_length_mm, sheet_width_mm from plant_profile (or spec_input as fallback)
    - plant_profile.allow_blank_rotation controls whether rotated layout
      is considered.

    Returns a dict with:
    - orientation: "normal" or "rotated"
    - blanks_per_row, blanks_per_column, blanks_per_sheet
    - sheet_usage_percent, waste_percent
    """

    blank_length_mm = _float_attr(spec_input, "blank_length_mm")
    blank_width_mm = _float_attr(spec_input, "blank_width_mm")

    layout_policy = getattr(spec_input, "layout_policy", None)

    # Sheet dimensions are primarily plant-driven; LayoutPolicyInput and
    # EngineOverrides may override this behaviour when present.
    sheet_length_plant = _float_attr(plant_profile, "sheet_length_mm")
    sheet_width_plant = _float_attr(plant_profile, "sheet_width_mm")
    sheet_length_spec = _float_attr(spec_input, "sheet_length_mm")
    sheet_width_spec = _float_attr(spec_input, "sheet_width_mm")

    # Determine sheet source strategy:
    # - Layout policy sheet_source when provided,
    # - otherwise fall back to PlantProfile.default_sheet_behavior,
    # - default to plant_default when neither is set.
    sheet_source = None
    if layout_policy is not None:
        try:
            sheet_source = (layout_policy.sheet_source or "").strip() or None
        except Exception:
            sheet_source = None
    if not sheet_source:
        try:
            sheet_source = (
                (getattr(plant_profile, "default_sheet_behavior", "") or "").strip()
                or "plant_default"
            )
        except Exception:
            sheet_source = "plant_default"

    if sheet_source == "manual":
        # Prefer spec-side manual sheet overrides, fall back to plant.
        sheet_length_mm = sheet_length_spec or sheet_length_plant
        sheet_width_mm = sheet_width_spec or sheet_width_plant
    elif sheet_source == "derived":
        # Derived sheet behaviour: compute a minimal sheet size that
        # honours the requested printing UPS. Plant-level sheet size is
        # intentionally ignored here to avoid accidental overfitting
        # from large default sheets.
        try:
            printing_ups_raw = getattr(plant_profile, "printing_ups", 1)
        except Exception:
            printing_ups_raw = 1
        try:
            printing_ups = int(printing_ups_raw or 1)
        except Exception:
            printing_ups = 1
        if printing_ups <= 0:
            printing_ups = 1

        sheet_length_mm, sheet_width_mm = _derive_sheet_from_ups(
            blank_length_mm, blank_width_mm, printing_ups
        )
    else:
        # plant_default: prefer plant config, then spec overrides.
        sheet_length_mm = sheet_length_plant or sheet_length_spec
        sheet_width_mm = sheet_width_plant or sheet_width_spec

    # Layout rotation: LayoutPolicyInput.allow_rotation overrides plant
    # when present, otherwise plant_profile.allow_blank_rotation drives it.
    if layout_policy is not None:
        try:
            allow_rotation = bool(layout_policy.allow_rotation)
        except Exception:
            allow_rotation = bool(getattr(plant_profile, "allow_blank_rotation", True))
    else:
        allow_rotation = bool(getattr(plant_profile, "allow_blank_rotation", True))

    # Apply trim and gripper margins when configured in the layout policy.
    trim_margin_mm = 0.0
    gripper_margin_mm = 0.0
    if layout_policy is not None:
        try:
            trim_margin_mm = float(getattr(layout_policy, "trim_margin_mm", 0.0) or 0.0)
        except Exception:
            trim_margin_mm = 0.0
        try:
            gripper_margin_mm = float(
                getattr(layout_policy, "gripper_margin_mm", 0.0) or 0.0
            )
        except Exception:
            gripper_margin_mm = 0.0

    # Compute usable window for layout without changing the physical
    # sheet dimensions. Margins reduce the effective area available for
    # placing blanks but do not affect RM consumption or sheet area.
    usable_length_mm = sheet_length_mm
    usable_width_mm = sheet_width_mm
    if trim_margin_mm > 0.0:
        usable_width_mm = max(0.0, sheet_width_mm - trim_margin_mm)
    if gripper_margin_mm > 0.0:
        usable_length_mm = max(0.0, sheet_length_mm - gripper_margin_mm)

    # Guard against missing/invalid dimensions.
    if (
        blank_length_mm <= 0.0
        or blank_width_mm <= 0.0
        or sheet_length_mm <= 0.0
        or sheet_width_mm <= 0.0
        or usable_length_mm <= 0.0
        or usable_width_mm <= 0.0
    ):
        return {
            "orientation": "normal",
            "blanks_per_row": 0,
            "blanks_per_column": 0,
            "blanks_per_sheet": 0,
            "sheet_usage_percent": 0.0,
            "waste_percent": 0.0,
        }

    # Step 1: normal orientation
    row = int(math.floor(usable_length_mm / blank_length_mm))
    col = int(math.floor(usable_width_mm / blank_width_mm))
    pieces_normal = max(0, row * col)

    # Step 2: rotated orientation (if permitted)
    pieces_rot = 0
    row_rot = 0
    col_rot = 0
    if allow_rotation:
        row_rot = int(math.floor(usable_length_mm / blank_width_mm))
        col_rot = int(math.floor(usable_width_mm / blank_length_mm))
        pieces_rot = max(0, row_rot * col_rot)

    # Step 3: choose best orientation; prefer normal on ties
    if allow_rotation and pieces_rot > pieces_normal:
        orientation = "rotated"
        blanks_per_row = row_rot
        blanks_per_column = col_rot
        blanks_per_sheet = pieces_rot
    else:
        orientation = "normal"
        blanks_per_row = row
        blanks_per_column = col
        blanks_per_sheet = pieces_normal

    # Step 4: waste metrics
    sheet_area = sheet_length_mm * sheet_width_mm
    blank_area = blank_length_mm * blank_width_mm

    if sheet_area <= 0.0 or blanks_per_sheet <= 0:
        sheet_usage_percent = 0.0
    else:
        sheet_usage_percent = (blanks_per_sheet * blank_area) / sheet_area
        if sheet_usage_percent < 0.0:
            sheet_usage_percent = 0.0
        elif sheet_usage_percent > 1.0:
            sheet_usage_percent = 1.0

    waste_percent = 1.0 - sheet_usage_percent
    if waste_percent < 0.0:
        waste_percent = 0.0

    return {
        "sheet_source": sheet_source,
        "orientation": orientation,
        "blanks_per_row": int(blanks_per_row),
        "blanks_per_column": int(blanks_per_column),
        "blanks_per_sheet": int(blanks_per_sheet),
        "sheet_usage_percent": float(sheet_usage_percent),
        "waste_percent": float(waste_percent),
        # Expose the physical sheet dimensions actually used for layout
        # so that downstream validation can rely on resolved values
        # instead of re-deriving them.
        "sheet_length_mm": float(sheet_length_mm),
        "sheet_width_mm": float(sheet_width_mm),
    }



