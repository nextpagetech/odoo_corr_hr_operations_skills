from math import ceil

from odoo.tests.common import TransactionCase, tagged
from odoo.addons.corr_engine_v2.engine_v2 import compute as engine_v2_compute
from odoo.addons.corr_engine_v2.spec_adapter import build_engine_spec_input
from odoo.addons.corr_engine_v2.plant_adapter import build_plant_profile


@tagged("corr_mrp_bridge")
class TestEngineV2BomAdapterRouting(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        # Plant params so that printing_ups = diecut_ups = 4 (via product hook).
        cls.plant = env["corr.plant.param"].create({
            "code": "DEFAULT",
            "setup_waste_m2": 0.0,
            "process_waste_pct": 2.0,
            "default_glue_lap_mm": 20.0,
            "flap_basis": "fixed",
            "flap_fixed_mm": 20.0,
            "ink_gm2_at_100pct_default": 5.0,
            "lam_overlap_percent_default": 5.0,
            "default_reel_widths_mm": [1200, 1600],
            "humidity_factor": 1.0,
            "rounding_rules_json": {"kg": 3, "m": 3, "m2": 4},
            "gsm_tolerance_pct": 5.0,
            "default_ply_max": 3,
        })

        # Products for liner and medium.
        uom_kg = env.ref("uom.product_uom_kgm")
        cls.prod_liner = env["product.product"].create({
            "name": "Liner 180 GSM",
            "uom_id": uom_kg.id,
            "uom_po_id": uom_kg.id,
        })
        cls.prod_medium = env["product.product"].create({
            "name": "Medium 120 GSM",
            "uom_id": uom_kg.id,
            "uom_po_id": uom_kg.id,
        })

        # Mapping bands for liner and medium.
        env["corr.material.product.map"].create({
            "kind": "liner",
            "gsm_min": 150,
            "gsm_max": 200,
            "product_id": cls.prod_liner.id,
        })
        env["corr.material.product.map"].create({
            "kind": "medium",
            "gsm_min": 100,
            "gsm_max": 130,
            "product_id": cls.prod_medium.id,
        })

        # Finished product with corr_printing_ups = 4 so Engine V2 sees effective_ups = 4.
        cls.pt = env["product.template"].create({
            "name": "Routing Box",
            "corr_printing_ups": 4,
        })
        Spec = env["corr.spec.template"].with_context(skip_corr_stack_check=True)
        cls.spec = Spec.create({
            "name": "Routing Spec",
            "product_tmpl_id": cls.pt.id,
            "style": "0201",
            "length_mm": 600.0,
            "width_mm": 400.0,
            "height_mm": 200.0,
            "ply_count": 2,
        })
        # Simple 2-ply stack: liner + medium with required BF and flute.
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

        # Configure machine + sheet so that layout_blanks_per_sheet = 8.
        # Blank dims for 0201: length = 2*L + W + glue_flap = 2*600 + 400 + 20 = 1620
        # width  = W + 2*H = 400 + 2*200 = 800
        # Sheet:  length  = 3240 (2 blanks along length)
        #         width   = 3200 (4 blanks across width)
        # => blanks_per_sheet = 2 * 4 = 8
        plant = cls.plant
        plant.write({
            "default_reel_widths_mm": [3240],
            "sheet_length_mm": 3240.0,
            "sheet_width_mm": 3200.0,
        })

        # Minimal routing setup so that mrp.routing.workcenter records
        # created by the adapter always have a workcenter_id.
        wc = env["mrp.workcenter"].create({"name": "Routing WC"})
        OpMap = env["corr.operation.map"]
        OpMap.create({
            "op_name": "Corrugation",
            "workcenter_id": wc.id,
        })
        OpMap.create({
            "op_name": "Printing",
            "workcenter_id": wc.id,
        })
        OpMap.create({
            "op_name": "Slot/Die",
            "workcenter_id": wc.id,
        })

    def _build_bom_with_engine_v2(self):
        Wizard = self.env["corr.bom.generate.wizard"].with_context(engine_v2_enabled=True)
        wiz = Wizard.create({
            "spec_id": self.spec.id,
            "target_product_tmpl_id": self.pt.id,
        })
        action = wiz.action_apply()
        self.assertEqual(action.get("res_model"), "mrp.bom")
        bom = self.env["mrp.bom"].browse(action.get("res_id"))
        self.assertTrue(bom.exists())
        return bom

    def _expected_passes_per_sheet(self):
        """Recompute passes_per_sheet from the engine metrics."""
        spec_input = build_engine_spec_input(self.spec, sfg_mode="staged")
        plant_profile = build_plant_profile(self.plant)
        result = engine_v2_compute(spec_input, plant_profile)
        metrics = getattr(result, "metrics", None)
        extra = getattr(metrics, "extra", {}) or {}
        layout_bps = int(extra.get("layout_blanks_per_sheet", 0) or 0)
        effective_ups = int(extra.get("effective_ups", 0) or 0)
        if layout_bps > 0 and effective_ups > 0 and layout_bps > effective_ups:
            return float(ceil(layout_bps / float(effective_ups)))
        return 1.0

    def test_printing_time_scaled_when_layout_exceeds_ups(self):
        bom = self._build_bom_with_engine_v2()

        printing_ops = bom.operation_ids.filtered(lambda op: (op.name or "").strip() == "Printing")
        self.assertTrue(printing_ops, "Printing operation should be present")
        printing_op = printing_ops[0]

        expected_passes = self._expected_passes_per_sheet()
        self.assertEqual(printing_op.time_cycle_manual, expected_passes)

    def test_cycle_time_unchanged_when_layout_within_ups(self):
        # Adjust product UPS hint; effective behaviour is validated
        # against the engine metrics rather than hard-coded values.
        self.pt.corr_printing_ups = 8

        bom = self._build_bom_with_engine_v2()
        printing_ops = bom.operation_ids.filtered(lambda op: (op.name or "").strip() == "Printing")
        self.assertTrue(printing_ops, "Printing operation should be present")
        printing_op = printing_ops[0]

        expected_passes = self._expected_passes_per_sheet()
        self.assertEqual(printing_op.time_cycle_manual, expected_passes)

