import json

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.addons.corr_mrp_bridge import bom_adapter


@tagged("corr_mrp_bridge")
class TestEngineV2BomFromPreview(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        # Minimal plant params to allow Engine V2 plant profile resolution.
        cls.plant = env["corr.plant.param"].create(
            {
                "code": "DEFAULT_PREVIEW",
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
                # Ensure layout has valid sheet dimensions for previews.
                "sheet_length_mm": 2000.0,
                "sheet_width_mm": 2000.0,
            }
        )

        # Basic routing config: use a single generic workcenter mapped
        # to the Corrugation / Printing / Slot/Die operations so that
        # mrp.routing.workcenter rows created by the Engine V2 BoM
        # adapter always have a non-null workcenter_id.
        wc = env["mrp.workcenter"].create({"name": "Preview Routing WC"})
        OpMap = env["corr.operation.map"]
        OpMap.create({"op_name": "Corrugation", "workcenter_id": wc.id})
        OpMap.create({"op_name": "Printing", "workcenter_id": wc.id})
        OpMap.create({"op_name": "Slot/Die", "workcenter_id": wc.id})

        # Products and mappings for liner, medium and lamination.
        # Prefer standard external IDs when available; fall back to the
        # product's default UoM so tests remain portable across Odoo
        # distributions that do not ship uom.product_uom_sqm.
        try:
            uom_kg = env.ref("uom.product_uom_kgm")
        except Exception:
            uom_kg = env["uom.uom"].search([], limit=1)
        uom_m2 = uom_kg

        cls.prod_liner = env["product.product"].create(
            {
                "name": "Liner 180 GSM",
                "uom_id": uom_kg.id,
                "uom_po_id": uom_kg.id,
            }
        )
        cls.prod_medium = env["product.product"].create(
            {
                "name": "Medium 120 GSM",
                "uom_id": uom_kg.id,
                "uom_po_id": uom_kg.id,
            }
        )
        cls.prod_top_roll = env["product.product"].create(
            {
                "name": "Top Roll 180 GSM",
                "uom_id": uom_kg.id,
                "uom_po_id": uom_kg.id,
            }
        )
        cls.prod_lam = env["product.product"].create(
            {
                "name": "Lamination Film",
                "uom_id": uom_m2.id,
                "uom_po_id": uom_m2.id,
            }
        )

        Map = env["corr.material.product.map"]
        cls.map_liner = Map.create(
            {
                "kind": "liner",
                "gsm_min": 150,
                "gsm_max": 200,
                "product_id": cls.prod_liner.id,
            }
        )
        cls.map_medium = Map.create(
            {
                "kind": "medium",
                "gsm_min": 100,
                "gsm_max": 130,
                "product_id": cls.prod_medium.id,
            }
        )
        cls.map_lam = Map.create(
            {
                "kind": "lamination",
                "product_id": cls.prod_lam.id,
            }
        )
        # Lamination product for subtype-specific mapping (via lamination_type).
        cls.prod_lam_gloss = env["product.product"].create(
            {
                "name": "Lamination Film Gloss 30",
                "uom_id": uom_m2.id,
                "uom_po_id": uom_m2.id,
            }
        )
        cls.map_lam_gloss = Map.create(
            {
                "kind": "lamination",
                "engine_code": "gloss_30",
                "product_id": cls.prod_lam_gloss.id,
            }
        )
        # Ink product + mapping for printing consumable tests.
        cls.prod_ink = env["product.product"].create(
            {
                "name": "Flexo Ink",
                "uom_id": uom_kg.id,
                "uom_po_id": uom_kg.id,
            }
        )
        cls.map_ink_flexo = Map.create(
            {
                "kind": "ink",
                "engine_code": "flexo",
                "product_id": cls.prod_ink.id,
            }
        )
        # Generic ink mapping without engine_code to validate fallback
        # behaviour when print_process is not used for engine_code.
        cls.map_ink_generic = Map.create(
            {
                "kind": "ink",
                "product_id": cls.prod_ink.id,
            }
        )
        # Wire product + generic mapping for stitched locking.
        cls.prod_wire = env["product.product"].create(
            {
                "name": "Stitching Wire",
                "uom_id": uom_kg.id,
                "uom_po_id": uom_kg.id,
            }
        )
        cls.map_wire = Map.create(
            {
                "kind": "wire",
                "product_id": cls.prod_wire.id,
            }
        )
        # Wire product for subtype-specific mapping (via wire_grade).
        cls.prod_wire_heavy = env["product.product"].create(
            {
                "name": "Stitching Wire HEAVY_SS304",
                "uom_id": uom_kg.id,
                "uom_po_id": uom_kg.id,
            }
        )
        cls.map_wire_heavy = Map.create(
            {
                "kind": "wire",
                "engine_code": "heavy_ss304",
                "product_id": cls.prod_wire_heavy.id,
            }
        )

        # Adhesive products + mappings for consumable aggregation tests.
        cls.prod_adh_1 = env["product.product"].create(
            {
                "name": "Adhesive A",
                "uom_id": uom_kg.id,
                "uom_po_id": uom_kg.id,
            }
        )
        cls.prod_adh_2 = env["product.product"].create(
            {
                "name": "Adhesive B",
                "uom_id": uom_kg.id,
                "uom_po_id": uom_kg.id,
            }
        )

        cls.map_adh_corr = Map.create(
            {
                "kind": "adhesive",
                "engine_code": "corrugation",
                "product_id": cls.prod_adh_1.id,
            }
        )
        cls.map_adh_board = Map.create(
            {
                "kind": "adhesive",
                "engine_code": "board",
                "product_id": cls.prod_adh_1.id,
            }
        )
        cls.map_adh_fg = Map.create(
            {
                "kind": "adhesive",
                "engine_code": "fg",
                "product_id": cls.prod_adh_1.id,
            }
        )

        # Top-sheet products for unit-sheet tests.
        cls.prod_top_sheet_mapped = env["product.product"].create(
            {
                "name": "Generic Top Sheet",
                "uom_id": uom_kg.id,
                "uom_po_id": uom_kg.id,
            }
        )
        cls.prod_top_sheet_selected = env["product.product"].create(
            {
                "name": "Selected Top Sheet Product",
                "uom_id": uom_kg.id,
                "uom_po_id": uom_kg.id,
            }
        )
        # Mapping entry for legacy / fallback top-sheet behaviour.
        cls.map_top_sheet = Map.create(
            {
                "kind": "top_sheet",
                "product_id": cls.prod_top_sheet_mapped.id,
            }
        )

        # Secondary UoM in the same weight category for aggregation
        # tests where different UoMs must not be merged.
        uom_cat_kg = env.ref("uom.product_uom_categ_kgm")
        cls.uom_adh_small = env["uom.uom"].create(
            {
                "name": "Adhesive Gram",
                "category_id": uom_cat_kg.id,
                "uom_type": "smaller",
                "factor_inv": 1000.0,  # 1000 g in 1 kg
                "rounding": 0.00001,
            }
        )

        # Finished product template + spec.
        cls.pt = env["product.template"].create({"name": "Preview Box"})
        Spec = env["corr.spec.template"].with_context(skip_corr_stack_check=True)
        cls.spec = Spec.create(
            {
                "name": "Preview Spec",
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

    def _run_preview(self, order_qty=10.0):
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": order_qty,
            }
        )
        action = wiz.action_run_debug()
        self.assertEqual(action.get("res_model"), "corr.engine_v2.debug.wizard")
        # Legacy tests in this module exercise the mapping-based BoM
        # creation flow. Strip paper_resolution so that create-from-preview
        # falls back to corr.material.product.map for paper roles.
        if wiz.raw_json:
            payload = json.loads(wiz.raw_json or "{}")
            payload.pop("paper_resolution", None)
            wiz.raw_json = json.dumps(payload)
        return wiz

    def test_basic_bom_created_from_preview(self):
        wiz = self._run_preview(order_qty=10.0)

        action_bom = wiz.action_create_bom_from_preview()
        self.assertEqual(action_bom.get("res_model"), "mrp.bom")

        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())
        self.assertEqual(bom.product_tmpl_id.id, self.pt.id)
        self.assertEqual(bom.corr_spec_id.id, self.spec.id)
        # BoM base quantity is always 1 FG for reusability.
        self.assertEqual(bom.product_qty, 1.0)

        roles = {line.corr_role for line in bom.bom_line_ids}
        self.assertIn("liner", roles)
        self.assertIn("medium", roles)
        self.assertTrue(all(line.corr_engine_generated for line in bom.bom_line_ids))

        raw_payload = json.loads(wiz.raw_json or "{}")
        components = {c["product"]: c for c in raw_payload.get("bom_struct", {}).get("components", [])}
        liner_comp = components.get("rm_liner")
        medium_comp = components.get("rm_medium")
        if liner_comp:
            liner_line = bom.bom_line_ids.filtered(lambda l: l.corr_role == "liner")[:1]
            self.assertTrue(liner_line)
            # Preview quantities are order-level; BoM lines are per FG.
        self.assertAlmostEqual(
            liner_line.product_qty,
            float(liner_comp.get("qty", 0.0)) / 10.0,
            places=2,
        )
        if medium_comp:
            medium_line = bom.bom_line_ids.filtered(lambda l: l.corr_role == "medium")[:1]
            self.assertTrue(medium_line)
        self.assertAlmostEqual(
            medium_line.product_qty,
            float(medium_comp.get("qty", 0.0)) / 10.0,
            places=2,
        )

    def test_create_bom_from_manual_printed_and_laminated_preview(self):
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": 50.0,
            }
        )

        payload = {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": 50.0,
            "metrics": {"m2_per_box": 1.0, "kg_per_box": 0.0, "extra": {}},
            "rm_requirements": [],
            "quantity_stage_graph": {"nodes": {}, "edges": []},
            "bom_struct": {
                "product_qty": 50.0,
                "components": [
                    {
                        "product": "rm_liner",
                        "qty": 10.0,
                        "uom": "kg",
                        "stage": "corrugator",
                        "metadata": {"consumption_type": "area"},
                    },
                    {
                        "product": "rm_lamination_film",
                        "qty": 5.0,
                        "uom": "m2",
                        "stage": "lamination",
                        "metadata": {"consumption_type": "area"},
                    },
                ],
                "operations": [
                    {"stage": "corrugator", "input_qty": 10.0, "output_qty": 10.0},
                    {"stage": "printing", "input_qty": 10.0, "output_qty": 10.0},
                    {"stage": "diecut", "input_qty": 10.0, "output_qty": 50.0},
                ],
            },
            "trace": [],
        }
        wiz.raw_json = json.dumps(payload)
        wiz.preview_is_current = True

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        roles = {line.corr_role for line in bom.bom_line_ids}
        self.assertIn("liner", roles)
        self.assertIn("lamination", roles)

        # Base quantity is 1 FG; component quantities are per FG.
        self.assertEqual(bom.product_qty, 1.0)
        liner_line = bom.bom_line_ids.filtered(lambda l: l.corr_role == "liner")[:1]
        lam_line = bom.bom_line_ids.filtered(lambda l: l.corr_role == "lamination")[:1]
        self.assertTrue(liner_line)
        self.assertTrue(lam_line)
        self.assertAlmostEqual(liner_line.product_qty, 10.0 / 50.0, places=6)
        self.assertAlmostEqual(lam_line.product_qty, 5.0 / 50.0, places=6)

        op_names = {op.name for op in bom.operation_ids}
        self.assertIn("Corrugation", op_names)
        self.assertIn("Printing", op_names)
        self.assertIn("Slot/Die", op_names)

    def test_recreate_preview_archives_generated_operations(self):
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": 50.0,
            }
        )

        payload = {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": 50.0,
            "metrics": {"m2_per_box": 1.0, "kg_per_box": 0.0, "extra": {}},
            "rm_requirements": [],
            "quantity_stage_graph": {"nodes": {}, "edges": []},
            "bom_struct": {
                "product_qty": 50.0,
                "components": [
                    {
                        "product": "rm_liner",
                        "qty": 10.0,
                        "uom": "kg",
                        "stage": "corrugator",
                        "metadata": {"consumption_type": "area"},
                    },
                ],
                "operations": [
                    {"stage": "corrugator", "input_qty": 10.0, "output_qty": 10.0},
                    {"stage": "printing", "input_qty": 10.0, "output_qty": 10.0},
                ],
            },
            "trace": [],
        }
        wiz.raw_json = json.dumps(payload)
        wiz.preview_is_current = True

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        old_ops = bom.operation_ids.filtered(lambda op: op.corr_engine_generated)
        self.assertTrue(old_ops)

        wiz.action_create_bom_from_preview()
        old_ops.invalidate_recordset(["active"])
        self.assertFalse(any(old_ops.mapped("active")))

        bom = self.env["mrp.bom"].browse(bom.id)
        active_ops = bom.operation_ids.filtered(lambda op: op.corr_engine_generated)
        self.assertTrue(active_ops)
        self.assertFalse(set(old_ops.ids) & set(active_ops.ids))

    def test_lamination_mapping_uses_engine_code_from_metadata(self):
        """Lamination component prefers subtype-specific mapping via lamination_type."""
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": 50.0,
            }
        )

        payload = {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": 50.0,
            "metrics": {"m2_per_box": 1.0, "kg_per_box": 0.0, "extra": {}},
            "rm_requirements": [],
            "quantity_stage_graph": {"nodes": {}, "edges": []},
            "bom_struct": {
                "product_qty": 50.0,
                "components": [
                    {
                        "product": "rm_lamination_film",
                        "qty": 5.0,
                        "uom": "m2",
                        "stage": "lamination",
                        "metadata": {
                            "consumption_type": "area",
                            # Mixed-case / padded subtype code should still map.
                            "lamination_type": "  Gloss_30  ",
                        },
                    },
                ],
                "operations": [],
            },
            "trace": [],
        }
        wiz.raw_json = json.dumps(payload)
        wiz.preview_is_current = True

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        lam_lines = bom.bom_line_ids.filtered(lambda l: l.corr_role == "lamination")
        self.assertEqual(len(lam_lines), 1)
        lam_line = lam_lines[0]
        # Subtype-specific mapping should be used.
        self.assertEqual(lam_line.product_id.id, self.prod_lam_gloss.id)
        self.assertAlmostEqual(lam_line.product_qty, 5.0 / 50.0, places=6)

    def test_ink_component_mapped_from_preview(self):
        """Ink component in preview payload is mapped to ink product."""
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": 40.0,
            }
        )

        payload = {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": 40.0,
            "metrics": {"m2_per_box": 1.0, "kg_per_box": 0.0, "extra": {}},
            "rm_requirements": [],
            "quantity_stage_graph": {"nodes": {}, "edges": []},
            "bom_struct": {
                "product_qty": 40.0,
                "components": [
                    {
                        "product": "rm_ink",
                        "qty": 4.0,
                        "uom": "kg",
                        "stage": "printing",
                        "metadata": {
                            "consumption_type": "area",
                            "kind": "ink",
                            "print_process": "flexo",
                        },
                    },
                ],
                "operations": [
                    {"stage": "printing", "input_qty": 4.0, "output_qty": 40.0},
                ],
            },
            "trace": [],
        }
        wiz.raw_json = json.dumps(payload)
        wiz.preview_is_current = True

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        ink_lines = bom.bom_line_ids.filtered(lambda l: l.corr_role == "ink")
        self.assertEqual(len(ink_lines), 1)
        ink_line = ink_lines[0]
        self.assertEqual(ink_line.product_id.id, self.prod_ink.id)
        # BoM base quantity is 1 FG, so per-FG quantity is total / order_qty.
        self.assertAlmostEqual(ink_line.product_qty, 4.0 / 40.0, places=6)

    def test_ink_generic_mapping_without_engine_code(self):
        """Ink mapping also works when engine_code is not provided."""
        # Deactivate process-specific mapping so generic mapping is used.
        self.map_ink_flexo.active = False

        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": 20.0,
            }
        )

        payload = {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": 20.0,
            "metrics": {"m2_per_box": 1.0, "kg_per_box": 0.0, "extra": {}},
            "rm_requirements": [],
            "quantity_stage_graph": {"nodes": {}, "edges": []},
            "bom_struct": {
                "product_qty": 20.0,
                "components": [
                    {
                        "product": "rm_ink",
                        "qty": 2.0,
                        "uom": "kg",
                        "stage": "printing",
                        # No print_process -> engine_code is None and
                        # generic mapping (kind='ink' only) must apply.
                        "metadata": {"consumption_type": "area", "kind": "ink"},
                    },
                ],
                "operations": [],
            },
            "trace": [],
        }
        wiz.raw_json = json.dumps(payload)
        wiz.preview_is_current = True

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        ink_lines = bom.bom_line_ids.filtered(lambda l: l.corr_role == "ink")
        self.assertEqual(len(ink_lines), 1)
        ink_line = ink_lines[0]
        self.assertEqual(ink_line.product_id.id, self.prod_ink.id)
        self.assertAlmostEqual(ink_line.product_qty, 2.0 / 20.0, places=6)

    def test_wire_component_mapped_from_preview(self):
        """Wire component in preview payload is mapped to wire product."""
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": 50.0,
            }
        )

        payload = {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": 50.0,
            "metrics": {"m2_per_box": 1.0, "kg_per_box": 0.0, "extra": {}},
            "rm_requirements": [],
            "quantity_stage_graph": {"nodes": {}, "edges": []},
            "bom_struct": {
                "product_qty": 50.0,
                "components": [
                    {
                        "product": "rm_wire",
                        "qty": 1.0,
                        "uom": "kg",
                        "stage": "fg",
                        "metadata": {"consumption_type": "length", "kind": "wire"},
                    },
                ],
                "operations": [],
            },
            "trace": [],
        }
        wiz.raw_json = json.dumps(payload)
        wiz.preview_is_current = True

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        wire_lines = bom.bom_line_ids.filtered(lambda l: l.corr_role == "wire")
        self.assertEqual(len(wire_lines), 1)
        wire_line = wire_lines[0]
        self.assertEqual(wire_line.product_id.id, self.prod_wire.id)
        # BoM base quantity is 1 FG, so per-FG quantity is total / order_qty.
        self.assertAlmostEqual(wire_line.product_qty, 1.0 / 50.0, places=6)

    def test_wire_mapping_uses_engine_code_from_wire_grade(self):
        """Wire component prefers subtype-specific mapping via wire_grade when present."""
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": 40.0,
            }
        )

        payload = {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": 40.0,
            "metrics": {"m2_per_box": 1.0, "kg_per_box": 0.0, "extra": {}},
            "rm_requirements": [],
            "quantity_stage_graph": {"nodes": {}, "edges": []},
            "bom_struct": {
                "product_qty": 40.0,
                "components": [
                    {
                        "product": "rm_wire",
                        "qty": 2.0,
                        "uom": "kg",
                        "stage": "fg",
                        "metadata": {
                            "consumption_type": "length",
                            "kind": "wire",
                            # Mixed-case / padded grade should still map.
                            "wire_grade": "  HEAVY_SS304  ",
                        },
                    },
                ],
                "operations": [],
            },
            "trace": [],
        }
        wiz.raw_json = json.dumps(payload)
        wiz.preview_is_current = True

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        wire_lines = bom.bom_line_ids.filtered(lambda l: l.corr_role == "wire")
        self.assertEqual(len(wire_lines), 1)
        wire_line = wire_lines[0]
        # Subtype-specific mapping should be used instead of generic mapping.
        self.assertEqual(wire_line.product_id.id, self.prod_wire_heavy.id)
        self.assertAlmostEqual(wire_line.product_qty, 2.0 / 40.0, places=6)

    def test_top_roll_uses_exact_preview_paper_resolution_product(self):
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": 1.0,
            }
        )

        payload = {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": 1.0,
            "metrics": {"m2_per_box": 1.0, "kg_per_box": 0.0, "extra": {}},
            "rm_requirements": [],
            "quantity_stage_graph": {"nodes": {}, "edges": []},
            "paper_resolution": [
                {
                    "role": "top_roll",
                    "layer_no": 1,
                    "status": "exact",
                    "product_id": self.prod_top_roll.id,
                    "product_name": self.prod_top_roll.display_name,
                    "uom_id": self.prod_top_roll.uom_id.id,
                    "uom_name": self.prod_top_roll.uom_id.name,
                    "gsm": 180.0,
                },
                {
                    "role": "liner",
                    "layer_no": 1,
                    "status": "exact",
                    "product_id": self.prod_liner.id,
                    "product_name": self.prod_liner.display_name,
                    "uom_id": self.prod_liner.uom_id.id,
                    "uom_name": self.prod_liner.uom_id.name,
                    "gsm": 140.0,
                },
            ],
            "bom_struct": {
                "product_qty": 1.0,
                "components": [
                    {
                        "product": "rm_top_roll",
                        "qty": 0.088,
                        "uom": "kg",
                        "stage": "board",
                        "metadata": {
                            "kind": "top_roll",
                            "layer_no": 1,
                            "gsm": 180.0,
                            "required_deckle_cm": 79.0,
                        },
                    },
                ],
                "operations": [],
            },
            "trace": [],
        }
        wiz.raw_json = json.dumps(payload)
        wiz.preview_is_current = True

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())
        self.assertEqual(len(bom.bom_line_ids), 1)
        line = bom.bom_line_ids[0]
        self.assertEqual(line.product_id.id, self.prod_top_roll.id)
        self.assertAlmostEqual(line.product_qty, 0.088, places=6)

    def test_preview_component_product_id_overrides_conflicting_liner_resolution(self):
        payload = {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": 1.0,
            "metrics": {"m2_per_box": 1.0, "kg_per_box": 0.0, "extra": {}},
            "rm_requirements": [],
            "quantity_stage_graph": {"nodes": {}, "edges": []},
            "paper_resolution": [
                {
                    "role": "liner",
                    "layer_no": 1,
                    "status": "exact",
                    "product_id": self.prod_liner.id,
                    "product_name": self.prod_liner.display_name,
                    "uom_id": self.prod_liner.uom_id.id,
                    "uom_name": self.prod_liner.uom_id.name,
                    "gsm": 140.0,
                },
            ],
            "bom_struct": {
                "product_qty": 1.0,
                "components": [
                    {
                        "product": "rm_liner",
                        "qty": 0.088,
                        "uom": "kg",
                        "stage": "board",
                        "metadata": {
                            "kind": "top_roll",
                            "type": "liner",
                            "role": "liner",
                            "layer_no": 1,
                            "gsm": 180.0,
                            "product_id": self.prod_top_roll.id,
                            "uom_id": self.prod_top_roll.uom_id.id,
                            "resolution_status": "exact",
                            "writer_product_key": "product.product:%s" % self.prod_top_roll.id,
                        },
                    },
                ],
                "operations": [],
            },
            "trace": [],
        }

        bom = bom_adapter.create_bom_from_preview_payload(
            self.env,
            payload,
            self.pt.product_variant_id,
        )
        self.assertTrue(bom.exists())
        self.assertEqual(len(bom.bom_line_ids), 1)
        line = bom.bom_line_ids[0]
        self.assertEqual(line.product_id.id, self.prod_top_roll.id)
        self.assertAlmostEqual(line.product_qty, 0.088, places=6)

    def test_missing_mapping_blocks_creation(self):
        wiz = self._run_preview(order_qty=5.0)

        self.map_liner.active = False

        with self.assertRaises(UserError):
            wiz.action_create_bom_from_preview()

    def test_split_job_preview_creation(self):
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": 100.0,
            }
        )

        payload = {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": 100.0,
            "metrics": {
                "m2_per_box": 1.0,
                "kg_per_box": 0.0,
                "extra": {
                    "pieces_per_box": 2,
                    "layout_blanks_per_sheet": 4,
                    "boxes_per_sheet": 2,
                },
            },
            "rm_requirements": [],
            "quantity_stage_graph": {"nodes": {}, "edges": []},
            "bom_struct": {
                "product_qty": 100.0,
                "components": [
                    {
                        "product": "rm_liner",
                        "qty": 20.0,
                        "uom": "kg",
                        "stage": "corrugator",
                        "metadata": {"consumption_type": "area"},
                    },
                    {
                        "product": "rm_medium",
                        "qty": 15.0,
                        "uom": "kg",
                        "stage": "corrugator",
                        "metadata": {"consumption_type": "area"},
                    },
                ],
                "operations": [
                    {"stage": "corrugator", "input_qty": 35.0, "output_qty": 35.0},
                    {"stage": "diecut", "input_qty": 35.0, "output_qty": 100.0},
                ],
            },
            "trace": [],
        }
        wiz.raw_json = json.dumps(payload)
        wiz.preview_is_current = True

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())
        # Base quantity remains 1 FG even for split scenarios.
        self.assertEqual(bom.product_qty, 1.0)

    def test_repeated_previews_do_not_change_base_bom(self):
        # First preview/order.
        wiz1 = self._run_preview(order_qty=10.0)
        action_bom1 = wiz1.action_create_bom_from_preview()
        bom1 = self.env["mrp.bom"].browse(action_bom1.get("res_id"))
        self.assertTrue(bom1.exists())
        self.assertEqual(bom1.product_qty, 1.0)
        liner_line_1 = bom1.bom_line_ids.filtered(lambda l: l.corr_role == "liner")[:1]
        qty_liner_1 = liner_line_1.product_qty if liner_line_1 else 0.0

        # Second preview with a different order quantity should reuse
        # the same BoM identity and keep per-FG quantities unchanged.
        wiz2 = self._run_preview(order_qty=25.0)
        action_bom2 = wiz2.action_create_bom_from_preview()
        bom2 = self.env["mrp.bom"].browse(action_bom2.get("res_id"))
        self.assertTrue(bom2.exists())
        self.assertEqual(bom1.id, bom2.id)
        self.assertEqual(bom2.product_qty, 1.0)
        liner_line_2 = bom2.bom_line_ids.filtered(lambda l: l.corr_role == "liner")[:1]
        qty_liner_2 = liner_line_2.product_qty if liner_line_2 else 0.0
        self.assertAlmostEqual(qty_liner_1, qty_liner_2, places=6)

    def test_different_plants_create_distinct_boms(self):
        # First plant preview/Bom.
        wiz1 = self._run_preview(order_qty=10.0)
        action_bom1 = wiz1.action_create_bom_from_preview()
        bom1 = self.env["mrp.bom"].browse(action_bom1.get("res_id"))
        self.assertTrue(bom1.exists())

        # Second plant with different code -> different Engine V2 context.
        plant2 = self.env["corr.plant.param"].create(
            {
                "code": "ALT_PREVIEW",
                "setup_waste_m2": 0.0,
                "process_waste_pct": 3.0,
                "default_glue_lap_mm": 25.0,
                "flap_basis": "fixed",
                "flap_fixed_mm": 25.0,
                "ink_gm2_at_100pct_default": 6.0,
                "lam_overlap_percent_default": 6.0,
                "default_reel_widths_mm": [1300, 1500],
                "humidity_factor": 1.1,
                "rounding_rules_json": {"kg": 3, "m": 3, "m2": 4},
                "gsm_tolerance_pct": 5.0,
                "default_ply_max": 3,
                "sheet_length_mm": 2000.0,
                "sheet_width_mm": 2000.0,
            }
        )
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz2 = Wizard.create(
            {
                "spec_id": self.spec.id,
                "plant_id": plant2.id,
                "order_qty": 10.0,
            }
        )
        wiz2.action_run_debug()
        # Drop preview-only paper resolution: this test exercises the
        # signature/plant behaviour, not paper variant matching.
        if wiz2.raw_json:
            payload2 = json.loads(wiz2.raw_json or "{}")
            payload2.pop("paper_resolution", None)
            wiz2.raw_json = json.dumps(payload2)
            wiz2.preview_is_current = True
        action_bom2 = wiz2.action_create_bom_from_preview()
        bom2 = self.env["mrp.bom"].browse(action_bom2.get("res_id"))
        self.assertTrue(bom2.exists())

        # Different plant/profile context -> different Engine V2 signature
        # and a distinct BoM record.
        self.assertNotEqual(bom1.id, bom2.id)
        self.assertNotEqual(bom1.corr_engine_v2_signature, bom2.corr_engine_v2_signature)

    # ------------------------------------------------------------------
    # Consumable aggregation (adhesive) tests
    # ------------------------------------------------------------------

    def _make_adhesive_payload(self, wiz, order_qty, components):
        """Helper: override preview payload components for adhesive tests."""
        payload = json.loads(wiz.raw_json or "{}")
        payload["bom_struct"]["components"] = components
        payload["order_qty"] = order_qty
        wiz.raw_json = json.dumps(payload)

    def _make_top_sheet_payload(self, wiz, order_qty, component):
        """Helper: override preview payload with a single top-sheet component."""
        payload = json.loads(wiz.raw_json or "{}")
        payload["bom_struct"]["components"] = [component]
        payload["order_qty"] = order_qty
        wiz.raw_json = json.dumps(payload)

    def test_adhesive_aggregation_same_product(self):
        """Three adhesive usages -> one BoM line when product/UoM match."""
        order_qty = 10.0
        wiz = self._run_preview(order_qty=order_qty)

        components = [
            # Non-adhesive component to ensure it is unaffected.
            {
                "product": "rm_liner",
                "qty": 5.0,
                "uom": "kg",
                "stage": "corrugator",
                "metadata": {"consumption_type": "area"},
            },
            # Adhesive consumables: corrugation / board / fg.
            {
                "product": "rm_adhesive_corrugation",
                "qty": 2.0,
                "uom": "kg",
                "stage": "corrugator",
                "metadata": {"consumption_type": "area", "kind": "adhesive_corrugation"},
            },
            {
                "product": "rm_adhesive_board",
                "qty": 3.0,
                "uom": "kg",
                "stage": "board",
                "metadata": {"consumption_type": "area", "kind": "adhesive_board"},
            },
            {
                "product": "rm_adhesive_fg",
                "qty": 5.0,
                "uom": "kg",
                "stage": "fg",
                "metadata": {"consumption_type": "area", "kind": "adhesive_fg"},
            },
        ]
        self._make_adhesive_payload(wiz, order_qty, components)

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        # Liner component remains present and per-FG quantity is preserved.
        liner_line = bom.bom_line_ids.filtered(lambda l: l.corr_role == "liner")[:1]
        self.assertTrue(liner_line)
        self.assertAlmostEqual(liner_line.product_qty, 5.0 / order_qty, places=6)

        # All three adhesive usages map to the same product / UoM and
        # must be aggregated into a single BoM line.
        adh_lines = bom.bom_line_ids.filtered(lambda l: l.corr_role == "adhesive")
        self.assertEqual(len(adh_lines), 1)
        # Per-FG quantity equals the sum of the three per-FG usages.
        expected_adh_per_fg = (2.0 + 3.0 + 5.0) / order_qty
        self.assertAlmostEqual(adh_lines.product_qty, expected_adh_per_fg, places=6)

    def test_adhesive_not_aggregated_when_products_differ(self):
        """Adhesive usages that map to different products stay separate."""
        order_qty = 10.0
        wiz = self._run_preview(order_qty=order_qty)

        # Redirect board + fg adhesive to a different product so they
        # must not be aggregated with corrugation adhesive.
        self.map_adh_board.product_id = self.prod_adh_2
        self.map_adh_fg.product_id = self.prod_adh_2

        components = [
            {
                "product": "rm_adhesive_corrugation",
                "qty": 2.0,
                "uom": "kg",
                "stage": "corrugator",
                "metadata": {"consumption_type": "area", "kind": "adhesive_corrugation"},
            },
            {
                "product": "rm_adhesive_board",
                "qty": 3.0,
                "uom": "kg",
                "stage": "board",
                "metadata": {"consumption_type": "area", "kind": "adhesive_board"},
            },
            {
                "product": "rm_adhesive_fg",
                "qty": 5.0,
                "uom": "kg",
                "stage": "fg",
                "metadata": {"consumption_type": "area", "kind": "adhesive_fg"},
            },
        ]
        self._make_adhesive_payload(wiz, order_qty, components)

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        adh_lines = bom.bom_line_ids.filtered(lambda l: l.corr_role == "adhesive")
        # Two products -> two adhesive lines.
        self.assertEqual(len(adh_lines), 2)
        by_product = {line.product_id.id: line for line in adh_lines}
        self.assertIn(self.prod_adh_1.id, by_product)
        self.assertIn(self.prod_adh_2.id, by_product)

        # Quantities per product must reflect only the components that
        # mapped to that product.
        qty_prod1 = by_product[self.prod_adh_1.id].product_qty
        qty_prod2 = by_product[self.prod_adh_2.id].product_qty
        self.assertAlmostEqual(qty_prod1, 2.0 / order_qty, places=6)
        self.assertAlmostEqual(qty_prod2, (3.0 + 5.0) / order_qty, places=6)

    def test_non_adhesive_components_unchanged_by_aggregation(self):
        """Aggregation must not alter non-adhesive components."""
        order_qty = 20.0
        wiz = self._run_preview(order_qty=order_qty)

        components = [
            {
                "product": "rm_liner",
                "qty": 8.0,
                "uom": "kg",
                "stage": "corrugator",
                "metadata": {"consumption_type": "area"},
            },
            {
                "product": "rm_medium",
                "qty": 4.0,
                "uom": "kg",
                "stage": "corrugator",
                "metadata": {"consumption_type": "area"},
            },
            {
                "product": "rm_adhesive_corrugation",
                "qty": 2.0,
                "uom": "kg",
                "stage": "corrugator",
                "metadata": {"consumption_type": "area", "kind": "adhesive_corrugation"},
            },
        ]
        self._make_adhesive_payload(wiz, order_qty, components)

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        # Liner and medium roles should be present with unmodified
        # per-FG quantities.
        liner_line = bom.bom_line_ids.filtered(lambda l: l.corr_role == "liner")[:1]
        medium_line = bom.bom_line_ids.filtered(lambda l: l.corr_role == "medium")[:1]
        self.assertTrue(liner_line)
        self.assertTrue(medium_line)
        self.assertAlmostEqual(liner_line.product_qty, 8.0 / order_qty, places=6)
        self.assertAlmostEqual(medium_line.product_qty, 4.0 / order_qty, places=6)

        # Single adhesive component -> single adhesive line (no change,
        # but it still flows through the aggregation helper).
        adh_lines = bom.bom_line_ids.filtered(lambda l: l.corr_role == "adhesive")
        self.assertEqual(len(adh_lines), 1)
        self.assertAlmostEqual(adh_lines.product_qty, 2.0 / order_qty, places=6)

    def test_adhesive_not_aggregated_when_uom_differs(self):
        """Same adhesive product but different component UoMs must not merge."""
        order_qty = 10.0
        wiz = self._run_preview(order_qty=order_qty)

        # Board adhesive uses a different component UoM; aggregation key
        # includes product_uom_id so these lines must remain separate.
        self.map_adh_board.uom_id = self.uom_adh_small

        components = [
            {
                "product": "rm_adhesive_corrugation",
                "qty": 2.0,
                "uom": "kg",
                "stage": "corrugator",
                "metadata": {"consumption_type": "area", "kind": "adhesive_corrugation"},
            },
            {
                "product": "rm_adhesive_board",
                "qty": 3.0,
                "uom": "Gram",
                "stage": "board",
                "metadata": {"consumption_type": "area", "kind": "adhesive_board"},
            },
        ]
        self._make_adhesive_payload(wiz, order_qty, components)

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        adh_lines = bom.bom_line_ids.filtered(lambda l: l.corr_role == "adhesive")
        # Different component UoMs -> two lines despite shared product.
        self.assertEqual(len(adh_lines), 2)

    def test_unit_sheet_with_selected_product_uses_product_first(self):
        """Unit-sheet top sheet should use the selected product, not mapping."""
        order_qty = 10.0
        wiz = self._run_preview(order_qty=order_qty)

        components = {
            "product": "rm_top_sheet_external",
            "qty": 5.0,
            "uom": "kg",
            "stage": "board",
            "metadata": {
                "consumption_type": "area",
                "kind": "top_sheet_external",
                "top_sheet_mode": "unit_sheet",
                "top_sheet_product_id": self.prod_top_sheet_selected.id,
            },
        }
        self._make_top_sheet_payload(wiz, order_qty, components)

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        ts_lines = bom.bom_line_ids.filtered(lambda l: l.corr_role == "top_sheet")
        self.assertEqual(len(ts_lines), 1)
        ts_line = ts_lines[0]
        # Selected product must be used, not the generic mapping product.
        self.assertEqual(ts_line.product_id.id, self.prod_top_sheet_selected.id)

    def test_unit_sheet_without_selected_product_uses_mapping(self):
        """When no top_sheet_product_id is provided, fall back to mapping."""
        order_qty = 10.0
        wiz = self._run_preview(order_qty=order_qty)

        components = {
            "product": "rm_top_sheet_external",
            "qty": 5.0,
            "uom": "kg",
            "stage": "board",
            "metadata": {
                "consumption_type": "area",
                "kind": "top_sheet_external",
                "top_sheet_mode": "unit_sheet",
                # No top_sheet_product_id -> mapping must be used.
            },
        }
        self._make_top_sheet_payload(wiz, order_qty, components)

        action_bom = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())

        ts_lines = bom.bom_line_ids.filtered(lambda l: l.corr_role == "top_sheet")
        self.assertEqual(len(ts_lines), 1)
        ts_line = ts_lines[0]
        # Fallback uses the configured top_sheet mapping.
        self.assertEqual(ts_line.product_id.id, self.prod_top_sheet_mapped.id)
