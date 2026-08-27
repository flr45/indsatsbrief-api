import unittest

import asbestos_guard


class AsbestosGuardTests(unittest.TestCase):
    def test_understands_boolean_and_bbr_codes(self):
        self.assertEqual(
            asbestos_guard.inspect_building({"asbestholdigt_materiale": True})["status"],
            "yes",
        )
        self.assertEqual(
            asbestos_guard.inspect_building({"asbestholdigt_materiale": 0})["status"],
            "no",
        )
        self.assertEqual(
            asbestos_guard.inspect_building({"asbestholdigt_materiale": 9})["status"],
            "unknown",
        )

    def test_property_check_promotes_positive_secondary_building(self):
        checks = [
            asbestos_guard.inspect_building(
                {"bygningsnummer": 1, "asbestholdigt_materiale": 0}
            ),
            asbestos_guard.inspect_building(
                {
                    "bygningsnummer": 2,
                    "anvendelse_tekst": "Udhus",
                    "asbestholdigt_materiale": 1,
                }
            ),
        ]
        combined = asbestos_guard.combine_checks(
            checks, coverage="all_registered_buildings"
        )

        self.assertEqual(combined["status"], "yes")
        self.assertEqual(combined["buildings_checked"], 2)
        self.assertEqual(combined["positive_buildings"][0]["building_number"], 2)

        building = {"asbestos_check": combined}
        lines = asbestos_guard.report_lines(building)
        self.assertTrue(any("ASBEST" in line for line in lines))
        self.assertTrue(any("1 af 2" in line for line in lines))

    def test_no_is_worded_as_bbr_registration_not_absolute_truth(self):
        combined = asbestos_guard.combine_checks(
            [
                asbestos_guard.inspect_building(
                    {"bygningsnummer": 1, "asbestholdigt_materiale": 0}
                ),
                asbestos_guard.inspect_building(
                    {"bygningsnummer": 2, "asbestholdigt_materialale": 0}
                ),
            ],
            coverage="all_registered_buildings",
        )
        # The misspelled field above deliberately verifies that an absent field
        # cannot be interpreted as a definitive negative.
        self.assertNotEqual(combined["status"], "no")

    def test_eternit_is_flagged_only_as_material_indicator(self):
        check = asbestos_guard.inspect_building(
            {
                "asbestholdigt_materiale": 0,
                "roof_material_text": "Eternit/cement",
                "outer_wall_material_text": "Mursten",
            }
        )
        combined = asbestos_guard.combine_checks([check], coverage="main_building_only")
        lines = asbestos_guard.report_lines({"asbestos_check": combined})

        self.assertEqual(combined["status"], "no")
        self.assertTrue(any("Materialeindikator" in line for line in lines))
        self.assertTrue(any("ikke i sig selv dokumentation" in line for line in lines))

    def test_missing_field_is_never_reported_as_no(self):
        check = asbestos_guard.inspect_building({"bygningsnummer": 1})
        self.assertEqual(check["status"], "not_returned")
        lines = asbestos_guard.report_lines(
            {"asbestos_check": asbestos_guard.combine_checks([check])}
        )
        self.assertTrue(any("ikke returneret" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
