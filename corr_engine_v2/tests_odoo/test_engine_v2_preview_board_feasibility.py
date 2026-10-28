import json

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("corr_mrp_bridge")
class TestEngineV2PreviewBoardFeasibility(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        # Minimal plant params for Engine V2 preview.
        cls.plant = env["corr.plant.param"].create(
            {
                "code": "BOARD_FEAS",
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
                # Provide explicit sheet dimensions so layout can be
                # resolved during preview (blanks_per_sheet > 0).
                "sheet_length_mm": 2000.0,
                "sheet_width_mm": 2000.0,
            }
        )

        # Finished product template + spec skeleton.
        cls.pt = env["product.template"].create({"name": "Board Preview Box"})

    def _make_spec(self, liner_gsm, medium_gsm):
        env = self.env
        Spec = env["corr.spec.template"].with_context(skip_corr_stack_check=True)
        spec = Spec.create(
            {
                "name": "Board Preview Spec",
                "product_tmpl_id": self.pt.id,
                "style": "0201",
                "length_mm": 300.0,
                "width_mm": 200.0,
                "height_mm": 150.0,
                "ply_count": 2,
                "coverage_pct": 0.0,
            }
        )
        # Minimal stack lines. To respect engine constraints while still
        # exercising board feasibility, only create layers when the GSM
        # values are positive. When both GSMs are zero, the spec has no
        # stack lines and the engine will treat the board structure as
        # missing/invalid.
        flute = env["corr.flute"].create({"name": "B", "code": "B", "caliper_mm": 2.5, "tuf": 0.05})
        if liner_gsm > 0.0:
            env["corr.spec.stack.line"].create(
                {
                    "spec_id": spec.id,
                    "sequence": 10,
                    "role": "liner",
                    "gsm": liner_gsm,
                    "bf": 23.0,
                    "color": "Kraft",
                }
            )
        if medium_gsm > 0.0:
            env["corr.spec.stack.line"].create(
                {
                    "spec_id": spec.id,
                    "sequence": 20,
                    "role": "medium",
                    "gsm": medium_gsm,
                    "bf": 23.0,
                    "flute_id": flute.id,
                    "color": "Kraft",
                }
            )
        return spec

    def test_preview_includes_board_data_with_valid_stack(self):
        """Preview succeeds and raw_json contains board + RM data when board is valid."""

        spec = self._make_spec(liner_gsm=180.0, medium_gsm=120.0)
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": spec.id,
                "plant_id": self.plant.id,
                "order_qty": 10.0,
            }
        )

        wiz.action_run_debug()
        self.assertTrue(wiz.raw_json)

        payload = json.loads(wiz.raw_json or "{}")
        # RM requirements in payload must include corrugation RMs.
        rm_reqs = payload.get("rm_requirements") or []
        roles = {rm.get("role") for rm in rm_reqs}
        self.assertIn("liner", roles)
        self.assertIn("medium", roles)

        # Trace must contain a board_structure_resolved step with layers.
        trace = payload.get("trace") or []
        bs_entries = [
            t for t in trace if t.get("step") == "board_structure_resolved"
        ]
        self.assertTrue(bs_entries, "Expected board_structure_resolved in trace")
        layers = (bs_entries[0].get("payload") or {}).get("layers") or []
        self.assertTrue(layers, "Expected at least one board layer in board_structure_resolved")

    def test_preview_fails_when_board_layers_invalid(self):
        """Preview surfaces a clear error when board/material setup is incomplete."""

        # Stack lines have zero GSM, so resolved board structure is empty.
        spec = self._make_spec(liner_gsm=0.0, medium_gsm=0.0)
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": spec.id,
                "plant_id": self.plant.id,
                "order_qty": 10.0,
            }
        )

        with self.assertRaises(UserError) as ctx:
            wiz.action_run_debug()
        msg = str(ctx.exception)
        self.assertIn("Engine V2 validation failed", msg)
        self.assertIn("board/material resolution failed", msg)
        self.assertFalse(wiz.raw_json)



