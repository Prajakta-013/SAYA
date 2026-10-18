"""
Tests for automatic emergency dispatch:
accident → incident → priority → ambulance → hospital → routing → assignment.
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app, create_assignment_record, create_incident_record, get_database_connection  # noqa: E402
from ai.accident_detector import simulate_cctv_accident  # noqa: E402
from ai.emergency_dispatcher import (  # noqa: E402
    dispatch_detected_accident,
    dispatch_simulated_accident,
    get_last_auto_response,
    set_last_auto_response,
)
from ai.priority_engine import calculate_priority  # noqa: E402


def _fake_route(start_lat, start_lon, end_lat, end_lon):
    """Deterministic route result so tests do not depend on live OSRM."""
    distance = abs(start_lat - end_lat) + abs(start_lon - end_lon)
    return {
        "distance_km": round(max(distance * 100, 1.5), 2),
        "duration_minutes": round(max(distance * 40, 4.0), 2),
        "geometry": [[start_lon, start_lat], [end_lon, end_lat]],
    }


class TestEmergencyDispatch(unittest.TestCase):

    def setUp(self):
        set_last_auto_response(None)
        self.client = app.test_client()
        self.client.post(
            "/admin/login",
            data={"username": "admin", "password": "saya"},
        )

        connection = get_database_connection()
        ambulance = connection.execute(
            "SELECT id FROM ambulances ORDER BY id LIMIT 1"
        ).fetchone()
        if ambulance:
            connection.execute(
                """
                UPDATE ambulances
                SET status = 'Available', current_incident_id = NULL
                WHERE id = ?
                """,
                (ambulance["id"],),
            )
        hospital = connection.execute(
            "SELECT id FROM hospitals ORDER BY id LIMIT 1"
        ).fetchone()
        if hospital:
            connection.execute(
                """
                UPDATE hospitals
                SET status = 'Available', available_beds = 50, emergency_capacity = 40
                WHERE id = ?
                """,
                (hospital["id"],),
            )
        connection.commit()
        connection.close()

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_dispatch_simulated_accident_full_pipeline(self, *_mocks):
        result = dispatch_simulated_accident(
            camera_id="CAM-MUM-01",
            create_incident_fn=create_incident_record,
            create_assignment_fn=create_assignment_record,
        )

        self.assertTrue(result["success"], msg=result.get("message"))
        self.assertEqual(result["emergency_status"], "AUTO_DISPATCHED")
        self.assertIsNotNone(result["incident_id"])
        self.assertIsNotNone(result["ambulance"])
        self.assertIsNotNone(result["hospital"])
        self.assertIsNotNone(result["ambulance_eta"])
        self.assertIsNotNone(result["hospital_eta"])
        self.assertEqual(result["camera_id"], "CAM-MUM-01")

        expected_score, expected_priority = calculate_priority(5, 1, "Road Accident")
        self.assertEqual(result["priority_score"], expected_score)
        self.assertEqual(result["priority"], expected_priority)

        connection = get_database_connection()
        incident = connection.execute(
            "SELECT * FROM incidents WHERE id = ?",
            (result["incident_id"],),
        ).fetchone()
        assignment = connection.execute(
            "SELECT * FROM assignments WHERE incident_id = ?",
            (result["incident_id"],),
        ).fetchone()
        connection.close()

        self.assertIsNotNone(incident)
        self.assertEqual(incident["status"], "Assigned")
        self.assertIsNotNone(assignment)
        self.assertEqual(get_last_auto_response()["incident_id"], result["incident_id"])

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_detect_accident_endpoint(self, *_mocks):
        detection = simulate_cctv_accident(camera_id="CAM-MUM-02")
        response = self.client.post("/detect-accident", json=detection)

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["emergency_status"], "AUTO_DISPATCHED")
        self.assertIsNotNone(payload["assignment"])

    def test_detect_accident_rejects_missing_location_instead_of_using_demo_defaults(self):
        response = self.client.post(
            "/detect-accident",
            json={"accident_detected": True, "confidence": 0.9},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("latitude", response.get_json()["error"])

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_simulate_accident_endpoint_triggers_pipeline(self, *_mocks):
        response = self.client.post(
            "/simulate-accident",
            json={"camera_id": "CAM-MUM-03"},
        )

        self.assertEqual(response.status_code, 201, msg=response.get_json())
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["detection"]["accident_detected"], True)
        self.assertIsNotNone(payload["incident_id"])
        self.assertIsNotNone(payload["ambulance"])
        self.assertIsNotNone(payload["hospital"])

        connection = get_database_connection()
        assignment = connection.execute(
            "SELECT * FROM assignments WHERE incident_id = ?",
            (payload["incident_id"],),
        ).fetchone()
        connection.close()
        self.assertIsNotNone(assignment)

    def test_no_accident_does_not_create_incident(self):
        before = get_database_connection()
        count_before = before.execute("SELECT COUNT(*) AS c FROM incidents").fetchone()["c"]
        before.close()

        result = dispatch_detected_accident(
            {
                "accident_detected": False,
                "confidence": 0.1,
                "camera_id": "CAM-MUM-01",
                "location": "Test",
                "latitude": 19.0,
                "longitude": 72.8,
                "incident_type": "Road Accident",
                "severity": 1,
                "number_of_patients": 0,
            },
            create_incident_fn=create_incident_record,
            create_assignment_fn=create_assignment_record,
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["emergency_status"], "NO_ACCIDENT")

        after = get_database_connection()
        count_after = after.execute("SELECT COUNT(*) AS c FROM incidents").fetchone()["c"]
        after.close()
        self.assertEqual(count_before, count_after)


if __name__ == "__main__":
    unittest.main()
