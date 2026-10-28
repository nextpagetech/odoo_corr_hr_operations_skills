"""Adapter facade for corr.engine.v2.spec inputs.

The implementation currently delegates to the shared spec adapter so existing
Engine V2 compute paths remain unchanged. Keeping this module separate gives
the new V2 spec shell its own import surface as the domain grows.
"""

from ..spec_adapter import build_engine_spec_input


def build_v2_engine_spec_input(v2_spec, sfg_mode="staged"):
    """Build EngineSpecInput from a corr.engine.v2.spec record."""

    return build_engine_spec_input(v2_spec, sfg_mode=sfg_mode)



