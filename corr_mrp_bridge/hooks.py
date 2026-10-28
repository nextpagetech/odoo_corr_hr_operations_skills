import logging

from odoo import SUPERUSER_ID, api, models

_logger = logging.getLogger(__name__)


def _ensure_env(first_arg, registry=None):
    """Allow post_init_hook to work whether Odoo passes env (v17+) or cr/registry (older)."""
    if isinstance(first_arg, (api.Environment, models.BaseModel)):
        return first_arg
    return api.Environment(first_arg, SUPERUSER_ID, {})


def post_init_hook(env, registry=None):
    """Post-install / upgrade adjustments.

    - Tag existing corrugation SFG templates with product_role='sfg'.
    - Kick off migration of mrp.workorder Operation text into corr.operation.type.
    """
    env = _ensure_env(env, registry)

    # Existing behaviour: tag SFG templates
    ProductTemplate = env["product.template"].with_context(active_test=False)
    if "corr_sfg_key" in ProductTemplate._fields and "product_role" in ProductTemplate._fields:
        domain = [
            ("corr_sfg_key", "!=", False),
            ("product_role", "in", [False, "", "other"]),
        ]
        sfg_templates = ProductTemplate.search(domain)
        if sfg_templates:
            _logger.info(
                "corr_mrp_bridge post-init: tagging %s staged SFG templates with product_role='sfg'.",
                len(sfg_templates),
            )
            sfg_templates.write({"product_role": "sfg"})
        else:
            _logger.info(
                "corr_mrp_bridge post-init: no staged SFG templates detected for tagging."
            )

    # New behaviour: migrate existing work orders to use corr.operation.type.
    # Delegated to a model method that is also called at registry load.
    try:
        env["corr.operation.type"].migrate_workorder_operation_types()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning(
            "corr_mrp_bridge post-init: failed to migrate work order operation types: %s",
            exc,
        )

    # Engine V2 generic stage-execution foundation (Phase 1):
    # backfill stage capabilities, input roles, quantity profiles, and
    # stage template contract fields. This must be best-effort and must
    # not break existing behaviour if anything fails.
    try:
        _backfill_engine_stage_contract(env)
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning(
            "corr_mrp_bridge post-init: failed to backfill Engine V2 stage contract: %s",
            exc,
        )

    try:
        if "corr_engine_v2_signature" in env["mrp.bom"]._fields:
            env["mrp.bom"]._corr_initialize_current_engine_v2_boms()
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning(
            "corr_mrp_bridge post-init: failed to initialize Engine V2 current BoM pointers: %s",
            exc,
        )


def _backfill_engine_stage_contract(env):
    """Best-effort seeding of stage contract master data and template defaults.

    This function:
    - ensures a minimal set of stage capabilities exists,
    - ensures a minimal set of input roles and quantity profiles exists,
    - assigns safe default contract values on existing stage templates.

    It must not change current Engine V2 runtime behaviour.
    """

    StageCapability = env["corr.engine.stage.capability"].sudo()
    InputRole = env["corr.engine.input.role"].sudo()
    QuantityProfile = env["corr.engine.quantity.profile"].sudo()
    StageTemplate = env["corr.engine.stage.template"].sudo()

    # 1) Seed stage capabilities aligned with existing stage_type values.
    capability_defs = [
        ("corrugation", "Corrugation", "material"),
        ("printing", "Printing", "operation"),
        ("lamination", "Lamination", "operation"),
        ("diecut", "Die-cut", "operation"),
        ("conversion", "Conversion / Seaming", "material"),
        ("fg", "Finished Goods", "material"),
        ("auxiliary", "Auxiliary", "operation"),
    ]

    existing_caps = {
        c.code: c
        for c in StageCapability.search([])
    }
    for code, name, exec_type in capability_defs:
        if code in existing_caps:
            # Ensure execution type is set for existing capabilities.
            cap = existing_caps[code]
            if not cap.execution_type:
                cap.execution_type = exec_type
            continue
        StageCapability.create(
            {
                "name": name,
                "code": code,
                "execution_type": exec_type,
                "is_system_default": True,
                "seed_key": f"stage_capability_{code}",
                "seed_version": "v1",
            }
        )

    capability_by_code = {
        c.code: c
        for c in StageCapability.search([])
    }

    # 2) Seed input roles for future stage-local RM/consumable modelling.
    input_role_defs = [
        # Raw materials
        ("RM_PAPER_LINER", "Paper Liner", "raw", "sheet"),
        ("RM_PAPER_MEDIUM", "Paper Medium", "raw", "sheet"),
        ("RM_PAPER_TOP", "Top Paper", "raw", "sheet"),
        ("RM_PAPER_GENERIC", "Generic Paper", "raw", "sheet"),
        ("RM_COMPONENT_GENERIC", "Generic Component", "raw", "piece"),
        # Consumables
        ("CONSUMABLE_ADHESIVE_CORRUGATION", "Adhesive (Corrugation)", "consumable", "liquid"),
        ("CONSUMABLE_ADHESIVE_BOARD", "Adhesive (Board)", "consumable", "liquid"),
        ("CONSUMABLE_ADHESIVE_FG", "Adhesive (FG)", "consumable", "liquid"),
        ("CONSUMABLE_INK", "Ink", "consumable", "liquid"),
        ("CONSUMABLE_VARNISH", "Varnish / Coating", "consumable", "liquid"),
        # Intermediate outputs
        ("INT_SHEET", "Intermediate Sheet", "intermediate", "sheet"),
        ("INT_BOARD", "Intermediate Board", "intermediate", "sheet"),
        ("INT_2PLY_BOARD", "2-ply Board", "intermediate", "sheet"),
        ("INT_BOX_SEAMED", "Seamed Box", "intermediate", "piece"),
        # Generic components
        ("COMP_GENERIC", "Generic Component", "generic_component", "piece"),
    ]

    existing_roles = {
        r.code: r
        for r in InputRole.search([])
    }
    for code, name, category, shape in input_role_defs:
        if code in existing_roles:
            continue
        InputRole.create(
            {
                "name": name,
                "code": code,
                "category": category,
                "shape": shape,
                "is_system_default": True,
                "seed_key": f"input_role_{code}",
                "seed_version": "v1",
            }
        )

    # 3) Seed quantity profiles for future quantity planner.
    qty_profile_defs = [
        ("per_fg_unit", "Per Finished Unit"),
        ("sheet_based_board", "Board from Sheets"),
        ("roll_to_sheet_cutting", "Cutting Roll to Sheets"),
        ("area_based_printing", "Printing by Area"),
    ]

    existing_qp = {
        q.code: q
        for q in QuantityProfile.search([])
    }
    for code, name in qty_profile_defs:
        if code in existing_qp:
            continue
        QuantityProfile.create(
            {
                "name": name,
                "code": code,
                "is_system_default": True,
                "seed_key": f"quantity_profile_{code}",
                "seed_version": "v1",
            }
        )

    quantity_profile_by_code = {
        q.code: q
        for q in QuantityProfile.search([])
    }

    # 4) Backfill contract fields on existing stage templates with safe defaults.
    templates = StageTemplate.search([])
    if not templates:
        return

    for tmpl in templates:
        vals = {}

        # Execution type: material for corrugation, conversion, fg; operation otherwise.
        stage_type = tmpl.stage_type or "auxiliary"
        if tmpl.execution_type in (False, "", None):
            if stage_type in ("corrugation", "conversion", "fg"):
                vals["execution_type"] = "material"
            else:
                vals["execution_type"] = "operation"

        # Capability: map from stage_type when not set.
        if not tmpl.capability_id:
            cap = capability_by_code.get(stage_type)
            if cap:
                vals["capability_id"] = cap.id

        # Output kind and stock behaviour: best-effort based on stage_type flags.
        if not tmpl.output_kind:
            if stage_type == "fg":
                vals["output_kind"] = "fg"
            elif stage_type in ("conversion",):
                vals["output_kind"] = "box"
            elif stage_type == "corrugation":
                vals["output_kind"] = "board"
            elif stage_type in ("printing", "lamination", "diecut"):
                vals["output_kind"] = "sheet"
            else:
                vals["output_kind"] = "generic"

        if not tmpl.output_stock_behavior:
            if stage_type == "fg" or tmpl.is_fg_stage:
                vals["output_stock_behavior"] = "fg"
            elif stage_type in ("corrugation", "conversion"):
                vals["output_stock_behavior"] = "semi_finished"
            else:
                vals["output_stock_behavior"] = "non_stock"

        # Terminal flag: honour existing is_fg_stage when present.
        if tmpl.can_be_terminal in (False, None):
            vals["can_be_terminal"] = bool(tmpl.is_fg_stage or stage_type == "fg")

        # BoM mapping policy: default own_operation.
        if not tmpl.bom_mapping_policy:
            vals["bom_mapping_policy"] = "own_operation"

        # Quantity profile: map from stage_type where sensible.
        if not tmpl.quantity_profile_id:
            qp_code = None
            if stage_type == "corrugation":
                qp_code = "sheet_based_board"
            elif stage_type in ("printing", "lamination"):
                qp_code = "area_based_printing"
            elif stage_type in ("conversion", "fg", "diecut"):
                qp_code = "per_fg_unit"
            else:
                qp_code = "per_fg_unit"
            qp = quantity_profile_by_code.get(qp_code)
            if qp:
                vals["quantity_profile_id"] = qp.id

        # Previous output flags: conservative defaults for now. Treat non-entry,
        # non-corrugation stages as consuming previous output.
        if not tmpl.consumes_previous_output and stage_type not in ("corrugation",):
            if not tmpl.is_entry_stage:
                vals.setdefault("consumes_previous_output", True)
        if (
            not tmpl.previous_output_required
            and (vals.get("consumes_previous_output") or tmpl.consumes_previous_output)
        ):
            vals.setdefault("previous_output_required", True)

        if vals:
            tmpl.write(vals)
