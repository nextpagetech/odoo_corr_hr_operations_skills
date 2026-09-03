from odoo import SUPERUSER_ID, api, models


def _ensure_env(first_arg, registry=None):
    """Allow post_init_hook to work whether Odoo passes env (v17+) or cr/registry (older)."""
    if isinstance(first_arg, (api.Environment, models.BaseModel)):
        return first_arg
    return api.Environment(first_arg, SUPERUSER_ID, {})


def post_init_hook(env, registry=None):
    """Seed Operations skill type/levels and sync operation skills from corrugation engine."""
    env = _ensure_env(env, registry)
    env["hr.skill"].sudo()._sync_operations_skills()
