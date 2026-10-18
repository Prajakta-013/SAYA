"""
Tests for AI-Assisted Crash Detection (victim phone prototype).

Covers: normal movement, hard braking, minor/major impact scenarios,
cancel, GPS alert, phone alert → full emergency pipeline, and duplicates.
"""

import os
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app, get_database_connection  # noqa: E402
from ai.emergency_dispatcher import set_last_auto_response  # noqa: E402
from ai.priority_engine import calculate_priority  # noqa: E402
from mobile.crash_detector import (  # noqa: E402
    SensorFusionWindow,
    SensorSample,
    assess_crash,
    get_demo_scenario,
    simulate_crash_sensor_bundle,
    simulate_hard_braking_sample,
    simulate_minor_impact_sample,
    simulate_normal_driving_sample,
)
from mobile.location_service import LiveGPSLocation, get_phone_gps  # noqa: E402
from mobile import config as mobile_config  # noqa: E402
from mobile.main import (  # noqa: E402
    build_alert_payload,
    get_or_create_device_id,
    process_sensor_sample,
    run_countdown,
    run_safe_demo,
)


def _fake_route(start_lat, start_lon, end_lat, end_lon):
    distance = abs(start_lat - end_lat) + abs(start_lon - end_lon)
    return {
        "distance_km": round(max(distance * 100, 1.5), 2),
        "duration_minutes": round(max(distance * 40, 4.0), 2),
        "geometry": [[start_lon, start_lat], [end_lon, end_lat]],
    }


def _free_resources():
    connection = get_database_connection()
    connection.execute(
        """
        UPDATE ambulances
        SET status = 'Available', current_incident_id = NULL
        WHERE id IN (SELECT id FROM ambulances ORDER BY id LIMIT 2)
        """
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


class TestPhoneCrashDetection(unittest.TestCase):

    def setUp(self):
        set_last_auto_response(None)
        self.client = app.test_client()
        self.client.post(
            "/admin/login",
            data={"username": "admin", "password": "saya"},
        )
        _free_resources()

    def test_normal_movement_no_crash(self):
        assessment = assess_crash(simulate_normal_driving_sample())
        self.assertFalse(assessment.crash_detected)
        self.assertLess(assessment.crash_confidence, 0.55)

    def test_normal_driving_scenario_no_crash(self):
        demo = get_demo_scenario("NORMAL_DRIVING")
        assessment = assess_crash(demo["sample"])
        self.assertFalse(assessment.crash_detected)
        response = self.client.post(
            "/phone-crash-demo",
            json={"scenario": "NORMAL_DRIVING"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["emergency_status"], "NO_CRASH")
        self.assertIsNone(data.get("incident_id"))

    def test_hard_braking_no_emergency(self):
        assessment = assess_crash(simulate_hard_braking_sample())
        self.assertFalse(assessment.crash_detected)
        self.assertTrue(assessment.signals.get("speed_drop"))
        self.assertFalse(assessment.signals.get("impact"))
        response = self.client.post(
            "/phone-crash-demo",
            json={"scenario": "HARD_BRAKING"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["emergency_status"], "NO_CRASH")
        self.assertFalse(data["success"])

    def test_minor_impact_different_confidence_severity(self):
        minor = assess_crash(simulate_minor_impact_sample())
        major = assess_crash(simulate_crash_sensor_bundle())
        self.assertTrue(minor.crash_detected)
        self.assertTrue(major.crash_detected)
        self.assertLess(minor.crash_confidence, major.crash_confidence)
        self.assertLessEqual(minor.severity, major.severity)
        self.assertNotEqual(minor.severity, 5)  # not hard-coded max

    def test_single_sensor_does_not_trigger(self):
        sample = SensorSample(
            accel_magnitude=50.0,
            gyro_magnitude=0.1,
            previous_speed_mps=10.0,
            speed_mps=10.0,
            post_impact_idle=False,
        )
        assessment = assess_crash(sample)
        self.assertFalse(assessment.crash_detected)
        self.assertEqual(sum(1 for v in assessment.signals.values() if v), 1)

    def test_live_rotation_and_small_movement_do_not_trigger(self):
        rotation_window = SensorFusionWindow()
        for sample_index in range(8):
            sample = rotation_window.update(
                (0.0, 0.0, 9.8),
                (0.0, 0.0, 6.0),
                timestamp=sample_index * 0.2,
            )
        self.assertFalse(assess_crash(sample).crash_detected)

        movement_window = SensorFusionWindow()
        sample = movement_window.update(
            (0.0, 0.0, 15.0),
            (0.0, 0.0, 2.0),
            timestamp=0.0,
        )
        self.assertFalse(assess_crash(sample).crash_detected)

    def test_live_hard_braking_does_not_trigger_without_impact(self):
        window = SensorFusionWindow()
        window.update(
            (18.0, 0.0, 0.0), (0.0, 0.0, 0.4), speed_mps=24.0, timestamp=0.0
        )
        sample = window.update(
            (18.0, 0.0, 0.0), (0.0, 0.0, 0.4), speed_mps=6.0, timestamp=0.2
        )
        assessment = assess_crash(sample)
        self.assertTrue(assessment.signals["speed_drop"])
        self.assertFalse(assessment.signals["impact"])
        self.assertFalse(assessment.crash_detected)

    def test_live_impact_rotation_speed_and_post_impact_idle_detect(self):
        window = SensorFusionWindow()
        window.update(
            (55.0, 0.0, 0.0), (10.0, 0.0, 0.0), speed_mps=25.0, timestamp=0.0
        )
        for sample_index in range(1, 8):
            sample = window.update(
                (0.0, 0.0, 9.8),
                (0.0, 0.0, 0.0),
                speed_mps=0.0,
                timestamp=sample_index * 0.2,
            )
        assessment = assess_crash(sample)
        self.assertTrue(assessment.crash_detected)
        self.assertTrue(assessment.signals["impact"])
        self.assertTrue(assessment.signals["rotation"])
        self.assertTrue(assessment.signals["speed_drop"])
        self.assertTrue(assessment.signals["inactivity"])

    def test_live_gps_requires_a_real_fix_and_includes_available_speed(self):
        location = LiveGPSLocation()
        self.assertIsNone(location.latest_fix())
        location._on_location(lat=19.123, lon=72.456, speed=4.5)
        fix = location.latest_fix()
        self.assertEqual(fix["latitude"], 19.123)
        self.assertEqual(fix["longitude"], 72.456)
        self.assertEqual(fix["speed"], 4.5)

    def test_device_id_persists_in_phone_app_storage(self):
        with tempfile.TemporaryDirectory() as storage_directory:
            first = get_or_create_device_id(storage_directory)
            second = get_or_create_device_id(storage_directory)
        self.assertEqual(first, second)

    def test_combined_crash_signals_detect_crash(self):
        assessment = assess_crash(simulate_crash_sensor_bundle())
        self.assertTrue(assessment.crash_detected)
        self.assertGreaterEqual(sum(1 for v in assessment.signals.values() if v), 2)
        self.assertGreaterEqual(assessment.crash_confidence, 0.55)
        self.assertGreaterEqual(assessment.severity, 2)
        self.assertLessEqual(assessment.severity, 5)
        self.assertEqual(assessment.detection_source, "Victim Smartphone")

    def test_cancel_sends_no_emergency(self):
        result = process_sensor_sample(
            simulate_crash_sensor_bundle(),
            cancelled=True,
            send_alert=False,
            skip_countdown=True,
        )
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["alert_sent"])
        self.assertEqual(result["phase"], "CANCELLED")

    def test_countdown_can_be_cancelled_before_alert(self):
        elapsed = {"seconds": 0}

        def sleep_one_second(_seconds):
            elapsed["seconds"] += 1

        cancelled = run_countdown(
            seconds=10,
            cancelled_check=lambda: elapsed["seconds"] >= 3,
            sleep_fn=sleep_one_second,
        )
        self.assertTrue(cancelled)
        self.assertEqual(elapsed["seconds"], 3)

    def test_confirmed_crash_obtains_gps_and_builds_alert(self):
        assessment = assess_crash(simulate_crash_sensor_bundle())
        gps = get_phone_gps(get_demo_scenario("MAJOR_CRASH")["gps"])
        payload = build_alert_payload(assessment, gps, speed_mps=1.5)
        self.assertIn("latitude", payload)
        self.assertIn("longitude", payload)
        self.assertEqual(payload["incident_type"], "Road Accident")
        self.assertEqual(payload["detection_source"], "Victim Smartphone")
        self.assertIn("crash_confidence", payload)
        self.assertIn("device_id", payload)
        self.assertIn("timestamp", payload)

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_major_crash_triggers_emergency_dispatch(self, *_mocks):
        response = self.client.post(
            "/phone-crash-demo",
            json={"scenario": "MAJOR_CRASH"},
        )
        self.assertEqual(response.status_code, 201, msg=response.get_json())
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["scenario"], "MAJOR_CRASH")
        self.assertEqual(data["emergency_status"], "AUTO_DISPATCHED")
        self.assertIsNotNone(data["incident_id"])
        self.assertIsNotNone(data["ambulance"])
        self.assertIsNotNone(data["hospital"])

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_minor_impact_demo_dispatches_with_lower_severity(self, *_mocks):
        minor_resp = self.client.post(
            "/phone-crash-demo",
            json={"scenario": "MINOR_IMPACT"},
        )
        self.assertEqual(minor_resp.status_code, 201, msg=minor_resp.get_json())
        minor = minor_resp.get_json()
        major_resp = self.client.post(
            "/phone-crash-demo",
            json={"scenario": "MAJOR_CRASH"},
        )
        major = major_resp.get_json()
        self.assertTrue(minor["success"])
        self.assertTrue(major["success"])
        self.assertLess(
            minor["assessment"]["crash_confidence"],
            major["assessment"]["crash_confidence"],
        )
        self.assertNotEqual(minor["location"], major["location"])
        self.assertLessEqual(minor["severity"], major["severity"])

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_phone_alert_creates_full_pipeline(self, *_mocks):
        assessment = assess_crash(simulate_crash_sensor_bundle())
        gps = get_phone_gps()
        payload = build_alert_payload(
            assessment,
            gps,
            device_id=f"TEST-PHONE-{uuid.uuid4().hex[:8]}",
            speed_mps=1.5,
        )

        response = self.client.post("/phone-crash-alert", json=payload)
        self.assertEqual(response.status_code, 201, msg=response.get_json())
        data = response.get_json()

        self.assertTrue(data["success"])
        self.assertIsNotNone(data["incident_id"])
        expected_score, expected_priority = calculate_priority(
            payload["severity"], 1, "Road Accident"
        )
        self.assertEqual(data["priority_score"], expected_score)
        self.assertEqual(data["priority"], expected_priority)
        self.assertIsNotNone(data["ambulance"])
        self.assertIsNotNone(data["hospital"])
        self.assertIsNotNone(data["ambulance_eta"])
        self.assertIsNotNone(data["hospital_eta"])
        self.assertEqual(data["detection_source"], "Victim Smartphone")
        self.assertEqual(data["emergency_status"], "AUTO_DISPATCHED")

        connection = get_database_connection()
        incident = connection.execute(
            "SELECT * FROM incidents WHERE id = ?",
            (data["incident_id"],),
        ).fetchone()
        assignment = connection.execute(
            "SELECT * FROM assignments WHERE incident_id = ?",
            (data["incident_id"],),
        ).fetchone()
        alert = connection.execute(
            "SELECT * FROM phone_crash_alerts WHERE incident_id = ?",
            (data["incident_id"],),
        ).fetchone()
        connection.close()
        self.assertIsNotNone(incident)
        self.assertEqual(incident["status"], "Assigned")
        self.assertIsNotNone(assignment)
        self.assertEqual(alert["event_id"], payload["event_id"])
        self.assertEqual(alert["speed"], payload["speed"])

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_two_independent_phone_events_get_distinct_assignments_and_views(self, *_mocks):
        connection = get_database_connection()
        connection.execute(
            "UPDATE ambulances SET status = 'Available', current_incident_id = NULL"
        )
        connection.execute(
            "UPDATE hospitals SET status = 'Available', available_beds = 50, emergency_capacity = 40"
        )
        connection.commit()
        connection.close()

        samples = [
            ("PHONE-EAST", "EVENT-EAST", simulate_minor_impact_sample(), 19.1176, 72.9060),
            ("PHONE-WEST", "EVENT-WEST", simulate_crash_sensor_bundle(), 19.0596, 72.8295),
        ]
        responses = []
        for device_id, event_id, sample, latitude, longitude in samples:
            assessment = assess_crash(sample)
            payload = build_alert_payload(
                assessment,
                {"latitude": latitude, "longitude": longitude, "location": f"Test location {device_id}"},
                device_id=device_id,
                event_id=event_id,
                speed_mps=sample.speed_mps,
            )
            responses.append(self.client.post("/phone-crash-alert", json=payload))

        self.assertEqual([response.status_code for response in responses], [201, 201])
        events = [response.get_json() for response in responses]
        self.assertNotEqual(events[0]["incident_id"], events[1]["incident_id"])
        self.assertNotEqual(events[0]["assignment"]["ambulance"]["ambulance_id"], events[1]["assignment"]["ambulance"]["ambulance_id"])
        self.assertNotEqual(events[0]["crash_confidence"], events[1]["crash_confidence"])
        self.assertNotEqual(events[0]["severity"], events[1]["severity"])

        connection = get_database_connection()
        saved = connection.execute(
            "SELECT event_id, location FROM incidents WHERE id IN (?, ?)",
            (events[0]["incident_id"], events[1]["incident_id"]),
        ).fetchall()
        connection.close()
        self.assertEqual({row["event_id"] for row in saved}, {"EVENT-EAST", "EVENT-WEST"})
        self.assertEqual({row["location"] for row in saved}, {"Test location PHONE-EAST", "Test location PHONE-WEST"})

        self.client.post("/admin/login", data={"username": "admin", "password": "saya"})
        live_page = self.client.get("/admin/live").get_data(as_text=True)
        self.assertIn("EVENT-EAST", live_page)
        self.assertIn("EVENT-WEST", live_page)
        self.assertIn("HIGH", live_page)
        self.assertIn("Original event time", live_page)
        self.assertIn("Backend received / incident created", live_page)
        self.assertIn("Run safe demo incident", live_page)
        self.assertIn("19.0596", live_page)
        self.assertIn("72.8295", live_page)
        self.assertIn("https://www.google.com/maps?q=19.0596,72.8295", live_page)

        selected_hospital_id = events[0]["assignment"]["hospital"]["hospital_id"]
        connection = get_database_connection()
        hospitals = connection.execute(
            "SELECT id, username FROM hospitals ORDER BY id"
        ).fetchall()
        selected_hospital = next(row for row in hospitals if row["id"] == selected_hospital_id)
        other_hospital = next(row for row in hospitals if row["id"] != selected_hospital_id)
        connection.close()

        self.client.get("/hospital/logout")
        selected_password = (
            "safe-test-password"
            if selected_hospital["username"].startswith("address-test-")
            else "saya"
        )
        self.client.post(
            "/hospital/login",
            data={
                "username": selected_hospital["username"],
                "password": selected_password,
            },
        )
        own_page = self.client.get("/hospital/incoming").get_data(as_text=True)
        self.assertIn("EVENT-EAST", own_page)

        self.client.get("/hospital/logout")
        self.client.post(
            "/hospital/login",
            data={"username": other_hospital["username"], "password": "saya"},
        )
        other_page = self.client.get("/hospital/incoming").get_data(as_text=True)
        self.assertNotIn("EVENT-EAST", other_page)

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_ambulance_shortage_keeps_incident_waiting(self, *_mocks):
        connection = get_database_connection()
        connection.execute(
            "UPDATE ambulances SET status = 'Busy', current_incident_id = 999999"
        )
        connection.execute(
            "UPDATE hospitals SET status = 'Available', available_beds = 50, emergency_capacity = 40"
        )
        connection.commit()
        connection.close()

        assessment = assess_crash(simulate_crash_sensor_bundle())
        event_id = str(uuid.uuid4())
        payload = build_alert_payload(
            assessment,
            get_phone_gps(),
            device_id=f"SHORTAGE-{uuid.uuid4().hex}",
            event_id=event_id,
        )
        response = self.client.post("/phone-crash-alert", json=payload)
        self.assertEqual(response.status_code, 202, msg=response.get_json())
        result = response.get_json()
        self.assertEqual(result["emergency_status"], "WAITING_FOR_RESOURCES")

        connection = get_database_connection()
        incident = connection.execute(
            "SELECT status FROM incidents WHERE id = ?", (result["incident_id"],)
        ).fetchone()
        assignment = connection.execute(
            "SELECT id FROM assignments WHERE incident_id = ?", (result["incident_id"],)
        ).fetchone()
        busy_count = connection.execute(
            "SELECT COUNT(*) FROM ambulances WHERE status = 'Busy'"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(incident["status"], "Waiting")
        self.assertIsNone(assignment)
        self.assertEqual(busy_count, 5)

        self.client.post(
            "/admin/login", data={"username": "admin", "password": "saya"}
        )
        live_page = self.client.get("/admin/live").get_data(as_text=True)
        self.assertIn(event_id, live_page)
        self.assertIn("No ambulance assigned", live_page)

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_hospital_shortage_keeps_incident_waiting(self, *_mocks):
        connection = get_database_connection()
        connection.execute(
            "UPDATE ambulances SET status = 'Available', current_incident_id = NULL"
        )
        connection.execute(
            "UPDATE hospitals SET status = 'Unavailable', available_beds = 0, emergency_capacity = 0"
        )
        connection.commit()
        connection.close()

        payload = build_alert_payload(
            assess_crash(simulate_crash_sensor_bundle()),
            get_phone_gps(),
            device_id=f"HOSPITAL-SHORTAGE-{uuid.uuid4().hex}",
            event_id=str(uuid.uuid4()),
        )
        response = self.client.post("/phone-crash-alert", json=payload)
        self.assertEqual(response.status_code, 202, msg=response.get_json())
        result = response.get_json()
        self.assertEqual(result["emergency_status"], "WAITING_FOR_RESOURCES")

        connection = get_database_connection()
        incident = connection.execute(
            "SELECT status FROM incidents WHERE id = ?", (result["incident_id"],)
        ).fetchone()
        connection.close()
        self.assertEqual(incident["status"], "Waiting")

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_hospital_resource_update_changes_next_selection(self, *_mocks):
        connection = get_database_connection()
        connection.execute(
            "UPDATE ambulances SET status = 'Available', current_incident_id = NULL"
        )
        connection.execute(
            "UPDATE hospitals SET status = 'Available', available_beds = 50, emergency_capacity = 40"
        )
        connection.commit()
        connection.close()

        def send_event(device_id, event_id):
            payload = build_alert_payload(
                assess_crash(simulate_crash_sensor_bundle()),
                get_phone_gps(),
                device_id=device_id,
                event_id=event_id,
            )
            return self.client.post("/phone-crash-alert", json=payload).get_json()

        first = send_event(f"RESOURCE-A-{uuid.uuid4().hex}", str(uuid.uuid4()))
        first_hospital_id = first["assignment"]["hospital"]["hospital_id"]

        connection = get_database_connection()
        hospital = connection.execute(
            "SELECT username FROM hospitals WHERE id = ?", (first_hospital_id,)
        ).fetchone()
        connection.close()
        self.client.post(
            "/hospital/login",
            data={"username": hospital["username"], "password": "saya"},
        )
        update = self.client.post(
            "/hospital/resources/update",
            data={
                "available_beds": "0",
                "occupied_beds": "50",
                "emergency_capacity": "0",
                "status": "Unavailable",
            },
        )
        self.assertEqual(update.status_code, 302)
        self.client.get("/hospital/logout")

        second = send_event(f"RESOURCE-B-{uuid.uuid4().hex}", str(uuid.uuid4()))
        self.assertEqual(second["emergency_status"], "AUTO_DISPATCHED")
        self.assertNotEqual(
            first_hospital_id,
            second["assignment"]["hospital"]["hospital_id"],
        )

    @patch("app.geocode_location", return_value=(19.1234, 72.9876))
    def test_hospital_registration_uses_address_without_coordinate_fields(self, _geocode):
        login = self.client.post(
            "/admin/login", data={"username": "admin", "password": "saya"}
        )
        self.assertEqual(login.status_code, 302)
        form = self.client.get("/admin/hospitals").get_data(as_text=True)
        self.assertNotIn('name="latitude"', form)
        self.assertNotIn('name="longitude"', form)

        response = self.client.post(
            "/admin/hospitals/add",
            data={
                "name": "Address Test Hospital",
                "location": "123 Example Road, Mumbai",
                "username": f"address-test-{uuid.uuid4().hex[:8]}",
                "password": "safe-test-password",
                "total_beds": "40",
                "available_beds": "28",
                "occupied_beds": "12",
                "emergency_capacity": "8",
                "status": "Available",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(_geocode.called)

        connection = get_database_connection()
        hospital = connection.execute(
            "SELECT * FROM hospitals WHERE name = 'Address Test Hospital'"
        ).fetchone()
        connection.close()
        self.assertEqual(hospital["latitude"], 19.1234)
        self.assertEqual(hospital["longitude"], 72.9876)
        self.assertEqual(hospital["location"], "123 Example Road, Mumbai")

    def test_existing_admin_and_hospital_logins_still_work(self):
        admin_response = self.client.post(
            "/admin/login", data={"username": "admin", "password": "saya"}
        )
        self.assertEqual(admin_response.status_code, 302)

        connection = get_database_connection()
        usernames = [
            row["username"]
            for row in connection.execute("SELECT username FROM hospitals").fetchall()
        ]
        connection.close()
        self.client.get("/admin/logout")
        for username in usernames:
            response = self.client.post(
                "/hospital/login", data={"username": username, "password": "saya"}
            )
            self.assertEqual(response.status_code, 302, msg=username)
            self.client.get("/hospital/logout")

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_duplicate_alert_blocked(self, *_mocks):
        assessment = assess_crash(simulate_crash_sensor_bundle())
        gps = get_phone_gps()
        event_id = str(uuid.uuid4())
        device_id = f"TEST-DUP-{uuid.uuid4().hex[:8]}"
        payload = build_alert_payload(
            assessment,
            gps,
            device_id=device_id,
            event_id=event_id,
            speed_mps=1.5,
        )

        first = self.client.post("/phone-crash-alert", json=payload)
        self.assertEqual(first.status_code, 201, msg=first.get_json())

        second = self.client.post("/phone-crash-alert", json=payload)
        self.assertEqual(second.status_code, 409)
        body = second.get_json()
        self.assertEqual(body["emergency_status"], "DUPLICATE_BLOCKED")
        self.assertEqual(body["incident_id"], first.get_json()["incident_id"])

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_repeated_demo_clicks_are_separate_events(self, *_mocks):
        first = self.client.post(
            "/phone-crash-demo",
            json={"scenario": "MAJOR_CRASH"},
        )
        second = self.client.post(
            "/phone-crash-demo",
            json={"scenario": "MAJOR_CRASH"},
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(
            first.get_json()["incident_id"],
            second.get_json()["incident_id"],
        )

    @patch("ai.ambulance_selector.get_route", side_effect=_fake_route)
    @patch("ai.hospital_selector.get_route", side_effect=_fake_route)
    @patch("app.get_route", side_effect=_fake_route)
    def test_safe_demo_goes_through_phone_crash_alert(self, *_mocks):
        response = self.client.post(
            "/phone-crash-demo",
            json={"scenario": "MAJOR_CRASH"},
        )
        self.assertEqual(response.status_code, 201, msg=response.get_json())
        data = response.get_json()
        self.assertTrue(data.get("demo"))
        self.assertTrue(data.get("success"))
        self.assertTrue(data["assessment"]["crash_detected"])
        self.assertIsNotNone(data.get("incident_id"))
        self.assertIsNotNone(data.get("assignment") or data.get("ambulance"))

    @patch("mobile.api_client.requests.post")
    def test_run_safe_demo_uses_api_client(self, mock_post):
        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {
            "success": True,
            "emergency_status": "AUTO_DISPATCHED",
            "ambulance": "AMB-001",
            "hospital": "Test Hospital",
            "message": "ok",
        }
        result = run_safe_demo(
            scenario="MAJOR_CRASH",
            cancel=False,
            send_alert=True,
            skip_countdown=True,
        )
        self.assertTrue(result["assessment"]["crash_detected"])
        self.assertTrue(result["alert_sent"])
        self.assertTrue(mock_post.called)
        args, kwargs = mock_post.call_args
        self.assertIn("/phone-crash-alert", args[0])

    @patch("mobile.api_client.requests.post", side_effect=__import__("requests").Timeout)
    def test_phone_api_client_returns_actionable_connection_failure(self, _mock_post):
        from mobile.api_client import send_phone_crash_alert

        result = send_phone_crash_alert({"event_id": "network-test"})
        self.assertTrue(result["connection_error"])
        self.assertIn("Unable to contact SAYA", result["message"])
        self.assertIn("same network", result["message"])

    def test_android_backend_default_uses_configured_lan_address(self):
        self.assertTrue(mobile_config.API_BASE_URL.startswith("http://"))
        self.assertNotIn("localhost", mobile_config.API_BASE_URL)
        self.assertNotIn("127.0.0.1", mobile_config.API_BASE_URL)

    @patch("mobile.api_client.requests.post")
    def test_waiting_response_counts_as_accepted_phone_alert(self, mock_post):
        mock_post.return_value.status_code = 202
        mock_post.return_value.json.return_value = {
            "success": False,
            "emergency_status": "WAITING_FOR_RESOURCES",
            "message": "Incident remains active and unassigned.",
        }
        result = process_sensor_sample(
            simulate_crash_sensor_bundle(),
            auto_confirm=True,
            skip_countdown=True,
            base_url="http://saya.test",
        )
        self.assertTrue(result["alert_sent"])
        self.assertEqual(result["phase"], "DISPATCHED")

    @patch("mobile.api_client.requests.post")
    def test_duplicate_acknowledgement_counts_as_delivered(self, mock_post):
        mock_post.return_value.status_code = 409
        mock_post.return_value.json.return_value = {
            "success": True,
            "already_received": True,
            "incident_id": 123,
            "emergency_status": "DUPLICATE_BLOCKED",
        }
        result = process_sensor_sample(
            simulate_crash_sensor_bundle(),
            auto_confirm=True,
            skip_countdown=True,
            base_url="http://saya.test",
        )
        self.assertTrue(result["alert_sent"])
        self.assertEqual(result["phase"], "DISPATCHED")

    def test_sensitive_json_apis_require_admin_but_phone_alert_remains_public(self):
        client = app.test_client()
        self.assertEqual(client.get("/phone").status_code, 200)
        self.assertEqual(client.get("/admin/live").status_code, 302)
        self.assertEqual(client.get("/hospital/incoming").status_code, 302)
        for path in ("/incidents", "/ambulances", "/hospitals", "/assignments"):
            response = client.get(path)
            self.assertEqual(response.status_code, 401, msg=path)
            self.assertEqual(response.get_json(), {"error": "Admin authentication required"})

        response = client.post("/incidents", json={})
        self.assertEqual(response.status_code, 401)
        response = client.post(
            "/create-emergency",
            data={"incident_type": "Road Accident", "location": "Test"},
        )
        self.assertEqual(response.status_code, 302)
        response = client.post("/phone-crash-alert", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "JSON body required")

    def test_incident_api_rejects_invalid_coordinates_and_accepts_valid_payload(self):
        response = self.client.post(
            "/incidents",
            json={
                "incident_type": "Road Accident",
                "location": "Controlled test",
                "latitude": 91,
                "longitude": 72,
                "severity": 3,
                "number_of_patients": 1,
            },
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            "/incidents",
            json={
                "incident_type": "Road Accident",
                "location": "Controlled test",
                "latitude": 19.1,
                "longitude": 72.9,
                "severity": 3,
                "number_of_patients": 1,
            },
        )
        self.assertEqual(response.status_code, 201)
        incident_id = response.get_json()["incident_id"]
        connection = get_database_connection()
        saved = connection.execute(
            "SELECT latitude, longitude, status FROM incidents WHERE id = ?",
            (incident_id,),
        ).fetchone()
        connection.close()
        self.assertEqual((saved["latitude"], saved["longitude"]), (19.1, 72.9))
        self.assertEqual(saved["status"], "Pending")


if __name__ == "__main__":
    unittest.main()
