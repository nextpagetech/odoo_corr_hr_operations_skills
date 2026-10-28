from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

from .spec_input import EngineSpecInput


@dataclass
class BoardLayer:
    """Single layer in a corrugated board structure.

    - type: legacy bucket used by current RM rules ("liner" or "medium").
    - role: richer V2 role when available ("outer_liner", "medium", etc.).
    """

    type: str  # "liner" or "medium"
    gsm: float
    layer_no: int
    role: str = ""
    flute_code: str = ""
    flute_tuf: float = 0.0
    bf: float = 0.0
    color: str = ""
    material_product_id: int = 0
    material_product_name: str = ""
    material_product_uom_id: int = 0
    material_product_uom_name: str = ""
    material_template_id: int = 0
    material_template_name: str = ""
    gsm_value_id: int = 0
    bf_value_id: int = 0
    color_value_id: int = 0


@dataclass
class BoardStructure:
    """Corrugated board structure as an ordered list of layers."""

    layers: List[BoardLayer] = field(default_factory=list)


def _as_board_layer(idx: int, raw: Any) -> BoardLayer:
    """Convert a generic layer descriptor (dict or object) into BoardLayer."""

    def _s(name: str) -> str:
        try:
            return str(raw[name]) if isinstance(raw, dict) else str(getattr(raw, name))
        except Exception:
            return ""

    def _f(name: str) -> float:
        try:
            val = raw[name] if isinstance(raw, dict) else getattr(raw, name)
        except Exception:
            val = 0.0
        try:
            return float(val or 0.0)
        except Exception:
            return 0.0

    t = (_s("type") or "").strip().lower()
    if t not in ("liner", "medium"):
        t = "liner"
    gsm = _f("gsm")
    # Optional richer role when provided; fall back to type.
    role = (_s("role") or "").strip().lower() or t
    flute_code = (_s("flute_code") or _s("flute_type_code") or "").strip().upper()
    flute_tuf = _f("flute_tuf") or _f("tuf")
    # Respect explicit layer_no when provided, else fallback to index.
    try:
        layer_no = int(raw["layer_no"]) if isinstance(raw, dict) else int(getattr(raw, "layer_no"))
    except Exception:
        layer_no = idx
    return BoardLayer(
        type=t,
        gsm=gsm,
        layer_no=layer_no,
        role=role,
        flute_code=flute_code,
        flute_tuf=flute_tuf,
        bf=_f("bf"),
        color=_s("color"),
        material_product_id=int(_f("material_product_id") or 0),
        material_product_name=_s("material_product_name"),
        material_product_uom_id=int(_f("material_product_uom_id") or 0),
        material_product_uom_name=_s("material_product_uom_name"),
        material_template_id=int(_f("material_template_id") or 0),
        material_template_name=_s("material_template_name"),
        gsm_value_id=int(_f("gsm_value_id") or 0),
        bf_value_id=int(_f("bf_value_id") or 0),
        color_value_id=int(_f("color_value_id") or 0),
    )


def resolve_board_structure(spec_input: EngineSpecInput) -> BoardStructure:
    """Resolve a BoardStructure from the spec input.

    Precedence (V2-first, legacy fallback):

    - If spec_input.board_layers_v2 is provided, it is interpreted as an
      ordered sequence of rich board-layer entries (BoardLayerInput or
      dict-like objects) with attributes:
        - role: "outer_liner" | "medium" | "inner_liner" | "center_liner" | "other"
        - gsm: grammage
      These are bucketed into the legacy "liner"/"medium" types for the
      current engine slice while preserving the original role.

    - Else if spec_input.board_layers is provided, it is interpreted as
      an ordered sequence of layers (dicts or objects) with attributes:
        - type: "liner" or "medium"
        - gsm: grammage
        - layer_no: optional explicit index.

    - Otherwise, falls back to the legacy scalar fields:
        - liner_gsm
        - medium_gsm
      and creates a simple 2-layer structure: liner -> medium.
    """

    layers: List[BoardLayer] = []

    # 1) V2 source of truth when available.
    v2_layers_raw = getattr(spec_input, "board_layers_v2", None)
    if v2_layers_raw:
        try:
            seq = list(v2_layers_raw)
        except TypeError:
            seq = [v2_layers_raw]

        for idx, raw in enumerate(seq, start=1):
            # role is the richer V2 semantic; type is the legacy bucket.
            try:
                role = (str(raw.role) if not isinstance(raw, dict) else str(raw.get("role", ""))).strip().lower()
            except Exception:
                try:
                    role = (raw.get("role") if isinstance(raw, dict) else getattr(raw, "role", "")) or ""
                    role = str(role).strip().lower()
                except Exception:
                    role = ""

            # Bucket into liner/medium for current engine slice.
            if role == "medium":
                t = "medium"
            else:
                t = "liner"

            try:
                gsm_val = raw.gsm if not isinstance(raw, dict) else raw.get("gsm", 0.0)
            except Exception:
                gsm_val = 0.0
            try:
                gsm = float(gsm_val or 0.0)
            except Exception:
                gsm = 0.0

            if gsm <= 0.0:
                continue

            try:
                flute_code = raw.flute_type_code if not isinstance(raw, dict) else raw.get("flute_type_code", "")
            except Exception:
                flute_code = ""
            try:
                flute_tuf_val = raw.flute_tuf if not isinstance(raw, dict) else raw.get("flute_tuf", 0.0)
                flute_tuf = float(flute_tuf_val or 0.0)
            except Exception:
                flute_tuf = 0.0
            def _raw_value(name, default=None):
                try:
                    return raw.get(name, default) if isinstance(raw, dict) else getattr(raw, name, default)
                except Exception:
                    return default
            try:
                bf = float(_raw_value("bf", 0.0) or 0.0)
            except Exception:
                bf = 0.0

            layers.append(
                BoardLayer(
                    type=t,
                    gsm=gsm,
                    layer_no=idx,
                    role=role or t,
                    flute_code=str(flute_code or "").strip().upper(),
                    flute_tuf=flute_tuf,
                    bf=bf,
                    color=str(_raw_value("color", "") or ""),
                    material_product_id=int(_raw_value("material_product_id", 0) or 0),
                    material_product_name=str(_raw_value("material_product_name", "") or ""),
                    material_product_uom_id=int(_raw_value("material_product_uom_id", 0) or 0),
                    material_product_uom_name=str(_raw_value("material_product_uom_name", "") or ""),
                    material_template_id=int(_raw_value("material_template_id", 0) or 0),
                    material_template_name=str(_raw_value("material_template_name", "") or ""),
                    gsm_value_id=int(_raw_value("gsm_value_id", 0) or 0),
                    bf_value_id=int(_raw_value("bf_value_id", 0) or 0),
                    color_value_id=int(_raw_value("color_value_id", 0) or 0),
                )
            )

        return BoardStructure(layers=layers)

    # 2) Legacy board_layers attribute (already bucketed).
    layers_raw = getattr(spec_input, "board_layers", None)
    if layers_raw:
        try:
            seq = list(layers_raw)
        except TypeError:
            seq = [layers_raw]
        for idx, raw in enumerate(seq, start=1):
            layer = _as_board_layer(idx, raw)
            if layer.gsm > 0:
                layers.append(layer)
        return BoardStructure(layers=layers)

    # 3) Fallback to simple liner/medium from scalar GSM.
    try:
        liner_gsm = float(getattr(spec_input, "liner_gsm", 0.0) or 0.0)
    except Exception:
        liner_gsm = 0.0
    try:
        medium_gsm = float(getattr(spec_input, "medium_gsm", 0.0) or 0.0)
    except Exception:
        medium_gsm = 0.0

    layer_idx = 1
    if liner_gsm > 0:
        layers.append(
            BoardLayer(type="liner", gsm=liner_gsm, layer_no=layer_idx, role="liner")
        )
        layer_idx += 1
    if medium_gsm > 0:
        layers.append(
            BoardLayer(type="medium", gsm=medium_gsm, layer_no=layer_idx, role="medium")
        )

    return BoardStructure(layers=layers)



