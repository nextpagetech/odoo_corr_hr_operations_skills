"""corr_mrp_bridge addon bootstrap.

When loaded by Odoo (module name starting with ``odoo.addons.``), this
module registers its models and wizards. For standalone Engine V2
unit tests, the addon may be imported as ``custom_addons.corr_mrp_bridge``
without an Odoo environment; in that case we avoid importing business
models to keep the engine decoupled and to prevent Odoo assertions.
"""

import sys

if __name__.startswith("odoo.addons."):
    from . import models  # type: ignore[f401]
    from . import wizards  # type: ignore[f401]
    from .hooks import post_init_hook  # type: ignore[f401]
else:  # Imported outside Odoo (e.g., pure engine tests)
    post_init_hook = None
