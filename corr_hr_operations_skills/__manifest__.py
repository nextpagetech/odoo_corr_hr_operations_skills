{
    "name": "HR Operations Skills",
    "summary": "Match MRP work order employees by HR Operations skills",
    "version": "18.0.1.0.0",
    "description": """
Adds Operations skills for employees, syncs operation skills from the corrugation engine, and filters work order employee selection by matching operation skills.
    """,
    "license": "LGPL-3",
    "author": "Next Page Technologies Pvt Ltd",
    "website": "https://nextpagetechnologies.com",
    "category": "Human Resources/Employees",
    "depends": [
        "hr_skills",
        "corr_mrp_bridge",
        "workorder_employee_picker",
    ],
    "data": [
        "data/hr_operations_skill_type.xml",
        "views/hr_employee_skill_views.xml",
        "views/workorder_employee_wizard_views.xml",
        "views/mrp_workorder_views.xml",
    ],
    "images": [
        "static/description/banner.png",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
