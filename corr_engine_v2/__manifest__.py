{
    "name": "Corrugation Engine v2",
    "summary": "Advanced corrugation MRP planning and materialization engine",
    "version": "18.0.1.0.5",
    "license": "LGPL-3",
    "author": "Your Company",
    "website": "",
    "category": "Manufacturing/MRP",
    "depends": [
        "corr_mrp_bridge",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/engine_style_views.xml",
        "views/flute_views.xml",
        "views/engine_v2_wire_type_views.xml",
        "views/engine_v2_spec_views.xml",
        "views/plant_param_views.xml",
        "views/engine_v2_phase8_config_views.xml",
        "wizards/engine_v2_phase8_seed_wizard_view.xml",
        "wizards/engine_v2_debug_wizard_view.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "corr_engine_v2/static/src/js/engine_v2_raw_json_copy.js",
        ],
    },
    "installable": True,
    "application": False,
}



