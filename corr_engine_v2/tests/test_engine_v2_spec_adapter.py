import unittest
import xml.etree.ElementTree as ET

from custom_addons.corr_engine_v2.engine_v2 import compute
from custom_addons.corr_engine_v2.engine_v2.board_structure import (
    BoardStructure,
    resolve_board_structure,
)
from custom_addons.corr_engine_v2.engine_v2.plant_profiles import PlantProfile
from custom_addons.corr_engine_v2.spec_adapter import build_engine_spec_input
from custom_addons.corr_engine_v2.engine_v2.materialization_writer import (
    _prepare_product_template_values,
)
from custom_addons.corr_engine_v2.engine_style_display import style_display_name
from custom_addons.corr_engine_v2.engine_v2.v2_spec_adapter import (
    build_v2_engine_spec_input,
)


class TestEngineV2SpecAdapter(unittest.TestCase):
    def test_engine_style_display_name_includes_fefco_code(self):
        self.assertEqual(
            style_display_name("Regular Slotted Container", "0201"),
            "Regular Slotted Container (0201)",
        )
        self.assertEqual(
            style_display_name("Custom Diecut Sheet", "DIECUT_SHEET"),
            "Custom Diecut Sheet",
        )
        self.assertEqual(style_display_name("Custom Style", ""), "Custom Style")

    def test_board_layers_from_stack_lines(self):
        class StackLine:
            def __init__(self, sequence, line_id, role, gsm):
                self.sequence = sequence
                self.id = line_id
                self.role = role
                self.gsm = gsm

        class MockSpec:
            def __init__(self):
                self.id = 1
                self.name = "Multi-ply Spec"
                self.style = "0201"
                self.length_mm = 500.0
                self.width_mm = 400.0
                self.height_mm = 300.0
                self.stack_line_ids = [
                    StackLine(10, 1, "liner", 180.0),
                    StackLine(20, 2, "medium", 120.0),
                    StackLine(30, 3, "liner", 200.0),
                ]
                self.extra_params_json = {}

        spec = MockSpec()
        spec_input = build_engine_spec_input(spec)

        # Board structure should reflect the three stack lines in order.
        board_struct = resolve_board_structure(spec_input)
        self.assertIsInstance(board_struct, BoardStructure)
        self.assertEqual(len(board_struct.layers), 3)

        self.assertEqual(board_struct.layers[0].type, "liner")
        self.assertAlmostEqual(board_struct.layers[0].gsm, 180.0)
        self.assertEqual(board_struct.layers[0].layer_no, 1)

        self.assertEqual(board_struct.layers[1].type, "medium")
        self.assertAlmostEqual(board_struct.layers[1].gsm, 120.0)
        self.assertEqual(board_struct.layers[1].layer_no, 2)

        self.assertEqual(board_struct.layers[2].type, "liner")
        self.assertAlmostEqual(board_struct.layers[2].gsm, 200.0)
        self.assertEqual(board_struct.layers[2].layer_no, 3)

        # Aggregated GSMs on EngineSpecInput should sum per role.
        self.assertAlmostEqual(spec_input.liner_gsm, 180.0 + 200.0)
        self.assertAlmostEqual(spec_input.medium_gsm, 120.0)

    def test_top_sheet_gsm_from_extra_params(self):
        class MockSpec:
            def __init__(self):
                self.id = 2
                self.name = "Top Sheet Spec"
                self.style = "BMC-Custom"
                self.length_mm = 600.0
                self.width_mm = 500.0
                self.height_mm = 0.0
                self.stack_line_ids = []
                self.extra_params_json = {
                    "top_layer_mode": "Unit",
                    "top_sheet_gsm": 150.0,
                }

        spec = MockSpec()
        spec_input = build_engine_spec_input(spec)

        top_gsm = getattr(spec_input, "top_sheet_gsm", 0.0)
        self.assertAlmostEqual(top_gsm, 150.0)

    def test_v2_manual_board_spec_mapping(self):
        # Simulate corr.engine.v2.spec with manual board lines.
        class DummyStyle:
            def __init__(self):
                self.id = 5
                self.code = "0201"
                self.name = "0201 RSC"

        class DummyPlant:
            def __init__(self):
                self.id = 1
                self.code = "PLANT"
                self.name = "Main Plant"

        class DummyFlute:
            def __init__(self, code, tuf=1.4):
                self.code = code
                self.tuf = tuf

        class DummyProduct:
            def __init__(self, default_code, name, product_id=901):
                self.id = product_id
                self.default_code = default_code
                self.name = name
                self.display_name = name
                self.uom_id = type("Uom", (), {"id": 81, "name": "kg", "display_name": "kg"})()

        class DummyTemplate:
            def __init__(self, template_id, name):
                self.id = template_id
                self.name = name

        class DummyAttrValue:
            def __init__(self, value_id):
                self.id = value_id

        class V2StackLine:
            def __init__(
                self,
                seq,
                line_id,
                role,
                gsm,
                bf,
                color,
                flute_code,
                material_code,
                source_type,
                material_template_id=None,
                material_template_name="",
                gsm_value_id=None,
                bf_value_id=None,
                color_value_id=None,
            ):
                self.sequence = seq
                self.id = line_id
                self.role = role
                self.gsm = gsm
                self.bf = bf
                self.color = color
                self.flute_type_id = DummyFlute(flute_code) if flute_code else None
                self.material_product_id = (
                    DummyProduct(material_code, "Paper %s" % material_code) if material_code else None
                )
                self.material_template_id = (
                    DummyTemplate(material_template_id, material_template_name)
                    if material_template_id
                    else None
                )
                self.gsm_value_id = DummyAttrValue(gsm_value_id) if gsm_value_id else None
                self.bf_value_id = DummyAttrValue(bf_value_id) if bf_value_id else None
                self.color_value_id = DummyAttrValue(color_value_id) if color_value_id else None
                self.source_type = source_type

        class V2SpecManual:
            _name = "corr.engine.v2.spec"

            def __init__(self):
                self.id = 10
                self.name = "V2 Manual Board"
                self.style_id = DummyStyle()
                self.plant_id = DummyPlant()
                self.box_length_mm = 300.0
                self.box_width_mm = 200.0
                self.box_height_mm = 150.0
                self.glue_flap_override_mm = 25.0
                self.stack_line_ids = [
                    V2StackLine(10, 1, "outer_liner", 180.0, 30.0, "Kraft", "B", "L180", "manual", 501, "Kraft Paper", 601, 701, 801),
                    V2StackLine(20, 2, "medium", 120.0, 26.0, "Kraft", "B", "M120", "manual", 501, "Kraft Paper", 602, 702, 801),
                    V2StackLine(30, 3, "inner_liner", 200.0, 35.0, "White", "B", "L200", "manual", 502, "Duplex Board", 603, 703, 802),
                ]
                self.surface_profile_id = None
                self.layout_policy_id = None
                self.routing_profile_id = None
                self.manual_sheet_length_mm = 0.0
                self.manual_sheet_width_mm = 0.0
                self.forced_ups = None
                self.ups_enforcement = None

        spec = V2SpecManual()
        spec_input = build_engine_spec_input(spec)

        # Geometry should be populated using V2 field names.
        self.assertEqual(spec_input.style_code, "0201")
        self.assertIsNotNone(spec_input.geometry)
        self.assertEqual(spec_input.geometry.box_length_mm, 300.0)
        self.assertEqual(spec_input.geometry.box_width_mm, 200.0)
        self.assertEqual(spec_input.geometry.box_height_mm, 150.0)
        self.assertEqual(spec_input.geometry.glue_flap_override_mm, 25.0)
        # style_id is the primary identity in GeometrySpec
        self.assertEqual(spec_input.geometry.style_id, 5)
        self.assertEqual(spec_input.box_length_mm, 300.0)
        self.assertEqual(spec_input.box_width_mm, 200.0)
        self.assertEqual(spec_input.box_height_mm, 150.0)

        # Board structure for engine metrics should bucket rich roles into liner/medium.
        board_struct = resolve_board_structure(spec_input)
        self.assertIsInstance(board_struct, BoardStructure)
        self.assertEqual(len(board_struct.layers), 3)
        self.assertEqual(board_struct.layers[0].type, "liner")   # outer_liner
        self.assertEqual(board_struct.layers[1].type, "medium")  # medium
        self.assertEqual(board_struct.layers[2].type, "liner")   # inner_liner

        # Aggregated GSMs should match liner vs medium buckets.
        self.assertAlmostEqual(spec_input.liner_gsm, 180.0 + 200.0)
        self.assertAlmostEqual(spec_input.medium_gsm, 120.0)
        self.assertEqual(spec_input.board_layers_v2[0].material_template_id, 501)
        self.assertEqual(spec_input.board_layers_v2[0].material_template_name, "Kraft Paper")
        self.assertEqual(spec_input.board_layers_v2[0].material_product_id, 901)
        self.assertEqual(spec_input.board_layers_v2[0].material_product_name, "Paper L180")
        self.assertEqual(spec_input.board_layers_v2[0].material_product_uom_id, 81)
        self.assertEqual(spec_input.board_layers_v2[2].material_template_id, 502)
        self.assertEqual(spec_input.board_layers_v2[2].color_value_id, 802)

        # Rich board layer inputs should preserve roles, flute and material info.
        self.assertTrue(hasattr(spec_input, "board_layers_v2"))
        self.assertEqual(len(spec_input.board_layers_v2), 3)
        roles = [l.role for l in spec_input.board_layers_v2]
        self.assertEqual(roles, ["outer_liner", "medium", "inner_liner"])
        flute_codes = {l.flute_type_code for l in spec_input.board_layers_v2}
        self.assertIn("B", flute_codes)
        medium_layer = next(l for l in spec_input.board_layers_v2 if l.role == "medium")
        self.assertAlmostEqual(medium_layer.flute_tuf, 1.4)
        board_struct_medium = next(l for l in board_struct.layers if l.type == "medium")
        self.assertEqual(board_struct_medium.flute_code, "B")
        self.assertAlmostEqual(board_struct_medium.flute_tuf, 1.4)
        material_codes = {l.material_code for l in spec_input.board_layers_v2}
        self.assertIn("L180", material_codes)
        self.assertIn("M120", material_codes)

        # Optional typed sections without profiles or overrides should remain None where applicable.
        self.assertIsNone(spec_input.surface_profile)
        self.assertIsNone(spec_input.layout_policy)
        self.assertIsNone(spec_input.routing_profile)
        # Overrides object exists but carries default/zero values.
        self.assertIsNotNone(spec_input.overrides)
        self.assertEqual(spec_input.overrides.manual_sheet_length_mm, 0.0)
        self.assertEqual(spec_input.overrides.manual_sheet_width_mm, 0.0)

    def test_v2_adapter_facade_uses_v2_spec_mapping(self):
        class V2StackLine:
            def __init__(self):
                self.sequence = 10
                self.id = 1
                self.role = "outer_liner"
                self.gsm = 180.0
                self.bf = 20.0
                self.color = "NS"
                self.flute_type_id = None
                self.material_product_id = None
                self.source_type = "manual"

        class V2Spec:
            _name = "corr.engine.v2.spec"

            def __init__(self):
                self.id = 101
                self.name = "V2 Shell Spec"
                self.style_id = type("S", (), {"id": 9, "code": "0201"})()
                self.plant_id = None
                self.box_length_mm = 700.0
                self.box_width_mm = 800.0
                self.box_height_mm = 300.0
                self.glue_flap_override_mm = 0.0
                self.stack_line_ids = [V2StackLine()]
                self.surface_profile_id = None
                self.layout_policy_id = None
                self.routing_profile_id = None
                self.manual_sheet_length_mm = 0.0
                self.manual_sheet_width_mm = 0.0
                self.forced_ups = None
                self.ups_enforcement = None

        spec_input = build_v2_engine_spec_input(V2Spec())

        self.assertEqual(spec_input.spec_id, 101)
        self.assertEqual(spec_input.spec_name, "V2 Shell Spec")
        self.assertEqual(spec_input.style_code, "0201")
        self.assertEqual(spec_input.geometry.box_length_mm, 700.0)
        self.assertEqual(len(spec_input.board_layers_v2), 1)

    def test_v2_spec_selected_wire_type_maps_to_locking_input(self):
        class Product:
            id = 901
            display_name = "14mm Stitching Wire"
            name = "14mm Stitching Wire"

        class WireType:
            id = 14
            code = "WIRE_14MM"
            name = "14mm"
            product_id = Product()
            stitches_per_cm = 1.8
            g_per_stitch = 0.065

        class V2Spec:
            _name = "corr.engine.v2.spec"

            def __init__(self):
                self.id = 202
                self.name = "V2 Wire Spec"
                self.style_id = type("S", (), {"id": 9, "code": "0201"})()
                self.plant_id = None
                self.box_length_mm = 700.0
                self.box_width_mm = 800.0
                self.box_height_mm = 300.0
                self.glue_flap_override_mm = 0.0
                self.stack_line_ids = []
                self.surface_profile_id = None
                self.layout_policy_id = None
                self.routing_profile_id = None
                self.manual_sheet_length_mm = 0.0
                self.manual_sheet_width_mm = 0.0
                self.forced_ups = None
                self.ups_enforcement = None
                self.joining_type = "stitching"
                self.stitching_wire_type_id = WireType()

        spec_input = build_engine_spec_input(V2Spec())

        self.assertEqual(spec_input.joining_type, "stitching")
        self.assertEqual(spec_input.locking_type.method, "wire")
        self.assertEqual(spec_input.locking_type.code, "WIRE_14MM")
        self.assertEqual(spec_input.locking_type.wire_type_id, 14)
        self.assertEqual(spec_input.locking_type.wire_product_id, 901)
        self.assertEqual(spec_input.locking_type.wire_product_name, "14mm Stitching Wire")
        self.assertAlmostEqual(spec_input.locking_type.wire_stitches_per_cm, 1.8)
        self.assertAlmostEqual(spec_input.locking_type.wire_g_per_stitch, 0.065)

    def test_v2_style_parameter_values_map_to_style_params(self):
        class Param:
            key = "overlap_mm"
            param_type = "float"

        class ParamValue:
            active = True
            key = "overlap_mm"
            param_id = Param()
            value_float = 25.0
            value_int = 0
            value_bool = False
            value_char = ""
            value_selection = ""

        class V2Spec0202:
            _name = "corr.engine.v2.spec"

            def __init__(self):
                self.id = 202
                self.name = "V2 0202 Parameter Spec"
                self.style_id = type("S", (), {"id": 202, "code": "0202"})()
                self.plant_id = None
                self.box_length_mm = 600.0
                self.box_width_mm = 300.0
                self.box_height_mm = 180.0
                self.glue_flap_override_mm = 30.0
                self.stack_line_ids = [
                    type(
                        "Layer",
                        (),
                        {
                            "sequence": 10,
                            "id": 1,
                            "role": "outer_liner",
                            "gsm": 180.0,
                            "bf": 0.0,
                            "color": "",
                            "flute_type_id": None,
                            "material_product_id": None,
                            "source_type": "manual",
                        },
                    )(),
                    type(
                        "Layer",
                        (),
                        {
                            "sequence": 20,
                            "id": 2,
                            "role": "medium",
                            "gsm": 120.0,
                            "bf": 0.0,
                            "color": "",
                            "flute_type_id": None,
                            "material_product_id": None,
                            "source_type": "manual",
                        },
                    )(),
                ]
                self.surface_profile_id = None
                self.layout_policy_id = None
                self.routing_profile_id = None
                self.manual_sheet_length_mm = 0.0
                self.manual_sheet_width_mm = 0.0
                self.forced_ups = None
                self.ups_enforcement = None
                self.style_param_value_ids = [ParamValue()]

        spec_input = build_engine_spec_input(V2Spec0202())

        self.assertEqual(spec_input.style_code, "0202")
        self.assertEqual(getattr(spec_input, "style_params", {}), {"overlap_mm": 25.0})

        plant = PlantProfile(
            stages=["2ply", "board", "fg"],
            code="PARAM",
            name="Parameter Plant",
            printing_ups=1,
            diecut_ups=1,
            default_glue_lap_mm=30.0,
        )
        result = compute(spec_input, plant)

        self.assertEqual(result.metrics.extra["geometry"]["blank_length_mm"], 1830.0)
        self.assertEqual(result.metrics.extra["geometry"]["blank_width_mm"], 530.0)

    def test_v2_persistent_multi_piece_mapping(self):
        class PieceLine:
            def __init__(self, sequence, line_id, code, name, length, width, qty):
                self.sequence = sequence
                self.id = line_id
                self.piece_code = code
                self.piece_name = name
                self.piece_length_mm = length
                self.piece_width_mm = width
                self.piece_qty_per_fg = qty

        class V2SpecWithPieces:
            _name = "corr.engine.v2.spec"

            def __init__(self):
                self.id = 102
                self.name = "V2 Multi Piece"
                self.style_id = type("S", (), {"id": 9, "code": "0201"})()
                self.plant_id = None
                self.box_length_mm = 700.0
                self.box_width_mm = 800.0
                self.box_height_mm = 300.0
                self.glue_flap_override_mm = 0.0
                self.stack_line_ids = []
                self.surface_profile_id = None
                self.layout_policy_id = None
                self.routing_profile_id = None
                self.manual_sheet_length_mm = 0.0
                self.manual_sheet_width_mm = 0.0
                self.forced_ups = None
                self.ups_enforcement = None
                self.multi_piece_enabled = True
                self.multi_piece_source = "accepted_auto_split"
                self.multi_piece_split_axis = "width"
                self.multi_piece_split_mode = "equal"
                self.multi_piece_source_length_mm = 3020.0
                self.multi_piece_source_width_mm = 1120.0
                self.multi_piece_merge_stage_key = "seaming"
                self.multi_piece_line_ids = [
                    PieceLine(10, 1, "A", "Piece A", 3020.0, 560.0, 1.0),
                    PieceLine(20, 2, "B", "Piece B", 3020.0, 560.0, 1.0),
                ]

        spec_input = build_engine_spec_input(V2SpecWithPieces())

        self.assertIsNotNone(spec_input.piece_plan)
        plan = spec_input.piece_plan
        self.assertTrue(plan.enabled)
        self.assertEqual(plan.source, "accepted_auto_split")
        self.assertEqual(plan.split_axis, "width")
        self.assertEqual(plan.split_mode, "equal")
        self.assertEqual(plan.source_length_mm, 3020.0)
        self.assertEqual(plan.source_width_mm, 1120.0)
        self.assertEqual(plan.merge_stage_key, "seaming")
        self.assertEqual([piece.piece_key for piece in plan.pieces], ["piece_a", "piece_b"])
        self.assertEqual([piece.piece_width_mm for piece in plan.pieces], [560.0, 560.0])
        self.assertEqual([piece.piece_length_mm for piece in plan.pieces], [3020.0, 3020.0])

    def test_v2_stale_accepted_multi_piece_is_ignored(self):
        class PieceLine:
            def __init__(self, sequence, line_id, code, name, length, width, qty):
                self.sequence = sequence
                self.id = line_id
                self.piece_code = code
                self.piece_name = name
                self.piece_length_mm = length
                self.piece_width_mm = width
                self.piece_qty_per_fg = qty

        class V2SpecWithStaleAcceptedPieces:
            _name = "corr.engine.v2.spec"

            def __init__(self):
                self.id = 104
                self.name = "V2 Stale Multi Piece"
                self.style_id = type("S", (), {"id": 9, "code": "DIECUT_SHEET"})()
                self.plant_id = None
                self.box_length_mm = 1000.0
                self.box_width_mm = 1340.0
                self.box_height_mm = 0.0
                self.glue_flap_override_mm = 0.0
                self.stack_line_ids = []
                self.surface_profile_id = None
                self.layout_policy_id = None
                self.routing_profile_id = None
                self.manual_sheet_length_mm = 0.0
                self.manual_sheet_width_mm = 0.0
                self.forced_ups = None
                self.ups_enforcement = None
                self.multi_piece_enabled = True
                self.multi_piece_source = "accepted_auto_split"
                self.multi_piece_split_stale = True
                self.multi_piece_split_axis = "width"
                self.multi_piece_split_mode = "equal"
                self.multi_piece_source_length_mm = 1000.0
                self.multi_piece_source_width_mm = 1200.0
                self.multi_piece_merge_stage_key = "seaming"
                self.multi_piece_line_ids = [
                    PieceLine(10, 1, "A", "Piece A", 1000.0, 600.0, 1.0),
                    PieceLine(20, 2, "B", "Piece B", 1000.0, 600.0, 1.0),
                ]

        spec_input = build_engine_spec_input(V2SpecWithStaleAcceptedPieces())

        self.assertIsNone(spec_input.piece_plan)

    def test_v2_persistent_multi_piece_mapping_recovers_zero_source_from_piece_rows(self):
        class PieceLine:
            def __init__(self, sequence, line_id, code, name, length, width, qty):
                self.sequence = sequence
                self.id = line_id
                self.piece_code = code
                self.piece_name = name
                self.piece_length_mm = length
                self.piece_width_mm = width
                self.piece_qty_per_fg = qty

        class V2SpecWithZeroSourcePieces:
            _name = "corr.engine.v2.spec"

            def __init__(self):
                self.id = 103
                self.name = "V2 Multi Piece Zero Source"
                self.style_id = type("S", (), {"id": 9, "code": "0201"})()
                self.plant_id = None
                self.box_length_mm = 700.0
                self.box_width_mm = 800.0
                self.box_height_mm = 300.0
                self.glue_flap_override_mm = 0.0
                self.stack_line_ids = []
                self.surface_profile_id = None
                self.layout_policy_id = None
                self.routing_profile_id = None
                self.manual_sheet_length_mm = 0.0
                self.manual_sheet_width_mm = 0.0
                self.forced_ups = None
                self.ups_enforcement = None
                self.multi_piece_enabled = True
                self.multi_piece_source = "accepted_auto_split"
                self.multi_piece_split_axis = "width"
                self.multi_piece_split_mode = "equal"
                self.multi_piece_source_length_mm = 0.0
                self.multi_piece_source_width_mm = 0.0
                self.multi_piece_merge_stage_key = "seaming"
                self.multi_piece_line_ids = [
                    PieceLine(10, 1, "A", "Piece A", 3020.0, 560.0, 1.0),
                    PieceLine(20, 2, "B", "Piece B", 3020.0, 560.0, 1.0),
                ]

        plan = build_engine_spec_input(V2SpecWithZeroSourcePieces()).piece_plan

        self.assertEqual(plan.source_length_mm, 3020.0)
        self.assertEqual(plan.source_width_mm, 1120.0)
        self.assertEqual([piece.piece_width_mm for piece in plan.pieces], [560.0, 560.0])

    def test_v2_recipe_and_customized_lines_preserved(self):
        # Mix of recipe-derived and customized (manual) lines must all be mapped.
        class V2StackLine:
            def __init__(self, seq, line_id, role, gsm, source_type):
                self.sequence = seq
                self.id = line_id
                self.role = role
                self.gsm = gsm
                self.bf = 0.0
                self.color = ""
                self.flute_type_id = None
                self.material_product_id = None
                self.source_type = source_type

        class V2SpecMixed:
            _name = "corr.engine.v2.spec"

            def __init__(self):
                self.id = 11
                self.name = "V2 Mixed Board"
                self.style_id = type("S", (), {"id": 7, "code": "0201"})()
                self.plant_id = None
                self.box_length_mm = 300.0
                self.box_width_mm = 200.0
                self.box_height_mm = 150.0
                self.glue_flap_override_mm = 0.0
                # First two lines copied from recipe, third edited/custom.
                self.stack_line_ids = [
                    V2StackLine(10, 1, "outer_liner", 150.0, "recipe"),
                    V2StackLine(20, 2, "medium", 100.0, "recipe"),
                    V2StackLine(30, 3, "inner_liner", 180.0, "manual"),
                ]
                self.surface_profile_id = None
                self.layout_policy_id = None
                self.routing_profile_id = None
                self.manual_sheet_length_mm = 0.0
                self.manual_sheet_width_mm = 0.0
                self.forced_ups = None
                self.ups_enforcement = None

        spec = V2SpecMixed()
        spec_input = build_engine_spec_input(spec)

        # All three layers should be present in both board structure and rich inputs.
        board_struct = resolve_board_structure(spec_input)
        self.assertEqual(len(board_struct.layers), 3)
        self.assertEqual(len(spec_input.board_layers_v2), 3)

        # Source types should be carried through for future safety logic.
        source_types = {l.source_type for l in spec_input.board_layers_v2}
        self.assertIn("recipe", source_types)
        self.assertIn("manual", source_types)

    def test_v2_manual_sheet_override_and_policy_mapping(self):
        # Surface, layout and routing profiles should be mapped into typed inputs.
        class SurfaceProfile:
            def __init__(self):
                self.printing_required = True
                self.print_process = "flexo"
                self.print_colors = 3
                self.print_coverage_percent = 75.0
                self.top_sheet_mode = "unit_sheet"
                self.top_sheet_product_id = type("P", (), {"id": 42})()
                self.lamination_required = True
                self.lamination_type_id = type("L", (), {"id": 7})()
                self.lamination_side = "top"

        class LayoutPolicy:
            def __init__(self):
                self.allow_rotation = False
                self.preferred_ups = 4
                self.max_ups = 8
                self.layout_mode = "strict"
                self.sheet_source = "manual"
                self.trim_margin_mm = 10.0
                self.gripper_margin_mm = 5.0

        class RoutingTemplate:
            def __init__(self, code, execution_type, stage_type, output_kind="", can_be_terminal=False, is_fg_stage=False):
                self.code = code
                self.execution_type = execution_type
                self.stage_type = stage_type
                self.output_kind = output_kind
                self.can_be_terminal = can_be_terminal
                self.is_fg_stage = is_fg_stage

        class RoutingStage:
            def __init__(self, seq, line_id, code, stage_type, waste_percent, tmpl=None):
                self.sequence = seq
                self.id = line_id
                self.stage_code = code
                self.stage_type = stage_type
                self.waste_percent = waste_percent
                self.stage_template_id = tmpl

        class RoutingProfile:
            def __init__(self):
                self.name = "V2 Routing"
                self.stage_ids = [
                    RoutingStage(10, 1, "2ply", "material", 2.0, RoutingTemplate("2ply", "material", "corrugation")),
                    RoutingStage(20, 2, "board", "material", 1.0, RoutingTemplate("board", "material", "conversion")),
                    RoutingStage(30, 3, "diecut", "operation", 0.5, RoutingTemplate("diecut", "operation", "diecut", output_kind="box", can_be_terminal=False, is_fg_stage=False)),
                ]

        class V2SpecWithProfiles:
            _name = "corr.engine.v2.spec"

            def __init__(self):
                self.id = 12
                self.name = "V2 With Profiles"
                self.style_id = type("S", (), {"id": 9, "code": "0201"})()
                self.plant_id = None
                self.box_length_mm = 300.0
                self.box_width_mm = 200.0
                self.box_height_mm = 150.0
                self.glue_flap_override_mm = 0.0
                self.stack_line_ids = []
                self.surface_profile_id = SurfaceProfile()
                self.layout_policy_id = LayoutPolicy()
                self.routing_profile_id = RoutingProfile()
                self.manual_sheet_length_mm = 1400.0
                self.manual_sheet_width_mm = 900.0
                self.forced_ups = 2
                self.ups_enforcement = "hard"

        spec = V2SpecWithProfiles()
        spec_input = build_engine_spec_input(spec)

        # Manual sheet overrides should be present both in overrides and as legacy sheet_* attrs.
        self.assertIsNotNone(spec_input.overrides)
        self.assertEqual(spec_input.overrides.manual_sheet_length_mm, 1400.0)
        self.assertEqual(spec_input.overrides.manual_sheet_width_mm, 900.0)
        self.assertEqual(getattr(spec_input, "sheet_length_mm", 0.0), 1400.0)
        self.assertEqual(getattr(spec_input, "sheet_width_mm", 0.0), 900.0)
        self.assertEqual(spec_input.overrides.forced_ups, 2)
        self.assertEqual(spec_input.overrides.ups_enforcement, "hard")

        # Surface profile mapping completeness.
        self.assertIsNotNone(spec_input.surface_profile)
        sp = spec_input.surface_profile
        self.assertTrue(sp.printing_required)
        self.assertEqual(sp.print_process, "flexo")
        self.assertEqual(sp.print_colors, 3)
        self.assertAlmostEqual(sp.print_coverage_percent, 75.0)
        self.assertEqual(sp.top_sheet_mode, "unit_sheet")
        self.assertEqual(sp.top_sheet_product_id, 42)
        self.assertTrue(sp.lamination_required)
        self.assertEqual(sp.lamination_type_id, 7)
        self.assertEqual(sp.lamination_side, "top")

        # Layout policy mapping completeness.
        self.assertIsNotNone(spec_input.layout_policy)
        lp = spec_input.layout_policy
        self.assertFalse(lp.allow_rotation)
        self.assertEqual(lp.preferred_ups, 4)
        self.assertEqual(lp.max_ups, 8)
        self.assertEqual(lp.layout_mode, "strict")
        self.assertEqual(lp.sheet_source, "manual")
        self.assertAlmostEqual(lp.trim_margin_mm, 10.0)
        self.assertAlmostEqual(lp.gripper_margin_mm, 5.0)

        # Routing profile mapping should preserve ordered stages.
        self.assertIsNotNone(spec_input.routing_profile)
        rp = spec_input.routing_profile
        self.assertEqual(rp.name, "V2 Routing")
        self.assertEqual([s.stage_code for s in rp.stages], ["2ply", "board", "diecut"])
        self.assertEqual([s.stage_type for s in rp.stages], ["material", "material", "operation"])
        self.assertEqual([s.stage_semantic for s in rp.stages], ["corrugation", "conversion", "diecut"])
        self.assertEqual([s.output_kind for s in rp.stages], ["", "", "box"])
        self.assertEqual([s.can_be_terminal for s in rp.stages], [False, False, False])
        self.assertEqual([s.is_fg_stage for s in rp.stages], [False, False, False])
        self.assertAlmostEqual(rp.stages[0].waste_percent, 2.0)

    def test_v2_surface_profile_is_template_adapter_uses_spec_copy(self):
        class Product:
            id = 88

        class SharedSurfaceProfile:
            def __init__(self):
                self.printing_required = True
                self.print_process = "flexo"
                self.print_colors = 2
                self.print_coverage_percent = 50.0
                self.top_sheet_mode = "roll"
                self.top_sheet_product_id = None
                self.lamination_required = False
                self.lamination_type_id = None
                self.lamination_side = ""

        class V2Spec:
            _name = "corr.engine.v2.spec"

            def __init__(self):
                self.id = 21
                self.name = "Spec Override"
                self.style_id = type("S", (), {"id": 9, "code": "0201"})()
                self.plant_id = None
                self.box_length_mm = 300.0
                self.box_width_mm = 200.0
                self.box_height_mm = 150.0
                self.glue_flap_override_mm = 0.0
                self.stack_line_ids = []
                self.surface_profile_id = SharedSurfaceProfile()
                self.surface_printing_required = False
                self.surface_print_process = "digital"
                self.surface_print_colors = 4
                self.surface_print_coverage_percent = 80.0
                self.surface_top_sheet_mode = "unit_sheet"
                self.surface_top_sheet_product_id = Product()
                self.surface_lamination_required = True
                self.surface_lamination_type_id = type("L", (), {"id": 12})()
                self.surface_lamination_side = "both"
                self.layout_policy_id = None
                self.routing_profile_id = None
                self.manual_sheet_length_mm = 0.0
                self.manual_sheet_width_mm = 0.0
                self.forced_ups = 0
                self.ups_enforcement = ""

        spec_input = build_engine_spec_input(V2Spec())
        surface = spec_input.surface_profile
        self.assertIsNotNone(surface)
        self.assertFalse(surface.printing_required)
        self.assertEqual(surface.print_process, "digital")
        self.assertEqual(surface.print_colors, 4)
        self.assertAlmostEqual(surface.print_coverage_percent, 80.0)
        self.assertEqual(surface.top_sheet_mode, "unit_sheet")
        self.assertEqual(surface.top_sheet_product_id, 88)
        self.assertTrue(surface.lamination_required)
        self.assertEqual(surface.lamination_type_id, 12)
        self.assertEqual(surface.lamination_side, "both")

    def test_v2_spec_fg_per_sheet_business_field_feeds_style_params(self):
        class V2Spec:
            _name = "corr.engine.v2.spec"

            def __init__(self):
                self.id = 31
                self.name = "Diecut Spec"
                self.style_id = type("S", (), {"id": 9, "code": "DIECUT_SHEET"})()
                self.plant_id = None
                self.box_length_mm = 1000.0
                self.box_width_mm = 700.0
                self.box_height_mm = 0.0
                self.glue_flap_override_mm = 0.0
                self.fg_per_sheet = 3
                self.style_param_value_ids = []
                self.stack_line_ids = []
                self.surface_profile_id = None
                self.layout_policy_id = None
                self.routing_profile_id = None
                self.manual_sheet_length_mm = 0.0
                self.manual_sheet_width_mm = 0.0
                self.forced_ups = 0
                self.ups_enforcement = ""

        spec_input = build_engine_spec_input(V2Spec())
        self.assertEqual(spec_input.style_params["fg_per_sheet"], 3)

    def test_engine_v2_spec_form_has_user_friendly_style_param_value_table(self):
        tree = ET.parse("custom_addons/corr_engine_v2/views/engine_v2_spec_views.xml")
        root = tree.getroot()
        normal_style_groups = [
            group
            for group in root.findall(".//group")
            if group.find(".//field[@name='fg_per_sheet']") is not None
        ]
        self.assertTrue(normal_style_groups)
        normal_xml = ET.tostring(normal_style_groups[0], encoding="unicode")

        self.assertIn('name="fg_per_sheet"', normal_xml)
        self.assertIn('string="Reset Defaults"', normal_xml)
        self.assertIn('name="style_param_value_ids"', normal_xml)
        self.assertIn('name="display_value"', normal_xml)
        for field_name in (
            "value_float",
            "value_int",
            "value_bool",
            "value_char",
            "value_selection",
            "min_value",
            "max_value",
        ):
            self.assertNotIn(f'name="{field_name}"', normal_xml)

    def test_engine_v2_spec_finished_product_domain_allows_fg_and_other_only(self):
        tree = ET.parse("custom_addons/corr_engine_v2/views/engine_v2_spec_views.xml")
        root = tree.getroot()
        product_fields = [
            field
            for field in root.findall(".//field")
            if field.get("name") == "product_tmpl_id" and field.get("domain")
        ]
        self.assertTrue(product_fields)
        domain = product_fields[0].get("domain")
        self.assertIn("product_role", domain)
        self.assertIn("finished_good", domain)
        self.assertIn("other", domain)
        self.assertNotIn("raw_material", domain)
        self.assertNotIn("'sfg'", domain)

    def test_sfg_product_create_values_keep_product_role_sfg(self):
        class Env:
            def __getitem__(self, name):
                raise AssertionError("route lookup should not be needed for this assertion")

        values = _prepare_product_template_values(
            Env(),
            {
                "name": "Engine V2 Board SFG",
                "product_role": "sfg",
                "corr_sfg_key": "sfg:test",
            },
        )
        self.assertEqual(values["product_role"], "sfg")
        self.assertFalse(values["sale_ok"])
        self.assertFalse(values["purchase_ok"])
        self.assertTrue(values["is_storable"])




