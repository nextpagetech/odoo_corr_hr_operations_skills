import json

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "corr_mrp_bridge", "corr_mrp_bridge_phase5")
class TestEngineV2PreviewPhase5Parity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        try:
            cls.uom_kg = env.ref("uom.product_uom_kgm")
        except Exception:
            cls.uom_kg = env["uom.uom"].search([], limit=1)

        wc = env["mrp.workcenter"].create({"name": "Phase5 Preview WC"})
        OpMap = env["corr.operation.map"]
        OpMap.create({"op_name": "Corrugation", "workcenter_id": wc.id})
        OpMap.create({"op_name": "Printing", "workcenter_id": wc.id})
        OpMap.create({"op_name": "Slot/Die", "workcenter_id": wc.id})

        cls.prod_liner = env["product.product"].create(
            {"name": "Phase5 Liner", "uom_id": cls.uom_kg.id, "uom_po_id": cls.uom_kg.id}
        )
        cls.prod_medium = env["product.product"].create(
            {"name": "Phase5 Medium", "uom_id": cls.uom_kg.id, "uom_po_id": cls.uom_kg.id}
        )
        cls.prod_adh = env["product.product"].create(
            {"name": "Phase5 Adhesive", "uom_id": cls.uom_kg.id, "uom_po_id": cls.uom_kg.id}
        )
        Map = env["corr.material.product.map"]
        Map.create({"kind": "liner", "gsm_min": 150, "gsm_max": 200, "product_id": cls.prod_liner.id})
        Map.create({"kind": "medium", "gsm_min": 100, "gsm_max": 140, "product_id": cls.prod_medium.id})
        Map.create({"kind": "adhesive", "engine_code": "fg", "product_id": cls.prod_adh.id})

        cls.plant = cls._make_plant("PHASE5_BASE")
        cls.split_plant = cls._make_plant(
            "PHASE5_SPLIT",
            max_machine_width_mm=800.0,
            split_mode="equal_only",
            sheet_length_mm=4000.0,
            sheet_width_mm=4000.0,
        )
        cls.consumable_plant = cls._make_plant("PHASE5_CONS")
        cls.route_fallback_plant = cls._make_plant("PHASE5_ROUTEFALLBACK")

        cls.spec = cls._make_spec("Phase5 Base Spec", 300.0, 200.0, 150.0)
        cls.split_spec = cls._make_spec("Phase5 Split Spec", 1000.0, 800.0, 400.0)

        cls._seed_phase5_runtime()
        cls._apply_phase4_quantity_template_fixes()

    @classmethod
    def _make_plant(cls, code, **extra):
        vals = {
            "code": code,
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
            "sheet_length_mm": 2000.0,
            "sheet_width_mm": 2000.0,
        }
        vals.update(extra)
        return cls.env["corr.plant.param"].create(vals)

    @classmethod
    def _make_spec(cls, name, length_mm, width_mm, height_mm):
        pt = cls.env["product.template"].create({"name": name})
        Spec = cls.env["corr.spec.template"].with_context(skip_corr_stack_check=True)
        spec = Spec.create(
            {
                "name": name,
                "product_tmpl_id": pt.id,
                "style": "0201",
                "length_mm": length_mm,
                "width_mm": width_mm,
                "height_mm": height_mm,
                "ply_count": 2,
            }
        )
        flute = cls.env["corr.flute"].search([("code", "=", "B")], limit=1)
        if not flute:
            flute = cls.env["corr.flute"].create({"name": "B", "code": "B", "caliper_mm": 2.5, "tuf": 0.05})

        cls.env["corr.spec.stack.line"].create(
            {
                "spec_id": spec.id,
                "sequence": 10,
                "role": "liner",
                "gsm": 180.0,
                "bf": 23.0,
                "color": "Kraft",
            }
        )
        cls.env["corr.spec.stack.line"].create(
            {
                "spec_id": spec.id,
                "sequence": 20,
                "role": "medium",
                "gsm": 120.0,
                "bf": 23.0,
                "flute_id": flute.id,
                "color": "Kraft",
            }
        )
        return spec

    @classmethod
    def _seed_phase5_runtime(cls):
        Phase8Seed = cls.env["corr.engine.phase8.seed.wizard"]
        ContractSeed = cls.env["corr.engine.v2.contract.seed.wizard"]
        for plant in [cls.plant, cls.split_plant, cls.consumable_plant]:
            Phase8Seed.create(
                {
                    "plant_id": plant.id,
                    "allow_default_updates": True,
                }
            ).action_run_seed()
        ContractSeed.create({}).action_run()

    @classmethod
    def _apply_phase4_quantity_template_fixes(cls):
        StageTemplate = cls.env["corr.engine.stage.template"].sudo()
        QuantityProfile = cls.env["corr.engine.quantity.profile"].sudo()
        sheet_profile = QuantityProfile.search([("code", "=", "sheet_based_board")], limit=1)
        fg_profile = QuantityProfile.search([("code", "=", "per_fg_unit")], limit=1)

        diecut = StageTemplate.search([("code", "=", "diecut")], limit=1)
        peeloff = StageTemplate.search([("code", "=", "peeloff")], limit=1)
        fg = StageTemplate.search([("code", "=", "fg")], limit=1)
        if diecut and sheet_profile:
            diecut.write({"quantity_profile_id": sheet_profile.id})
        if peeloff and sheet_profile:
            peeloff.write({"quantity_profile_id": sheet_profile.id})
        if fg and fg_profile:
            fg.write(
                {
                    "quantity_profile_id": fg_profile.id,
                    "can_be_terminal": True,
                    "output_stock_behavior": "fg",
                }
            )

    def _run_preview_backend(self, spec, plant, order_qty, backend):
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": spec.id,
                "plant_id": plant.id,
                "order_qty": order_qty,
                "preview_backend": backend,
            }
        )
        action = wiz.action_run_debug()
        self.assertEqual(action.get("res_model"), "corr.engine_v2.debug.wizard")
        payload = json.loads(wiz.raw_json or "{}")
        self.assertEqual(payload.get("preview_backend_selected"), backend)
        expected_applied = "stagecontext" if backend == "stagecontext" else "legacy"
        self.assertEqual(payload.get("preview_backend_applied"), expected_applied)
        self.assertEqual(wiz.preview_backend_applied, expected_applied)
        return wiz, payload

    def _set_consumables_enabled(self, enabled, fg_factor=None):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("engine_v2_enable_consumables", "1" if enabled else "0")
        if fg_factor is None:
            icp.set_param("corr_mrp_bridge.default_adhesive_factor_fg_kg_per_m2", "")
        else:
            icp.set_param("corr_mrp_bridge.default_adhesive_factor_fg_kg_per_m2", str(float(fg_factor)))

    def _legacy_summary(self, payload):
        metrics = payload.get("metrics") or {}
        extra = metrics.get("extra") or {}
        qgraph = payload.get("quantity_stage_graph") or {}
        nodes = qgraph.get("nodes") or {}
        return {
            "order_qty": float(payload.get("order_qty", 0.0) or 0.0),
            "m2_per_box": float(metrics.get("m2_per_box", 0.0) or 0.0),
            "kg_per_box": float(metrics.get("kg_per_box", 0.0) or 0.0),
            "blanks_per_sheet": int(extra.get("layout_blanks_per_sheet", 0) or 0),
            "boxes_per_sheet": float(extra.get("boxes_per_sheet", 0.0) or 0.0),
            "sheets_required": float(((nodes.get("sheet") or {}).get("qty") or 0.0)),
            "fg_qty": float(((nodes.get("finished_goods") or {}).get("qty") or 0.0)),
            "pieces_per_box": int(extra.get("pieces_per_box", 1) or 1),
        }

    def _stagecontext_summary(self, payload):
        preview = payload.get("phase5_preview_model") or {}
        summary = preview.get("summary") or {}
        return {
            "order_qty": float(summary.get("order_qty", 0.0) or 0.0),
            "m2_per_box": float(summary.get("m2_per_box", 0.0) or 0.0),
            "kg_per_box": float(summary.get("kg_per_box", 0.0) or 0.0),
            "blanks_per_sheet": int(summary.get("blanks_per_sheet", 0) or 0),
            "boxes_per_sheet": float(summary.get("boxes_per_sheet", 0.0) or 0.0),
            "sheets_required": float(summary.get("sheets_required", 0.0) or 0.0),
            "fg_qty": float(summary.get("fg_stage_total_qty", 0.0) or 0.0),
        }

    def _normalize_role(self, role):
        role = (role or "").strip().lower()
        if role.startswith("adhesive"):
            return "adhesive"
        return role

    def _legacy_materials(self, payload):
        out = {}
        order_qty = float(payload.get("order_qty", 0.0) or 0.0)
        for item in payload.get("rm_requirements") or []:
            role = self._normalize_role(item.get("role"))
            current = out.setdefault(role, {"qty_per_fg": 0.0, "total_qty": 0.0})
            qty_per_fg = float(item.get("qty_per_fg", 0.0) or 0.0)
            current["qty_per_fg"] += qty_per_fg
            current["total_qty"] += qty_per_fg * order_qty
        return out

    def _stagecontext_materials(self, payload):
        preview = payload.get("phase5_preview_model") or {}
        out = {}
        for item in preview.get("materials") or []:
            role = self._normalize_role(item.get("role"))
            current = out.setdefault(
                role,
                {"qty_per_fg": 0.0, "total_qty": 0.0, "sources": set()},
            )
            current["qty_per_fg"] += float(item.get("qty_per_fg", 0.0) or 0.0)
            current["total_qty"] += float(item.get("total_qty", 0.0) or 0.0)
            if item.get("quantity_source"):
                current["sources"].add(item.get("quantity_source"))
        return out

    def _legacy_operations(self, payload):
        ops = {}
        for item in (payload.get("bom_struct") or {}).get("operations") or []:
            ops[item.get("stage")] = {
                "input_qty": float(item.get("input_qty", 0.0) or 0.0),
                "output_qty": float(item.get("output_qty", 0.0) or 0.0),
            }
        return ops

    def _stagecontext_operations(self, payload):
        ops = {}
        for item in ((payload.get("phase5_preview_model") or {}).get("operations") or []):
            ops[item.get("stage")] = {
                "input_qty": float(item.get("input_qty", 0.0) or 0.0),
                "output_qty": float(item.get("output_qty", 0.0) or 0.0),
            }
        return ops

    def _assert_summary_parity(self, legacy_payload, stage_payload):
        legacy = self._legacy_summary(legacy_payload)
        stage = self._stagecontext_summary(stage_payload)
        self.assertAlmostEqual(stage["order_qty"], legacy["order_qty"], places=6)
        self.assertAlmostEqual(stage["m2_per_box"], legacy["m2_per_box"], places=6)
        self.assertAlmostEqual(stage["kg_per_box"], legacy["kg_per_box"], places=6)
        self.assertEqual(stage["blanks_per_sheet"], legacy["blanks_per_sheet"])
        self.assertAlmostEqual(stage["boxes_per_sheet"], legacy["boxes_per_sheet"], places=6)
        self.assertAlmostEqual(stage["sheets_required"], legacy["sheets_required"], places=6)
        self.assertAlmostEqual(stage["fg_qty"], legacy["fg_qty"], places=6)

    def _assert_material_parity(self, legacy_payload, stage_payload, roles):
        legacy = self._legacy_materials(legacy_payload)
        stage = self._stagecontext_materials(stage_payload)
        stats = ((stage_payload.get("phase5_preview_model") or {}).get("material_stats") or {})
        for role in roles:
            self.assertIn(role, legacy, "legacy role missing: %s" % role)
            self.assertIn(role, stage, "stagecontext role missing: %s" % role)
            self.assertAlmostEqual(stage[role]["qty_per_fg"], legacy[role]["qty_per_fg"], places=6)
            self.assertAlmostEqual(stage[role]["total_qty"], legacy[role]["total_qty"], places=6)
            self.assertFalse("shadow_pending" in stage[role]["sources"])
        self.assertEqual(int(stats.get("legacy_unmatched_rows", 0) or 0), 0)
        self.assertEqual(int(stats.get("pending_qty_rows", 0) or 0), 0)

    def _assert_operation_parity(self, legacy_payload, stage_payload, required_stages):
        legacy = self._legacy_operations(legacy_payload)
        stage = self._stagecontext_operations(stage_payload)
        for op_name in required_stages:
            self.assertIn(op_name, legacy, "legacy operation missing: %s" % op_name)
            self.assertIn(op_name, stage, "stagecontext operation missing: %s" % op_name)
            self.assertAlmostEqual(
                stage[op_name]["input_qty"],
                legacy[op_name]["input_qty"],
                places=6,
                msg="operation input mismatch for %s" % op_name,
            )
            self.assertAlmostEqual(
                stage[op_name]["output_qty"],
                legacy[op_name]["output_qty"],
                places=6,
                msg="operation output mismatch for %s" % op_name,
            )

    def _assert_no_preview_noise(self, wiz):
        text = wiz.warnings_text or ""
        self.assertNotIn("Quantity buckets", text)
        self.assertNotIn("synthetic consumable material keys", text)
        self.assertNotIn("route_source:", text)

    def test_phase5_preview_parity_simple_case(self):
        self._set_consumables_enabled(False)
        legacy_wiz, legacy_payload = self._run_preview_backend(self.spec, self.plant, 1.0, "legacy")
        stage_wiz, stage_payload = self._run_preview_backend(self.spec, self.plant, 1.0, "stagecontext")

        self._assert_summary_parity(legacy_payload, stage_payload)
        self._assert_material_parity(legacy_payload, stage_payload, roles=["liner", "medium"])
        self._assert_operation_parity(legacy_payload, stage_payload, required_stages=["corrugator", "diecut"])
        self._assert_no_preview_noise(stage_wiz)
        self.assertTrue(
            ((stage_payload.get("phase8_shadow_runtime") or {}).get("route_source") or {}).get("selection_source")
        )

    def test_phase5_preview_parity_non_trivial_quantity_case(self):
        self._set_consumables_enabled(False)
        legacy_wiz, legacy_payload = self._run_preview_backend(self.spec, self.plant, 1234.0, "legacy")
        stage_wiz, stage_payload = self._run_preview_backend(self.spec, self.plant, 1234.0, "stagecontext")

        self._assert_summary_parity(legacy_payload, stage_payload)
        self._assert_material_parity(legacy_payload, stage_payload, roles=["liner", "medium"])
        self._assert_operation_parity(legacy_payload, stage_payload, required_stages=["corrugator", "diecut"])
        self._assert_no_preview_noise(stage_wiz)

    def test_phase5_preview_parity_split_case(self):
        self._set_consumables_enabled(False)
        legacy_wiz, legacy_payload = self._run_preview_backend(self.split_spec, self.split_plant, 100.0, "legacy")
        stage_wiz, stage_payload = self._run_preview_backend(self.split_spec, self.split_plant, 100.0, "stagecontext")

        self._assert_summary_parity(legacy_payload, stage_payload)
        self.assertEqual(self._legacy_summary(legacy_payload)["pieces_per_box"], 2)
        self._assert_material_parity(legacy_payload, stage_payload, roles=["liner", "medium"])
        self._assert_operation_parity(legacy_payload, stage_payload, required_stages=["corrugator", "diecut"])
        self._assert_no_preview_noise(stage_wiz)

    def test_phase5_preview_parity_consumables_case(self):
        self._set_consumables_enabled(True, fg_factor=0.2)
        legacy_wiz, legacy_payload = self._run_preview_backend(self.spec, self.consumable_plant, 10.0, "legacy")
        stage_wiz, stage_payload = self._run_preview_backend(self.spec, self.consumable_plant, 10.0, "stagecontext")

        self._assert_summary_parity(legacy_payload, stage_payload)
        self._assert_material_parity(legacy_payload, stage_payload, roles=["liner", "medium", "adhesive"])
        self._assert_operation_parity(legacy_payload, stage_payload, required_stages=["corrugator", "diecut"])
        self._assert_no_preview_noise(stage_wiz)

    def test_phase5_preview_parity_route_fallback_case(self):
        self._set_consumables_enabled(False)
        legacy_wiz, legacy_payload = self._run_preview_backend(self.spec, self.route_fallback_plant, 10.0, "legacy")
        stage_wiz, stage_payload = self._run_preview_backend(self.spec, self.route_fallback_plant, 10.0, "stagecontext")

        self._assert_summary_parity(legacy_payload, stage_payload)
        self._assert_material_parity(legacy_payload, stage_payload, roles=["liner", "medium"])
        self._assert_operation_parity(legacy_payload, stage_payload, required_stages=["corrugator", "diecut"])
        self._assert_no_preview_noise(stage_wiz)
        self.assertEqual(
            ((stage_payload.get("phase8_shadow_runtime") or {}).get("route_source") or {}).get("selection_source"),
            "global_default",
        )


@tagged("post_install", "-at_install", "corr_mrp_bridge", "corr_mrp_bridge_phase6a")
class TestEngineV2BomPhase6A(TestEngineV2PreviewPhase5Parity):
    def _run_bom_backend(self, spec, plant, order_qty, bom_backend):
        Wizard = self.env["corr.engine_v2.debug.wizard"]
        wiz = Wizard.create(
            {
                "spec_id": spec.id,
                "plant_id": plant.id,
                "order_qty": order_qty,
                "preview_backend": "stagecontext",
                "bom_backend": bom_backend,
            }
        )
        wiz.action_run_debug()
        payload = json.loads(wiz.raw_json or "{}")
        paper_resolution = list(payload.get("paper_resolution") or [])
        if paper_resolution and not any((item.get("status") or "") == "exact" for item in paper_resolution):
            payload.pop("paper_resolution", None)
            wiz.raw_json = json.dumps(payload)
        action_bom = wiz.action_create_bom_from_preview()
        self.assertEqual(action_bom.get("res_model"), "mrp.bom")
        bom = self.env["mrp.bom"].browse(action_bom.get("res_id"))
        self.assertTrue(bom.exists())
        return wiz, payload, bom, self._snapshot_bom(bom)

    def _snapshot_bom(self, bom):
        component_rows = []
        for line in bom.bom_line_ids.sorted(lambda l: (l.corr_role or "", l.product_id.id, l.product_uom_id.id, l.id)):
            component_rows.append(
                {
                    "role": line.corr_role or "",
                    "product_id": line.product_id.id,
                    "product_name": line.product_id.display_name,
                    "uom_id": line.product_uom_id.id,
                    "uom_name": line.product_uom_id.display_name,
                    "qty": float(line.product_qty or 0.0),
                }
            )

        operation_rows = []
        for op in bom.operation_ids.sorted(lambda r: (r.sequence, r.name or "", r.id)):
            operation_rows.append(
                {
                    "name": op.name or "",
                    "sequence": int(op.sequence or 0),
                    "workcenter_id": op.workcenter_id.id if op.workcenter_id else None,
                }
            )

        return {
            "bom_id": bom.id,
            "signature": bom.corr_engine_v2_signature,
            "components": component_rows,
            "operations": operation_rows,
        }

    def _assert_bom_parity(self, legacy_snapshot, stage_snapshot):
        self.assertEqual(stage_snapshot["components"], legacy_snapshot["components"])
        self.assertEqual(stage_snapshot["operations"], legacy_snapshot["operations"])
        self.assertEqual(stage_snapshot["signature"], legacy_snapshot["signature"])
        self.assertEqual(stage_snapshot["bom_id"], legacy_snapshot["bom_id"])

    def _assert_blueprint_shape(self, payload):
        blueprint = payload.get("phase6_stagecontext_bom_blueprint") or {}
        self.assertTrue(blueprint, "expected phase6_stagecontext_bom_blueprint in raw_json payload")
        self.assertIn("identity", blueprint)
        self.assertIn("stages", blueprint)
        self.assertIn("components", blueprint)
        self.assertIn("writer_payload", blueprint)
        self.assertTrue(blueprint.get("stages"))
        self.assertTrue(blueprint.get("components"))
        sample = blueprint["components"][0]
        self.assertIn("operation_binding", sample)
        self.assertIn("quantity_bridge", sample)
        self.assertIn("authority", sample)
        self.assertIn("component_key", sample)

    def test_phase6a_bom_parity_simple_case(self):
        self._set_consumables_enabled(False)
        _, _, _, legacy_snapshot = self._run_bom_backend(self.spec, self.plant, 1.0, "legacy")
        _, stage_payload, _, stage_snapshot = self._run_bom_backend(self.spec, self.plant, 1.0, "stagecontext_shadow")
        self._assert_blueprint_shape(stage_payload)
        self._assert_bom_parity(legacy_snapshot, stage_snapshot)

    def test_phase6a_bom_parity_non_trivial_quantity_case(self):
        self._set_consumables_enabled(False)
        _, _, _, legacy_snapshot = self._run_bom_backend(self.spec, self.plant, 1234.0, "legacy")
        _, _, _, stage_snapshot = self._run_bom_backend(self.spec, self.plant, 1234.0, "stagecontext_shadow")
        self._assert_bom_parity(legacy_snapshot, stage_snapshot)

    def test_phase6a_bom_parity_split_case(self):
        self._set_consumables_enabled(False)
        _, _, _, legacy_snapshot = self._run_bom_backend(self.split_spec, self.split_plant, 100.0, "legacy")
        _, _, _, stage_snapshot = self._run_bom_backend(self.split_spec, self.split_plant, 100.0, "stagecontext_shadow")
        self._assert_bom_parity(legacy_snapshot, stage_snapshot)

    def test_phase6a_bom_parity_consumables_case(self):
        self._set_consumables_enabled(True, fg_factor=0.2)
        _, _, _, legacy_snapshot = self._run_bom_backend(self.spec, self.consumable_plant, 10.0, "legacy")
        _, stage_payload, _, stage_snapshot = self._run_bom_backend(
            self.spec, self.consumable_plant, 10.0, "stagecontext_shadow"
        )
        self._assert_bom_parity(legacy_snapshot, stage_snapshot)

        blueprint = stage_payload.get("phase6_stagecontext_bom_blueprint") or {}
        adhesive_components = [
            item
            for item in (blueprint.get("components") or [])
            if (item.get("semantic_role") or "").startswith("adhesive")
        ]
        self.assertTrue(adhesive_components)
        self.assertTrue(
            any(
                (item.get("quantity_bridge") or {}).get("bridge_source") == "legacy_role_fallback"
                for item in adhesive_components
            )
        )
        self.assertTrue(
            any(
                (item.get("quantity_bridge") or {}).get("writer_bridge_source") == "legacy_bom_struct_components"
                for item in adhesive_components
            )
        )

    def test_phase6a_bom_parity_route_fallback_case(self):
        self._set_consumables_enabled(False)
        _, _, _, legacy_snapshot = self._run_bom_backend(self.spec, self.route_fallback_plant, 10.0, "legacy")
        _, stage_payload, _, stage_snapshot = self._run_bom_backend(
            self.spec, self.route_fallback_plant, 10.0, "stagecontext_shadow"
        )
        self._assert_bom_parity(legacy_snapshot, stage_snapshot)
        self.assertEqual(
            ((stage_payload.get("phase8_shadow_runtime") or {}).get("routing") or {}).get("selection_source"),
            "global_default",
        )


@tagged("post_install", "-at_install", "corr_mrp_bridge", "corr_mrp_bridge_phase6b")
class TestEngineV2GeneralBomPhase6B(TestEngineV2BomPhase6A):
    def _run_general_bom_backend(self, spec, plant, order_qty, bom_backend):
        self.env["ir.config_parameter"].sudo().set_param(
            "corr_mrp_bridge.engine_v2_general_bom_backend",
            bom_backend,
        )
        Wizard = self.env["corr.bom.generate.wizard"].with_context(
            engine_v2_enabled=True,
            engine_v2_plant_id=plant.id,
            engine_v2_bom_order_qty=order_qty,
        )
        wiz = Wizard.create(
            {
                "spec_id": spec.id,
                "target_product_tmpl_id": spec.product_tmpl_id.id,
            }
        )
        action = wiz.action_apply()
        self.assertEqual(action.get("res_model"), "mrp.bom")
        bom = self.env["mrp.bom"].browse(action.get("res_id"))
        self.assertTrue(bom.exists())
        try:
            trace_payload = json.loads(bom.corr_engine_trace or "{}")
        except Exception:
            trace_payload = {}
        if not isinstance(trace_payload, dict):
            trace_payload = {}
        return wiz, bom, self._snapshot_bom(bom), (trace_payload.get("writer_diagnostics") or {})

    def _assert_general_rollout_parity(self, spec, plant, order_qty, route_source=None, expect_consumable=False):
        preview_wiz, preview_payload = self._run_preview_backend(spec, plant, order_qty, "stagecontext")
        _, _, debug_stage_snapshot = None, None, None
        _, debug_payload, _, debug_stage_snapshot = self._run_bom_backend(
            spec,
            plant,
            order_qty,
            "stagecontext_shadow",
        )
        _, _, general_legacy_snapshot, _legacy_diag = self._run_general_bom_backend(
            spec,
            plant,
            order_qty,
            "legacy",
        )
        _, general_stage_bom, general_stage_snapshot, general_stage_diag = self._run_general_bom_backend(
            spec,
            plant,
            order_qty,
            "stagecontext_shadow",
        )

        self._assert_no_preview_noise(preview_wiz)
        self._assert_bom_parity(debug_stage_snapshot, general_stage_snapshot)
        self.assertTrue(general_legacy_snapshot.get("components"))
        self.assertTrue(general_legacy_snapshot.get("operations"))

        self.assertEqual(general_stage_diag.get("paper_resolution_policy"), "strict_required")
        self.assertFalse(general_stage_diag.get("paper_resolution_fallbacks"))

        if route_source:
            self.assertEqual(
                ((preview_payload.get("phase8_shadow_runtime") or {}).get("routing") or {}).get("selection_source"),
                route_source,
            )
            self.assertEqual(
                ((debug_payload.get("phase8_shadow_runtime") or {}).get("routing") or {}).get("selection_source"),
                route_source,
            )

        if expect_consumable:
            roles = [row.get("role") for row in general_stage_snapshot.get("components") or []]
            self.assertIn("adhesive", roles)

    def test_phase6b_general_bom_rollout_simple_case(self):
        self._set_consumables_enabled(False)
        self._assert_general_rollout_parity(self.spec, self.plant, 1.0)

    def test_phase6b_general_bom_rollout_non_trivial_case(self):
        self._set_consumables_enabled(False)
        self._assert_general_rollout_parity(self.spec, self.plant, 1234.0)

    def test_phase6b_general_bom_rollout_split_case(self):
        self._set_consumables_enabled(False)
        self._assert_general_rollout_parity(self.split_spec, self.split_plant, 100.0)

    def test_phase6b_general_bom_rollout_consumables_case(self):
        self._set_consumables_enabled(True, fg_factor=0.2)
        self._assert_general_rollout_parity(
            self.spec,
            self.consumable_plant,
            10.0,
            expect_consumable=True,
        )

    def test_phase6b_general_bom_rollout_route_fallback_case(self):
        self._set_consumables_enabled(False)
        self._assert_general_rollout_parity(
            self.spec,
            self.route_fallback_plant,
            10.0,
            route_source="global_default",
        )



