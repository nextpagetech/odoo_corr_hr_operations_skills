from odoo.tests.common import TransactionCase

from ..spec_adapter import build_engine_spec_input


class TestEngineV2SpecSurfaceIsolation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Plant = cls.env["corr.plant.param"]
        cls.Profile = cls.env["corr.engine.surface.profile"]
        cls.Spec = cls.env["corr.engine.v2.spec"]

        cls.plant = cls.Plant.create({"code": "TEST-V2-SURFACE-PLANT"})
        cls.profile = cls.Profile.create(
            {
                "name": "Surface Profile A",
                "company_id": cls.env.company.id,
                "plant_id": cls.plant.id,
                "surface_category": "default",
                "is_primary": True,
                "printing_required": True,
                "print_process": "flexo",
                "print_colors": 2,
                "print_coverage_percent": 50.0,
                "top_sheet_mode": "roll",
                "top_sheet_roll_code": "ROLL-A",
            }
        )

    def test_surface_profile_values_are_copied_to_each_spec(self):
        spec_1 = self.Spec.create(
            {
                "name": "Spec 1",
                "plant_id": self.plant.id,
                "surface_profile_id": self.profile.id,
            }
        )
        spec_2 = self.Spec.create(
            {
                "name": "Spec 2",
                "plant_id": self.plant.id,
                "surface_profile_id": self.profile.id,
            }
        )

        spec_2.write({"surface_top_sheet_mode": "unit_sheet"})

        self.assertEqual(self.profile.top_sheet_mode, "roll")
        self.assertEqual(spec_1.surface_top_sheet_mode, "roll")
        self.assertEqual(spec_2.surface_top_sheet_mode, "unit_sheet")

        input_1 = build_engine_spec_input(spec_1)
        input_2 = build_engine_spec_input(spec_2)
        self.assertEqual(input_1.surface_profile.top_sheet_mode, "roll")
        self.assertEqual(input_2.surface_profile.top_sheet_mode, "unit_sheet")

    def test_defaults_use_context_plant_and_primary_surface_profile(self):
        defaults = self.Spec.with_context(default_plant_id=self.plant.id).default_get(
            ["plant_id", "surface_profile_id", "surface_top_sheet_mode"]
        )

        self.assertEqual(defaults.get("plant_id"), self.plant.id)
        self.assertEqual(defaults.get("surface_profile_id"), self.profile.id)
        self.assertEqual(defaults.get("surface_top_sheet_mode"), "roll")
