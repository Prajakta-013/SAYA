import unittest
from unittest.mock import Mock, patch

import requests

from ai.routing import get_route


class TestRouting(unittest.TestCase):
    @patch("ai.routing.requests.get")
    def test_parses_osrm_distance_duration_and_geometry(self, get):
        response = Mock()
        response.json.return_value = {
            "code": "Ok",
            "routes": [
                {
                    "distance": 1234,
                    "duration": 345,
                    "geometry": {"coordinates": [[72.8, 19.0], [72.9, 19.1]]},
                }
            ],
        }
        get.return_value = response

        route = get_route(19.0, 72.8, 19.1, 72.9)

        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["timeout"], 8)
        self.assertEqual(route["distance_km"], 1.23)
        self.assertEqual(route["duration_minutes"], 5.75)
        self.assertEqual(route["geometry"], [[72.8, 19.0], [72.9, 19.1]])

    @patch("ai.routing.requests.get", side_effect=requests.Timeout("timeout"))
    def test_route_timeout_returns_no_route(self, _get):
        self.assertIsNone(get_route(19.0, 72.8, 19.1, 72.9))

    @patch("ai.routing.requests.get")
    def test_invalid_route_payload_returns_no_route(self, get):
        response = Mock()
        response.json.return_value = {"code": "Ok", "routes": []}
        get.return_value = response

        self.assertIsNone(get_route(19.0, 72.8, 19.1, 72.9))

    @patch("ai.routing.requests.get")
    def test_invalid_route_geometry_returns_no_route(self, get):
        response = Mock()
        response.json.return_value = {
            "code": "Ok",
            "routes": [
                {
                    "distance": 1234,
                    "duration": 345,
                    "geometry": {"coordinates": [[200, 19], [72.9, 19.1]]},
                }
            ],
        }
        get.return_value = response

        self.assertIsNone(get_route(19.0, 72.8, 19.1, 72.9))


if __name__ == "__main__":
    unittest.main()
