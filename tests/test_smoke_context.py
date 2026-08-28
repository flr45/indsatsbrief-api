import unittest
from unittest.mock import patch

import smoke_context


class SmokeContextTests(unittest.TestCase):
    def setUp(self):
        with smoke_context._CACHE_LOCK:
            smoke_context._CACHE.clear()

    def test_angular_difference_wraps_north(self):
        self.assertAlmostEqual(smoke_context.angular_difference(350, 10), 20)
        self.assertAlmostEqual(smoke_context.angular_difference(90, 270), 180)

    def test_classifies_common_vulnerable_places(self):
        self.assertEqual(
            smoke_context.classify_place({"amenity": "school"}),
            ("school", "Skole"),
        )
        self.assertEqual(
            smoke_context.classify_place({"amenity": "hospital"}),
            ("hospital", "Hospital"),
        )
        self.assertEqual(
            smoke_context.classify_place(
                {"amenity": "social_facility", "social_facility": "nursing_home"}
            ),
            ("care", "Pleje-/botilbud"),
        )

    def test_sector_filter_keeps_only_downwind_places(self):
        places = [
            {"name": "Øst skole", "bearing_deg": 90, "distance_m": 1200},
            {"name": "NØ børnehave", "bearing_deg": 55, "distance_m": 800},
            {"name": "Vest hospital", "bearing_deg": 270, "distance_m": 500},
        ]
        selected = smoke_context.sector_places(places, direction_to=90, half_angle=40)
        self.assertEqual([item["name"] for item in selected], ["NØ børnehave", "Øst skole"])

    def test_normalize_elements_uses_way_center_and_distance(self):
        payload = {
            "elements": [
                {
                    "type": "way",
                    "id": 123,
                    "center": {"lat": 55.0100, "lon": 11.0000},
                    "tags": {"amenity": "kindergarten", "name": "Børnehuset"},
                }
            ]
        }
        places = smoke_context._normalize_elements(payload, 55.0000, 11.0000)
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0]["category"], "childcare")
        self.assertEqual(places[0]["name"], "Børnehuset")
        self.assertGreater(places[0]["distance_m"], 1000)

    def test_overpass_query_is_post_friendly_and_bounded(self):
        query = smoke_context._overpass_query(55.4, 11.35, 2000)
        self.assertIn("[timeout:12]", query)
        self.assertIn("around:2000", query)
        self.assertIn("out center tags qt;", query)

    @patch("smoke_context._request_overpass")
    def test_large_radius_falls_back_to_clearly_marked_partial_result(self, request_mock):
        request_mock.side_effect = [
            {"ok": False, "attempts": [{"url": "primary", "error": "timeout"}]},
            {
                "ok": True,
                "working_overpass_url": "fallback",
                "attempts": [{"url": "fallback", "status_code": 200}],
                "payload": {
                    "elements": [
                        {
                            "type": "node",
                            "id": 1,
                            "lat": 55.401,
                            "lon": 11.351,
                            "tags": {"amenity": "school", "name": "Testskole"},
                        }
                    ]
                },
            },
        ]

        result = smoke_context.fetch_nearby_places(55.4, 11.35, 5000)

        self.assertTrue(result["ok"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["requested_radius_m"], 5000)
        self.assertEqual(result["radius_m"], smoke_context.DEGRADED_RADIUS_M)
        self.assertEqual(result["nearby_count"], 1)
        self.assertIn("delresultat", result["degraded_reason"])


if __name__ == "__main__":
    unittest.main()
