"""Compatibility wrapper for the optional Corrugation Engine v2 adapter."""

try:
    from odoo.addons.corr_engine_v2.spec_adapter import *  # noqa: F401,F403
except Exception:  # pragma: no cover - standalone test fallback
    from custom_addons.corr_engine_v2.spec_adapter import *  # noqa: F401,F403
