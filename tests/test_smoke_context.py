import unittest

import smoke_context


class SmokeContextTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
