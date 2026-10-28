import unittest

from custom_addons.corr_engine_v2.engine_v2.multi_piece_materialization import (
    build_phase_g1_materialization_debug_payload,
    build_phase_g2_routed_piece_materialization_debug_payload,
    build_phase_h2_shared_upstream_materialization_debug_payload,
    build_phase_h_shared_upstream_split_debug_payload,
    build_h2_candidate_summary,
    enrich_phase_g1_piece_rm_requirements,
    segment_phase_g2_route,
    segment_phase_h_shared_upstream_route,
    validate_phase_j3c_write_preconditions,
    validate_phase_h3_write_preconditions,
)
from custom_addons.corr_engine_v2.engine_v2.materialization_writer import (
    MaterializationWriterConfig,
    _find_existing_bom_for_payload,
    _execute_orm_payload_plan,
    build_orm_writer_payload_plan,
    build_materialization_writer_strategy,
)
from custom_addons.corr_engine_v2.engine_v2.materialization_resolver import (
    _resolve_paper_variant_by_product_attributes,
)


class _WriterTestRecord:
    def __init__(self, **values):
        self.__dict__.update(values)


class _WriterTestAttrValue:
    def __init__(self, attribute_name, value):
        self.attribute_id = _WriterTestRecord(name=attribute_name)
        self.name = value


class _WriterTestModel:
    def __init__(self, records):
        self.records = list(records or [])
        self._fields = None

    def sudo(self):
        return self

    def with_context(self, **kwargs):
        return self

    def create(self, values):
        next_id = max([getattr(record, "id", 0) for record in self.records] or [0]) + 1
        record = _WriterTestRecord(id=next_id, **dict(values or {}))
        self.records.append(record)
        return record

    def search(self, domain, limit=None):
        matches = []
        for record in self.records:
            if all(self._matches(record, term) for term in domain):
                matches.append(record)
        if limit:
            matches = matches[:limit]
        return matches[0] if matches else None

    def _matches(self, record, term):
        field, operator, value = term
        actual = getattr(record, field, None)
        if operator == "=":
            return actual == value
        return False

    def browse(self, record_id):
        for record in self.records:
            if getattr(record, "id", None) == record_id:
                return record
        return None


class _WriterTestEnv(dict):
    context = {}

    def ref(self, xmlid, raise_if_not_found=False):
        return None


class TestEngineV2MultiPieceMaterializationG1(unittest.TestCase):
    def _payload(self):
        return {
            "spec_id": 101,
            "style_code": "FG-BOX",
            "fg_product_name": "New EngineV2 Test Pro",
            "selected_layout": {},
            "multi_piece": {
                "enabled": True,
                "source": "accepted_auto_split",
                "status": "accepted_auto_split",
                "piece_count": 2,
                "split_axis": "width",
                "split_mode": "equal",
                "source_length_mm": 3020.0,
                "source_width_mm": 1120.0,
                "merge_stage_key": "seaming",
                "pieces": [
                    {
                        "piece_key": "A",
                        "piece_name": "Piece A",
                        "piece_index": 1,
                        "piece_qty_per_fg": 1.0,
                        "piece_length_mm": 3020.0,
                        "piece_width_mm": 560.0,
                        "stack_source": "inherited",
                        "surface_source": "inherited",
                        "top_sheet_source": "inherited",
                    },
                    {
                        "piece_key": "B",
                        "piece_name": "Piece B",
                        "piece_index": 2,
                        "piece_qty_per_fg": 1.0,
                        "piece_length_mm": 3020.0,
                        "piece_width_mm": 560.0,
                        "stack_source": "inherited",
                        "surface_source": "inherited",
                        "top_sheet_source": "inherited",
                    },
                ],
            },
            "piece_layout_planning": {
                "enabled": True,
                "pieces": [
                    {
                        "piece_key": "A",
                        "selected_option": {
                            "up_count": 1,
                            "final_sheet_length_mm": 3020.0,
                            "final_sheet_width_mm": 560.0,
                            "orientation_used": "direct",
                        },
                    },
                    {
                        "piece_key": "B",
                        "selected_option": {
                            "up_count": 1,
                            "final_sheet_length_mm": 3020.0,
                            "final_sheet_width_mm": 560.0,
                            "orientation_used": "direct",
                        },
                    },
                ],
            },
            "piece_manufacturing_overrides": {
                "enabled": True,
                "pieces": [
                    {
                        "piece_key": "A",
                        "stack": {"source": "inherited"},
                        "surface": {"source": "inherited"},
                        "top_sheet": {"source": "inherited"},
                    },
                    {
                        "piece_key": "B",
                        "stack": {"source": "inherited"},
                        "surface": {"source": "inherited"},
                        "top_sheet": {"source": "inherited"},
                    },
                ],
            },
            "rm_requirements": [
                {
                    "product_key": "liner_a",
                    "role": "liner",
                    "stage_key": "2ply",
                    "layer_no": 1,
                    "qty_per_fg": 0.5,
                    "qty_uom": "kg",
                    "metadata": {
                        "piece_key": "A",
                        "product_id": 11,
                        "semantic_role": "liner",
                    },
                },
                {
                    "product_key": "medium_a",
                    "role": "medium",
                    "stage_key": "2ply",
                    "layer_no": 2,
                    "qty_per_fg": 0.4,
                    "qty_uom": "kg",
                    "metadata": {
                        "piece_key": "A",
                        "product_id": 12,
                        "semantic_role": "medium",
                    },
                },
                {
                    "product_key": "liner_b",
                    "role": "liner",
                    "stage_key": "2ply",
                    "layer_no": 1,
                    "qty_per_fg": 0.5,
                    "qty_uom": "kg",
                    "metadata": {
                        "piece_key": "B",
                        "product_id": 21,
                        "semantic_role": "liner",
                    },
                },
                {
                    "product_key": "medium_b",
                    "role": "medium",
                    "stage_key": "2ply",
                    "layer_no": 2,
                    "qty_per_fg": 0.4,
                    "qty_uom": "kg",
                    "metadata": {
                        "piece_key": "B",
                        "product_id": 22,
                        "semantic_role": "medium",
                    },
                },
                {
                    "product_key": "unit_top_sheet",
                    "role": "top_sheet_external",
                    "stage_key": "printing",
                    "layer_no": 1,
                    "qty_per_fg": 1.0,
                    "qty_uom": "unit",
                    "metadata": {
                        "product_id": 31,
                        "semantic_role": "top_sheet_external",
                        "top_sheet_mode": "unit_sheet",
                    },
                },
            ],
            "phase5_preview_model": {
                "stages": [
                    {
                        "stage_code": "2ply",
                        "stage_name": "2-Ply Corrugation",
                        "stage_type": "corrugation",
                        "execution_type": "material",
                        "sequence": 10,
                        "capability_code": "corrugation",
                        "max_sheet_length_mm": 4000.0,
                        "max_sheet_width_mm": 1200.0,
                    },
                    {
                        "stage_code": "board",
                        "stage_name": "Board Formation",
                        "stage_type": "lamination",
                        "execution_type": "material",
                        "sequence": 20,
                        "capability_code": "board",
                        "max_sheet_length_mm": 4000.0,
                        "max_sheet_width_mm": 900.0,
                    },
                    {
                        "stage_code": "seaming",
                        "stage_name": "Seaming / Jointing",
                        "stage_type": "conversion",
                        "execution_type": "operation",
                        "sequence": 30,
                        "capability_code": "seaming",
                    },
                ]
            },
        }

    def _h1_payload(self):
        payload = self._payload()
        payload["multi_piece"]["multi_piece_strategy"] = "shared_upstream_split"
        payload["multi_piece"]["strategy"] = "shared_upstream_split"
        payload["multi_piece"]["split_after_stage_key"] = "2ply"
        return payload

    def _plan(self, payload=None):
        debug = build_phase_g1_materialization_debug_payload(payload or self._payload())
        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        return debug["materialization_plan"]

    def test_generates_terminal_piece_sfg_product_candidate_keys(self):
        plan = self._plan()

        product_keys = {product["candidate_key"] for product in plan["products"]}

        self.assertIn("product:piece:A:sfg", product_keys)
        self.assertIn("product:piece:B:sfg", product_keys)
        piece_products = [product for product in plan["products"] if product["product_role"] == "sfg"]
        self.assertEqual({product["metadata"]["piece_key"] for product in piece_products}, {"A", "B"})
        self.assertTrue(all(product["corr_sfg_key"] for product in piece_products))

    def test_piece_bom_lines_are_filtered_by_piece_key(self):
        plan = self._plan()
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}

        piece_a_lines = boms["bom:piece:A:terminal"]["lines"]
        piece_b_lines = boms["bom:piece:B:terminal"]["lines"]

        self.assertEqual({line["metadata"]["piece_key"] for line in piece_a_lines}, {"A"})
        self.assertEqual({line["metadata"]["piece_key"] for line in piece_b_lines}, {"B"})
        self.assertEqual({line["input_key"] for line in piece_a_lines}, {"liner_a", "medium_a"})
        self.assertEqual({line["input_key"] for line in piece_b_lines}, {"liner_b", "medium_b"})

    def test_fg_merge_bom_consumes_piece_sfgs_only(self):
        plan = self._plan()
        merge_bom = [bom for bom in plan["boms"] if bom["bom_role"] == "fg"][0]

        self.assertEqual(merge_bom["bom_key"], "bom:fg:multi_piece_merge")
        self.assertEqual({line["line_type"] for line in merge_bom["lines"]}, {"previous_output"})
        self.assertEqual(
            {line["product_candidate_key"] for line in merge_bom["lines"]},
            {"product:piece:A:sfg", "product:piece:B:sfg"},
        )
        self.assertEqual({line["qty"] for line in merge_bom["lines"]}, {1.0})
        self.assertEqual(len(merge_bom["operations"]), 1)
        self.assertEqual(merge_bom["operations"][0]["stage_code"], "seaming")

    def test_fg_merge_bom_has_no_raw_paper_lines(self):
        plan = self._plan()
        merge_bom = [bom for bom in plan["boms"] if bom["bom_role"] == "fg"][0]

        self.assertFalse(
            [
                line
                for line in merge_bom["lines"]
                if line["line_type"] == "rm_input" or line.get("role_code") in {"liner", "medium"}
            ]
        )
        self.assertFalse(merge_bom["metadata"]["raw_paper_lines_allowed"])
        self.assertEqual(merge_bom["metadata"]["raw_paper_line_count"], 0)

    def test_g1_plan_uses_existing_materialized_multi_level_writer_strategy(self):
        plan = self._plan()
        resolver_dry_run = {
            "mode": plan["mode"],
            "products": [
                {
                    "product_candidate_key": product["candidate_key"],
                    "product_role": product["product_role"],
                    "action": "create_product" if product["product_role"] == "sfg" else "reuse_product",
                    "corr_sfg_key": product["corr_sfg_key"],
                }
                for product in plan["products"]
            ],
            "boms": [
                {
                    "bom_candidate_key": bom["bom_key"],
                    "bom_role": bom["bom_role"],
                    "bom_type": bom["bom_type"],
                    "product_candidate_key": bom["product_candidate_key"],
                    "action": "create_bom",
                }
                for bom in plan["boms"]
            ],
            "blockers": [],
        }

        strategy = build_materialization_writer_strategy(
            resolver_dry_run,
            materialization_plan=plan,
            config=MaterializationWriterConfig(
                allow_multi_level_boms=True,
                writer_strategy="materialized_multi_level",
            ),
        )

        self.assertEqual(strategy["strategy"], "materialized_multi_level")
        self.assertEqual(strategy["operation_scope"], "local_bom_operations")
        self.assertEqual(
            [(item["bom_role"], item["writer_family"]) for item in strategy["boundaries"]],
            [
                ("sfg", "materialized_bom_writer"),
                ("sfg", "materialized_bom_writer"),
                ("fg", "materialized_bom_writer"),
            ],
        )

    def test_piece_rm_lines_get_required_deckle_from_piece_layout_width(self):
        payload = self._payload()
        for row in payload["rm_requirements"]:
            row["metadata"].pop("product_id", None)
            row["metadata"]["gsm"] = 180.0
            row["metadata"]["bf"] = 20.0
            row["metadata"]["resolution_status"] = "missing"

        plan = self._plan(payload)
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}
        piece_a_lines = boms["bom:piece:A:terminal"]["lines"]
        piece_b_lines = boms["bom:piece:B:terminal"]["lines"]

        self.assertEqual({line["metadata"]["required_deckle_cm"] for line in piece_a_lines}, {56.0})
        self.assertEqual({line["metadata"]["required_deckle_cm"] for line in piece_b_lines}, {56.0})
        self.assertEqual(
            {line["metadata"]["resolution"]["required_deckle_cm"] for line in piece_a_lines},
            {56.0},
        )

    def test_piece_rm_enrichment_can_resolve_products_with_piece_deckle(self):
        payload = self._payload()
        for row in payload["rm_requirements"]:
            row["metadata"].pop("product_id", None)
            row["metadata"]["gsm"] = 180.0
            row["metadata"]["bf"] = 20.0
            row["metadata"]["resolution_status"] = "missing"

        calls = []

        def resolver(**kwargs):
            calls.append(dict(kwargs))
            return {
                "status": "exact",
                "product_id": 99,
                "product_name": "180 GSM 56 CM Reel",
                "uom_id": 1,
                "uom_name": "kg",
                "candidate_ids": [99],
                "required_deckle_cm": kwargs["deckle_cm"],
            }

        enriched = enrich_phase_g1_piece_rm_requirements(payload, paper_resolver=resolver)

        self.assertTrue(calls)
        self.assertEqual({call["deckle_cm"] for call in calls}, {56.0})
        for row in enriched["rm_requirements"]:
            if row["role"] not in {"liner", "medium"}:
                continue
            metadata = row["metadata"]
            self.assertEqual(metadata["product_id"], 99)
            self.assertEqual(metadata["writer_product_key"], "product.product:99")
            self.assertEqual(metadata["resolution"]["status"], "exact")
            self.assertEqual(metadata["resolution"]["required_deckle_cm"], 56.0)
            self.assertEqual(metadata["resolution_note"], "Exact paper variant resolved")
            self.assertEqual(metadata["resolution"]["resolution_note"], "Exact paper variant resolved")

    def test_guard_blocks_non_inherited_piece_manufacturing(self):
        payload = self._payload()
        payload["multi_piece"]["pieces"][0]["stack_source"] = "override"

        debug = build_phase_g1_materialization_debug_payload(payload)

        self.assertFalse(debug["guard"]["passed"])
        self.assertIn("piece_A_stack_override_not_supported", debug["guard"]["blockers"])
        self.assertEqual(debug["materialization_plan"]["products"], [])
        self.assertEqual(debug["materialization_plan"]["boms"], [])

    def test_j1_inherited_piece_recipe_keeps_g2_materialization_allowed(self):
        payload = self._payload()

        debug = build_phase_g2_routed_piece_materialization_debug_payload(payload)

        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        self.assertTrue(debug["materialization_plan"]["products"])
        self.assertTrue(debug["materialization_plan"]["boms"])

    def test_j3a_custom_stack_preview_payload_allows_g2_dry_run(self):
        payload = self._payload()
        payload["multi_piece"]["pieces"][0]["stack_source"] = "custom"
        payload["piece_manufacturing_overrides"]["pieces"][0].update(
            {
                "stack_source": "custom",
                "stack": {
                    "source": "custom",
                    "effective_stack_lines": [
                        {
                            "sequence": 10,
                            "role": "medium",
                            "gsm": 180.0,
                            "bf": 22.0,
                            "color": "Natural Shade",
                            "flute": "B",
                        }
                    ],
                },
                "recipe_signature": "custom-stack-signature",
            }
        )
        payload["effective_piece_manufacturing"] = list(payload["piece_manufacturing_overrides"]["pieces"])

        debug = build_phase_g2_routed_piece_materialization_debug_payload(payload)

        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        self.assertTrue(debug["materialization_plan"]["products"])
        self.assertTrue(debug["materialization_plan"]["boms"])
        piece_a_products = [
            item for item in debug["materialization_plan"]["products"]
            if (item.get("metadata") or {}).get("piece_key") == "A"
        ]
        self.assertTrue(piece_a_products)
        self.assertEqual(piece_a_products[0]["metadata"]["stack_source"], "custom")
        self.assertEqual(piece_a_products[0]["metadata"]["recipe_signature"], "custom-stack-signature")

    def test_j3a_custom_surface_preview_payload_allows_g2_dry_run(self):
        payload = self._payload()
        payload["multi_piece"]["pieces"][1]["surface_source"] = "custom"
        payload["piece_manufacturing_overrides"]["pieces"][1].update(
            {
                "surface_source": "custom",
                "surface": {
                    "source": "custom",
                    "effective_surface_profile": {
                        "id": 55,
                        "name": "Piece Surface B",
                    },
                },
                "recipe_signature": "custom-surface-signature",
            }
        )
        payload["effective_piece_manufacturing"] = list(payload["piece_manufacturing_overrides"]["pieces"])

        debug = build_phase_g2_routed_piece_materialization_debug_payload(payload)

        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        piece_b_products = [
            item for item in debug["materialization_plan"]["products"]
            if (item.get("metadata") or {}).get("piece_key") == "B"
        ]
        self.assertTrue(piece_b_products)
        self.assertEqual(piece_b_products[0]["metadata"]["surface_source"], "custom")
        self.assertEqual(piece_b_products[0]["metadata"]["effective_surface_profile_id"], 55)
        self.assertEqual(piece_b_products[0]["metadata"]["recipe_signature"], "custom-surface-signature")

    def test_j3a_custom_top_sheet_preview_payload_allows_g2_dry_run(self):
        payload = self._payload()
        payload["multi_piece"]["pieces"][0]["top_sheet_source"] = "custom"
        payload["piece_manufacturing_overrides"]["pieces"][0].update(
            {
                "top_sheet_source": "custom",
                "top_sheet": {
                    "source": "custom",
                    "effective_top_sheet_mode": "unit_sheet",
                    "effective_top_sheet_product": {
                        "id": 88,
                        "name": "Piece A Top Sheet",
                    },
                },
                "recipe_signature": "custom-top-sheet-signature",
            }
        )
        payload["effective_piece_manufacturing"] = list(payload["piece_manufacturing_overrides"]["pieces"])

        debug = build_phase_g2_routed_piece_materialization_debug_payload(payload)

        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        piece_a_products = [
            item for item in debug["materialization_plan"]["products"]
            if (item.get("metadata") or {}).get("piece_key") == "A"
        ]
        self.assertTrue(piece_a_products)
        self.assertEqual(piece_a_products[0]["metadata"]["top_sheet_source"], "custom")
        self.assertEqual(piece_a_products[0]["metadata"]["effective_top_sheet_mode"], "unit_sheet")
        self.assertEqual(piece_a_products[0]["metadata"]["recipe_signature"], "custom-top-sheet-signature")

    def test_j3a_same_custom_recipe_is_reflected_on_both_piece_candidates(self):
        payload = self._payload()
        for piece in payload["multi_piece"]["pieces"]:
            piece["stack_source"] = "custom"
        payload["piece_manufacturing_overrides"]["pieces"] = [
            {
                "piece_key": "A",
                "stack_source": "custom",
                "surface_source": "inherited",
                "top_sheet_source": "inherited",
                "stack": {
                    "source": "custom",
                    "effective_stack_lines": [
                        {"sequence": 10, "role": "medium", "gsm": 180.0, "bf": 22.0, "color": "Natural Shade", "flute": "B"},
                    ],
                },
                "recipe_signature": "same-custom-signature",
            },
            {
                "piece_key": "B",
                "stack_source": "custom",
                "surface_source": "inherited",
                "top_sheet_source": "inherited",
                "stack": {
                    "source": "custom",
                    "effective_stack_lines": [
                        {"sequence": 10, "role": "medium", "gsm": 180.0, "bf": 22.0, "color": "Natural Shade", "flute": "B"},
                    ],
                },
                "recipe_signature": "same-custom-signature",
            },
        ]
        payload["effective_piece_manufacturing"] = list(payload["piece_manufacturing_overrides"]["pieces"])

        debug = build_phase_g2_routed_piece_materialization_debug_payload(payload)

        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        piece_products = [
            item for item in debug["materialization_plan"]["products"]
            if (item.get("metadata") or {}).get("piece_key") in {"A", "B"}
        ]
        signatures = {(item.get("metadata") or {}).get("piece_key"): (item.get("metadata") or {}).get("recipe_signature") for item in piece_products}
        self.assertEqual(signatures["A"], "same-custom-signature")
        self.assertEqual(signatures["B"], "same-custom-signature")

    def test_j3a_piece_a_unit_sheet_piece_b_roll_metadata(self):
        payload = self._payload()
        payload["multi_piece"]["pieces"][0]["top_sheet_source"] = "custom"
        payload["multi_piece"]["pieces"][1]["top_sheet_source"] = "custom"
        payload["piece_manufacturing_overrides"]["pieces"] = [
            {
                "piece_key": "A",
                "stack_source": "inherited",
                "surface_source": "inherited",
                "top_sheet_source": "custom",
                "top_sheet": {
                    "source": "custom",
                    "effective_top_sheet_mode": "unit_sheet",
                    "effective_top_sheet_product": {"id": 88, "name": "Piece A Top Sheet"},
                },
                "recipe_signature": "piece-a-unit",
            },
            {
                "piece_key": "B",
                "stack_source": "inherited",
                "surface_source": "inherited",
                "top_sheet_source": "custom",
                "top_sheet": {
                    "source": "custom",
                    "effective_top_sheet_mode": "roll",
                    "effective_top_sheet_product": {},
                },
                "recipe_signature": "piece-b-roll",
            },
        ]
        payload["effective_piece_manufacturing"] = list(payload["piece_manufacturing_overrides"]["pieces"])

        debug = build_phase_g2_routed_piece_materialization_debug_payload(payload)

        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        piece_products = {
            (item.get("metadata") or {}).get("piece_key"): item
            for item in debug["materialization_plan"]["products"]
            if (item.get("metadata") or {}).get("piece_key") in {"A", "B"}
        }
        self.assertEqual(piece_products["A"]["metadata"]["effective_top_sheet_mode"], "unit_sheet")
        self.assertEqual(piece_products["B"]["metadata"]["effective_top_sheet_mode"], "roll")

    def _j3b_effective_piece(self, piece_key, *, stack_lines=None, surface_id=None, top_sheet_mode="", top_sheet_product_id=None):
        piece = {
            "piece_key": piece_key,
            "stack_source": "inherited" if stack_lines is None else "custom",
            "surface_source": "inherited" if surface_id in (None, 0) else "custom",
            "top_sheet_source": "inherited" if not top_sheet_mode and not top_sheet_product_id else "custom",
            "stack": {
                "source": "inherited" if stack_lines is None else "custom",
                "effective_stack_lines": list(stack_lines or []),
            },
            "surface": {
                "source": "inherited" if surface_id in (None, 0) else "custom",
                "effective_surface_profile": {"id": surface_id} if surface_id else {},
                "effective_surface_profile_id": surface_id,
            },
            "top_sheet": {
                "source": "inherited" if not top_sheet_mode and not top_sheet_product_id else "custom",
                "effective_top_sheet_mode": top_sheet_mode,
                "effective_top_sheet_product": {"id": top_sheet_product_id} if top_sheet_product_id else {},
                "effective_top_sheet_product_id": top_sheet_product_id,
            },
        }
        piece["recipe_signature"] = f"{piece_key}:{surface_id or 0}:{top_sheet_mode or 'none'}:{top_sheet_product_id or 0}:{len(stack_lines or [])}"
        return piece

    def _j3b_payload_with_effective_pieces(self, piece_a_effective, piece_b_effective):
        payload = self._payload()
        pieces = payload["multi_piece"]["pieces"]
        pieces[0]["stack_source"] = piece_a_effective.get("stack_source") or "inherited"
        pieces[0]["surface_source"] = piece_a_effective.get("surface_source") or "inherited"
        pieces[0]["top_sheet_source"] = piece_a_effective.get("top_sheet_source") or "inherited"
        pieces[1]["stack_source"] = piece_b_effective.get("stack_source") or "inherited"
        pieces[1]["surface_source"] = piece_b_effective.get("surface_source") or "inherited"
        pieces[1]["top_sheet_source"] = piece_b_effective.get("top_sheet_source") or "inherited"
        payload["effective_piece_manufacturing"] = [piece_a_effective, piece_b_effective]
        payload["piece_manufacturing_overrides"]["pieces"] = [piece_a_effective, piece_b_effective]
        return payload

    def _j3b_align_piece_rm_products(self, payload, source_piece_key="A", target_piece_key="B"):
        source_rows = [
            row for row in payload["rm_requirements"]
            if row["role"] in {"liner", "medium"} and row["metadata"].get("piece_key") == source_piece_key
        ]
        target_rows = [
            row for row in payload["rm_requirements"]
            if row["role"] in {"liner", "medium"} and row["metadata"].get("piece_key") == target_piece_key
        ]
        source_by_role_layer = {
            (row["role"], row.get("layer_no")): row
            for row in source_rows
        }
        for row in target_rows:
            source = source_by_role_layer.get((row["role"], row.get("layer_no")))
            if not source:
                continue
            row["product_key"] = source.get("product_key")
            row["metadata"]["product_id"] = source["metadata"].get("product_id")
        return payload

    def _j3b_shareable_stage_products(self, plan, stage_key):
        return {
            (item.get("metadata") or {}).get("piece_key"): item
            for item in plan["products"]
            if (item.get("metadata") or {}).get("stage_key") == stage_key
            and not (item.get("metadata") or {}).get("terminal_piece_sfg")
        }

    def _j3b_shareable_stage_boms(self, plan, stage_key):
        return {
            (item.get("metadata") or {}).get("piece_key"): item
            for item in plan["boms"]
            if (item.get("metadata") or {}).get("stage_key") == stage_key
            and not (item.get("metadata") or {}).get("terminal_piece_bom")
        }

    def test_j3b_piece_a_custom_piece_b_inherited_same_effective_recipe_reuses_shareable_sfgs(self):
        stack_lines = [
            {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 20, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
            {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 40, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
        ]
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece("A", stack_lines=stack_lines, top_sheet_mode="unit_sheet", top_sheet_product_id=31),
            self._j3b_effective_piece("B", stack_lines=stack_lines, top_sheet_mode="unit_sheet", top_sheet_product_id=31),
        )
        payload = self._j3b_align_piece_rm_products(payload)

        plan = self._g2_plan(payload)
        products_2ply = self._j3b_shareable_stage_products(plan, "2ply")
        products_board = self._j3b_shareable_stage_products(plan, "board")
        boms_2ply = self._j3b_shareable_stage_boms(plan, "2ply")
        boms_board = self._j3b_shareable_stage_boms(plan, "board")

        self.assertEqual(products_2ply["A"]["corr_sfg_key"], products_2ply["B"]["corr_sfg_key"])
        self.assertEqual(products_board["A"]["corr_sfg_key"], products_board["B"]["corr_sfg_key"])
        self.assertEqual(boms_2ply["A"]["corr_material_signature"], boms_2ply["B"]["corr_material_signature"])
        self.assertEqual(boms_board["A"]["corr_material_signature"], boms_board["B"]["corr_material_signature"])

    def test_j3b_same_stack_different_unit_top_sheet_reuses_2ply_but_splits_board(self):
        stack_lines = [
            {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 20, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
            {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 40, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
        ]
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece("A", stack_lines=stack_lines, top_sheet_mode="unit_sheet", top_sheet_product_id=31),
            self._j3b_effective_piece("B", stack_lines=stack_lines, top_sheet_mode="unit_sheet", top_sheet_product_id=99),
        )
        payload = self._j3b_align_piece_rm_products(payload)

        plan = self._g2_plan(payload)
        products_2ply = self._j3b_shareable_stage_products(plan, "2ply")
        products_board = self._j3b_shareable_stage_products(plan, "board")

        self.assertEqual(products_2ply["A"]["corr_sfg_key"], products_2ply["B"]["corr_sfg_key"])
        self.assertNotEqual(products_board["A"]["corr_sfg_key"], products_board["B"]["corr_sfg_key"])

    def test_j3b_different_stack_separates_2ply_and_board(self):
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece(
                "A",
                stack_lines=[
                    {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
                    {"sequence": 20, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
                    {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
                    {"sequence": 40, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
                ],
                top_sheet_mode="unit_sheet",
                top_sheet_product_id=31,
            ),
            self._j3b_effective_piece(
                "B",
                stack_lines=[
                    {"sequence": 10, "role": "medium", "gsm": 180.0, "bf": 20.0, "color": "Natural Shade", "flute": "E"},
                    {"sequence": 20, "role": "liner", "gsm": 200.0, "bf": 20.0, "color": "Natural Shade"},
                    {"sequence": 30, "role": "medium", "gsm": 180.0, "bf": 20.0, "color": "Natural Shade", "flute": "E"},
                    {"sequence": 40, "role": "liner", "gsm": 200.0, "bf": 20.0, "color": "Natural Shade"},
                ],
                top_sheet_mode="unit_sheet",
                top_sheet_product_id=31,
            ),
        )

        plan = self._g2_plan(payload)
        products_2ply = self._j3b_shareable_stage_products(plan, "2ply")
        products_board = self._j3b_shareable_stage_products(plan, "board")

        self.assertNotEqual(products_2ply["A"]["corr_sfg_key"], products_2ply["B"]["corr_sfg_key"])
        self.assertNotEqual(products_board["A"]["corr_sfg_key"], products_board["B"]["corr_sfg_key"])

    def test_j3c_piece_specific_rm_rows_force_live_resolution_for_2ply_bom_lines(self):
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece(
                "A",
                stack_lines=[
                    {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
                    {"sequence": 20, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
                    {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
                    {"sequence": 40, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
                ],
                top_sheet_mode="unit_sheet",
                top_sheet_product_id=88,
            ),
            self._j3b_effective_piece(
                "B",
                top_sheet_mode="unit_sheet",
                top_sheet_product_id=99,
            ),
        )
        payload["piece_specific_rm_breakdown"] = {
            "rows": [
                {
                    "piece_key": "A",
                    "role": "medium",
                    "stage_key": "2ply_piece_a",
                    "layer_no": 2,
                    "source_layer_no": 1,
                    "qty_per_fg": 0.27,
                    "uom": "kg",
                    "product_id": 160,
                    "product_name": "KP-160-56",
                    "required_deckle_cm": 56,
                    "metadata": {"piece_key": "A", "semantic_role": "medium", "gsm": 160, "bf": 18, "color": "Natural Shade", "flute": "B"},
                },
                {
                    "piece_key": "A",
                    "role": "liner",
                    "stage_key": "2ply_piece_a",
                    "layer_no": 3,
                    "source_layer_no": 2,
                    "qty_per_fg": 0.30,
                    "uom": "kg",
                    "product_id": 180,
                    "product_name": "KP-180-56",
                    "required_deckle_cm": 56,
                    "metadata": {"piece_key": "A", "semantic_role": "liner", "gsm": 180, "bf": 18, "color": "Natural Shade"},
                },
                {
                    "piece_key": "B",
                    "role": "medium",
                    "stage_key": "2ply_piece_b",
                    "layer_no": 2,
                    "source_layer_no": 1,
                    "qty_per_fg": 0.27,
                    "uom": "kg",
                    "product_id": 160,
                    "product_name": "KP-160-56",
                    "required_deckle_cm": 56,
                    "metadata": {"piece_key": "B", "semantic_role": "medium", "gsm": 160, "bf": 18, "color": "Natural Shade", "flute": "B"},
                },
                {
                    "piece_key": "B",
                    "role": "liner",
                    "stage_key": "2ply_piece_b",
                    "layer_no": 3,
                    "source_layer_no": 2,
                    "qty_per_fg": 0.27,
                    "uom": "kg",
                    "product_id": 160,
                    "product_name": "KP-160-56",
                    "required_deckle_cm": 56,
                    "metadata": {"piece_key": "B", "semantic_role": "liner", "gsm": 160, "bf": 18, "color": "Natural Shade"},
                },
            ]
        }

        plan = self._g2_plan(payload)
        two_ply_boms = self._j3b_shareable_stage_boms(plan, "2ply")
        piece_a_paper_lines = [
            line
            for line in two_ply_boms["A"]["lines"]
            if line.get("role_code") in {"liner", "medium"}
        ]
        piece_b_paper_lines = [
            line
            for line in two_ply_boms["B"]["lines"]
            if line.get("role_code") in {"liner", "medium"}
        ]

        self.assertTrue(piece_a_paper_lines)
        self.assertTrue(piece_b_paper_lines)
        self.assertTrue(all(line["metadata"].get("force_live_paper_resolution") for line in piece_a_paper_lines))
        self.assertTrue(all(line["metadata"].get("force_live_paper_resolution") for line in piece_b_paper_lines))
        self.assertEqual({line["metadata"].get("product_id") for line in piece_a_paper_lines}, {None})
        self.assertEqual({line["metadata"].get("product_id") for line in piece_b_paper_lines}, {None})
        self.assertEqual({line["metadata"].get("gsm") for line in piece_a_paper_lines}, {160, 180})
        self.assertEqual({line["metadata"].get("gsm") for line in piece_b_paper_lines}, {160})
        self.assertNotEqual(
            two_ply_boms["A"]["corr_material_signature"],
            two_ply_boms["B"]["corr_material_signature"],
        )

    def test_j3c_piece_specific_mixed_flute_sets_keep_distinct_tuf_quantities(self):
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece("A"),
            self._j3b_effective_piece(
                "B",
                stack_lines=[
                    {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "C"},
                    {"sequence": 15, "role": "liner", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade"},
                    {"sequence": 20, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "F"},
                    {"sequence": 30, "role": "liner", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade"},
                ],
                top_sheet_mode="unit_sheet",
                top_sheet_product_id=99,
            ),
        )
        payload["piece_specific_rm_breakdown"] = {
            "rows": [
                {
                    "piece_key": "B",
                    "role": "medium",
                    "stage_key": "2ply_piece_b",
                    "layer_no": 2,
                    "source_layer_no": 1,
                    "qty_per_fg": 0.08,
                    "uom": "kg",
                    "product_id": 160,
                    "product_name": "KP-160-56",
                    "required_deckle_cm": 56,
                    "metadata": {"piece_key": "B", "semantic_role": "medium", "gsm": 160, "bf": 18, "color": "Natural Shade", "flute": "C"},
                },
                {
                    "piece_key": "B",
                    "role": "liner",
                    "stage_key": "2ply_piece_b",
                    "layer_no": 3,
                    "source_layer_no": 2,
                    "qty_per_fg": 0.27,
                    "uom": "kg",
                    "product_id": 160,
                    "product_name": "KP-160-56",
                    "required_deckle_cm": 56,
                    "metadata": {"piece_key": "B", "semantic_role": "liner", "gsm": 160, "bf": 18, "color": "Natural Shade"},
                },
                {
                    "piece_key": "B",
                    "role": "medium",
                    "stage_key": "2ply_piece_b",
                    "layer_no": 4,
                    "source_layer_no": 3,
                    "qty_per_fg": 0.31,
                    "uom": "kg",
                    "product_id": 160,
                    "product_name": "KP-160-56",
                    "required_deckle_cm": 56,
                    "metadata": {"piece_key": "B", "semantic_role": "medium", "gsm": 160, "bf": 18, "color": "Natural Shade", "flute": "F"},
                },
                {
                    "piece_key": "B",
                    "role": "liner",
                    "stage_key": "2ply_piece_b",
                    "layer_no": 5,
                    "source_layer_no": 4,
                    "qty_per_fg": 0.27,
                    "uom": "kg",
                    "product_id": 160,
                    "product_name": "KP-160-56",
                    "required_deckle_cm": 56,
                    "metadata": {"piece_key": "B", "semantic_role": "liner", "gsm": 160, "bf": 18, "color": "Natural Shade"},
                },
            ]
        }

        plan = self._g2_plan(payload)
        piece_b_2ply_boms = [
            bom for bom in plan["boms"]
            if str(bom.get("product_candidate_key") or "").startswith("product:piece:B:stage:2ply")
        ]
        medium_by_flute = {}
        for bom in piece_b_2ply_boms:
            for line in bom["lines"]:
                if line.get("role_code") == "medium":
                    medium_by_flute[line["metadata"].get("flute")] = line.get("qty")

        self.assertEqual(len(piece_b_2ply_boms), 2)
        self.assertEqual(medium_by_flute, {"C": 0.08, "F": 0.31})

    def test_j3c_piece_specific_resolution_overrides_stale_layer_level_parent_rm(self):
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece(
                "A",
                stack_lines=[
                    {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
                    {"sequence": 20, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
                    {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
                    {"sequence": 40, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
                ],
                top_sheet_mode="unit_sheet",
                top_sheet_product_id=88,
            ),
            self._j3b_effective_piece("B", top_sheet_mode="unit_sheet", top_sheet_product_id=99),
        )
        payload["rm_requirements"] = [
            {
                "product_key": "stale_a_medium_%s" % layer,
                "role": "medium",
                "stage_key": "2ply",
                "layer_no": layer,
                "qty_per_fg": 0.27,
                "qty_uom": "kg",
                "metadata": {"piece_key": "A", "product_id": 160, "semantic_role": "medium"},
            }
            for layer in (2, 4)
        ] + [
            {
                "product_key": "stale_a_liner_%s" % layer,
                "role": "liner",
                "stage_key": "2ply",
                "layer_no": layer,
                "qty_per_fg": 0.30,
                "qty_uom": "kg",
                "metadata": {"piece_key": "A", "product_id": 160, "semantic_role": "liner"},
            }
            for layer in (3, 5)
        ] + [
            {
                "product_key": "b_%s_%s" % (role, layer),
                "role": role,
                "stage_key": "2ply",
                "layer_no": layer,
                "qty_per_fg": 0.27,
                "qty_uom": "kg",
                "metadata": {"piece_key": "B", "product_id": 160, "semantic_role": role},
            }
            for role, layer in (("medium", 2), ("liner", 3), ("medium", 4), ("liner", 5))
        ]
        payload["piece_specific_rm_breakdown"] = {
            "rows": [
                {
                    "piece_key": "A",
                    "role": role,
                    "stage_key": "2ply_piece_a",
                    "layer_no": layer,
                    "source_layer_no": source_layer,
                    "qty_per_fg": 0.27 if role == "medium" else 0.30,
                    "uom": "kg",
                    "product_id": product_id,
                    "product_name": "KP-%s-56" % product_id,
                    "required_deckle_cm": 56,
                    "metadata": {
                        "piece_key": "A",
                        "semantic_role": role,
                        "gsm": 160 if role == "medium" else 180,
                        "bf": 18,
                        "color": "Natural Shade",
                        "flute": "B" if role == "medium" else "",
                    },
                }
                for role, layer, source_layer, product_id in (
                    ("medium", 2, 1, 160),
                    ("liner", 3, 2, 180),
                    ("medium", 4, 3, 160),
                    ("liner", 5, 4, 180),
                )
            ]
        }

        plan = self._g2_plan(payload)
        two_ply_boms = self._j3b_shareable_stage_boms(plan, "2ply")
        piece_a_lines = [
            line for line in two_ply_boms["A"]["lines"]
            if line.get("role_code") in {"liner", "medium"}
        ]

        self.assertEqual({line["metadata"].get("product_id") for line in piece_a_lines}, {None})
        self.assertTrue(all(line["metadata"].get("force_live_paper_resolution") for line in piece_a_lines))
        self.assertEqual({line["metadata"].get("required_deckle_cm") for line in piece_a_lines}, {56})
        self.assertTrue(
            all(
                line["metadata"].get("gsm") == 180
                for line in piece_a_lines
                if line.get("role_code") == "liner"
            )
        )

    def test_j3b_different_surface_same_stack_reuses_2ply_but_splits_board(self):
        stack_lines = [
            {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 20, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
            {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 40, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
        ]
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece("A", stack_lines=stack_lines, surface_id=55, top_sheet_mode="unit_sheet", top_sheet_product_id=31),
            self._j3b_effective_piece("B", stack_lines=stack_lines, surface_id=77, top_sheet_mode="unit_sheet", top_sheet_product_id=31),
        )
        payload = self._j3b_align_piece_rm_products(payload)

        plan = self._g2_plan(payload)
        products_2ply = self._j3b_shareable_stage_products(plan, "2ply")
        products_board = self._j3b_shareable_stage_products(plan, "board")

        self.assertEqual(products_2ply["A"]["corr_sfg_key"], products_2ply["B"]["corr_sfg_key"])
        self.assertNotEqual(products_board["A"]["corr_sfg_key"], products_board["B"]["corr_sfg_key"])

    def test_j3b_piece_a_unit_sheet_piece_b_roll_separates_2ply_and_board(self):
        stack_lines = [
            {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 20, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
            {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 40, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
        ]
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece("A", stack_lines=stack_lines, top_sheet_mode="unit_sheet", top_sheet_product_id=31),
            self._j3b_effective_piece("B", stack_lines=stack_lines, top_sheet_mode="roll"),
        )
        payload["rm_requirements"].append(
            {
                "product_key": "top_roll_b",
                "role": "top_roll",
                "stage_key": "2ply",
                "layer_no": 1,
                "qty_per_fg": 0.25,
                "qty_uom": "kg",
                "metadata": {
                    "piece_key": "B",
                    "product_id": 222,
                    "semantic_role": "top_roll",
                },
            }
        )

        plan = self._g2_plan(payload)
        products_2ply = self._j3b_shareable_stage_products(plan, "2ply")
        products_board = self._j3b_shareable_stage_products(plan, "board")

        self.assertNotEqual(products_2ply["A"]["corr_sfg_key"], products_2ply["B"]["corr_sfg_key"])
        self.assertNotEqual(products_board["A"]["corr_sfg_key"], products_board["B"]["corr_sfg_key"])

    def test_j3c_write_preconditions_allow_valid_independent_custom_piece_flow(self):
        stack_lines = [
            {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 20, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
            {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 40, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
        ]
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece("A", stack_lines=stack_lines, top_sheet_mode="unit_sheet", top_sheet_product_id=31),
            self._j3b_effective_piece("B"),
        )

        guard = validate_phase_j3c_write_preconditions(payload)

        self.assertTrue(guard["passed"], guard)
        self.assertEqual(guard["custom_piece_keys"], ["A"])

    def test_j3c_write_preconditions_block_shared_upstream_custom_piece_flow(self):
        payload = self._h1_payload()
        payload["piece_manufacturing_overrides"]["pieces"][0].update(
            {
                "stack_source": "custom",
                "stack": {
                    "source": "custom",
                    "effective_stack_lines": [
                        {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
                    ],
                },
                "recipe_signature": "custom-a",
            }
        )
        payload["effective_piece_manufacturing"] = list(payload["piece_manufacturing_overrides"]["pieces"])

        guard = validate_phase_j3c_write_preconditions(payload)

        self.assertFalse(guard["passed"])
        self.assertIn("shared_upstream_split_custom_piece_materialization_not_supported", guard["blockers"])

    def test_j3c_write_preconditions_block_unequal_split_custom_piece_flow(self):
        payload = self._payload()
        payload["multi_piece"]["split_mode"] = "uneven"
        payload["piece_manufacturing_overrides"]["pieces"][0].update(
            {
                "stack_source": "custom",
                "stack": {
                    "source": "custom",
                    "effective_stack_lines": [
                        {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
                    ],
                },
                "recipe_signature": "custom-a",
            }
        )
        payload["effective_piece_manufacturing"] = list(payload["piece_manufacturing_overrides"]["pieces"])

        guard = validate_phase_j3c_write_preconditions(payload)

        self.assertFalse(guard["passed"])
        self.assertIn("custom_piece_materialization_supports_equal_split_only", guard["blockers"])

    def test_j3c_same_custom_recipe_keeps_shareable_stage_keys_identical(self):
        stack_lines = [
            {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 20, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
            {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 40, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
        ]
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece("A", stack_lines=stack_lines, top_sheet_mode="unit_sheet", top_sheet_product_id=31),
            self._j3b_effective_piece("B", stack_lines=stack_lines, top_sheet_mode="unit_sheet", top_sheet_product_id=31),
        )
        payload = self._j3b_align_piece_rm_products(payload)

        plan = self._g2_plan(payload)
        payload_plan = self._writer_payload_for_plan(plan)
        create_vals = {
            item["candidate_key"]: item["values"]
            for item in payload_plan["product_create_vals"]
        }

        self.assertEqual(
            create_vals["product:piece:A:stage:2ply"]["corr_sfg_key"],
            create_vals["product:piece:B:stage:2ply"]["corr_sfg_key"],
        )
        self.assertEqual(
            create_vals["product:piece:A:stage:board"]["corr_sfg_key"],
            create_vals["product:piece:B:stage:board"]["corr_sfg_key"],
        )

    def test_j3c_same_stack_different_top_sheet_splits_board_only_in_writer_inputs(self):
        stack_lines = [
            {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 20, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
            {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 40, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
        ]
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece("A", stack_lines=stack_lines, top_sheet_mode="unit_sheet", top_sheet_product_id=31),
            self._j3b_effective_piece("B", stack_lines=stack_lines, top_sheet_mode="unit_sheet", top_sheet_product_id=99),
        )
        payload = self._j3b_align_piece_rm_products(payload)

        plan = self._g2_plan(payload)
        payload_plan = self._writer_payload_for_plan(plan)
        create_vals = {
            item["candidate_key"]: item["values"]
            for item in payload_plan["product_create_vals"]
        }

        self.assertEqual(
            create_vals["product:piece:A:stage:2ply"]["corr_sfg_key"],
            create_vals["product:piece:B:stage:2ply"]["corr_sfg_key"],
        )
        self.assertNotEqual(
            create_vals["product:piece:A:stage:board"]["corr_sfg_key"],
            create_vals["product:piece:B:stage:board"]["corr_sfg_key"],
        )

    def test_j3c_actual_plan_uses_effective_piece_stack_when_expanding_collapsed_rows(self):
        custom_stack_lines = [
            {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 20, "role": "inner_liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
            {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 40, "role": "inner_liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
        ]
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece("A", stack_lines=custom_stack_lines, top_sheet_mode="unit_sheet", top_sheet_product_id=31),
            self._j3b_effective_piece("B"),
        )

        plan = self._g2_plan(payload)
        products = {product["candidate_key"]: product for product in plan["products"]}
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}

        self.assertNotEqual(
            products["product:piece:A:stage:2ply"]["corr_sfg_key"],
            products["product:piece:B:stage:2ply"]["corr_sfg_key"],
        )
        self.assertNotEqual(
            products["product:piece:A:stage:board"]["corr_sfg_key"],
            products["product:piece:B:stage:board"]["corr_sfg_key"],
        )
        self.assertNotEqual(
            boms["bom:piece:A:stage:2ply"]["corr_material_signature"],
            boms["bom:piece:B:stage:2ply"]["corr_material_signature"],
        )

    def test_j3c_g2_plan_prefers_piece_specific_resolved_rm_rows_over_stale_rm_requirements(self):
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece(
                "A",
                stack_lines=[
                    {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
                    {"sequence": 20, "role": "inner_liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
                ],
                top_sheet_mode="unit_sheet",
                top_sheet_product_id=31,
            ),
            self._j3b_effective_piece("B", top_sheet_mode="unit_sheet", top_sheet_product_id=31),
        )
        # Simulate stale raw_json: legacy rm_requirements no longer carry the
        # piece deckle/product resolution, but the J2 preview rows do.
        for row in payload["rm_requirements"]:
            metadata = row.setdefault("metadata", {})
            if metadata.get("piece_key") in {"A", "B"} and row.get("role") in {"liner", "medium"}:
                metadata.pop("product_id", None)
                metadata.pop("writer_product_key", None)
                metadata.pop("required_deckle_cm", None)
                metadata["resolution"] = {}
        payload["piece_specific_rm_breakdown"] = {
            "enabled": True,
            "rows": [
                {
                    "piece_key": "A",
                    "piece_index": 1,
                    "role": "medium",
                    "stage_code": "2ply",
                    "layer_no": 2,
                    "qty_per_fg": 0.27,
                    "uom": "kg",
                    "metadata": {
                        "piece_key": "A",
                        "piece_index": 1,
                        "role": "medium",
                        "type": "medium",
                        "gsm": 160.0,
                        "bf": 18.0,
                        "color": "Natural Shade",
                        "flute_code": "B",
                        "layer_no": 2,
                        "source_layer_no": 1,
                        "required_deckle_cm": 56.0,
                        "product_id": 16056,
                        "product_name": "[KP-160-56-NS-18] Kraft Paper",
                        "uom_id": 1,
                        "resolution": {"product_id": 16056, "required_deckle_cm": 56.0, "status": "resolved"},
                    },
                },
                {
                    "piece_key": "B",
                    "piece_index": 2,
                    "role": "liner",
                    "stage_code": "2ply",
                    "layer_no": 3,
                    "qty_per_fg": 0.30,
                    "uom": "kg",
                    "metadata": {
                        "piece_key": "B",
                        "piece_index": 2,
                        "role": "liner",
                        "type": "liner",
                        "gsm": 180.0,
                        "bf": 18.0,
                        "color": "Natural Shade",
                        "layer_no": 3,
                        "source_layer_no": 2,
                        "required_deckle_cm": 56.0,
                        "product_id": 18056,
                        "product_name": "[KP-180-56-NS-18] Kraft Paper",
                        "uom_id": 1,
                        "resolution": {"product_id": 18056, "required_deckle_cm": 56.0, "status": "resolved"},
                    },
                },
            ],
        }

        debug = build_phase_g2_routed_piece_materialization_debug_payload(payload)
        plan = debug["materialization_plan"]
        lines = [
            line
            for bom in plan["boms"]
            if bom["bom_key"] in {"bom:piece:A:stage:2ply", "bom:piece:B:stage:2ply"}
            for line in list(bom.get("lines") or [])
            if line.get("line_type") == "rm_input"
        ]

        self.assertTrue(lines)
        self.assertTrue(all((line.get("metadata") or {}).get("required_deckle_cm") == 56.0 for line in lines))
        self.assertTrue(all((line.get("metadata") or {}).get("force_live_paper_resolution") for line in lines))
        self.assertEqual(
            {((line.get("metadata") or {}).get("piece_key"), (line.get("metadata") or {}).get("product_id")) for line in lines},
            {("A", None), ("B", None)},
        )

    def test_j3c_piece_effective_stack_clears_stale_resolved_product_before_write(self):
        piece_a_stack = [
            {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 20, "role": "inner_liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
            {"sequence": 30, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "B"},
            {"sequence": 40, "role": "inner_liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade"},
        ]
        payload = self._j3b_payload_with_effective_pieces(
            self._j3b_effective_piece("A", stack_lines=piece_a_stack, top_sheet_mode="unit_sheet", top_sheet_product_id=517),
            self._j3b_effective_piece("B", top_sheet_mode="unit_sheet", top_sheet_product_id=394),
        )
        stale_160_name = "[KP-160-56-NS-18] Kraft Paper (160, 56, Natural Shade(NS), 18)"
        correct_180_name = "[KP-180-56-NS-18] Kraft Paper (180, 56, Natural Shade(NS), 18)"
        rows = []
        for piece_key, piece_index, liner_gsm in (("A", 1, 180.0), ("B", 2, 160.0)):
            for layer_no, source_layer_no, role, gsm, flute in (
                (2, 1, "medium", 160.0, "B"),
                (3, 2, "liner", liner_gsm, ""),
                (4, 3, "medium", 160.0, "B"),
                (5, 4, "liner", liner_gsm, ""),
            ):
                display_name = correct_180_name if piece_key == "A" and role == "liner" else stale_160_name
                rows.append(
                    {
                        "piece_key": piece_key,
                        "piece_index": piece_index,
                        "role": role,
                        "stage_code": f"2ply_piece_{piece_key.lower()}",
                        "layer_no": layer_no,
                        "source_layer_no": source_layer_no,
                        "gsm": gsm,
                        "bf": 18.0,
                        "color": "Natural Shade",
                        "flute": flute,
                        "qty_per_fg": 0.27 if role == "medium" else 0.30,
                        "uom": "kg",
                        "product_id": 16056,
                        "product": display_name,
                        "product_name": display_name,
                        "product_tmpl_id": 160560,
                        "metadata": {
                            "piece_key": piece_key,
                            "piece_index": piece_index,
                            "role": role,
                            "type": role,
                            "gsm": gsm,
                            "bf": 18.0,
                            "color": "Natural Shade",
                            "flute_code": flute,
                            "layer_no": layer_no,
                            "source_layer_no": source_layer_no,
                            "required_deckle_cm": 56.0,
                            "product_id": 16056,
                            "product_name": stale_160_name,
                            "product_tmpl_id": 160560,
                            "writer_product_key": "product.product:16056",
                            "resolution": {
                                "product_id": 16056,
                                "product_name": stale_160_name,
                                "required_deckle_cm": 56.0,
                                "status": "resolved",
                            },
                        },
                    }
                )
        payload["piece_specific_rm_breakdown"] = {
            "enabled": True,
            "rows": rows,
        }

        debug = build_phase_g2_routed_piece_materialization_debug_payload(payload)
        plan = debug["materialization_plan"]
        products = {product["candidate_key"]: product for product in plan["products"]}
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}
        piece_a_2ply_lines = [
            line for line in boms["bom:piece:A:stage:2ply"]["lines"]
            if line.get("line_type") == "rm_input"
        ]
        piece_a_liner_lines = [
            line for line in piece_a_2ply_lines
            if (line.get("metadata") or {}).get("semantic_role") == "liner"
        ]

        self.assertNotEqual(
            products["product:piece:A:stage:2ply"]["corr_sfg_key"],
            products["product:piece:B:stage:2ply"]["corr_sfg_key"],
        )
        self.assertTrue(piece_a_liner_lines)
        self.assertTrue(all((line.get("metadata") or {}).get("gsm") == 180.0 for line in piece_a_liner_lines))
        self.assertTrue(all((line.get("metadata") or {}).get("force_live_paper_resolution") for line in piece_a_liner_lines))
        self.assertEqual({(line.get("metadata") or {}).get("product_id") for line in piece_a_liner_lines}, {None})
        self.assertEqual({(line.get("metadata") or {}).get("writer_product_key") for line in piece_a_liner_lines}, {""})
        self.assertTrue(all("KP-160" not in str((line.get("metadata") or {}).get("product_name") or "") for line in piece_a_liner_lines))

    def test_writer_does_not_duplicate_operations_when_same_bom_is_repaired_for_two_candidates(self):
        env = _WriterTestEnv(
            {
                "mrp.bom": _WriterTestModel(
                    [
                        _WriterTestRecord(
                            id=501,
                            product_tmpl_id=100,
                            corr_engine_v2_signature="shareable-signature",
                            corr_sfg_key="engine_v2:multi_piece:g2:mfg:2ply:shared",
                            bom_line_ids=[],
                            operation_ids=[],
                        )
                    ]
                ),
                "mrp.bom.line": _WriterTestModel([]),
                "mrp.routing.workcenter": _WriterTestModel([]),
                "product.template": _WriterTestModel([]),
                "product.product": _WriterTestModel([_WriterTestRecord(id=31, uom_id=_WriterTestRecord(id=1))]),
            }
        )
        payload_plan = {
            "bom_create_values": [
                {
                    "candidate_key": "bom:piece:A:stage:2ply",
                    "model": "mrp.bom",
                    "values": {
                        "product_tmpl_id": 100,
                        "corr_engine_v2_signature": "shareable-signature",
                        "corr_sfg_key": "engine_v2:multi_piece:g2:mfg:2ply:shared",
                        "_corr_strategy_type": "independent_routed_piece",
                        "_corr_shareable_branch_sfg": True,
                        "_corr_expected_line_count": 1,
                        "_corr_expected_operation_count": 1,
                    },
                },
                {
                    "candidate_key": "bom:piece:B:stage:2ply",
                    "model": "mrp.bom",
                    "values": {
                        "product_tmpl_id": 100,
                        "corr_engine_v2_signature": "shareable-signature",
                        "corr_sfg_key": "engine_v2:multi_piece:g2:mfg:2ply:shared",
                        "_corr_strategy_type": "independent_routed_piece",
                        "_corr_shareable_branch_sfg": True,
                        "_corr_expected_line_count": 1,
                        "_corr_expected_operation_count": 1,
                    },
                },
            ],
            "bom_line_create_values": [
                {
                    "candidate_key": "line:a",
                    "parent_bom_candidate_key": "bom:piece:A:stage:2ply",
                    "model": "mrp.bom.line",
                    "values": {"product_id": 31, "product_uom_id": 1, "product_qty": 1.0},
                },
                {
                    "candidate_key": "line:b",
                    "parent_bom_candidate_key": "bom:piece:B:stage:2ply",
                    "model": "mrp.bom.line",
                    "values": {"product_id": 31, "product_uom_id": 1, "product_qty": 1.0},
                },
            ],
            "operation_create_values": [
                {
                    "candidate_key": "op:a",
                    "parent_bom_candidate_key": "bom:piece:A:stage:2ply",
                    "model": "mrp.routing.workcenter",
                    "values": {"name": "2-Ply Corrugation", "time_cycle_manual": 1.0, "capacity": 1.0},
                },
                {
                    "candidate_key": "op:b",
                    "parent_bom_candidate_key": "bom:piece:B:stage:2ply",
                    "model": "mrp.routing.workcenter",
                    "values": {"name": "2-Ply Corrugation", "time_cycle_manual": 1.0, "capacity": 1.0},
                },
            ],
            "product_template_create_values": [],
            "product_template_sync_values": [],
        }

        result = _execute_orm_payload_plan(env, payload_plan, {"products": [], "boms": [], "blockers": []})

        self.assertEqual(len(env["mrp.bom.line"].records), 1)
        self.assertEqual(len(env["mrp.routing.workcenter"].records), 1)
        self.assertEqual(len(result["repaired_boms"]), 2)
        self.assertTrue(
            any(
                row.get("reason") == "target_bom_operations_already_written_in_same_execution"
                for row in result["skipped_payloads"]
            )
        )

    def test_guard_blocks_nested_piece_layout_ups(self):
        payload = self._payload()
        payload["piece_layout_planning"]["pieces"][0]["selected_option"]["up_count"] = 2

        debug = build_phase_g1_materialization_debug_payload(payload)

        self.assertFalse(debug["guard"]["passed"])
        self.assertIn("piece_A_layout_ups_must_be_1", debug["guard"]["blockers"])

    def test_g2_route_segmentation_finds_branch_merge_and_terminal_stages(self):
        segmentation = segment_phase_g2_route(self._payload())

        self.assertEqual(segmentation["branch_stage_keys"], ["2ply", "board"])
        self.assertEqual(segmentation["merge_stage_key"], "seaming")
        self.assertTrue(segmentation["merge_stage_found"])
        self.assertEqual(segmentation["terminal_stage_key"], "board")

    def test_h1_route_segmentation_finds_shared_split_branch_and_merge(self):
        segmentation = segment_phase_h_shared_upstream_route(self._h1_payload())

        self.assertEqual(segmentation["shared_stage_keys"], ["2ply"])
        self.assertEqual(segmentation["split_after_stage_key"], "2ply")
        self.assertTrue(segmentation["split_after_stage_found"])
        self.assertEqual(segmentation["post_split_branch_stage_keys"], ["board"])
        self.assertEqual(segmentation["terminal_piece_stage_key"], "board")
        self.assertTrue(segmentation["merge_stage_found"])

    def test_h1_debug_payload_includes_allocation_and_split_outputs(self):
        debug = build_phase_h_shared_upstream_split_debug_payload(self._h1_payload())

        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        self.assertEqual(debug["strategy"], "shared_upstream_split")
        self.assertEqual(debug["status"], "allowed")
        self.assertEqual(debug["source_blank"], {"length_mm": 3020.0, "width_mm": 1120.0})
        self.assertEqual({row["piece_key"] for row in debug["split_outputs"]}, {"A", "B"})
        self.assertEqual(
            {round(row["percentage"], 1) for row in debug["allocation_percentages"]},
            {50.0},
        )
        self.assertEqual(debug["allocation"], debug["allocation_percentages"])
        self.assertEqual(debug["products"], [])
        self.assertEqual(debug["boms"], [])
        self.assertEqual(debug["mos"], [])

    def test_h1_capacity_reads_wrapped_stage_feasibility_stages(self):
        payload = self._h1_payload()
        payload["stage_feasibility"] = {
            "stages": [
                {
                    "stage_key": "2ply",
                    "stage_code": "2ply",
                    "candidate_workcenters": [
                        {
                            "workcenter_id": 10,
                            "workcenter_name": "Corrugator",
                            "width_max_cm": 120.0,
                            "length_max_mm": 4000.0,
                        }
                    ],
                },
                {
                    "stage_key": "board",
                    "stage_code": "board",
                    "candidate_workcenters": [
                        {
                            "workcenter_id": 20,
                            "workcenter_name": "Board",
                            "width_max_cm": 60.0,
                            "length_max_mm": 4000.0,
                        }
                    ],
                },
            ],
            "summary": {"stage_count": 2},
        }

        debug = build_phase_h_shared_upstream_split_debug_payload(payload)

        self.assertEqual(debug["strategy"], "shared_upstream_split")
        self.assertNotIn("capacity_check_failed", " ".join(debug["guard"]["blockers"]))
        self.assertTrue(debug["diagnostics"]["capacity_checks"])
        self.assertEqual(
            {check["status"] for check in debug["diagnostics"]["capacity_checks"]},
            {"feasible"},
        )

    def test_h1_capacity_reports_missing_workcenter_mapping(self):
        payload = self._h1_payload()
        for stage in payload["phase5_preview_model"]["stages"]:
            stage.pop("max_sheet_length_mm", None)
            stage.pop("max_sheet_width_mm", None)
        payload["stage_feasibility"] = {"stages": [], "summary": {"stage_count": 0}}

        debug = build_phase_h_shared_upstream_split_debug_payload(payload)

        checks = debug["diagnostics"]["capacity_checks"]
        self.assertTrue(checks)
        self.assertEqual({check["status"] for check in checks}, {"not_checked"})
        self.assertEqual({check["reason"] for check in checks}, {"workcenter_not_configured_for_stage"})

    def test_h1_capacity_reports_mapped_workcenter_with_missing_limits(self):
        payload = self._h1_payload()
        for stage in payload["phase5_preview_model"]["stages"]:
            stage.pop("max_sheet_length_mm", None)
            stage.pop("max_sheet_width_mm", None)
        payload["stage_feasibility"] = {
            "stages": [
                {
                    "stage_key": "2ply",
                    "stage_code": "2ply",
                    "candidate_workcenters": [{"workcenter_id": 10, "workcenter_name": "Corrugator"}],
                },
                {
                    "stage_key": "board",
                    "stage_code": "board",
                    "candidate_workcenters": [{"workcenter_id": 20, "workcenter_name": "Board"}],
                },
            ],
            "summary": {"stage_count": 2},
        }

        debug = build_phase_h_shared_upstream_split_debug_payload(payload)

        checks = debug["diagnostics"]["capacity_checks"]
        self.assertTrue(checks)
        self.assertEqual({check["status"] for check in checks}, {"not_checked"})
        self.assertEqual(
            {check["reason"] for check in checks},
            {"capacity_limits_missing_on_workcenter"},
        )

    def test_h1_width_split_branch_check_uses_piece_width_for_direct_orientation(self):
        payload = self._h1_payload()
        payload["stage_feasibility"] = {
            "stages": [
                {
                    "stage_key": "2ply",
                    "stage_code": "2ply",
                    "candidate_workcenters": [
                        {
                            "workcenter_id": 10,
                            "workcenter_name": "Corrugator",
                            "width_max_cm": 120.0,
                            "length_max_mm": 4000.0,
                        }
                    ],
                },
                {
                    "stage_key": "board",
                    "stage_code": "board",
                    "candidate_workcenters": [
                        {
                            "workcenter_id": 20,
                            "workcenter_name": "Board",
                            "width_max_cm": 100.0,
                            "length_max_mm": 2000.0,
                        }
                    ],
                },
            ],
            "summary": {"stage_count": 2},
        }

        debug = build_phase_h_shared_upstream_split_debug_payload(payload)

        board_checks = [
            check
            for check in debug["diagnostics"]["capacity_checks"]
            if check["stage_key"] == "board"
        ]
        self.assertTrue(board_checks)
        for check in board_checks:
            direct = [
                item
                for item in check["orientation_checks"]
                if item["orientation"] == "direct"
            ][0]
            rotated = [
                item
                for item in check["orientation_checks"]
                if item["orientation"] == "rotated"
            ][0]
            self.assertEqual(direct["length_mm"], 3020.0)
            self.assertEqual(direct["width_mm"], 560.0)
            self.assertEqual(rotated["length_mm"], 560.0)
            self.assertEqual(rotated["width_mm"], 3020.0)
            self.assertIn("direct (3020 x 560 mm)", check["reason"])
            self.assertIn("rotated (560 x 3020 mm)", check["reason"])

    def test_h1_blocks_missing_split_after_stage(self):
        payload = self._h1_payload()
        payload["multi_piece"]["split_after_stage_key"] = "printing"

        debug = build_phase_h_shared_upstream_split_debug_payload(payload)

        self.assertFalse(debug["guard"]["passed"])
        self.assertIn("split_after_stage_not_found_in_active_route", debug["guard"]["blockers"])

    def test_h1_blocks_piece_qty_other_than_one(self):
        payload = self._h1_payload()
        payload["multi_piece"]["pieces"][0]["piece_qty_per_fg"] = 2.0

        debug = build_phase_h_shared_upstream_split_debug_payload(payload)

        self.assertFalse(debug["guard"]["passed"])
        self.assertIn("piece_A_qty_per_fg_must_be_1", debug["guard"]["blockers"])

    def test_h1_rejects_branch_piece_that_exceeds_owner_capacity(self):
        payload = self._h1_payload()
        payload["multi_piece"]["split_axis"] = "length"
        payload["multi_piece"]["pieces"][0]["piece_length_mm"] = 1510.0
        payload["multi_piece"]["pieces"][0]["piece_width_mm"] = 1120.0
        payload["multi_piece"]["pieces"][1]["piece_length_mm"] = 1510.0
        payload["multi_piece"]["pieces"][1]["piece_width_mm"] = 1120.0
        payload["stage_feasibility"] = {
            "stages": [
                {
                    "stage_key": "2ply",
                    "stage_code": "2ply",
                    "candidate_workcenters": [
                        {
                            "workcenter_id": 10,
                            "workcenter_name": "Corrugator",
                            "width_max_cm": 120.0,
                            "length_max_mm": 4000.0,
                        }
                    ],
                },
                {
                    "stage_key": "board",
                    "stage_code": "board",
                    "candidate_workcenters": [
                        {
                            "workcenter_id": 20,
                            "workcenter_name": "Board",
                            "width_max_cm": 90.0,
                            "length_max_mm": 4000.0,
                        }
                    ],
                },
            ],
            "summary": {"stage_count": 2},
        }

        debug = build_phase_h_shared_upstream_split_debug_payload(payload)

        self.assertFalse(debug["guard"]["passed"])
        blockers = set(debug["guard"]["blockers"])
        self.assertIn("piece_A_branch_output_board_capacity_failed", blockers)
        self.assertIn("piece_B_branch_output_board_capacity_failed", blockers)

    def _h2_plan(self, payload=None):
        debug = build_phase_h2_shared_upstream_materialization_debug_payload(payload or self._h1_payload())
        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        return debug["materialization_plan"]

    def test_h2_generates_shared_branch_terminal_and_merge_candidates(self):
        plan = self._h2_plan()

        product_keys = {product["candidate_key"] for product in plan["products"]}
        bom_keys = {bom["bom_key"] for bom in plan["boms"]}

        self.assertIn("product:shared_upstream:sfg", product_keys)
        self.assertIn("product:piece:A:split_output", product_keys)
        self.assertIn("product:piece:B:split_output", product_keys)
        self.assertIn("product:piece:A:branch:board", product_keys)
        self.assertIn("product:piece:B:branch:board", product_keys)
        self.assertIn("product:piece:A:sfg", product_keys)
        self.assertIn("product:piece:B:sfg", product_keys)
        self.assertIn("product:fg", product_keys)
        self.assertIn("bom:shared_upstream", bom_keys)
        self.assertIn("bom:piece:A:split_output", bom_keys)
        self.assertIn("bom:piece:B:split_output", bom_keys)
        self.assertIn("bom:piece:A:branch:board", bom_keys)
        self.assertIn("bom:piece:B:branch:board", bom_keys)
        self.assertIn("bom:piece:A:terminal", bom_keys)
        self.assertIn("bom:piece:B:terminal", bom_keys)
        self.assertIn("bom:fg:multi_piece_merge", bom_keys)
        self.assertEqual(plan["strategy_type"], "shared_upstream_split")
        self.assertFalse(plan["metadata"]["writes_enabled"])

    def test_h2_shared_bom_consumes_rm_once_and_branch_bom_consumes_split_output_sfg(self):
        plan = self._h2_plan()
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}

        shared_lines = boms["bom:shared_upstream"]["lines"]
        split_lines = boms["bom:piece:A:split_output"]["lines"]
        branch_lines = boms["bom:piece:A:branch:board"]["lines"]

        self.assertTrue(shared_lines)
        self.assertEqual({line["line_type"] for line in shared_lines}, {"rm_input"})
        self.assertEqual(len(split_lines), 1)
        self.assertEqual(split_lines[0]["line_type"], "previous_output")
        self.assertEqual(split_lines[0]["product_candidate_key"], "product:shared_upstream:sfg")
        self.assertEqual(split_lines[0]["qty"], 1.0)
        self.assertEqual(split_lines[0]["role_code"], "shared_upstream_sfg")
        self.assertEqual(split_lines[0]["operation_segment_key"], "slitting")
        self.assertTrue(split_lines[0]["metadata"]["consumes_shared_upstream_sfg"])
        self.assertEqual(boms["bom:piece:A:split_output"]["product_qty"], 2.0)
        self.assertTrue(boms["bom:piece:A:split_output"]["metadata"]["real_slitting_bom"])
        self.assertFalse(boms["bom:piece:A:split_output"]["metadata"]["no_recursive_shared_upstream_component"])
        self.assertIn(
            "product:piece:A:split_output",
            {line["product_candidate_key"] for line in branch_lines},
        )
        self.assertIn(
            "top_sheet_external",
            {line["role_code"] for line in branch_lines},
        )

    def test_h2_terminal_piece_products_are_fg_specific_but_branch_sfgs_are_reusable(self):
        plan_a = self._h2_plan()
        payload_b = self._h1_payload()
        payload_b["fg_product_name"] = "Another FG"
        payload_b["spec_id"] = 202
        plan_b = self._h2_plan(payload_b)

        products_a = {product["candidate_key"]: product for product in plan_a["products"]}
        products_b = {product["candidate_key"]: product for product in plan_b["products"]}

        self.assertEqual(
            products_a["product:shared_upstream:sfg"]["corr_sfg_key"],
            products_b["product:shared_upstream:sfg"]["corr_sfg_key"],
        )
        self.assertEqual(
            products_a["product:piece:A:split_output"]["corr_sfg_key"],
            products_b["product:piece:A:split_output"]["corr_sfg_key"],
        )
        self.assertEqual(
            products_a["product:piece:A:branch:board"]["corr_sfg_key"],
            products_b["product:piece:A:branch:board"]["corr_sfg_key"],
        )
        self.assertNotEqual(
            products_a["product:piece:A:sfg"]["corr_sfg_key"],
            products_b["product:piece:A:sfg"]["corr_sfg_key"],
        )

    def test_h2_fg_merge_bom_has_no_raw_paper_lines(self):
        plan = self._h2_plan()
        merge_bom = [bom for bom in plan["boms"] if bom["bom_key"] == "bom:fg:multi_piece_merge"][0]

        self.assertEqual({line["line_type"] for line in merge_bom["lines"]}, {"previous_output"})
        self.assertEqual(
            {line["product_candidate_key"] for line in merge_bom["lines"]},
            {"product:piece:A:sfg", "product:piece:B:sfg"},
        )
        self.assertFalse(
            [
                line
                for line in merge_bom["lines"]
                if line["line_type"] == "rm_input" or line.get("role_code") in {"liner", "medium"}
            ]
        )

    def test_h2_summary_exposes_candidate_reuse_status_without_resolver(self):
        debug = build_phase_h2_shared_upstream_materialization_debug_payload(self._h1_payload())
        summary = debug["candidate_summary"]

        self.assertTrue(summary["products"])
        self.assertTrue(summary["boms"])
        self.assertEqual({row["reuse_status"] for row in summary["products"]}, {"not_resolved"})

    def test_h2_dependency_chain_exposes_parent_child_structure(self):
        debug = build_phase_h2_shared_upstream_materialization_debug_payload(self._h1_payload())
        chain = debug["candidate_summary"]["dependency_chain"]

        self.assertIn("Shared Upstream SFG", chain["tree_text_lines"][0])
        edges = {
            (edge["parent_candidate_key"], edge["child_candidate_key"])
            for edge in chain["edges"]
        }
        self.assertIn(("product:shared_upstream:sfg", "product:piece:A:split_output"), edges)
        self.assertIn(("product:piece:A:split_output", "product:piece:A:branch:board"), edges)
        self.assertIn(("product:piece:A:branch:board", "product:piece:A:sfg"), edges)
        self.assertIn(("product:piece:B:branch:board", "product:piece:B:sfg"), edges)
        self.assertIn(("product:piece:A:sfg", "product:fg"), edges)
        product_nodes = {node["candidate_key"]: node for node in chain["product_nodes"]}
        self.assertEqual(
            product_nodes["product:piece:A:branch:board"]["parent_candidate_key"],
            "product:piece:A:split_output",
        )
        self.assertEqual(product_nodes["product:piece:A:branch:board"]["product_role"], "sfg")

    def test_h2_shared_upstream_flow_exposes_business_tree(self):
        debug = build_phase_h2_shared_upstream_materialization_debug_payload(self._h1_payload())
        flow = debug["shared_upstream_flow"]

        self.assertIn("Shared 2-Ply 302x112 cm", flow["tree_text_lines"][0])
        self.assertIn("Width Split (50/50)", "\n".join(flow["tree_text_lines"]))
        self.assertEqual(flow["shared_sfg"]["label"], "Shared 2-Ply 302x112 cm")
        self.assertEqual(flow["split_node"]["ratio_text"], "50/50")
        self.assertTrue(flow["split_node"]["real_slitting_bom"])
        self.assertFalse(flow["split_node"]["logical_only"])
        self.assertEqual(
            [output["label"] for output in flow["outputs"]],
            ["2-Ply 302x56 cm", "2-Ply 302x56 cm"],
        )
        self.assertEqual(flow["merge"]["fg_product"], "New EngineV2 Test Pro")

    def test_h2_shared_upstream_flow_supports_uneven_split_labels(self):
        payload = self._h1_payload()
        payload["multi_piece"]["split_mode"] = "uneven"
        payload["multi_piece"]["source_width_mm"] = 1200.0
        payload["multi_piece"]["pieces"][0]["piece_width_mm"] = 720.0
        payload["multi_piece"]["pieces"][1]["piece_width_mm"] = 480.0

        debug = build_phase_h2_shared_upstream_materialization_debug_payload(payload)
        flow = debug["shared_upstream_flow"]

        self.assertEqual(flow["shared_sfg"]["label"], "Shared 2-Ply 302x120 cm")
        self.assertEqual(flow["split_node"]["ratio_text"], "60/40")
        self.assertEqual(
            [output["label"] for output in flow["outputs"]],
            ["2-Ply 302x72 cm", "2-Ply 302x48 cm"],
        )

    def test_h2_dependency_chain_includes_resolver_decisions_and_matched_ids(self):
        plan = self._h2_plan()
        resolver = {
            "products": [
                {
                    "product_candidate_key": "product:shared_upstream:sfg",
                    "action": "reuse_product",
                    "reason": "corr_sfg_key_match",
                    "existing_product_match": {"id": 501, "display_name": "Shared SFG"},
                    "blockers": [],
                },
                {
                    "product_candidate_key": "product:piece:A:branch:board",
                    "action": "create_product",
                    "reason": "no_existing_corr_sfg_key_match",
                    "existing_product_match": None,
                    "blockers": [],
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:shared_upstream",
                    "action": "reuse_bom",
                    "reason": "signature_match",
                    "existing_bom_match": {"id": 701, "display_name": "Shared BoM"},
                    "blockers": [],
                }
            ],
        }

        summary = build_h2_candidate_summary(plan, resolver)
        chain_nodes = {node["candidate_key"]: node for node in summary["dependency_chain"]["all_candidates"]}

        self.assertEqual(
            chain_nodes["product:shared_upstream:sfg"]["reuse_create_decision"],
            "reuse_product",
        )
        self.assertEqual(chain_nodes["product:shared_upstream:sfg"]["matched_product_id"], 501)
        self.assertEqual(chain_nodes["bom:shared_upstream"]["reuse_create_decision"], "reuse_bom")
        self.assertEqual(chain_nodes["bom:shared_upstream"]["matched_bom_id"], 701)
        self.assertEqual(
            chain_nodes["bom:shared_upstream"]["parent_candidate_key"],
            "product:shared_upstream:sfg",
        )

    def test_g2_accepts_piece_key_aliases_from_accepted_split_payload(self):
        payload = self._payload()
        payload["multi_piece"]["pieces"][0]["piece_key"] = "piece_a"
        payload["multi_piece"]["pieces"][1]["piece_key"] = "piece_b"
        payload["piece_layout_planning"]["pieces"][0]["piece_key"] = "piece_a"
        payload["piece_layout_planning"]["pieces"][1]["piece_key"] = "piece_b"
        payload["piece_manufacturing_overrides"]["pieces"][0]["piece_key"] = "piece_a"
        payload["piece_manufacturing_overrides"]["pieces"][1]["piece_key"] = "piece_b"
        payload["rm_requirements"][0]["metadata"]["piece_key"] = "piece_a"
        payload["rm_requirements"][1]["metadata"]["piece_key"] = "piece_a"
        payload["rm_requirements"][2]["metadata"]["piece_key"] = "piece_b"
        payload["rm_requirements"][3]["metadata"]["piece_key"] = "piece_b"

        debug = build_phase_g2_routed_piece_materialization_debug_payload(payload)

        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        self.assertTrue(debug["materialization_plan"]["boms"])

    def test_h2_shared_upstream_bom_groups_full_deckle_rm_once(self):
        payload = self._h1_payload()
        for row in payload["rm_requirements"]:
            if row["role"] == "liner":
                row["metadata"]["product_id"] = 11
            if row["role"] == "medium":
                row["metadata"]["product_id"] = 12

        plan = self._h2_plan(payload)
        shared_bom = {bom["bom_key"]: bom for bom in plan["boms"]}["bom:shared_upstream"]
        lines = shared_bom["lines"]

        self.assertEqual({line["role_code"] for line in lines}, {"liner", "medium"})
        self.assertEqual({line["metadata"]["required_deckle_cm"] for line in lines}, {112.0})
        qty_by_role = {line["role_code"]: line["qty"] for line in lines}
        self.assertEqual(qty_by_role["liner"], 1.0)
        self.assertEqual(qty_by_role["medium"], 0.8)

    def test_h2_shared_upstream_bom_falls_back_when_rm_stage_key_is_legacy(self):
        payload = self._h1_payload()
        for row in payload["rm_requirements"]:
            if row.get("role") in {"liner", "medium"}:
                row["stage_key"] = "corrugator"

        plan = self._h2_plan(payload)
        shared_bom = {bom["bom_key"]: bom for bom in plan["boms"]}["bom:shared_upstream"]

        self.assertTrue(shared_bom["lines"])
        self.assertEqual({line["role_code"] for line in shared_bom["lines"]}, {"liner", "medium"})

    def test_h2_shared_upstream_bom_clears_branch_deckle_product_resolution(self):
        payload = self._h1_payload()
        for row in payload["rm_requirements"]:
            if row.get("role") in {"liner", "medium"}:
                metadata = row.setdefault("metadata", {})
                metadata["product_id"] = 5600
                metadata["product_name"] = "KP-160-56-NS-18"
                metadata["writer_product_key"] = "product.product:5600"
                metadata["required_deckle_cm"] = 56.0
                metadata["resolution"] = {
                    "product_id": 5600,
                    "product_name": "KP-160-56-NS-18",
                    "required_deckle_cm": 56.0,
                    "status": "resolved",
                }

        plan = self._h2_plan(payload)
        shared_bom = {bom["bom_key"]: bom for bom in plan["boms"]}["bom:shared_upstream"]

        self.assertTrue(shared_bom["lines"])
        for line in shared_bom["lines"]:
            metadata = line["metadata"]
            resolution = metadata["resolution"]
            self.assertEqual(metadata["required_deckle_cm"], 112.0)
            self.assertEqual(resolution["required_deckle_cm"], 112.0)
            self.assertIsNone(metadata.get("product_id"))
            self.assertIsNone(resolution.get("product_id"))
            self.assertEqual(metadata.get("resolution_status"), "pending_full_deckle_resolution")

    def test_h2_split_output_bom_keeps_split_allocation_metadata(self):
        plan = self._h2_plan()
        split_bom = {bom["bom_key"]: bom for bom in plan["boms"]}["bom:piece:A:split_output"]

        metadata = split_bom["metadata"]
        self.assertEqual(split_bom["product_qty"], 2.0)
        self.assertEqual(len(split_bom["lines"]), 1)
        self.assertEqual(split_bom["lines"][0]["line_type"], "previous_output")
        self.assertEqual(split_bom["lines"][0]["product_candidate_key"], "product:shared_upstream:sfg")
        self.assertEqual(split_bom["lines"][0]["qty"], 1.0)
        self.assertEqual(split_bom["lines"][0]["role_code"], "shared_upstream_sfg")
        self.assertEqual(split_bom["lines"][0]["operation_segment_key"], "slitting")
        self.assertTrue(split_bom["lines"][0]["metadata"]["consumes_shared_upstream_sfg"])
        self.assertEqual(split_bom["operations"][0]["operation_key"], "slitting")
        self.assertEqual(split_bom["operations"][0]["output_qty"], 2.0)
        self.assertEqual(metadata["source_shared_product_candidate_key"], "product:shared_upstream:sfg")
        self.assertEqual(metadata["allocation_ratio"], 0.5)
        self.assertEqual(metadata["split_axis"], "width")
        self.assertEqual(metadata["split_output_size"], {"length_mm": 3020.0, "width_mm": 560.0})
        self.assertTrue(metadata["shared_upstream_group_key"])
        self.assertTrue(metadata["real_slitting_bom"])
        self.assertFalse(metadata["no_recursive_shared_upstream_component"])

    def test_h2_writer_names_split_output_sfgs_readably(self):
        plan = self._h2_plan()
        payload_plan = self._writer_payload_for_plan(plan)
        product_names = {
            row["candidate_key"]: row["values"]["name"]
            for row in payload_plan["product_create_vals"]
        }

        self.assertEqual(
            product_names["product:piece:A:split_output"],
            "2-Ply 302x56 cm",
        )

    def test_h2_writer_names_shared_upstream_sfg_readably(self):
        plan = self._h2_plan()
        payload_plan = self._writer_payload_for_plan(plan)
        product_names = {
            row["candidate_key"]: row["values"]["name"]
            for row in payload_plan["product_create_vals"]
        }

        self.assertEqual(
            product_names["product:shared_upstream:sfg"],
            "Shared 2-Ply 302x112 cm",
        )

    def test_h3_writer_payload_includes_standalone_shared_upstream_bom_and_rm_lines(self):
        plan = self._h2_plan()
        payload_plan = self._writer_payload_for_plan(plan)
        bom_values = {
            row["candidate_key"]: row["values"]
            for row in payload_plan["bom_create_vals"]
        }
        shared_rm_lines = [
            row
            for row in payload_plan["bom_line_create_vals"]
            if row.get("parent_bom_candidate_key") == "bom:shared_upstream"
        ]
        split_output_lines = [
            row
            for row in payload_plan["bom_line_create_vals"]
            if row.get("parent_bom_candidate_key") == "bom:piece:A:split_output"
        ]

        self.assertIn("bom:shared_upstream", bom_values)
        self.assertEqual(bom_values["bom:shared_upstream"]["product_candidate_key"], "product:shared_upstream:sfg")
        self.assertTrue(shared_rm_lines)
        self.assertEqual({line["values"]["product_uom_name"] for line in shared_rm_lines}, {"kg"})
        self.assertEqual(len(split_output_lines), 1)
        self.assertEqual(bom_values["bom:piece:A:split_output"]["product_qty"], 2.0)
        self.assertEqual(
            split_output_lines[0]["values"]["product_candidate_key"],
            "product:shared_upstream:sfg",
        )
        self.assertEqual(split_output_lines[0]["values"]["product_qty"], 1.0)

    def test_h2_branch_bom_consumes_split_output_qty_one(self):
        plan = self._h2_plan()
        branch_bom = {bom["bom_key"]: bom for bom in plan["boms"]}["bom:piece:A:branch:board"]
        split_line = [
            line
            for line in branch_bom["lines"]
            if line.get("product_candidate_key") == "product:piece:A:split_output"
        ][0]

        self.assertEqual(split_line["qty"], 1.0)
        self.assertEqual(split_line["role_code"], "split_output_sfg")
        self.assertTrue(split_line["metadata"]["consumes_split_output_sfg"])

    def test_h2_branch_bom_carries_split_output_model_marker(self):
        plan = self._h2_plan()
        products = {product["candidate_key"]: product for product in plan["products"]}
        branch_bom = {bom["bom_key"]: bom for bom in plan["boms"]}["bom:piece:A:branch:board"]
        split_line = [
            line
            for line in branch_bom["lines"]
            if line.get("product_candidate_key") == "product:piece:A:split_output"
        ][0]

        self.assertEqual(plan["metadata"]["shared_upstream_model"], "split_output_sfg_v4")
        self.assertEqual(branch_bom["metadata"]["shared_upstream_model"], "split_output_sfg_v4")
        self.assertEqual(split_line["metadata"]["shared_upstream_model"], "split_output_sfg_v4")
        self.assertIn("h2:mfg:board:", products["product:piece:A:branch:board"]["corr_sfg_key"])

    def test_h2_equal_split_outputs_share_manufacturing_identity(self):
        plan = self._h2_plan()
        products = {product["candidate_key"]: product for product in plan["products"]}

        self.assertEqual(
            products["product:piece:A:split_output"]["corr_sfg_key"],
            products["product:piece:B:split_output"]["corr_sfg_key"],
        )

    def test_h2_unequal_split_outputs_have_distinct_manufacturing_identity(self):
        payload = self._h1_payload()
        payload["multi_piece"]["split_mode"] = "uneven"
        payload["multi_piece"]["pieces"][0]["piece_width_mm"] = 672.0
        payload["multi_piece"]["pieces"][1]["piece_width_mm"] = 448.0

        plan = self._h2_plan(payload)
        products = {product["candidate_key"]: product for product in plan["products"]}

        self.assertNotEqual(
            products["product:piece:A:split_output"]["corr_sfg_key"],
            products["product:piece:B:split_output"]["corr_sfg_key"],
        )

    def test_h3_write_preconditions_allow_valid_h1_h2_without_resolver_blockers(self):
        payload = self._h1_payload()
        payload["phase_h_shared_upstream_split_dry_run"] = build_phase_h_shared_upstream_split_debug_payload(payload)
        h2_debug = build_phase_h2_shared_upstream_materialization_debug_payload(payload)
        h2_debug["resolver_dry_run"] = {"blockers": []}
        payload["phase_h2_shared_upstream_materialization_dry_run"] = h2_debug

        guard = validate_phase_h3_write_preconditions(payload)

        self.assertTrue(guard["passed"], guard)
        self.assertFalse(guard["policy"]["mo_creation_enabled"])
        self.assertTrue(guard["policy"]["real_split_operation_enabled"])

    def test_h3_write_preconditions_block_unequal_split_for_h4(self):
        payload = self._h1_payload()
        payload["multi_piece"]["split_mode"] = "uneven"
        payload["phase_h_shared_upstream_split_dry_run"] = build_phase_h_shared_upstream_split_debug_payload(payload)
        h2_debug = build_phase_h2_shared_upstream_materialization_debug_payload(payload)
        h2_debug["resolver_dry_run"] = {"blockers": []}
        payload["phase_h2_shared_upstream_materialization_dry_run"] = h2_debug

        guard = validate_phase_h3_write_preconditions(payload)

        self.assertFalse(guard["passed"])
        self.assertIn("h4_write_supports_equal_split_only", guard["blockers"])

    def test_h3_write_preconditions_block_h2_resolver_blockers(self):
        payload = self._h1_payload()
        payload["phase_h_shared_upstream_split_dry_run"] = build_phase_h_shared_upstream_split_debug_payload(payload)
        h2_debug = build_phase_h2_shared_upstream_materialization_debug_payload(payload)
        h2_debug["resolver_dry_run"] = {
            "blockers": [{"scope": "bom", "candidate_key": "bom:shared_upstream", "reason": "component_missing"}]
        }
        payload["phase_h2_shared_upstream_materialization_dry_run"] = h2_debug

        guard = validate_phase_h3_write_preconditions(payload)

        self.assertFalse(guard["passed"])
        self.assertIn("component_missing", guard["blockers"])

    def test_h3_write_preconditions_block_missing_h2_resolver(self):
        payload = self._h1_payload()
        payload["phase_h_shared_upstream_split_dry_run"] = build_phase_h_shared_upstream_split_debug_payload(payload)
        payload["phase_h2_shared_upstream_materialization_dry_run"] = (
            build_phase_h2_shared_upstream_materialization_debug_payload(payload)
        )

        guard = validate_phase_h3_write_preconditions(payload)

        self.assertFalse(guard["passed"])
        self.assertIn("h2_resolver_dry_run_missing", guard["blockers"])

    def _g2_plan(self, payload=None):
        debug = build_phase_g2_routed_piece_materialization_debug_payload(payload or self._payload())
        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        return debug["materialization_plan"]

    def _writer_payload_for_plan(self, plan):
        resolver = {
            "mode": plan.get("mode"),
            "blockers": [],
            "products": [
                {
                    "product_candidate_key": product["candidate_key"],
                    "product_role": product.get("product_role"),
                    "corr_sfg_key": product.get("corr_sfg_key"),
                    "corr_plan_signature": product.get("corr_plan_signature"),
                    "corr_material_signature": product.get("corr_material_signature"),
                    "corr_segment_key": product.get("corr_segment_key"),
                    "product_type_hint": product.get("product_type_hint"),
                    "action": "create_product",
                }
                for product in plan["products"]
            ],
            "boms": [
                {
                    "bom_candidate_key": bom["bom_key"],
                    "product_candidate_key": bom.get("product_candidate_key"),
                    "bom_role": bom.get("bom_role"),
                    "action": "create_bom",
                }
                for bom in plan["boms"]
            ],
        }
        return build_orm_writer_payload_plan(
            resolver,
            materialization_plan=plan,
            config=MaterializationWriterConfig(),
        )

    def _payload_with_stage_link_qty(self, qty):
        payload = self._payload()
        payload["quantity_report"] = {
            "stages": [
                {
                    "stage_code": "2ply",
                    "target_output_qty": 1.0,
                    "target_output_uom": "Units",
                    "input_qty_from_prev": 0.0,
                },
                {
                    "stage_code": "board",
                    "target_output_qty": 1.0,
                    "target_output_uom": "Units",
                    "input_qty_from_prev": qty,
                },
                {
                    "stage_code": "seaming",
                    "target_output_qty": 1.0,
                    "target_output_uom": "Units",
                    "input_qty_from_prev": 1.0,
                },
            ],
        }
        return payload

    def _board_previous_output_qtys(self, plan):
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}
        return {
            piece: [
                line["qty"]
                for line in boms[f"bom:piece:{piece}:stage:board"]["lines"]
                if line["line_type"] == "previous_output"
            ]
            for piece in ("A", "B")
        }

    def _payload_with_piece_medium_layers(self, medium_layers):
        payload = self._payload_with_stage_link_qty(1.0)
        base_by_piece = {
            "A": [
                row
                for row in payload["rm_requirements"]
                if row["role"] in {"liner", "medium"} and row["metadata"].get("piece_key") == "A"
            ],
            "B": [
                row
                for row in payload["rm_requirements"]
                if row["role"] in {"liner", "medium"} and row["metadata"].get("piece_key") == "B"
            ],
        }
        others = [row for row in payload["rm_requirements"] if row["role"] not in {"liner", "medium"}]
        rows = []
        for piece_key, base_rows in base_by_piece.items():
            liner_template = next(row for row in base_rows if row["role"] == "liner")
            medium_template = next(row for row in base_rows if row["role"] == "medium")
            max_medium_layer = max(medium_layers or [2])
            for layer_no in range(1, max_medium_layer + 2):
                if layer_no in medium_layers:
                    row = dict(medium_template)
                    row["metadata"] = dict(medium_template["metadata"])
                    row["product_key"] = f"medium_{piece_key.lower()}_{layer_no}"
                    row["layer_no"] = layer_no
                elif layer_no % 2 == 1:
                    row = dict(liner_template)
                    row["metadata"] = dict(liner_template["metadata"])
                    row["product_key"] = f"liner_{piece_key.lower()}_{layer_no}"
                    row["layer_no"] = layer_no
                else:
                    continue
                row["metadata"]["layer_no"] = layer_no
                rows.append(row)
        payload["rm_requirements"] = rows + others
        return payload

    def test_g2_generates_piece_stage_and_terminal_product_candidates(self):
        plan = self._g2_plan()
        product_keys = {product["candidate_key"] for product in plan["products"]}

        self.assertIn("product:piece:A:stage:2ply", product_keys)
        self.assertIn("product:piece:A:stage:board", product_keys)
        self.assertIn("product:piece:A:sfg", product_keys)
        self.assertIn("product:piece:B:stage:2ply", product_keys)
        self.assertIn("product:piece:B:stage:board", product_keys)
        self.assertIn("product:piece:B:sfg", product_keys)
        self.assertIn("product:fg", product_keys)

    def test_g2_terminal_sfg_uses_g2_corr_sfg_key_not_g1_terminal_key(self):
        payload = self._payload()
        g1_plan = build_phase_g1_materialization_debug_payload(payload)["materialization_plan"]
        g2_plan = self._g2_plan(payload)

        g1_terminal = next(
            product for product in g1_plan["products"] if product["candidate_key"] == "product:piece:A:sfg"
        )
        g2_terminal = next(
            product for product in g2_plan["products"] if product["candidate_key"] == "product:piece:A:sfg"
        )

        self.assertIn(":g1:piece:A:", g1_terminal["corr_sfg_key"])
        self.assertIn(":g2:piece:A:stage:terminal:", g2_terminal["corr_sfg_key"])
        self.assertNotEqual(g1_terminal["corr_sfg_key"], g2_terminal["corr_sfg_key"])

    def test_g2_terminal_piece_product_names_are_user_readable(self):
        plan = self._g2_plan()
        payload_plan = self._writer_payload_for_plan(plan)
        values_by_key = {
            item["candidate_key"]: item["values"]
            for item in payload_plan["product_create_vals"]
        }

        self.assertEqual(values_by_key["product:piece:A:sfg"]["name"], "New EngineV2 Test Pro - Piece 1")
        self.assertEqual(values_by_key["product:piece:B:sfg"]["name"], "New EngineV2 Test Pro - Piece 2")

    def test_g2_terminal_piece_names_prefer_fg_product_name_over_spec_name(self):
        payload = self._payload()
        payload["spec_name"] = "Spec eng2"
        payload["fg_product_name"] = "Product Without Slit"
        plan = self._g2_plan(payload)
        payload_plan = self._writer_payload_for_plan(plan)
        values_by_key = {
            item["candidate_key"]: item["values"]
            for item in payload_plan["product_create_vals"]
        }

        self.assertEqual(values_by_key["product:piece:A:sfg"]["name"], "Product Without Slit - Piece 1")
        self.assertEqual(values_by_key["product:piece:B:sfg"]["name"], "Product Without Slit - Piece 2")

    def test_g2_branch_sfg_identity_reuses_same_manufacturing_signature_across_fg_products(self):
        payload_a = self._payload()
        payload_b = self._payload()
        payload_b["spec_id"] = 202
        payload_b["style_code"] = "FG-BOX-B"
        payload_b["fg_product_name"] = "Another FG"

        plan_a = self._g2_plan(payload_a)
        plan_b = self._g2_plan(payload_b)
        products_a = {product["candidate_key"]: product for product in plan_a["products"]}
        products_b = {product["candidate_key"]: product for product in plan_b["products"]}
        boms_a = {bom["bom_key"]: bom for bom in plan_a["boms"]}
        boms_b = {bom["bom_key"]: bom for bom in plan_b["boms"]}

        for product_key in ("product:piece:A:stage:2ply", "product:piece:A:stage:board"):
            self.assertEqual(products_a[product_key]["corr_sfg_key"], products_b[product_key]["corr_sfg_key"])
        for bom_key in ("bom:piece:A:stage:2ply", "bom:piece:A:stage:board"):
            self.assertEqual(boms_a[bom_key]["corr_material_signature"], boms_b[bom_key]["corr_material_signature"])
            self.assertEqual(boms_a[bom_key]["corr_route_signature"], boms_b[bom_key]["corr_route_signature"])

        payload_plan_a = self._writer_payload_for_plan(plan_a)
        payload_plan_b = self._writer_payload_for_plan(plan_b)
        writer_boms_a = {
            item["candidate_key"]: item["values"]
            for item in payload_plan_a["bom_create_values"]
        }
        writer_boms_b = {
            item["candidate_key"]: item["values"]
            for item in payload_plan_b["bom_create_values"]
        }
        for bom_key in ("bom:piece:A:stage:2ply", "bom:piece:A:stage:board"):
            self.assertEqual(
                writer_boms_a[bom_key]["corr_engine_v2_signature"],
                writer_boms_b[bom_key]["corr_engine_v2_signature"],
            )

        self.assertNotEqual(
            products_a["product:piece:A:sfg"]["corr_sfg_key"],
            products_b["product:piece:A:sfg"]["corr_sfg_key"],
        )

    def test_g2_branch_sfg_identity_changes_when_piece_dimensions_change(self):
        payload_a = self._payload()
        payload_b = self._payload()
        for piece in payload_b["multi_piece"]["pieces"]:
            if piece["piece_key"] == "A":
                piece["piece_width_mm"] = 600.0
        plan_a = self._g2_plan(payload_a)
        plan_b = self._g2_plan(payload_b)
        products_a = {product["candidate_key"]: product for product in plan_a["products"]}
        products_b = {product["candidate_key"]: product for product in plan_b["products"]}

        self.assertNotEqual(
            products_a["product:piece:A:stage:2ply"]["corr_sfg_key"],
            products_b["product:piece:A:stage:2ply"]["corr_sfg_key"],
        )
        self.assertNotEqual(
            products_a["product:piece:A:stage:board"]["corr_sfg_key"],
            products_b["product:piece:A:stage:board"]["corr_sfg_key"],
        )

    def test_g2_fg_merge_signature_is_distinct_from_g1_merge_signature(self):
        payload = self._payload()
        g1_plan = build_phase_g1_materialization_debug_payload(payload)["materialization_plan"]
        g2_plan = self._g2_plan(payload)

        g1_merge = next(bom for bom in g1_plan["boms"] if bom["bom_key"] == "bom:fg:multi_piece_merge")
        g2_merge = next(bom for bom in g2_plan["boms"] if bom["bom_key"] == "bom:fg:multi_piece_merge")

        self.assertNotEqual(g1_merge["corr_material_signature"], g2_merge["corr_material_signature"])
        self.assertNotEqual(g1_merge["corr_route_signature"], g2_merge["corr_route_signature"])

    def test_g2_generates_piece_bom_chain(self):
        plan = self._g2_plan()
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}

        self.assertIn("bom:piece:A:stage:2ply", boms)
        self.assertIn("bom:piece:A:stage:board", boms)
        self.assertIn("bom:piece:A:terminal", boms)
        self.assertIn("bom:piece:B:stage:2ply", boms)
        self.assertIn("bom:piece:B:stage:board", boms)
        self.assertIn("bom:piece:B:terminal", boms)
        self.assertIn("bom:fg:multi_piece_merge", boms)

        self.assertEqual(
            {line["product_candidate_key"] for line in boms["bom:piece:A:stage:board"]["lines"] if line["line_type"] == "previous_output"},
            {"product:piece:A:stage:2ply"},
        )
        self.assertEqual(
            {line["product_candidate_key"] for line in boms["bom:piece:A:terminal"]["lines"]},
            {"product:piece:A:stage:board"},
        )

    def test_g2_3ply_board_consumes_one_2ply_from_quantity_report(self):
        plan = self._g2_plan(self._payload_with_stage_link_qty(1.0))

        self.assertEqual(self._board_previous_output_qtys(plan), {"A": [1.0], "B": [1.0]})

    def test_g2_5ply_board_consumes_two_2ply_from_quantity_report(self):
        plan = self._g2_plan(self._payload_with_stage_link_qty(2.0))

        self.assertEqual(self._board_previous_output_qtys(plan), {"A": [2.0], "B": [2.0]})

    def test_g2_7ply_board_consumes_three_2ply_from_quantity_report(self):
        plan = self._g2_plan(self._payload_with_stage_link_qty(3.0))

        self.assertEqual(self._board_previous_output_qtys(plan), {"A": [3.0], "B": [3.0]})

    def test_g2_custom_recipe_stage_link_qty_follows_planner_output(self):
        plan = self._g2_plan(self._payload_with_stage_link_qty(2.5))

        self.assertEqual(self._board_previous_output_qtys(plan), {"A": [2.5], "B": [2.5]})

    def test_g2_5ply_board_consumes_two_2ply_when_quantity_report_is_flat(self):
        plan = self._g2_plan(self._payload_with_piece_medium_layers([2, 4]))

        self.assertEqual(self._board_previous_output_qtys(plan), {"A": [2.0], "B": [2.0]})

    def test_g2_7ply_board_consumes_three_2ply_when_quantity_report_is_flat(self):
        plan = self._g2_plan(self._payload_with_piece_medium_layers([2, 4, 6]))

        self.assertEqual(self._board_previous_output_qtys(plan), {"A": [3.0], "B": [3.0]})

    def test_g2_identical_5ply_sets_group_to_one_recipe_component(self):
        plan = self._g2_plan(self._payload_with_piece_medium_layers([2, 4]))
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}
        board_lines = [
            line
            for line in boms["bom:piece:A:stage:board"]["lines"]
            if line["line_type"] == "previous_output"
        ]

        self.assertEqual(
            {line["product_candidate_key"] for line in board_lines},
            {"product:piece:A:stage:2ply"},
        )
        self.assertEqual([line["qty"] for line in board_lines], [2.0])
        self.assertIn("bom:piece:A:stage:2ply", boms)
        self.assertEqual(
            boms["bom:piece:A:stage:2ply"]["product_candidate_key"],
            "product:piece:A:stage:2ply",
        )
        self.assertNotIn("bom:piece:A:stage:2ply:set2", boms)

    def test_g2_grouped_5ply_create_bom_payload_uses_generic_2ply_qty_two(self):
        plan = self._g2_plan(self._payload_with_piece_medium_layers([2, 4]))
        payload_plan = self._writer_payload_for_plan(plan)
        product_names = {
            item["candidate_key"]: item["values"]["name"]
            for item in payload_plan["product_create_vals"]
        }
        board_lines = [
            item["values"]
            for item in payload_plan["bom_line_create_vals"]
            if item.get("parent_bom_candidate_key") == "bom:piece:A:stage:board"
            and item["values"].get("product_candidate_key")
        ]

        self.assertEqual(
            [(line["product_candidate_key"], line["product_qty"]) for line in board_lines],
            [("product:piece:A:stage:2ply", 2.0)],
        )
        self.assertIn("product:piece:A:stage:2ply", product_names)
        self.assertNotIn("SET1", product_names["product:piece:A:stage:2ply"])
        self.assertNotIn("product:piece:A:stage:2ply:set1", product_names)
        self.assertIn("302x56 cm", product_names["product:piece:A:stage:2ply"])
        self.assertIn("302x56 cm", product_names["product:piece:A:stage:board"])

    def test_g2_mixed_5ply_create_bom_payload_preserves_distinct_set_outputs(self):
        payload = self._payload_with_piece_medium_layers([2, 4])
        for row in payload["rm_requirements"]:
            if row["role"] == "medium" and row["layer_no"] == 4:
                row["metadata"]["product_id"] = 77
                row["metadata"]["flute"] = "C"

        plan = self._g2_plan(payload)
        payload_plan = self._writer_payload_for_plan(plan)
        product_names = {
            item["candidate_key"]: item["values"]["name"]
            for item in payload_plan["product_create_vals"]
        }
        board_lines = [
            item["values"]
            for item in payload_plan["bom_line_create_vals"]
            if item.get("parent_bom_candidate_key") == "bom:piece:A:stage:board"
            and item["values"].get("product_candidate_key")
        ]
        board_roles = [
            item["values"]["role_code"]
            for item in payload_plan["bom_line_create_vals"]
            if item.get("parent_bom_candidate_key") == "bom:piece:A:stage:board"
        ]

        self.assertEqual(
            [(line["product_candidate_key"], line["product_qty"]) for line in board_lines],
            [
                ("product:piece:A:stage:2ply:set1", 1.0),
                ("product:piece:A:stage:2ply:set2", 1.0),
            ],
        )
        self.assertIn("SET1", product_names["product:piece:A:stage:2ply:set1"])
        self.assertIn("SET2", product_names["product:piece:A:stage:2ply:set2"])
        self.assertIn("top_sheet_external", board_roles)

    def test_j3_custom_piece_different_flutes_create_distinct_2ply_outputs_and_names(self):
        payload = self._payload()
        payload["multi_piece"]["pieces"][1]["stack_source"] = "custom"
        payload["piece_manufacturing_overrides"]["pieces"][1]["stack"] = {
            "source": "custom",
            "effective_stack_lines": [
                {"sequence": 10, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "Flute C"},
                {"sequence": 15, "role": "inner_liner", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade"},
                {"sequence": 20, "role": "medium", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "Flute F"},
                {"sequence": 30, "role": "inner_liner", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade"},
            ],
        }
        payload["effective_piece_manufacturing"] = [
            {
                "piece_key": "A",
                "piece_index": 1,
                "stack_source": "inherited",
                "surface_source": "inherited",
                "top_sheet_source": "inherited",
            },
            {
                "piece_key": "B",
                "piece_index": 2,
                "stack_source": "custom",
                "surface_source": "inherited",
                "top_sheet_source": "inherited",
                "recipe_signature": "piece-b-c-f",
                "effective_stack_lines": payload["piece_manufacturing_overrides"]["pieces"][1]["stack"]["effective_stack_lines"],
            },
        ]
        payload["piece_specific_rm_breakdown"] = {
            "rows": [
                {"piece_key": "B", "piece_index": 2, "role": "medium", "layer_no": 1, "source_layer_no": 1, "stage_code": "2ply", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "Flute C", "qty_per_fg": 0.27, "uom": "kg", "stack_source": "custom"},
                {"piece_key": "B", "piece_index": 2, "role": "liner", "layer_no": 2, "source_layer_no": 2, "stage_code": "2ply", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "qty_per_fg": 0.27, "uom": "kg", "stack_source": "custom"},
                {"piece_key": "B", "piece_index": 2, "role": "medium", "layer_no": 3, "source_layer_no": 3, "stage_code": "2ply", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "flute": "Flute F", "qty_per_fg": 0.27, "uom": "kg", "stack_source": "custom"},
                {"piece_key": "B", "piece_index": 2, "role": "liner", "layer_no": 4, "source_layer_no": 4, "stage_code": "2ply", "gsm": 160.0, "bf": 18.0, "color": "Natural Shade", "qty_per_fg": 0.27, "uom": "kg", "stack_source": "custom"},
            ]
        }

        plan = self._g2_plan(payload)
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}
        writer_payload = self._writer_payload_for_plan(plan)
        product_names = {
            item["candidate_key"]: item["values"]["name"]
            for item in writer_payload["product_create_vals"]
        }
        board_lines = [
            line
            for line in boms["bom:piece:B:stage:board"]["lines"]
            if line["line_type"] == "previous_output"
        ]

        self.assertEqual(
            [line["product_candidate_key"] for line in board_lines],
            ["product:piece:B:stage:2ply:set1", "product:piece:B:stage:2ply:set2"],
        )
        self.assertNotEqual(
            plan["products"][0]["corr_sfg_key"],
            next(product for product in plan["products"] if product["candidate_key"] == "product:piece:B:stage:2ply:set1")["corr_sfg_key"],
        )
        self.assertIn("2-Ply 302x56 cm (C:160/160 GSM BF:18/18)", product_names["product:piece:B:stage:2ply:set1"])
        self.assertIn("2-Ply 302x56 cm (F:160/160 GSM BF:18/18)", product_names["product:piece:B:stage:2ply:set2"])
        self.assertNotIn("SET1", product_names["product:piece:B:stage:2ply:set1"])
        self.assertNotIn("SET2", product_names["product:piece:B:stage:2ply:set2"])

    def test_g2_expands_collapsed_rm_rows_from_spec_stack_lines(self):
        payload = self._payload_with_stage_link_qty(1.0)
        payload["spec_stack_lines"] = [
            {"layer_no": 1, "role": "medium", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade", "flute_code": "B"},
            {"layer_no": 2, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade", "flute_code": ""},
            {"layer_no": 3, "role": "medium", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade", "flute_code": "B"},
            {"layer_no": 4, "role": "liner", "gsm": 180.0, "bf": 18.0, "color": "Natural Shade", "flute_code": ""},
        ]
        payload["paper_resolution"] = [
            {"role": "medium", "source_layer_no": 1, "layer_no": 1, "product_id": 12, "product_name": "Kraft Paper", "qty_per_fg": 0.35, "qty_uom": "kg", "status": "exact"},
            {"role": "liner", "source_layer_no": 2, "layer_no": 2, "product_id": 11, "product_name": "Kraft Paper", "qty_per_fg": 0.30, "qty_uom": "kg", "status": "exact"},
            {"role": "medium", "source_layer_no": 3, "layer_no": 3, "product_id": 12, "product_name": "Kraft Paper", "qty_per_fg": 0.35, "qty_uom": "kg", "status": "exact"},
            {"role": "liner", "source_layer_no": 4, "layer_no": 4, "product_id": 11, "product_name": "Kraft Paper", "qty_per_fg": 0.30, "qty_uom": "kg", "status": "exact"},
        ]
        payload["rm_requirements"] = [
            row for row in payload["rm_requirements"] if row["role"] not in {"liner", "medium"}
        ] + [
            {
                "product_key": "collapsed_liner_a",
                "role": "liner",
                "stage_key": "2ply",
                "layer_no": None,
                "qty_per_fg": 0.60,
                "qty_uom": "kg",
                "metadata": {"piece_key": "A", "product_id": 11, "semantic_role": "liner"},
            },
            {
                "product_key": "collapsed_medium_a",
                "role": "medium",
                "stage_key": "2ply",
                "layer_no": None,
                "qty_per_fg": 0.70,
                "qty_uom": "kg",
                "metadata": {"piece_key": "A", "product_id": 12, "semantic_role": "medium"},
            },
            {
                "product_key": "collapsed_liner_b",
                "role": "liner",
                "stage_key": "2ply",
                "layer_no": None,
                "qty_per_fg": 0.60,
                "qty_uom": "kg",
                "metadata": {"piece_key": "B", "product_id": 11, "semantic_role": "liner"},
            },
            {
                "product_key": "collapsed_medium_b",
                "role": "medium",
                "stage_key": "2ply",
                "layer_no": None,
                "qty_per_fg": 0.70,
                "qty_uom": "kg",
                "metadata": {"piece_key": "B", "product_id": 12, "semantic_role": "medium"},
            },
        ]

        plan = self._g2_plan(payload)
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}

        self.assertEqual(self._board_previous_output_qtys(plan), {"A": [2.0], "B": [2.0]})
        self.assertIn("bom:piece:A:stage:2ply", boms)
        self.assertEqual(
            boms["bom:piece:A:stage:2ply"]["product_candidate_key"],
            "product:piece:A:stage:2ply",
        )
        self.assertNotIn("bom:piece:A:stage:2ply:set2", boms)

    def test_g2_board_product_identity_changes_with_spec_stack_set_structure(self):
        base_plan = self._g2_plan(self._payload_with_stage_link_qty(1.0))
        stacked_plan = self._g2_plan(self._payload_with_piece_medium_layers([2, 4]))

        base_board = next(
            product
            for product in base_plan["products"]
            if product["candidate_key"] == "product:piece:A:stage:board"
        )
        stacked_board = next(
            product
            for product in stacked_plan["products"]
            if product["candidate_key"] == "product:piece:A:stage:board"
        )

        self.assertNotEqual(base_board["corr_sfg_key"], stacked_board["corr_sfg_key"])

    def test_g2_different_2ply_sets_create_separate_board_inputs(self):
        payload = self._payload_with_piece_medium_layers([2, 4])
        for row in payload["rm_requirements"]:
            if row["role"] == "medium" and row["layer_no"] == 4:
                row["metadata"]["product_id"] = 77
                row["metadata"]["flute"] = "C"

        plan = self._g2_plan(payload)
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}
        board_lines = [
            line
            for line in boms["bom:piece:A:stage:board"]["lines"]
            if line["line_type"] == "previous_output"
        ]

        self.assertEqual(len(board_lines), 2)
        self.assertEqual([line["qty"] for line in board_lines], [1.0, 1.0])
        self.assertEqual(
            {line["product_candidate_key"] for line in board_lines},
            {"product:piece:A:stage:2ply:set1", "product:piece:A:stage:2ply:set2"},
        )
        self.assertIn("bom:piece:A:stage:2ply:set1", boms)
        self.assertIn("bom:piece:A:stage:2ply:set2", boms)

    def test_g2_unit_top_sheet_role_is_consumed_on_board_stage(self):
        payload = self._payload()
        for row in payload["rm_requirements"]:
            if row["role"] == "top_sheet_external":
                row["role"] = "top_sheet"
                row["metadata"]["semantic_role"] = "top_sheet"

        plan = self._g2_plan(payload)
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}
        board_roles = {line["role_code"] for line in boms["bom:piece:A:stage:board"]["lines"]}

        self.assertIn("top_sheet", board_roles)

    def test_g2_places_paper_only_on_first_piece_stage(self):
        plan = self._g2_plan()
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}

        first_roles = {line["role_code"] for line in boms["bom:piece:A:stage:2ply"]["lines"]}
        board_roles = {line["role_code"] for line in boms["bom:piece:A:stage:board"]["lines"]}
        terminal_roles = {line["role_code"] for line in boms["bom:piece:A:terminal"]["lines"]}

        self.assertIn("liner", first_roles)
        self.assertIn("medium", first_roles)
        self.assertNotIn("liner", board_roles)
        self.assertNotIn("medium", board_roles)
        self.assertNotIn("liner", terminal_roles)
        self.assertNotIn("medium", terminal_roles)

    def test_g2_places_unit_top_sheet_on_board_stage(self):
        plan = self._g2_plan()
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}

        board_roles_a = {line["role_code"] for line in boms["bom:piece:A:stage:board"]["lines"]}
        board_roles_b = {line["role_code"] for line in boms["bom:piece:B:stage:board"]["lines"]}

        self.assertIn("top_sheet_external", board_roles_a)
        self.assertIn("top_sheet_external", board_roles_b)

    def test_g2_routes_unit_top_sheet_to_board_even_if_source_stage_is_printing(self):
        plan = self._g2_plan()
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}
        board_lines = boms["bom:piece:A:stage:board"]["lines"]

        top_sheet_lines = [line for line in board_lines if line["role_code"] == "top_sheet_external"]

        self.assertEqual(len(top_sheet_lines), 1)
        self.assertEqual(top_sheet_lines[0]["operation_segment_key"], "board")
        self.assertEqual(top_sheet_lines[0]["metadata"]["original_stage_key"], "printing")
        self.assertEqual(top_sheet_lines[0]["metadata"]["assigned_route_stage"], "board")

    def test_g2_unit_top_sheet_row_level_product_id_is_preserved_on_board_lines(self):
        payload = self._payload()
        for row in payload["rm_requirements"]:
            if row["role"] != "top_sheet_external":
                continue
            row["product_id"] = 31
            row["top_sheet_product_id"] = 31
            row["metadata"].pop("product_id", None)

        plan = self._g2_plan(payload)
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}

        for bom_key in ("bom:piece:A:stage:board", "bom:piece:B:stage:board"):
            top_sheet_lines = [
                line
                for line in boms[bom_key]["lines"]
                if line["role_code"] == "top_sheet_external"
            ]
            self.assertEqual(len(top_sheet_lines), 1)
            self.assertEqual(top_sheet_lines[0]["metadata"]["product_id"], 31)
            self.assertEqual(
                top_sheet_lines[0]["metadata"]["writer_product_key"],
                "product.product:31",
            )

    def test_g2_fg_merge_bom_consumes_terminal_sfgs_and_has_merge_operation_only(self):
        plan = self._g2_plan()
        merge_bom = next(bom for bom in plan["boms"] if bom["bom_key"] == "bom:fg:multi_piece_merge")

        self.assertEqual(
            {line["product_candidate_key"] for line in merge_bom["lines"]},
            {"product:piece:A:sfg", "product:piece:B:sfg"},
        )
        self.assertFalse(
            [
                line
                for line in merge_bom["lines"]
                if line["line_type"] == "rm_input" or line.get("role_code") in {"liner", "medium"}
            ]
        )
        self.assertEqual(len(merge_bom["operations"]), 1)
        self.assertEqual(merge_bom["operations"][0]["stage_code"], "seaming")
        self.assertTrue(merge_bom["operations"][0]["metadata"]["merge_operation_only"])

    def test_g2_write_strategy_uses_existing_materialized_multi_level_writer(self):
        plan = self._g2_plan()
        resolver_dry_run = {
            "mode": plan["mode"],
            "products": [
                {
                    "product_candidate_key": product["candidate_key"],
                    "product_role": product["product_role"],
                    "action": "create_product" if product["product_role"] == "sfg" else "reuse_product",
                    "corr_sfg_key": product["corr_sfg_key"],
                }
                for product in plan["products"]
            ],
            "boms": [
                {
                    "bom_candidate_key": bom["bom_key"],
                    "bom_role": bom["bom_role"],
                    "bom_type": bom["bom_type"],
                    "product_candidate_key": bom["product_candidate_key"],
                    "action": "create_bom",
                }
                for bom in plan["boms"]
            ],
            "blockers": [],
        }

        strategy = build_materialization_writer_strategy(
            resolver_dry_run,
            materialization_plan=plan,
            config=MaterializationWriterConfig(
                allow_multi_level_boms=True,
                writer_strategy="materialized_multi_level",
            ),
        )

        self.assertEqual(strategy["strategy"], "materialized_multi_level")
        self.assertEqual(strategy["operation_scope"], "local_bom_operations")
        self.assertEqual(len(strategy["boundaries"]), len(plan["boms"]))
        self.assertEqual(strategy["boundaries"][-1]["bom_role"], "fg")

    def test_writer_does_not_reuse_stale_bom_by_corr_sfg_key_when_signature_is_present(self):
        env = _WriterTestEnv(
            {
                "mrp.bom": _WriterTestModel(
                    [
                        _WriterTestRecord(
                            id=200,
                            product_tmpl_id=100,
                            corr_engine_v2_signature="old-g1-signature",
                            corr_sfg_key="piece-a-terminal",
                        )
                    ]
                )
            }
        )
        payload = {
            "values": {
                "product_tmpl_id": 100,
                "corr_engine_v2_signature": "new-g2-signature",
                "corr_sfg_key": "piece-a-terminal",
                "_corr_strategy_type": "independent_routed_piece",
            }
        }

        self.assertIsNone(_find_existing_bom_for_payload(env, payload))

    def test_writer_can_fallback_to_corr_sfg_key_only_when_signature_is_missing(self):
        env = _WriterTestEnv(
            {
                "mrp.bom": _WriterTestModel(
                    [
                        _WriterTestRecord(
                            id=200,
                            product_tmpl_id=100,
                            corr_engine_v2_signature="old-g1-signature",
                            corr_sfg_key="piece-a-terminal",
                        )
                    ]
                )
            }
        )
        payload = {
            "values": {
                "product_tmpl_id": 100,
                "corr_sfg_key": "piece-a-terminal",
            }
        }

        self.assertEqual(_find_existing_bom_for_payload(env, payload)["id"], 200)

    def test_writer_reuses_shareable_branch_bom_by_corr_sfg_key_during_signature_transition(self):
        env = _WriterTestEnv(
            {
                "mrp.bom": _WriterTestModel(
                    [
                        _WriterTestRecord(
                            id=200,
                            product_tmpl_id=100,
                            corr_engine_v2_signature="old-piece-specific-signature",
                            corr_sfg_key="engine_v2:multi_piece:g2:mfg:board:abc",
                        )
                    ]
                )
            }
        )
        payload = {
            "values": {
                "product_tmpl_id": 100,
                "corr_engine_v2_signature": "new-manufacturing-signature",
                "corr_sfg_key": "engine_v2:multi_piece:g2:mfg:board:abc",
                "_corr_strategy_type": "independent_routed_piece",
                "_corr_shareable_branch_sfg": True,
            }
        }

        self.assertEqual(_find_existing_bom_for_payload(env, payload)["id"], 200)

    def test_writer_does_not_reuse_shared_upstream_old_model_bom_by_corr_sfg_key(self):
        env = _WriterTestEnv(
            {
                "mrp.bom": _WriterTestModel(
                    [
                        _WriterTestRecord(
                            id=200,
                            product_tmpl_id=100,
                            corr_engine_v2_signature="old-allocation-model-signature",
                            corr_sfg_key="engine_v2:multi_piece:h2:mfg:board:abc",
                        )
                    ]
                )
            }
        )
        payload = {
            "values": {
                "product_tmpl_id": 100,
                "corr_engine_v2_signature": "split-output-sfg-v1-signature",
                "corr_sfg_key": "engine_v2:multi_piece:h2:mfg:board:abc",
                "_corr_strategy_type": "shared_upstream_split",
            }
        }

        self.assertIsNone(_find_existing_bom_for_payload(env, payload))

    def test_writer_does_not_reuse_empty_exact_bom_when_lines_are_expected(self):
        env = _WriterTestEnv(
            {
                "mrp.bom": _WriterTestModel(
                    [
                        _WriterTestRecord(
                            id=200,
                            product_tmpl_id=100,
                            corr_engine_v2_signature="shared-v3-signature",
                            corr_sfg_key="engine_v2:multi_piece:h2:mfg:2ply:shared",
                            bom_line_ids=[],
                            operation_ids=[],
                        )
                    ]
                )
            }
        )
        payload = {
            "values": {
                "product_tmpl_id": 100,
                "corr_engine_v2_signature": "shared-v3-signature",
                "corr_sfg_key": "engine_v2:multi_piece:h2:mfg:2ply:shared",
                "_corr_strategy_type": "shared_upstream_split",
                "_corr_expected_line_count": 2,
                "_corr_expected_operation_count": 1,
            }
        }

        self.assertIsNone(_find_existing_bom_for_payload(env, payload))

    def test_writer_payload_plan_repairs_stale_reuse_decision_for_empty_shared_bom(self):
        plan = self._h2_plan()
        resolver = {
            "products": [
                {
                    "product_candidate_key": product["candidate_key"],
                    "action": "reuse_product",
                    "product_role": product.get("product_role"),
                    "corr_sfg_key": product.get("corr_sfg_key"),
                    "existing_product_match": {"id": index + 100, "product_variant_id": index + 200},
                    "blockers": [],
                }
                for index, product in enumerate(plan["products"])
            ],
            "boms": [
                {
                    "bom_candidate_key": bom["bom_key"],
                    "action": "reuse_bom",
                    "product_candidate_key": bom.get("product_candidate_key"),
                    "bom_role": bom.get("bom_role"),
                    "existing_bom_match": {"id": 900 + index},
                    "blockers": [],
                }
                for index, bom in enumerate(plan["boms"])
            ],
            "blockers": [],
        }
        shared_bom_id = [
            row["existing_bom_match"]["id"]
            for row in resolver["boms"]
            if row["bom_candidate_key"] == "bom:shared_upstream"
        ][0]
        env = _WriterTestEnv(
            {
                "mrp.bom": _WriterTestModel(
                    [
                        _WriterTestRecord(
                            id=shared_bom_id,
                            bom_line_ids=[],
                            operation_ids=[],
                        )
                    ]
                )
            }
        )

        payload_plan = build_orm_writer_payload_plan(
            resolver,
            materialization_plan=plan,
            config=MaterializationWriterConfig(),
            env=env,
        )

        self.assertIn(
            "bom:shared_upstream",
            {row["candidate_key"] for row in payload_plan["bom_create_vals"]},
        )
        self.assertTrue(
            [
                row
                for row in payload_plan["bom_line_create_vals"]
                if row.get("parent_bom_candidate_key") == "bom:shared_upstream"
            ]
        )

    def test_paper_attribute_fallback_resolves_existing_full_deckle_variant(self):
        product = _WriterTestRecord(
            id=3038,
            default_code="KP-160-112-NS-18",
            product_template_attribute_value_ids=[
                _WriterTestAttrValue("GSM", "160"),
                _WriterTestAttrValue("Deckle", "112"),
                _WriterTestAttrValue("Color", "Natural Shade(NS)"),
                _WriterTestAttrValue("BF", "18"),
            ],
        )
        env = _WriterTestEnv({"product.product": _WriterTestModel([product])})
        diagnostics = []

        resolved = _resolve_paper_variant_by_product_attributes(
            env,
            role="liner",
            gsm=160,
            bf=18,
            color="Natural Shade",
            flute="",
            deckle_cm=112.0,
            diagnostics=diagnostics,
        )

        self.assertIs(resolved, product)
        self.assertEqual(diagnostics[-1]["source"], "paper_variant_attribute_fallback")
        self.assertEqual(diagnostics[-1]["product_id"], 3038)

    def test_g2_route_b_board_to_seaming_materializes_board_then_terminal(self):
        payload = self._payload()
        payload["phase5_preview_model"]["stages"] = [
            {
                "stage_code": "board",
                "stage_name": "Board Formation",
                "stage_type": "lamination",
                "execution_type": "material",
                "sequence": 10,
                "capability_code": "board",
            },
            {
                "stage_code": "seaming",
                "stage_name": "Seaming / Jointing",
                "stage_type": "conversion",
                "execution_type": "operation",
                "sequence": 20,
                "capability_code": "seaming",
            },
        ]
        debug = build_phase_g2_routed_piece_materialization_debug_payload(payload)
        self.assertTrue(debug["guard"]["passed"], debug["guard"])
        self.assertEqual(debug["route_segmentation"]["branch_stage_keys"], ["board"])
        plan = debug["materialization_plan"]
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}

        self.assertIn("bom:piece:A:stage:board", boms)
        self.assertIn("bom:piece:A:terminal", boms)
        self.assertEqual(
            {line["role_code"] for line in boms["bom:piece:A:stage:board"]["lines"]},
            {"liner", "medium", "top_sheet_external"},
        )
        self.assertEqual(
            {line["product_candidate_key"] for line in boms["bom:piece:A:terminal"]["lines"]},
            {"product:piece:A:stage:board"},
        )

    def test_g2_roll_mode_keeps_roll_paper_on_first_piece_stage_not_board_top_sheet(self):
        payload = self._payload()
        payload["rm_requirements"] = [
            row
            for row in payload["rm_requirements"]
            if row["role"] != "top_sheet_external"
        ]
        payload["rm_requirements"].append(
            {
                "product_key": "top_roll_a",
                "role": "top_roll",
                "stage_key": "2ply",
                "layer_no": 1,
                "qty_per_fg": 0.2,
                "qty_uom": "kg",
                "metadata": {
                    "piece_key": "A",
                    "product_id": 41,
                    "semantic_role": "top_roll",
                    "top_sheet_mode": "roll",
                },
            }
        )
        payload["rm_requirements"].append(
            {
                "product_key": "top_roll_b",
                "role": "top_roll",
                "stage_key": "2ply",
                "layer_no": 1,
                "qty_per_fg": 0.2,
                "qty_uom": "kg",
                "metadata": {
                    "piece_key": "B",
                    "product_id": 42,
                    "semantic_role": "top_roll",
                    "top_sheet_mode": "roll",
                },
            }
        )

        plan = self._g2_plan(payload)
        boms = {bom["bom_key"]: bom for bom in plan["boms"]}
        first_roles = {line["role_code"] for line in boms["bom:piece:A:stage:2ply"]["lines"]}
        board_roles = {line["role_code"] for line in boms["bom:piece:A:stage:board"]["lines"]}

        self.assertIn("top_roll", first_roles)
        self.assertNotIn("top_sheet_external", board_roles)


if __name__ == "__main__":
    unittest.main()




