import os
import unittest

import bbr_danskadresse


class FakeAppModule:
    @staticmethod
    def get_building_placeholder(address_data):
        return {
            "source": "placeholder",
            "access_address_id": (address_data or {}).get("access_address_id"),
            "usage_text": "Ikke verificeret",
        }


class DanskAdresseAdapterTests(unittest.TestCase):
    def setUp(self):
        self.address = {
            "access_address_id": "0a3f50ab-test",
            "address_id": "0a3f5099-test",
            "house_number": "26",
            "municipality_code": "0330",
        }

    def test_missing_api_key_is_safe(self):
        old_primary = os.environ.pop("DANSKADRESSE_API_KEY", None)
        old_alias = os.environ.pop("INDSATSBRIEF_DANSKADRESSE_API_KEY", None)
        try:
            result = bbr_danskadresse.fetch_access_address("abc")
            self.assertFalse(result["ok"])
            self.assertIn("mangler", result["error"].lower())
        finally:
            if old_primary is not None:
                os.environ["DANSKADRESSE_API_KEY"] = old_primary
            if old_alias is not None:
                os.environ["INDSATSBRIEF_DANSKADRESSE_API_KEY"] = old_alias

    def test_normalizes_core_building_fields(self):
        result = {
            "ok": True,
            "cache": "miss",
            "payload": {
                "id": self.address["access_address_id"],
                "husnr": "26",
                "bbr": {
                    "bygning": {
                        "bygningsnummer": 1,
                        "anvendelseskode": 120,
                        "anvendelse_tekst": "Fritliggende enfamiliehus",
                        "opfoerelsesaar": 1960,
                        "ombygningsaar": 1976,
                        "samlet_bygningsareal": 184,
                        "bebygget_areal": 115,
                        "antal_etager": 2,
                        "ydervaegs_materiale_kode": 1,
                        "ydervaegs_materiale_tekst": "Mursten",
                        "tagdaeknings_materiale_kode": 3,
                        "tagdaeknings_materiale_tekst": "Fibercement",
                        "varmeinstallation_kode": 1,
                        "varmeinstallation_tekst": "Fjernvarme",
                        "asbestholdigt_materiale": True,
                        "energimaerke": "C",
                        "fredning_status": "Ikke fredet",
                    },
                    "enhed": {
                        "samlet_boligareal": 172,
                    },
                    "etager": [
                        {"etagebetegnelse": "st", "samlet_areal_af_etage": 115},
                        {"etagebetegnelse": "kl", "samlet_areal_af_etage": 69, "kaelder_areal": 69},
                    ],
                    "tekniske_anlaeg": [{"anlaeg_type_tekst": "Varmepumpe"}],
                    "grunde": [{"id": "grund-1"}],
                    "opgange": [{"elevator": False}],
                },
            },
        }

        building = bbr_danskadresse.normalize(result, self.address, FakeAppModule)

        self.assertEqual(building["source"], "BBR via DanskAdresseAPI")
        self.assertEqual(building["usage_text"], "Fritliggende enfamiliehus")
        self.assertEqual(building["construction_year"], 1960)
        self.assertEqual(building["area_m2"], 184)
        self.assertEqual(building["residential_area_m2"], 172)
        self.assertEqual(building["outer_wall_material_text"], "Mursten")
        self.assertEqual(building["roof_material_text"], "Fibercement")
        self.assertEqual(building["energy_label"], "C")
        self.assertEqual(building["asbestos_material_text"], "Ja")
        self.assertTrue(building["basement_present"])
        self.assertEqual(building["basement_area_m2"], 69)
        self.assertEqual(len(building["technical_installations"]), 1)

    def test_payload_envelope_is_supported(self):
        payload = {
            "data": {
                "id": "x",
                "bbr": {"bygning": {"opfoerelsesaar": 2001}},
            }
        }
        self.assertEqual(bbr_danskadresse._unwrap_payload(payload)["id"], "x")

    def test_floor_without_area_can_still_mark_basement(self):
        normalized = bbr_danskadresse._normalize_floors(
            {"etager": [{"etagebetegnelse": "Kælder"}]}
        )
        self.assertTrue(normalized["basement_present"])
        self.assertIsNone(normalized["basement_area_m2"])


if __name__ == "__main__":
    unittest.main()
