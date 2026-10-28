import unittest
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

from custom_addons.corr_engine_v2.engine_v2 import EngineSpecInput, compute
from custom_addons.corr_engine_v2.engine_v2.bom_adapter import build_bom_structure
from custom_addons.corr_engine_v2.engine_v2.manufacturing_plan import (
    build_manufacturing_plan_from_stagecontext_preview_payload,
    build_materialization_plan_from_manufacturing_plan,
)
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.engine_v2.styles import STYLE_REGISTRY, StyleResult
from custom_addons.corr_engine_v2.engine_v2.spec_input import SurfaceProfileInput
from custom_addons.corr_engine_v2.engine_v2.styles.contract import (
    MissingStyleParameterError,
    require_registered_style,
)
from custom_addons.corr_engine_v2.engine_v2.styles import (
    style_0100,
    style_0110,
    style_0201,
    style_0202,
    style_0203,
    style_diecut_sheet,
)
from custom_addons.corr_engine_v2.preview_adapter import build_preview_model


class TestEngineV2StyleDiecutSheet(unittest.TestCase):
    def _plant(self, stages=None):
        plant = PlantProfile(
            stages=stages or ["2ply", "board", "diecut", "fg"],
            code="STYLE_DIECUT_SHEET",
            name="Diecut Sheet Plant",
            printing_ups=1,
            diecut_ups=1,
            sheet_length_mm=3000.0,
            sheet_width_mm=2000.0,
            default_glue_lap_mm=20.0,
            allow_blank_rotation=True,
            flap_basis="fixed",
            flap_fixed_mm=20.0,
            flap_percent=0.0,
            flap_percent_of="width",
        )
        plant.max_machine_width_mm = 2000.0
        plant.split_mode = "equal_only"
        return plant

    def _spec(self, *, style_code="DIECUT_SHEET", length=1000.0, width=700.0, fg_per_sheet=2):
        spec_input = EngineSpecInput(
            blank_length_mm=0.0,
            blank_width_mm=0.0,
            liner_gsm=180.0,
            medium_gsm=120.0,
            style_code=style_code,
            sfg_mode="staged",
            spec_id=900,
            spec_name=f"{style_code} Spec",
        )
        if style_code == "DIECUT_SHEET":
            spec_input.box_length_mm = length
            spec_input.box_width_mm = width
            spec_input.box_height_mm = 0.0
            spec_input.style_params = {"fg_per_sheet": fg_per_sheet}
        elif style_code == "0100":
            spec_input.box_length_mm = 100000.0
            spec_input.box_width_mm = 1200.0
            spec_input.box_height_mm = 0.0
        elif style_code == "0110":
            spec_input.box_length_mm = 1200.0
            spec_input.box_width_mm = 1000.0
            spec_input.box_height_mm = 0.0
        else:
            spec_input.box_length_mm = 600.0
            spec_input.box_width_mm = 300.0
            spec_input.box_height_mm = 180.0
            spec_input.glue_flap_mm = 30.0
        return spec_input

    def test_style_diecut_sheet_blank_and_yield_formula(self):
        result = style_diecut_sheet.compute_blank(self._spec(), self._plant())

        self.assertIsInstance(result, StyleResult)
        self.assertAlmostEqual(result["blank_length_mm"], 1000.0, places=6)
        self.assertAlmostEqual(result["blank_width_mm"], 700.0, places=6)
        self.assertAlmostEqual(result["seam_mm"], 0.0, places=6)
        self.assertAlmostEqual(result["flap_mm"], 0.0, places=6)
        metadata = result["metadata"]
        self.assertEqual(metadata["style_code"], "DIECUT_SHEET")
        self.assertEqual(metadata["family"], "diecut_sheet")
        self.assertEqual(metadata["fg_per_sheet"], 2)
        self.assertAlmostEqual(metadata["area_m2_per_sheet"], 0.7, places=6)
        self.assertAlmostEqual(metadata["area_m2_per_fg"], 0.35, places=6)
        self.assertEqual(metadata["yield_basis"], "fg_per_sheet")
        self.assertFalse(result["allows_width_split"])
        self.assertFalse(result["allows_length_split"])
        self.assertTrue(result["allows_shared_upstream"])

    def test_style_diecut_sheet_validation(self):
        with self.assertRaisesRegex(MissingStyleParameterError, "Sheet Length > 0"):
            style_diecut_sheet.compute_blank(
                self._spec(length=0.0, width=700.0, fg_per_sheet=2),
                self._plant(),
            )
        with self.assertRaisesRegex(MissingStyleParameterError, "Sheet Width > 0"):
            style_diecut_sheet.compute_blank(
                self._spec(length=1000.0, width=0.0, fg_per_sheet=2),
                self._plant(),
            )
        with self.assertRaisesRegex(MissingStyleParameterError, "integer >= 1"):
            style_diecut_sheet.compute_blank(
                self._spec(length=1000.0, width=700.0, fg_per_sheet=0),
                self._plant(),
            )
        with self.assertRaisesRegex(MissingStyleParameterError, "integer >= 1"):
            style_diecut_sheet.compute_blank(
                self._spec(length=1000.0, width=700.0, fg_per_sheet=2.5),
                self._plant(),
            )

    def test_registry_resolves_diecut_sheet(self):
        handler = require_registered_style("DIECUT_SHEET", STYLE_REGISTRY)
        self.assertIs(handler, style_diecut_sheet.compute_blank)

    def test_compute_uses_area_per_fg_for_rm(self):
        result = compute(self._spec(), self._plant())
        liner = next(rm for rm in result.rm_requirements if rm.role == "liner")
        medium = next(rm for rm in result.rm_requirements if rm.role == "medium")
        metadata = result.metrics.extra["style"]["metadata"]

        self.assertAlmostEqual(result.metrics.m2_per_box, 0.35, places=6)
        self.assertAlmostEqual(liner.qty_per_fg, 0.063, places=6)
        self.assertAlmostEqual(medium.qty_per_fg, 0.042, places=6)
        self.assertEqual(metadata.get("yield_stage_kind"), "diecut")
        self.assertEqual(metadata.get("yield_output_label"), "usable blanks")

    def test_unit_top_sheet_uses_fg_per_sheet_yield(self):
        spec = self._spec(fg_per_sheet=4)
        spec.surface_profile = SurfaceProfileInput(
            top_sheet_mode="unit_sheet",
            top_sheet_product_id=42,
            top_sheet_gsm=200.0,
        )
        result = compute(spec, self._plant())
        top = next(rm for rm in result.rm_requirements if rm.role == "top_sheet_external")

        self.assertAlmostEqual(top.qty_per_fg, 0.25, places=6)
        self.assertEqual(top.metadata.get("yield_basis"), "fg_per_sheet")
        self.assertEqual(top.metadata.get("fg_per_sheet"), 4)

        preview = build_preview_model(
            {
                "stage_contexts": [
                    {"stage_code": "2ply", "stage_name": "2-Ply Corrugation"},
                    {"stage_code": "board", "stage_name": "Board Formation"},
                    {"stage_code": "diecut", "stage_name": "Diecut"},
                    {"stage_code": "fg", "stage_name": "Finished Good"},
                ],
            },
            spec_name="Diecut Sheet",
            style="DIECUT_SHEET",
            plant_name="Diecut Sheet Plant",
            order_qty=4.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )
        top_row = next(row for row in preview["materials"] if row["role"] == "top_sheet_external")
        self.assertAlmostEqual(top_row["qty_per_fg"], 0.25, places=6)
        self.assertAlmostEqual(top_row["total_qty"], 1.0, places=6)

    def test_no_diecut_route_warns_and_preview_uses_whole_sheet_order_rm(self):
        result = compute(self._spec(), self._plant(stages=["2ply", "board", "fg"]))

        preview = build_preview_model(
            {
                "stage_contexts": [
                    {"stage_code": "2ply", "stage_name": "2-ply"},
                    {"stage_code": "board", "stage_name": "Board"},
                    {"stage_code": "fg", "stage_name": "Finished Good"},
                ],
            },
            spec_name="Diecut Sheet",
            style="DIECUT_SHEET",
            plant_name="Diecut Sheet Plant",
            order_qty=1001.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )

        warning = "No diecut stage found. FG-per-sheet yield is applied at the first conversion/terminal stage."
        liner = next(row for row in preview["materials"] if row["role"] == "liner")
        self.assertIn(warning, preview["warnings"])
        self.assertEqual(preview["summary"]["yield"]["required_sheets"], 501)
        self.assertEqual(liner["yield_required_sheets"], 501)
        self.assertAlmostEqual(liner["qty_per_fg"], 0.063, places=6)
        self.assertAlmostEqual(liner["total_qty"], 63.126, places=6)

    def test_diecut_sheet_preview_and_bom_smoke(self):
        result = compute(self._spec(), self._plant())
        preview = build_preview_model(
            {
                "stage_contexts": [
                    {"stage_code": "2ply", "stage_name": "2-ply"},
                    {"stage_code": "board", "stage_name": "Board"},
                    {"stage_code": "diecut", "stage_name": "Diecut"},
                    {"stage_code": "fg", "stage_name": "Finished Good"},
                ],
            },
            spec_name="Diecut Sheet",
            style="DIECUT_SHEET",
            plant_name="Diecut Sheet Plant",
            order_qty=1.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )
        bom = build_bom_structure(result, order_qty=1.0)

        self.assertEqual(preview["summary"]["style"], "DIECUT_SHEET")
        self.assertTrue(preview["materials"])
        self.assertTrue(bom["components"])
        self.assertEqual(bom["product_qty"], 1.0)

    def test_bom_preview_operations_show_whole_sheet_yield_relation_for_one_fg(self):
        result = compute(self._spec(fg_per_sheet=2), self._plant())

        preview = build_preview_model(
            {
                "stage_contexts": [
                    {
                        "stage_code": "corrugator",
                        "stage_name": "Corrugator",
                        "stage_type": "corrugation",
                        "sequence": 10,
                        "input_qty_from_prev": 1.0,
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_code": "diecut",
                        "stage_name": "Diecut",
                        "stage_type": "diecut",
                        "sequence": 20,
                        "input_qty_from_prev": 1.0,
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                    },
                    {
                        "stage_code": "conversion",
                        "stage_name": "Conversion",
                        "stage_type": "conversion",
                        "sequence": 30,
                        "input_qty_from_prev": 1.0,
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                    },
                ],
            },
            spec_name="Diecut Sheet",
            style="DIECUT_SHEET",
            plant_name="Diecut Sheet Plant",
            order_qty=1.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )

        operations = {row["stage"]: row for row in preview["operations"]}

        self.assertAlmostEqual(operations["corrugator"]["output_qty"], 1.0, places=6)
        self.assertAlmostEqual(operations["diecut"]["input_qty"], 1.0, places=6)
        self.assertAlmostEqual(operations["diecut"]["output_qty"], 2.0, places=6)
        self.assertAlmostEqual(operations["conversion"]["input_qty"], 2.0, places=6)
        self.assertAlmostEqual(operations["conversion"]["output_qty"], 1.0, places=6)
        self.assertEqual(operations["diecut"]["yield_output_label"], "usable blanks")
        self.assertEqual(operations["diecut"]["yield_quantity_basis"], "diecut_sheet_required_sheets_ceil")
        self.assertEqual(operations["diecut"]["output_uom"], "unit")
        self.assertEqual(operations["diecut"]["uom"], "unit")
        self.assertEqual(operations["diecut"]["yield_output_kind"], "blank")
        self.assertEqual(operations["conversion"]["uom"], "unit")

    def test_order_level_preview_operations_use_required_sheets_before_yield(self):
        result = compute(self._spec(fg_per_sheet=2), self._plant())

        preview = build_preview_model(
            {
                "stage_contexts": [
                    {
                        "stage_code": "corrugator",
                        "stage_name": "Corrugator",
                        "stage_type": "corrugation",
                        "sequence": 10,
                        "input_qty_from_prev": 1.0,
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_code": "diecut",
                        "stage_name": "Diecut",
                        "stage_type": "diecut",
                        "sequence": 20,
                        "input_qty_from_prev": 1.0,
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                    },
                    {
                        "stage_code": "conversion",
                        "stage_name": "Conversion",
                        "stage_type": "conversion",
                        "sequence": 30,
                        "input_qty_from_prev": 1.0,
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                    },
                ],
            },
            spec_name="Diecut Sheet",
            style="DIECUT_SHEET",
            plant_name="Diecut Sheet Plant",
            order_qty=100.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )

        operations = {row["stage"]: row for row in preview["operations"]}

        self.assertEqual(preview["summary"]["yield"]["required_sheets"], 50)
        self.assertAlmostEqual(operations["corrugator"]["output_qty"], 50.0, places=6)
        self.assertAlmostEqual(operations["diecut"]["input_qty"], 50.0, places=6)
        self.assertAlmostEqual(operations["diecut"]["output_qty"], 100.0, places=6)
        self.assertAlmostEqual(operations["conversion"]["input_qty"], 100.0, places=6)
        self.assertAlmostEqual(operations["conversion"]["output_qty"], 100.0, places=6)
        self.assertEqual(operations["diecut"]["yield_quantity_basis"], "diecut_sheet_required_sheets_ceil")

    def test_selected_route_with_peeloff_shows_gross_and_usable_blank_flow(self):
        result = compute(
            self._spec(fg_per_sheet=2),
            self._plant(stages=["2ply", "board", "diecut", "peeloff", "seaming"]),
        )

        preview = build_preview_model(
            {
                "stage_contexts": [
                    {
                        "stage_code": "2ply",
                        "stage_name": "2-Ply Corrugation",
                        "stage_type": "corrugation",
                        "sequence": 10,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_descriptor": {"kind": "sheet", "quantity": 1.0, "uom": "sheet"},
                    },
                    {
                        "stage_code": "board",
                        "stage_name": "Board Formation",
                        "stage_type": "board",
                        "sequence": 20,
                        "execution_type": "operation",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_code": "diecut",
                        "stage_name": "Die-cut",
                        "stage_type": "diecut",
                        "sequence": 30,
                        "execution_type": "operation",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_code": "peeloff",
                        "stage_name": "Peel-off / Waste Stripping",
                        "stage_type": "peeloff",
                        "sequence": 40,
                        "execution_type": "operation",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_code": "seaming",
                        "stage_name": "Seaming / Jointing",
                        "stage_type": "conversion",
                        "sequence": 50,
                        "execution_type": "material",
                        "output_stock_behavior": "fg",
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                        "output_descriptor": {"kind": "box", "quantity": 1.0, "uom": "unit"},
                    },
                ],
            },
            spec_name="Diecut Sheet",
            style="DIECUT_SHEET",
            plant_name="Diecut Sheet Plant",
            order_qty=1.0,
            metrics=result.metrics,
            rm_requirements=result.rm_requirements,
        )

        routed = preview["routed_operations"]
        self.assertEqual([row["stage_code"] for row in routed], ["2ply", "board", "diecut", "peeloff", "seaming"])
        operations = {row["stage_code"]: row for row in routed}

        self.assertAlmostEqual(operations["2ply"]["output_qty"], 1.0, places=6)
        self.assertEqual(operations["2ply"]["uom"], "sheet")
        self.assertAlmostEqual(operations["board"]["output_qty"], 1.0, places=6)
        self.assertEqual(operations["board"]["uom"], "sheet")
        self.assertAlmostEqual(operations["diecut"]["input_qty"], 1.0, places=6)
        self.assertAlmostEqual(operations["diecut"]["output_qty"], 2.0, places=6)
        self.assertEqual(operations["diecut"]["yield_output_label"], "gross blanks")
        self.assertEqual(operations["diecut"]["uom"], "unit")
        self.assertAlmostEqual(operations["peeloff"]["input_qty"], 2.0, places=6)
        self.assertAlmostEqual(operations["peeloff"]["output_qty"], 2.0, places=6)
        self.assertEqual(operations["peeloff"]["yield_output_label"], "usable blanks")
        self.assertEqual(operations["peeloff"]["uom"], "unit")
        self.assertAlmostEqual(operations["seaming"]["input_qty"], 2.0, places=6)
        self.assertAlmostEqual(operations["seaming"]["output_qty"], 1.0, places=6)
        self.assertEqual(operations["seaming"]["uom"], "unit")

    def test_materialized_bom_preserves_fg_per_sheet_yield_batch_relation(self):
        payload = {
            "spec_id": 900,
            "style_code": "DIECUT_SHEET",
            "order_qty": 1.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "stage_template_id": 10,
                        "stage_contract_code": "route.stage.board",
                        "stage_code": "board",
                        "stage_name": "Board/Corrugator",
                        "stage_type": "corrugation",
                        "sequence": 10,
                        "index": 0,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_kind": "board",
                        "output_descriptor": {"kind": "board", "quantity": 1.0, "uom": "sheet"},
                    },
                    {
                        "stage_template_id": 20,
                        "stage_contract_code": "route.stage.slot_die",
                        "stage_code": "slot_die",
                        "stage_name": "Slot/Die",
                        "stage_type": "diecut",
                        "sequence": 20,
                        "index": 1,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                        "output_kind": "blank",
                        "output_descriptor": {"kind": "blank", "quantity": 1.0, "uom": "unit"},
                        "consumes_previous_output": True,
                        "previous_output_required": True,
                        "input_qty_from_prev": 1.0,
                    },
                    {
                        "stage_template_id": 30,
                        "stage_contract_code": "route.stage.conversion",
                        "stage_code": "conversion",
                        "stage_name": "Conversion",
                        "stage_type": "conversion",
                        "sequence": 30,
                        "index": 2,
                        "execution_type": "material",
                        "output_stock_behavior": "fg",
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                        "output_kind": "box",
                        "output_descriptor": {"kind": "box", "quantity": 1.0, "uom": "unit"},
                        "consumes_previous_output": True,
                        "previous_output_required": True,
                        "input_qty_from_prev": 1.0,
                    },
                ],
            },
            "phase5_preview_model": {
                "routed_operations": [
                    {
                        "stage": "corrugator",
                        "stage_code": "board",
                        "input_qty": 1.0,
                        "output_qty": 1.0,
                        "uom": "sheet",
                    },
                    {
                        "stage": "diecut",
                        "stage_code": "slot_die",
                        "input_qty": 1.0,
                        "output_qty": 2.0,
                        "uom": "unit",
                        "input_uom": "sheet",
                        "output_uom": "unit",
                        "yield_basis": "fg_per_sheet",
                        "fg_per_sheet": 2,
                        "yield_quantity_basis": "whole_sheet_yield_relation",
                        "yield_relation_input_sheets": 1.0,
                        "yield_relation_output_blanks": 2.0,
                        "yield_output_label": "usable blanks",
                        "yield_output_kind": "blank",
                    },
                    {
                        "stage": "conversion",
                        "stage_code": "conversion",
                        "input_qty": 1.0,
                        "output_qty": 1.0,
                        "uom": "unit",
                    },
                ],
            },
        }

        plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        slot_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.slot_die")
        fg_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.conversion")
        board_line = next(line for line in slot_bom.lines if line.line_type == "previous_output")
        slot_line = next(line for line in fg_bom.lines if line.line_type == "previous_output")

        self.assertEqual(slot_bom.product_qty, 2.0)
        self.assertEqual(board_line.source_segment_key, "contract:route.stage.board")
        self.assertEqual(board_line.qty, 1.0)
        self.assertEqual(slot_line.source_segment_key, "contract:route.stage.slot_die")
        self.assertEqual(slot_line.qty, 1.0)
        slot_operation = next(op for op in slot_bom.operations if op.stage_code == "slot_die")
        self.assertEqual(slot_operation.output_qty, 2.0)
        self.assertEqual(slot_operation.uom, "unit")
        self.assertEqual(slot_operation.metadata["yield_output_label"], "usable blanks")

        payload["order_qty"] = 100.0
        yield_row = payload["phase5_preview_model"]["routed_operations"][1]
        yield_row["output_qty"] = 100.0
        yield_row["yield_relation_output_blanks"] = 100.0
        yield_row["yield_quantity_basis"] = "order_required_sheets"
        order_plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        order_materialized = build_materialization_plan_from_manufacturing_plan(order_plan)
        order_slot_bom = next(
            bom for bom in order_materialized.boms if bom.segment_key == "contract:route.stage.slot_die"
        )
        self.assertEqual(order_slot_bom.product_qty, 2.0)

    def test_materialization_prefers_stage_overlay_quantities_when_routed_overlay_is_stale(self):
        payload = {
            "spec_id": 998,
            "style_code": "DIECUT_SHEET",
            "order_qty": 1.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "stage_template_id": 10,
                        "stage_contract_code": "route.stage.board",
                        "stage_code": "board",
                        "stage_name": "Board Formation",
                        "stage_type": "board",
                        "sequence": 10,
                        "index": 0,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_kind": "board",
                        "output_descriptor": {"kind": "board", "quantity": 1.0, "uom": "sheet"},
                    },
                    {
                        "stage_template_id": 20,
                        "stage_contract_code": "route.stage.diecut",
                        "stage_code": "diecut",
                        "stage_name": "Die-cut",
                        "stage_type": "diecut",
                        "sequence": 20,
                        "index": 1,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 2.0,
                        "target_output_uom": "unit",
                        "output_kind": "blank",
                        "output_descriptor": {"kind": "blank", "quantity": 2.0, "uom": "unit"},
                        "consumes_previous_output": True,
                        "previous_output_required": True,
                        "input_qty_from_prev": 1.0,
                    },
                    {
                        "stage_template_id": 30,
                        "stage_contract_code": "route.stage.seaming",
                        "stage_code": "seaming",
                        "stage_name": "Seaming / Jointing",
                        "stage_type": "conversion",
                        "sequence": 30,
                        "index": 2,
                        "execution_type": "material",
                        "output_stock_behavior": "fg",
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                        "output_kind": "box",
                        "output_descriptor": {"kind": "box", "quantity": 1.0, "uom": "unit"},
                        "consumes_previous_output": True,
                        "previous_output_required": True,
                        "input_qty_from_prev": 2.0,
                    },
                ],
            },
            "phase5_preview_model": {
                "routed_operations": [
                    {
                        "stage": "board",
                        "stage_code": "board",
                        "stage_contract_code": "route.stage.board",
                        "stage_template_id": 10,
                        "input_qty": 1.0,
                        "output_qty": 1.0,
                        "uom": "sheet",
                    },
                    {
                        "stage": "diecut",
                        "stage_code": "diecut",
                        "stage_contract_code": "route.stage.diecut",
                        "stage_template_id": 20,
                        "input_qty": 1.0,
                        "output_qty": 1.0,
                        "uom": "sheet",
                        "input_uom": "sheet",
                        "output_uom": "sheet",
                        "yield_basis": "fg_per_sheet",
                        "fg_per_sheet": 2,
                        "yield_quantity_basis": "diecut_sheet_required_sheets_ceil",
                        "yield_relation_input_sheets": 1.0,
                        "yield_relation_output_blanks": 2.0,
                        "yield_output_label": "gross blanks",
                        "yield_output_kind": "blank",
                    },
                    {
                        "stage": "conversion",
                        "stage_code": "seaming",
                        "stage_contract_code": "route.stage.seaming",
                        "stage_template_id": 30,
                        "input_qty": 1.0,
                        "output_qty": 2.0,
                        "uom": "unit",
                        "input_uom": "sheet",
                        "output_uom": "unit",
                        "yield_basis": "fg_per_sheet",
                        "fg_per_sheet": 2,
                        "yield_quantity_basis": "diecut_sheet_required_sheets_ceil",
                        "yield_relation_input_sheets": 1.0,
                        "yield_relation_output_blanks": 2.0,
                        "yield_output_label": "Yield applied before Board Conversion",
                        "yield_output_kind": "blank",
                    },
                ],
                "stages": [
                    {
                        "stage_code": "board",
                        "stage_contract_code": "route.stage.board",
                        "stage_template_id": 10,
                        "input_qty": 0.0,
                        "output_qty": 1.0,
                        "output_uom": "sheet",
                    },
                    {
                        "stage_code": "diecut",
                        "stage_contract_code": "route.stage.diecut",
                        "stage_template_id": 20,
                        "input_qty": 1.0,
                        "output_qty": 2.0,
                        "output_uom": "unit",
                    },
                    {
                        "stage_code": "seaming",
                        "stage_contract_code": "route.stage.seaming",
                        "stage_template_id": 30,
                        "input_qty": 2.0,
                        "output_qty": 1.0,
                        "output_uom": "unit",
                    },
                ],
            },
        }

        plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        diecut_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.diecut")
        fg_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.seaming")
        board_line = next(line for line in diecut_bom.lines if line.line_type == "previous_output")
        fg_line = next(line for line in fg_bom.lines if line.line_type == "previous_output")

        self.assertEqual(diecut_bom.product_qty, 2.0)
        self.assertEqual(board_line.qty, 1.0)
        self.assertEqual(fg_bom.product_qty, 2.0)
        self.assertEqual(fg_line.source_segment_key, "contract:route.stage.diecut")
        self.assertEqual(fg_line.qty, 2.0)

    def test_materialized_bom_promotes_diecut_operation_route_to_yield_boundary(self):
        payload = {
            "spec_id": 900,
            "style_code": "DIECUT_SHEET",
            "order_qty": 1.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "stage_template_id": 10,
                        "stage_contract_code": "route.stage.2ply",
                        "stage_code": "2ply",
                        "stage_name": "2-Ply Corrugation",
                        "stage_type": "corrugation",
                        "sequence": 10,
                        "index": 0,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 2.0,
                        "target_output_uom": "sheet",
                        "output_kind": "sheet",
                        "output_descriptor": {"kind": "sheet", "quantity": 2.0, "uom": "sheet"},
                    },
                    {
                        "stage_template_id": 20,
                        "stage_contract_code": "route.stage.board",
                        "stage_code": "board",
                        "stage_name": "Board Formation",
                        "stage_type": "board",
                        "sequence": 20,
                        "index": 1,
                        "execution_type": "operation",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_template_id": 30,
                        "stage_contract_code": "route.stage.diecut",
                        "stage_code": "diecut",
                        "stage_name": "Die-cut",
                        "stage_type": "diecut",
                        "sequence": 30,
                        "index": 2,
                        "execution_type": "operation",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_template_id": 40,
                        "stage_contract_code": "route.stage.peeloff",
                        "stage_code": "peeloff",
                        "stage_name": "Peel-off / Waste Stripping",
                        "stage_type": "peeloff",
                        "sequence": 40,
                        "index": 3,
                        "execution_type": "operation",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_template_id": 50,
                        "stage_contract_code": "route.stage.seaming",
                        "stage_code": "seaming",
                        "stage_name": "Seaming / Jointing",
                        "stage_type": "conversion",
                        "sequence": 50,
                        "index": 4,
                        "execution_type": "material",
                        "output_stock_behavior": "fg",
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                        "output_kind": "box",
                        "output_descriptor": {"kind": "box", "quantity": 1.0, "uom": "unit"},
                        "consumes_previous_output": True,
                        "previous_output_required": True,
                        "input_qty_from_prev": 1.0,
                    },
                ],
            },
            "phase5_preview_model": {
                "materials": [
                    {
                        "stage_code": "2ply",
                        "stage_contract_code": "route.stage.2ply",
                        "role": "liner",
                        "semantic_role": "liner",
                        "category": "rm",
                        "product_id": 8801,
                        "product_name": "Test Paper",
                        "qty_per_fg": 0.5,
                        "total_qty": 0.5,
                        "uom": "kg",
                    },
                ],
                "routed_operations": [
                    {"stage": "corrugator", "stage_code": "2ply", "input_qty": 1.0, "output_qty": 2.0, "uom": "sheet"},
                    {
                        "stage": "board",
                        "stage_code": "board",
                        "input_qty": 1.0,
                        "output_qty": 1.0,
                        "uom": "sheet",
                    },
                    {
                        "stage": "diecut",
                        "stage_code": "diecut",
                        "input_qty": 1.0,
                        "output_qty": 2.0,
                        "uom": "unit",
                        "input_uom": "sheet",
                        "output_uom": "unit",
                        "yield_basis": "fg_per_sheet",
                        "fg_per_sheet": 2,
                        "yield_quantity_basis": "whole_sheet_yield_relation",
                        "yield_relation_input_sheets": 1.0,
                        "yield_relation_output_blanks": 2.0,
                        "yield_output_label": "gross blanks",
                        "yield_output_kind": "blank",
                    },
                    {
                        "stage": "peeloff",
                        "stage_code": "peeloff",
                        "input_qty": 2.0,
                        "output_qty": 2.0,
                        "uom": "unit",
                        "yield_output_label": "usable blanks",
                    },
                    {
                        "stage": "conversion",
                        "stage_code": "seaming",
                        "input_qty": 2.0,
                        "output_qty": 2.0,
                        "uom": "unit",
                        "yield_basis": "fg_per_sheet",
                        "fg_per_sheet": 2,
                        "yield_quantity_basis": "whole_sheet_yield_relation",
                        "yield_relation_input_sheets": 1.0,
                        "yield_relation_output_blanks": 2.0,
                    },
                ],
            },
        }

        plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        two_ply_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.2ply")
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        slot_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.diecut")
        peel_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.peeloff")
        fg_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.seaming")
        two_ply_line = next(line for line in board_bom.lines if line.line_type == "previous_output")
        board_line = next(line for line in slot_bom.lines if line.line_type == "previous_output")
        gross_line = next(line for line in peel_bom.lines if line.line_type == "previous_output")
        usable_line = next(line for line in fg_bom.lines if line.line_type == "previous_output")
        rm_line = next(line for line in two_ply_bom.lines if line.line_type == "rm_input")

        self.assertEqual(two_ply_bom.product_qty, 1.0)
        self.assertEqual(rm_line.qty, 1.0)
        self.assertEqual(board_bom.product_qty, 1.0)
        self.assertEqual(two_ply_line.source_segment_key, "contract:route.stage.2ply")
        self.assertEqual(two_ply_line.qty, 1.0)
        self.assertEqual(slot_bom.product_qty, 1.0)
        self.assertEqual(board_line.source_segment_key, "contract:route.stage.board")
        self.assertEqual(board_line.qty, 1.0)
        self.assertEqual(peel_bom.product_qty, 2.0)
        self.assertEqual(gross_line.source_segment_key, "contract:route.stage.diecut")
        self.assertEqual(gross_line.qty, 1.0)
        self.assertEqual(fg_bom.product_qty, 2.0)
        self.assertEqual(usable_line.source_segment_key, "contract:route.stage.peeloff")
        self.assertEqual(usable_line.qty, 2.0)

        sfg_payload = deepcopy(payload)
        peel_ctx = sfg_payload["phase8_shadow_runtime"]["stage_contexts"][3]
        peel_ctx.update(
            {
                "output_stock_behavior": "semi_finished",
                "target_output_qty": 2.0,
                "target_output_uom": "unit",
                "output_kind": "blank",
                "output_descriptor": {"kind": "blank", "quantity": 2.0, "uom": "unit"},
                "consumes_previous_output": True,
                "previous_output_required": True,
                "input_qty_from_prev": 2.0,
            }
        )
        peel_row = sfg_payload["phase5_preview_model"]["routed_operations"][3]
        peel_row.update(
            {
                "yield_basis": "fg_per_sheet",
                "fg_per_sheet": 2,
                "yield_quantity_basis": "whole_sheet_yield_relation",
                "yield_relation_input_sheets": 1.0,
                "yield_relation_output_blanks": 2.0,
                "yield_output_kind": "blank",
            }
        )
        sfg_plan = build_manufacturing_plan_from_stagecontext_preview_payload(sfg_payload)
        sfg_materialized = build_materialization_plan_from_manufacturing_plan(sfg_plan)

        sfg_slot_bom = next(bom for bom in sfg_materialized.boms if bom.segment_key == "contract:route.stage.diecut")
        peel_bom = next(bom for bom in sfg_materialized.boms if bom.segment_key == "contract:route.stage.peeloff")
        sfg_fg_bom = next(bom for bom in sfg_materialized.boms if bom.segment_key == "contract:route.stage.seaming")
        sfg_board_line = next(line for line in sfg_slot_bom.lines if line.line_type == "previous_output")
        gross_line = next(line for line in peel_bom.lines if line.line_type == "previous_output")
        usable_line = next(line for line in sfg_fg_bom.lines if line.line_type == "previous_output")

        self.assertEqual(sfg_slot_bom.product_qty, 1.0)
        self.assertEqual(sfg_board_line.source_segment_key, "contract:route.stage.board")
        self.assertEqual(sfg_board_line.qty, 1.0)
        self.assertEqual(peel_bom.product_qty, 2.0)
        self.assertEqual(gross_line.source_segment_key, "contract:route.stage.diecut")
        self.assertEqual(gross_line.qty, 1.0)
        self.assertEqual(sfg_fg_bom.product_qty, 2.0)
        self.assertEqual(usable_line.source_segment_key, "contract:route.stage.peeloff")
        self.assertEqual(usable_line.qty, 2.0)

    def test_materialized_bom_with_peeloff_keeps_diecut_as_gross_sheet_for_fg_per_sheet_3(self):
        payload = {
            "spec_id": 997,
            "style_code": "DIECUT_SHEET",
            "order_qty": 1.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "stage_template_id": 10,
                        "stage_contract_code": "route.stage.board",
                        "stage_code": "board",
                        "stage_name": "Board Formation",
                        "stage_type": "board",
                        "sequence": 10,
                        "index": 0,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_kind": "board",
                        "output_descriptor": {"kind": "board", "quantity": 1.0, "uom": "sheet"},
                    },
                    {
                        "stage_template_id": 20,
                        "stage_contract_code": "route.stage.diecut",
                        "stage_code": "diecut",
                        "stage_name": "Die-cut",
                        "stage_type": "diecut",
                        "sequence": 20,
                        "index": 1,
                        "execution_type": "operation",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_template_id": 30,
                        "stage_contract_code": "route.stage.peeloff",
                        "stage_code": "peeloff",
                        "stage_name": "Peel-off / Waste Stripping",
                        "stage_type": "peeloff",
                        "sequence": 30,
                        "index": 2,
                        "execution_type": "operation",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_template_id": 40,
                        "stage_contract_code": "route.stage.seaming",
                        "stage_code": "seaming",
                        "stage_name": "Seaming / Jointing",
                        "stage_type": "conversion",
                        "sequence": 40,
                        "index": 3,
                        "execution_type": "material",
                        "output_stock_behavior": "fg",
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                        "output_kind": "box",
                        "output_descriptor": {"kind": "box", "quantity": 1.0, "uom": "unit"},
                        "consumes_previous_output": True,
                        "previous_output_required": True,
                        "input_qty_from_prev": 3.0,
                    },
                ],
            },
            "phase5_preview_model": {
                "routed_operations": [
                    {
                        "stage": "board",
                        "stage_code": "board",
                        "stage_contract_code": "route.stage.board",
                        "stage_template_id": 10,
                        "input_qty": 1.0,
                        "output_qty": 1.0,
                        "uom": "sheet",
                    },
                    {
                        "stage": "diecut",
                        "stage_code": "diecut",
                        "stage_contract_code": "route.stage.diecut",
                        "stage_template_id": 20,
                        "input_qty": 1.0,
                        "output_qty": 3.0,
                        "uom": "unit",
                        "input_uom": "sheet",
                        "output_uom": "unit",
                        "yield_basis": "fg_per_sheet",
                        "fg_per_sheet": 3,
                        "yield_quantity_basis": "whole_sheet_yield_relation",
                        "yield_relation_input_sheets": 1.0,
                        "yield_relation_output_blanks": 3.0,
                        "yield_output_label": "gross blanks",
                        "yield_output_kind": "blank",
                    },
                    {
                        "stage": "peeloff",
                        "stage_code": "peeloff",
                        "stage_contract_code": "route.stage.peeloff",
                        "stage_template_id": 30,
                        "input_qty": 3.0,
                        "output_qty": 3.0,
                        "uom": "unit",
                        "yield_basis": "fg_per_sheet",
                        "fg_per_sheet": 3,
                        "yield_quantity_basis": "whole_sheet_yield_relation",
                        "yield_relation_input_sheets": 1.0,
                        "yield_relation_output_blanks": 3.0,
                        "yield_output_label": "usable blanks",
                        "yield_output_kind": "blank",
                    },
                    {
                        "stage": "conversion",
                        "stage_code": "seaming",
                        "stage_contract_code": "route.stage.seaming",
                        "stage_template_id": 40,
                        "input_qty": 3.0,
                        "output_qty": 3.0,
                        "uom": "unit",
                        "input_uom": "unit",
                        "output_uom": "unit",
                        "yield_basis": "fg_per_sheet",
                        "fg_per_sheet": 3,
                        "yield_quantity_basis": "whole_sheet_yield_relation",
                        "yield_relation_input_sheets": 1.0,
                        "yield_relation_output_blanks": 3.0,
                    },
                ],
                "stages": [
                    {
                        "stage_code": "board",
                        "stage_contract_code": "route.stage.board",
                        "stage_template_id": 10,
                        "input_qty": 0.0,
                        "output_qty": 1.0,
                        "output_uom": "sheet",
                    },
                    {
                        "stage_code": "diecut",
                        "stage_contract_code": "route.stage.diecut",
                        "stage_template_id": 20,
                        "input_qty": 1.0,
                        "output_qty": 3.0,
                        "output_uom": "unit",
                    },
                    {
                        "stage_code": "peeloff",
                        "stage_contract_code": "route.stage.peeloff",
                        "stage_template_id": 30,
                        "input_qty": 3.0,
                        "output_qty": 3.0,
                        "output_uom": "unit",
                    },
                    {
                        "stage_code": "seaming",
                        "stage_contract_code": "route.stage.seaming",
                        "stage_template_id": 40,
                        "input_qty": 3.0,
                        "output_qty": 1.0,
                        "output_uom": "unit",
                    },
                ],
            },
        }

        plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        diecut_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.diecut")
        peeloff_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.peeloff")
        fg_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.seaming")
        board_line = next(line for line in diecut_bom.lines if line.line_type == "previous_output")
        gross_line = next(line for line in peeloff_bom.lines if line.line_type == "previous_output")
        usable_line = next(line for line in fg_bom.lines if line.line_type == "previous_output")

        self.assertEqual(diecut_bom.product_qty, 1.0)
        self.assertEqual(board_line.qty, 1.0)
        self.assertEqual(peeloff_bom.product_qty, 3.0)
        self.assertEqual(gross_line.source_segment_key, "contract:route.stage.diecut")
        self.assertEqual(gross_line.qty, 1.0)
        self.assertEqual(fg_bom.product_qty, 3.0)
        self.assertEqual(usable_line.source_segment_key, "contract:route.stage.peeloff")
        self.assertEqual(usable_line.qty, 3.0)

    def test_diecut_sheet_yield_stage_sfg_key_varies_by_fg_per_sheet_but_upstream_sfgs_do_not(self):
        def _payload(fg_per_sheet):
            return {
                "spec_id": 910 + int(fg_per_sheet),
                "style_code": "DIECUT_SHEET",
                "order_qty": 1.0,
                "phase8_shadow_runtime": {
                    "stage_contexts": [
                        {
                            "stage_template_id": 10,
                            "stage_contract_code": "route.stage.2ply",
                            "stage_code": "2ply",
                            "stage_name": "2-Ply Corrugation",
                            "stage_type": "corrugation",
                            "sequence": 10,
                            "index": 0,
                            "execution_type": "material",
                            "output_stock_behavior": "semi_finished",
                            "target_output_qty": 1.0,
                            "target_output_uom": "sheet",
                            "output_kind": "sheet",
                            "output_descriptor": {"kind": "sheet", "quantity": 1.0, "uom": "sheet"},
                        },
                        {
                            "stage_template_id": 20,
                            "stage_contract_code": "route.stage.board",
                            "stage_code": "board",
                            "stage_name": "Board Formation",
                            "stage_type": "board",
                            "sequence": 20,
                            "index": 1,
                            "execution_type": "operation",
                            "target_output_qty": 1.0,
                            "target_output_uom": "sheet",
                        },
                        {
                            "stage_template_id": 30,
                            "stage_contract_code": "route.stage.diecut",
                            "stage_code": "diecut",
                            "stage_name": "Die-cut",
                            "stage_type": "diecut",
                            "sequence": 30,
                            "index": 2,
                            "execution_type": "operation",
                            "target_output_qty": 1.0,
                            "target_output_uom": "sheet",
                        },
                        {
                            "stage_template_id": 40,
                            "stage_contract_code": "route.stage.peeloff",
                            "stage_code": "peeloff",
                            "stage_name": "Peel-off / Waste Stripping",
                            "stage_type": "peeloff",
                            "sequence": 40,
                            "index": 3,
                            "execution_type": "operation",
                            "target_output_qty": 1.0,
                            "target_output_uom": "sheet",
                        },
                        {
                            "stage_template_id": 50,
                            "stage_contract_code": "route.stage.seaming",
                            "stage_code": "seaming",
                            "stage_name": "Seaming / Jointing",
                            "stage_type": "conversion",
                            "sequence": 50,
                            "index": 4,
                            "execution_type": "material",
                            "output_stock_behavior": "fg",
                            "target_output_qty": 1.0,
                            "target_output_uom": "unit",
                            "output_kind": "box",
                            "output_descriptor": {"kind": "box", "quantity": 1.0, "uom": "unit"},
                            "consumes_previous_output": True,
                            "previous_output_required": True,
                            "input_qty_from_prev": float(fg_per_sheet),
                        },
                    ],
                },
                "phase5_preview_model": {
                    "materials": [
                        {
                            "stage_code": "2ply",
                            "stage_contract_code": "route.stage.2ply",
                            "role": "liner",
                            "semantic_role": "liner",
                            "category": "rm",
                            "product_id": 8801,
                            "product_name": "Test Liner",
                            "qty_per_fg": 0.144,
                            "total_qty": 0.144 * float(fg_per_sheet),
                            "uom": "kg",
                        },
                        {
                            "stage_code": "2ply",
                            "stage_contract_code": "route.stage.2ply",
                            "role": "medium",
                            "semantic_role": "medium",
                            "category": "rm",
                            "product_id": 8802,
                            "product_name": "Test Medium",
                            "qty_per_fg": 0.177,
                            "total_qty": 0.177 * float(fg_per_sheet),
                            "uom": "kg",
                        },
                        {
                            "stage_code": "board",
                            "stage_contract_code": "route.stage.board",
                            "role": "top_sheet_external",
                            "semantic_role": "top_sheet_external",
                            "category": "rm",
                            "product_id": 9901,
                            "product_name": "Top Sheet",
                            "qty_per_fg": 1.0,
                            "total_qty": 1.0,
                            "uom": "unit",
                            "default_code": "TOP:10-897-751-00363",
                        },
                    ],
                    "routed_operations": [
                        {"stage": "corrugator", "stage_code": "2ply", "input_qty": 1.0, "output_qty": 1.0, "uom": "sheet"},
                        {"stage": "board", "stage_code": "board", "input_qty": 1.0, "output_qty": 1.0, "uom": "sheet"},
                        {
                            "stage": "diecut",
                            "stage_code": "diecut",
                            "input_qty": 1.0,
                            "output_qty": 1.0,
                            "uom": "sheet",
                            "input_uom": "sheet",
                            "output_uom": "sheet",
                            "yield_basis": "fg_per_sheet",
                            "fg_per_sheet": int(fg_per_sheet),
                            "yield_quantity_basis": "whole_sheet_yield_relation",
                            "yield_relation_input_sheets": 1.0,
                            "yield_relation_output_blanks": float(fg_per_sheet),
                        },
                        {
                            "stage": "peeloff",
                            "stage_code": "peeloff",
                            "input_qty": float(fg_per_sheet),
                            "output_qty": float(fg_per_sheet),
                            "uom": "unit",
                            "input_uom": "sheet",
                            "output_uom": "unit",
                            "yield_basis": "fg_per_sheet",
                            "fg_per_sheet": int(fg_per_sheet),
                            "yield_quantity_basis": "whole_sheet_yield_relation",
                            "yield_relation_input_sheets": 1.0,
                            "yield_relation_output_blanks": float(fg_per_sheet),
                            "yield_output_label": "usable blanks",
                            "yield_output_kind": "blank",
                        },
                        {
                            "stage": "conversion",
                            "stage_code": "seaming",
                            "input_qty": float(fg_per_sheet),
                            "output_qty": float(fg_per_sheet),
                            "uom": "unit",
                            "input_uom": "unit",
                            "output_uom": "unit",
                            "yield_basis": "fg_per_sheet",
                            "fg_per_sheet": int(fg_per_sheet),
                            "yield_quantity_basis": "whole_sheet_yield_relation",
                            "yield_relation_input_sheets": 1.0,
                            "yield_relation_output_blanks": float(fg_per_sheet),
                        },
                    ],
                },
            }

        snapshots = {}
        for fg_per_sheet in (1, 2, 3):
            payload = _payload(fg_per_sheet)
            plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)
            materialized = build_materialization_plan_from_manufacturing_plan(plan)
            products = {product.segment_key: product for product in materialized.products}
            boms = {bom.segment_key: bom for bom in materialized.boms}
            yield_segment_key = next(
                bom.segment_key
                for bom in materialized.boms
                if bom.segment_key not in {"contract:route.stage.2ply", "contract:route.stage.board", "contract:route.stage.seaming"}
                and bom.product_qty == float(fg_per_sheet)
            )
            snapshots[fg_per_sheet] = {
                "2ply_key": products["contract:route.stage.2ply"].corr_sfg_key,
                "board_key": products["contract:route.stage.board"].corr_sfg_key,
                "yield_key": products[yield_segment_key].corr_sfg_key,
                "yield_segment_key": yield_segment_key,
                "board_bom_qty": boms["contract:route.stage.board"].product_qty,
                "yield_bom_qty": boms[yield_segment_key].product_qty,
                "fg_bom_qty": boms["contract:route.stage.seaming"].product_qty,
            }

        self.assertEqual(snapshots[1]["2ply_key"], snapshots[2]["2ply_key"])
        self.assertEqual(snapshots[2]["2ply_key"], snapshots[3]["2ply_key"])
        self.assertEqual(snapshots[1]["board_key"], snapshots[2]["board_key"])
        self.assertEqual(snapshots[2]["board_key"], snapshots[3]["board_key"])

        self.assertNotEqual(snapshots[1]["yield_key"], snapshots[2]["yield_key"])
        self.assertNotEqual(snapshots[2]["yield_key"], snapshots[3]["yield_key"])
        self.assertNotEqual(snapshots[1]["yield_key"], snapshots[3]["yield_key"])

        self.assertEqual(snapshots[1]["board_bom_qty"], 1.0)
        self.assertEqual(snapshots[2]["board_bom_qty"], 1.0)
        self.assertEqual(snapshots[3]["board_bom_qty"], 1.0)
        self.assertEqual(snapshots[1]["yield_bom_qty"], 1.0)
        self.assertEqual(snapshots[2]["yield_bom_qty"], 2.0)
        self.assertEqual(snapshots[3]["yield_bom_qty"], 3.0)
        self.assertEqual(snapshots[1]["fg_bom_qty"], 1.0)
        self.assertEqual(snapshots[2]["fg_bom_qty"], 2.0)
        self.assertEqual(snapshots[3]["fg_bom_qty"], 3.0)

    def test_materialized_bom_keeps_upstream_sheet_boms_whole_for_batch_yield(self):
        payload = {
            "spec_id": 901,
            "style_code": "DIECUT_SHEET",
            "order_qty": 2.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "stage_template_id": 10,
                        "stage_contract_code": "route.stage.2ply",
                        "stage_code": "2ply",
                        "stage_name": "2-Ply Corrugation",
                        "stage_type": "corrugation",
                        "sequence": 10,
                        "index": 0,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 2.0,
                        "target_output_uom": "sheet",
                        "output_descriptor": {"quantity": 2.0, "uom": "sheet"},
                    },
                    {
                        "stage_template_id": 20,
                        "stage_contract_code": "route.stage.board",
                        "stage_code": "board",
                        "stage_name": "Board Formation",
                        "stage_type": "board",
                        "sequence": 20,
                        "index": 1,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_descriptor": {"quantity": 1.0, "uom": "sheet"},
                        "consumes_previous_output": True,
                        "previous_output_required": True,
                        "input_qty_from_prev": 1.0,
                    },
                    {
                        "stage_template_id": 30,
                        "stage_contract_code": "route.stage.diecut",
                        "stage_code": "diecut",
                        "stage_name": "Slot/Die",
                        "stage_type": "diecut",
                        "sequence": 30,
                        "index": 2,
                        "execution_type": "operation",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_template_id": 40,
                        "stage_contract_code": "route.stage.peeloff",
                        "stage_code": "peeloff",
                        "stage_name": "Peel-off / Waste Stripping",
                        "stage_type": "peeloff",
                        "sequence": 40,
                        "index": 3,
                        "execution_type": "operation",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                    },
                    {
                        "stage_template_id": 50,
                        "stage_contract_code": "route.stage.seaming",
                        "stage_code": "seaming",
                        "stage_name": "Seaming / Jointing",
                        "stage_type": "conversion",
                        "sequence": 50,
                        "index": 4,
                        "execution_type": "material",
                        "output_stock_behavior": "fg",
                        "target_output_qty": 1.0,
                        "target_output_uom": "unit",
                        "output_kind": "box",
                        "output_descriptor": {"kind": "box", "quantity": 1.0, "uom": "unit"},
                        "consumes_previous_output": True,
                        "previous_output_required": True,
                        "input_qty_from_prev": 1.0,
                    },
                ],
            },
            "phase5_preview_model": {
                "materials": [
                    {
                        "stage_code": "2ply",
                        "stage_contract_code": "route.stage.2ply",
                        "role": "liner",
                        "semantic_role": "liner",
                        "category": "rm",
                        "product_id": 8801,
                        "product_name": "Test Liner",
                        "qty_per_fg": 0.048,
                        "total_qty": 0.096,
                        "uom": "kg",
                    },
                    {
                        "stage_code": "2ply",
                        "stage_contract_code": "route.stage.2ply",
                        "role": "medium",
                        "semantic_role": "medium",
                        "category": "rm",
                        "product_id": 8802,
                        "product_name": "Test Medium",
                        "qty_per_fg": 0.075,
                        "total_qty": 0.149,
                        "uom": "kg",
                    },
                    {
                        "stage_code": "board",
                        "stage_contract_code": "route.stage.board",
                        "role": "liner",
                        "semantic_role": "liner",
                        "category": "rm",
                        "product_id": 8803,
                        "product_name": "Outer Liner",
                        "qty_per_fg": 0.048,
                        "total_qty": 0.096,
                        "uom": "kg",
                    },
                ],
                "routed_operations": [
                    {"stage": "corrugator", "stage_code": "2ply", "input_qty": 1.0, "output_qty": 2.0, "uom": "sheet"},
                    {"stage": "board", "stage_code": "board", "input_qty": 1.0, "output_qty": 1.0, "uom": "sheet"},
                    {
                        "stage": "diecut",
                        "stage_code": "diecut",
                        "input_qty": 1.0,
                        "output_qty": 2.0,
                        "uom": "unit",
                        "input_uom": "sheet",
                        "output_uom": "unit",
                        "yield_basis": "fg_per_sheet",
                        "fg_per_sheet": 2,
                        "yield_quantity_basis": "whole_sheet_yield_relation",
                        "yield_relation_input_sheets": 1.0,
                        "yield_relation_output_blanks": 2.0,
                        "yield_output_label": "gross blanks",
                        "yield_output_kind": "blank",
                    },
                    {"stage": "peeloff", "stage_code": "peeloff", "input_qty": 2.0, "output_qty": 2.0, "uom": "unit"},
                    {
                        "stage": "conversion",
                        "stage_code": "seaming",
                        "input_qty": 2.0,
                        "output_qty": 2.0,
                        "uom": "unit",
                        "yield_basis": "fg_per_sheet",
                        "fg_per_sheet": 2,
                        "yield_quantity_basis": "whole_sheet_yield_relation",
                        "yield_relation_input_sheets": 1.0,
                        "yield_relation_output_blanks": 2.0,
                    },
                ],
            },
        }

        plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        two_ply_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.2ply")
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        slot_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.diecut")
        peel_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.peeloff")
        fg_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.seaming")
        two_ply_line = next(line for line in board_bom.lines if line.line_type == "previous_output")
        board_line = next(line for line in slot_bom.lines if line.line_type == "previous_output")
        gross_line = next(line for line in peel_bom.lines if line.line_type == "previous_output")
        usable_line = next(line for line in fg_bom.lines if line.line_type == "previous_output")
        rm_lines = [line for line in two_ply_bom.lines if line.line_type == "rm_input"]

        self.assertEqual(slot_bom.product_qty, 1.0)
        self.assertEqual(board_line.qty, 1.0)
        self.assertEqual(gross_line.qty, 1.0)
        self.assertEqual(peel_bom.product_qty, 2.0)
        self.assertEqual(fg_bom.product_qty, 2.0)
        self.assertEqual(usable_line.qty, 2.0)
        self.assertEqual(board_bom.product_qty, 1.0)
        self.assertEqual(two_ply_line.source_segment_key, "contract:route.stage.2ply")
        self.assertEqual(two_ply_line.qty, 1.0)
        self.assertNotEqual(two_ply_line.qty, 0.5)
        self.assertEqual(two_ply_line.metadata["qty_basis_used"], "upstream_yield_sheet_whole_sheet_chain")
        self.assertEqual(two_ply_line.metadata["source_stage_code"], "2ply")
        self.assertEqual(two_ply_line.metadata["target_stage_code"], "board")
        self.assertEqual(two_ply_line.metadata["fg_per_sheet"], 2.0)
        self.assertFalse(two_ply_line.metadata["yield_normalization_applied"])
        self.assertEqual(two_ply_bom.product_qty, 1.0)
        self.assertEqual([round(line.qty, 3) for line in rm_lines], [0.096, 0.149])
        self.assertTrue(
            all(line.metadata.get("qty_basis_used") == "upstream_yield_sheet_raw_qty" for line in rm_lines)
        )

        preview_with_broad_yield_metadata = deepcopy(payload)
        for row in preview_with_broad_yield_metadata["phase5_preview_model"]["routed_operations"]:
            if row["stage_code"] in {"2ply", "board"}:
                row.update(
                    {
                        "yield_basis": "fg_per_sheet",
                        "fg_per_sheet": 2,
                        "input_uom": "sheet",
                        "output_uom": "sheet",
                        "yield_quantity_basis": "whole_sheet_yield_relation",
                        "yield_relation_input_sheets": 1.0,
                        "yield_relation_output_blanks": 2.0,
                    }
                )
        broad_plan = build_manufacturing_plan_from_stagecontext_preview_payload(preview_with_broad_yield_metadata)
        broad_materialized = build_materialization_plan_from_manufacturing_plan(broad_plan)
        broad_two_ply_bom = next(
            bom for bom in broad_materialized.boms if bom.segment_key == "contract:route.stage.2ply"
        )
        broad_board_bom = next(
            bom for bom in broad_materialized.boms if bom.segment_key == "contract:route.stage.board"
        )
        broad_slot_bom = next(
            bom for bom in broad_materialized.boms if bom.segment_key == "contract:route.stage.diecut"
        )
        broad_fg_bom = next(
            bom for bom in broad_materialized.boms if bom.segment_key == "contract:route.stage.seaming"
        )

        self.assertEqual(broad_two_ply_bom.product_qty, 1.0)
        self.assertEqual(broad_board_bom.product_qty, 1.0)
        self.assertEqual(broad_slot_bom.product_qty, 1.0)
        self.assertEqual(broad_fg_bom.product_qty, 2.0)

        payload_with_order_level_prev_qty = deepcopy(preview_with_broad_yield_metadata)
        payload_with_order_level_prev_qty["phase8_shadow_runtime"]["stage_contexts"][0]["target_output_qty"] = 2.0
        payload_with_order_level_prev_qty["phase8_shadow_runtime"]["stage_contexts"][1]["input_qty_from_prev"] = 2.0
        order_level_plan = build_manufacturing_plan_from_stagecontext_preview_payload(
            payload_with_order_level_prev_qty
        )
        order_level_materialized = build_materialization_plan_from_manufacturing_plan(order_level_plan)
        order_level_board_bom = next(
            bom for bom in order_level_materialized.boms if bom.segment_key == "contract:route.stage.board"
        )
        order_level_two_ply_line = next(
            line for line in order_level_board_bom.lines if line.line_type == "previous_output"
        )
        self.assertEqual(order_level_board_bom.product_qty, 1.0)
        self.assertEqual(order_level_two_ply_line.qty, 1.0)

    def test_style_master_and_param_seed_exist(self):
        repo_root = Path(__file__).resolve().parents[3]
        view_path = repo_root / "custom_addons/corr_engine_v2/views/engine_style_views.xml"
        root = ET.parse(view_path).getroot()
        records = {
            record.attrib.get("id"): record
            for record in root.findall(".//record")
        }

        self.assertIn("corr_engine_style_diecut_sheet", records)
        self.assertIn("corr_engine_style_diecut_sheet_param_fg_per_sheet", records)

        style_record = records["corr_engine_style_diecut_sheet"]
        style_fields = {
            field.attrib.get("name"): (field.text or "")
            for field in style_record.findall("field")
        }
        self.assertEqual(style_fields.get("code"), "DIECUT_SHEET")
        self.assertEqual(style_fields.get("family"), "diecut_sheet")

        param_record = records["corr_engine_style_diecut_sheet_param_fg_per_sheet"]
        param_fields = {
            field.attrib.get("name"): (field.text or "")
            for field in param_record.findall("field")
        }
        self.assertEqual(param_fields.get("key"), "fg_per_sheet")
        self.assertEqual(param_fields.get("param_type"), "integer")
        self.assertEqual(param_fields.get("required"), "1")

    def test_no_style_code_leakage_into_guardrailed_modules(self):
        repo_root = Path(__file__).resolve().parents[3]
        forbidden = [
            "custom_addons/corr_engine_v2/engine_v2/stage_graph.py",
            "custom_addons/corr_engine_v2/engine_v2/rm_rules.py",
            "custom_addons/corr_engine_v2/engine_v2/materialization_writer.py",
            "custom_addons/corr_engine_v2/engine_v2/materialization_resolver.py",
            "custom_addons/corr_engine_v2/engine_v2/multi_piece.py",
            "custom_addons/corr_engine_v2/engine_v2/multi_piece_materialization.py",
            "custom_addons/corr_engine_v2/engine_v2/quantity_planner.py",
        ]
        for rel_path in forbidden:
            with self.subTest(path=rel_path):
                text = (repo_root / rel_path).read_text(encoding="utf-8")
                self.assertNotIn("DIECUT_SHEET", text)

    def test_0100_0110_0201_0202_0203_regression_anchors(self):
        plant = self._plant()

        blank_0100 = style_0100.compute_blank(self._spec(style_code="0100"), plant)
        self.assertAlmostEqual(blank_0100["blank_length_mm"], 100000.0, places=6)
        self.assertAlmostEqual(blank_0100["blank_width_mm"], 1200.0, places=6)

        blank_0110 = style_0110.compute_blank(self._spec(style_code="0110"), plant)
        self.assertAlmostEqual(blank_0110["blank_length_mm"], 1200.0, places=6)
        self.assertAlmostEqual(blank_0110["blank_width_mm"], 1000.0, places=6)

        blank_0201 = style_0201.compute_blank(self._spec(style_code="0201"), plant)
        self.assertAlmostEqual(blank_0201["blank_length_mm"], 1830.0, places=6)
        self.assertAlmostEqual(blank_0201["blank_width_mm"], 500.0, places=6)

        spec_0202 = self._spec(style_code="0202")
        spec_0202.style_params = {"overlap_mm": 25.0}
        blank_0202 = style_0202.compute_blank(spec_0202, plant)
        self.assertAlmostEqual(blank_0202["blank_length_mm"], 1830.0, places=6)
        self.assertAlmostEqual(blank_0202["blank_width_mm"], 530.0, places=6)

        blank_0203 = style_0203.compute_blank(self._spec(style_code="0203"), plant)
        self.assertAlmostEqual(blank_0203["blank_length_mm"], 1830.0, places=6)
        self.assertAlmostEqual(blank_0203["blank_width_mm"], 780.0, places=6)


if __name__ == "__main__":
    unittest.main()




