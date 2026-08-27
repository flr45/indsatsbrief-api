import unittest

import property_inventory


class PropertyInventoryTests(unittest.TestCase):
    def test_normalizes_all_common_secondary_building_fields(self):
        raw = {
            "id": "garage-1",
            "bygningsnummer": 2,
            "anvendelse": 910,
            "opfoerelsesaar": 1988,
            "ombygningsaar": 2004,
            "samlet_bygningsareal": 42,
            "bebygget_areal": 42,
            "ydervaeggens_materiale": 5,
            "tagdaekningsmateriale": 3,
            "asbestholdigt_materiale": 1,
        }

        item = property_inventory.normalize_building(raw)

        self.assertEqual(item["building_number"], 2)
        self.assertEqual(item["use_text"], "Garage")
        self.assertEqual(item["construction_year"], 1988)
        self.assertEqual(item["alteration_year"], 2004)
        self.assertEqual(item["total_area_m2"], 42)
        self.assertEqual(item["outer_wall"], "Træ")
        self.assertEqual(item["roof"], "Eternit/cement")
        self.assertEqual(item["asbestos_status"], "yes")

    def test_inventory_keeps_house_garage_carport_and_outhouse(self):
        inventory = property_inventory.build_inventory(
            [
                {"bygningsnummer": 4, "anvendelse": 930, "samlet_bygningsareal": 16},
                {"bygningsnummer": 1, "anvendelse": 120, "samlet_bygningsareal": 155},
                {"bygningsnummer": 3, "anvendelse": 920, "samlet_bygningsareal": 28},
                {"bygningsnummer": 2, "anvendelse": 910, "samlet_bygningsareal": 41},
            ]
        )

        self.assertEqual([item["building_number"] for item in inventory], [1, 2, 3, 4])
        self.assertEqual(
            [item["use_text"] for item in inventory],
            ["Fritliggende enfamiliehus", "Garage", "Carport", "Udhus"],
        )

    def test_report_notes_extensions_and_embedded_structures(self):
        building = {
            "building_inventory_status": "ok",
            "registered_buildings": [
                {
                    "building_number": 1,
                    "use_text": "Fritliggende enfamiliehus",
                    "total_area_m2": 180,
                    "construction_year": 1972,
                    "alteration_year": 1998,
                    "built_in_garage_m2": 25,
                    "built_in_outhouse_m2": 12,
                    "outer_wall": "Mursten",
                    "roof": "Tagsten/tegl",
                    "asbestos_status": "no",
                },
                {
                    "building_number": 2,
                    "use_text": "Garage",
                    "total_area_m2": 40,
                    "construction_year": 1980,
                    "roof": "Eternit/cement",
                    "asbestos_status": "unknown",
                },
            ],
            "building_inventory_may_be_truncated": False,
        }

        lines = property_inventory.report_lines(building)

        self.assertEqual(lines[0], "Bygningsoversigt: 2 registrerede bygninger")
        self.assertTrue(any("om-/tilbygget 1998" in line for line in lines))
        self.assertTrue(any("indbygget garage 25 m²" in line for line in lines))
        self.assertTrue(any("indbygget udhus 12 m²" in line for line in lines))
        self.assertTrue(any("Bygning 2: Garage" in line for line in lines))
        self.assertTrue(any("asbeststatus ukendt" in line for line in lines))

    def test_twenty_buildings_are_marked_potentially_truncated(self):
        building = {
            "building_inventory_status": "ok",
            "registered_buildings": [
                {"building_number": number, "use_text": "Udhus"}
                for number in range(1, 21)
            ],
            "building_inventory_may_be_truncated": True,
        }

        lines = property_inventory.report_lines(building)
        self.assertIn("API-grænse på 20 er nået", lines[0])
        self.assertEqual(len(lines), 21)


if __name__ == "__main__":
    unittest.main()
