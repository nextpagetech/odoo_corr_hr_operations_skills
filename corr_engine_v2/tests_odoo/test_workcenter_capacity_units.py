from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "corr_mrp_bridge", "corr_engine_v2")
class TestWorkcenterCapacityUnits(TransactionCase):
    def test_existing_mm_length_displays_as_cm(self):
        workcenter = self.env["mrp.workcenter"].create(
            {
                "name": "WC Units Existing",
                "corr_length_max_mm": 100000.0,
            }
        )
        self.assertEqual(workcenter.corr_length_max_cm, 10000.0)

    def test_editing_length_cm_updates_internal_mm_storage(self):
        workcenter = self.env["mrp.workcenter"].create(
            {
                "name": "WC Units Edit",
                "corr_length_max_mm": 0.0,
                "corr_deckle_max_cm": 160.0,
            }
        )
        workcenter.corr_length_max_cm = 100.0

        self.assertEqual(workcenter.corr_length_max_mm, 1000.0)
        self.assertEqual(workcenter.get_corr_length_max_mm(), 1000.0)
        self.assertEqual(workcenter.get_corr_deckle_max_mm(), 1600.0)

    def test_workcenter_form_view_uses_cm_length_fields(self):
        arch = self.env.ref("corr_mrp_bridge.view_mrp_workcenter_corr_rules").arch_db or ""
        self.assertIn('name="corr_length_min_cm"', arch)
        self.assertIn('name="corr_length_max_cm"', arch)
