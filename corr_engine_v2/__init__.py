"""Corrugation Engine V2 addon bootstrap."""

import sys

if __name__.startswith("odoo.addons."):
    from . import models  # type: ignore[f401]
    from . import wizards  # type: ignore[f401]



