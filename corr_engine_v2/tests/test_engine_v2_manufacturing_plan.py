import unittest
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from custom_addons.corr_engine_v2.engine_v2.manufacturing_plan import (
    ManufacturingEdge,
    ManufacturingInput,
    ManufacturingPlan,
    ManufacturingOutput,
    MaterializedBomCandidate,
    build_materialization_debug_payload_from_stagecontext_preview_payload,
    build_materialization_plan_from_manufacturing_plan,
    build_manufacturing_plan_from_stagecontext_preview_payload,
    build_manufacturing_plan_from_stagecontexts,
)
from custom_addons.corr_engine_v2.engine_v2.materialization_resolver import (
    _bom_signature,
    resolve_materialization_plan_dry_run,
)
from custom_addons.corr_engine_v2.engine_v2.materialization_writer import (
    _collapse_bom_line_payloads,
    _plan_requires_reused_generated_content_refresh,
    MaterializationWriterConfig,
    MaterializationWriterDisabledError,
    build_guarded_writer_implementation_plan,
    build_guarded_executor_diagnostics,
    build_materialization_writer_strategy,
    build_materialization_writer_plan,
    build_orm_writer_payload_plan,
    execute_materialization_writer,
    writer_config_from_env,
)


@dataclass
class FixtureOutput:
    kind: str
    quantity: float
    uom: str
    fg_equivalent_qty: float
    dimensions: Dict[str, float] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


@dataclass
class FixtureInputRole:
    role_code: str
    category: str
    requirement: str = "required"
    source: str = "stage_contract"
    priority: int = 10


@dataclass
class FixtureAttachment:
    role_code: Optional[str]
    semantic_role: Optional[str]
    category: Optional[str]
    source: str = "material_profile_rule"
    material_profile_id: Optional[int] = None
    product_tmpl_id: Optional[int] = None
    rule_id: Optional[int] = None
    material_type: Optional[str] = None
    default_bucket: Optional[str] = None
    input_quantity_node_key: Optional[str] = None
    output_quantity_node_key: Optional[str] = None
    legacy_role_aliases: List[str] = field(default_factory=list)


@dataclass
class FixtureStageContext:
    stage_template_id: int
    stage_contract_code: str
    stage_code: str
    stage_name: str
    stage_type: str
    sequence: int
    index: int
    execution_type: str
    output_stock_behavior: Optional[str]
    target_output_qty: float
    target_output_uom: Optional[str]
    output_kind: Optional[str] = None
    capability_code: Optional[str] = None
    input_quantity_node_key: Optional[str] = None
    output_quantity_node_key: Optional[str] = None
    consumes_previous_output: bool = False
    previous_output_required: bool = False
    can_be_terminal: bool = False
    bom_mapping_policy: Optional[str] = None
    quantity_profile_code: Optional[str] = None
    quantity_profile_id: Optional[int] = None
    parent_material_index: Optional[int] = None
    parent_material_stage_code: Optional[str] = None
    input_qty_from_prev: float = 0.0
    input_roles: List[FixtureInputRole] = field(default_factory=list)
    rm_attachments: List[FixtureAttachment] = field(default_factory=list)
    consumable_attachments: List[FixtureAttachment] = field(default_factory=list)
    intermediate_attachments: List[FixtureAttachment] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    output_descriptor: Optional[FixtureOutput] = None


def _material_ctx(
    *,
    index,
    contract,
    stock_behavior,
    qty,
    consumes_previous=False,
    previous_required=False,
    input_qty=0.0,
    output_kind="sheet",
):
    return FixtureStageContext(
        stage_template_id=100 + index,
        stage_contract_code=contract,
        stage_code=f"custom_stage_{index}",
        stage_name=f"Custom Stage {index}",
        stage_type="custom_material",
        sequence=(index + 1) * 10,
        index=index,
        execution_type="material",
        output_stock_behavior=stock_behavior,
        target_output_qty=qty,
        target_output_uom="sheet" if stock_behavior != "fg" else "unit",
        output_kind=output_kind,
        consumes_previous_output=consumes_previous,
        previous_output_required=previous_required,
        input_qty_from_prev=input_qty,
        output_descriptor=FixtureOutput(
            kind=output_kind,
            quantity=qty,
            uom="sheet" if stock_behavior != "fg" else "unit",
            fg_equivalent_qty=qty,
        ),
    )


def _resolved_attachment(
    role,
    semantic_role,
    category,
    *,
    product_tmpl_id,
    qty,
    uom,
    material_profile_id=None,
    rule_id=None,
    product_key=None,
    layer_no=None,
    gsm=None,
    bf=None,
    flute=None,
    shade=None,
    source="preview_material_row",
    trace_only=False,
):
    preview_metadata = {}
    if layer_no is not None:
        preview_metadata["layer_no"] = layer_no
    if gsm is not None:
        preview_metadata["gsm"] = gsm
    if bf is not None:
        preview_metadata["bf"] = bf
    if flute is not None:
        preview_metadata["flute"] = flute
    if shade is not None:
        preview_metadata["shade"] = shade
    return {
        "role_code": role,
        "semantic_role": semantic_role,
        "category": category,
        "source": source,
        "material_profile_id": material_profile_id,
        "product_tmpl_id": product_tmpl_id,
        "rule_id": rule_id,
        "preview_metadata": preview_metadata,
        "writer_product_key": product_key or f"product.product:{product_tmpl_id}",
        "writer_qty": qty,
        "writer_uom": uom,
        "trace_only": trace_only,
    }


def _fallback_preview_payload(route_stages, material_rows):
    stage_contexts = []
    for index, stage in enumerate(route_stages):
        stock_behavior = "fg" if stage == "seaming" else "semi_finished"
        stage_contexts.append(
            {
                "stage_template_id": 500 + index,
                "stage_contract_code": f"route.stage.{stage}",
                "stage_code": stage,
                "stage_name": stage.title(),
                "stage_type": stage,
                "sequence": (index + 1) * 10,
                "index": index,
                "execution_type": "material",
                "output_stock_behavior": stock_behavior,
                "target_output_qty": 1.0,
                "target_output_uom": "unit" if stock_behavior == "fg" else "sheet",
                "output_kind": "box" if stock_behavior == "fg" else "sheet",
                "output_descriptor": {
                    "kind": "box" if stock_behavior == "fg" else "sheet",
                    "quantity": 1.0,
                    "uom": "unit" if stock_behavior == "fg" else "sheet",
                    "fg_equivalent_qty": 1.0,
                },
                "consumes_previous_output": index > 0,
                "previous_output_required": index > 0,
                "input_qty_from_prev": 1.0 if index > 0 else 0.0,
            }
        )
    rows = []
    for idx, (role, stage) in enumerate(material_rows):
        rows.append(
            {
                "role": role,
                "semantic_role": role,
                "role_code": role,
                "category": "raw",
                "material_type": "raw",
                "stage_code": stage,
                "component_stage": stage,
                "product_id": 6000 + idx,
                "product_tmpl_id": 7000 + idx,
                "product_key": f"product.product:{6000 + idx}",
                "product_name": role,
                "total_qty": 1.0,
                "uom": "kg" if role in {"liner", "medium"} else "unit",
                "source": "preview_material_row",
            }
        )
    return {
        "order_qty": 1.0,
        "phase8_shadow_runtime": {"stage_contexts": stage_contexts},
        "phase5_preview_model": {"materials": rows, "operations": [], "routed_operations": [], "stages": []},
        "paper_resolution": [],
    }


class FakeRecord:
    def __init__(self, **values):
        self.__dict__.update(values)

    def __bool__(self):
        return bool(getattr(self, "id", None))

    def write(self, values):
        self.__dict__.update(dict(values or {}))
        return True


class FakeRecordSet(list):
    def __bool__(self):
        return len(self) > 0

    @property
    def ids(self):
        return [getattr(record, "id", None) for record in self if getattr(record, "id", None)]


class FakeModel:
    def __init__(self, records):
        self.records = list(records)
        self.env = FakeRecord(cr=FakeRecord(dbname="fake_test_db"))

    def __bool__(self):
        return False

    def sudo(self):
        return self

    def with_context(self, **kwargs):
        return self

    def search(self, domain, limit=None):
        found = [record for record in self.records if self._matches(record, domain)]
        if limit:
            found = found[:limit]
        return FakeRecordSet(found)

    def browse(self, record_id):
        return FakeRecordSet([record for record in self.records if getattr(record, "id", None) == record_id])

    def _matches(self, record, domain):
        for field, operator, expected in domain:
            actual = getattr(record, field, None)
            if isinstance(actual, FakeRecord):
                actual = actual.id
            if actual is None:
                actual = False
            if operator == "=" and actual != expected:
                return False
            if operator == "!=" and actual == expected:
                return False
            if operator == "in" and actual not in expected:
                return False
        return True


class FakePaperMapModel(FakeModel):
    def __init__(self, records, resolved_product=None, consumable_matches=None):
        super().__init__(records)
        self.resolved_product = resolved_product
        self.consumable_matches = dict(consumable_matches or {})

    def resolve_engine_paper_variant_v1(self, **kwargs):
        if not self.resolved_product:
            return {
                "status": "missing",
                "product": None,
                "uom": None,
                "candidate_ids": [],
                "debug": {},
            }
        return {
            "status": "exact",
            "product": self.resolved_product,
            "uom": self.resolved_product.uom_id,
            "candidate_ids": [self.resolved_product.id],
            "debug": {"kwargs": kwargs},
        }

    def find_match(self, role, engine_code=None):
        return self.consumable_matches.get((role, engine_code)) or self.consumable_matches.get((role, None))


class FakeEnv(dict):
    def __init__(self, **models):
        super().__init__(models)
        self.company = FakeRecord(id=1)
        self.context = {}


class FakeConfigParameterModel:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def sudo(self):
        return self

    def get_param(self, key, default=None):
        return self.values.get(key, default)


class FakeWriterEnv(dict):
    def __init__(self, *, context=None, params=None):
        super().__init__({"ir.config_parameter": FakeConfigParameterModel(params)})
        self.context = dict(context or {})
        self.refs = {"mrp.route_warehouse0_manufacture": FakeRecord(id=77, name="Manufacture")}

    def ref(self, xmlid, raise_if_not_found=True):
        record = self.refs.get(xmlid)
        if record or not raise_if_not_found:
            return record
        raise KeyError(xmlid)


class FakeCreateModel:
    def __init__(self, model_name, id_start=1, records=None):
        self.model_name = model_name
        self.next_id = id_start
        self.created = []
        self.records = list(records or [])

    def sudo(self):
        return self

    def create(self, values):
        self.created.append(dict(values or {}))
        record = FakeRecord(id=self.next_id, **dict(values or {}))
        if self.model_name == "product.template":
            record.product_variant_id = FakeRecord(id=self.next_id + 10000)
        self.records.append(record)
        self.next_id += 1
        return record

    def search(self, domain, limit=None):
        found = [record for record in self.records if self._matches(record, domain)]
        if limit:
            found = found[:limit]
        return FakeRecordSet(found)

    def browse(self, record_id):
        return FakeRecordSet([record for record in self.records if getattr(record, "id", None) == record_id])

    def _matches(self, record, domain):
        for field, operator, expected in domain:
            actual = getattr(record, field, None)
            if isinstance(actual, FakeRecord):
                actual = actual.id
            if actual is None:
                actual = False
            if operator == "=" and actual != expected:
                return False
            if operator == "!=" and actual == expected:
                return False
            if operator == "in" and actual not in expected:
                return False
        return True


class FakeWriterCreateEnv(FakeWriterEnv):
    def __init__(self, *, context=None, params=None):
        super().__init__(context=context, params=params)
        self["product.template"] = FakeCreateModel("product.template", id_start=100)
        self["mrp.bom"] = FakeCreateModel("mrp.bom", id_start=200)
        self["mrp.bom.line"] = FakeCreateModel("mrp.bom.line", id_start=300)
        self["mrp.routing.workcenter"] = FakeCreateModel("mrp.routing.workcenter", id_start=400)


def _resolver_env(
    *,
    include_sfg=True,
    include_bom=True,
    bom_signature=None,
    company_id=None,
    live_paper_resolution=False,
    unit_uom_name="unit",
    include_consumable_map=False,
):
    unit_category = FakeRecord(id=10, display_name="Unit")
    weight_category = FakeRecord(id=11, display_name="Weight")
    kg = FakeRecord(id=1, name="kg", display_name="kg", category_id=weight_category)
    unit = FakeRecord(id=2, name=unit_uom_name, display_name=unit_uom_name, category_id=unit_category)
    paper_tmpl = FakeRecord(id=401, display_name="Paper", company_id=None, uom_id=kg, active=True)
    paper = FakeRecord(id=501, display_name="Paper Variant", product_tmpl_id=paper_tmpl, company_id=None, uom_id=kg, active=True)
    paper_tmpl.product_variant_id = paper
    top_sheet_tmpl = FakeRecord(id=2, display_name="Top Sheets", company_id=None, uom_id=unit, active=True)
    top_sheet = FakeRecord(id=2947, display_name="Top Sheet Variant", product_tmpl_id=top_sheet_tmpl, company_id=None, uom_id=unit, active=True)
    top_sheet_tmpl.product_variant_id = top_sheet
    adhesive_tmpl = FakeRecord(id=801, display_name="Adhesive", company_id=None, uom_id=kg, active=True)
    adhesive = FakeRecord(id=901, display_name="Mapped Corrugation Adhesive", product_tmpl_id=adhesive_tmpl, company_id=None, uom_id=kg, active=True)
    adhesive_tmpl.product_variant_id = adhesive
    board_adhesive_tmpl = FakeRecord(id=802, display_name="Board Adhesive", company_id=None, uom_id=kg, active=True)
    board_adhesive = FakeRecord(id=902, display_name="Mapped Board Adhesive", product_tmpl_id=board_adhesive_tmpl, company_id=None, uom_id=kg, active=True)
    board_adhesive_tmpl.product_variant_id = board_adhesive
    templates = [paper_tmpl, top_sheet_tmpl, adhesive_tmpl, board_adhesive_tmpl]
    products = [paper, top_sheet, adhesive, board_adhesive]
    boms = []
    if include_sfg:
        sfg_tmpl = FakeRecord(
            id=601,
            display_name="SFG Existing",
            corr_sfg_key="sfg:test",
            company_id=FakeRecord(id=company_id) if company_id else None,
            uom_id=unit,
            active=True,
        )
        sfg_product = FakeRecord(
            id=602,
            display_name="SFG Variant",
            product_tmpl_id=sfg_tmpl,
            company_id=sfg_tmpl.company_id,
            uom_id=unit,
            active=True,
        )
        sfg_tmpl.product_variant_id = sfg_product
        templates.append(sfg_tmpl)
        products.append(sfg_product)
        if include_bom:
            boms.append(
                FakeRecord(
                    id=701,
                    display_name="SFG BoM",
                    product_tmpl_id=sfg_tmpl,
                    corr_sfg_key="sfg:test",
                    corr_engine_v2_signature=bom_signature,
                    company_id=sfg_tmpl.company_id,
                )
            )
    return FakeEnv(
        **{
            "product.template": FakeModel(templates),
            "product.product": FakeModel(products),
            "mrp.bom": FakeModel(boms),
            "uom.uom": FakeModel([kg, unit]),
            "corr.material.product.map": FakePaperMapModel(
                [],
                resolved_product=paper if live_paper_resolution else None,
                consumable_matches=(
                    {
                        ("adhesive", "corrugation"): FakeRecord(id=1801, product_id=adhesive, uom_id=kg),
                        ("adhesive", "board"): FakeRecord(id=1802, product_id=board_adhesive, uom_id=kg),
                    }
                    if include_consumable_map
                    else {}
                ),
            ),
        }
    )


def _resolved_sfg_materialization(corr_sfg_key="sfg:test", bom_signature="sig"):
    ctx = _material_ctx(
        index=0,
        contract="route.stage.resolver",
        stock_behavior="semi_finished",
        qty=7.0,
    )
    ctx.rm_attachments = [
        _resolved_attachment(
            "paper",
            "liner",
            "material",
            product_tmpl_id=401,
            qty=3.25,
            uom="kg",
            product_key="product.product:501",
        )
    ]
    plan = build_manufacturing_plan_from_stagecontexts([ctx])
    materialized = build_materialization_plan_from_manufacturing_plan(plan)
    product = materialized.products[0]
    bom = materialized.boms[0]
    materialized.products[0] = type(product)(
        **{**asdict(product), "corr_sfg_key": corr_sfg_key}
    )
    materialized.boms[0] = type(bom)(
        **{**asdict(bom), "corr_plan_signature": bom_signature}
    )
    return materialized


class TestEngineV2ManufacturingPlan(unittest.TestCase):
    def test_material_semifinished_stage_creates_sfg_candidate(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.alpha",
            stock_behavior="semi_finished",
            qty=12.0,
        )

        plan = build_manufacturing_plan_from_stagecontexts([ctx], spec_id=7, order_qty=100)

        self.assertEqual(plan.source, "stagecontext")
        self.assertEqual(plan.spec_id, 7)
        self.assertEqual(len(plan.segments), 1)
        output = plan.segments[0].output
        self.assertIsInstance(output, ManufacturingOutput)
        self.assertEqual(output.output_role, "sfg")
        self.assertEqual(output.qty, 12.0)
        self.assertEqual(output.uom, "sheet")
        self.assertTrue(output.identity_fingerprint)

    def test_sheet_family_promotes_last_material_output_to_fg(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=1.0,
            output_kind="sheet",
        )

        plan = build_manufacturing_plan_from_stagecontexts(
            [ctx],
            spec_id=110,
            order_qty=1.0,
            metadata={"style_family": "sheet", "style_code": "0110"},
        )
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        self.assertEqual(plan.fg_segment_key, "contract:route.stage.2ply")
        self.assertEqual(plan.segments[0].output.output_role, "fg")
        self.assertEqual(materialized.products[0].product_role, "fg")
        self.assertIsNone(materialized.products[0].corr_sfg_key)
        self.assertEqual(materialized.boms[0].bom_role, "fg")

    def test_0110_stagecontext_materialization_uses_selected_route_operations_only(self):
        payload = {
            "spec_id": 110,
            "style_code": "0110",
            "order_qty": 1.0,
            "metrics": {
                "extra": {
                    "style": {
                        "metadata": {
                            "style_code": "0110",
                            "family": "sheet",
                            "uom_basis": "sheet",
                        }
                    }
                }
            },
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "stage_template_id": 1101,
                        "stage_contract_code": "route.stage.2ply",
                        "stage_code": "2ply",
                        "stage_name": "2ply",
                        "stage_type": "corrugation",
                        "sequence": 10,
                        "index": 0,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "workcenter_id": 10,
                        "routing_workcenter_id": 11,
                        "output_kind": "sheet",
                        "output_descriptor": {
                            "kind": "sheet",
                            "quantity": 1.0,
                            "uom": "sheet",
                            "fg_equivalent_qty": 1.0,
                        },
                    },
                    {
                        "stage_template_id": 1102,
                        "stage_contract_code": "route.stage.lamination",
                        "stage_code": "lamination",
                        "stage_name": "Lamination",
                        "stage_type": "lamination",
                        "sequence": 20,
                        "index": 1,
                        "execution_type": "operation",
                        "output_stock_behavior": "",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "consumes_previous_output": True,
                        "previous_output_required": True,
                        "input_qty_from_prev": 1.0,
                        "workcenter_id": 20,
                    },
                ]
            },
            "phase5_preview_model": {
                "materials": [],
                "operations": [],
                "routed_operations": [],
                "stages": [],
            },
        }

        debug = build_materialization_debug_payload_from_stagecontext_preview_payload(payload)
        materialized = debug["materialization_plan"]
        bom = materialized["boms"][0]
        operations = bom["operations"]

        self.assertEqual(materialized["fg_segment_key"], "contract:route.stage.2ply")
        self.assertEqual(materialized["products"][0]["product_role"], "fg")
        self.assertEqual(bom["bom_role"], "fg")
        self.assertEqual(
            [operation["operation_key"] for operation in operations],
            ["corrugator", "lamination"],
        )
        self.assertEqual(
            [operation["operation_name"] for operation in operations],
            ["Corrugation", "Lamination"],
        )
        self.assertNotIn("printing", [operation["operation_key"] for operation in operations])

        resolver = resolve_materialization_plan_dry_run(
            _resolver_env(include_sfg=False, include_bom=False),
            materialized,
            company_id=1,
            fg_product_tmpl_id=2,
        )
        payload_plan = build_orm_writer_payload_plan(
            resolver,
            materialization_plan=materialized,
            config=MaterializationWriterConfig(
                product_create_enabled=True,
                bom_create_enabled=True,
            ),
            env=_resolver_env(include_sfg=False, include_bom=False),
        )

        self.assertEqual(resolver["products"][0]["action"], "reuse_product")
        self.assertEqual(resolver["products"][0]["existing_product_match"]["id"], 2)
        self.assertEqual(payload_plan["bom_create_values"][0]["values"]["product_tmpl_id"], 2)
        self.assertEqual(
            [op["values"]["name"] for op in payload_plan["operation_create_values"]],
            ["Corrugation", "Lamination"],
        )
        self.assertEqual(
            [op["values"]["workcenter_id"] for op in payload_plan["operation_create_values"]],
            [11, 20],
        )

    def test_operation_only_stage_does_not_create_sfg_output(self):
        ctx = {
            "stage_template_id": 201,
            "stage_contract_code": "route.stage.print_custom",
            "stage_code": "anything",
            "stage_name": "Anything",
            "stage_type": "printing",
            "sequence": 10,
            "index": 0,
            "execution_type": "operation",
            "output_stock_behavior": "semi_finished",
            "target_output_qty": 20.0,
            "target_output_uom": "sheet",
        }

        plan = build_manufacturing_plan_from_stagecontexts([ctx])

        self.assertIsNone(plan.segments[0].output)
        self.assertFalse([s for s in plan.segments if s.output and s.output.output_role == "sfg"])

    def test_previous_output_edge_uses_stagecontext_flags(self):
        first = _material_ctx(
            index=0,
            contract="route.stage.material_a",
            stock_behavior="semi_finished",
            qty=17.0,
        )
        second = _material_ctx(
            index=1,
            contract="route.stage.material_b",
            stock_behavior="fg",
            qty=100.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=17.0,
            output_kind="box",
        )

        plan = build_manufacturing_plan_from_stagecontexts([first, second], order_qty=100)

        self.assertEqual(plan.fg_segment_key, "contract:route.stage.material_b")
        previous_edges = [edge for edge in plan.edges if edge.edge_type == "previous_output"]
        self.assertEqual(len(previous_edges), 1)
        self.assertEqual(previous_edges[0].from_key, "contract:route.stage.material_a")
        self.assertEqual(previous_edges[0].to_key, "contract:route.stage.material_b")
        self.assertEqual(previous_edges[0].qty, 17.0)

    def test_rm_and_consumable_edges_remain_separate(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.material_with_inputs",
            stock_behavior="semi_finished",
            qty=5.0,
        )
        ctx.input_roles = [
            FixtureInputRole(role_code="paper", category="material"),
            FixtureInputRole(role_code="ink", category="consumable"),
        ]
        ctx.rm_attachments = [
            FixtureAttachment(
                role_code="paper",
                semantic_role="liner",
                category="material",
                material_profile_id=1,
                product_tmpl_id=10,
                rule_id=1000,
            )
        ]
        ctx.consumable_attachments = [
            FixtureAttachment(
                role_code="ink",
                semantic_role="ink",
                category="consumable",
                material_profile_id=2,
                product_tmpl_id=20,
                rule_id=2000,
            )
        ]

        plan = build_manufacturing_plan_from_stagecontexts([ctx])

        segment = plan.segments[0]
        self.assertEqual(len(segment.material_inputs), 1)
        self.assertIsInstance(segment.material_inputs[0], ManufacturingInput)
        self.assertEqual(len(segment.consumable_inputs), 1)

        rm_edges = [edge for edge in plan.edges if edge.edge_type == "rm_input"]
        consumable_edges = [edge for edge in plan.edges if edge.edge_type == "consumable_input"]
        self.assertEqual(len(rm_edges), 1)
        self.assertEqual(len(consumable_edges), 1)
        self.assertEqual(rm_edges[0].role_code, "paper")
        self.assertEqual(consumable_edges[0].role_code, "ink")

    def test_disabled_consumable_state_suppresses_consumable_edges(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.material_with_disabled_consumable",
            stock_behavior="semi_finished",
            qty=5.0,
        )
        ctx.consumable_attachments = [
            FixtureAttachment(
                role_code="ink",
                semantic_role="ink",
                category="consumable",
                material_profile_id=2,
                product_tmpl_id=20,
                rule_id=2000,
            )
        ]

        plan = build_manufacturing_plan_from_stagecontexts(
            [ctx],
            metadata={
                "consumable_state": {
                    "enable_consumables": False,
                    "enable_printing_ink": False,
                }
            },
        )

        self.assertFalse(plan.segments[0].consumable_inputs)
        self.assertFalse([edge for edge in plan.edges if edge.edge_type == "consumable_input"])

    def test_zero_material_output_qty_warns_instead_of_crashing(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.zero_output",
            stock_behavior="semi_finished",
            qty=0.0,
        )

        plan = build_manufacturing_plan_from_stagecontexts([ctx])

        self.assertIsNone(plan.segments[0].output)
        self.assertIn("material_output_qty_missing", plan.segments[0].warnings)
        self.assertEqual([warning.code for warning in plan.warnings], ["material_output_qty_missing"])

    def test_required_previous_output_without_prior_material_output_warns(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.needs_previous",
            stock_behavior="fg",
            qty=10.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=3.0,
            output_kind="box",
        )

        plan = build_manufacturing_plan_from_stagecontexts([ctx])

        self.assertEqual(
            [warning.code for warning in plan.warnings],
            ["previous_output_source_missing"],
        )
        self.assertFalse([edge for edge in plan.edges if edge.edge_type == "previous_output"])

    def test_plan_identity_does_not_depend_on_stage_names(self):
        first = _material_ctx(
            index=0,
            contract="route.stage.semantic_output",
            stock_behavior="semi_finished",
            qty=9.0,
        )
        second = _material_ctx(
            index=0,
            contract="route.stage.semantic_output",
            stock_behavior="semi_finished",
            qty=9.0,
        )
        first.stage_code = "alpha"
        first.stage_name = "Alpha"
        second.stage_code = "beta"
        second.stage_name = "Beta"

        left = build_manufacturing_plan_from_stagecontexts([first])
        right = build_manufacturing_plan_from_stagecontexts([second])

        self.assertEqual(left.segments[0].key, right.segments[0].key)
        self.assertEqual(
            left.segments[0].output.identity_fingerprint,
            right.segments[0].output.identity_fingerprint,
        )

    def test_materialization_generates_sfg_and_fg_product_candidates(self):
        first = _material_ctx(
            index=0,
            contract="route.stage.material_source",
            stock_behavior="semi_finished",
            qty=12.0,
        )
        second = _material_ctx(
            index=1,
            contract="route.stage.material_final",
            stock_behavior="fg",
            qty=100.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=12.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts([first, second], order_qty=100)

        materialized = build_materialization_plan_from_manufacturing_plan(plan, mode="stockable")

        self.assertEqual(materialized.mode, "stockable")
        self.assertEqual(len(materialized.products), 2)
        sfg = next(item for item in materialized.products if item.product_role == "sfg")
        fg = next(item for item in materialized.products if item.product_role == "fg")
        self.assertTrue(sfg.corr_sfg_key.startswith("sfg:global:"))
        self.assertIsNone(fg.corr_sfg_key)
        self.assertEqual(sfg.route_policy, "manufacture")
        self.assertTrue(sfg.corr_plan_signature)

    def test_materialization_generates_sfg_and_fg_bom_candidates(self):
        first = _material_ctx(
            index=0,
            contract="route.stage.source",
            stock_behavior="semi_finished",
            qty=8.0,
        )
        first.rm_attachments = [
            _resolved_attachment("paper", "liner", "material", product_tmpl_id=10, qty=8.0, uom="kg")
        ]
        second = _material_ctx(
            index=1,
            contract="route.stage.final",
            stock_behavior="fg",
            qty=20.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=8.0,
            output_kind="box",
        )
        second.consumable_attachments = [
            _resolved_attachment("glue", "adhesive_fg", "consumable", product_tmpl_id=11, qty=1.0, uom="kg")
        ]
        plan = build_manufacturing_plan_from_stagecontexts([first, second], order_qty=20)

        materialized = build_materialization_plan_from_manufacturing_plan(plan, mode="phantom")

        self.assertEqual(len(materialized.boms), 2)
        sfg_bom = next(bom for bom in materialized.boms if bom.bom_role == "sfg")
        fg_bom = next(bom for bom in materialized.boms if bom.bom_role == "fg")
        self.assertIsInstance(sfg_bom, MaterializedBomCandidate)
        self.assertEqual(sfg_bom.bom_type, "phantom")
        self.assertEqual(fg_bom.bom_type, "normal")
        self.assertEqual([line.line_type for line in sfg_bom.lines], ["rm_input"])
        self.assertEqual(
            [line.line_type for line in fg_bom.lines],
            ["previous_output", "consumable_input"],
        )

    def test_materialization_stock_behavior_drives_multilevel_boundaries(self):
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply_corrugation",
            stock_behavior="semi_finished",
            qty=100.0,
        )
        two_ply.rm_attachments = [
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=401, qty=8.0, uom="kg"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=401, qty=6.0, uom="kg"),
        ]
        board = _material_ctx(
            index=1,
            contract="route.stage.board_formation",
            stock_behavior="semi_finished",
            qty=100.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=100.0,
        )
        board.rm_attachments = [
            _resolved_attachment(
                "top_sheet",
                "top_sheet_external",
                "material",
                product_tmpl_id=2,
                qty=100.0,
                uom="unit",
                product_key="product.product:2947",
            )
        ]
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=100.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=100.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts([two_ply, board, seaming], order_qty=100)

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        self.assertEqual(
            [(product.segment_key, product.product_role) for product in materialized.products],
            [
                ("contract:route.stage.2ply_corrugation", "sfg"),
                ("contract:route.stage.board_formation", "sfg"),
                ("contract:route.stage.seaming", "fg"),
            ],
        )
        two_ply_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.2ply_corrugation")
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board_formation")
        fg_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.seaming")
        self.assertEqual(two_ply_bom.product_qty, 1.0)
        self.assertEqual(board_bom.product_qty, 1.0)
        self.assertEqual(fg_bom.product_qty, 1.0)
        self.assertEqual([line.role_code for line in two_ply_bom.lines], ["liner", "medium"])
        self.assertEqual([line.line_type for line in board_bom.lines], ["previous_output", "rm_input"])
        self.assertEqual([line.role_code for line in board_bom.lines], [None, "top_sheet"])
        self.assertEqual([line.line_type for line in fg_bom.lines], ["previous_output"])
        self.assertEqual(fg_bom.lines[0].source_segment_key, "contract:route.stage.board_formation")
        mo_chain = materialized.metadata["future_mo_chain"]
        self.assertFalse(mo_chain["writes_enabled"])
        self.assertEqual(mo_chain["root_segment_key"], "contract:route.stage.seaming")
        self.assertEqual(
            [(node["segment_key"], node["mo_role"], node["child_segment_keys"]) for node in mo_chain["nodes"]],
            [
                ("contract:route.stage.2ply_corrugation", "child_mo_candidate", []),
                (
                    "contract:route.stage.board_formation",
                    "child_mo_candidate",
                    ["contract:route.stage.2ply_corrugation"],
                ),
                ("contract:route.stage.seaming", "fg_mo", ["contract:route.stage.board_formation"]),
            ],
        )

    def test_non_stockable_operation_does_not_create_materialization_boundary(self):
        material = _material_ctx(
            index=0,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=10.0,
        )
        operation = {
            "stage_template_id": 203,
            "stage_contract_code": "route.stage.scoring",
            "stage_code": "scoring",
            "stage_name": "Scoring",
            "stage_type": "scoring",
            "sequence": 20,
            "index": 1,
            "execution_type": "operation",
            "output_stock_behavior": "non_stockable",
            "target_output_qty": 10.0,
            "target_output_uom": "sheet",
            "parent_material_index": 0,
            "_plan_operation_key": "scoring",
            "_plan_operation_name": "Scoring",
        }
        plan = build_manufacturing_plan_from_stagecontexts([material, operation], order_qty=10)

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        self.assertEqual(len(materialized.products), 1)
        self.assertEqual(len(materialized.boms), 1)
        self.assertEqual(
            [op.segment_key for op in materialized.boms[0].operations],
            ["contract:route.stage.scoring"],
        )

    def test_previous_output_edge_becomes_sfg_component_line(self):
        first = _material_ctx(
            index=0,
            contract="route.stage.upstream",
            stock_behavior="semi_finished",
            qty=6.0,
        )
        second = _material_ctx(
            index=1,
            contract="route.stage.downstream",
            stock_behavior="fg",
            qty=30.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=6.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts([first, second], order_qty=30)

        materialized = build_materialization_plan_from_manufacturing_plan(plan, mode="mto")

        fg_bom = next(bom for bom in materialized.boms if bom.bom_role == "fg")
        previous_line = next(line for line in fg_bom.lines if line.line_type == "previous_output")
        upstream_product = next(product for product in materialized.products if product.product_role == "sfg")
        self.assertEqual(previous_line.product_candidate_key, upstream_product.candidate_key)
        self.assertEqual(previous_line.qty, 1.0)
        self.assertEqual(upstream_product.route_policy, "manufacture_mto")

    def test_rm_and_consumables_attach_to_correct_bom_candidates(self):
        first = _material_ctx(
            index=0,
            contract="route.stage.rm_owner",
            stock_behavior="semi_finished",
            qty=4.0,
        )
        first.rm_attachments = [
            _resolved_attachment("paper", "liner", "material", product_tmpl_id=101, qty=4.0, uom="kg")
        ]
        second = _material_ctx(
            index=1,
            contract="route.stage.consumable_owner",
            stock_behavior="fg",
            qty=10.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=4.0,
            output_kind="box",
        )
        second.consumable_attachments = [
            _resolved_attachment("ink", "ink", "consumable", product_tmpl_id=102, qty=0.5, uom="kg")
        ]
        plan = build_manufacturing_plan_from_stagecontexts([first, second], order_qty=10)

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        sfg_bom = next(bom for bom in materialized.boms if bom.bom_role == "sfg")
        fg_bom = next(bom for bom in materialized.boms if bom.bom_role == "fg")
        self.assertEqual([line.role_code for line in sfg_bom.lines], ["paper"])
        self.assertIn("ink", [line.role_code for line in fg_bom.lines])

    def test_operation_only_stage_attaches_to_parent_material_segment(self):
        material = _material_ctx(
            index=0,
            contract="route.stage.owner",
            stock_behavior="semi_finished",
            qty=4.0,
        )
        operation = {
            "stage_template_id": 202,
            "stage_contract_code": "route.stage.operation",
            "stage_code": "op_anything",
            "stage_name": "Operation Anything",
            "stage_type": "printing",
            "capability_code": "printing",
            "sequence": 20,
            "index": 1,
            "execution_type": "operation",
            "output_stock_behavior": "non_stock",
            "target_output_qty": 4.0,
            "target_output_uom": "sheet",
            "parent_material_index": 0,
            "_plan_operation_key": "printing",
            "_plan_operation_name": "Printing",
            "_plan_operation_metrics": {
                "input_qty": 4.0,
                "output_qty": 4.0,
                "uom": "sheet",
                "source": "test",
            },
        }
        plan = build_manufacturing_plan_from_stagecontexts([material, operation], order_qty=4)

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        sfg_bom = next(bom for bom in materialized.boms if bom.bom_role == "sfg")
        self.assertEqual([op.segment_key for op in sfg_bom.operations], ["contract:route.stage.operation"])

    def test_non_stock_board_inputs_attach_to_parent_sfg_boundary(self):
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=100.0,
        )
        two_ply._plan_operation_key = "corrugation"
        two_ply._plan_operation_name = "Corrugation"
        two_ply.rm_attachments = [
            _resolved_attachment(
                "liner",
                "liner",
                "material",
                product_tmpl_id=401,
                qty=8.0,
                uom="kg",
                product_key="product.product:501",
            )
        ]
        board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="non_stockable",
            qty=100.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=100.0,
        )
        board.parent_material_index = 0
        board._plan_operation_key = "board"
        board._plan_operation_name = "Board Making"
        board.rm_attachments = [
            _resolved_attachment(
                "top_sheet",
                "top_sheet_external",
                "material",
                product_tmpl_id=2,
                qty=100.0,
                uom="unit",
                product_key="product.product:2947",
            )
        ]
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=100.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=100.0,
            output_kind="box",
        )
        seaming._plan_operation_key = "seaming"
        seaming._plan_operation_name = "Seaming"
        plan = build_manufacturing_plan_from_stagecontexts([two_ply, board, seaming], order_qty=100)

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        self.assertEqual(
            [(product.segment_key, product.product_role) for product in materialized.products],
            [
                ("contract:route.stage.2ply", "sfg"),
                ("contract:route.stage.seaming", "fg"),
            ],
        )
        self.assertFalse(any(product.segment_key == "contract:route.stage.board" for product in materialized.products))
        self.assertFalse(any(bom.segment_key == "contract:route.stage.board" for bom in materialized.boms))
        sfg_bom = next(bom for bom in materialized.boms if bom.bom_role == "sfg")
        self.assertEqual(sfg_bom.segment_key, "contract:route.stage.2ply")
        self.assertEqual(sfg_bom.product_qty, 1.0)
        self.assertEqual(
            [op.segment_key for op in sfg_bom.operations],
            ["contract:route.stage.2ply", "contract:route.stage.board"],
        )
        self.assertEqual(
            [op.operation_name for op in sfg_bom.operations],
            ["Corrugation", "Board Making"],
        )
        top_sheet_line = next(line for line in sfg_bom.lines if line.role_code == "top_sheet")
        self.assertEqual(top_sheet_line.metadata["product_id"], None)
        self.assertEqual(top_sheet_line.metadata["writer_product_key"], "product.product:2947")
        self.assertEqual(top_sheet_line.qty, 1.0)
        self.assertEqual(top_sheet_line.uom, "unit")
        self.assertEqual(top_sheet_line.operation_segment_key, "contract:route.stage.board")
        liner_line = next(line for line in sfg_bom.lines if line.role_code == "liner")
        self.assertEqual(liner_line.operation_segment_key, "contract:route.stage.2ply")

    def test_board_semifinished_keeps_own_sfg_boundary_and_inputs(self):
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=100.0,
        )
        board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=100.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=100.0,
        )
        board._plan_operation_key = "board"
        board._plan_operation_name = "Board Making"
        board.rm_attachments = [
            _resolved_attachment(
                "top_sheet",
                "top_sheet_external",
                "material",
                product_tmpl_id=2,
                qty=100.0,
                uom="unit",
                product_key="product.product:2947",
            )
        ]
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=100.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=100.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts([two_ply, board, seaming], order_qty=100)

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        self.assertTrue(any(product.segment_key == "contract:route.stage.board" for product in materialized.products))
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        self.assertEqual(board_bom.product_qty, 1.0)
        self.assertIn("previous_output", [line.line_type for line in board_bom.lines])
        self.assertEqual([op.operation_name for op in board_bom.operations], ["Board Making"])
        top_sheet_line = next(line for line in board_bom.lines if line.role_code == "top_sheet")
        self.assertEqual(top_sheet_line.operation_segment_key, "contract:route.stage.board")

    def test_materialized_bom_quantities_preserve_preview_batch_scaling(self):
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=3.0,
        )
        two_ply.rm_attachments = [
            _resolved_attachment(
                "medium",
                "medium",
                "material",
                product_tmpl_id=401,
                qty=0.16,
                uom="kg",
                product_key="product.product:501",
            ),
            _resolved_attachment(
                "liner",
                "liner",
                "material",
                product_tmpl_id=402,
                qty=0.12,
                uom="kg",
                product_key="product.product:502",
            ),
        ]
        board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=3.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=3.0,
        )
        board._plan_operation_key = "board"
        board._plan_operation_name = "Board Making"
        board.rm_attachments = [
            _resolved_attachment(
                "top_sheet",
                "top_sheet_external",
                "material",
                product_tmpl_id=2,
                qty=1.0,
                uom="unit",
                product_key="product.product:2947",
            )
        ]
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=3.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts([two_ply, board, seaming], order_qty=1.0)

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        two_ply_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.2ply")
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        fg_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.seaming")
        self.assertEqual(two_ply_bom.product_qty, 1.0)
        self.assertEqual(board_bom.product_qty, 1.0)
        self.assertEqual(fg_bom.product_qty, 1.0)
        self.assertEqual(
            [(line.role_code, line.qty, line.uom) for line in two_ply_bom.lines],
            [("medium", 0.16, "kg"), ("liner", 0.12, "kg")],
        )
        self.assertEqual(next(line for line in board_bom.lines if line.line_type == "previous_output").qty, 1.0)
        self.assertEqual(next(line for line in board_bom.lines if line.role_code == "top_sheet").qty, 1.0)
        self.assertEqual(fg_bom.lines[0].qty, 1.0)

    def test_slitting_ups_keeps_component_chain_one_to_one_for_overview(self):
        selected_layout = {
            "selection_source": "wizard_selected_option",
            "option": {
                "up_count": 3,
                "final_sheet_length_mm": 1180.0,
                "final_sheet_width_mm": 1650.0,
            },
        }
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=300.0,
        )
        two_ply.rm_attachments = [
            _resolved_attachment(
                "medium",
                "medium",
                "material",
                product_tmpl_id=401,
                qty=16.0,
                uom="kg",
                product_key="product.product:501",
            )
        ]
        board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
        )
        board.rm_attachments = [
            _resolved_attachment(
                "top_sheet",
                "top_sheet_external",
                "material",
                product_tmpl_id=2,
                qty=300.0,
                uom="unit",
                product_key="product.product:2947",
            )
        ]
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts(
            [two_ply, board, seaming],
            order_qty=300.0,
            metadata={"selected_layout": selected_layout},
        )

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        two_ply_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.2ply")
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        fg_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.seaming")
        board_line = next(line for line in fg_bom.lines if line.line_type == "previous_output")
        two_ply_line = next(line for line in board_bom.lines if line.line_type == "previous_output")
        top_sheet_line = next(line for line in board_bom.lines if line.role_code == "top_sheet")
        self.assertEqual(board_line.qty / fg_bom.product_qty, 1.0)
        self.assertEqual(two_ply_line.qty / board_bom.product_qty, 1.0)
        self.assertEqual(top_sheet_line.qty / board_bom.product_qty, 1.0)
        self.assertEqual(two_ply_bom.metadata["output_sheet_length_mm"], 1180.0)
        self.assertEqual(two_ply_bom.metadata["output_sheet_width_mm"], 550.0)
        self.assertEqual(two_ply_bom.metadata["input_deckle_width_mm"], 1650.0)
        self.assertEqual(two_ply_bom.metadata["slitting_ups"], 3)
        self.assertEqual(two_ply_bom.metadata["production_cycles"], 100.0)

    def test_non_stockable_board_with_slitting_keeps_parent_component_ratio_one_to_one(self):
        selected_layout = {
            "option": {
                "up_count": 3,
                "final_sheet_length_mm": 1180.0,
                "final_sheet_width_mm": 1650.0,
            },
        }
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=300.0,
        )
        board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="non_stockable",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
        )
        board.parent_material_index = 0
        board._plan_operation_key = "board"
        board._plan_operation_name = "Board Making"
        board.rm_attachments = [
            _resolved_attachment(
                "top_sheet",
                "top_sheet_external",
                "material",
                product_tmpl_id=2,
                qty=300.0,
                uom="unit",
                product_key="product.product:2947",
            )
        ]
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts(
            [two_ply, board, seaming],
            order_qty=300.0,
            metadata={"selected_layout": selected_layout},
        )

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        self.assertFalse(any(bom.segment_key == "contract:route.stage.board" for bom in materialized.boms))
        two_ply_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.2ply")
        top_sheet_line = next(line for line in two_ply_bom.lines if line.role_code == "top_sheet")
        self.assertEqual(top_sheet_line.qty / two_ply_bom.product_qty, 1.0)
        self.assertEqual(top_sheet_line.operation_segment_key, "contract:route.stage.board")

    def test_five_ply_identical_subassemblies_merge_to_board_qty_two(self):
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=300.0,
        )
        two_ply.rm_attachments = [
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, material_profile_id=1001, rule_id=2001, qty=0.10, uom="kg", layer_no=1, gsm=180, bf=18, product_key="product.product:101"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=201, material_profile_id=1002, rule_id=2002, qty=0.08, uom="kg", layer_no=2, gsm=150, bf=16, flute="B", product_key="product.product:201"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, material_profile_id=1003, rule_id=2003, qty=0.10, uom="kg", layer_no=3, gsm=180, bf=18, product_key="product.product:101"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=201, material_profile_id=1004, rule_id=2004, qty=0.08, uom="kg", layer_no=4, gsm=150, bf=16, flute="B", product_key="product.product:201"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, qty=0.10, uom="kg", layer_no=5, gsm=180, bf=18, product_key="product.product:101"),
        ]
        board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
        )
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts(
            [two_ply, board, seaming],
            order_qty=300.0,
            metadata={
                "selected_layout": {
                    "option": {
                        "output_sheet_length_mm": 3020.0,
                        "output_sheet_width_mm": 1120.0,
                        "input_deckle_width_mm": 1120.0,
                    }
                }
            },
        )

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        self.assertFalse(any(product.segment_key == "contract:route.stage.2ply" for product in materialized.products))
        virtual_two_ply_products = [
            product for product in materialized.products if product.metadata.get("five_ply_subassembly")
        ]
        self.assertEqual(len(virtual_two_ply_products), 1)
        self.assertEqual(virtual_two_ply_products[0].metadata["merge_qty"], 2.0)
        self.assertEqual(virtual_two_ply_products[0].metadata["merged_layer_pairs"], [[3, 2], [5, 4]])

        virtual_two_ply_boms = [
            bom for bom in materialized.boms if bom.metadata.get("five_ply_subassembly")
        ]
        self.assertEqual(len(virtual_two_ply_boms), 1)
        self.assertEqual(
            [(line.role_code, line.metadata.get("layer_no")) for line in virtual_two_ply_boms[0].lines],
            [("liner", 3), ("medium", 2)],
        )
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        two_ply_line = next(line for line in board_bom.lines if line.line_type == "previous_output")
        self.assertEqual(two_ply_line.qty, 2.0)
        self.assertEqual(two_ply_line.metadata["merged_layer_pairs"], [[3, 2], [5, 4]])
        remaining_liner = next(line for line in board_bom.lines if line.role_code == "liner")
        self.assertEqual(remaining_liner.metadata.get("layer_no"), 1)
        self.assertEqual(remaining_liner.operation_segment_key, "contract:route.stage.board")

    def test_five_ply_board_diecut_route_keeps_all_papers_on_board(self):
        board = _material_ctx(
            index=0,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        board.stage_code = "board"
        board.stage_name = "Board"
        board.stage_type = "corrugation"
        board.capability_code = "corrugation"
        board.rm_attachments = [
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, qty=0.033, uom="kg", layer_no=1, gsm=180, bf=18),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=102, qty=0.036, uom="kg", layer_no=2, gsm=140, bf=18, flute="B"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=103, qty=0.026, uom="kg", layer_no=3, gsm=140, bf=18),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=104, qty=0.036, uom="kg", layer_no=4, gsm=140, bf=18, flute="B"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=105, qty=0.026, uom="kg", layer_no=5, gsm=140, bf=18),
        ]
        diecut = FixtureStageContext(
            stage_template_id=101,
            stage_contract_code="route.stage.diecut",
            stage_code="diecut",
            stage_name="Diecut",
            stage_type="diecut",
            sequence=20,
            index=1,
            execution_type="material",
            output_stock_behavior="semi_finished",
            target_output_qty=1.0,
            target_output_uom="sheet",
            output_kind="sheet",
            consumes_previous_output=True,
            previous_output_required=True,
            input_qty_from_prev=1.0,
            output_descriptor=FixtureOutput(kind="sheet", quantity=1.0, uom="sheet", fg_equivalent_qty=1.0),
        )
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
            output_kind="box",
        )
        seaming.stage_code = "seaming"

        plan = build_manufacturing_plan_from_stagecontexts([board, diecut, seaming], order_qty=1.0)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        self.assertFalse(any(product.metadata.get("five_ply_subassembly") for product in materialized.products))
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        diecut_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.diecut")
        self.assertEqual(
            [(line.role_code, line.metadata.get("layer_no")) for line in board_bom.lines],
            [("liner", 1), ("medium", 2), ("liner", 3), ("medium", 4), ("liner", 5)],
        )
        self.assertFalse(any(line.line_type == "rm_input" for line in diecut_bom.lines))
        self.assertEqual([line.line_type for line in diecut_bom.lines], ["previous_output"])

    def test_roll_cutting_sfg_bypasses_2ply_and_merges_at_board(self):
        cutting = _material_ctx(
            index=0,
            contract="route.stage.cutting",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        cutting.stage_code = "cutting"
        cutting.stage_name = "Cutting"
        cutting.stage_type = "cutting"
        cutting.capability_code = "cutting"
        cutting.rm_attachments = [
            _resolved_attachment("top_roll", "top_roll", "material", product_tmpl_id=201, qty=0.033, uom="kg", layer_no=1, gsm=180, bf=18),
        ]
        two_ply = _material_ctx(
            index=1,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        two_ply.stage_code = "2ply"
        two_ply.stage_name = "2ply"
        two_ply.stage_type = "corrugation"
        two_ply.capability_code = "corrugation"
        two_ply.rm_attachments = [
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=202, qty=0.036, uom="kg", layer_no=2, gsm=140, bf=18, flute="B"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=203, qty=0.026, uom="kg", layer_no=3, gsm=140, bf=18),
        ]
        board = _material_ctx(
            index=2,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        board.stage_code = "board"
        board.stage_name = "Board"
        board.stage_type = "corrugation"
        board.capability_code = "corrugation"

        plan = build_manufacturing_plan_from_stagecontexts([cutting, two_ply, board], order_qty=1.0)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        previous_edges = [(edge.from_key, edge.to_key) for edge in plan.edges if edge.edge_type == "previous_output"]
        self.assertNotIn(("contract:route.stage.cutting", "contract:route.stage.2ply"), previous_edges)
        self.assertIn(("contract:route.stage.2ply", "contract:route.stage.board"), previous_edges)
        self.assertIn(("contract:route.stage.cutting", "contract:route.stage.board"), previous_edges)

        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        previous_sources = {line.source_segment_key for line in board_bom.lines if line.line_type == "previous_output"}
        self.assertEqual(previous_sources, {"contract:route.stage.cutting", "contract:route.stage.2ply"})

    def test_top_sheet_cut_keeps_explicit_top_sheet_when_stack_liner_resolves_same_product(self):
        cutting = _material_ctx(
            index=0,
            contract="route.stage.top_sheet_cut",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        cutting.stage_code = "top_sheet_cut"
        cutting.stage_name = "Top Sheet Cut"
        cutting.stage_type = "auxiliary"
        cutting.capability_code = "cutting"
        cutting.rm_attachments = [
            _resolved_attachment("top_roll", "top_roll", "material", product_tmpl_id=201, qty=0.0396, uom="kg", layer_no=1, gsm=150, bf=18, product_key="product.product:2925"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=201, qty=0.0396, uom="kg", layer_no=1, gsm=150, bf=18, product_key="product.product:2925"),
        ]
        two_ply = _material_ctx(
            index=1,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        two_ply.stage_code = "2ply"
        two_ply.stage_name = "2-Ply Corrugation"
        two_ply.stage_type = "corrugation"
        two_ply.capability_code = "corrugation"
        two_ply.rm_attachments = [
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=202, qty=0.0444, uom="kg", layer_no=2, gsm=120, bf=20, flute="B", product_key="product.product:2926"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=201, qty=0.0396, uom="kg", layer_no=3, gsm=150, bf=18, product_key="product.product:2925"),
        ]
        board = _material_ctx(
            index=2,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        board.stage_code = "board"
        board.stage_name = "Board Formation"
        board.stage_type = "corrugation"
        board.capability_code = "corrugation"

        plan = build_manufacturing_plan_from_stagecontexts([cutting, two_ply, board], order_qty=1.0)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        cutting_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.top_sheet_cut")
        cutting_paper_lines = [
            line
            for line in cutting_bom.lines
            if (line.metadata or {}).get("writer_product_key") == "product.product:2925"
        ]
        self.assertEqual([line.role_code for line in cutting_paper_lines], ["top_roll"])
        self.assertEqual(cutting_paper_lines[0].qty, 0.0396)

        two_ply_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.2ply")
        self.assertEqual(
            [(line.role_code, (line.metadata or {}).get("layer_no")) for line in two_ply_bom.lines if line.line_type == "rm_input"],
            [("medium", 2), ("liner", 3)],
        )
        self.assertEqual(
            [row["allocation_source"] for row in plan.metadata.get("top_sheet_allocation_trace", [])],
            ["explicit_top_sheet_owner", "suppressed_stack_liner_duplicate"],
        )

    def test_explicit_top_sheet_3ply_materialization_keeps_liner_on_2ply(self):
        cutting = _material_ctx(
            index=0,
            contract="route.stage.top_sheet_cut",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        cutting.stage_code = "top_sheet_cut"
        cutting.stage_name = "Top Sheet Cut"
        cutting.stage_type = "auxiliary"
        cutting.capability_code = "cutting"
        cutting.rm_attachments = [
            _resolved_attachment("top_roll", "top_roll", "material", product_tmpl_id=201, qty=0.040, uom="kg", layer_no=1, gsm=150, bf=18, product_key="product.product:2925"),
        ]
        two_ply = _material_ctx(
            index=1,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        two_ply.stage_code = "2ply"
        two_ply.stage_name = "2-Ply Corrugation"
        two_ply.stage_type = "corrugation"
        two_ply.capability_code = "corrugation"
        two_ply.rm_attachments = [
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=202, qty=0.044, uom="kg", layer_no=2, gsm=120, bf=20, flute="B", product_key="product.product:2926"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=203, qty=0.040, uom="kg", layer_no=3, gsm=150, bf=18, product_key="product.product:2927"),
        ]
        board = _material_ctx(
            index=2,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        board.stage_code = "board"
        board.stage_name = "Board Formation"
        board.stage_type = "corrugation"
        board.capability_code = "corrugation"

        plan = build_manufacturing_plan_from_stagecontexts([cutting, two_ply, board], order_qty=1.0)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        cutting_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.top_sheet_cut")
        self.assertEqual(
            [(line.role_code, (line.metadata or {}).get("layer_no")) for line in cutting_bom.lines if line.line_type == "rm_input"],
            [("top_roll", 1)],
        )

        two_ply_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.2ply")
        self.assertEqual(
            [(line.role_code, (line.metadata or {}).get("layer_no")) for line in two_ply_bom.lines if line.line_type == "rm_input"],
            [("medium", 2), ("liner", 3)],
        )

        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        self.assertFalse(any(line.line_type == "rm_input" and line.role_code == "liner" for line in board_bom.lines))
        previous_sources = {line.source_segment_key for line in board_bom.lines if line.line_type == "previous_output"}
        self.assertEqual(previous_sources, {"contract:route.stage.top_sheet_cut", "contract:route.stage.2ply"})

    def test_roll_cutting_printing_sfg_merges_printed_sheet_at_board(self):
        cutting = _material_ctx(
            index=0,
            contract="route.stage.cutting",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        cutting.stage_code = "cutting"
        cutting.stage_name = "Cutting"
        cutting.stage_type = "auxiliary"
        cutting.capability_code = "cutting"
        cutting.rm_attachments = [
            _resolved_attachment("top_roll", "top_roll", "material", product_tmpl_id=201, qty=0.033, uom="kg", layer_no=1, gsm=180, bf=18),
        ]
        printing = _material_ctx(
            index=1,
            contract="route.stage.ts_print",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        printing.stage_code = "ts_print"
        printing.stage_name = "Top Sheet Printing"
        printing.stage_type = "printing"
        printing.capability_code = "printing"
        printing.consumable_attachments = [
            _resolved_attachment("ink", "ink", "consumable", product_tmpl_id=204, qty=0.002, uom="kg"),
        ]
        two_ply = _material_ctx(
            index=2,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        two_ply.stage_code = "2ply"
        two_ply.stage_name = "2ply"
        two_ply.stage_type = "corrugation"
        two_ply.capability_code = "corrugation"
        two_ply.rm_attachments = [
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=202, qty=0.036, uom="kg", layer_no=2, gsm=140, bf=18, flute="B"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=203, qty=0.026, uom="kg", layer_no=3, gsm=140, bf=18),
        ]
        board = _material_ctx(
            index=3,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        board.stage_code = "board"
        board.stage_name = "Board"
        board.stage_type = "corrugation"
        board.capability_code = "corrugation"

        plan = build_manufacturing_plan_from_stagecontexts([cutting, printing, two_ply, board], order_qty=1.0)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        previous_edges = [(edge.from_key, edge.to_key) for edge in plan.edges if edge.edge_type == "previous_output"]
        self.assertIn(("contract:route.stage.cutting", "contract:route.stage.ts_print"), previous_edges)
        self.assertNotIn(("contract:route.stage.cutting", "contract:route.stage.2ply"), previous_edges)
        self.assertNotIn(("contract:route.stage.ts_print", "contract:route.stage.2ply"), previous_edges)
        self.assertIn(("contract:route.stage.ts_print", "contract:route.stage.board"), previous_edges)

        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        previous_sources = {line.source_segment_key for line in board_bom.lines if line.line_type == "previous_output"}
        self.assertEqual(previous_sources, {"contract:route.stage.ts_print", "contract:route.stage.2ply"})

    def test_board_merges_prior_2ply_with_later_surface_film_branch(self):
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        two_ply.stage_code = "2ply"
        two_ply.stage_name = "2-Ply Corrugation"
        two_ply.stage_type = "corrugation"
        two_ply.capability_code = "corrugation"
        two_ply.rm_attachments = [
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=201, qty=0.033, uom="kg", layer_no=1, gsm=180, bf=18),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=202, qty=0.036, uom="kg", layer_no=2, gsm=140, bf=18, flute="B"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=203, qty=0.026, uom="kg", layer_no=3, gsm=140, bf=18),
        ]
        cutting = _material_ctx(
            index=1,
            contract="route.stage.cutting",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        cutting.stage_code = "cutting"
        cutting.stage_name = "Cutting"
        cutting.stage_type = "auxiliary"
        cutting.capability_code = "cutting"
        cutting.rm_attachments = [
            _resolved_attachment("top_roll", "top_roll", "material", product_tmpl_id=204, qty=0.033, uom="kg", layer_no=1, gsm=180, bf=18),
        ]
        printing = _material_ctx(
            index=2,
            contract="route.stage.ts_print",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        printing.stage_code = "ts_print"
        printing.stage_name = "Top Sheet Printing"
        printing.stage_type = "printing"
        printing.capability_code = "printing"
        printing.consumable_attachments = [
            _resolved_attachment("ink", "ink", "consumable", product_tmpl_id=205, qty=0.002, uom="kg"),
        ]
        film = _material_ctx(
            index=3,
            contract="route.stage.top_sheet_film",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        film.stage_code = "top_sheet_film"
        film.stage_name = "Top-sheet Film"
        film.stage_type = "film_lamination"
        film.capability_code = "film_lamination"
        film.consumable_attachments = [
            _resolved_attachment("film", "film", "consumable", product_tmpl_id=206, qty=0.010, uom="kg"),
        ]
        board = _material_ctx(
            index=4,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        board.stage_code = "board"
        board.stage_name = "Board Formation"
        board.stage_type = "corrugation"
        board.capability_code = "corrugation"
        board.rm_attachments = [
            _resolved_attachment("adhesive_board", "adhesive_board", "material", product_tmpl_id=207, qty=0.010, uom="kg"),
        ]

        plan = build_manufacturing_plan_from_stagecontexts([two_ply, cutting, printing, film, board], order_qty=1.0)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        previous_edges = [(edge.from_key, edge.to_key) for edge in plan.edges if edge.edge_type == "previous_output"]
        self.assertIn(("contract:route.stage.cutting", "contract:route.stage.ts_print"), previous_edges)
        self.assertIn(("contract:route.stage.ts_print", "contract:route.stage.top_sheet_film"), previous_edges)
        self.assertIn(("contract:route.stage.top_sheet_film", "contract:route.stage.board"), previous_edges)
        self.assertIn(("contract:route.stage.2ply", "contract:route.stage.board"), previous_edges)

        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        previous_sources = {line.source_segment_key for line in board_bom.lines if line.line_type == "previous_output"}
        self.assertEqual(previous_sources, {"contract:route.stage.top_sheet_film", "contract:route.stage.2ply"})

    def test_stagecontext_payload_preserves_top_sheet_cut_material_owner_before_printing(self):
        payload = {
            "order_qty": 1.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [
                    {
                        "stage_template_id": 71,
                        "stage_code": "top_sheet_cut",
                        "stage_name": "Top Sheet Cut",
                        "stage_type": "auxiliary",
                        "capability_code": "cutting",
                        "sequence": 10,
                        "index": 0,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_kind": "sheet",
                        "output_descriptor": {"kind": "sheet", "quantity": 1.0, "uom": "sheet", "fg_equivalent_qty": 1.0},
                    },
                    {
                        "stage_template_id": 72,
                        "stage_code": "ts_print",
                        "stage_name": "Top Sheet Printing",
                        "stage_type": "printing",
                        "capability_code": "printing",
                        "sequence": 20,
                        "index": 1,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_kind": "sheet",
                        "output_descriptor": {"kind": "sheet", "quantity": 1.0, "uom": "sheet", "fg_equivalent_qty": 1.0},
                        "consumes_previous_output": True,
                        "previous_output_required": True,
                        "input_qty_from_prev": 1.0,
                    },
                    {
                        "stage_template_id": 59,
                        "stage_code": "2ply",
                        "stage_name": "2-Ply Corrugation",
                        "stage_type": "corrugation",
                        "capability_code": "corrugation",
                        "sequence": 30,
                        "index": 2,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_kind": "sheet",
                        "output_descriptor": {"kind": "sheet", "quantity": 1.0, "uom": "sheet", "fg_equivalent_qty": 1.0},
                    },
                    {
                        "stage_template_id": 60,
                        "stage_code": "board",
                        "stage_name": "Board Formation",
                        "stage_type": "corrugation",
                        "capability_code": "corrugation",
                        "sequence": 40,
                        "index": 3,
                        "execution_type": "material",
                        "output_stock_behavior": "semi_finished",
                        "target_output_qty": 1.0,
                        "target_output_uom": "sheet",
                        "output_kind": "board",
                        "output_descriptor": {"kind": "board", "quantity": 1.0, "uom": "sheet", "fg_equivalent_qty": 1.0},
                        "consumes_previous_output": True,
                        "previous_output_required": True,
                        "input_qty_from_prev": 1.0,
                    },
                ],
            },
            "phase5_preview_model": {
                "materials": [
                    {"stage": "top_sheet_cut", "stage_code": "top_sheet_cut", "role": "top_roll", "category": "rm", "product_tmpl_id": 201, "qty_per_fg": 0.067, "total_qty": 0.067, "uom": "kg", "metadata": {"kind": "top_roll", "layer_no": 1, "gsm": 180}},
                    {"stage": "2ply", "stage_code": "2ply", "role": "medium", "category": "rm", "product_tmpl_id": 202, "qty_per_fg": 0.078, "total_qty": 0.078, "uom": "kg", "metadata": {"layer_no": 2}},
                    {"stage": "2ply", "stage_code": "2ply", "role": "liner", "category": "rm", "product_tmpl_id": 203, "qty_per_fg": 0.067, "total_qty": 0.067, "uom": "kg", "metadata": {"layer_no": 3}},
                ],
                "operations": [],
                "routed_operations": [],
                "stages": [],
            },
        }

        debug = build_materialization_debug_payload_from_stagecontext_preview_payload(payload)
        boms = {bom["segment_key"]: bom for bom in debug["materialization_plan"]["boms"]}

        self.assertEqual(
            [(line["line_type"], line["role_code"]) for line in boms["template:71"]["lines"]],
            [("rm_input", "top_roll")],
        )
        self.assertEqual(
            [(operation["operation_key"], operation["operation_name"]) for operation in boms["template:71"]["operations"]],
            [("cutting", "Cutting")],
        )
        self.assertEqual(
            [line["source_segment_key"] for line in boms["template:72"]["lines"] if line["line_type"] == "previous_output"],
            ["template:71"],
        )
        self.assertEqual(
            {line["source_segment_key"] for line in boms["template:60"]["lines"] if line["line_type"] == "previous_output"},
            {"template:72", "template:59"},
        )

    def test_five_ply_identical_subassemblies_merge_even_when_preview_metadata_differs(self):
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=300.0,
        )
        two_ply.rm_attachments = [
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, material_profile_id=1001, rule_id=2001, qty=0.10, uom="kg", layer_no=1, gsm=180, bf=18, product_key="product.product:101"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=201, material_profile_id=1002, rule_id=2002, qty=0.08, uom="kg", layer_no=2, gsm=150, bf=16, flute="B", product_key="product.product:201"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, material_profile_id=1003, rule_id=2003, qty=0.10, uom="kg", layer_no=3, product_key="product.product:101"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=201, material_profile_id=1004, rule_id=2004, qty=0.08, uom="kg", layer_no=4, product_key="product.product:201"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, qty=0.10, uom="kg", layer_no=5, gsm=180, bf=18, product_key="product.product:101"),
        ]
        board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
        )
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts(
            [two_ply, board, seaming],
            order_qty=300.0,
            metadata={
                "selected_layout": {
                    "option": {
                        "output_sheet_length_mm": 3020.0,
                        "output_sheet_width_mm": 1120.0,
                        "input_deckle_width_mm": 1120.0,
                    }
                }
            },
        )

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        virtual_two_ply_products = [
            product for product in materialized.products if product.metadata.get("five_ply_subassembly")
        ]
        self.assertEqual(len(virtual_two_ply_products), 1)
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        previous_lines = [line for line in board_bom.lines if line.line_type == "previous_output"]
        self.assertEqual(len(previous_lines), 1)
        self.assertEqual(previous_lines[0].qty, 2.0)

    def test_five_ply_different_flutes_create_separate_2ply_subassemblies(self):
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=300.0,
        )
        two_ply.rm_attachments = [
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, qty=0.10, uom="kg", layer_no=1, gsm=180, bf=18, product_key="product.product:101"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=201, qty=0.08, uom="kg", layer_no=2, gsm=150, bf=16, flute="B", product_key="product.product:201"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, qty=0.10, uom="kg", layer_no=3, gsm=180, bf=18, product_key="product.product:101"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=202, qty=0.08, uom="kg", layer_no=4, gsm=150, bf=16, flute="C", product_key="product.product:202"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, qty=0.10, uom="kg", layer_no=5, gsm=180, bf=18, product_key="product.product:101"),
        ]
        board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
        )
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts([two_ply, board, seaming], order_qty=300.0)

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        virtual_two_ply_products = [
            product for product in materialized.products if product.metadata.get("five_ply_subassembly")
        ]
        self.assertEqual(len(virtual_two_ply_products), 2)
        self.assertEqual(len({product.corr_sfg_key for product in virtual_two_ply_products}), 2)
        self.assertEqual(
            sorted(product.metadata["merged_layer_pairs"] for product in virtual_two_ply_products),
            [[[3, 2]], [[5, 4]]],
        )
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        two_ply_lines = [line for line in board_bom.lines if line.line_type == "previous_output"]
        self.assertEqual(len(two_ply_lines), 2)
        self.assertEqual([line.qty for line in two_ply_lines], [1.0, 1.0])
        self.assertEqual(
            sorted(line.metadata["merged_layer_pairs"] for line in two_ply_lines),
            [[[3, 2]], [[5, 4]]],
        )
        remaining_liners = [line for line in board_bom.lines if line.role_code == "liner"]
        self.assertEqual([line.metadata.get("layer_no") for line in remaining_liners], [1])

    def test_five_ply_subassembly_reuses_standard_2ply_identity_when_recipe_matches(self):
        standard_two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=300.0,
        )
        standard_two_ply.rm_attachments = [
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, qty=0.10, uom="kg", layer_no=1, gsm=180, bf=18, product_key="product.product:101"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=201, qty=0.08, uom="kg", layer_no=2, gsm=150, bf=16, flute="B", product_key="product.product:201"),
        ]
        standard_board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
        )
        standard_seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
            output_kind="box",
        )
        standard_plan = build_manufacturing_plan_from_stagecontexts([standard_two_ply, standard_board, standard_seaming], order_qty=300.0)
        standard_materialized = build_materialization_plan_from_manufacturing_plan(standard_plan)
        standard_product = next(
            product for product in standard_materialized.products if product.segment_key == "contract:route.stage.2ply"
        )

        five_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=300.0,
        )
        five_ply.rm_attachments = [
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, qty=0.10, uom="kg", layer_no=1, gsm=180, bf=18, product_key="product.product:101"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=201, qty=0.08, uom="kg", layer_no=2, gsm=150, bf=16, flute="B", product_key="product.product:201"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, qty=0.10, uom="kg", layer_no=3, gsm=180, bf=18, product_key="product.product:101"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=201, qty=0.08, uom="kg", layer_no=4, gsm=150, bf=16, flute="B", product_key="product.product:201"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=101, qty=0.10, uom="kg", layer_no=5, gsm=180, bf=18, product_key="product.product:101"),
        ]
        five_board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
        )
        five_seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=300.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=300.0,
            output_kind="box",
        )
        five_plan = build_manufacturing_plan_from_stagecontexts([five_ply, five_board, five_seaming], order_qty=300.0)
        five_materialized = build_materialization_plan_from_manufacturing_plan(five_plan)
        virtual_two_ply = next(
            product for product in five_materialized.products if product.metadata.get("five_ply_subassembly")
        )

        self.assertEqual(virtual_two_ply.corr_sfg_key, standard_product.corr_sfg_key)

    def test_five_ply_ignores_unresolved_profile_placeholders_when_merging(self):
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        two_ply.rm_attachments = [
            _resolved_attachment("RM_PAPER_LINER", "liner", "material", product_tmpl_id=1181, material_profile_id=13, rule_id=5, qty=0.0, uom=""),
            _resolved_attachment("RM_PAPER_MEDIUM", "medium", "material", product_tmpl_id=1182, material_profile_id=14, rule_id=6, qty=0.0, uom=""),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=1, qty=0.70, uom="kg", layer_no=2, gsm=150, bf=20, flute="B", product_key="product.product:2926"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=1, qty=0.70, uom="kg", layer_no=2, gsm=150, bf=20, flute="B", product_key="product.product:2926"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=1, qty=0.61, uom="kg", layer_no=3, gsm=180, bf=20, product_key="product.product:2925"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=1, qty=0.70, uom="kg", layer_no=4, gsm=150, bf=20, flute="B", product_key="product.product:2926"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=1, qty=0.70, uom="kg", layer_no=4, gsm=150, bf=20, flute="B", product_key="product.product:2926"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=1, qty=0.61, uom="kg", layer_no=5, gsm=180, bf=20, product_key="product.product:2925"),
        ]
        board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts([two_ply, board, seaming], order_qty=1.0)

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        virtual_two_ply_products = [
            product for product in materialized.products if product.metadata.get("five_ply_subassembly")
        ]
        self.assertEqual(len(virtual_two_ply_products), 1)
        self.assertEqual(virtual_two_ply_products[0].metadata["merge_qty"], 2.0)
        self.assertEqual(virtual_two_ply_products[0].metadata["merged_layer_pairs"], [[3, 2], [5, 4]])
        virtual_two_ply_bom = next(bom for bom in materialized.boms if bom.metadata.get("five_ply_subassembly"))
        self.assertEqual(len(virtual_two_ply_bom.lines), 2)
        self.assertEqual([line.role_code for line in virtual_two_ply_bom.lines], ["liner", "medium"])
        self.assertEqual(virtual_two_ply_bom.metadata["diagnostic_only_lines"], [])
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        two_ply_lines = [line for line in board_bom.lines if line.line_type == "previous_output"]
        self.assertEqual(len(two_ply_lines), 1)
        self.assertEqual(two_ply_lines[0].qty, 2.0)
        remaining_liners = [line for line in board_bom.lines if line.role_code == "liner"]
        self.assertEqual([line.metadata.get("layer_no") for line in remaining_liners], [])

    def test_five_ply_board_does_not_duplicate_liner_used_by_2ply_as_top_roll(self):
        two_ply = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        two_ply.rm_attachments = [
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=1, qty=0.70, uom="kg", layer_no=2, gsm=150, bf=20, flute="B", product_key="product.product:2926"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=1, qty=0.61, uom="kg", layer_no=3, gsm=180, bf=20, product_key="product.product:2925"),
            _resolved_attachment("medium", "medium", "material", product_tmpl_id=1, qty=0.75, uom="kg", layer_no=4, gsm=160, bf=20, flute="B", product_key="product.product:2927"),
            _resolved_attachment("liner", "liner", "material", product_tmpl_id=1, qty=0.61, uom="kg", layer_no=5, gsm=180, bf=20, product_key="product.product:2925"),
        ]
        board = _material_ctx(
            index=1,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
        )
        board.rm_attachments = [
            _resolved_attachment("top_sheet_external", "top_roll", "material", product_tmpl_id=1, qty=0.61, uom="kg", layer_no=1, gsm=180, bf=20, product_key="product.product:2925")
        ]
        seaming = _material_ctx(
            index=2,
            contract="route.stage.seaming",
            stock_behavior="fg",
            qty=1.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=1.0,
            output_kind="box",
        )
        plan = build_manufacturing_plan_from_stagecontexts([two_ply, board, seaming], order_qty=1.0)

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        board_bom = next(bom for bom in materialized.boms if bom.segment_key == "contract:route.stage.board")
        previous_output_lines = [line for line in board_bom.lines if line.line_type == "previous_output"]
        self.assertEqual(len(previous_output_lines), 2)
        paper_lines = [
            line
            for line in board_bom.lines
            if (line.metadata or {}).get("writer_product_key") == "product.product:2925"
        ]
        self.assertEqual(len(paper_lines), 1)
        self.assertIn(paper_lines[0].role_code, ("top_roll", "top_sheet_external", "top_sheet"))
        self.assertEqual(paper_lines[0].qty, 0.61)
        self.assertEqual((paper_lines[0].metadata or {}).get("layer_no"), 1)
        self.assertFalse(any(line.role_code == "liner" and (line.metadata or {}).get("layer_no") == 5 for line in board_bom.lines))

    def test_missing_2ply_materials_fallback_to_board_route_stage(self):
        payload = _fallback_preview_payload(
            ["board", "diecut", "seaming"],
            [
                ("liner", "2ply"),
                ("medium", "2ply"),
                ("top_sheet_external", "board"),
            ],
        )

        plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        board_segment = next(segment for segment in plan.segments if segment.stage_code == "board")
        roles = {item.semantic_role: item.metadata for item in board_segment.material_inputs}
        self.assertEqual(roles["liner"]["assigned_route_stage"], "board")
        self.assertEqual(roles["liner"]["original_semantic_stage"], "2ply")
        self.assertEqual(roles["liner"]["fallback_reason"], "missing_stage")
        self.assertEqual(roles["medium"]["assigned_route_stage"], "board")
        self.assertNotIn("liner", [
            item.semantic_role
            for segment in plan.segments
            if segment.stage_code != "board"
            for item in segment.material_inputs
        ])
        board_bom = next(bom for bom in materialized.boms if bom.segment_key == board_segment.key)
        self.assertEqual(
            sorted(line.role_code for line in board_bom.lines if line.line_type == "rm_input"),
            ["liner", "medium", "top_sheet_external"],
        )

    def test_missing_2ply_and_board_materials_fallback_to_diecut_route_stage(self):
        payload = _fallback_preview_payload(
            ["diecut", "seaming"],
            [
                ("liner", "2ply"),
                ("medium", "2ply"),
                ("top_sheet_external", "board"),
            ],
        )

        plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)

        diecut_segment = next(segment for segment in plan.segments if segment.stage_code == "diecut")
        roles = {item.semantic_role: item.metadata for item in diecut_segment.material_inputs}
        self.assertEqual(roles["liner"]["assigned_route_stage"], "diecut")
        self.assertEqual(roles["medium"]["assigned_route_stage"], "diecut")
        self.assertEqual(roles["top_sheet_external"]["assigned_route_stage"], "diecut")
        self.assertEqual(roles["top_sheet_external"]["original_semantic_stage"], "board")

    def test_present_2ply_materials_do_not_fallback(self):
        payload = _fallback_preview_payload(
            ["2ply", "board", "seaming"],
            [
                ("liner", "2ply"),
                ("medium", "2ply"),
                ("top_sheet_external", "board"),
            ],
        )

        plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)

        two_ply_segment = next(segment for segment in plan.segments if segment.stage_code == "2ply")
        roles = {item.semantic_role: item.metadata for item in two_ply_segment.material_inputs}
        self.assertIsNone(roles["liner"].get("fallback_reason"))
        self.assertIsNone(roles["medium"].get("fallback_reason"))

    def test_0110_board_top_sheet_row_attaches_by_semantic_stage_after_identity_miss(self):
        payload = _fallback_preview_payload(
            ["2ply", "board"],
            [
                ("liner", "2ply"),
                ("medium", "2ply"),
                ("top_sheet_external", "board_source_row"),
            ],
        )
        payload["style_code"] = "0110"
        payload["metrics"] = {
            "extra": {
                "style": {
                    "metadata": {
                        "family": "sheet",
                        "style_code": "0110",
                    }
                }
            }
        }

        plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        board_segment = next(segment for segment in plan.segments if segment.stage_code == "board")
        self.assertEqual(board_segment.output.output_role, "fg")
        roles = {item.semantic_role: item.metadata for item in board_segment.material_inputs}
        self.assertEqual(roles["top_sheet_external"]["assigned_route_stage"], "board")
        self.assertEqual(roles["top_sheet_external"]["original_semantic_stage"], "board")
        self.assertEqual(roles["top_sheet_external"]["fallback_reason"], "semantic_stage_identity_miss")

        board_bom = next(bom for bom in materialized.boms if bom.segment_key == board_segment.key)
        line_roles = [line.role_code for line in board_bom.lines]
        self.assertIn(None, line_roles)
        self.assertIn("top_sheet_external", line_roles)
        top_sheet_line = next(line for line in board_bom.lines if line.role_code == "top_sheet_external")
        self.assertEqual(top_sheet_line.operation_segment_key, board_segment.key)
        self.assertEqual(top_sheet_line.metadata["writer_product_key"], "product.product:6002")

    def test_materialization_identity_does_not_depend_on_stage_names(self):
        left_ctx = _material_ctx(
            index=0,
            contract="route.stage.same_identity",
            stock_behavior="semi_finished",
            qty=5.0,
        )
        right_ctx = _material_ctx(
            index=0,
            contract="route.stage.same_identity",
            stock_behavior="semi_finished",
            qty=5.0,
        )
        left_ctx.stage_code = "alpha"
        left_ctx.stage_name = "Alpha"
        right_ctx.stage_code = "beta"
        right_ctx.stage_name = "Beta"

        left = build_materialization_plan_from_manufacturing_plan(
            build_manufacturing_plan_from_stagecontexts([left_ctx])
        )
        right = build_materialization_plan_from_manufacturing_plan(
            build_manufacturing_plan_from_stagecontexts([right_ctx])
        )

        self.assertEqual(left.products[0].corr_sfg_key, right.products[0].corr_sfg_key)
        self.assertEqual(left.products[0].corr_output_fingerprint, right.products[0].corr_output_fingerprint)

    def test_materialization_cycle_and_disconnected_graph_warnings(self):
        a = _material_ctx(index=0, contract="route.stage.a", stock_behavior="semi_finished", qty=1.0)
        b = _material_ctx(index=1, contract="route.stage.b", stock_behavior="semi_finished", qty=1.0)
        base = build_manufacturing_plan_from_stagecontexts([a, b])
        cyclic = ManufacturingPlan(
            plan_version=base.plan_version,
            source=base.source,
            spec_id=base.spec_id,
            order_qty=base.order_qty,
            fg_segment_key=base.fg_segment_key,
            segments=base.segments,
            edges=[
                ManufacturingEdge("previous_output", base.segments[0].key, base.segments[1].key),
                ManufacturingEdge("previous_output", base.segments[1].key, base.segments[0].key),
            ],
            warnings=[],
            metadata={},
        )

        materialized = build_materialization_plan_from_manufacturing_plan(cyclic)

        warning_codes = {warning.code for warning in materialized.warnings}
        self.assertIn("cycle_detected", warning_codes)

        disconnected = build_materialization_plan_from_manufacturing_plan(base)
        self.assertIn(
            "disconnected_output_segment",
            {warning.code for warning in disconnected.warnings},
        )

    def test_materialization_signatures_are_deterministic(self):
        first = _material_ctx(
            index=0,
            contract="route.stage.deterministic",
            stock_behavior="semi_finished",
            qty=12.0,
        )
        plan = build_manufacturing_plan_from_stagecontexts([first], order_qty=12)

        left = build_materialization_plan_from_manufacturing_plan(plan)
        right = build_materialization_plan_from_manufacturing_plan(plan)

        self.assertEqual(left.corr_plan_signature, right.corr_plan_signature)
        self.assertEqual(left.products[0].corr_material_signature, right.products[0].corr_material_signature)
        self.assertEqual(left.boms[0].corr_route_signature, right.boms[0].corr_route_signature)

    def test_materialization_sfg_key_ignores_order_level_rm_totals(self):
        first = _material_ctx(
            index=0,
            contract="route.stage.same_sfg",
            stock_behavior="semi_finished",
            qty=5.0,
        )
        first.rm_attachments = [
            {
                "role_code": "liner",
                "semantic_role": "liner",
                "category": "material",
                "source": "preview_material_row",
                "product_tmpl_id": 10,
                "writer_product_key": "product.product:501",
                "writer_uom": "kg",
                "qty_per_fg": 0.125,
                "writer_qty": 12.5,
                "total_qty": 12.5,
                "preview_metadata": {
                    "layer_no": 1,
                    "gsm": 180.0,
                    "bf": 18.0,
                    "color": "Natural Shade",
                },
            }
        ]
        second = _material_ctx(
            index=0,
            contract="route.stage.same_sfg",
            stock_behavior="semi_finished",
            qty=5.0,
        )
        second.rm_attachments = [
            {
                "role_code": "liner",
                "semantic_role": "liner",
                "category": "material",
                "source": "preview_material_row",
                "product_tmpl_id": 10,
                "writer_product_key": "product.product:501",
                "writer_uom": "kg",
                "qty_per_fg": 0.125,
                "writer_qty": 25.0,
                "total_qty": 25.0,
                "preview_metadata": {
                    "layer_no": 1,
                    "gsm": 180.0,
                    "bf": 18.0,
                    "color": "Natural Shade",
                },
            }
        ]

        left = build_materialization_plan_from_manufacturing_plan(
            build_manufacturing_plan_from_stagecontexts([first], order_qty=100.0)
        )
        right = build_materialization_plan_from_manufacturing_plan(
            build_manufacturing_plan_from_stagecontexts([second], order_qty=200.0)
        )

        self.assertEqual(left.products[0].corr_material_signature, right.products[0].corr_material_signature)
        self.assertEqual(left.products[0].corr_sfg_key, right.products[0].corr_sfg_key)

    def test_materialization_sfg_key_ignores_absolute_layer_numbers_when_material_order_matches(self):
        first = _material_ctx(
            index=0,
            contract="route.stage.same_layers",
            stock_behavior="semi_finished",
            qty=5.0,
        )
        first.rm_attachments = [
            {
                "role_code": "medium",
                "semantic_role": "medium",
                "category": "material",
                "source": "preview_material_row",
                "product_tmpl_id": 10,
                "writer_product_key": "product.product:501",
                "writer_uom": "kg",
                "qty_per_fg": 0.177,
                "preview_metadata": {
                    "layer_no": 1,
                    "source_layer_no": 1,
                    "gsm": 160.0,
                    "bf": 18.0,
                    "color": "Natural Shade",
                    "flute": "B",
                },
            },
            {
                "role_code": "inner_liner",
                "semantic_role": "liner",
                "category": "material",
                "source": "preview_material_row",
                "product_tmpl_id": 11,
                "writer_product_key": "product.product:502",
                "writer_uom": "kg",
                "qty_per_fg": 0.144,
                "preview_metadata": {
                    "layer_no": 2,
                    "source_layer_no": 2,
                    "gsm": 180.0,
                    "bf": 18.0,
                    "color": "Natural Shade",
                },
            },
        ]
        second = _material_ctx(
            index=0,
            contract="route.stage.same_layers",
            stock_behavior="semi_finished",
            qty=5.0,
        )
        second.rm_attachments = [
            {
                "role_code": "medium",
                "semantic_role": "medium",
                "category": "material",
                "source": "preview_material_row",
                "product_tmpl_id": 10,
                "writer_product_key": "product.product:501",
                "writer_uom": "kg",
                "qty_per_fg": 0.177,
                "preview_metadata": {
                    "layer_no": 2,
                    "source_layer_no": 2,
                    "gsm": 160.0,
                    "bf": 18.0,
                    "color": "Natural Shade",
                    "flute": "B",
                },
            },
            {
                "role_code": "inner_liner",
                "semantic_role": "liner",
                "category": "material",
                "source": "preview_material_row",
                "product_tmpl_id": 11,
                "writer_product_key": "product.product:502",
                "writer_uom": "kg",
                "qty_per_fg": 0.144,
                "preview_metadata": {
                    "layer_no": 3,
                    "source_layer_no": 3,
                    "gsm": 180.0,
                    "bf": 18.0,
                    "color": "Natural Shade",
                },
            },
        ]

        left = build_materialization_plan_from_manufacturing_plan(
            build_manufacturing_plan_from_stagecontexts([first], order_qty=1.0)
        )
        right = build_materialization_plan_from_manufacturing_plan(
            build_manufacturing_plan_from_stagecontexts([second], order_qty=1.0)
        )

        self.assertEqual(left.products[0].corr_material_signature, right.products[0].corr_material_signature)
        self.assertEqual(left.products[0].corr_sfg_key, right.products[0].corr_sfg_key)

    def test_materialization_sfg_key_ignores_qty_per_fg_when_material_identity_matches(self):
        first = _material_ctx(
            index=0,
            contract="route.stage.same_share",
            stock_behavior="semi_finished",
            qty=5.0,
        )
        first.rm_attachments = [
            {
                "role_code": "medium",
                "semantic_role": "medium",
                "category": "material",
                "source": "preview_material_row",
                "product_tmpl_id": 10,
                "writer_product_key": "product.product:501",
                "writer_uom": "kg",
                "qty_per_fg": 0.08832,
                "preview_metadata": {
                    "gsm": 160.0,
                    "bf": 18.0,
                    "color": "Natural Shade",
                    "flute": "B",
                },
            },
            {
                "role_code": "inner_liner",
                "semantic_role": "liner",
                "category": "material",
                "source": "preview_material_row",
                "product_tmpl_id": 11,
                "writer_product_key": "product.product:502",
                "writer_uom": "kg",
                "qty_per_fg": 0.072,
                "preview_metadata": {
                    "gsm": 180.0,
                    "bf": 18.0,
                    "color": "Natural Shade",
                },
            },
        ]
        second = _material_ctx(
            index=0,
            contract="route.stage.same_share",
            stock_behavior="semi_finished",
            qty=5.0,
        )
        second.rm_attachments = [
            {
                "role_code": "medium",
                "semantic_role": "medium",
                "category": "material",
                "source": "preview_material_row",
                "product_tmpl_id": 10,
                "writer_product_key": "product.product:501",
                "writer_uom": "kg",
                "qty_per_fg": 0.17664,
                "preview_metadata": {
                    "gsm": 160.0,
                    "bf": 18.0,
                    "color": "Natural Shade",
                    "flute": "B",
                },
            },
            {
                "role_code": "inner_liner",
                "semantic_role": "liner",
                "category": "material",
                "source": "preview_material_row",
                "product_tmpl_id": 11,
                "writer_product_key": "product.product:502",
                "writer_uom": "kg",
                "qty_per_fg": 0.144,
                "preview_metadata": {
                    "gsm": 180.0,
                    "bf": 18.0,
                    "color": "Natural Shade",
                },
            },
        ]

        left = build_materialization_plan_from_manufacturing_plan(
            build_manufacturing_plan_from_stagecontexts([first], order_qty=1.0)
        )
        right = build_materialization_plan_from_manufacturing_plan(
            build_manufacturing_plan_from_stagecontexts([second], order_qty=1.0)
        )

        self.assertEqual(left.products[0].corr_material_signature, right.products[0].corr_material_signature)
        self.assertEqual(left.products[0].corr_sfg_key, right.products[0].corr_sfg_key)

    def test_materialization_dry_run_has_no_orm_dependency(self):
        plan = build_manufacturing_plan_from_stagecontexts(
            [_material_ctx(index=0, contract="route.stage.no_orm", stock_behavior="semi_finished", qty=1.0)]
        )

        materialized = build_materialization_plan_from_manufacturing_plan(plan, mode="single_bom")

        self.assertTrue(materialized.metadata["dry_run"])
        self.assertFalse(any(hasattr(item, "_name") for item in materialized.products))

    def test_materialization_debug_payload_exposes_dry_run_shape(self):
        first = _material_ctx(
            index=0,
            contract="route.stage.debug_source",
            stock_behavior="semi_finished",
            qty=6.0,
        )
        first.rm_attachments = [
            FixtureAttachment("paper", "liner", "material", product_tmpl_id=301)
        ]
        second = _material_ctx(
            index=1,
            contract="route.stage.debug_final",
            stock_behavior="fg",
            qty=24.0,
            consumes_previous=True,
            previous_required=True,
            input_qty=6.0,
            output_kind="box",
        )
        second.consumable_attachments = [
            FixtureAttachment("ink", "ink", "consumable", product_tmpl_id=302)
        ]
        payload = {
            "spec_id": 42,
            "order_qty": 24.0,
            "phase8_shadow_runtime": {
                "stage_contexts": [asdict(first), asdict(second)],
            },
            "phase5_preview_model": {
                "materials": [],
                "operations": [],
                "routed_operations": [],
                "stages": [],
            },
        }

        debug_payload = build_materialization_debug_payload_from_stagecontext_preview_payload(
            payload,
            mode="phantom",
        )

        materialized = debug_payload["materialization_plan"]
        self.assertTrue(debug_payload["dry_run"])
        self.assertEqual(materialized["mode"], "phantom")
        self.assertIn("corr_plan_signature", materialized)
        self.assertEqual(
            {item["product_role"] for item in materialized["products"]},
            {"sfg", "fg"},
        )
        bom_roles = {item["bom_role"] for item in materialized["boms"]}
        self.assertEqual(bom_roles, {"sfg", "fg"})
        fg_bom = next(item for item in materialized["boms"] if item["bom_role"] == "fg")
        self.assertIn(
            "previous_output",
            {line["line_type"] for line in fg_bom["lines"]},
        )
        self.assertTrue(any(product["corr_sfg_key"] for product in materialized["products"]))
        self.assertFalse(any(hasattr(item, "_name") for item in materialized["products"]))

    def test_materialization_prefers_resolved_preview_rm_without_duplicates(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.preview_resolved",
            stock_behavior="semi_finished",
            qty=7.0,
        )
        ctx.rm_attachments = [
            FixtureAttachment("paper", "liner", "material", product_tmpl_id=401)
        ]
        payload = {
            "spec_id": 43,
            "order_qty": 7.0,
            "phase8_shadow_runtime": {"stage_contexts": [asdict(ctx)]},
            "phase5_preview_model": {
                "materials": [
                    {
                        "stage_contract_code": "route.stage.preview_resolved",
                        "role_code": "paper",
                        "semantic_role": "liner",
                        "category": "material",
                        "source": "preview_material_row",
                        "product_tmpl_id": 401,
                        "product_key": "product.product:401",
                        "total_qty": 3.25,
                        "uom": "kg",
                        "metadata": {"layer_no": 1},
                    }
                ],
                "operations": [],
                "routed_operations": [],
                "stages": [],
            },
        }

        debug_payload = build_materialization_debug_payload_from_stagecontext_preview_payload(payload)

        sfg_bom = debug_payload["materialization_plan"]["boms"][0]
        rm_lines = [line for line in sfg_bom["lines"] if line["line_type"] == "rm_input"]
        self.assertEqual(len(rm_lines), 1)
        self.assertEqual(rm_lines[0]["qty"], 3.25)
        self.assertEqual(rm_lines[0]["uom"], "kg")
        self.assertEqual(rm_lines[0]["metadata"]["writer_product_key"], "product.product:401")
        diagnostics = sfg_bom["metadata"]["diagnostic_only_lines"]
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["diagnostic_reason"], "zero_or_missing_qty")

    def test_materialization_excludes_zero_qty_candidate_component_lines(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.zero_qty",
            stock_behavior="semi_finished",
            qty=7.0,
        )
        ctx.rm_attachments = [
            _resolved_attachment("paper", "liner", "material", product_tmpl_id=402, qty=0.0, uom="kg")
        ]
        plan = build_manufacturing_plan_from_stagecontexts([ctx])

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        sfg_bom = materialized.boms[0]
        self.assertEqual(sfg_bom.lines, [])
        self.assertEqual(sfg_bom.metadata["diagnostic_only_lines"][0]["diagnostic_reason"], "zero_or_missing_qty")

    def test_materialization_excludes_diagnostic_only_component_lines(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.trace_only",
            stock_behavior="semi_finished",
            qty=7.0,
        )
        ctx.rm_attachments = [
            _resolved_attachment(
                "paper",
                "liner",
                "material",
                product_tmpl_id=403,
                qty=2.0,
                uom="kg",
                trace_only=True,
            )
        ]
        plan = build_manufacturing_plan_from_stagecontexts([ctx])

        materialized = build_materialization_plan_from_manufacturing_plan(plan)

        sfg_bom = materialized.boms[0]
        self.assertEqual(sfg_bom.lines, [])
        self.assertEqual(sfg_bom.metadata["diagnostic_only_lines"][0]["diagnostic_reason"], "trace_only")

    def test_enabled_corrugation_adhesive_preview_row_materializes_via_product_map(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        ctx.stage_code = "2ply"
        payload = {
            "order_qty": 1.0,
            "phase8_shadow_runtime": {"stage_contexts": [asdict(ctx)]},
            "phase5_preview_model": {
                "materials": [
                    {
                        "stage_code": "2ply",
                        "stage_contract_code": "route.stage.2ply",
                        "role": "adhesive_corrugation",
                        "semantic_role": "adhesive_corrugation",
                        "category": "consumable",
                        "material_type": "consumable",
                        "product": "rm_adhesive_corrugation",
                        "product_key": "rm_adhesive_corrugation",
                        "total_qty": 0.093,
                        "uom": "kg",
                        "quantity_source": "legacy_rm_requirement",
                        "source": "legacy_rm_requirement",
                    }
                ],
                "operations": [],
                "routed_operations": [],
                "stages": [],
            },
        }

        plan = build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        self.assertEqual(len(plan.segments[0].consumable_inputs), 1)
        edge = next(edge for edge in plan.edges if edge.edge_type == "consumable_input")
        self.assertEqual(edge.input_key, plan.segments[0].consumable_inputs[0].input_key)

        materialized = build_materialization_plan_from_manufacturing_plan(plan)
        sfg_bom = materialized.boms[0]
        self.assertEqual(len(sfg_bom.lines), 1)
        line = sfg_bom.lines[0]
        self.assertEqual(line.line_type, "consumable_input")
        self.assertEqual(line.qty, 0.093)
        self.assertEqual(line.uom, "kg")
        self.assertEqual(line.metadata["writer_product_key"], "rm_adhesive_corrugation")

        resolved = resolve_materialization_plan_dry_run(
            _resolver_env(include_sfg=False, include_bom=False, include_consumable_map=True),
            materialized,
            company_id=1,
        )
        bom_result = resolved["boms"][0]
        self.assertNotIn("component_product_not_found", bom_result["blockers"])
        diagnostic = next(item for item in bom_result["line_diagnostics"] if item["line_type"] == "consumable_input")
        self.assertEqual(diagnostic["reason"], "component_product_resolved")
        self.assertEqual(diagnostic["product_id"], 901)
        lookup = next(item for item in diagnostic["product_lookup"] if item["source"] == "consumable_product_map")
        self.assertEqual(lookup["engine_code"], "corrugation")

        payload_plan = build_orm_writer_payload_plan(
            resolved,
            materialization_plan=asdict(materialized),
            config=MaterializationWriterConfig(
                product_create_enabled=True,
                bom_create_enabled=True,
            ),
            env=_resolver_env(include_sfg=False, include_bom=False, include_consumable_map=True),
        )
        self.assertEqual(len(payload_plan["bom_line_create_values"]), 1)
        values = payload_plan["bom_line_create_values"][0]["values"]
        self.assertEqual(values["line_type"], "consumable_input")
        self.assertEqual(values["product_id"], 901)
        self.assertEqual(values["product_qty"], 0.093)
        self.assertEqual(values["product_uom_name"], "kg")

    def test_enabled_board_adhesive_preview_row_materializes_via_product_map(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.board",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        ctx.stage_code = "board"
        payload = {
            "order_qty": 1.0,
            "phase8_shadow_runtime": {"stage_contexts": [asdict(ctx)]},
            "phase5_preview_model": {
                "materials": [
                    {
                        "stage_code": "board",
                        "stage_contract_code": "route.stage.board",
                        "role": "adhesive_board",
                        "semantic_role": "adhesive_board",
                        "category": "consumable",
                        "material_type": "consumable",
                        "product": "rm_adhesive_board",
                        "product_key": "rm_adhesive_board",
                        "total_qty": 0.037,
                        "uom": "kg",
                        "quantity_source": "legacy_rm_requirement",
                        "source": "legacy_rm_requirement",
                    }
                ],
                "operations": [],
                "routed_operations": [],
                "stages": [],
            },
        }

        materialized = build_materialization_plan_from_manufacturing_plan(
            build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        )
        line = materialized.boms[0].lines[0]
        self.assertEqual(line.line_type, "consumable_input")
        self.assertEqual(line.qty, 0.037)
        self.assertEqual(line.metadata["writer_product_key"], "rm_adhesive_board")

        resolved = resolve_materialization_plan_dry_run(
            _resolver_env(include_sfg=False, include_bom=False, include_consumable_map=True),
            materialized,
            company_id=1,
        )
        diagnostic = next(item for item in resolved["boms"][0]["line_diagnostics"] if item["line_type"] == "consumable_input")
        self.assertEqual(diagnostic["product_id"], 902)
        lookup = next(item for item in diagnostic["product_lookup"] if item["source"] == "consumable_product_map")
        self.assertEqual(lookup["engine_code"], "board")

    def test_missing_consumable_product_map_reports_clear_diagnostic(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.2ply",
            stock_behavior="semi_finished",
            qty=1.0,
        )
        ctx.stage_code = "2ply"
        payload = {
            "order_qty": 1.0,
            "phase8_shadow_runtime": {"stage_contexts": [asdict(ctx)]},
            "phase5_preview_model": {
                "materials": [
                    {
                        "stage_code": "2ply",
                        "stage_contract_code": "route.stage.2ply",
                        "role": "adhesive_corrugation",
                        "semantic_role": "adhesive_corrugation",
                        "category": "consumable",
                        "material_type": "consumable",
                        "product": "rm_adhesive_corrugation",
                        "product_key": "rm_adhesive_corrugation",
                        "total_qty": 0.093,
                        "uom": "kg",
                        "quantity_source": "legacy_rm_requirement",
                        "source": "legacy_rm_requirement",
                    }
                ],
                "operations": [],
                "routed_operations": [],
                "stages": [],
            },
        }
        materialized = build_materialization_plan_from_manufacturing_plan(
            build_manufacturing_plan_from_stagecontext_preview_payload(payload)
        )

        resolved = resolve_materialization_plan_dry_run(
            _resolver_env(include_sfg=False, include_bom=False, include_consumable_map=False),
            materialized,
            company_id=1,
        )

        bom_result = resolved["boms"][0]
        self.assertIn("component_product_not_found", bom_result["blockers"])
        diagnostic = next(item for item in bom_result["line_diagnostics"] if item["line_type"] == "consumable_input")
        self.assertEqual(diagnostic["reason"], "component_product_not_found")
        lookup = next(item for item in diagnostic["product_lookup"] if item["source"] == "consumable_product_map")
        self.assertEqual(
            lookup["skipped_reason"],
            "Consumable product not configured for Corrugation Adhesive",
        )

    def test_resolver_dry_run_reuses_matching_product_and_bom(self):
        materialized = _resolved_sfg_materialization(bom_signature="sig-match")
        env = _resolver_env(bom_signature=_bom_signature(asdict(materialized.boms[0])))

        resolved = resolve_materialization_plan_dry_run(env, materialized, company_id=1)

        product_result = resolved["products"][0]
        bom_result = resolved["boms"][0]
        self.assertEqual(product_result["action"], "reuse_product")
        self.assertEqual(product_result["existing_product_match"]["id"], 601)
        self.assertEqual(bom_result["action"], "reuse_bom")
        self.assertEqual(bom_result["existing_bom_match"]["id"], 701)
        self.assertEqual(resolved["blockers"], [])
        self.assertFalse(resolved["metadata"]["writes_enabled"])

    def test_resolver_dry_run_reports_create_product_and_bom_without_writes(self):
        materialized = _resolved_sfg_materialization(corr_sfg_key="sfg:new", bom_signature="sig-new")
        env = _resolver_env(include_sfg=False, include_bom=False)

        resolved = resolve_materialization_plan_dry_run(env, materialized, company_id=1)

        self.assertEqual(resolved["products"][0]["action"], "create_product")
        self.assertEqual(resolved["boms"][0]["action"], "create_bom")
        self.assertEqual(resolved["blockers"], [])
        self.assertFalse(resolved["metadata"]["creates_enabled"])

    def test_resolver_dry_run_reports_stale_bom_creates_new_version(self):
        materialized = _resolved_sfg_materialization(bom_signature="sig-current")
        env = _resolver_env(bom_signature="sig-stale")

        resolved = resolve_materialization_plan_dry_run(env, materialized, company_id=1)

        self.assertEqual(resolved["products"][0]["action"], "reuse_product")
        self.assertEqual(resolved["boms"][0]["action"], "create_bom")
        self.assertEqual(resolved["boms"][0]["stale_bom_match"]["id"], 701)
        self.assertEqual(resolved["boms"][0]["reason"], "stale_signature_match_create_new_version")

    def test_resolver_dry_run_blocks_invalid_candidate_lines(self):
        materialized = _resolved_sfg_materialization()
        bom_data = asdict(materialized.boms[0])
        bom_data["lines"][0]["qty"] = 0.0
        bom_data["lines"][0]["metadata"]["diagnostic_only"] = True
        plan_data = asdict(materialized)
        plan_data["boms"] = [bom_data]
        env = _resolver_env(include_bom=False)

        resolved = resolve_materialization_plan_dry_run(env, plan_data, company_id=1)

        self.assertEqual(resolved["boms"][0]["action"], "blocked_bom")
        self.assertIn("line_qty_not_positive", resolved["boms"][0]["blockers"])
        self.assertIn("diagnostic_only_line_in_create_payload", resolved["boms"][0]["blockers"])

    def test_resolver_dry_run_accepts_preview_product_id_variant_resolution(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.product_id_resolution",
            stock_behavior="semi_finished",
            qty=7.0,
        )
        payload = {
            "spec_id": 44,
            "order_qty": 7.0,
            "paper_resolution": [
                {
                    "role": "liner",
                    "layer_no": 1,
                    "product_id": 501,
                    "product_name": "KP-180-110-NS-18",
                    "uom_name": "kg",
                    "required_deckle_cm": 110,
                    "selected_layout": {
                        "selection_source": "wizard_selected_option",
                        "up_count": 2,
                        "final_sheet_width_mm": 1100.0,
                        "final_sheet_length_mm": 900.0,
                    },
                }
            ],
            "phase8_shadow_runtime": {"stage_contexts": [asdict(ctx)]},
            "phase5_preview_model": {
                "materials": [
                    {
                        "stage_contract_code": "route.stage.product_id_resolution",
                        "role_code": "paper",
                        "semantic_role": "liner",
                        "category": "material",
                        "source": "preview_material_row",
                        "total_qty": 3.25,
                        "uom": "kg",
                        "metadata": {"layer_no": 1},
                    }
                ],
                "operations": [],
                "routed_operations": [],
                "stages": [],
            },
        }
        debug_payload = build_materialization_debug_payload_from_stagecontext_preview_payload(payload)
        materialized = debug_payload["materialization_plan"]
        materialized["products"][0]["corr_sfg_key"] = "sfg:new"

        resolved = resolve_materialization_plan_dry_run(
            _resolver_env(include_sfg=False, include_bom=False),
            materialized,
            company_id=1,
        )

        line = materialized["boms"][0]["lines"][0]
        self.assertEqual(line["metadata"]["product_id"], 501)
        self.assertEqual(line["metadata"]["writer_product_key"], "product.product:501")
        self.assertEqual(line["metadata"]["product_name"], "KP-180-110-NS-18")
        self.assertEqual(line["metadata"]["required_deckle_cm"], 110)
        self.assertEqual(line["metadata"]["selected_layout"]["selection_source"], "wizard_selected_option")
        self.assertEqual(debug_payload["layout_diagnostics"]["selected_layout_up_count"], 2)
        self.assertEqual(debug_payload["layout_diagnostics"]["selected_layout_sheet_width_mm"], 1100.0)
        self.assertEqual(debug_payload["layout_diagnostics"]["selection_source"], "wizard_selected_option")
        self.assertEqual(resolved["boms"][0]["action"], "create_bom")
        self.assertEqual(
            resolved["resolver_layout_signature"],
            materialized["boms"][0]["corr_layout_signature"],
        )
        self.assertNotIn("component_product_not_found", resolved["boms"][0]["blockers"])

    def test_resolver_product_lookup_diagnostic_reports_searched_product_variant(self):
        materialized = _resolved_sfg_materialization()
        bom_data = asdict(materialized.boms[0])
        bom_data["lines"][0]["metadata"]["product_id"] = 999999
        bom_data["lines"][0]["metadata"]["writer_product_key"] = "product.product:999999"
        bom_data["lines"][0]["metadata"]["product_name"] = "Missing Variant"
        bom_data["lines"][0]["metadata"].pop("product_tmpl_id", None)
        plan_data = asdict(materialized)
        plan_data["boms"] = [bom_data]

        resolved = resolve_materialization_plan_dry_run(
            _resolver_env(include_bom=False),
            plan_data,
            company_id=1,
        )

        diagnostic = resolved["boms"][0]["line_diagnostics"][0]
        self.assertEqual(diagnostic["reason"], "component_product_not_found")
        self.assertEqual(diagnostic["product_lookup"][0]["searched_model"], "product.product")
        self.assertEqual(diagnostic["product_lookup"][0]["searched_id"], 999999)
        self.assertFalse(diagnostic["product_lookup"][0]["exists"])
        self.assertFalse(diagnostic["product_lookup"][0]["sudo_browse_exists"])
        self.assertFalse(diagnostic["product_lookup"][0]["active_test_false_exists"])
        self.assertEqual(diagnostic["product_lookup"][0]["search_ids"], [])
        self.assertEqual(diagnostic["product_lookup"][0]["db_name"], "fake_test_db")

    def test_resolver_uses_live_paper_resolution_when_preview_product_id_is_stale(self):
        materialized = _resolved_sfg_materialization()
        bom_data = asdict(materialized.boms[0])
        bom_data["lines"][0]["metadata"].update(
            {
                "product_id": 2946,
                "writer_product_key": "product.product:2946",
                "product_name": "KP-180-110-NS-18",
                "required_deckle_cm": 110.0,
                "preview_metadata": {
                    "resolution": {
                        "gsm": 180,
                        "bf": 18,
                        "color": "NS",
                    }
                },
            }
        )
        bom_data["lines"][0]["metadata"].pop("product_tmpl_id", None)
        plan_data = asdict(materialized)
        plan_data["boms"] = [bom_data]

        resolved = resolve_materialization_plan_dry_run(
            _resolver_env(include_sfg=False, include_bom=False, live_paper_resolution=True),
            plan_data,
            company_id=1,
        )

        self.assertEqual(resolved["boms"][0]["action"], "create_bom")
        self.assertEqual(resolved["boms"][0]["blockers"], [])
        diagnostic = resolved["boms"][0]["line_diagnostics"][0]
        self.assertEqual(diagnostic["reason"], "component_product_resolved_by_live_paper_resolution")
        live_lookup = next(item for item in diagnostic["product_lookup"] if item["source"] == "live_paper_resolution")
        self.assertFalse(live_lookup["skipped"])
        self.assertEqual(live_lookup["status"], "exact")
        self.assertEqual(live_lookup["product_id"], 501)
        self.assertIn(
            "live_paper_resolution",
            [item["source"] for item in diagnostic["product_lookup"]],
        )

    def test_resolver_falls_back_from_template_id_to_default_variant(self):
        materialized = _resolved_sfg_materialization()
        bom_data = asdict(materialized.boms[0])
        bom_data["lines"][0]["metadata"].update(
            {
                "product_id": None,
                "product_tmpl_id": 2,
                "writer_product_key": "",
                "product_name": "Top Sheets",
                "source_kind": "preview_material_row",
            }
        )
        bom_data["lines"][0]["uom"] = "unit"
        plan_data = asdict(materialized)
        plan_data["boms"] = [bom_data]

        resolved = resolve_materialization_plan_dry_run(
            _resolver_env(include_sfg=False, include_bom=False),
            plan_data,
            company_id=1,
        )

        self.assertEqual(resolved["boms"][0]["action"], "create_bom")
        self.assertEqual(resolved["boms"][0]["blockers"], [])

    def test_resolver_normalizes_unit_alias_to_product_uom(self):
        materialized = _resolved_sfg_materialization()
        bom_data = asdict(materialized.boms[0])
        bom_data["lines"][0]["metadata"].update(
            {
                "product_id": 2947,
                "product_tmpl_id": None,
                "writer_product_key": "product.product:2947",
                "product_name": "Top Sheets",
                "source_kind": "preview_material_row",
            }
        )
        bom_data["lines"][0]["uom"] = "unit"
        plan_data = asdict(materialized)
        plan_data["boms"] = [bom_data]

        resolved = resolve_materialization_plan_dry_run(
            _resolver_env(include_sfg=False, include_bom=False, unit_uom_name="Units"),
            plan_data,
            company_id=1,
        )

        self.assertEqual(resolved["boms"][0]["action"], "create_bom")
        self.assertEqual(resolved["boms"][0]["blockers"], [])

    def test_resolver_normalizes_pcs_alias_to_product_uom(self):
        materialized = _resolved_sfg_materialization()
        bom_data = asdict(materialized.boms[0])
        bom_data["lines"][0]["metadata"].update(
            {
                "product_id": 2947,
                "product_tmpl_id": None,
                "writer_product_key": "product.product:2947",
                "product_name": "Top Sheets",
                "source_kind": "preview_material_row",
            }
        )
        bom_data["lines"][0]["uom"] = "pcs"
        plan_data = asdict(materialized)
        plan_data["boms"] = [bom_data]

        resolved = resolve_materialization_plan_dry_run(
            _resolver_env(include_sfg=False, include_bom=False, unit_uom_name="Units"),
            plan_data,
            company_id=1,
        )

        self.assertEqual(resolved["boms"][0]["action"], "create_bom")
        self.assertEqual(resolved["boms"][0]["blockers"], [])

    def test_materialization_propagates_top_sheet_product_variant_from_metadata(self):
        ctx = _material_ctx(
            index=0,
            contract="route.stage.top_sheet",
            stock_behavior="semi_finished",
            qty=7.0,
        )
        payload = {
            "spec_id": 45,
            "order_qty": 7.0,
            "phase8_shadow_runtime": {"stage_contexts": [asdict(ctx)]},
            "phase5_preview_model": {
                "materials": [
                    {
                        "stage_contract_code": "route.stage.top_sheet",
                        "role_code": "top_sheet_external",
                        "semantic_role": "top_sheet_external",
                        "category": "material",
                        "source": "preview_material_row",
                        "product_tmpl_id": 2,
                        "product": "Top Sheets",
                        "total_qty": 7.0,
                        "uom": "unit",
                        "metadata": {
                            "top_sheet_product_id": 2947,
                        },
                    }
                ],
                "operations": [],
                "routed_operations": [],
                "stages": [],
            },
        }

        debug_payload = build_materialization_debug_payload_from_stagecontext_preview_payload(payload)

        line = debug_payload["materialization_plan"]["boms"][0]["lines"][0]
        self.assertEqual(line["metadata"]["product_id"], 2947)
        self.assertEqual(line["metadata"]["writer_product_key"], "product.product:2947")

    def test_resolver_dry_run_blocks_previous_output_without_sfg_candidate(self):
        materialized = _resolved_sfg_materialization()
        bom_data = asdict(materialized.boms[0])
        bom_data["lines"] = [
            {
                "line_key": "bom:test:previous:missing",
                "bom_key": bom_data["bom_key"],
                "line_type": "previous_output",
                "product_candidate_key": "product:missing",
                "source_segment_key": "missing",
                "input_key": None,
                "role_code": None,
                "qty": 1.0,
                "uom": "unit",
                "operation_segment_key": bom_data["segment_key"],
                "metadata": {},
            }
        ]
        plan_data = asdict(materialized)
        plan_data["boms"] = [bom_data]

        resolved = resolve_materialization_plan_dry_run(_resolver_env(), plan_data, company_id=1)

        self.assertEqual(resolved["boms"][0]["action"], "blocked_bom")
        self.assertIn(
            "previous_output_missing_sfg_product_candidate",
            resolved["boms"][0]["blockers"],
        )

    def test_resolver_dry_run_blocks_company_and_uom_incompatibility(self):
        materialized = _resolved_sfg_materialization()
        bom_data = asdict(materialized.boms[0])
        bom_data["lines"][0]["uom"] = "unit"
        plan_data = asdict(materialized)
        plan_data["boms"] = [bom_data]
        env = _resolver_env(company_id=2)

        resolved = resolve_materialization_plan_dry_run(env, plan_data, company_id=1)

        self.assertEqual(resolved["products"][0]["action"], "blocked_product")
        self.assertEqual(resolved["boms"][0]["action"], "blocked_bom")
        self.assertIn("company_incompatible", resolved["boms"][0]["blockers"])
        self.assertIn("line_uom_category_incompatible", resolved["boms"][0]["blockers"])

    def test_resolver_dry_run_missing_component_diagnostic_includes_line_details(self):
        materialized = _resolved_sfg_materialization()
        bom_data = asdict(materialized.boms[0])
        bom_data["lines"][0]["metadata"].pop("product_id", None)
        bom_data["lines"][0]["metadata"]["writer_product_key"] = "KP-180-110-NS-18"
        bom_data["lines"][0]["metadata"].pop("product_tmpl_id", None)
        bom_data["lines"][0]["metadata"]["product_name"] = "KP-180-110-NS-18"
        bom_data["lines"][0]["metadata"]["source_kind"] = "paper_resolution"
        plan_data = asdict(materialized)
        plan_data["boms"] = [bom_data]

        resolved = resolve_materialization_plan_dry_run(
            _resolver_env(include_bom=False),
            plan_data,
            company_id=1,
        )

        diagnostic = resolved["boms"][0]["line_diagnostics"][0]
        self.assertEqual(diagnostic["reason"], "component_product_not_found")
        self.assertEqual(diagnostic["bom_candidate_key"], bom_data["bom_key"])
        self.assertEqual(diagnostic["product_name"], "KP-180-110-NS-18")
        self.assertEqual(diagnostic["source_kind"], "paper_resolution")
        self.assertEqual(diagnostic["role"], "liner")

    def test_resolver_live_paper_resolution_skipped_reason_is_reported(self):
        materialized = _resolved_sfg_materialization()
        bom_data = asdict(materialized.boms[0])
        bom_data["lines"][0]["metadata"].update(
            {
                "product_id": None,
                "writer_product_key": "",
                "product_name": "KP-180-110-NS-18",
                "source_kind": "paper_resolution",
                "preview_metadata": {
                    "resolution": {
                        "gsm": 180,
                        "bf": 18,
                        "color": "NS",
                    }
                },
            }
        )
        bom_data["lines"][0]["metadata"].pop("product_tmpl_id", None)
        bom_data["lines"][0]["metadata"].pop("required_deckle_cm", None)
        plan_data = asdict(materialized)
        plan_data["boms"] = [bom_data]

        resolved = resolve_materialization_plan_dry_run(
            _resolver_env(include_bom=False, live_paper_resolution=True),
            plan_data,
            company_id=1,
        )

        diagnostic = resolved["boms"][0]["line_diagnostics"][0]
        self.assertEqual(diagnostic["reason"], "component_product_not_found")
        live_lookup = next(item for item in diagnostic["product_lookup"] if item["source"] == "live_paper_resolution")
        self.assertTrue(live_lookup["skipped"])
        self.assertEqual(live_lookup["skipped_reason"], "missing_required_deckle_cm")

    def test_materialization_writer_plan_is_disabled_and_ordered(self):
        resolver_dry_run = {
            "resolver_version": "test",
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:sfg",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:test",
                    "action": "create_product",
                },
                {
                    "product_candidate_key": "product:fg",
                    "product_role": "fg",
                    "corr_sfg_key": None,
                    "action": "reuse_product",
                    "existing_product_match": {"id": 10},
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:sfg",
                    "product_candidate_key": "product:sfg",
                    "bom_role": "sfg",
                    "bom_type": "normal",
                    "action": "create_bom",
                },
                {
                    "bom_candidate_key": "bom:fg",
                    "product_candidate_key": "product:fg",
                    "bom_role": "fg",
                    "bom_type": "normal",
                    "action": "create_bom",
                },
            ],
            "blockers": [],
        }

        plan = build_materialization_writer_plan(resolver_dry_run)

        self.assertTrue(plan["dry_run"])
        self.assertFalse(plan["writes_enabled"])
        self.assertFalse(plan["creates_enabled"])
        self.assertFalse(plan["archives_enabled"])
        self.assertFalse(plan["execution_allowed"])
        self.assertEqual(plan["disabled_reason"], "context_flag_disabled")
        self.assertEqual(
            [(step["step_type"], step["candidate_key"], step["action"]) for step in plan["steps"]],
            [
                ("product", "product:sfg", "create_product"),
                ("product", "product:fg", "reuse_product"),
                ("bom", "bom:sfg", "create_bom"),
                ("bom", "bom:fg", "create_bom"),
            ],
        )

    def test_materialization_writer_execute_is_guarded_even_with_flags(self):
        resolver_dry_run = {"products": [], "boms": [], "blockers": []}

        with self.assertRaises(MaterializationWriterDisabledError):
            execute_materialization_writer(None, resolver_dry_run)
        with self.assertRaises(MaterializationWriterDisabledError):
            execute_materialization_writer(
                None,
                resolver_dry_run,
                config=MaterializationWriterConfig(
                    writes_enabled=True,
                    creates_enabled=True,
                    allow_multi_level_boms=True,
                ),
            )

    def test_materialization_writer_config_is_disabled_by_default(self):
        config = writer_config_from_env(FakeWriterEnv())

        self.assertFalse(config.writes_enabled)
        self.assertFalse(config.creates_enabled)
        self.assertFalse(config.allow_multi_level_boms)
        self.assertFalse(config.archive_delete_enabled)
        self.assertFalse(config.mo_creation_enabled)

    def test_materialization_writer_config_requires_context_and_system_parameter(self):
        context_only = writer_config_from_env(
            FakeWriterEnv(context={"corr_materialization_writer_enabled": True})
        )
        param_only = writer_config_from_env(
            FakeWriterEnv(params={"corr_mrp_bridge.materialization_writer_enabled": "1"})
        )
        both = writer_config_from_env(
            FakeWriterEnv(
                context={"corr_materialization_writer_enabled": True},
                params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
            )
        )

        self.assertFalse(context_only.writes_enabled)
        self.assertFalse(param_only.writes_enabled)
        self.assertTrue(both.writes_enabled)
        self.assertTrue(both.creates_enabled)
        self.assertTrue(both.allow_multi_level_boms)
        self.assertFalse(both.archives_enabled)
        self.assertFalse(both.archive_delete_enabled)
        self.assertFalse(both.mo_creation_enabled)

    def test_guarded_writer_implementation_plan_keeps_real_writes_disabled(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:sfg",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:test",
                    "action": "create_product",
                },
                {
                    "product_candidate_key": "product:fg",
                    "product_role": "fg",
                    "action": "reuse_product",
                    "existing_product_match": {"id": 10},
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:sfg",
                    "bom_role": "sfg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:sfg",
                    "action": "create_bom",
                },
                {
                    "bom_candidate_key": "bom:fg",
                    "bom_role": "fg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:fg",
                    "action": "create_bom",
                },
            ],
            "blockers": [],
        }

        plan = build_guarded_writer_implementation_plan(
            resolver_dry_run,
            config=writer_config_from_env(
                FakeWriterEnv(
                    context={"corr_materialization_writer_enabled": True},
                    params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
                )
            ),
        )

        self.assertFalse(plan["real_orm_writes_enabled"])
        self.assertFalse(plan["execution_allowed"])
        self.assertTrue(plan["no_mo_creation"])
        self.assertTrue(plan["no_archive_delete"])
        self.assertEqual(
            [(step["action"], step["guard_status"], step["writes_enabled"]) for step in plan["product_write_steps"]],
            [
                ("create_product", "ready_when_writer_enabled", False),
                ("reuse_product", "ready_when_writer_enabled", False),
            ],
        )
        self.assertEqual(
            [(step["action"], step["guard_status"], step["writes_enabled"]) for step in plan["bom_write_steps"]],
            [
                ("create_bom", "ready_when_writer_enabled", False),
                ("create_bom", "ready_when_writer_enabled", False),
            ],
        )

    def test_guarded_writer_implementation_plan_rejects_archive_and_mo_scope(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [],
            "boms": [
                {
                    "bom_candidate_key": "bom:sfg",
                    "bom_role": "sfg",
                    "action": "archive_bom_candidate",
                    "stale_bom_match": {"id": 99},
                }
            ],
            "blockers": [],
        }

        plan = build_guarded_writer_implementation_plan(resolver_dry_run)

        self.assertEqual(plan["unsupported_steps"][0]["action"], "archive_bom_candidate")
        self.assertEqual(plan["unsupported_steps"][0]["guard_status"], "unsupported_in_d4")
        self.assertTrue(plan["no_mo_creation"])
        self.assertTrue(plan["no_archive_delete"])

    def test_orm_writer_payload_plan_prepares_values_without_writes(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:2ply",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:2ply",
                    "corr_plan_signature": "sig-plan",
                    "corr_segment_key": "contract:route.stage.2ply",
                    "product_type_hint": "consu_storable",
                    "route_policy": "manufacture",
                    "uom": "sheet",
                    "action": "create_product",
                },
                {
                    "product_candidate_key": "product:fg",
                    "product_role": "fg",
                    "action": "reuse_product",
                    "existing_product_match": {"id": 900},
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:contract:route.stage.2ply",
                    "bom_role": "sfg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:2ply",
                    "action": "create_bom",
                }
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:contract:route.stage.2ply",
                    "segment_key": "contract:route.stage.2ply",
                    "corr_plan_signature": "sig-plan",
                    "lines": [
                        {
                            "line_key": "line:liner",
                            "line_type": "rm_input",
                            "qty": 10.0,
                            "uom": "kg",
                            "role_code": "liner",
                            "operation_segment_key": "contract:route.stage.2ply",
                            "metadata": {
                                "semantic_role": "liner",
                                "product_id": 2946,
                                "writer_product_key": "product.product:2946",
                                "layer_no": 2,
                                "flute_code": "B",
                                "gsm": 180.0,
                            },
                        }
                    ],
                    "operations": [
                        {
                            "operation_candidate_key": "op:2ply",
                            "segment_key": "contract:route.stage.2ply",
                            "operation_name": "2ply Corrugation",
                            "sequence": 10,
                            "input_qty": 0.0,
                            "output_qty": 100.0,
                            "uom": "sheet",
                            "stage_contract_code": "route.stage.2ply",
                            "stage_template_id": 1,
                            "stage_code": "2ply",
                        }
                    ],
                }
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=writer_config_from_env(
                FakeWriterEnv(
                    context={"corr_materialization_writer_enabled": True},
                    params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
                )
            ),
        )

        self.assertFalse(payload_plan["real_orm_writes_enabled"])
        self.assertTrue(payload_plan["payloads_are_values_only"])
        self.assertTrue(payload_plan["no_create_calls"])
        self.assertTrue(payload_plan["no_write_calls"])
        self.assertTrue(payload_plan["no_mo_creation"])
        self.assertEqual(payload_plan["product_template_create_values"], payload_plan["product_create_vals"])
        self.assertEqual(payload_plan["bom_create_values"], payload_plan["bom_create_vals"])
        self.assertEqual(payload_plan["bom_line_create_values"], payload_plan["bom_line_create_vals"])
        self.assertEqual(payload_plan["operation_create_values"], payload_plan["bom_operation_create_vals"])
        self.assertEqual(payload_plan["product_create_vals"][0]["values"]["corr_sfg_key"], "sfg:2ply")
        self.assertEqual(payload_plan["bom_create_vals"][0]["values"]["product_candidate_key"], "product:2ply")
        self.assertEqual(payload_plan["bom_line_create_vals"][0]["values"]["product_id"], 2946)
        self.assertEqual(payload_plan["bom_line_create_vals"][0]["values"]["product_qty"], 10.0)
        self.assertEqual(payload_plan["bom_line_create_vals"][0]["values"]["corr_role"], "liner")
        self.assertEqual(payload_plan["bom_line_create_vals"][0]["values"]["corr_layer_no"], 2)
        self.assertEqual(payload_plan["bom_line_create_vals"][0]["values"]["corr_flute_code"], "B")
        self.assertEqual(payload_plan["bom_line_create_vals"][0]["values"]["corr_gsm"], 180.0)
        self.assertTrue(payload_plan["bom_line_create_vals"][0]["values"]["corr_engine_generated"])
        self.assertEqual(payload_plan["bom_operation_create_vals"][0]["operation_scope"], "local_bom_operations")
        self.assertEqual(payload_plan["bom_operation_create_vals"][0]["values"]["name"], "2ply Corrugation")

    def test_cutting_sfg_name_uses_segment_before_template_contract_fallback(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:cutting",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:cutting",
                    "corr_segment_key": "contract:route.stage.cutting",
                    "product_type_hint": "consu_storable",
                    "route_policy": "manufacture",
                    "uom": "sheet",
                    "action": "create_product",
                }
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:contract:route.stage.cutting",
                    "bom_role": "sfg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:cutting",
                    "action": "create_bom",
                }
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:contract:route.stage.cutting",
                    "segment_key": "contract:route.stage.cutting",
                    "product_candidate_key": "product:cutting",
                    "corr_plan_signature": "sig-plan",
                    "metadata": {
                        "output_sheet_length_mm": 620.0,
                        "output_sheet_width_mm": 600.0,
                    },
                    "lines": [
                        {
                            "line_key": "line:top-roll",
                            "line_type": "rm_input",
                            "qty": 0.033,
                            "uom": "kg",
                            "role_code": "top_roll",
                            "operation_segment_key": "contract:route.stage.cutting",
                            "metadata": {"semantic_role": "top_roll", "product_id": 2946, "gsm": 180.0, "bf": 18.0},
                        }
                    ],
                    "operations": [
                        {
                            "operation_candidate_key": "op:cutting",
                            "segment_key": "contract:route.stage.cutting",
                            "operation_name": "",
                            "sequence": 10,
                            "stage_contract_code": "Template:15",
                            "stage_template_id": 15,
                        }
                    ],
                }
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=writer_config_from_env(
                FakeWriterEnv(
                    context={"corr_materialization_writer_enabled": True},
                    params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
                )
            ),
        )

        self.assertEqual(payload_plan["product_create_vals"][0]["values"]["name"], "Cutting 62x60 cm (180 GSM BF:18)")

    def test_template_fallback_top_sheet_material_names_cutting_sfg(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:template-cut",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:global:template-cut",
                    "product_type_hint": "consu_storable",
                    "route_policy": "manufacture",
                    "uom": "sheet",
                    "action": "create_product",
                }
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:template-cut",
                    "bom_role": "sfg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:template-cut",
                    "action": "create_bom",
                }
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:template-cut",
                    "product_candidate_key": "product:template-cut",
                    "metadata": {
                        "output_sheet_length_mm": 620.0,
                        "output_sheet_width_mm": 600.0,
                    },
                    "lines": [
                        {
                            "line_key": "line:top-sheet",
                            "line_type": "rm_input",
                            "qty": 0.033,
                            "uom": "kg",
                            "role_code": "top_sheet",
                            "operation_segment_key": "contract:stage_template:15",
                            "metadata": {"semantic_role": "top_sheet", "product_id": 3303, "gsm": 180.0, "bf": 18.0},
                        }
                    ],
                    "operations": [
                        {
                            "operation_candidate_key": "op:template-cut",
                            "operation_name": "",
                            "sequence": 10,
                            "stage_contract_code": "Template:15",
                            "stage_template_id": 15,
                        }
                    ],
                }
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=writer_config_from_env(
                FakeWriterEnv(
                    context={"corr_materialization_writer_enabled": True},
                    params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
                )
            ),
        )

        self.assertEqual(payload_plan["product_create_vals"][0]["values"]["name"], "Top-sheet Cut 62x60 cm (180 GSM BF:18)")

    def test_printing_sfg_name_inherits_cutting_surface_spec(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:cut",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:cut",
                    "corr_segment_key": "contract:route.stage.cutting",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
                {
                    "product_candidate_key": "product:print",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:print",
                    "corr_segment_key": "contract:route.stage.ts_print",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
            ],
            "boms": [
                {"bom_candidate_key": "bom:cut", "bom_role": "sfg", "bom_type": "normal", "product_candidate_key": "product:cut", "action": "create_bom"},
                {"bom_candidate_key": "bom:print", "bom_role": "sfg", "bom_type": "normal", "product_candidate_key": "product:print", "action": "create_bom"},
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:cut",
                    "segment_key": "contract:route.stage.cutting",
                    "product_candidate_key": "product:cut",
                    "metadata": {"output_sheet_length_mm": 620.0, "output_sheet_width_mm": 600.0},
                    "lines": [
                        {
                            "line_key": "line:top-roll",
                            "line_type": "rm_input",
                            "qty": 0.033,
                            "uom": "kg",
                            "role_code": "top_roll",
                            "metadata": {"semantic_role": "top_roll", "product_id": 3303, "gsm": 180.0, "bf": 18.0},
                        }
                    ],
                    "operations": [{"operation_candidate_key": "op:cut", "stage_code": "cutting", "sequence": 10}],
                },
                {
                    "bom_key": "bom:print",
                    "segment_key": "contract:route.stage.ts_print",
                    "product_candidate_key": "product:print",
                    "metadata": {"output_sheet_length_mm": 620.0, "output_sheet_width_mm": 600.0},
                    "lines": [
                        {
                            "line_key": "line:prev-cut",
                            "line_type": "previous_output",
                            "qty": 1.0,
                            "uom": "sheet",
                            "product_candidate_key": "product:cut",
                        }
                    ],
                    "operations": [{"operation_candidate_key": "op:print", "stage_code": "ts_print", "sequence": 15}],
                },
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=writer_config_from_env(
                FakeWriterEnv(
                    context={"corr_materialization_writer_enabled": True},
                    params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
                )
            ),
        )

        names = {item["candidate_key"]: item["values"]["name"] for item in payload_plan["product_create_vals"]}
        self.assertEqual(names["product:print"], "Top-sheet Printed 62x60 cm (180 GSM BF:18)")

    def test_orm_writer_payload_plan_uses_human_readable_sfg_names_and_metadata(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:2ply",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-2ply",
                    "corr_plan_signature": "sig-2ply",
                    "corr_material_signature": "mat-2ply",
                    "corr_segment_key": "contract:route.stage.2ply",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
                {
                    "product_candidate_key": "product:board-roll",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-board-roll",
                    "corr_plan_signature": "sig-board-roll",
                    "corr_material_signature": "mat-board-roll",
                    "corr_segment_key": "contract:route.stage.board",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
                {
                    "product_candidate_key": "product:die-roll",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-die-roll",
                    "corr_plan_signature": "sig-die-roll",
                    "corr_material_signature": "mat-die-roll",
                    "corr_segment_key": "contract:route.stage.die_cutting",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:2ply",
                    "bom_role": "sfg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:2ply",
                    "action": "create_bom",
                },
                {
                    "bom_candidate_key": "bom:board-roll",
                    "bom_role": "sfg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:board-roll",
                    "action": "create_bom",
                },
                {
                    "bom_candidate_key": "bom:die-roll",
                    "bom_role": "sfg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:die-roll",
                    "action": "create_bom",
                },
            ],
            "blockers": [],
        }
        layout = {"option": {"final_sheet_length_mm": 1000.0, "final_sheet_width_mm": 850.0}}
        materialization_plan = {
            "mode": "stockable",
            "products": [],
            "boms": [
                {
                    "bom_key": "bom:2ply",
                    "product_candidate_key": "product:2ply",
                    "segment_key": "contract:route.stage.2ply",
                    "corr_plan_signature": "sig-2ply",
                    "lines": [
                        {
                            "line_key": "line:liner",
                            "role_code": "liner",
                            "metadata": {
                                "semantic_role": "liner",
                                "gsm": 200.0,
                                "bf": 22.0,
                                "flute_code": "B",
                                "selected_layout": layout,
                            },
                        },
                        {
                            "line_key": "line:medium",
                            "role_code": "medium",
                            "metadata": {
                                "semantic_role": "medium",
                                "gsm": 190.0,
                                "bf": 20.0,
                                "flute_code": "B",
                                "selected_layout": layout,
                            },
                        },
                    ],
                    "operations": [{"stage_code": "2ply", "operation_name": "2ply Corrugation"}],
                },
                {
                    "bom_key": "bom:board-roll",
                    "product_candidate_key": "product:board-roll",
                    "segment_key": "contract:route.stage.board",
                    "corr_plan_signature": "sig-board-roll",
                    "lines": [
                        {
                            "line_key": "line:top-roll",
                            "role_code": "top_roll",
                            "metadata": {
                                "semantic_role": "top_roll",
                                "top_sheet_roll_code": "KRFT-180-110-B",
                                "product_name": "Do Not Use This Template Name",
                                "selected_layout": layout,
                            },
                        }
                    ],
                    "operations": [{"stage_code": "board", "operation_name": "Board Making"}],
                },
                {
                    "bom_key": "bom:die-roll",
                    "product_candidate_key": "product:die-roll",
                    "segment_key": "contract:route.stage.die_cutting",
                    "corr_plan_signature": "sig-die-roll",
                    "lines": [
                        {
                            "line_key": "line:top-roll",
                            "role_code": "top_roll",
                            "metadata": {
                                "semantic_role": "top_roll",
                                "top_sheet_roll_code": "KRFT-180-110-B",
                                "selected_layout": layout,
                            },
                        }
                    ],
                    "operations": [{"stage_code": "die_cutting", "operation_name": "Die Cutting"}],
                },
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
        )

        values_by_key = {
            item["candidate_key"]: item["values"]
            for item in payload_plan["product_create_vals"]
        }
        self.assertEqual(
            values_by_key["product:2ply"]["name"],
            "2-Ply 100x85 cm (B:200/190 GSM BF:22/20)",
        )
        self.assertEqual(
            values_by_key["product:board-roll"]["name"],
            "Board 100x85 cm (ROLL:KRFT-180-110-B)",
        )
        self.assertEqual(
            values_by_key["product:die-roll"]["name"],
            "Die Cutting (ROLL:KRFT-180-110-B)",
        )
        self.assertEqual(values_by_key["product:2ply"]["corr_sfg_key"], "sfg:key-2ply")
        self.assertEqual(values_by_key["product:2ply"]["corr_sfg_type"], "2ply")
        self.assertEqual(values_by_key["product:2ply"]["corr_stage_code"], "2ply")
        self.assertEqual(values_by_key["product:2ply"]["corr_sheet_length_mm"], 1000.0)
        self.assertEqual(values_by_key["product:2ply"]["corr_sheet_width_mm"], 850.0)
        self.assertEqual(values_by_key["product:2ply"]["corr_flute"], "B")
        self.assertEqual(values_by_key["product:2ply"]["corr_material_signature"], "mat-2ply")
        self.assertNotIn("sfg:key", values_by_key["product:2ply"]["name"])
        self.assertNotIn("Do Not Use", values_by_key["product:board-roll"]["name"])

    def test_orm_writer_payload_plan_names_slit_sfg_by_output_sheet_size(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:2ply",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-2ply-slit",
                    "corr_plan_signature": "sig-2ply",
                    "corr_material_signature": "mat-2ply",
                    "corr_segment_key": "contract:route.stage.2ply",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                }
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:2ply",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:2ply",
                    "action": "create_bom",
                }
            ],
            "blockers": [],
        }
        layout = {
            "option": {
                "up_count": 3,
                "final_sheet_length_mm": 1180.0,
                "final_sheet_width_mm": 1650.0,
            }
        }
        materialization_plan = {
            "mode": "stockable",
            "metadata": {"selected_layout": layout},
            "boms": [
                {
                    "bom_key": "bom:2ply",
                    "product_candidate_key": "product:2ply",
                    "segment_key": "contract:route.stage.2ply",
                    "corr_plan_signature": "sig-2ply",
                    "metadata": {
                        "output_sheet_length_mm": 1180.0,
                        "output_sheet_width_mm": 550.0,
                        "input_deckle_width_mm": 1650.0,
                        "slitting_ups": 3,
                        "production_cycles": 100.0,
                    },
                    "lines": [
                        {
                            "line_key": "line:liner",
                            "role_code": "liner",
                            "metadata": {
                                "semantic_role": "liner",
                                "gsm": 180.0,
                                "bf": 18.0,
                                "flute_code": "B",
                                "selected_layout": layout,
                            },
                        },
                        {
                            "line_key": "line:medium",
                            "role_code": "medium",
                            "metadata": {
                                "semantic_role": "medium",
                                "gsm": 180.0,
                                "bf": 18.0,
                                "flute_code": "B",
                                "selected_layout": layout,
                            },
                        },
                    ],
                    "operations": [{"stage_code": "2ply", "operation_name": "2ply Corrugation"}],
                }
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
        )

        values = payload_plan["product_create_vals"][0]["values"]
        self.assertEqual(values["name"], "2-Ply 118x55 cm (B:180/180 GSM BF:18/18)")
        self.assertEqual(values["corr_sheet_length_mm"], 1180.0)
        self.assertEqual(values["corr_sheet_width_mm"], 550.0)

    def test_orm_writer_payload_plan_uses_unit_top_sheet_internal_ref(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:board-unit",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-board-unit",
                    "corr_plan_signature": "sig-board-unit",
                    "corr_segment_key": "contract:route.stage.board",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
                {
                    "product_candidate_key": "product:peeling-unit",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-peeling-unit",
                    "corr_plan_signature": "sig-peeling-unit",
                    "corr_segment_key": "contract:route.stage.peeling",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:board-unit",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:board-unit",
                    "action": "create_bom",
                },
                {
                    "bom_candidate_key": "bom:peeling-unit",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:peeling-unit",
                    "action": "create_bom",
                },
            ],
            "blockers": [],
        }
        layout = {"option": {"final_sheet_length_mm": 1000.0, "final_sheet_width_mm": 850.0}}
        top_line = {
            "line_key": "line:top",
            "role_code": "top_sheet_external",
            "metadata": {
                "semantic_role": "top_sheet_external",
                "default_code": "10-897-751-00517",
                "product_name": "Printed Top Sheet Display Name",
                "selected_layout": layout,
            },
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:board-unit",
                    "product_candidate_key": "product:board-unit",
                    "segment_key": "contract:route.stage.board",
                    "lines": [top_line],
                    "operations": [{"stage_code": "board", "operation_name": "Board Making"}],
                },
                {
                    "bom_key": "bom:peeling-unit",
                    "product_candidate_key": "product:peeling-unit",
                    "segment_key": "contract:route.stage.peeling",
                    "lines": [top_line],
                    "operations": [{"stage_code": "peeling", "operation_name": "Peeling"}],
                },
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
        )

        values_by_key = {
            item["candidate_key"]: item["values"]
            for item in payload_plan["product_create_vals"]
        }
        self.assertEqual(
            values_by_key["product:board-unit"]["name"],
            "Board 100x85 cm (TOP:10-897-751-00517)",
        )
        self.assertEqual(
            values_by_key["product:peeling-unit"]["name"],
            "Peeling (TOP:10-897-751-00517)",
        )
        self.assertNotIn("Printed Top Sheet Display Name", values_by_key["product:board-unit"]["name"])

    def test_orm_writer_payload_plan_inherits_top_sheet_ref_from_previous_output_sfg(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:board-unit",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-board-unit",
                    "corr_plan_signature": "sig-board-unit",
                    "corr_segment_key": "contract:route.stage.board",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
                {
                    "product_candidate_key": "product:die-unit",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-die-unit",
                    "corr_plan_signature": "sig-die-unit",
                    "corr_segment_key": "contract:route.stage.die_cutting",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:board-unit",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:board-unit",
                    "action": "create_bom",
                },
                {
                    "bom_candidate_key": "bom:die-unit",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:die-unit",
                    "action": "create_bom",
                },
            ],
            "blockers": [],
        }
        layout = {"option": {"final_sheet_length_mm": 1000.0, "final_sheet_width_mm": 850.0}}
        top_line = {
            "line_key": "line:top",
            "role_code": "top_sheet_external",
            "metadata": {
                "semantic_role": "top_sheet_external",
                "default_code": "10-897-751-00517",
                "selected_layout": layout,
            },
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:board-unit",
                    "product_candidate_key": "product:board-unit",
                    "segment_key": "contract:route.stage.board",
                    "lines": [top_line],
                    "operations": [{"stage_code": "board", "operation_name": "Board Making"}],
                },
                {
                    "bom_key": "bom:die-unit",
                    "product_candidate_key": "product:die-unit",
                    "segment_key": "contract:route.stage.die_cutting",
                    "lines": [
                        {
                            "line_key": "line:board",
                            "line_type": "previous_output",
                            "product_candidate_key": "product:board-unit",
                            "source_segment_key": "contract:route.stage.board",
                        }
                    ],
                    "operations": [{"stage_code": "die_cutting", "operation_name": "Slot/Die"}],
                },
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
        )

        values_by_key = {
            item["candidate_key"]: item["values"]
            for item in payload_plan["product_create_vals"]
        }
        self.assertEqual(values_by_key["product:board-unit"]["name"], "Board 100x85 cm (TOP:10-897-751-00517)")
        self.assertEqual(values_by_key["product:die-unit"]["name"], "Die Cutting (TOP:10-897-751-00517)")

    def test_orm_writer_payload_plan_uses_roll_top_sheet_ref_from_plan_metadata_without_rm_line(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:board-roll",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-board-roll",
                    "corr_plan_signature": "sig-board-roll",
                    "corr_segment_key": "contract:route.stage.board",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
                {
                    "product_candidate_key": "product:die-roll",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-die-roll",
                    "corr_plan_signature": "sig-die-roll",
                    "corr_segment_key": "contract:route.stage.die_cutting",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:board-roll",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:board-roll",
                    "action": "create_bom",
                },
                {
                    "bom_candidate_key": "bom:die-roll",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:die-roll",
                    "action": "create_bom",
                },
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "metadata": {
                "top_sheet_mode": "roll",
                "top_sheet_roll_code": "pro5123",
                "selected_layout": {"option": {"final_sheet_length_mm": 1000.0, "final_sheet_width_mm": 600.0}},
            },
            "boms": [
                {
                    "bom_key": "bom:board-roll",
                    "product_candidate_key": "product:board-roll",
                    "segment_key": "contract:route.stage.board",
                    "lines": [],
                    "operations": [{"stage_code": "board", "operation_name": "Board Making"}],
                },
                {
                    "bom_key": "bom:die-roll",
                    "product_candidate_key": "product:die-roll",
                    "segment_key": "contract:route.stage.die_cutting",
                    "lines": [
                        {
                            "line_key": "line:board",
                            "line_type": "previous_output",
                            "product_candidate_key": "product:board-roll",
                            "source_segment_key": "contract:route.stage.board",
                        }
                    ],
                    "operations": [{"stage_code": "die_cutting", "operation_name": "Slot/Die"}],
                },
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
        )

        values_by_key = {
            item["candidate_key"]: item["values"]
            for item in payload_plan["product_create_vals"]
        }
        self.assertEqual(values_by_key["product:board-roll"]["name"], "Board 100x60 cm (ROLL:pro5123)")
        self.assertEqual(values_by_key["product:die-roll"]["name"], "Die Cutting (ROLL:pro5123)")

    def test_orm_writer_payload_plan_uses_unit_top_sheet_display_ref_when_code_missing(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:board-unit",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-board-unit",
                    "corr_plan_signature": "sig-board-unit",
                    "corr_segment_key": "contract:route.stage.board",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:board-unit",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:board-unit",
                    "action": "create_bom",
                },
            ],
            "blockers": [],
        }
        top_line = {
            "line_key": "line:top",
            "role_code": "top_sheet_external",
            "metadata": {
                "semantic_role": "top_sheet_external",
                "product_name": "[TOP:10-897-751-00363] Top Sheets (10-897-751-00363)",
                "selected_layout": {"option": {"final_sheet_length_mm": 3020.0, "final_sheet_width_mm": 1120.0}},
                "writer_product_key": "product.product:104",
            },
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:board-unit",
                    "product_candidate_key": "product:board-unit",
                    "segment_key": "contract:route.stage.board",
                    "lines": [top_line],
                    "operations": [{"stage_code": "board", "operation_name": "Board Making"}],
                },
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
        )

        values = payload_plan["product_create_vals"][0]["values"]
        self.assertEqual(values["name"], "Board 302x112 cm (TOP:10-897-751-00363)")

    def test_orm_writer_payload_plan_uses_unit_top_sheet_orm_ref_when_display_is_generic(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:board-unit",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-board-unit",
                    "corr_plan_signature": "sig-board-unit",
                    "corr_segment_key": "contract:route.stage.board",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:board-unit",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:board-unit",
                    "action": "create_bom",
                },
            ],
            "blockers": [],
        }
        top_template = FakeRecord(id=2, display_name="Top Sheets")
        top_variant = FakeRecord(
            id=104,
            default_code="TOP:10-897-751-00363",
            display_name="[TOP:10-897-751-00363] Top Sheets",
            product_tmpl_id=top_template,
        )
        env = FakeWriterCreateEnv()
        env["product.product"] = FakeCreateModel("product.product", records=[top_variant])
        top_line = {
            "line_key": "line:top",
            "role_code": "top_sheet_external",
            "metadata": {
                "semantic_role": "top_sheet_external",
                "product_name": "Top Sheets",
                "selected_layout": {"option": {"final_sheet_length_mm": 3020.0, "final_sheet_width_mm": 1120.0}},
                "writer_product_key": "product.product:104",
            },
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:board-unit",
                    "product_candidate_key": "product:board-unit",
                    "segment_key": "contract:route.stage.board",
                    "lines": [top_line],
                    "operations": [{"stage_code": "board", "operation_name": "Board Making"}],
                },
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
            env=env,
        )

        values = payload_plan["product_create_vals"][0]["values"]
        self.assertEqual(values["name"], "Board 302x112 cm (TOP:10-897-751-00363)")

    def test_orm_writer_payload_plan_does_not_use_generic_top_sheet_display_as_ref(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:board-unit",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-board-unit",
                    "corr_plan_signature": "sig-board-unit",
                    "corr_segment_key": "contract:route.stage.board",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:board-unit",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:board-unit",
                    "action": "create_bom",
                },
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:board-unit",
                    "product_candidate_key": "product:board-unit",
                    "segment_key": "contract:route.stage.board",
                    "lines": [
                        {
                            "line_key": "line:top",
                            "role_code": "top_sheet_external",
                            "metadata": {
                                "semantic_role": "top_sheet_external",
                                "product_name": "Top Sheets",
                                "selected_layout": {
                                    "option": {
                                        "final_sheet_length_mm": 3020.0,
                                        "final_sheet_width_mm": 1120.0,
                                    }
                                },
                            },
                        }
                    ],
                    "operations": [{"stage_code": "board", "operation_name": "Board Making"}],
                },
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
        )

        values = payload_plan["product_create_vals"][0]["values"]
        self.assertEqual(values["name"], "Board 302x112 cm")
        self.assertFalse(values["corr_top_sheet_ref"])

    def test_orm_writer_payload_plan_normalizes_prefixed_top_sheet_default_code(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:board-unit",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:key-board-unit",
                    "corr_plan_signature": "sig-board-unit",
                    "corr_segment_key": "contract:route.stage.board",
                    "product_type_hint": "consu_storable",
                    "action": "create_product",
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:board-unit",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:board-unit",
                    "action": "create_bom",
                },
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:board-unit",
                    "product_candidate_key": "product:board-unit",
                    "segment_key": "contract:route.stage.board",
                    "lines": [
                        {
                            "line_key": "line:top",
                            "role_code": "top_sheet_external",
                            "metadata": {
                                "semantic_role": "top_sheet_external",
                                "default_code": "TOP:10-897-751-00363",
                                "selected_layout": {
                                    "option": {
                                        "final_sheet_length_mm": 3020.0,
                                        "final_sheet_width_mm": 1120.0,
                                    }
                                },
                            },
                        }
                    ],
                    "operations": [{"stage_code": "board", "operation_name": "Board Making"}],
                },
            ],
        }

        payload_plan = build_orm_writer_payload_plan(
            resolver_dry_run,
            materialization_plan=materialization_plan,
        )

        values = payload_plan["product_create_vals"][0]["values"]
        self.assertEqual(values["name"], "Board 302x112 cm (TOP:10-897-751-00363)")
        self.assertEqual(values["corr_top_sheet_ref"], "TOP:10-897-751-00363")

    def test_orm_writer_payload_plan_does_not_create_mo_or_archive_payloads(self):
        payload_plan = build_orm_writer_payload_plan(
            {
                "mode": "stockable",
                "products": [],
                "boms": [
                    {
                        "bom_candidate_key": "bom:stale",
                        "bom_role": "sfg",
                        "action": "archive_bom_candidate",
                        "stale_bom_match": {"id": 1},
                    }
                ],
                "blockers": [],
            },
            materialization_plan={"boms": []},
        )

        self.assertEqual(payload_plan["bom_create_vals"], [])
        self.assertEqual(payload_plan["bom_line_create_vals"], [])
        self.assertEqual(payload_plan["bom_operation_create_vals"], [])
        self.assertTrue(payload_plan["no_archive_delete"])
        self.assertTrue(payload_plan["no_mo_creation"])

    def test_executor_does_not_write_when_gates_disabled(self):
        env = FakeWriterCreateEnv()
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:sfg",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:test",
                    "action": "create_product",
                }
            ],
            "boms": [],
            "blockers": [],
        }

        with self.assertRaises(MaterializationWriterDisabledError):
            execute_materialization_writer(env, resolver_dry_run, materialization_plan={"mode": "stockable", "boms": []})

        self.assertEqual(env["product.template"].created, [])
        self.assertEqual(env["mrp.bom"].created, [])
        self.assertEqual(env["mrp.bom.line"].created, [])
        self.assertEqual(env["mrp.routing.workcenter"].created, [])

    def test_executor_does_not_write_when_only_one_gate_enabled(self):
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:sfg",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:test",
                    "action": "create_product",
                }
            ],
            "boms": [],
            "blockers": [],
        }
        gate_cases = [
            (
                {"corr_materialization_writer_enabled": True},
                {},
            ),
            (
                {},
                {"corr_mrp_bridge.materialization_writer_enabled": "1"},
            ),
        ]
        for context, params in gate_cases:
            env = FakeWriterCreateEnv(context=context, params=params)
            with self.assertRaises(MaterializationWriterDisabledError):
                execute_materialization_writer(
                    env,
                    resolver_dry_run,
                    materialization_plan={"mode": "stockable", "boms": []},
                )

            self.assertEqual(env["product.template"].created, [])
            self.assertEqual(env["mrp.bom"].created, [])
            self.assertEqual(env["mrp.bom.line"].created, [])
            self.assertEqual(env["mrp.routing.workcenter"].created, [])

    def test_executor_creates_controlled_records_only_when_both_gates_enabled(self):
        env = FakeWriterCreateEnv(
            context={"corr_materialization_writer_enabled": True},
            params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
        )
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:sfg",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:test",
                    "corr_plan_signature": "sig",
                    "corr_segment_key": "contract:route.stage.sfg",
                    "product_type_hint": "consu_storable",
                    "route_policy": "manufacture",
                    "uom": "sheet",
                    "action": "create_product",
                }
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:contract:route.stage.sfg",
                    "bom_role": "sfg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:sfg",
                    "action": "create_bom",
                }
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:contract:route.stage.sfg",
                    "segment_key": "contract:route.stage.sfg",
                    "corr_plan_signature": "sig",
                    "lines": [
                        {
                            "line_key": "line:paper",
                            "line_type": "rm_input",
                            "qty": 5.0,
                            "uom": "kg",
                            "role_code": "liner",
                            "operation_segment_key": "contract:route.stage.sfg",
                            "metadata": {"product_id": 2946},
                        }
                    ],
                    "operations": [
                        {
                            "operation_candidate_key": "op:sfg",
                            "operation_name": "SFG Operation",
                            "sequence": 10,
                            "capacity": 0.0,
                            "time_cycle_manual": 0.0,
                            "input_qty": 0.0,
                            "output_qty": 10.0,
                            "uom": "sheet",
                            "stage_contract_code": "route.stage.sfg",
                            "stage_template_id": 1,
                            "stage_code": "sfg",
                        }
                    ],
                }
            ],
        }

        result = execute_materialization_writer(
            env,
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=MaterializationWriterConfig(
                writes_enabled=True,
                creates_enabled=True,
                allow_multi_level_boms=True,
                context_flag_enabled=True,
                system_parameter_enabled=True,
                product_create_enabled=True,
                bom_create_enabled=True,
                writer_strategy="materialized_multi_level",
            ),
        )

        self.assertTrue(result["executed"])
        self.assertEqual(result["phase"], "7B-D7")
        self.assertTrue(result["real_orm_writes_enabled"])
        self.assertTrue(result["no_mo_creation"])
        self.assertTrue(result["no_archive_delete"])
        self.assertTrue(result["no_destructive_updates"])
        self.assertEqual(len(env["product.template"].created), 1)
        self.assertEqual(len(env["mrp.bom"].created), 1)
        self.assertEqual(len(env["mrp.bom.line"].created), 1)
        self.assertEqual(len(env["mrp.routing.workcenter"].created), 1)
        self.assertEqual(env["product.template"].created[0]["corr_sfg_key"], "sfg:test")
        self.assertEqual(env["product.template"].created[0]["product_role"], "sfg")
        self.assertEqual(env["product.template"].created[0]["type"], "consu")
        self.assertFalse(env["product.template"].created[0]["sale_ok"])
        self.assertFalse(env["product.template"].created[0]["purchase_ok"])
        self.assertTrue(env["product.template"].created[0]["is_storable"])
        self.assertEqual(env["product.template"].created[0]["route_ids"], [(6, 0, [77])])
        self.assertEqual(env["mrp.bom"].created[0]["product_tmpl_id"], 100)
        self.assertTrue(env["mrp.bom"].created[0]["corr_engine_v2_generated"])
        self.assertEqual(env["mrp.bom.line"].created[0]["bom_id"], 200)
        self.assertEqual(env["mrp.bom.line"].created[0]["product_id"], 2946)
        self.assertEqual(env["mrp.routing.workcenter"].created[0]["bom_id"], 200)
        self.assertEqual(env["mrp.routing.workcenter"].created[0]["capacity"], 1.0)
        self.assertEqual(env["mrp.routing.workcenter"].created[0]["time_mode"], "manual")
        self.assertEqual(env["mrp.routing.workcenter"].created[0]["time_cycle_manual"], 1.0)
        self.assertNotIn("mrp.production", env)

    def test_executor_reuses_existing_product_and_bom_idempotently(self):
        env = FakeWriterCreateEnv(
            context={"corr_materialization_writer_enabled": True},
            params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
        )
        existing_product = FakeRecord(
            id=700,
            corr_sfg_key="sfg:test",
            product_variant_id=FakeRecord(id=1700),
        )
        existing_bom = FakeRecord(
            id=800,
            product_tmpl_id=700,
            corr_sfg_key="sfg:test",
            corr_engine_v2_signature="sig",
        )
        env["product.template"].records.append(existing_product)
        env["mrp.bom"].records.append(existing_bom)
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:sfg",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:test",
                    "corr_plan_signature": "sig",
                    "corr_segment_key": "contract:route.stage.sfg",
                    "product_type_hint": "consu_storable",
                    "route_policy": "manufacture",
                    "uom": "sheet",
                    "action": "create_product",
                }
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:contract:route.stage.sfg",
                    "bom_role": "sfg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:sfg",
                    "action": "create_bom",
                }
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:contract:route.stage.sfg",
                    "segment_key": "contract:route.stage.sfg",
                    "corr_plan_signature": "sig",
                    "lines": [
                        {
                            "line_key": "line:paper",
                            "line_type": "rm_input",
                            "qty": 5.0,
                            "uom": "kg",
                            "metadata": {"product_id": 2946},
                        }
                    ],
                    "operations": [
                        {
                            "operation_candidate_key": "op:sfg",
                            "operation_name": "SFG Operation",
                            "sequence": 10,
                        }
                    ],
                }
            ],
        }

        result = execute_materialization_writer(
            env,
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=MaterializationWriterConfig(
                writes_enabled=True,
                creates_enabled=True,
                allow_multi_level_boms=True,
                context_flag_enabled=True,
                system_parameter_enabled=True,
                product_create_enabled=True,
                bom_create_enabled=True,
                writer_strategy="materialized_multi_level",
            ),
        )

        self.assertTrue(result["executed"])
        self.assertEqual(result["created_product_templates"], {})
        self.assertEqual(result["created_boms"], {})
        self.assertEqual(result["reused_products"]["product:sfg"]["id"], 700)
        self.assertEqual(result["reused_boms"]["bom:contract:route.stage.sfg"]["id"], 800)
        self.assertEqual(env["product.template"].created, [])
        self.assertEqual(env["mrp.bom"].created, [])
        self.assertEqual(env["mrp.bom.line"].created, [])
        self.assertEqual(env["mrp.routing.workcenter"].created, [])
        self.assertEqual(existing_product.product_role, "sfg")
        self.assertEqual(existing_product.type, "consu")
        self.assertFalse(existing_product.sale_ok)
        self.assertFalse(existing_product.purchase_ok)
        self.assertTrue(existing_product.is_storable)
        self.assertEqual(existing_product.route_ids, [(6, 0, [77])])
        self.assertEqual(len(result["skipped_payloads"]), 2)
        self.assertEqual(
            result["skipped_payloads"][0]["reason"],
            "parent_bom_reused_existing_no_destructive_update",
        )

    def test_executor_refreshes_reused_generated_bom_lines_for_batch_yield_plan(self):
        env = FakeWriterCreateEnv(
            context={"corr_materialization_writer_enabled": True},
            params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
        )
        existing_product = FakeRecord(
            id=700,
            corr_sfg_key="sfg:slot",
            product_variant_id=FakeRecord(id=1700),
        )
        existing_board_product = FakeRecord(
            id=701,
            corr_sfg_key="sfg:board",
            product_variant_id=FakeRecord(id=1701),
        )
        existing_bom = FakeRecord(
            id=800,
            product_tmpl_id=existing_product,
            corr_sfg_key="sfg:slot",
            corr_engine_v2_signature="sig-slot",
            product_qty=1.0,
        )
        existing_board_bom = FakeRecord(
            id=801,
            product_tmpl_id=existing_board_product,
            corr_sfg_key="sfg:board",
            corr_engine_v2_signature="old-board",
            product_qty=2.0,
        )
        env["product.template"].records.append(existing_product)
        env["product.template"].records.append(existing_board_product)
        env["mrp.bom"].records.append(existing_bom)
        env["mrp.bom"].records.append(existing_board_bom)
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:slot",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:slot",
                    "product_type_hint": "consu_storable",
                    "action": "reuse_product",
                    "existing_product_match": {"id": 700, "product_variant_id": 1700},
                },
                {
                    "product_candidate_key": "product:board",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:board",
                    "product_type_hint": "consu_storable",
                    "action": "reuse_product",
                    "existing_product_match": {"id": 701, "product_variant_id": 1701},
                }
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:slot",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:slot",
                    "action": "reuse_bom",
                    "existing_bom_match": {"id": 800},
                },
                {
                    "bom_candidate_key": "bom:board",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:board",
                    "action": "reuse_bom",
                    "existing_bom_match": {"id": 801},
                }
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:slot",
                    "segment_key": "contract:route.stage.diecut",
                    "product_candidate_key": "product:slot",
                    "product_qty": 2.0,
                    "lines": [
                        {
                            "line_key": "line:board",
                            "line_type": "previous_output",
                            "product_candidate_key": "product:board",
                            "source_segment_key": "contract:route.stage.board",
                            "qty": 1.0,
                            "uom": "sheet",
                            "metadata": {"product_id": 2946},
                        }
                    ],
                    "operations": [],
                },
                {
                    "bom_key": "bom:board",
                    "segment_key": "contract:route.stage.board",
                    "product_candidate_key": "product:board",
                    "product_qty": 1.0,
                    "lines": [
                        {
                            "line_key": "line:2ply",
                            "line_type": "previous_output",
                            "product_candidate_key": "product:2ply",
                            "source_segment_key": "contract:route.stage.2ply",
                            "qty": 1.0,
                            "uom": "sheet",
                            "metadata": {"product_id": 2947},
                        }
                    ],
                    "operations": [],
                }
            ],
        }

        result = execute_materialization_writer(
            env,
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=MaterializationWriterConfig(
                writes_enabled=True,
                creates_enabled=True,
                allow_multi_level_boms=True,
                context_flag_enabled=True,
                system_parameter_enabled=True,
                product_create_enabled=True,
                bom_create_enabled=True,
                writer_strategy="materialized_multi_level",
            ),
        )

        self.assertTrue(result["executed"])
        self.assertEqual(existing_bom.product_qty, 2.0)
        self.assertEqual(existing_board_bom.product_qty, 1.0)
        created_lines = {
            (line["bom_id"], line["product_qty"], line["product_id"])
            for line in env["mrp.bom.line"].created
        }
        self.assertIn((800, 1.0, 2946), created_lines)
        self.assertIn((801, 1.0, 2947), created_lines)
        self.assertEqual(result["skipped_payloads"], [])
        self.assertTrue(result["idempotency"]["existing_bom_generated_lines_are_refreshed"])

    def test_executor_syncs_existing_board_sfg_name_to_selected_top_sheet_ref(self):
        env = FakeWriterCreateEnv(
            context={"corr_materialization_writer_enabled": True},
            params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
        )
        existing_product = FakeRecord(
            id=700,
            name="Board 302x112 cm (Top Sheets)",
            corr_sfg_key="sfg:board",
            product_variant_id=FakeRecord(id=1700),
        )
        existing_bom = FakeRecord(
            id=800,
            product_tmpl_id=700,
            corr_sfg_key="sfg:board",
            corr_engine_v2_signature="sig-board",
        )
        top_template = FakeRecord(id=2, display_name="Top Sheets")
        top_variant = FakeRecord(
            id=104,
            default_code="TOP:10-897-751-00363",
            display_name="[TOP:10-897-751-00363] Top Sheets",
            product_tmpl_id=top_template,
        )
        env["product.template"].records.append(existing_product)
        env["product.product"] = FakeCreateModel("product.product", records=[top_variant])
        env["mrp.bom"].records.append(existing_bom)
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:board",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:board",
                    "corr_plan_signature": "sig-board",
                    "corr_material_signature": "mat-board",
                    "corr_segment_key": "contract:route.stage.board",
                    "product_type_hint": "consu_storable",
                    "route_policy": "manufacture",
                    "uom": "unit",
                    "action": "reuse_product",
                }
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:board",
                    "bom_role": "sfg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:board",
                    "action": "create_bom",
                }
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:board",
                    "product_candidate_key": "product:board",
                    "segment_key": "contract:route.stage.board",
                    "corr_plan_signature": "sig-board",
                    "lines": [
                        {
                            "line_key": "line:top",
                            "role_code": "top_sheet_external",
                            "qty": 1.0,
                            "uom": "unit",
                            "metadata": {
                                "semantic_role": "top_sheet_external",
                                "product_name": "Top Sheets",
                                "writer_product_key": "product.product:104",
                                "selected_layout": {
                                    "option": {
                                        "final_sheet_length_mm": 3020.0,
                                        "final_sheet_width_mm": 1120.0,
                                    }
                                },
                            },
                        }
                    ],
                    "operations": [{"stage_code": "board", "operation_name": "Board Making"}],
                }
            ],
        }

        result = execute_materialization_writer(
            env,
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=MaterializationWriterConfig(
                writes_enabled=True,
                creates_enabled=True,
                allow_multi_level_boms=True,
                context_flag_enabled=True,
                system_parameter_enabled=True,
                product_create_enabled=True,
                bom_create_enabled=True,
                writer_strategy="materialized_multi_level",
            ),
        )

        self.assertTrue(result["executed"])
        self.assertEqual(env["product.template"].created, [])
        self.assertEqual(existing_product.name, "Board 302x112 cm (TOP:10-897-751-00363)")
        self.assertEqual(existing_product.corr_sfg_key, "sfg:board")
        self.assertEqual(existing_product.corr_sfg_type, "board")
        self.assertEqual(existing_product.corr_stage_code, "board")
        self.assertEqual(existing_product.corr_sheet_length_mm, 3020.0)
        self.assertEqual(existing_product.corr_sheet_width_mm, 1120.0)
        self.assertEqual(existing_product.corr_top_sheet_ref, "TOP:10-897-751-00363")
        self.assertEqual(existing_product.corr_material_signature, "mat-board")
        self.assertEqual(existing_product.corr_engine_v2_signature, "sig-board")

    def test_executor_resolves_reused_template_variant_for_previous_output_line(self):
        env = FakeWriterCreateEnv(
            context={"corr_materialization_writer_enabled": True},
            params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
        )
        board_template = FakeRecord(
            id=1215,
            corr_sfg_key="sfg:board",
            product_variant_id=FakeRecord(id=2961),
        )
        board_variant = FakeRecord(id=2961, product_tmpl_id=board_template)
        env["product.template"].records.append(board_template)
        env["product.product"] = FakeCreateModel(
            "product.product",
            id_start=9000,
            records=[board_variant],
        )
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:board",
                    "product_role": "sfg",
                    "corr_sfg_key": "sfg:board",
                    "existing_product_match": {"id": 1215},
                    "action": "reuse_product",
                },
                {
                    "product_candidate_key": "product:fg",
                    "product_role": "fg",
                    "existing_product_match": {"id": 1177},
                    "action": "reuse_product",
                },
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:fg",
                    "bom_role": "fg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:fg",
                    "action": "create_bom",
                }
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:fg",
                    "segment_key": "contract:fg",
                    "corr_plan_signature": "fg-sig",
                    "lines": [
                        {
                            "line_key": "line:board",
                            "line_type": "previous_output",
                            "product_candidate_key": "product:board",
                            "qty": 1.0,
                            "uom": "unit",
                        }
                    ],
                    "operations": [],
                }
            ],
        }

        result = execute_materialization_writer(
            env,
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=MaterializationWriterConfig(
                writes_enabled=True,
                creates_enabled=True,
                allow_multi_level_boms=True,
                context_flag_enabled=True,
                system_parameter_enabled=True,
                product_create_enabled=True,
                bom_create_enabled=True,
                writer_strategy="materialized_multi_level",
            ),
        )

        self.assertTrue(result["executed"])
        self.assertEqual(env["mrp.bom.line"].created[0]["product_id"], 2961)
        self.assertNotEqual(env["mrp.bom.line"].created[0]["product_id"], 1215)

    def test_executor_uses_context_fg_template_for_fg_bom(self):
        env = FakeWriterCreateEnv(
            context={
                "corr_materialization_writer_enabled": True,
                "corr_materialization_fg_product_tmpl_id": 900,
            },
            params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
        )
        resolver_dry_run = {
            "mode": "stockable",
            "products": [
                {
                    "product_candidate_key": "product:fg",
                    "product_role": "fg",
                    "corr_sfg_key": None,
                    "existing_product_match": None,
                    "action": "reuse_product",
                }
            ],
            "boms": [
                {
                    "bom_candidate_key": "bom:fg",
                    "bom_role": "fg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:fg",
                    "action": "create_bom",
                }
            ],
            "blockers": [],
        }
        materialization_plan = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:fg",
                    "segment_key": "contract:route.stage.fg",
                    "corr_plan_signature": "fg-sig",
                    "lines": [],
                    "operations": [],
                }
            ],
        }

        result = execute_materialization_writer(
            env,
            resolver_dry_run,
            materialization_plan=materialization_plan,
            config=MaterializationWriterConfig(
                writes_enabled=True,
                creates_enabled=True,
                allow_multi_level_boms=True,
                context_flag_enabled=True,
                system_parameter_enabled=True,
                product_create_enabled=True,
                bom_create_enabled=True,
                writer_strategy="materialized_multi_level",
            ),
        )

        self.assertTrue(result["executed"])
        self.assertEqual(env["mrp.bom"].created[0]["product_tmpl_id"], 900)

    def test_executor_blocks_bom_create_without_product_template(self):
        env = FakeWriterCreateEnv(
            context={"corr_materialization_writer_enabled": True},
            params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
        )
        resolver_dry_run = {
            "mode": "stockable",
            "products": [],
            "boms": [
                {
                    "bom_candidate_key": "bom:missing-product",
                    "bom_role": "fg",
                    "bom_type": "normal",
                    "product_candidate_key": "product:missing",
                    "action": "create_bom",
                }
            ],
            "blockers": [],
        }

        with self.assertRaises(MaterializationWriterDisabledError) as raised:
            execute_materialization_writer(
                env,
                resolver_dry_run,
                materialization_plan={"mode": "stockable", "boms": [{"bom_key": "bom:missing-product"}]},
                config=MaterializationWriterConfig(
                    writes_enabled=True,
                    creates_enabled=True,
                    allow_multi_level_boms=True,
                    context_flag_enabled=True,
                    system_parameter_enabled=True,
                    product_create_enabled=True,
                    bom_create_enabled=True,
                    writer_strategy="materialized_multi_level",
                ),
            )

        self.assertIn("missing_product_tmpl_id_for_bom", str(raised.exception))
        self.assertEqual(env["mrp.bom"].created, [])

    def test_executor_preserves_legacy_single_bom_fallback(self):
        env = FakeWriterCreateEnv(
            context={"corr_materialization_writer_enabled": True},
            params={"corr_mrp_bridge.materialization_writer_enabled": "1"},
        )
        result = execute_materialization_writer(
            env,
            {
                "mode": "single_bom",
                "products": [],
                "boms": [
                    {
                        "bom_candidate_key": "bom:single",
                        "bom_role": "single_bom",
                        "action": "create_bom",
                    }
                ],
                "blockers": [],
            },
            materialization_plan={"mode": "single_bom", "boms": []},
        )

        self.assertFalse(result["executed"])
        self.assertEqual(result["phase"], "7B-D7")
        self.assertEqual(result["reason"], "legacy_single_bom_fallback_preserved")
        self.assertEqual(env["mrp.bom"].created, [])

    def test_guarded_executor_diagnostics_exposes_d7_phase_without_execution(self):
        diagnostics = build_guarded_executor_diagnostics(
            {
                "mode": "stockable",
                "products": [],
                "boms": [],
                "blockers": [],
            },
            materialization_plan={"mode": "stockable", "boms": []},
        )

        self.assertEqual(diagnostics["phase"], "7B-D7")
        self.assertEqual(diagnostics["payload_plan_phase"], "7B-D5")
        self.assertTrue(diagnostics["executor_scaffold_available"])
        self.assertTrue(diagnostics["controlled_execution_available"])
        self.assertFalse(diagnostics["execution_attempted"])
        self.assertFalse(diagnostics["real_orm_writes_enabled"])
        self.assertTrue(diagnostics["no_mo_creation"])
        self.assertTrue(diagnostics["no_archive_delete"])
        self.assertTrue(diagnostics["no_destructive_updates"])
        self.assertTrue(diagnostics["execution_scope"]["create_or_reuse_sfg_products"])
        self.assertEqual(diagnostics["idempotency"]["product_lookup_key"], "corr_sfg_key")

    def test_materialization_writer_strategy_separates_multi_level_from_legacy_flattened_writer(self):
        materialized = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_key": "bom:contract:route.stage.2ply",
                    "bom_role": "sfg",
                    "lines": [{"line_type": "rm_input"}, {"line_type": "rm_input"}],
                    "operations": [{"segment_key": "contract:route.stage.2ply"}],
                },
                {
                    "bom_key": "bom:contract:route.stage.board",
                    "bom_role": "sfg",
                    "lines": [{"line_type": "previous_output"}, {"line_type": "rm_input"}],
                    "operations": [{"segment_key": "contract:route.stage.board"}],
                },
                {
                    "bom_key": "bom:contract:route.stage.seaming",
                    "bom_role": "fg",
                    "lines": [{"line_type": "previous_output"}],
                    "operations": [{"segment_key": "contract:route.stage.seaming"}],
                },
            ],
        }
        resolver_dry_run = {
            "mode": "stockable",
            "boms": [
                {
                    "bom_candidate_key": "bom:contract:route.stage.2ply",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:2ply",
                    "action": "create_bom",
                },
                {
                    "bom_candidate_key": "bom:contract:route.stage.board",
                    "bom_role": "sfg",
                    "product_candidate_key": "product:board",
                    "action": "create_bom",
                },
                {
                    "bom_candidate_key": "bom:contract:route.stage.seaming",
                    "bom_role": "fg",
                    "product_candidate_key": "product:fg",
                    "action": "create_bom",
                },
            ],
            "products": [],
            "blockers": [],
        }

        strategy = build_materialization_writer_strategy(
            resolver_dry_run,
            materialization_plan=materialized,
        )

        self.assertEqual(strategy["strategy"], "materialized_multi_level")
        self.assertEqual(strategy["writer_family"], "materialized_multi_level")
        self.assertEqual(strategy["operation_scope"], "local_bom_operations")
        self.assertFalse(strategy["real_orm_writes_enabled"])
        self.assertEqual(
            {item["operation_scope"] for item in strategy["boundaries"]},
            {"local_bom_operations"},
        )
        self.assertEqual(
            [(item["bom_role"], item["writer_family"], item["creates_stock_boundary"]) for item in strategy["boundaries"]],
            [
                ("sfg", "materialized_bom_writer", True),
                ("sfg", "materialized_bom_writer", True),
                ("fg", "materialized_bom_writer", True),
            ],
        )

    def test_materialization_writer_strategy_preserves_single_bom_fallback(self):
        resolver_dry_run = {
            "mode": "single_bom",
            "boms": [
                {
                    "bom_candidate_key": "bom:single",
                    "bom_role": "single_bom",
                    "product_candidate_key": "product:fg",
                    "action": "create_bom",
                }
            ],
            "products": [],
            "blockers": [],
        }

        strategy = build_materialization_writer_strategy(resolver_dry_run)

        self.assertEqual(strategy["strategy"], "legacy_single_bom_flattened")
        self.assertEqual(strategy["writer_family"], "legacy_single_bom_flattened")
        self.assertEqual(strategy["operation_scope"], "flattened_fg_bom_operations")
        self.assertTrue(strategy["legacy_single_bom_fallback_preserved"])
        self.assertEqual(strategy["boundaries"][0]["writer_family"], "legacy_flattened_bom_writer")
        self.assertEqual(strategy["boundaries"][0]["operation_scope"], "flattened_fg_bom_operations")

    def test_collapse_bom_line_payloads_merges_identical_previous_output_components(self):
        payloads = [
            {
                "candidate_key": "line:a",
                "parent_bom_candidate_key": "bom:board",
                "model": "mrp.bom.line",
                "values": {
                    "bom_id": 10,
                    "line_type": "previous_output",
                    "product_id": 501,
                    "product_qty": 1.0,
                    "product_uom_id": 1,
                    "product_uom_name": "Units",
                    "operation_segment_key": "contract:route.stage.board",
                    "source_segment_key": "contract:route.stage.2ply:a",
                },
            },
            {
                "candidate_key": "line:b",
                "parent_bom_candidate_key": "bom:board",
                "model": "mrp.bom.line",
                "values": {
                    "bom_id": 10,
                    "line_type": "previous_output",
                    "product_id": 501,
                    "product_qty": 1.0,
                    "product_uom_id": 1,
                    "product_uom_name": "Units",
                    "operation_segment_key": "contract:route.stage.board",
                    "source_segment_key": "contract:route.stage.2ply:b",
                },
            },
        ]

        collapsed = _collapse_bom_line_payloads(payloads)

        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["values"]["product_qty"], 2.0)
        self.assertEqual(
            collapsed[0]["values"]["merged_source_segment_keys"],
            ["contract:route.stage.2ply:a", "contract:route.stage.2ply:b"],
        )

    def test_collapse_bom_line_payloads_keeps_rm_inputs_separate(self):
        payloads = [
            {
                "candidate_key": "line:rm:a",
                "parent_bom_candidate_key": "bom:board",
                "model": "mrp.bom.line",
                "values": {
                    "bom_id": 10,
                    "line_type": "rm_input",
                    "product_id": 601,
                    "product_qty": 1.0,
                    "product_uom_id": 1,
                    "operation_segment_key": "contract:route.stage.board",
                    "source_segment_key": "rm:a",
                },
            },
            {
                "candidate_key": "line:rm:b",
                "parent_bom_candidate_key": "bom:board",
                "model": "mrp.bom.line",
                "values": {
                    "bom_id": 10,
                    "line_type": "rm_input",
                    "product_id": 601,
                    "product_qty": 1.0,
                    "product_uom_id": 1,
                    "operation_segment_key": "contract:route.stage.board",
                    "source_segment_key": "rm:b",
                },
            },
        ]

        collapsed = _collapse_bom_line_payloads(payloads)

        self.assertEqual(len(collapsed), 2)

    def test_reused_generated_content_refresh_stays_enabled_for_fg_per_sheet_chain(self):
        materialized_boms = [
            {
                "bom_key": "bom:board",
                "product_qty": 1.0,
                "metadata": {},
                "operations": [
                    {
                        "metadata": {
                            "yield_basis": "fg_per_sheet",
                            "fg_per_sheet": 2.0,
                        }
                    }
                ],
                "lines": [
                    {
                        "line_type": "previous_output",
                        "metadata": {
                            "fg_per_sheet": 2.0,
                        },
                    }
                ],
            }
        ]

        self.assertTrue(_plan_requires_reused_generated_content_refresh(materialized_boms))


if __name__ == "__main__":
    unittest.main()




