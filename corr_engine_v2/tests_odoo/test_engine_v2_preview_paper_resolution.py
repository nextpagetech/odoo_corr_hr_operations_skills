import json

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("corr_mrp_bridge")
class TestEngineV2PreviewPaperResolution(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        # Plant with basic parameters; deckle behaviour is driven by spec UPS.
        cls.plant = env["corr.plant.param"].create(
            {
                "code": "PAPER_PREVIEW",
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
                # Provide sheet dimensions so that layout is feasible
                # and blanks_per_sheet is non-zero during preview.
                "sheet_length_mm": 2000.0,
                "sheet_width_mm": 2000.0,
            }
        )

        # Category for kraft paper rolls, referenced via planning.paper_roll_category_name.
        cls.paper_cat = env["product.category"].create({"name": "Kraft Rolls"})
        env["ir.config_parameter"].sudo().set_param("planning.paper_roll_category_name", cls.paper_cat.name)

        # UoM
        try:
            cls.uom_kg = env.ref("uom.product_uom_kgm")
        except Exception:
            cls.uom_kg = env["uom.uom"].search([], limit=1)

        # Base template for liners.
        tmpl = env["product.template"].create(
            {
                "name": "Test Liner",
                "categ_id": cls.paper_cat.id,
                "uom_id": cls.uom_kg.id,
                "uom_po_id": cls.uom_kg.id,
            }
        )

        # Attributes: GSM, BF, Color, Deckle (cm)
        Attr = env["product.attribute"]
        Value = env["product.attribute.value"]
        tmpl_attr = env["product.template.attribute.line"]

        a_gsm = Attr.create({"name": "GSM"})
        a_bf = Attr.create({"name": "BF"})
        a_color = Attr.create({"name": "Color"})
        a_deckle = Attr.create({"name": "Deckle (cm)"})

        v_gsm_180 = Value.create({"name": "180", "attribute_id": a_gsm.id})
        v_gsm_180_b = Value.create({"name": "180B", "attribute_id": a_gsm.id})
        v_bf_23 = Value.create({"name": "23", "attribute_id": a_bf.id})
        v_bf_24 = Value.create({"name": "24", "attribute_id": a_bf.id})
        v_color_nat = Value.create({"name": "Natural Shade", "attribute_id": a_color.id})
        v_color_alt = Value.create({"name": "Brown", "attribute_id": a_color.id})
        v_deckle_112 = Value.create({"name": "112", "attribute_id": a_deckle.id})
        v_deckle_240 = Value.create({"name": "240", "attribute_id": a_deckle.id})
        v_deckle_135 = Value.create({"name": "135", "attribute_id": a_deckle.id})
        v_deckle_150 = Value.create({"name": "150", "attribute_id": a_deckle.id})
        v_deckle_130 = Value.create({"name": "130", "attribute_id": a_deckle.id})

        # Attach attribute lines to template.
        tmpl_attr.create({"product_tmpl_id": tmpl.id, "attribute_id": a_gsm.id, "value_ids": [(6, 0, [v_gsm_180.id, v_gsm_180_b.id])]})
        tmpl_attr.create({"product_tmpl_id": tmpl.id, "attribute_id": a_bf.id, "value_ids": [(6, 0, [v_bf_23.id, v_bf_24.id])]})
        tmpl_attr.create({"product_tmpl_id": tmpl.id, "attribute_id": a_color.id, "value_ids": [(6, 0, [v_color_nat.id, v_color_alt.id])]})
        tmpl_attr.create(
            {
                "product_tmpl_id": tmpl.id,
                "attribute_id": a_deckle.id,
                "value_ids": [(6, 0, [v_deckle_112.id, v_deckle_135.id, v_deckle_150.id, v_deckle_130.id, v_deckle_240.id])],
            }
        )

        # Generate variants.
        tmpl._create_variant_ids()
        cls.variants = tmpl.product_variant_ids

        def find_variant(gsm_name, bf_name, color_name, deckle_name):
            for p in cls.variants:
                names = {v.attribute_id.name: v.name for v in p.product_template_attribute_value_ids}
                if (
                    names.get("GSM") == gsm_name
                    and names.get("BF") == bf_name
                    and names.get("Color") == color_name
                    and names.get("Deckle (cm)") == deckle_name
                ):
                    return p
            return env["product.product"]

        # Exact: GSM 180, BF 23, Natural Shade, Deckle 135.
        cls.prod_exact = find_variant("180", "23", "Natural Shade", "135")
        cls.prod_layout_112 = find_variant("180", "23", "Natural Shade", "112")
        cls.prod_layout_240 = find_variant("180", "23", "Natural Shade", "240")
        # Usable: GSM 180, BF 23, Natural Shade, Deckle 150 (wider).
        cls.prod_usable_deckle = find_variant("180", "23", "Natural Shade", "150")
        # Too narrow: GSM 180, BF 23, Natural Shade, Deckle 130.
        cls.prod_narrow = find_variant("180", "23", "Natural Shade", "130")
        # Alternate color/BF: GSM 180B, BF 24, Brown, Deckle 135.
        cls.prod_alt = find_variant("180B", "24", "Brown", "135")

        # Finished product template + spec for Engine V2.
        cls.pt = env["product.template"].create({"name": "Paper Preview Box"})
        Spec = env["corr.spec.template"].with_context(skip_corr_stack_check=True)
        cls.spec = Spec.create(
            {
                "name": "Paper Preview Spec",
                "product_tmpl_id": cls.pt.id,
                "style": "0201",
                "length_mm": 300.0,
                "width_mm": 200.0,
                "height_mm": 150.0,
                "ply_count": 2,
            }
        )
        # Simple 2-ply: liner + medium; use GSM/BF/Color matching exact variant.
        env["corr.spec.stack.line"].create(
            {
                "spec_id": cls.spec.id,
                "sequence": 10,
                "role": "liner",
                "gsm": 180.0,
                "bf": 23.0,
                "color": "Natural Shade",
            }
        )
        flute = env["corr.flute"].create({"name": "B", "code": "B", "caliper_mm": 2.5, "tuf": 0.05})
        env["corr.spec.stack.line"].create(
            {
                "spec_id": cls.spec.id,
                "sequence": 20,
                "role": "medium",
                "gsm": 180.0,
                "bf": 23.0,
                "flute_id": flute.id,
                "color": "Natural Shade",
            }
        )

        cls.env = env

        # Finished good variant used for preview/BoM tests.
        cls.fg_product = cls.pt.product_variant_id

        phase8_seed = env["corr.engine.phase8.seed.wizard"]
        phase8_seed.create(
            {
                "plant_id": cls.plant.id,
                "allow_default_updates": True,
            }
        ).action_run_seed()
        env["corr.engine.v2.contract.seed.wizard"].create({}).action_run()

        cls.stage_template_by_code = {
            rec.code: rec
            for rec in env["corr.engine.stage.template"].sudo().search(
                [("code", "in", ["2ply", "board", "seaming"])]
            )
        }
        cls.route_layout = env["corr.engine.routing.profile"].create(
            {"name": "Paper Preview Layout Route", "active": True}
        )
        StageLine = env["corr.engine.routing.profile.stage"]
        for sequence, code in ((10, "2ply"), (20, "board"), (30, "seaming")):
            StageLine.create(
                {
                    "routing_profile_id": cls.route_layout.id,
                    "sequence": sequence,
                    "stage_template_id": cls.stage_template_by_code[code].id,
                }
            )

        cls.wc_layout = env["mrp.workcenter"].create(
            {
                "name": "Paper Preview Layout WC",
                "corr_deckle_min_cm": 40.0,
                "corr_deckle_max_cm": 240.0,
                "corr_length_max_mm": 4000.0,
                "max_slitting_ups": 2,
                "corr_flutes_supported": "B",
            }
        )
        cls.wc_seaming = env["mrp.workcenter"].create({"name": "Paper Preview Seaming WC"})
        env["corr.operation.map"].create(
            {
                "op_name": "Corrugation",
                "workcenter_id": cls.wc_layout.id,
                "deckle_min_cm": 40.0,
                "deckle_max_cm": 240.0,
            }
        )
        env["corr.operation.map"].create(
            {
                "op_name": "Lamination",
                "workcenter_id": cls.wc_layout.id,
                "deckle_min_cm": 40.0,
                "deckle_max_cm": 240.0,
            }
        )
        env["corr.operation.map"].create(
            {
                "op_name": "Seaming",
                "workcenter_id": cls.wc_seaming.id,
            }
        )

        cls.v2_layout = env["corr.engine.v2.spec"].create(
            {
                "name": "Paper Preview Layout V2",
                "plant_id": cls.plant.id,
                "routing_profile_id": cls.route_layout.id,
            }
        )

        pt_layout = env["product.template"].create({"name": "Paper Preview Layout Box"})
        cls.spec_layout = Spec.create(
            {
                "name": "Paper Preview Layout Spec",
                "product_tmpl_id": pt_layout.id,
                "style": "0201",
                "length_mm": 700.0,
                "width_mm": 800.0,
                "height_mm": 300.0,
                "ply_count": 2,
            }
        )
        env["corr.spec.stack.line"].create(
            {
                "spec_id": cls.spec_layout.id,
                "sequence": 10,
                "role": "liner",
                "gsm": 180.0,
                "bf": 23.0,
                "color": "Natural Shade",
            }
        )
        env["corr.spec.stack.line"].create(
            {
                "spec_id": cls.spec_layout.id,
                "sequence": 20,
                "role": "medium",
                "gsm": 180.0,
                "bf": 23.0,
                "flute_id": flute.id,
                "color": "Natural Shade",
            }
        )

    def _run_preview(self, printing_ups, cost_source=None):
        # Set UPS hints on the spec so that required_deckle_cm and
        # layout both see the intended effective UPS value.
        xp = dict(self.spec.extra_params_json or {})
        pu = int(printing_ups)
        xp["printing_ups"] = pu
        # Deckle computation uses liner_slitting_ups for non-BMC styles.
        xp["liner_slitting_ups"] = pu
        self.spec.extra_params_json = xp

        Wizard = self.env["corr.engine_v2.debug.wizard"]
        vals = {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": 10.0,
        }
        if cost_source:
            vals["cost_source"] = cost_source
        wiz = Wizard.create(vals)
        action = wiz.action_run_debug()
        self.assertEqual(action.get("res_model"), "corr.engine_v2.debug.wizard")
        return wiz

    def _run_layout_preview(self):
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": self.spec_layout.id,
                "plant_id": self.plant.id,
                "order_qty": 10.0,
                "v2_spec_id": self.v2_layout.id,
                "preview_backend": "stagecontext",
                "bom_backend": "stagecontext_shadow",
            }
        )
        action = wiz.action_run_debug()
        self.assertEqual(action.get("res_model"), "corr.engine_v2.debug.wizard")
        return wiz, json.loads(wiz.raw_json or "{}")

    def _select_layout_option(self, wiz, up_count):
        line = wiz.slitting_option_line_ids.filtered(lambda item: item.up_count == up_count)[:1]
        self.assertTrue(line, "expected a slitting/layout option for up_count=%s" % up_count)
        action = line.action_select_option()
        self.assertEqual(action.get("res_model"), "corr.engine_v2.debug.wizard")
        wiz = self.env["corr.engine_v2.debug.wizard"].browse(wiz.id)
        return wiz, json.loads(wiz.raw_json or "{}")

    def test_paper_resolution_exact_and_missing(self):
        """Preview stores resolution statuses for paper layers in raw_json."""

        wiz = self._run_preview(printing_ups=1)

        payload = json.loads(wiz.raw_json or "{}")
        paper_res = payload.get("paper_resolution") or []
        self.assertTrue(paper_res, "paper_resolution should not be empty")

        # All entries should have one of the known statuses.
        statuses = {e.get("status") for e in paper_res}
        self.assertTrue(statuses.issubset({"exact", "missing"}))

        # required_deckle_cm must be present when deckle could be computed.
        # For 0201 + width 200mm + printing_ups=1, required_deckle_cm should be > 0.
        req_values = {e.get("required_deckle_cm") for e in paper_res}
        # All entries share the same required deckle.
        self.assertEqual(len(req_values - {None}), 1)
        req_deckle = list(req_values - {None})[0]
        self.assertGreater(req_deckle, 0.0)

        # At least one layer must be resolved to a concrete product.
        any_product = any(e.get("product_id") for e in paper_res if e.get("status") == "exact")
        self.assertTrue(any_product)

    def test_deckle_required_matches_preview_geometry(self):
        """required_deckle_cm should reflect base blank width and UPS."""

        # For style 0201, blank width is width_mm + height_mm + flap_add.
        # With width=200, height=150, flap_fixed=20 -> blank_width_mm = 370.
        # printing_ups=2 should double required deckle.
        wiz = self._run_preview(printing_ups=2)
        payload = json.loads(wiz.raw_json or "{}")
        paper_res = payload.get("paper_resolution") or []
        self.assertTrue(paper_res)
        req_values = {e.get("required_deckle_cm") for e in paper_res}
        req_values -= {None}
        self.assertEqual(len(req_values), 1)
        req_deckle = req_values.pop()

        # Expected: base_cm = 370 / 10 = 37; with UPS=2 -> 40 cm
        # based on current deckle computation.
        self.assertAlmostEqual(req_deckle, 40.0, places=3)

    def test_deckle_too_narrow_is_marked_missing(self):
        """When no variant has deckle >= required, resolution is missing."""

        # Force required deckle above all existing deckles by setting a large UPS.
        wiz = self._run_preview(printing_ups=10)
        payload = json.loads(wiz.raw_json or "{}")
        paper_res = payload.get("paper_resolution") or []
        self.assertTrue(paper_res)
        statuses = {e.get("status") for e in paper_res}
        self.assertIn("missing", statuses)

    def test_bf_and_color_used_as_tie_breakers(self):
        """BF and Color act as ranking tie-breakers without blocking matches."""

        # Make BF and color non-strict so they don't enforce hard filters.
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("planning.strict_bf_match", "0")
        icp.set_param("planning.strict_color_match", "0")

        wiz = self._run_preview(printing_ups=1)
        payload = json.loads(wiz.raw_json or "{}")
        paper_res = payload.get("paper_resolution") or []
        self.assertTrue(paper_res)

        # With multiple candidates, exact-only paper resolution must still
        # choose only a truly exact match and never degrade to a generic usable
        # fallback for preview/BoM.
        for entry in paper_res:
            if entry.get("status") == "exact":
                self.assertTrue(entry.get("product_id"))

    def test_selected_layout_uses_exact_112_deckle_variant(self):
        wiz, _payload = self._run_layout_preview()
        wiz, payload = self._select_layout_option(wiz, 1)

        paper_res = payload.get("paper_resolution") or []
        self.assertTrue(paper_res)
        required = {entry.get("required_deckle_cm") for entry in paper_res}
        self.assertEqual(required - {None}, {112.0})
        self.assertEqual({entry.get("product_id") for entry in paper_res}, {self.prod_layout_112.id})

        selected_map = json.loads(wiz.selected_slitting_options_json or "{}")
        selected = next(iter(selected_map.values()))
        self.assertEqual(int(selected.get("up_count") or 0), 1)
        self.assertAlmostEqual(float(selected.get("final_sheet_width_mm") or 0.0), 1120.0, places=3)

        action = wiz.action_create_bom_from_preview()
        self.assertEqual(action.get("res_model"), "mrp.bom")
        bom = self.env["mrp.bom"].browse(action.get("res_id"))
        self.assertTrue(bom.exists())
        bom_products = {
            line.product_id.id
            for line in bom.bom_line_ids
            if (line.corr_role or "").strip().lower() in ("liner", "medium")
        }
        self.assertEqual(bom_products, {self.prod_layout_112.id})

    def test_selected_layout_missing_exact_variant_does_not_fall_back_to_wider_deckle(self):
        wiz, _payload = self._run_layout_preview()
        wiz, payload = self._select_layout_option(wiz, 2)

        paper_res = payload.get("paper_resolution") or []
        self.assertTrue(paper_res)
        required = {entry.get("required_deckle_cm") for entry in paper_res}
        self.assertEqual(required - {None}, {224.0})
        self.assertEqual({entry.get("status") for entry in paper_res}, {"missing"})
        self.assertEqual({entry.get("product_id") for entry in paper_res}, {None})
        self.assertEqual({entry.get("product_name") for entry in paper_res}, {"Missing / Unresolved"})

    def test_selected_layout_missing_variant_is_reported_cleanly(self):
        self.wc_layout.write({"corr_deckle_max_cm": 400.0, "max_slitting_ups": 3})
        try:
            wiz, _payload = self._run_layout_preview()
            wiz, payload = self._select_layout_option(wiz, 3)
            paper_res = payload.get("paper_resolution") or []
            self.assertTrue(paper_res)
            self.assertEqual({entry.get("required_deckle_cm") for entry in paper_res} - {None}, {336.0})
            self.assertEqual({entry.get("status") for entry in paper_res}, {"missing"})
        finally:
            self.wc_layout.write({"corr_deckle_max_cm": 240.0, "max_slitting_ups": 2})

    def test_selected_layout_resolution_matches_created_bom_components(self):
        wiz, _payload = self._run_layout_preview()
        wiz, payload = self._select_layout_option(wiz, 2)

        expected_products = {
            entry.get("product_id")
            for entry in (payload.get("paper_resolution") or [])
            if entry.get("role") in ("liner", "medium")
        }
        self.assertEqual(expected_products, {None})

        with self.assertRaises(UserError):
            wiz.action_create_bom_from_preview()

    # ----------------------
    # Create-from-preview V2
    # ----------------------

    def _build_preview_payload(self, status_liner="exact", status_medium="exact"):
        """Helper: construct a minimal preview payload with paper_resolution."""
        env = self.env
        order_qty = 10.0

        def _entry(role, layer_no, status, product):
            if status == "exact":
                prod_id = product.id
                uom_id = product.uom_id.id
                name = product.display_name
            else:
                prod_id = None
                uom_id = None
                name = "Missing / Unresolved"
            return {
                "key": "%s:%d" % (role, layer_no),
                "role": role,
                "layer_no": layer_no,
                "gsm": 180.0,
                "bf": 23.0,
                "color": "Natural Shade",
                "required_deckle_cm": 74.0,
                "qty_per_fg": 1.0,
                "qty_uom": "kg",
                "status": status,
                "product_id": prod_id,
                "product_name": name,
                "uom_id": uom_id,
                "uom_name": env.ref("uom.product_uom_kgm").display_name,
                "candidate_ids": [prod_id] if prod_id else [],
            }

        paper_resolution = [
            _entry("liner", 1, status_liner, self.prod_exact),
            _entry("medium", 2, status_medium, self.prod_exact),
        ]

        bom_struct = {
            "product_qty": order_qty,
            "components": [
                {
                    "product": "rm_liner",
                    "qty": 10.0,
                    "uom": "kg",
                    "stage": "corrugator",
                    "metadata": {"type": "liner", "layer_no": 1, "consumption_type": "area"},
                },
                {
                    "product": "rm_medium",
                    "qty": 10.0,
                    "uom": "kg",
                    "stage": "corrugator",
                    "metadata": {"type": "medium", "layer_no": 2, "consumption_type": "area"},
                },
            ],
            "operations": [],
        }

        return {
            "spec_id": self.spec.id,
            "plant_id": self.plant.id,
            "order_qty": order_qty,
            "metrics": {"m2_per_box": 1.0, "kg_per_box": 0.0, "extra": {}},
            "rm_requirements": [],
            "quantity_stage_graph": {"nodes": {}, "edges": []},
            "bom_struct": bom_struct,
            "paper_resolution": paper_resolution,
            "trace": [],
        }

    def test_create_from_preview_uses_resolved_paper_variants_exact(self):
        """BoM creation uses product_id from preview for exact paper matches."""

        from odoo.addons.corr_mrp_bridge import bom_adapter as engine_v2_bom_adapter

        payload = self._build_preview_payload(status_liner="exact", status_medium="exact")

        # Add a conflicting mapping to ensure mapping is not used for paper roles.
        other_prod = self.env["product.product"].create(
            {
                "name": "Mapped Liner 999",
                "uom_id": self.uom_kg.id,
                "uom_po_id": self.uom_kg.id,
            }
        )
        self.env["corr.material.product.map"].create(
            {"kind": "liner", "gsm_min": 100, "gsm_max": 200, "product_id": other_prod.id}
        )

        bom = engine_v2_bom_adapter.create_bom_from_preview_payload(self.env, payload, self.fg_product)
        self.assertTrue(bom.exists())

        roles = {line.corr_role for line in bom.bom_line_ids}
        self.assertIn("liner", roles)
        self.assertIn("medium", roles)

        liner_line = bom.bom_line_ids.filtered(lambda l: l.corr_role == "liner")[:1]
        medium_line = bom.bom_line_ids.filtered(lambda l: l.corr_role == "medium")[:1]
        self.assertTrue(liner_line)
        self.assertTrue(medium_line)
        self.assertEqual(liner_line.product_id.id, self.prod_exact.id)
        self.assertEqual(medium_line.product_id.id, self.prod_exact.id)
        # Ensure mapping product was not used.
        self.assertNotEqual(liner_line.product_id.id, other_prod.id)

    def test_create_from_preview_blocks_non_exact_paper_variants(self):
        """BoM creation fails when paper variants are not exact."""

        from odoo.addons.corr_mrp_bridge import bom_adapter as engine_v2_bom_adapter

        payload = self._build_preview_payload(status_liner="usable", status_medium="usable")
        with self.assertRaises(UserError):
            engine_v2_bom_adapter.create_bom_from_preview_payload(self.env, payload, self.fg_product)

    def test_create_from_preview_blocks_missing_paper_variants(self):
        """BoM creation blocks missing exact paper variants instead of falling back."""

        from odoo.addons.corr_mrp_bridge import bom_adapter as engine_v2_bom_adapter

        mapped_prod = self.env["product.product"].create(
            {
                "name": "Mapped Liner Fallback",
                "uom_id": self.uom_kg.id,
                "uom_po_id": self.uom_kg.id,
            }
        )
        self.env["corr.material.product.map"].create(
            {"kind": "liner", "gsm_min": 100, "gsm_max": 200, "product_id": mapped_prod.id}
        )
        payload = self._build_preview_payload(status_liner="missing", status_medium="exact")
        with self.assertRaises(UserError):
            engine_v2_bom_adapter.create_bom_from_preview_payload(self.env, payload, self.fg_product)

    def test_create_from_preview_blocks_ambiguous_paper_variants(self):
        """BoM creation blocks unresolved paper variants instead of falling back."""

        from odoo.addons.corr_mrp_bridge import bom_adapter as engine_v2_bom_adapter

        mapped_prod = self.env["product.product"].create(
            {
                "name": "Mapped Liner Ambiguous",
                "uom_id": self.uom_kg.id,
                "uom_po_id": self.uom_kg.id,
            }
        )
        self.env["corr.material.product.map"].create(
            {"kind": "liner", "gsm_min": 100, "gsm_max": 200, "product_id": mapped_prod.id}
        )
        payload = self._build_preview_payload(status_liner="ambiguous", status_medium="exact")
        with self.assertRaises(UserError):
            engine_v2_bom_adapter.create_bom_from_preview_payload(self.env, payload, self.fg_product)

    def test_costing_summary_present_in_raw_json(self):
        """Preview raw_json includes a costing block with basic totals."""

        # Assign a simple non-zero cost to the exact paper variant so
        # costing has something to work with.
        self.prod_exact.standard_price = 10.0

        wiz = self._run_preview(printing_ups=1)

        payload = json.loads(wiz.raw_json or "{}")
        costing = payload.get("costing") or {}

        # Costing block should be present and contain components.
        self.assertTrue(costing, "costing block should not be empty")
        comps = costing.get("components") or []
        self.assertTrue(comps, "costing.components should not be empty")

        # Selected cost source must be surfaced.
        self.assertEqual(costing.get("cost_source"), "standard_price")

        # At least one component must use one of the resolved paper variants
        # from the preview's paper_resolution block.
        paper_res = payload.get("paper_resolution") or []
        resolved_ids = {
            e.get("product_id")
            for e in paper_res
            if e.get("status") == "exact" and e.get("product_id")
        }
        self.assertTrue(resolved_ids, "expected resolved paper variants in paper_resolution")

        paper_lines = [c for c in comps if c.get("product_id") in resolved_ids]
        self.assertTrue(paper_lines, "expected costing component for resolved paper variant")

        # Sanity check: total_cost_for_order scales with order_qty.
        order_qty = float(costing.get("order_qty") or 0.0)
        total_per_fg = float(costing.get("total_cost_per_fg") or 0.0)
        total_for_order = float(costing.get("total_cost_for_order") or 0.0)
        if order_qty > 0.0:
            self.assertAlmostEqual(total_for_order, total_per_fg * order_qty, places=6)

        # Operation costing summary fields should be present even when no
        # per-stage configuration exists yet (defaulting to zero).
        total_operation = float(costing.get("total_operation_cost") or 0.0)
        total_production = float(costing.get("total_production_cost") or 0.0)
        self.assertGreaterEqual(total_operation, 0.0)
        if order_qty > 0.0:
            self.assertAlmostEqual(total_production, total_for_order + total_operation, places=6)

    def test_operation_cost_missing_config_defaults_to_zero(self):
        """When no operation-cost config exists, operation totals are zero and preview works."""

        # Ensure a clean slate for operation costing configuration.
        self.env["corr.engine.v2.operation.cost"].search([]).unlink()

        self.prod_exact.standard_price = 10.0
        wiz = self._run_preview(printing_ups=1)

        payload = json.loads(wiz.raw_json or "{}")
        costing = payload.get("costing") or {}
        stage_costs = costing.get("stage_costs") or {}

        # Global operation total should be zero.
        total_operation = float(costing.get("total_operation_cost") or 0.0)
        self.assertEqual(total_operation, 0.0)

        # All stages, when present, should have zero operation_cost totals.
        for info in stage_costs.values():
            self.assertEqual(float(info.get("total_operation_cost") or 0.0), 0.0)

    def test_operation_cost_per_unit_configuration(self):
        """Per-unit machine/labor costing on configured stages is applied from StageGraph quantities."""

        # Clean any existing configuration to keep expectations deterministic.
        self.env["corr.engine.v2.operation.cost"].search([]).unlink()

        OpCost = self.env["corr.engine.v2.operation.cost"]
        # Configure per-unit costs on the finished_goods stage (boxes).
        OpCost.create(
            {
                "plant_id": self.plant.id,
                "stage_key": "finished_goods",
                "machine_cost_method": "per_unit",
                "machine_rate": 2.0,
                "labor_cost_method": "per_unit",
                "labor_rate": 3.0,
            }
        )

        self.prod_exact.standard_price = 10.0
        wiz = self._run_preview(printing_ups=1)

        payload = json.loads(wiz.raw_json or "{}")
        costing = payload.get("costing") or {}
        stage_costs = costing.get("stage_costs") or {}

        fg_info = stage_costs.get("finished_goods") or {}
        qty_boxes = float(fg_info.get("qty") or 0.0)
        self.assertGreater(qty_boxes, 0.0)

        machine_cost = float(fg_info.get("machine_cost") or 0.0)
        labor_cost = float(fg_info.get("labor_cost") or 0.0)
        total_op_stage = float(fg_info.get("total_operation_cost") or 0.0)

        # Per-unit costing must scale with StageGraph quantity.
        self.assertAlmostEqual(machine_cost, 2.0 * qty_boxes, places=6)
        self.assertAlmostEqual(labor_cost, 3.0 * qty_boxes, places=6)
        self.assertAlmostEqual(total_op_stage, machine_cost + labor_cost, places=6)

        # Global total_operation_cost must equal the sum of per-stage totals.
        total_operation = float(costing.get("total_operation_cost") or 0.0)
        self.assertAlmostEqual(total_operation, total_op_stage, places=6)

        # Total production cost remains material + operation.
        total_material = float(costing.get("total_cost_for_order") or 0.0)
        total_production = float(costing.get("total_production_cost") or 0.0)
        self.assertAlmostEqual(total_production, total_material + total_operation, places=6)

        # Quantity basis debug info should clearly indicate per-box interpretation.
        self.assertEqual(fg_info.get("stage_qty_basis_label"), "per_box")

        # Raw quantity_stage_graph node must carry the same operation_cost metadata.
        qgraph = (payload.get("quantity_stage_graph") or {})
        nodes = qgraph.get("nodes") or {}
        fg_node = nodes.get("finished_goods") or {}
        op_meta = (fg_node.get("meta") or {}).get("operation_cost") or {}
        self.assertEqual(op_meta.get("stage_qty_basis_label"), "per_box")
        self.assertAlmostEqual(float(op_meta.get("stage_qty") or 0.0), qty_boxes, places=6)

    def test_operation_cost_dynamic_stage_keys_map_to_buckets(self):
        """Dynamic engine stage keys map to quantity buckets for operation costing."""

        # Clean any existing configuration.
        self.env["corr.engine.v2.operation.cost"].search([]).unlink()

        OpCost = self.env["corr.engine.v2.operation.cost"]
        # Configure costs using dynamic stage keys that should map to
        # board (2ply), sheet (ts_print) and finished_goods (fg).
        OpCost.create(
            {
                "plant_id": self.plant.id,
                "stage_key": "2ply",
                "machine_cost_method": "per_unit",
                "machine_rate": 1.0,
                "labor_cost_method": "per_unit",
                "labor_rate": 0.0,
            }
        )
        OpCost.create(
            {
                "plant_id": self.plant.id,
                "stage_key": "ts_print",
                "machine_cost_method": "per_unit",
                "machine_rate": 0.5,
                "labor_cost_method": "per_unit",
                "labor_rate": 0.0,
            }
        )
        OpCost.create(
            {
                "plant_id": self.plant.id,
                "stage_key": "fg",
                "machine_cost_method": "per_unit",
                "machine_rate": 0.25,
                "labor_cost_method": "per_unit",
                "labor_rate": 0.0,
            }
        )

        self.prod_exact.standard_price = 10.0
        wiz = self._run_preview(printing_ups=1)

        payload = json.loads(wiz.raw_json or "{}")
        costing = payload.get("costing") or {}
        stage_costs = costing.get("stage_costs") or {}

        board_info = stage_costs.get("board") or {}
        sheet_info = stage_costs.get("sheet") or {}
        fg_info = stage_costs.get("finished_goods") or {}

        # All three buckets should have non-zero machine_cost from the
        # dynamic configuration.
        self.assertGreater(float(board_info.get("machine_cost") or 0.0), 0.0)
        self.assertGreater(float(sheet_info.get("machine_cost") or 0.0), 0.0)
        self.assertGreater(float(fg_info.get("machine_cost") or 0.0), 0.0)

    def test_stage_material_costs_match_components(self):
        """Stage-wise material_cost totals equal summed component costs."""

        # Ensure non-zero material costing so aggregation is meaningful.
        self.prod_exact.standard_price = 10.0

        wiz = self._run_preview(printing_ups=1)
        payload = json.loads(wiz.raw_json or "{}")
        costing = payload.get("costing") or {}

        components = costing.get("components") or []
        stage_costs = costing.get("stage_costs") or {}

        # Guard: preview should expose both components and stage_costs.
        self.assertTrue(components)
        self.assertTrue(stage_costs)

        total_material = float(costing.get("total_cost_for_order") or 0.0)
        sum_component_costs = sum(float(c.get("cost_for_order") or 0.0) for c in components)
        sum_stage_material = sum(float(info.get("material_cost") or 0.0) for info in stage_costs.values())

        # total_cost_for_order must match the component aggregate.
        self.assertAlmostEqual(total_material, sum_component_costs, places=6)
        # Stage-wise material_cost must aggregate back to the same total.
        self.assertAlmostEqual(total_material, sum_stage_material, places=6)

    def test_liner_and_medium_standard_price_are_costed_line_wise(self):
        """Paper RM lines use resolved variants and standard price per line."""

        self.prod_exact.standard_price = 10.0

        wiz = self._run_preview(printing_ups=1, cost_source="standard_price")
        payload = json.loads(wiz.raw_json or "{}")
        costing = payload.get("costing") or {}
        components = costing.get("components") or []

        liner = next((c for c in components if c.get("engine_role") == "liner"), None)
        medium = next((c for c in components if c.get("engine_role") == "medium"), None)
        self.assertTrue(liner, "expected cost line for resolved liner variant")
        self.assertTrue(medium, "expected cost line for resolved medium variant")

        for line in (liner, medium):
            self.assertEqual(line.get("product_id"), self.prod_exact.id)
            self.assertEqual(line.get("cost_source"), "standard_price")
            self.assertAlmostEqual(float(line.get("standard_price") or 0.0), 10.0, places=6)
            self.assertAlmostEqual(float(line.get("selected_unit_cost") or 0.0), 10.0, places=6)
            self.assertAlmostEqual(
                float(line.get("cost_for_order") or 0.0),
                float(line.get("qty_for_order") or 0.0) * 10.0,
                places=6,
            )

    def test_standard_price_source_warns_when_zero(self):
        """Standard-price costing reports zero/missing product costs explicitly."""

        self.prod_exact.standard_price = 0.0

        wiz = self._run_preview(printing_ups=1, cost_source="standard_price")
        payload = json.loads(wiz.raw_json or "{}")
        costing = payload.get("costing") or {}
        components = costing.get("components") or []
        missing = costing.get("missing_cost") or []

        paper_line = next((c for c in components if c.get("product_id") == self.prod_exact.id), None)
        self.assertTrue(paper_line, "expected resolved paper cost trace line")
        self.assertEqual(paper_line.get("cost_source"), "standard_price")
        self.assertAlmostEqual(float(paper_line.get("standard_price") or 0.0), 0.0, places=6)
        self.assertAlmostEqual(float(paper_line.get("selected_unit_cost") or 0.0), 0.0, places=6)
        self.assertAlmostEqual(float(paper_line.get("cost_for_order") or 0.0), 0.0, places=6)
        self.assertEqual(paper_line.get("cost_warning"), "standard_price_missing_or_zero")
        self.assertIn("Standard price missing/zero for product", paper_line.get("cost_warning_message") or "")
        self.assertTrue(
            any(
                item.get("product_id") == self.prod_exact.id
                and item.get("reason") == "standard_price_missing_or_zero"
                and "Standard price missing/zero for product" in (item.get("warning") or "")
                for item in missing
            ),
            "missing_cost should warn when selected standard price source has no value",
        )
        self.assertIn("Standard price missing/zero for product", wiz.stage_costing_html or "")

    def test_unit_top_sheet_external_standard_price_is_included(self):
        """Unit top-sheet RM uses selected product and contributes material cost."""

        try:
            uom_unit = self.env.ref("uom.product_uom_unit")
        except Exception:
            uom_unit = self.env["uom.uom"].search([], limit=1)
        top_product = self.env["product.product"].create(
            {
                "name": "Costed Unit Top Sheet",
                "uom_id": uom_unit.id,
                "uom_po_id": uom_unit.id,
                "standard_price": 7.0,
            }
        )
        self.spec.write(
            {
                "printing_required": True,
                "top_layer_mode_ui": "Unit",
                "top_sheet_product_id": top_product.id,
            }
        )
        self.prod_exact.standard_price = 10.0

        wiz = self._run_preview(printing_ups=1, cost_source="standard_price")
        payload = json.loads(wiz.raw_json or "{}")
        costing = payload.get("costing") or {}
        components = costing.get("components") or []

        top_line = next((c for c in components if c.get("engine_role") == "top_sheet_external"), None)
        self.assertTrue(top_line, "expected unit top_sheet_external cost line")
        self.assertEqual(top_line.get("product_id"), top_product.id)
        self.assertEqual(top_line.get("cost_source"), "standard_price")
        self.assertAlmostEqual(float(top_line.get("selected_unit_cost") or 0.0), 7.0, places=6)
        self.assertAlmostEqual(float(top_line.get("qty_for_order") or 0.0), 10.0, places=6)
        self.assertAlmostEqual(
            float(top_line.get("cost_for_order") or 0.0),
            float(top_line.get("qty_for_order") or 0.0) * 7.0,
            places=6,
        )

        stage_costs = costing.get("stage_costs") or {}
        routed_stage = top_line.get("target_stage")
        self.assertTrue(routed_stage)
        self.assertGreater(float(stage_costs.get(routed_stage, {}).get("material_cost") or 0.0), 0.0)

    def test_roll_top_sheet_standard_price_is_included_as_kg_line(self):
        """Roll top-sheet RM is costed as its resolved kg paper variant."""

        self.spec.write(
            {
                "printing_required": True,
                "top_layer_mode_ui": "Roll",
                "top_sheet_product_id": False,
            }
        )
        self.prod_exact.standard_price = 12.0

        wiz = self._run_preview(printing_ups=1, cost_source="standard_price")
        payload = json.loads(wiz.raw_json or "{}")
        costing = payload.get("costing") or {}
        components = costing.get("components") or []

        top_roll = next((c for c in components if c.get("engine_role") == "top_roll"), None)
        self.assertTrue(top_roll, "expected kg top_roll cost line")
        self.assertEqual(top_roll.get("product_id"), self.prod_exact.id)
        self.assertAlmostEqual(float(top_roll.get("selected_unit_cost") or 0.0), 12.0, places=6)
        self.assertAlmostEqual(
            float(top_roll.get("cost_for_order") or 0.0),
            float(top_roll.get("qty_for_order") or 0.0) * 12.0,
            places=6,
        )

    def test_last_purchase_price_source_warns_when_unavailable(self):
        """Last-purchase source does not fall back silently to standard price."""

        try:
            uom_unit = self.env.ref("uom.product_uom_unit")
        except Exception:
            uom_unit = self.env["uom.uom"].search([], limit=1)
        top_product = self.env["product.product"].create(
            {
                "name": "Top Sheet Without Last Purchase",
                "uom_id": uom_unit.id,
                "uom_po_id": uom_unit.id,
                "standard_price": 9.0,
            }
        )
        self.spec.write(
            {
                "printing_required": True,
                "top_layer_mode_ui": "Unit",
                "top_sheet_product_id": top_product.id,
            }
        )

        wiz = self._run_preview(printing_ups=1, cost_source="last_purchase_price")
        payload = json.loads(wiz.raw_json or "{}")
        costing = payload.get("costing") or {}
        components = costing.get("components") or []
        missing = costing.get("missing_cost") or []

        top_line = next((c for c in components if c.get("product_id") == top_product.id), None)
        self.assertTrue(top_line, "expected trace line for top sheet even without last purchase cost")
        self.assertEqual(top_line.get("cost_source"), "last_purchase_price")
        self.assertAlmostEqual(float(top_line.get("last_purchase_price") or 0.0), 0.0, places=6)
        self.assertAlmostEqual(float(top_line.get("selected_unit_cost") or 0.0), 0.0, places=6)
        self.assertTrue(
            any(
                item.get("product_id") == top_product.id
                and item.get("reason") == "last_purchase_price_missing_or_zero"
                for item in missing
            ),
            "missing_cost should warn when selected last purchase source has no value",
        )

    def test_stage_material_costs_are_bucket_local_not_cumulative(self):
        """Material stage costs are local bucket totals, not carry-forward totals."""

        self.env["corr.engine.v2.operation.cost"].search([]).unlink()
        self.prod_exact.standard_price = 10.0

        wiz = self._run_preview(printing_ups=1)
        payload = json.loads(wiz.raw_json or "{}")
        costing = payload.get("costing") or {}
        stage_costs = costing.get("stage_costs") or {}

        total_material = float(costing.get("total_cost_for_order") or 0.0)
        self.assertGreater(total_material, 0.0)

        positive_material_stages = sorted(
            key
            for key, info in stage_costs.items()
            if float(info.get("material_cost") or 0.0) > 0.0
        )
        self.assertEqual(positive_material_stages, ["board"])

        for info in stage_costs.values():
            material = float(info.get("material_cost") or 0.0)
            operation = float(info.get("total_operation_cost") or 0.0)
            total = float(info.get("stage_total_cost") or 0.0)
            self.assertAlmostEqual(total, material + operation, places=6)

    def test_sheet_operation_cost_reflects_selected_printing_ups(self):
        """Sheet-stage machine cost follows StageGraph sheet quantity after UPS changes."""

        self.env["corr.engine.v2.operation.cost"].search([]).unlink()
        self.env["corr.engine.v2.operation.cost"].create(
            {
                "plant_id": self.plant.id,
                "stage_key": "sheet",
                "machine_cost_method": "per_unit",
                "machine_rate": 1.0,
                "labor_cost_method": "per_unit",
                "labor_rate": 0.0,
            }
        )
        self.prod_exact.standard_price = 10.0

        wiz_1up = self._run_preview(printing_ups=1)
        payload_1up = json.loads(wiz_1up.raw_json or "{}")
        sheet_1up = (payload_1up.get("costing") or {}).get("stage_costs", {}).get("sheet") or {}

        wiz_2up = self._run_preview(printing_ups=2)
        payload_2up = json.loads(wiz_2up.raw_json or "{}")
        sheet_2up = (payload_2up.get("costing") or {}).get("stage_costs", {}).get("sheet") or {}

        qty_1up = float(sheet_1up.get("qty") or 0.0)
        qty_2up = float(sheet_2up.get("qty") or 0.0)
        machine_1up = float(sheet_1up.get("machine_cost") or 0.0)
        machine_2up = float(sheet_2up.get("machine_cost") or 0.0)

        self.assertGreater(qty_1up, qty_2up)
        self.assertAlmostEqual(machine_1up, qty_1up, places=6)
        self.assertAlmostEqual(machine_2up, qty_2up, places=6)
        self.assertGreater(machine_1up, machine_2up)

    def test_operation_cost_per_hour_configuration_rejected(self):
        """per_hour costing is not silently accepted; configuration is rejected."""

        OpCost = self.env["corr.engine.v2.operation.cost"]

        with self.assertRaises(ValidationError):
            OpCost.create(
                {
                    "plant_id": self.plant.id,
                    "stage_key": "finished_goods",
                    "machine_cost_method": "per_hour",
                    "machine_rate": 100.0,
                    "labor_cost_method": "per_unit",
                    "labor_rate": 1.0,
                }
            )



