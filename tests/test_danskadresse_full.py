import unittest

import danskadresse_full


class FakeAppModule:
    @staticmethod
    def get_building_placeholder(address_data):
        return {
            "source": "placeholder",
            "access_address_id": (address_data or {}).get("access_address_id"),
            "usage_text": "Ikke verificeret",
        }

    @staticmethod
    def is_positive_report_value(value):
        return value not in (None, "", [], {})

    @staticmethod
    def clean_report_sections(report):
        return report


class FullDanskAdresseTests(unittest.TestCase):
    def setUp(self):
        self.address = {
            "access_address_id": "address-1",
            "address_id": "address-1",
            "house_number": "12",
            "municipality_code": "0330",
        }

    def test_normalizes_operational_enrichment(self):
        result = {
            "ok": True,
            "cache": "miss",
            "include": "bbr,dagi,dar,jordstykke",
            "payload": {
                "id": "address-1",
                "husnr": "12",
                "bbr": {
                    "bygning": {
                        "id": "building-1",
                        "bygningsnummer": 1,
                        "anvendelse": 223,
                        "opfoerelsesaar": 1978,
                        "samlet_bygningsareal": 4093,
                        "antal_etager": 1,
                        "ydervaeggens_materiale": 5,
                        "tagdaekningsmateriale": 2,
                        "varmeinstallation": 7,
                        "opvarmningsmiddel": 1,
                        "asbestholdigt_materiale": 1,
                        "sikringsrumpladser": 45,
                    },
                    "enhed": {"samlet_boligareal": 2280},
                    "etager": [],
                    "opgange": [{"elevator": 1}],
                    "grunde": [
                        {
                            "grund_areal": 7500,
                            "vandforsyning": 1,
                            "matrikelnr": "12ab",
                            "ejerlavskode": "12345",
                        }
                    ],
                    "tekniske_anlaeg": [
                        {"klassifikation": "solcelleanlaeg", "effekt": 5000},
                        {
                            "klassifikation": "olietank",
                            "tankstoerrelse_liter": 2500,
                            "placering": "Nedgravet",
                        },
                    ],
                },
                "dagi": {
                    "kommune": {"kode": "0330", "navn": "Slagelse"},
                    "region": {"kode": "1085", "navn": "Region Sjælland"},
                    "politikreds": {"kode": "1467", "navn": "Midt- og Vestsjællands Politi"},
                },
                "dar": {
                    "matrikelnr": "12ab",
                    "ejerlav": {"kode": "12345", "navn": "Slagelse Bygrunde"},
                    "zone": "Byzone",
                    "vejpunkt": {"x": 11.3, "y": 55.4, "kvalitet": "A"},
                    "adgangspunkt": {"x": 11.31, "y": 55.41, "kvalitet": "A"},
                    "bebyggelser": [{"navn": "Slagelse", "type": "by"}],
                },
                "jordstykke": {
                    "matrikelnr": "12ab",
                    "ejerlavskode": "12345",
                    "areal": 7500,
                    "sfe_ejendomsnr": "1234567",
                },
            },
        }

        building = danskadresse_full.normalize(result, self.address, FakeAppModule)

        self.assertEqual(building["outer_wall_material_text"], "Træ")
        self.assertEqual(building["roof_material_text"], "Cementtagsten")
        self.assertEqual(building["heating_installation_text"], "Elvarme")
        self.assertEqual(building["heating_fuel_text"], "El")
        self.assertTrue(building["elevator_registered"])
        self.assertEqual(building["shelter_spaces"], 45)
        self.assertEqual(building["cadastre"]["matrikel_number"], "12ab")
        self.assertEqual(building["cadastre"]["owner_area_name"], "Slagelse Bygrunde")
        self.assertEqual(building["cadastre"]["ground_area_m2"], 7500)
        self.assertEqual(building["administrative_context"]["municipality"]["name"], "Slagelse")
        self.assertEqual(building["operational_installations"][0]["type"], "Solcelleanlæg")
        self.assertEqual(building["operational_installations"][1]["tank_size_l"], 2500)

    def test_report_additions_are_compact_and_operational(self):
        building = {
            "energy_label": "C",
            "asbestos_material_text": "Ja",
            "elevator_registered": True,
            "shelter_spaces": 20,
            "operational_installations": [
                {"type": "Solcelleanlæg", "output": 5000},
                {"type": "Olietank", "tank_size_l": 2500, "placement": "Nedgravet"},
            ],
            "cadastre": {
                "matrikel_number": "12ab",
                "owner_area_name": "Slagelse Bygrunde",
                "ground_area_m2": 7500,
                "zone": "Byzone",
                "ground_water_supply": "Alment vandforsyningsanlæg",
            },
            "administrative_context": {
                "municipality": {"name": "Slagelse"},
                "region": {"name": "Region Sjælland"},
                "police_district": {"name": "Midt- og Vestsjællands Politi"},
            },
        }

        additions = danskadresse_full.operational_report_additions(building)

        self.assertIn("Energimærke: C", additions["building_details"])
        self.assertTrue(any("Asbest" in line for line in additions["risk_context_lines"]))
        self.assertTrue(any("Solcelleanlæg" in line for line in additions["risk_context_lines"]))
        self.assertTrue(any("Olietank" in line for line in additions["risk_context_lines"]))
        self.assertIn("Matrikel: 12ab, Slagelse Bygrunde", additions["supplementary_lines"])
        self.assertIn("Grundareal: 7500 m²", additions["supplementary_lines"])


if __name__ == "__main__":
    unittest.main()
