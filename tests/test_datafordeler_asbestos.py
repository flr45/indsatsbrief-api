import os
import unittest
from unittest.mock import patch

import datafordeler_asbestos


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class DatafordelerAsbestosTests(unittest.TestCase):
    def setUp(self):
        datafordeler_asbestos._CACHE.clear()

    def test_bbr_codes_1_2_3_are_positive(self):
        for code in (1, 2, 3, "1", "2", "3"):
            self.assertEqual(datafordeler_asbestos.asbestos_status(code), "yes")
        self.assertIn("tag", datafordeler_asbestos.asbestos_text(2).lower())
        self.assertIn("ydervæg", datafordeler_asbestos.asbestos_text(1).lower())

    def test_existing_normalized_code_2_is_preserved_without_network(self):
        building = {
            "bbr_id": 1,
            "building_type_text": "Lager",
            "asbestos_material": 2,
            "asbestos_check": {"status": "not_returned"},
        }
        with patch("datafordeler_asbestos.fetch") as fetch:
            result = datafordeler_asbestos.enrich_if_needed(building, "husnummer-id")
        fetch.assert_not_called()
        self.assertEqual(result["asbestos_check"]["status"], "yes")
        self.assertEqual(result["asbestos_material_text"], "Asbestholdigt tagdækningsmateriale")

    def test_graphql_fallback_promotes_positive_roof_registration(self):
        payload = {
            "data": {
                "BBR_Bygning": {
                    "nodes": [
                        {
                            "id_lokalId": "b1",
                            "status": "6",
                            "husnummer": "husnummer-id",
                            "byg007Bygningsnummer": 1,
                            "byg021BygningensAnvendelse": 320,
                            "byg036AsbestholdigtMateriale": 2,
                        }
                    ]
                }
            }
        }
        building = {
            "access_address_id": "husnummer-id",
            "asbestos_check": {"status": "not_returned"},
        }
        with patch.dict(os.environ, {"DATAFORDELER_API_KEY": "test-key"}, clear=False), patch(
            "datafordeler_asbestos.requests.post", return_value=FakeResponse(payload)
        ):
            result = datafordeler_asbestos.enrich_if_needed(building)
        self.assertEqual(result["asbestos_check"]["status"], "yes")
        self.assertEqual(result["asbestos_fallback"]["source"], "Datafordeler BBR GraphQL v1")
        self.assertEqual(result["asbestos_material"], "2")

    def test_missing_graphql_field_does_not_infer_asbestos_from_eternit(self):
        payload = {
            "data": {
                "BBR_Bygning": {
                    "nodes": [
                        {
                            "id_lokalId": "b1",
                            "status": "6",
                            "byg033Tagdaekningsmateriale": 3,
                            "byg036AsbestholdigtMateriale": None,
                        }
                    ]
                }
            }
        }
        building = {
            "access_address_id": "husnummer-id",
            "roof_material_text": "Eternit/cement",
            "asbestos_check": {"status": "not_returned"},
        }
        with patch.dict(os.environ, {"DATAFORDELER_API_KEY": "test-key"}, clear=False), patch(
            "datafordeler_asbestos.requests.post", return_value=FakeResponse(payload)
        ):
            result = datafordeler_asbestos.enrich_if_needed(building)
        self.assertNotEqual(result["asbestos_check"].get("status"), "yes")


if __name__ == "__main__":
    unittest.main()
