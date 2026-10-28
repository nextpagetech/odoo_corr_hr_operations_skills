import json

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "corr_mrp_bridge", "corr_mrp_bridge_phase7c")
class TestEngineV2PreviewStageFeasibility(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        try:
            cls.uom_kg = env.ref("uom.product_uom_kgm")
        except Exception:
            cls.uom_kg = env["uom.uom"].search([], limit=1)

        cls.plant = env["corr.plant.param"].create(
            {
                "code": "PHASE7C_FEAS",
                "setup_waste_m2": 0.0,
                "process_waste_pct": 2.0,
                "default_glue_lap_mm": 20.0,
                "flap_basis": "fixed",
                "flap_fixed_mm": 20.0,
                "ink_gm2_at_100pct_default": 5.0,
                "lam_overlap_percent_default": 5.0,
                "default_reel_widths_mm": [2200, 2600],
                "humidity_factor": 1.0,
                "rounding_rules_json": {"kg": 3, "m": 3, "m2": 4},
                "gsm_tolerance_pct": 5.0,
                "default_ply_max": 3,
                "sheet_length_mm": 4000.0,
                "sheet_width_mm": 4000.0,
                "max_machine_width_mm": 4000.0,
            }
        )

        cls.prod_liner = env["product.product"].create(
            {"name": "Phase7C Liner", "uom_id": cls.uom_kg.id, "uom_po_id": cls.uom_kg.id}
        )
        cls.prod_medium = env["product.product"].create(
            {"name": "Phase7C Medium", "uom_id": cls.uom_kg.id, "uom_po_id": cls.uom_kg.id}
        )
        env["corr.material.product.map"].create(
            {"kind": "liner", "gsm_min": 150, "gsm_max": 200, "product_id": cls.prod_liner.id}
        )
        env["corr.material.product.map"].create(
            {"kind": "medium", "gsm_min": 100, "gsm_max": 130, "product_id": cls.prod_medium.id}
        )

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
                [("code", "in", ["2ply", "board", "seaming", "lamination", "diecut"])]
            )
        }

        cls.wc_corr_feasible = env["mrp.workcenter"].create(
            {"name": "Phase7C Corr Feasible", "corr_flutes_supported": "B"}
        )
        cls.wc_corr_rejected = env["mrp.workcenter"].create(
            {"name": "Phase7C Corr Rejected", "corr_flutes_supported": "B"}
        )
        cls.wc_corr_f_only = env["mrp.workcenter"].create(
            {
                "name": "Phase7C Corr F Only",
                "corr_flutes_supported": "F",
                "corr_deckle_min_cm": 1.0,
                "corr_deckle_max_cm": 500.0,
                "corr_length_max_mm": 5000.0,
                "max_slitting_ups": 2,
            }
        )
        cls.wc_seaming = env["mrp.workcenter"].create({"name": "Phase7C Seaming"})

        OpMap = env["corr.operation.map"]
        cls.map_corr_feasible = OpMap.create(
            {
                "op_name": "Corrugation",
                "workcenter_id": cls.wc_corr_feasible.id,
                "deckle_max_cm": 500.0,
            }
        )
        cls.map_corr_rejected = OpMap.create(
            {
                "op_name": "Corrugation",
                "workcenter_id": cls.wc_corr_rejected.id,
                "deckle_max_cm": 10.0,
            }
        )
        cls.map_corr_f_only = OpMap.create(
            {
                "op_name": "Corrugation",
                "workcenter_id": cls.wc_corr_f_only.id,
                "deckle_max_cm": 500.0,
            }
        )
        cls.map_seaming = OpMap.create(
            {
                "op_name": "Seaming",
                "workcenter_id": cls.wc_seaming.id,
            }
        )

        cls.spec = cls._make_spec("Phase7C Feasibility Spec", 600.0, 400.0, 200.0)
        cls.route_standard = cls._make_routing_profile("Phase7C 2ply-board-seaming", ["2ply", "board", "seaming"])
        cls.route_with_diecut = cls._make_routing_profile(
            "Phase7C 2ply-board-diecut-seaming",
            ["2ply", "board", "diecut", "seaming"],
        )
        cls.route_seaming_only = cls._make_routing_profile("Phase7C seaming-only", ["seaming"])

        cls.v2_standard = env["corr.engine.v2.spec"].create(
            {
                "name": "Phase7C V2 Standard",
                "plant_id": cls.plant.id,
                "routing_profile_id": cls.route_standard.id,
            }
        )
        cls.v2_seaming_only = env["corr.engine.v2.spec"].create(
            {
                "name": "Phase7C V2 Seaming Only",
                "plant_id": cls.plant.id,
                "routing_profile_id": cls.route_seaming_only.id,
            }
        )
        cls.v2_with_diecut = env["corr.engine.v2.spec"].create(
            {
                "name": "Phase7C V2 With Diecut",
                "plant_id": cls.plant.id,
                "routing_profile_id": cls.route_with_diecut.id,
            }
        )

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
    def _make_routing_profile(cls, name, stage_codes):
        profile = cls.env["corr.engine.routing.profile"].create({"name": name, "active": True})
        StageLine = cls.env["corr.engine.routing.profile.stage"]
        seq = 10
        for code in stage_codes:
            tmpl = cls.stage_template_by_code[code]
            StageLine.create(
                {
                    "routing_profile_id": profile.id,
                    "sequence": seq,
                    "stage_template_id": tmpl.id,
                }
            )
            seq += 10
        return profile

    def _run_preview(self, v2_spec, *, bom_backend="legacy"):
        wiz = self.env["corr.engine_v2.debug.wizard"].create(
            {
                "spec_id": self.spec.id,
                "plant_id": self.plant.id,
                "order_qty": 100.0,
                "v2_spec_id": v2_spec.id,
                "preview_backend": "stagecontext",
                "bom_backend": bom_backend,
            }
        )
        action = wiz.action_run_debug()
        self.assertEqual(action.get("res_model"), "corr.engine_v2.debug.wizard")
        payload = json.loads(wiz.raw_json or "{}")
        return wiz, payload

    def _snapshot_bom(self, bom):
        return {
            "components": [
                {
                    "role": line.corr_role or "",
                    "product_id": line.product_id.id,
                    "qty": float(line.product_qty or 0.0),
                }
                for line in bom.bom_line_ids.sorted(lambda l: (l.corr_role or "", l.product_id.id, l.id))
            ],
            "operations": [
                {
                    "name": op.name or "",
                    "sequence": int(op.sequence or 0),
                    "workcenter_id": op.workcenter_id.id if op.workcenter_id else None,
                }
                for op in bom.operation_ids.sorted(lambda r: (r.sequence, r.name or "", r.id))
            ],
        }

    def test_stage_feasibility_present_for_each_routed_stage_with_mixed_candidate_status(self):
        _wiz, payload = self._run_preview(self.v2_standard)

        self.assertIn("stage_feasibility", payload)
        stage_feasibility = payload["stage_feasibility"]
        route_stage_codes = [
            item.get("stage_code")
            for item in ((payload.get("phase8_shadow_runtime") or {}).get("routing") or {}).get("stages", [])
        ]
        self.assertEqual(route_stage_codes, ["2ply", "board", "seaming"])
        self.assertEqual(
            ((payload.get("phase8_shadow_runtime") or {}).get("routing") or {}).get("selection_source"),
            "routing_profile",
        )

        feasibility_stage_codes = [item.get("stage_code") for item in (stage_feasibility.get("stages") or [])]
        self.assertEqual(feasibility_stage_codes, route_stage_codes)
        self.assertEqual(int((stage_feasibility.get("summary") or {}).get("stage_count", 0) or 0), 3)

        qgraph_nodes = (payload.get("quantity_stage_graph") or {}).get("nodes") or {}
        for key in ("raw_material", "board", "sheet", "blanks", "finished_goods"):
            self.assertIn(key, qgraph_nodes)

        first_stage = next(item for item in stage_feasibility["stages"] if item.get("stage_code") == "2ply")
        self.assertEqual(first_stage.get("status"), "feasible")
        self.assertEqual((first_stage.get("current_dimensions") or {}).get("basis"), "sheet")
        self.assertTrue((first_stage.get("current_dimensions") or {}).get("checked_orientations"))
        candidate_statuses = {item.get("workcenter_name"): item.get("status") for item in first_stage.get("candidates") or []}
        self.assertEqual(candidate_statuses.get("Phase7C Corr Feasible"), "feasible")
        self.assertEqual(candidate_statuses.get("Phase7C Corr Rejected"), "rejected")
        self.assertEqual(candidate_statuses.get("Phase7C Corr F Only"), "rejected")

        rejected_candidate = next(
            item
            for item in (first_stage.get("candidates") or [])
            if item.get("workcenter_name") == "Phase7C Corr Rejected"
        )
        self.assertTrue(
            any("width" in reason for reason in (rejected_candidate.get("rejection_reasons") or []))
        )
        self.assertTrue(rejected_candidate.get("dimension_checks"))
        flute_rejected_candidate = next(
            item
            for item in (first_stage.get("candidates") or [])
            if item.get("workcenter_name") == "Phase7C Corr F Only"
        )
        self.assertTrue(
            any("required flute" in reason for reason in (flute_rejected_candidate.get("rejection_reasons") or []))
        )

        seaming_stage = next(item for item in stage_feasibility["stages"] if item.get("stage_code") == "seaming")
        self.assertEqual(seaming_stage.get("status"), "feasible")
        self.assertTrue(seaming_stage.get("candidates"))

    def test_stage_feasibility_reports_no_candidate_cleanly(self):
        self.map_seaming.active = False

        _wiz, payload = self._run_preview(self.v2_seaming_only)

        stage_feasibility = payload.get("stage_feasibility") or {}
        self.assertEqual(int((stage_feasibility.get("summary") or {}).get("stage_count", 0) or 0), 1)
        self.assertEqual(int((stage_feasibility.get("summary") or {}).get("rejected_stage_count", 0) or 0), 1)
        entry = (stage_feasibility.get("stages") or [])[0]
        self.assertEqual(entry.get("stage_code"), "seaming")
        self.assertEqual(entry.get("status"), "rejected")
        self.assertEqual(entry.get("rejection_reasons"), ["no_candidate_workcenters"])
        self.assertEqual(entry.get("candidates"), [])

        self.assertTrue(payload.get("phase8_shadow_runtime"))
        self.assertTrue(payload.get("quantity_stage_graph"))

    def test_preview_exposes_slitting_options_ui_and_defaults_to_preferred_selection(self):
        wiz, payload = self._run_preview(self.v2_standard)

        self.assertTrue(wiz.slitting_options_html)
        self.assertTrue(wiz.slitting_option_line_ids)
        self.assertNotIn("<strong>Stage:", wiz.slitting_options_html)
        self.assertNotIn("<h4>2-Ply Corrugation</h4>", wiz.slitting_options_html)
        self.assertNotIn("Workcenter", wiz.slitting_options_html)
        self.assertNotIn("Phase7C Corr", wiz.slitting_options_html)

        slitting = ((payload.get("phase8_shadow_runtime") or {}).get("slitting_options") or {})
        stages = list(slitting.get("stages") or [])
        self.assertTrue(stages)
        self.assertEqual([item.get("stage_code") for item in stages], ["2ply", "board"])
        self.assertEqual((slitting.get("layout_driving_stage") or {}).get("stage_code"), "2ply")
        self.assertEqual(
            (slitting.get("layout_driving_stage") or {}).get("selection_source"),
            "first_eligible_layout_stage_fallback",
        )
        self.assertEqual(wiz.slitting_option_line_ids.mapped("stage_code"), ["2ply"] * len(wiz.slitting_option_line_ids))

        stage_with_options = next((item for item in stages if item.get("preferred_option")), None)
        self.assertTrue(stage_with_options, "expected at least one stage with a preferred slitting option")
        self.assertEqual(stage_with_options.get("stage_code"), "2ply")

        selected_map = json.loads(wiz.selected_slitting_options_json or "{}")
        stage_identity = (
            (slitting.get("layout_driving_stage") or {}).get("stage_contract_code")
            or (slitting.get("layout_driving_stage") or {}).get("stage_code")
            or str((slitting.get("layout_driving_stage") or {}).get("stage_template_id") or "")
        )
        self.assertEqual(list(selected_map.keys()), [stage_identity])
        self.assertIn(stage_identity, selected_map)

        selected = selected_map[stage_identity]
        preferred = stage_with_options.get("preferred_option") or {}
        self.assertEqual(selected.get("up_count"), preferred.get("up_count"))
        self.assertEqual(selected.get("orientation_used"), preferred.get("orientation_used"))
        self.assertEqual(
            ((selected.get("matched_workcenter") or {}).get("workcenter_id")),
            ((preferred.get("matched_workcenter") or {}).get("workcenter_id")),
        )
        self.assertEqual(((preferred.get("matched_workcenter") or {}).get("workcenter_id")), self.wc_corr_feasible.id)

        feasible_wc_ids = {
            ((item.get("matched_workcenter") or {}).get("workcenter_id"))
            for item in (stage_with_options.get("feasible_options") or [])
        }
        self.assertIn(self.wc_corr_feasible.id, feasible_wc_ids)
        self.assertNotIn(self.wc_corr_f_only.id, feasible_wc_ids)

    def test_layout_options_view_hides_stage_columns(self):
        view = self.env.ref("corr_engine_v2.view_corr_engine_v2_debug_wizard")
        arch = view.arch_db or ""
        self.assertNotIn('<field name="stage_name" string="Stage"/>', arch)
        self.assertNotIn('<field name="stage_code" optional="hide"/>', arch)
        self.assertNotIn('matched_workcenter_name', arch)

    def test_invalid_selected_layout_option_is_revalidated_to_compatible_preferred(self):
        wiz, payload = self._run_preview(self.v2_standard)
        slitting = ((payload.get("phase8_shadow_runtime") or {}).get("slitting_options") or {})
        stage_identity = (
            (slitting.get("layout_driving_stage") or {}).get("stage_contract_code")
            or (slitting.get("layout_driving_stage") or {}).get("stage_code")
            or str((slitting.get("layout_driving_stage") or {}).get("stage_template_id") or "")
        )
        old_invalid = {
            stage_identity: {
                "up_count": 1,
                "orientation_used": "direct",
                "final_sheet_length_mm": 1000.0,
                "final_sheet_width_mm": 1000.0,
                "matched_workcenter": {
                    "workcenter_id": self.wc_corr_f_only.id,
                    "workcenter_name": self.wc_corr_f_only.name,
                },
            }
        }
        wiz.selected_slitting_options_json = json.dumps(old_invalid)
        wiz._load_slitting_option_selection_ui(slitting)

        selected_map = json.loads(wiz.selected_slitting_options_json or "{}")
        selected = selected_map[stage_identity]
        self.assertEqual(((selected.get("matched_workcenter") or {}).get("workcenter_id")), self.wc_corr_feasible.id)

    def test_preview_allows_selecting_alternative_slitting_option(self):
        wiz, payload = self._run_preview(self.v2_standard)

        lines_by_stage = {}
        for line in wiz.slitting_option_line_ids.sorted(lambda item: (item.stage_sequence, item.option_rank, item.id)):
            lines_by_stage.setdefault(line.stage_identity, self.env["corr.engine_v2.debug.wizard.slitting.option"])
            lines_by_stage[line.stage_identity] |= line

        candidate_lines = next(
            (lines for lines in lines_by_stage.values() if len(lines) > 1),
            self.env["corr.engine_v2.debug.wizard.slitting.option"],
        )
        self.assertTrue(candidate_lines, "expected at least one stage with multiple feasible options")
        self.assertEqual(len(lines_by_stage), 1)

        original_selected = candidate_lines.filtered(lambda item: item.is_selected)[:1]
        self.assertTrue(original_selected)
        alternative = candidate_lines.filtered(lambda item: not item.is_selected)[:1]
        self.assertTrue(alternative)
        original_selected_id = original_selected.id
        alternative_id = alternative.id
        stage_identity = alternative.stage_identity

        action = alternative.action_select_option()
        self.assertEqual(action.get("res_model"), "corr.engine_v2.debug.wizard")

        wiz = self.env["corr.engine_v2.debug.wizard"].browse(wiz.id)
        candidate_lines = wiz.slitting_option_line_ids.filtered(
            lambda item: item.stage_identity == stage_identity
        )
        self.assertEqual(len(candidate_lines.filtered(lambda item: item.is_selected)), 1)
        self.assertTrue(candidate_lines.filtered(lambda item: item.id == alternative_id and item.is_selected))
        self.assertFalse(candidate_lines.filtered(lambda item: item.id == original_selected_id and item.is_selected))

        selected_map = json.loads(wiz.selected_slitting_options_json or "{}")
        selected = selected_map[stage_identity]
        selected_line = candidate_lines.filtered(lambda item: item.id == alternative_id)[:1]
        self.assertEqual(selected.get("up_count"), selected_line.up_count)
        self.assertEqual(selected.get("orientation_used"), selected_line.orientation_used)

        payload_after = json.loads(wiz.raw_json or "{}")
        self.assertEqual(
            ((payload_after.get("phase8_shadow_runtime") or {}).get("slitting_options") or {}),
            ((payload.get("phase8_shadow_runtime") or {}).get("slitting_options") or {}),
        )

    def test_preview_explicit_primary_layout_stage_wins(self):
        board_tmpl = self.stage_template_by_code["board"]
        previous = bool(board_tmpl.drives_layout_selection)
        try:
            board_tmpl.write({"drives_layout_selection": True})
            wiz, payload = self._run_preview(self.v2_standard)

            slitting = ((payload.get("phase8_shadow_runtime") or {}).get("slitting_options") or {})
            self.assertEqual((slitting.get("layout_driving_stage") or {}).get("stage_code"), "board")
            self.assertEqual(
                (slitting.get("layout_driving_stage") or {}).get("selection_source"),
                "explicit_primary_layout_stage",
            )
            self.assertTrue(wiz.slitting_option_line_ids)
            self.assertEqual(wiz.slitting_option_line_ids.mapped("stage_code"), ["board"] * len(wiz.slitting_option_line_ids))

            selected_map = json.loads(wiz.selected_slitting_options_json or "{}")
            self.assertEqual(len(selected_map), 1)
            self.assertIn("v2_compat.stage.board", selected_map)
        finally:
            board_tmpl.write({"drives_layout_selection": previous})

    def test_preview_hides_slitting_options_for_ineligible_stages(self):
        wiz, payload = self._run_preview(self.v2_with_diecut)

        slitting = ((payload.get("phase8_shadow_runtime") or {}).get("slitting_options") or {})
        stage_codes = [item.get("stage_code") for item in (slitting.get("stages") or [])]
        self.assertEqual(stage_codes, ["2ply", "board"])
        self.assertNotIn("diecut", stage_codes)
        self.assertNotIn("seaming", stage_codes)
        self.assertEqual(wiz.slitting_option_line_ids.mapped("stage_code"), ["2ply"] * len(wiz.slitting_option_line_ids))

    def test_stage_feasibility_is_additive_for_create_bom_flow(self):
        wiz_with, payload_with = self._run_preview(self.v2_standard, bom_backend="stagecontext_shadow")
        self.assertTrue(payload_with.get("stage_feasibility"))

        action_bom_with = wiz_with.action_create_bom_from_preview()
        self.assertEqual(action_bom_with.get("res_model"), "mrp.bom")
        bom_with = self.env["mrp.bom"].browse(action_bom_with.get("res_id"))
        self.assertTrue(bom_with.exists())
        snapshot_with = self._snapshot_bom(bom_with)

        wiz_without, payload_without = self._run_preview(self.v2_standard, bom_backend="stagecontext_shadow")
        payload_without.pop("stage_feasibility", None)
        shadow = dict(payload_without.get("phase8_shadow_runtime") or {})
        shadow.pop("stage_feasibility", None)
        payload_without["phase8_shadow_runtime"] = shadow
        wiz_without.raw_json = json.dumps(payload_without)

        action_bom_without = wiz_without.action_create_bom_from_preview()
        self.assertEqual(action_bom_without.get("res_model"), "mrp.bom")
        bom_without = self.env["mrp.bom"].browse(action_bom_without.get("res_id"))
        self.assertTrue(bom_without.exists())
        snapshot_without = self._snapshot_bom(bom_without)

        self.assertEqual(snapshot_with, snapshot_without)

    def test_bom_workcenter_assignment_uses_compatible_layout_workcenter_when_enabled(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "corr_engine_v2.auto_assign_operation_workcenter",
            "1",
        )
        wiz, _payload = self._run_preview(self.v2_standard, bom_backend="stagecontext_shadow")
        action = wiz.action_create_bom_from_preview()
        bom = self.env["mrp.bom"].browse(action.get("res_id"))
        self.assertTrue(bom.exists())

        corr_op = bom.operation_ids.filtered(lambda op: op.name == "2-Ply Corrugation")[:1]
        self.assertTrue(corr_op)
        self.assertEqual(corr_op.workcenter_id.id, self.wc_corr_feasible.id)
        self.assertNotEqual(corr_op.workcenter_id.id, self.wc_corr_f_only.id)

    def test_bom_workcenter_assignment_respects_disabled_parameter(self):
        icp = self.env["ir.config_parameter"].sudo()
        previous = icp.get_param("corr_engine_v2.auto_assign_operation_workcenter", "1")
        try:
            icp.set_param("corr_engine_v2.auto_assign_operation_workcenter", "0")
            wiz, _payload = self._run_preview(self.v2_standard, bom_backend="stagecontext_shadow")
            action = wiz.action_create_bom_from_preview()
            bom = self.env["mrp.bom"].browse(action.get("res_id"))
            self.assertTrue(bom.exists())
            self.assertFalse(bom.operation_ids.mapped("workcenter_id"))
        finally:
            icp.set_param("corr_engine_v2.auto_assign_operation_workcenter", previous)



