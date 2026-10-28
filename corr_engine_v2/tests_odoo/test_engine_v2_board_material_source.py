from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import ValidationError


@tagged("corr_mrp_bridge")
class TestEngineV2BoardMaterialSource(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.attr_gsm = env["product.attribute"].create({"name": "GSM"})
        cls.attr_bf = env["product.attribute"].create({"name": "BF"})
        cls.attr_color = env["product.attribute"].create({"name": "Color"})
        cls.gsm_180 = env["product.attribute.value"].create({"name": "180", "attribute_id": cls.attr_gsm.id})
        cls.bf_18 = env["product.attribute.value"].create({"name": "18", "attribute_id": cls.attr_bf.id})
        cls.color_ns = env["product.attribute.value"].create({"name": "Natural Shade", "attribute_id": cls.attr_color.id})
        cls.uom = env.ref("uom.product_uom_kgm", raise_if_not_found=False) or env["uom.uom"].search([], limit=1)
        cls.kraft = env["product.template"].create(
            {
                "name": "Kraft Paper",
                "product_role": "raw_material",
                "uom_id": cls.uom.id,
                "uom_po_id": cls.uom.id,
            }
        )
        Line = env["product.template.attribute.line"]
        Line.create({"product_tmpl_id": cls.kraft.id, "attribute_id": cls.attr_gsm.id, "value_ids": [(6, 0, [cls.gsm_180.id])]})
        Line.create({"product_tmpl_id": cls.kraft.id, "attribute_id": cls.attr_bf.id, "value_ids": [(6, 0, [cls.bf_18.id])]})
        Line.create({"product_tmpl_id": cls.kraft.id, "attribute_id": cls.attr_color.id, "value_ids": [(6, 0, [cls.color_ns.id])]})
        icp = env["ir.config_parameter"].sudo()
        icp.set_param("corr_engine_v2.allowed_paper_material_template_ids", str(cls.kraft.id))
        icp.set_param("corr_engine_v2.attr_gsm", "GSM")
        icp.set_param("corr_engine_v2.attr_bf", "BF")
        icp.set_param("corr_engine_v2.attr_color", "Color")

    def test_attribute_values_update_spec_snapshots(self):
        line = self.env["corr.engine.v2.spec.layer"].create(
            {
                "spec_id": self.env["corr.engine.v2.spec"].create({"name": "Material Source Spec"}).id,
                "sequence": 10,
                "role": "outer_liner",
                "material_template_id": self.kraft.id,
                "gsm_value_id": self.gsm_180.id,
                "bf_value_id": self.bf_18.id,
                "color_value_id": self.color_ns.id,
            }
        )

        self.assertEqual(line.material_template_id, self.kraft)
        self.assertEqual(line.gsm, 180.0)
        self.assertEqual(line.bf, 18.0)
        self.assertEqual(line.color, "Natural Shade")

    def test_board_recipe_copies_material_source_to_spec(self):
        recipe = self.env["corr.engine.board.recipe"].create(
            {
                "name": "Kraft Stack",
                "code": "KRAFT",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": 10,
                            "role": "outer_liner",
                            "material_template_id": self.kraft.id,
                            "gsm_value_id": self.gsm_180.id,
                            "bf_value_id": self.bf_18.id,
                            "color_value_id": self.color_ns.id,
                        },
                    )
                ],
            }
        )
        spec = self.env["corr.engine.v2.spec"].create(
            {
                "name": "Recipe Material Source Spec",
                "board_recipe_id": recipe.id,
            }
        )

        self.assertFalse(spec.is_board_customized)
        self.assertEqual(len(spec.stack_line_ids), 1)
        line = spec.stack_line_ids[0]
        self.assertEqual(line.source_type, "recipe")
        self.assertEqual(line.material_template_id, self.kraft)
        self.assertEqual(line.gsm_value_id, self.gsm_180)
        self.assertEqual(line.bf_value_id, self.bf_18)
        self.assertEqual(line.color_value_id, self.color_ns)
        self.assertEqual(line.gsm, 180.0)
        self.assertEqual(line.bf, 18.0)
        self.assertEqual(line.color, "Natural Shade")

    def test_board_recipe_snapshot_preserves_sequence_order(self):
        recipe = self.env["corr.engine.board.recipe"].create(
            {
                "name": "Ordered Stack",
                "code": "ORDERED",
                "line_ids": [
                    (0, 0, {"sequence": 20, "role": "inner_liner", "material_template_id": self.kraft.id, "gsm_value_id": self.gsm_180.id, "bf_value_id": self.bf_18.id, "color_value_id": self.color_ns.id}),
                    (0, 0, {"sequence": 10, "role": "medium", "material_template_id": self.kraft.id, "gsm_value_id": self.gsm_180.id, "bf_value_id": self.bf_18.id, "color_value_id": self.color_ns.id}),
                ],
            }
        )
        spec = self.env["corr.engine.v2.spec"].create({"name": "Ordered Spec", "board_recipe_id": recipe.id})

        lines = spec.stack_line_ids.sorted(lambda line: (line.sequence or 0, line.id or 0))
        self.assertEqual([line.sequence for line in lines], [10, 20])
        self.assertEqual([line.role for line in lines], ["medium", "inner_liner"])

    def test_board_recipe_snapshot_derives_attrs_from_material_variant(self):
        variant = self.kraft.product_variant_id
        recipe = self.env["corr.engine.board.recipe"].create(
            {
                "name": "Variant Stack",
                "code": "VARIANT",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": 10,
                            "role": "outer_liner",
                            "material_template_id": self.kraft.id,
                            "material_product_id": variant.id,
                            "gsm": 0.0,
                            "bf": 0.0,
                            "color": False,
                            "gsm_value_id": False,
                            "bf_value_id": False,
                            "color_value_id": False,
                        },
                    )
                ],
            }
        )

        spec = self.env["corr.engine.v2.spec"].create({"name": "Variant Derived Spec", "board_recipe_id": recipe.id})
        line = spec.stack_line_ids[:1]
        self.assertEqual(line.material_product_id, variant)
        self.assertEqual(line.material_template_id, self.kraft)
        self.assertEqual(line.gsm_value_id, self.gsm_180)
        self.assertEqual(line.bf_value_id, self.bf_18)
        self.assertEqual(line.color_value_id, self.color_ns)
        self.assertEqual(line.gsm, 180.0)
        self.assertEqual(line.bf, 18.0)
        self.assertEqual(line.color, "Natural Shade")

    def test_board_recipe_snapshot_derives_attr_ids_from_numeric_recipe_fields(self):
        recipe = self.env["corr.engine.board.recipe"].create(
            {
                "name": "Numeric Stack",
                "code": "NUMERIC",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": 10,
                            "role": "outer_liner",
                            "material_template_id": self.kraft.id,
                            "gsm": 180.0,
                            "bf": 18.0,
                            "color": "Natural Shade",
                            "gsm_value_id": False,
                            "bf_value_id": False,
                            "color_value_id": False,
                        },
                    )
                ],
            }
        )

        spec = self.env["corr.engine.v2.spec"].create({"name": "Numeric Derived Spec", "board_recipe_id": recipe.id})
        line = spec.stack_line_ids[:1]
        self.assertEqual(line.material_template_id, self.kraft)
        self.assertEqual(line.gsm, 180.0)
        self.assertEqual(line.bf, 18.0)
        self.assertEqual(line.color, "Natural Shade")
        self.assertEqual(line.gsm_value_id, self.gsm_180)
        self.assertEqual(line.bf_value_id, self.bf_18)
        self.assertEqual(line.color_value_id, self.color_ns)

    def test_board_recipe_snapshot_matches_color_code_to_color_attribute(self):
        recipe = self.env["corr.engine.board.recipe"].create(
            {
                "name": "Color Code Stack",
                "code": "COLORCODE",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": 10,
                            "role": "outer_liner",
                            "material_template_id": self.kraft.id,
                            "gsm": 180.0,
                            "bf": 18.0,
                            "color": "NS",
                            "gsm_value_id": False,
                            "bf_value_id": False,
                            "color_value_id": False,
                        },
                    )
                ],
            }
        )

        spec = self.env["corr.engine.v2.spec"].create({"name": "Color Code Spec", "board_recipe_id": recipe.id})
        line = spec.stack_line_ids[:1]
        self.assertEqual(line.color_value_id, self.color_ns)
        self.assertTrue(line.color)

    def test_board_recipe_snapshot_handles_empty_optional_many2one_fields_safely(self):
        recipe = self.env["corr.engine.board.recipe"].create(
            {
                "name": "Safe Optional Stack",
                "code": "SAFEOPT",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": 10,
                            "role": "outer_liner",
                            "material_template_id": self.kraft.id,
                            "material_product_id": False,
                            "flute_type_id": False,
                            "gsm": 180.0,
                            "bf": 18.0,
                            "color": "Natural Shade",
                            "gsm_value_id": False,
                            "bf_value_id": False,
                            "color_value_id": False,
                        },
                    )
                ],
            }
        )

        spec = self.env["corr.engine.v2.spec"].create({"name": "Safe Optional Spec", "board_recipe_id": recipe.id})
        line = spec.stack_line_ids[:1]
        self.assertEqual(line.material_template_id, self.kraft)
        self.assertFalse(line.material_product_id)
        self.assertFalse(line.flute_type_id)
        self.assertEqual(line.gsm, 180.0)
        self.assertEqual(line.bf, 18.0)
        self.assertEqual(line.color, "Natural Shade")

    def test_paper_resolver_honors_material_template_id(self):
        other_template = self.env["product.template"].create(
            {
                "name": "Other Kraft Paper",
                "default_code": "OTHER-KRAFT",
                "product_role": "raw_material",
                "uom_id": self.uom.id,
                "uom_po_id": self.uom.id,
            }
        )
        Line = self.env["product.template.attribute.line"]
        Line.create({"product_tmpl_id": other_template.id, "attribute_id": self.attr_gsm.id, "value_ids": [(6, 0, [self.gsm_180.id])]})
        Line.create({"product_tmpl_id": other_template.id, "attribute_id": self.attr_bf.id, "value_ids": [(6, 0, [self.bf_18.id])]})
        Line.create({"product_tmpl_id": other_template.id, "attribute_id": self.attr_color.id, "value_ids": [(6, 0, [self.color_ns.id])]})
        other_variant = other_template.product_variant_id

        result = self.env["corr.material.product.map"].resolve_engine_paper_variant_v1(
            role="liner",
            gsm=180.0,
            bf=18.0,
            color="Natural Shade",
            material_template_id=self.kraft.id,
            exact_resolution_only=True,
        )

        self.assertEqual(result.get("status"), "exact")
        self.assertEqual(result.get("product"), self.kraft.product_variant_id)
        self.assertNotEqual(result.get("product"), other_variant)

    def test_spec_layer_create_shows_friendly_missing_role_message(self):
        spec = self.env["corr.engine.v2.spec"].create({"name": "Friendly Role Validation"})

        with self.assertRaises(ValidationError) as err:
            self.env["corr.engine.v2.spec.layer"].create(
                {
                    "spec_id": spec.id,
                    "sequence": 10,
                    "gsm_value_id": self.gsm_180.id,
                    "bf_value_id": self.bf_18.id,
                    "color_value_id": self.color_ns.id,
                }
            )

        message = str(err.exception)
        self.assertIn("Board Structure is incomplete.", message)
        self.assertIn("Board Structure line 1", message)
        self.assertIn("Role", message)

    def test_spec_layer_create_shows_friendly_missing_flute_message_for_medium(self):
        spec = self.env["corr.engine.v2.spec"].create({"name": "Friendly Flute Validation"})

        with self.assertRaises(ValidationError) as err:
            self.env["corr.engine.v2.spec.layer"].create(
                {
                    "spec_id": spec.id,
                    "sequence": 10,
                    "role": "medium",
                    "material_template_id": self.kraft.id,
                    "gsm_value_id": self.gsm_180.id,
                    "bf_value_id": self.bf_18.id,
                    "color_value_id": self.color_ns.id,
                }
            )

        message = str(err.exception)
        self.assertIn("Board Structure line 1 (Medium / Kraft Paper)", message)
        self.assertIn("Flute Type", message)

    def test_resolved_product_uses_selected_material_product(self):
        variant = self.kraft.product_variant_id
        spec = self.env["corr.engine.v2.spec"].create({"name": "Resolved Variant Spec"})
        line = self.env["corr.engine.v2.spec.layer"].create(
            {
                "spec_id": spec.id,
                "sequence": 10,
                "role": "outer_liner",
                "material_template_id": self.kraft.id,
                "material_product_id": variant.id,
                "gsm": 180.0,
                "bf": 18.0,
                "color": "Natural Shade",
            }
        )
        self.assertEqual(line.resolved_product_id, variant)

    def test_board_recipe_snapshot_raises_on_incomplete_material_recipe_line(self):
        recipe = self.env["corr.engine.board.recipe"].create(
            {
                "name": "Broken Stack",
                "code": "BROKEN",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": 10,
                            "role": "outer_liner",
                            "material_template_id": self.kraft.id,
                            "gsm": 0.0,
                            "bf": 0.0,
                            "color": False,
                            "gsm_value_id": False,
                            "bf_value_id": False,
                            "color_value_id": False,
                        },
                    )
                ],
            }
        )

        with self.assertRaises(ValidationError):
            self.env["corr.engine.v2.spec"].create({"name": "Broken Recipe Spec", "board_recipe_id": recipe.id})
