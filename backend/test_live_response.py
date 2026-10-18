import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app import (
    app,
    _format_local_timestamp,
    create_assignment_record,
    create_incident_record,
    get_database_connection,
)


def _route(distance_km=12.5, duration_minutes=9.5):
    return {
        "distance_km": distance_km,
        "duration_minutes": duration_minutes,
        "geometry": [[72.8802, 19.0701], [72.8656, 19.0896]],
    }


class TestLiveResponseDetails(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.client.post(
            "/admin/login",
            data={"username": "admin", "password": "saya"},
        )
        connection = get_database_connection()
        connection.execute(
            """
            UPDATE ambulances
            SET status = 'Busy', current_incident_id = NULL
            """
        )
        connection.execute(
            """
            UPDATE hospitals
            SET status = 'Limited', available_beds = 0, emergency_capacity = 0
            """
        )
        connection.commit()
        connection.close()

    def _incident(self, latitude=19.1234, longitude=72.9876, gps_accuracy=None):
        return create_incident_record(
            "Road Accident",
            f"controlled test {uuid.uuid4()}",
            latitude,
            longitude,
            5,
            1,
            detection_source="Test phone",
            crash_confidence=0.87,
            event_id=f"test-event-{uuid.uuid4()}",
            detection_timestamp="2026-10-13T17:17:28.934608+00:00",
            gps_accuracy=gps_accuracy,
        )[0]

    def _make_available_ambulance_and_hospital(self, hospital_id=1):
        connection = get_database_connection()
        ambulance = connection.execute(
            "SELECT id FROM ambulances ORDER BY id LIMIT 1"
        ).fetchone()
        connection.execute(
            """
            UPDATE ambulances SET status = 'Available',
                latitude = 19.0701, longitude = 72.8802
            WHERE id = ?
            """,
            (ambulance["id"],),
        )
        connection.execute(
            """
            UPDATE hospitals SET status = 'Available',
                available_beds = 22, occupied_beds = 8, total_beds = 30,
                emergency_capacity = 18, latitude = 19.076,
                longitude = 72.8777, location = 'Test hospital address'
            WHERE id = ?
            """,
            (hospital_id,),
        )
        other_hospital = connection.execute(
            "SELECT id FROM hospitals WHERE id != ? ORDER BY id LIMIT 1",
            (hospital_id,),
        ).fetchone()
        if other_hospital:
            connection.execute(
                """
                UPDATE hospitals SET status = 'Available',
                    available_beds = 1, occupied_beds = 4, total_beds = 5,
                    emergency_capacity = 1, latitude = 19.033,
                    longitude = 72.835, location = 'Lower-capacity test address'
                WHERE id = ?
                """,
                (other_hospital["id"],),
            )
        connection.commit()
        connection.close()
        return ambulance["id"]

    @patch("ai.hospital_selector.get_route", return_value=_route(4.25, 6.5))
    @patch("ai.ambulance_selector.get_route", return_value=_route(7.75, 11.5))
    def test_assignment_details_and_dashboard_use_stored_values(
        self, _ambulance_route, _hospital_route
    ):
        ambulance_id = self._make_available_ambulance_and_hospital()
        incident_id = self._incident(gps_accuracy=None)

        payload, error, status = create_assignment_record(incident_id)

        self.assertIsNone(error)
        self.assertEqual(status, 201)
        self.assertEqual(payload["ambulance"]["ambulance_id"], ambulance_id)
        self.assertEqual(payload["ambulance"]["road_eta_minutes"], 11.5)
        self.assertEqual(payload["hospital"]["road_eta_minutes"], 6.5)
        self.assertEqual(payload["hospital"]["hospital_id"], 1)
        self.assertIn("Occupied beds: 8", payload["hospital"]["selection_reason"])

        connection = get_database_connection()
        stored = connection.execute(
            """
            SELECT i.latitude, i.longitude, i.event_id, i.status, i.assignment_explanation,
                   a.ambulance_distance, a.ambulance_eta,
                   a.hospital_distance, a.hospital_eta, a.ambulance_id,
                   am.latitude AS ambulance_latitude,
                   am.longitude AS ambulance_longitude,
                   h.latitude AS hospital_latitude,
                   h.longitude AS hospital_longitude
            FROM incidents i
            JOIN assignments a ON a.incident_id = i.id
            JOIN ambulances am ON am.id = a.ambulance_id
            JOIN hospitals h ON h.id = a.hospital_id
            WHERE i.id = ?
            """,
            (incident_id,),
        ).fetchone()
        connection.close()
        self.assertEqual((stored["latitude"], stored["longitude"]), (19.1234, 72.9876))
        self.assertEqual(
            (stored["ambulance_latitude"], stored["ambulance_longitude"]),
            (19.0701, 72.8802),
        )
        self.assertEqual(
            (stored["hospital_latitude"], stored["hospital_longitude"]),
            (19.076, 72.8777),
        )
        self.assertEqual(stored["status"], "Assigned")
        self.assertEqual(stored["ambulance_id"], ambulance_id)
        self.assertEqual(stored["ambulance_distance"], 7.75)
        self.assertEqual(stored["ambulance_eta"], 11.5)
        self.assertEqual(stored["hospital_distance"], 4.25)
        self.assertEqual(stored["hospital_eta"], 6.5)
        self.assertIn("Deterministic score", stored["assignment_explanation"])

        response = self.client.get("/admin/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/phone").status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn(stored["event_id"], page)
        self.assertIn("Test phone", page)
        self.assertIn("19.1234", page)
        self.assertIn("72.9876", page)
        self.assertIn("GPS accuracy not supplied by device", page)
        self.assertIn("11.5 minutes", page)
        self.assertIn("6.5 minutes", page)
        self.assertIn("4.25 km", page)
        self.assertIn("7.75 km", page)
        self.assertIn("19.076", page)
        self.assertIn("72.8777", page)
        self.assertIn("19.0701", page)
        self.assertIn("72.8802", page)
        self.assertIn("Test hospital address", page)
        self.assertIn(
            "https://www.google.com/maps?q=19.1234,72.9876",
            page,
        )
        self.assertIn(
            '<dt>Occupied beds / current patients</dt><dd data-live-field="hospital_occupied_beds">8',
            page,
        )
        self.assertIn("Deterministic score", page)
        self.assertIn("Backend received / incident created", page)
        self.assertIn("Elapsed since received", page)
        self.assertIn("13 Oct 2026, 10:47:28 PM IST", page)
        self.assertIn("Assignment recorded:", page)
        self.assertIn("Numeric priority score", page)
        self.assertIn("Priority category", page)
        self.assertIn("7.50 / 10", page)
        self.assertIn("HIGH", page)
        self.assertIn("leaflet@1.9.4", page)
        self.assertIn("SIMULATED AMBULANCE MOVEMENT", page)
        self.assertIn(
            "https://www.google.com/maps?q=19.076,72.8777",
            page,
        )
        live_data = self.client.get("/admin/live/data")
        self.assertEqual(live_data.status_code, 200)
        live_item = next(
            row
            for row in live_data.get_json()["live_responses"]
            if row["incident_id"] == incident_id
        )
        self.assertEqual(
            live_item["map_payload"]["ambulance_route"],
            [[19.0701, 72.8802], [19.0896, 72.8656]],
        )
        self.assertEqual(
            live_item["map_payload"]["incident"]["coordinates"],
            [19.1234, 72.9876],
        )
        self.assertEqual(
            live_item["map_payload"]["hospital"]["coordinates"],
            [19.076, 72.8777],
        )
        self.assertEqual(
            live_item["assignment_timestamp_display"],
            self._display_assignment_timestamp(live_item["assignment_created_at"]),
        )
        self.assertTrue(live_item["assignment_timestamp_display"].endswith(" IST"))
        self.assertIn(str(incident_id), live_data.get_json()["new_cards_html"])
        known_cards = self.client.get(
            f"/admin/live/data?known={incident_id}"
        ).get_json()["new_cards_html"]
        self.assertNotIn(f'data-incident-id="{incident_id}"', known_cards)

    @staticmethod
    def _display_assignment_timestamp(value):
        return _format_local_timestamp(value, assume_utc=True)

    def test_timestamp_conversion_and_missing_values(self):
        self.assertEqual(
            _format_local_timestamp("2026-10-13T17:17:28+00:00"),
            "13 Oct 2026, 10:47:28 PM IST",
        )
        self.assertEqual(
            _format_local_timestamp("2026-10-13 17:17:28", assume_utc=True),
            "13 Oct 2026, 10:47:28 PM IST",
        )
        self.assertEqual(_format_local_timestamp(None), "Timestamp unavailable")
        self.assertEqual(_format_local_timestamp("not-a-date"), "Timestamp unavailable")
        self.assertEqual(
            _format_local_timestamp("2026-10-13 17:17:28"),
            "Timestamp unavailable",
        )

    @patch("ai.hospital_selector.get_route", return_value=_route())
    @patch("ai.ambulance_selector.get_route", return_value=_route())
    def test_retry_dispatch_is_explicit_and_idempotent(
        self, _ambulance_route, _hospital_route
    ):
        incident_id = self._incident()
        waiting = self.client.get("/admin/live").get_data(as_text=True)
        self.assertIn(
            "Retry dispatch",
            waiting[waiting.index(f"Incident #{incident_id}") :],
        )
        ambulance_id = self._make_available_ambulance_and_hospital()

        first = self.client.post(f"/admin/live/retry/{incident_id}")
        second = self.client.post(f"/admin/live/retry/{incident_id}")

        self.assertEqual(first.status_code, 201, first.get_json())
        self.assertEqual(second.status_code, 409)
        self.assertIn("already has assignment", second.get_json()["error"])
        connection = get_database_connection()
        rows = connection.execute(
            "SELECT COUNT(*) FROM assignments WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()[0]
        ambulance = connection.execute(
            "SELECT status, current_incident_id FROM ambulances WHERE id = ?",
            (ambulance_id,),
        ).fetchone()
        connection.close()
        self.assertEqual(rows, 1)
        self.assertEqual(ambulance["status"], "Assigned")
        self.assertEqual(ambulance["current_incident_id"], incident_id)

    @patch("ai.hospital_selector.get_route", return_value=_route())
    @patch("ai.ambulance_selector.get_route", return_value=_route())
    def test_concurrent_incidents_cannot_claim_the_same_ambulance(
        self, _ambulance_route, _hospital_route
    ):
        ambulance_id = self._make_available_ambulance_and_hospital()
        incident_ids = [self._incident(), self._incident(19.2, 72.8)]

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(create_assignment_record, incident_ids)
            )

        self.assertEqual(sorted(result[2] for result in results), [201, 503])
        connection = get_database_connection()
        active_claims = connection.execute(
            """
            SELECT COUNT(*) FROM assignments
            WHERE ambulance_id = ? AND lower(status) NOT IN ('completed', 'cancelled')
            """,
            (ambulance_id,),
        ).fetchone()[0]
        assigned_incidents = connection.execute(
            "SELECT incident_id FROM assignments WHERE incident_id IN (?, ?)",
            incident_ids,
        ).fetchall()
        connection.close()
        self.assertEqual(active_claims, 1)
        self.assertEqual(len(assigned_incidents), 1)

    def test_unavailable_ambulances_leave_incident_waiting_with_reason(self):
        incident_id = self._incident()

        payload, error, status = create_assignment_record(incident_id)

        self.assertIsNone(payload)
        self.assertEqual(status, 503)
        self.assertIn("no ambulance is currently marked Available", error)
        connection = get_database_connection()
        incident = connection.execute(
            "SELECT status, assignment_failure_reason FROM incidents WHERE id = ?",
            (incident_id,),
        ).fetchone()
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM assignments WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()[0],
            0,
        )
        connection.close()
        self.assertEqual(incident["status"], "Waiting")
        self.assertIn("no ambulance is currently marked Available", incident["assignment_failure_reason"])

        page = self.client.get("/admin/live").get_data(as_text=True)
        self.assertIn("No ambulance assigned", page)
        self.assertIn("Waiting for an available ambulance", page)
        self.assertIn("ETA unavailable — ambulance is not assigned", page)

    @patch("ai.ambulance_selector.get_route", return_value=_route())
    def test_no_eligible_hospital_keeps_incident_unassigned(self, _route_mock):
        self._make_available_ambulance_and_hospital()
        connection = get_database_connection()
        connection.execute("UPDATE hospitals SET status = 'Limited'")
        connection.commit()
        connection.close()
        incident_id = self._incident()

        payload, error, status = create_assignment_record(incident_id)

        self.assertIsNone(payload)
        self.assertEqual(status, 503)
        self.assertIn("no eligible hospital", error)
        self.assertIn("not operationally Available", error)
        connection = get_database_connection()
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM assignments WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute(
                "SELECT status FROM ambulances WHERE id = (SELECT id FROM ambulances ORDER BY id LIMIT 1)"
            ).fetchone()["status"],
            "Available",
        )
        connection.close()
        page = self.client.get("/admin/live").get_data(as_text=True)
        self.assertIn("Awaiting hospital placement: no eligible hospital", page)

    @patch("ai.ambulance_selector.get_route", return_value=None)
    def test_ambulance_route_failure_assigns_without_fabricated_distance_eta(
        self, _route_mock
    ):
        self._make_available_ambulance_and_hospital()
        incident_id = self._incident()

        payload, error, status = create_assignment_record(incident_id)

        self.assertIsNone(error)
        self.assertEqual(status, 201)
        self.assertIsNone(payload["ambulance"]["road_eta_minutes"])
        self.assertIsNone(payload["ambulance"]["distance_km"])
        self.assertIn("OSRM routing failed", payload["assignment_explanation"])
        connection = get_database_connection()
        stored = connection.execute(
            """
            SELECT ambulance_distance, ambulance_eta, ambulance_route_status,
                   ambulance_route_geometry
            FROM assignments WHERE incident_id = ?
            """,
            (incident_id,),
        ).fetchone()
        connection.close()
        self.assertIsNone(stored["ambulance_distance"])
        self.assertIsNone(stored["ambulance_eta"])
        self.assertEqual(stored["ambulance_route_status"], "unavailable")
        self.assertIsNone(stored["ambulance_route_geometry"])
        page = self.client.get("/admin/live").get_data(as_text=True)
        self.assertIn("Road route and ETA unavailable — OSRM routing failed", page)
        self.assertIn("Road distance unavailable — routing failed or was not recorded", page)
        self.assertIn("ETA unavailable — routing failed or was not recorded", page)

    @patch("ai.hospital_selector.get_route", return_value=None)
    @patch("ai.ambulance_selector.get_route", return_value=_route())
    def test_hospital_route_failure_assigns_eligible_resources_without_fake_eta(
        self, _ambulance_route, _hospital_route
    ):
        self._make_available_ambulance_and_hospital()
        incident_id = self._incident()

        payload, error, status = create_assignment_record(incident_id)

        self.assertIsNone(error)
        self.assertEqual(status, 201)
        self.assertEqual(payload["hospital"]["hospital_id"], 1)
        self.assertIsNone(payload["hospital"]["road_eta_minutes"])
        self.assertIn(
            "routing failed for every eligible option",
            payload["assignment_explanation"],
        )
        connection = get_database_connection()
        stored = connection.execute(
            """
            SELECT status, hospital_eta, hospital_distance,
                   hospital_route_status, hospital_route_geometry
            FROM assignments WHERE incident_id = ?
            """,
            (incident_id,),
        ).fetchone()
        connection.close()
        self.assertEqual(stored["status"], "Active")
        self.assertIsNone(stored["hospital_eta"])
        self.assertIsNone(stored["hospital_distance"])
        self.assertEqual(stored["hospital_route_status"], "unavailable")
        self.assertIsNone(stored["hospital_route_geometry"])
        page = self.client.get("/admin/live").get_data(as_text=True)
        self.assertIn("Road route and ETA unavailable — OSRM routing failed", page)
        self.assertIn("Road distance unavailable — routing failed or was not recorded", page)
        self.assertIn("ETA unavailable — routing failed or was not recorded", page)

    @patch("ai.hospital_selector.get_route", return_value=_route(3.0, 4.0))
    @patch("ai.ambulance_selector.get_route", return_value=_route(5.0, 7.0))
    def test_incident_assignment_details_do_not_leak_between_incidents(
        self, _ambulance_route, _hospital_route
    ):
        first_ambulance = self._make_available_ambulance_and_hospital()
        connection = get_database_connection()
        second = connection.execute(
            "SELECT id FROM ambulances WHERE id != ? ORDER BY id LIMIT 1",
            (first_ambulance,),
        ).fetchone()
        connection.execute(
            """
            UPDATE ambulances SET status = 'Available',
                latitude = 19.2101, longitude = 72.9902
            WHERE id = ?
            """,
            (second["id"],),
        )
        connection.commit()
        connection.close()

        incident_one = self._incident(19.1234, 72.9876)
        incident_two = self._incident(19.3456, 72.7654)
        self.assertEqual(create_assignment_record(incident_one)[2], 201)
        self.assertEqual(create_assignment_record(incident_two)[2], 201)

        connection = get_database_connection()
        rows = connection.execute(
            """
            SELECT i.id, i.event_id, i.latitude, i.longitude, a.ambulance_id,
                   a.ambulance_eta
            FROM incidents i JOIN assignments a ON a.incident_id = i.id
            WHERE i.id IN (?, ?)
            ORDER BY i.id
            """,
            (incident_one, incident_two),
        ).fetchall()
        connection.close()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["ambulance_id"], rows[1]["ambulance_id"])
        self.assertEqual((rows[0]["latitude"], rows[0]["longitude"]), (19.1234, 72.9876))
        self.assertEqual((rows[1]["latitude"], rows[1]["longitude"]), (19.3456, 72.7654))
        self.assertNotEqual(rows[0]["event_id"], rows[1]["event_id"])


if __name__ == "__main__":
    unittest.main()
