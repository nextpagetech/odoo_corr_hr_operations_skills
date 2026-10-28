from odoo.tests.common import TransactionCase, tagged


@tagged("corr_mrp_bridge")
class TestEngineV2BomAdapter(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        # Minimal plant params to allow Engine V2 plant profile resolution.
        env["corr.plant.param"].create({
            "code": "DEFAULT",
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
            # Provide sheet size so that layout and routing are
            # well-defined for Engine V2 BoM generation.
            "sheet_length_mm": 2000.0,
            "sheet_width_mm": 2000.0,
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

        # Finished product template + spec.
        cls.pt = env["product.template"].create({"name": "Engine V2 Box"})
        Spec = env["corr.spec.template"].with_context(skip_corr_stack_check=True)
        cls.spec = Spec.create({
            "name": "Engine V2 Spec",
            "product_tmpl_id": cls.pt.id,
            "style": "0201",
            "length_mm": 300.0,
            "width_mm": 200.0,
            "height_mm": 150.0,
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

        # Minimal routing setup so that the Engine V2 BoM adapter can
        # create operations without violating the NOT NULL constraint on
        # mrp.routing.workcenter.workcenter_id.
        wc = env["mrp.workcenter"].create({"name": "Engine V2 Generic WC"})
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

    def test_engine_v2_bom_adapter_creates_bom_lines(self):
        Wizard = self.env["corr.bom.generate.wizard"].with_context(engine_v2_enabled=True)
        wiz = Wizard.create({
            "spec_id": self.spec.id,
            "target_product_tmpl_id": self.pt.id,
        })

        action = wiz.action_apply()
        self.assertEqual(action.get("res_model"), "mrp.bom")

        bom = self.env["mrp.bom"].browse(action.get("res_id"))
        self.assertTrue(bom.exists())
        self.assertEqual(bom.product_tmpl_id.id, self.pt.id)

        # Engine V2 BoM adapter should create at least liner + medium lines.
        roles = {line.corr_role for line in bom.bom_line_ids}
        self.assertIn("liner", roles)
        self.assertIn("medium", roles)

        # Quantities must be positive and come directly from EngineResult.rm_requirements.
        total_qty = sum(line.product_qty for line in bom.bom_line_ids)
        self.assertGreater(total_qty, 0.0)

        # Engine-generated lines should be marked so they can be safely
        # replaced on subsequent runs without touching manual lines.
        self.assertTrue(all(line.corr_engine_generated for line in bom.bom_line_ids))

