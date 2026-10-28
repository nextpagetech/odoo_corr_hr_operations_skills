import json

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("corr_mrp_bridge")
class TestEngineV2PreviewFeasibility(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        # Plant with derived sheet behaviour and tight sheet size limits.
        cls.plant = env["corr.plant.param"].create(
            {
                "code": "DERIVED_FEAS",
                "setup_waste_m2": 0.0,
                "process_waste_pct": 2.0,
                "default_glue_lap_mm": 20.0,
                "flap_basis": "fixed",
                "flap_fixed_mm": 20.0,
                "ink_gm2_at_100pct_default": 5.0,
                "lam_overlap_percent_default": 5.0,
                "default_reel_widths_mm": [1200, 1400],
                "humidity_factor": 1.0,
                "rounding_rules_json": {"kg": 3, "m": 3, "m2": 4},
                "gsm_tolerance_pct": 5.0,
                "default_ply_max": 3,
                # Derived-mode behaviour + explicit sheet size limits.
                "sheet_length_mm": 400.0,
                "sheet_width_mm": 700.0,
                "default_sheet_behavior": "derived",
            }
        )

        # Simple finished product + spec so that blank size is
        # controlled by 0201 style rules on the plant.
        cls.pt = env["product.template"].create({"name": "Feasibility Box"})
        Spec = env["corr.spec.template"].with_context(skip_corr_stack_check=True)
        cls.spec = Spec.create(
            {
                "name": "Feasibility Spec",
                "product_tmpl_id": cls.pt.id,
                "style": "0201",
                "length_mm": 300.0,
                "width_mm": 200.0,
                "height_mm": 150.0,
                "ply_count": 2,
            }
        )
        flute = env["corr.flute"].create({"name": "B", "code": "B", "caliper_mm": 2.5, "tuf": 0.05})
        env["corr.spec.stack.line"].create(
            {
                "spec_id": cls.spec.id,
                "sequence": 10,
                "role": "liner",
                "gsm": 180.0,
                "bf": 23.0,
                "color": "Kraft",
            }
        )
        env["corr.spec.stack.line"].create(
            {
                "spec_id": cls.spec.id,
                "sequence": 20,
                "role": "medium",
                "gsm": 120.0,
                "bf": 23.0,
                "flute_id": flute.id,
                "color": "Kraft",
            }
        )

    def _run_preview(self, printing_ups):
        # Persist a Printing UPS hint on the spec so that Engine V2
        # resolves printing_ups from the spec in derived mode.
        xp = dict(self.spec.extra_params_json or {})
        xp["printing_ups"] = int(printing_ups)
        self.spec.extra_params_json = xp

        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": 10.0,
            }
        )
        return wiz

    def test_preview_fails_for_infeasible_derived_sheet(self):
        """Preview surfaces a clear error when derived sheet exceeds plant limits."""

        # For this spec + plant, a higher UPS will drive a derived sheet
        # that violates the plant sheet_length_mm / sheet_width_mm.
        wiz = self._run_preview(printing_ups=4)

        with self.assertRaises(UserError) as ctx:
            wiz.action_run_debug()
        msg = str(ctx.exception)
        self.assertIn("Engine V2 validation failed", msg)
        self.assertIn("Derived sheet", msg)

        # Ensure no raw_json was stored on failure so that create-from-preview
        # cannot proceed with an inconsistent layout.
        self.assertFalse(wiz.raw_json)



