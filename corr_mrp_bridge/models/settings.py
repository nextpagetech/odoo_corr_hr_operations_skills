from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Backward-compat: DB column exists but core model no longer defines it.
    # Define a minimal field so any legacy views referencing it keep working.
    use_excluded_locations_for_qc = fields.Boolean(
        string='Use Excluded Locations for QC',
    )

    autobom_enabled = fields.Boolean(
        string='Auto-generate BoM on product create/import',
        config_parameter='corr_mrp_bridge.autobom_enabled',
        default=True,
    )
    # Feature flag: enable sheet-level staged SFGs for BMC styles (driven by printing_ups)
    sheet_staging_bmc = fields.Boolean(
        string='BMC: Sheet-level Staging (UPS-based)',
        config_parameter='corr_mrp_bridge.sheet_staging_bmc',
        default=False,
        help="When enabled, BMC specs generate staged SFGs as sheet-level items and set FG BoM base quantity to Printing UPS."
    )
    autobom_scope = fields.Selection(
        [('template', 'Template-level BoM'), ('variant', 'Variant-level BoM')],
        string='Auto BoM Scope',
        config_parameter='corr_mrp_bridge.autobom_scope',
        default='template',
    )
    corr_active_engine = fields.Selection(
        [
            ('old', 'Old Formula Engine'),
            ('v2', 'Corr Engine v2'),
        ],
        string='Corrugation MRP Engine',
        config_parameter='corr_mrp_bridge.active_engine',
        default='old',
        help='Select which corrugation engine the MRP bridge should use for engine-specific flows.',
    )
    bulk_upload_use_engine_v2 = fields.Boolean(
        string='Bulk Upload Uses Engine V2',
        config_parameter='corr_mrp_bridge.bulk_upload_use_engine_v2',
        default=False,
        help='Migration flag for product import/create/write auto spec and BoM generation. '
             'When enabled, bulk upload uses the native Engine V2 path. '
             'When disabled, the legacy corr.spec.template path remains active.',
    )
    bulk_upload_auto_create_missing_materials = fields.Boolean(
        string='Bulk Upload: Auto-create Missing Material Variants',
        config_parameter='corr_mrp_bridge.bulk_upload_auto_create_missing_materials',
        default=False,
        help='When enabled, Engine V2 bulk upload creates deterministic missing paper variants '
             'from material template, GSM, BF, color, and deckle, then retries BoM generation once.',
    )
    bulk_upload_auto_create_missing_top_sheets = fields.Boolean(
        string='Bulk Upload: Auto-create Missing Top Sheets',
        config_parameter='corr_mrp_bridge.bulk_upload_auto_create_missing_top_sheets',
        default=False,
        help='Reserved for safe top-sheet product creation when upload rows provide complete product master data. '
             'Incomplete top sheet references still fail with an actionable bulk upload result.',
    )
    corr_include_ops_default = fields.Boolean(
        string='Default: Include Operations',
        config_parameter='corr_mrp_bridge.default_include_ops',
        default=True,
    )
    corr_include_sfg_default = fields.Boolean(
        string='Default: Use SFG (phantom)',
        config_parameter='corr_mrp_bridge.default_include_sfg',
        default=True,
    )
    corr_include_scrap_default = fields.Boolean(
        string='Default: Include Scrap By-product',
        config_parameter='corr_mrp_bridge.default_include_scrap',
        default=True,
    )
    corr_overhead_per_1000_default = fields.Float(
        string='Default: Overhead per 1,000 units',
        config_parameter='corr_mrp_bridge.default_overhead_per_1000',
        default=0.0,
    )
    sfg_mode = fields.Selection(
        [
            ('none', 'No SFG (flat FG BoM)'),
            ('flat_single_wo', 'Flat FG (single work order)'),
            ('phantom', 'SFG as Phantom (explode in FG)'),
            ('staged', 'Staged Stockable SFGs (2/4/5/Die-cut)'),
        ],
        string='SFG Mode',
        config_parameter='corr_mrp_bridge.sfg_mode',
        default='staged',
    )
    corr_enable_peeling_sfg = fields.Boolean(
        string='Use Peeling SFG stage',
        config_parameter='corr_mrp_bridge.enable_peeling_sfg',
        default=False,
        help='When enabled, staged corrugation BoMs insert a Peeling SFG between Die-cut and FG; FG operations start at Seaming.',
    )
    enable_mto_for_sfg = fields.Boolean(
        string='Enable MTO for SFG Products',
        config_parameter='corr_mrp_bridge.enable_mto_for_sfg',
        default=False,
    )
    show_all_chained_mos_in_so_smart_button = fields.Boolean(
        string='Show All Chained MOs In SO : manufacturing Smart Button',
        config_parameter='corr_mrp_bridge.show_all_chained_mos_in_so_smart_button',
        default=False,
        help='When enabled, the Sales Order Manufacturing smart button includes all recursive child MOs for corr staged flows.',
    )
    keep_so_created_corr_mos_draft = fields.Boolean(
        string='Keep SO-Created Corr MOs In Draft Until Manual Confirmation',
        config_parameter='corr_mrp_bridge.keep_so_created_corr_mos_draft',
        default=False,
        help='When enabled, sales-order-created corrugation MOs stay in Draft on SO confirmation. '
             'They follow the manual MO flow: child MOs are launched when the user confirms the parent MO.',
    )
    engine_v2_current_bom_selection_enabled = fields.Boolean(
        string='Engine V2: Use Current BoM For New MOs',
        config_parameter='corr_mrp_bridge.engine_v2_current_bom_selection_enabled',
        default=False,
        help='When enabled, newly created MOs prefer the product current Engine V2 BoM. '
             'Existing MOs keep their original BoM.',
    )
    corr_assign_workcenter = fields.Boolean(
        string='Assign Workcenter from Operation Map',
        config_parameter='corr_mrp_bridge.assign_workcenter',
        default=True,
        help='When enabled, BoM generation picks workcenters using corr.operation.map. '
             'When disabled, operations are created on the placeholder workcenter (corr_is_placeholder) if available, '
             'otherwise without a specific workcenter.'
    )
    corr_engine_v2_max_slitting_ups = fields.Integer(
        string='Engine V2 Max Slitting UPS',
        config_parameter='corr_engine_v2.max_slitting_ups',
        default=0,
        help='Global fallback cap for slitting option UPS generation when workcenter-level limits are not set.',
    )
    corr_engine_v2_auto_assign_operation_workcenter = fields.Boolean(
        string='Engine V2: Auto-assign Operation Workcenter',
        config_parameter='corr_engine_v2.auto_assign_operation_workcenter',
        default=True,
        help='When enabled, Engine V2 BoM operations are assigned only to compatible resolved workcenters. '
             'When disabled, Engine V2 leaves operation workcenter assignment empty.',
    )
    corr_engine_v2_top_sheet_category_ids = fields.Many2many(
        'product.category',
        string='Engine V2 Top Sheet Categories',
        help='Raw Material products in these categories are shown in Engine V2 Top Sheet Product dropdowns. '
             'Leave empty to show all Raw Material products.',
    )
    corr_engine_v2_allowed_paper_material_template_ids = fields.Many2many(
        'product.template',
        string='Allowed Paper Material Templates',
        help='Product templates that can be selected as board-layer material sources in Engine V2 specs.',
    )
    corr_engine_v2_attr_gsm = fields.Char(
        string='Engine V2 GSM Attribute',
        config_parameter='corr_engine_v2.attr_gsm',
        default='GSM',
    )
    corr_engine_v2_attr_bf = fields.Char(
        string='Engine V2 BF Attribute',
        config_parameter='corr_engine_v2.attr_bf',
        default='BF',
    )
    corr_engine_v2_attr_color = fields.Char(
        string='Engine V2 Color Attribute',
        config_parameter='corr_engine_v2.attr_color',
        default='Color',
    )
    corr_engine_v2_attr_deckle = fields.Char(
        string='Engine V2 Deckle Attribute',
        config_parameter='corr_engine_v2.attr_deckle',
        default='Deckle',
    )

    def set_values(self):
        res = super().set_values()
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('corr_mrp_bridge.autobom_enabled', '1' if self.autobom_enabled else '0')
        icp.set_param('corr_mrp_bridge.autobom_scope', self.autobom_scope or 'template')
        icp.set_param('corr_mrp_bridge.active_engine', self.corr_active_engine or 'old')
        icp.set_param(
            'corr_mrp_bridge.bulk_upload_use_engine_v2',
            '1' if self.bulk_upload_use_engine_v2 else '0',
        )
        icp.set_param(
            'corr_mrp_bridge.bulk_upload_auto_create_missing_materials',
            '1' if self.bulk_upload_auto_create_missing_materials else '0',
        )
        icp.set_param(
            'corr_mrp_bridge.bulk_upload_auto_create_missing_top_sheets',
            '1' if self.bulk_upload_auto_create_missing_top_sheets else '0',
        )
        icp.set_param('corr_mrp_bridge.default_include_ops', '1' if self.corr_include_ops_default else '0')
        icp.set_param('corr_mrp_bridge.default_include_sfg', '1' if self.corr_include_sfg_default else '0')
        icp.set_param('corr_mrp_bridge.default_include_scrap', '1' if self.corr_include_scrap_default else '0')
        icp.set_param('corr_mrp_bridge.default_overhead_per_1000', str(self.corr_overhead_per_1000_default or 0.0))
        icp.set_param('corr_mrp_bridge.sfg_mode', self.sfg_mode or 'staged')
        icp.set_param('corr_mrp_bridge.enable_peeling_sfg', '1' if self.corr_enable_peeling_sfg else '0')
        icp.set_param('corr_mrp_bridge.enable_mto_for_sfg', '1' if self.enable_mto_for_sfg else '0')
        icp.set_param(
            'corr_mrp_bridge.show_all_chained_mos_in_so_smart_button',
            '1' if self.show_all_chained_mos_in_so_smart_button else '0',
        )
        icp.set_param(
            'corr_mrp_bridge.keep_so_created_corr_mos_draft',
            '1' if self.keep_so_created_corr_mos_draft else '0',
        )
        icp.set_param(
            'corr_mrp_bridge.engine_v2_current_bom_selection_enabled',
            '1' if self.engine_v2_current_bom_selection_enabled else '0',
        )
        icp.set_param('corr_mrp_bridge.assign_workcenter', '1' if self.corr_assign_workcenter else '0')
        icp.set_param('corr_engine_v2.max_slitting_ups', str(int(self.corr_engine_v2_max_slitting_ups or 0)))
        icp.set_param(
            'corr_engine_v2.auto_assign_operation_workcenter',
            '1' if self.corr_engine_v2_auto_assign_operation_workcenter else '0',
        )
        icp.set_param(
            'corr_engine_v2.top_sheet_category_ids',
            ','.join(str(category_id) for category_id in self.corr_engine_v2_top_sheet_category_ids.ids),
        )
        icp.set_param(
            'corr_engine_v2.allowed_paper_material_template_ids',
            ','.join(str(template_id) for template_id in self.corr_engine_v2_allowed_paper_material_template_ids.ids),
        )
        return res

    @api.model
    def get_values(self):
        res = super().get_values()
        icp = self.env['ir.config_parameter'].sudo()
        res.update({
            'autobom_enabled': icp.get_param('corr_mrp_bridge.autobom_enabled', '1') == '1',
            'autobom_scope': icp.get_param('corr_mrp_bridge.autobom_scope', 'template'),
            'corr_active_engine': icp.get_param('corr_mrp_bridge.active_engine', 'old') or 'old',
            'bulk_upload_use_engine_v2': icp.get_param(
                'corr_mrp_bridge.bulk_upload_use_engine_v2',
                '0',
            ) in ('1', 'true', 'True'),
            'bulk_upload_auto_create_missing_materials': icp.get_param(
                'corr_mrp_bridge.bulk_upload_auto_create_missing_materials',
                '0',
            ) in ('1', 'true', 'True'),
            'bulk_upload_auto_create_missing_top_sheets': icp.get_param(
                'corr_mrp_bridge.bulk_upload_auto_create_missing_top_sheets',
                '0',
            ) in ('1', 'true', 'True'),
            'corr_include_ops_default': icp.get_param('corr_mrp_bridge.default_include_ops', '1') == '1',
            'corr_include_sfg_default': icp.get_param('corr_mrp_bridge.default_include_sfg', '1') == '1',
            'corr_include_scrap_default': icp.get_param('corr_mrp_bridge.default_include_scrap', '1') == '1',
            'corr_overhead_per_1000_default': float(icp.get_param('corr_mrp_bridge.default_overhead_per_1000', '0') or 0.0),
            'sfg_mode': (icp.get_param('corr_mrp_bridge.sfg_mode') or 'staged'),
            'corr_enable_peeling_sfg': icp.get_param('corr_mrp_bridge.enable_peeling_sfg', '0') in ('1', 'true', 'True'),
            'enable_mto_for_sfg': icp.get_param('corr_mrp_bridge.enable_mto_for_sfg', '0') in ('1', 'true', 'True'),
            'show_all_chained_mos_in_so_smart_button': icp.get_param(
                'corr_mrp_bridge.show_all_chained_mos_in_so_smart_button', '0'
            ) in ('1', 'true', 'True'),
            'keep_so_created_corr_mos_draft': icp.get_param(
                'corr_mrp_bridge.keep_so_created_corr_mos_draft', '0'
            ) in ('1', 'true', 'True'),
            'engine_v2_current_bom_selection_enabled': icp.get_param(
                'corr_mrp_bridge.engine_v2_current_bom_selection_enabled', '0'
            ) in ('1', 'true', 'True'),
            'corr_assign_workcenter': icp.get_param('corr_mrp_bridge.assign_workcenter', '1') in ('1', 'true', 'True'),
            'corr_engine_v2_max_slitting_ups': int(icp.get_param('corr_engine_v2.max_slitting_ups', '0') or 0),
            'corr_engine_v2_auto_assign_operation_workcenter': icp.get_param(
                'corr_engine_v2.auto_assign_operation_workcenter',
                '1',
            ) in ('1', 'true', 'True'),
        })
        category_ids = []
        raw_category_ids = icp.get_param('corr_engine_v2.top_sheet_category_ids', '') or ''
        for item in raw_category_ids.replace(';', ',').split(','):
            try:
                category_id = int((item or '').strip())
            except Exception:
                category_id = 0
            if category_id:
                category_ids.append(category_id)
        res['corr_engine_v2_top_sheet_category_ids'] = [(6, 0, list(dict.fromkeys(category_ids)))]
        template_ids = []
        raw_template_ids = icp.get_param('corr_engine_v2.allowed_paper_material_template_ids', '') or ''
        for item in raw_template_ids.replace(';', ',').split(','):
            try:
                template_id = int((item or '').strip())
            except Exception:
                template_id = 0
            if template_id:
                template_ids.append(template_id)
        res['corr_engine_v2_allowed_paper_material_template_ids'] = [
            (6, 0, list(dict.fromkeys(template_ids)))
        ]
        return res
