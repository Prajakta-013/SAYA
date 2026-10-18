from functools import wraps
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import secrets

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
import sqlite3
import requests
from werkzeug.security import check_password_hash, generate_password_hash

from ai.priority_engine import calculate_priority
from ai.ambulance_selector import (
    rank_available_ambulances,
    select_best_ambulance,
)
from ai.hospital_selector import rank_hospitals, select_best_hospital
from ai.routing import get_route
from ai.emergency_dispatcher import (
    dispatch_detected_accident,
    dispatch_phone_crash_alert,
    dispatch_simulated_accident,
    get_last_auto_response,
)
from mobile import config as mobile_config
from mobile.crash_detector import assess_crash, get_demo_scenario, list_demo_scenarios
from mobile.location_service import get_phone_gps
from mobile.main import build_alert_payload


app = Flask(__name__)
app.secret_key = os.environ.get("SAYA_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SAYA_COOKIE_SECURE") == "1",
)

DATABASE = os.environ.get(
    "SAYA_DATABASE_PATH",
    str(Path(__file__).resolve().parent / "lifeline.db"),
)
HOSPITAL_PASSWORD = os.environ.get("SAYA_HOSPITAL_BOOTSTRAP_PASSWORD")
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = os.environ.get("SAYA_ADMIN_PASSWORD")

HOSPITAL_USERNAME_HINTS = {
    "Lifeline General Hospital": "lifeline",
    "City Care Hospital": "citycare",
    "Metro Emergency Hospital": "metro",
    "Sunrise Medical Center": "sunrise",
    "Hope Multispeciality Hospital": "hope",
}


def get_database_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def _parse_integer(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not an integer value.")
    number = float(value)
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError("Expected a finite whole number.")
    return int(number)


def geocode_location(location):
    response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": location, "format": "jsonv2", "limit": 1},
        headers={"User-Agent": "SAYA-Emergency-Response/1.0"},
        timeout=8,
    )
    response.raise_for_status()
    matches = response.json()
    if not matches:
        return None
    return float(matches[0]["lat"]), float(matches[0]["lon"])


def _table_columns(connection, table_name):
    return [
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    ]


def _add_column_if_missing(connection, table_name, column_name, definition):
    if column_name not in _table_columns(connection, table_name):
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )


def _ensure_schema():
    connection = get_database_connection()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS phone_crash_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            event_id TEXT,
            alert_timestamp TEXT,
            incident_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _add_column_if_missing(connection, "phone_crash_alerts", "crash_confidence", "REAL")
    _add_column_if_missing(connection, "phone_crash_alerts", "speed", "REAL")

    _add_column_if_missing(
        connection, "assignments", "hospital_intake_status", "TEXT DEFAULT 'Received'"
    )
    _add_column_if_missing(connection, "assignments", "created_at", "TEXT")
    _add_column_if_missing(
        connection, "assignments", "ambulance_route_geometry", "TEXT"
    )
    _add_column_if_missing(
        connection, "assignments", "hospital_route_geometry", "TEXT"
    )
    _add_column_if_missing(connection, "assignments", "ambulance_route_status", "TEXT")
    _add_column_if_missing(connection, "assignments", "hospital_route_status", "TEXT")

    _add_column_if_missing(connection, "incidents", "detection_source", "TEXT")
    _add_column_if_missing(connection, "incidents", "crash_confidence", "REAL")
    _add_column_if_missing(connection, "incidents", "event_id", "TEXT")
    _add_column_if_missing(connection, "incidents", "detection_timestamp", "TEXT")
    _add_column_if_missing(connection, "incidents", "gps_accuracy", "REAL")
    _add_column_if_missing(connection, "incidents", "speed", "REAL")
    _add_column_if_missing(connection, "incidents", "assignment_failure_reason", "TEXT")
    _add_column_if_missing(connection, "incidents", "assignment_explanation", "TEXT")
    _add_column_if_missing(connection, "phone_crash_alerts", "gps_accuracy", "REAL")
    _add_column_if_missing(connection, "phone_crash_alerts", "severity", "INTEGER")
    _add_column_if_missing(connection, "phone_crash_alerts", "detection_source", "TEXT")

    _add_column_if_missing(connection, "hospitals", "username", "TEXT")
    _add_column_if_missing(connection, "hospitals", "password_hash", "TEXT")
    _add_column_if_missing(connection, "hospitals", "occupied_beds", "INTEGER")
    _add_column_if_missing(connection, "ambulances", "location", "TEXT")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
        """
    )

    if connection.execute("SELECT COUNT(*) AS c FROM admins").fetchone()["c"] == 0:
        if not ADMIN_PASSWORD:
            connection.close()
            raise RuntimeError(
                "SAYA_ADMIN_PASSWORD is required to create the first admin account."
            )
        connection.execute(
            "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
            (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD)),
        )

    hospitals = connection.execute("SELECT id, name, username, password_hash, total_beds, available_beds, occupied_beds FROM hospitals").fetchall()
    used_usernames = {
        row["username"].lower()
        for row in hospitals
        if row["username"]
    }
    for hospital in hospitals:
        updates = []
        values = []
        username = hospital["username"]
        if not username:
            hinted = HOSPITAL_USERNAME_HINTS.get(hospital["name"])
            if not hinted:
                hinted = "".join(
                    ch for ch in hospital["name"].split()[0].lower() if ch.isalnum()
                ) or f"hospital{hospital['id']}"
            base = hinted
            suffix = 1
            while hinted.lower() in used_usernames:
                suffix += 1
                hinted = f"{base}{suffix}"
            username = hinted
            used_usernames.add(username.lower())
            updates.append("username = ?")
            values.append(username)
        if not hospital["password_hash"]:
            if not HOSPITAL_PASSWORD:
                connection.close()
                raise RuntimeError(
                    "SAYA_HOSPITAL_BOOTSTRAP_PASSWORD is required to set "
                    "a password for an unconfigured hospital."
                )
            updates.append("password_hash = ?")
            values.append(generate_password_hash(HOSPITAL_PASSWORD))
        if hospital["occupied_beds"] is None:
            total = hospital["total_beds"] or 0
            available = hospital["available_beds"] or 0
            updates.append("occupied_beds = ?")
            values.append(max(0, total - available))
        if updates:
            values.append(hospital["id"])
            connection.execute(
                f"UPDATE hospitals SET {', '.join(updates)} WHERE id = ?",
                values,
            )

    connection.commit()
    connection.close()


def fetch_dashboard_data():
    _ensure_schema()

    connection = get_database_connection()

    incidents = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM incidents ORDER BY created_at DESC"
        ).fetchall()
    ]

    ambulances = [
        dict(row)
        for row in connection.execute("SELECT * FROM ambulances").fetchall()
    ]

    hospitals = [
        dict(row)
        for row in connection.execute("SELECT * FROM hospitals").fetchall()
    ]

    assignments = [
        dict(row)
        for row in connection.execute(
            """
            SELECT
                assignments.*,
                ambulances.ambulance_number,
                ambulances.ambulance_type,
                ambulances.latitude AS ambulance_latitude,
                ambulances.longitude AS ambulance_longitude,
                ambulances.status AS ambulance_status,
                hospitals.name AS hospital_name,
                hospitals.available_beds,
                hospitals.total_beds,
                hospitals.emergency_capacity,
                hospitals.latitude AS hospital_latitude,
                hospitals.longitude AS hospital_longitude,
                incidents.incident_type,
                incidents.location AS incident_location,
                incidents.severity,
                incidents.priority_score,
                incidents.status AS incident_status,
                incidents.latitude AS incident_latitude,
                incidents.longitude AS incident_longitude,
                incidents.detection_source AS incident_detection_source,
                incidents.crash_confidence AS incident_crash_confidence,
                incidents.event_id AS incident_event_id,
                incidents.gps_accuracy AS incident_gps_accuracy,
                phone_crash_alerts.id AS phone_alert_id,
                phone_crash_alerts.event_id AS phone_event_id,
                phone_crash_alerts.crash_confidence AS alert_crash_confidence
            FROM assignments
            JOIN ambulances ON assignments.ambulance_id = ambulances.id
            JOIN hospitals ON assignments.hospital_id = hospitals.id
            JOIN incidents ON assignments.incident_id = incidents.id
            LEFT JOIN phone_crash_alerts
                ON phone_crash_alerts.id = (
                    SELECT MAX(p2.id)
                    FROM phone_crash_alerts p2
                    WHERE p2.incident_id = incidents.id
                )
            ORDER BY assignments.id DESC
            """
        ).fetchall()
    ]

    live_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT
                incidents.id AS incident_id,
                incidents.incident_type,
                incidents.location AS incident_location,
                incidents.severity,
                incidents.priority_score,
                incidents.status AS incident_status,
                incidents.latitude AS incident_latitude,
                incidents.longitude AS incident_longitude,
                incidents.number_of_patients,
                incidents.detection_source AS incident_detection_source,
                incidents.crash_confidence AS incident_crash_confidence,
                incidents.event_id AS incident_event_id,
                incidents.gps_accuracy AS incident_gps_accuracy,
                incidents.detection_timestamp AS incident_detection_timestamp,
                incidents.assignment_failure_reason,
                incidents.assignment_explanation,
                incidents.created_at,
                assignments.id AS id,
                assignments.ambulance_id,
                assignments.hospital_id,
                assignments.ambulance_distance,
                assignments.ambulance_eta,
                assignments.hospital_distance,
                assignments.hospital_eta,
                assignments.created_at AS assignment_created_at,
                assignments.ambulance_route_geometry,
                assignments.hospital_route_geometry,
                assignments.ambulance_route_status,
                assignments.hospital_route_status,
                assignments.status AS assignment_status,
                assignments.hospital_intake_status,
                ambulances.latitude AS ambulance_latitude,
                ambulances.longitude AS ambulance_longitude,
                ambulances.ambulance_number,
                ambulances.ambulance_type,
                ambulances.status AS ambulance_status,
                hospitals.name AS hospital_name,
                hospitals.location AS hospital_location,
                hospitals.latitude AS hospital_latitude,
                hospitals.longitude AS hospital_longitude,
                hospitals.status AS hospital_status,
                hospitals.total_beds AS hospital_total_beds,
                hospitals.available_beds AS hospital_available_beds,
                hospitals.occupied_beds AS hospital_occupied_beds,
                hospitals.emergency_capacity AS hospital_emergency_capacity,
                phone_crash_alerts.id AS phone_alert_id,
                phone_crash_alerts.event_id AS phone_event_id,
                phone_crash_alerts.crash_confidence AS alert_crash_confidence,
                phone_crash_alerts.alert_timestamp AS phone_event_timestamp,
                phone_crash_alerts.created_at AS alert_received_at
            FROM incidents
            LEFT JOIN assignments
                ON assignments.id = (
                    SELECT MAX(a2.id)
                    FROM assignments a2
                    WHERE a2.incident_id = incidents.id
                )
            LEFT JOIN ambulances ON assignments.ambulance_id = ambulances.id
            LEFT JOIN hospitals ON assignments.hospital_id = hospitals.id
            LEFT JOIN phone_crash_alerts
                ON phone_crash_alerts.id = (
                    SELECT MAX(p2.id)
                    FROM phone_crash_alerts p2
                    WHERE p2.incident_id = incidents.id
                )
            ORDER BY incidents.id DESC
            """
        ).fetchall()
    ]

    connection.close()

    available_ambulances = sum(
        1 for ambulance in ambulances if ambulance["status"] == "Available"
    )
    available_hospitals = sum(
        1 for hospital in hospitals if hospital["status"] == "Available"
    )

    latest_assignment = assignments[0] if assignments else None
    latest_incident = incidents[0] if incidents else None
    auto_detection = get_last_auto_response()
    live_responses = _enrich_live_responses(live_rows, auto_detection, ambulances)

    assigned_ids = {row["incident_id"] for row in assignments}
    pending_incidents = [
        incident for incident in incidents
        if incident["id"] not in assigned_ids
    ]

    active_incident_count = sum(
        1 for incident in incidents
        if incident.get("status") not in ("Completed", "Cancelled")
    )
    active_assignment_count = sum(
        1 for row in assignments
        if row.get("status") not in ("Completed", "Cancelled")
    )

    assignment_counts = {}
    for row in assignments:
        if row.get("status") in ("Completed", "Cancelled"):
            continue
        hid = row["hospital_id"]
        assignment_counts[hid] = assignment_counts.get(hid, 0) + 1
    for hospital in hospitals:
        hospital["active_assignment_count"] = assignment_counts.get(hospital["id"], 0)
        if hospital.get("occupied_beds") is None:
            total = hospital.get("total_beds") or 0
            available = hospital.get("available_beds") or 0
            hospital["occupied_beds"] = max(0, total - available)

    waiting_count = sum(1 for row in live_responses if not row.get("ambulance_number"))
    capacity_issues = [
        hospital["name"]
        for hospital in hospitals
        if (hospital.get("available_beds") or 0) <= 0
        or (hospital.get("emergency_capacity") or 0) <= 0
        or hospital.get("status") != "Available"
    ]
    attention = []
    if available_ambulances == 0:
        attention.append("Ambulance shortage: no SAYA ambulance is currently available.")
    if waiting_count:
        attention.append(
            f"{waiting_count} {'emergencies' if waiting_count != 1 else 'emergency'} waiting for resources."
        )
    if capacity_issues:
        attention.append(
            "Hospital capacity issues: " + ", ".join(capacity_issues) + "."
        )
    if not attention:
        attention.append("No resource shortages right now.")

    system_status = "READY"
    if available_ambulances == 0 or waiting_count:
        system_status = "ATTENTION"

    return {
        "incidents": incidents,
        "ambulances": ambulances,
        "hospitals": hospitals,
        "assignments": assignments,
        "live_responses": live_responses,
        "pending_incidents": pending_incidents,
        "latest_assignment": latest_assignment,
        "latest_incident": latest_incident,
        "available_ambulances": available_ambulances,
        "available_hospitals": available_hospitals,
        "active_incident_count": active_incident_count,
        "active_assignment_count": active_assignment_count,
        "auto_detection": auto_detection,
        "demo_scenarios": list_demo_scenarios(),
        "attention": attention,
        "system_status": system_status,
    }


_INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="IST")


def _format_local_timestamp(value, assume_utc=False):
    if not value:
        return "Timestamp unavailable"
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "Timestamp unavailable"
    if timestamp.tzinfo is None:
        if not assume_utc:
            return "Timestamp unavailable"
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    local_timestamp = timestamp.astimezone(_INDIA_TIMEZONE)
    hour = local_timestamp.strftime("%I").lstrip("0") or "0"
    return (
        f"{local_timestamp.day:02d} {local_timestamp.strftime('%b %Y')}, "
        f"{hour}:{local_timestamp.strftime('%M:%S %p')} IST"
    )


def _valid_map_coordinates(latitude, longitude):
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        return None
    return [latitude, longitude]


def _route_geometry_for_map(value):
    if not value:
        return []
    try:
        coordinates = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    if not isinstance(coordinates, list):
        return []
    points = []
    for coordinate in coordinates:
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
            return []
        try:
            longitude, latitude = float(coordinate[0]), float(coordinate[1])
        except (TypeError, ValueError):
            return []
        if not (
            math.isfinite(latitude)
            and math.isfinite(longitude)
            and -90 <= latitude <= 90
            and -180 <= longitude <= 180
        ):
            return []
        points.append([latitude, longitude])
    return points if len(points) >= 2 else []


def _elapsed_since(value):
    if not value:
        return "Elapsed time unavailable — backend receipt time not recorded"
    try:
        received = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "Elapsed time unavailable — received timestamp is invalid"
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    elapsed_seconds = int((datetime.now(timezone.utc) - received).total_seconds())
    if elapsed_seconds < 0:
        return "Received timestamp is in the future; elapsed time unavailable"
    if elapsed_seconds < 60:
        return f"{elapsed_seconds} seconds"
    minutes, seconds = divmod(elapsed_seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds} sec"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} hr {minutes} min"
    days, hours = divmod(hours, 24)
    return f"{days} days {hours} hr"


def _enrich_live_responses(assignments, auto_detection, ambulances):
    rows = []
    for assignment in assignments:
        item = dict(assignment)
        score = item.get("priority_score")
        if score is None:
            item["priority_label"] = None
        elif score >= 8:
            item["priority_label"] = "CRITICAL"
        elif score >= 6:
            item["priority_label"] = "HIGH"
        elif score >= 4:
            item["priority_label"] = "MEDIUM"
        else:
            item["priority_label"] = "LOW"
        item["event_id"] = (
            item.get("phone_event_id")
            or item.get("incident_event_id")
        )
        item["event_timestamp"] = (
            item.get("incident_detection_timestamp")
            or item.get("phone_event_timestamp")
        )
        received_at = item.get("alert_received_at") or item.get("created_at")
        item["event_timestamp_display"] = _format_local_timestamp(
            item["event_timestamp"]
        )
        item["received_timestamp_display"] = _format_local_timestamp(
            received_at, assume_utc=True
        )
        item["assignment_timestamp_display"] = _format_local_timestamp(
            item.get("assignment_created_at"), assume_utc=True
        )
        item["elapsed_since_received"] = _elapsed_since(received_at)
        item["crash_confidence"] = (
            item.get("incident_crash_confidence")
            if item.get("incident_crash_confidence") is not None
            else item.get("alert_crash_confidence")
        )
        item["detection_source"] = (
            item.get("incident_detection_source")
            or ("Victim Smartphone" if item.get("phone_alert_id") else "Manual report")
        )
        item["gps_accuracy_unavailable_reason"] = (
            "GPS accuracy not supplied by device"
            if "phone" in item["detection_source"].lower()
            or "smartphone" in item["detection_source"].lower()
            else "GPS accuracy not recorded for this incident"
        )
        if auto_detection and auto_detection.get("incident_id") == item.get("incident_id"):
            if item["crash_confidence"] is None:
                item["crash_confidence"] = (
                    auto_detection.get("crash_confidence")
                    if auto_detection.get("crash_confidence") is not None
                    else auto_detection.get("confidence")
                )
            item["detection_source"] = (
                auto_detection.get("detection_source") or item["detection_source"]
            )
        if not item["event_id"]:
            item["event_id_display"] = (
                "Event ID unavailable — not recorded for this incident"
            )
        else:
            item["event_id_display"] = item["event_id"]

        if item.get("id") is None:
            item["assignment_status_display"] = (
                "No assignment record — incident remains unassigned"
            )
            failure_reason = item.get("assignment_failure_reason") or ""
            ambulance_route_failed = (
                "ambulance" in failure_reason.lower()
                and "rout" in failure_reason.lower()
            )
            hospital_route_failed = (
                "hospital" in failure_reason.lower()
                and "rout" in failure_reason.lower()
            )
            hospital_failure = (
                "hospital" in failure_reason.lower()
                or "placement" in failure_reason.lower()
            )
            item["ambulance_distance_status_message"] = (
                "Road distance unavailable — ambulance routing failed"
                if ambulance_route_failed
                else "Route not persisted — hospital placement failed; ambulance is unassigned"
                if hospital_failure
                else "Not calculated — ambulance is not assigned"
            )
            item["ambulance_eta_status_message"] = (
                "ETA unavailable — ambulance routing failed"
                if ambulance_route_failed
                else "ETA not persisted — hospital placement failed; ambulance is unassigned"
                if hospital_failure
                else "ETA unavailable — ambulance is not assigned"
            )
            item["hospital_distance_status_message"] = (
                "Road distance unavailable — hospital routing failed"
                if hospital_route_failed
                else "Not calculated — hospital is not assigned"
            )
            item["hospital_eta_status_message"] = (
                "ETA unavailable — hospital routing failed"
                if hospital_route_failed
                else "ETA unavailable — hospital is not assigned"
            )
            if item.get("assignment_failure_reason"):
                item["assignment_explanation_display"] = item[
                    "assignment_failure_reason"
                ]
            else:
                available_count = sum(
                    ambulance.get("status") == "Available"
                    for ambulance in ambulances
                )
                if available_count == 0:
                    status_counts = {}
                    for ambulance in ambulances:
                        status = ambulance.get("status") or "Status not recorded"
                        status_counts[status] = status_counts.get(status, 0) + 1
                    counts = ", ".join(
                        f"{count} {status}" for status, count in sorted(status_counts.items())
                    ) or "no ambulance records"
                    item["assignment_explanation_display"] = (
                        "Waiting for an available ambulance: none are currently "
                        f"marked Available ({counts})."
                    )
                else:
                    item["assignment_explanation_display"] = (
                        "No assignment record exists; a historical failure reason "
                        "was not stored."
                    )
            failure_reason = item.get("assignment_failure_reason")
            if failure_reason and (
                "hospital" in failure_reason.lower()
                or "placement" in failure_reason.lower()
            ):
                item["hospital_selection_explanation"] = failure_reason
            else:
                item["hospital_selection_explanation"] = (
                    "Hospital selection has not been attempted because no "
                    "ambulance assignment was completed."
                )
            item["ambulance_assignment_status"] = "No ambulance assigned"
            item["hospital_assignment_status"] = "No hospital assigned"
        else:
            item["assignment_status_display"] = (
                item.get("assignment_status") or "Assignment status not recorded"
            )
            item["ambulance_distance_status_message"] = "Road distance unavailable — not recorded"
            item["ambulance_eta_status_message"] = "ETA unavailable — not recorded"
            item["hospital_distance_status_message"] = "Road distance unavailable — not recorded"
            item["hospital_eta_status_message"] = "ETA unavailable — not recorded"
            item["assignment_explanation_display"] = (
                item.get("assignment_explanation")
                or "Assignment exists in the database; selection rationale was not recorded for this older assignment."
            )
            item["hospital_selection_explanation"] = (
                item.get("assignment_explanation")
                or "Hospital assignment exists in the database; historical selection rationale was not recorded."
            )
            item["ambulance_assignment_status"] = (
                f"Assigned — current resource status: {item.get('ambulance_status') or 'not recorded'}"
                if item.get("ambulance_number")
                else "Assignment record exists, but its ambulance record is missing"
            )
            item["hospital_assignment_status"] = (
                f"Assigned — current operational status: {item.get('hospital_status') or 'not recorded'}"
                if item.get("hospital_name")
                else "Assignment record exists, but its hospital record is missing"
            )
        if item.get("ambulance_number") is None and item.get("id") is not None:
            item["ambulance_number_display"] = (
                "Ambulance record unavailable — assignment references a missing resource"
            )
        elif item.get("id") is None:
            item["ambulance_number_display"] = "No ambulance assigned"
        else:
            item["ambulance_number_display"] = item.get("ambulance_number")
        if item.get("hospital_name") is None and item.get("id") is not None:
            item["hospital_name_display"] = (
                "Hospital record unavailable — assignment references a missing resource"
            )
        else:
            item["hospital_name_display"] = item.get("hospital_name")
        item["hospital_intake_status"] = item.get("hospital_intake_status") or (
            "Not assigned" if item.get("id") is None else "Received"
        )
        if item.get("id") is None:
            item["ambulance_route_status_display"] = (
                item.get("ambulance_distance_status_message")
                or "Road route and ETA unavailable until assignment and routing succeed"
            )
            item["hospital_route_status_display"] = (
                item.get("hospital_distance_status_message")
                or "Road route and ETA unavailable until hospital placement succeeds"
            )
        else:
            item["ambulance_route_status_display"] = (
                "OSRM route available"
                if item.get("ambulance_route_status") == "available"
                else "Road route and ETA unavailable — OSRM routing failed or the historical route was not recorded"
            )
            item["hospital_route_status_display"] = (
                "OSRM route available"
                if item.get("hospital_route_status") == "available"
                else "Road route and ETA unavailable — OSRM routing failed or the historical route was not recorded"
            )
        item["ambulance_last_gps_update_display"] = (
            "Not recorded — no ambulance GPS feed/update timestamp exists"
        )

        incident_coordinates = _valid_map_coordinates(
            item.get("incident_latitude"), item.get("incident_longitude")
        )
        ambulance_coordinates = (
            _valid_map_coordinates(
                item.get("ambulance_latitude"), item.get("ambulance_longitude")
            )
            if item.get("ambulance_id") is not None
            else None
        )
        hospital_coordinates = (
            _valid_map_coordinates(
                item.get("hospital_latitude"), item.get("hospital_longitude")
            )
            if item.get("hospital_id") is not None
            else None
        )
        item["map_payload"] = {
            "incident_id": item.get("incident_id"),
            "assignment_id": item.get("id"),
            "incident": (
                {
                    "coordinates": incident_coordinates,
                    "label": f"Incident #{item.get('incident_id')}",
                    "url": (
                        "https://www.google.com/maps?q="
                        f"{incident_coordinates[0]},{incident_coordinates[1]}"
                    ),
                }
                if incident_coordinates
                else None
            ),
            "ambulance": (
                {
                    "id": item.get("ambulance_id"),
                    "coordinates": ambulance_coordinates,
                    "label": (
                        f"{item.get('ambulance_number') or 'Assigned ambulance'} "
                        f"({item.get('ambulance_type') or 'type not recorded'})"
                    ),
                    "url": (
                        "https://www.google.com/maps?q="
                        f"{ambulance_coordinates[0]},{ambulance_coordinates[1]}"
                    ),
                }
                if ambulance_coordinates and item.get("ambulance_id") is not None
                else None
            ),
            "hospital": (
                {
                    "id": item.get("hospital_id"),
                    "coordinates": hospital_coordinates,
                    "label": (
                        f"{item.get('hospital_name') or 'Assigned hospital'} "
                        f"(Hospital #{item.get('hospital_id')})"
                    ),
                    "url": (
                        "https://www.google.com/maps?q="
                        f"{hospital_coordinates[0]},{hospital_coordinates[1]}"
                    ),
                }
                if hospital_coordinates and item.get("hospital_id") is not None
                else None
            ),
            "ambulance_route": _route_geometry_for_map(
                item.get("ambulance_route_geometry")
            ),
            "hospital_route": _route_geometry_for_map(
                item.get("hospital_route_geometry")
            ),
            "ambulance_distance_km": item.get("ambulance_distance"),
            "ambulance_eta_minutes": item.get("ambulance_eta"),
            "hospital_distance_km": item.get("hospital_distance"),
            "hospital_eta_minutes": item.get("hospital_eta"),
        }
        rows.append(item)
    return rows


def hospital_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "hospital" or not session.get("hospital_id"):
            flash("Please log in as a hospital.", "error")
            return redirect(url_for("hospital_login"))
        return view(*args, **kwargs)

    return wrapped


def admin_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Please log in to the SAYA Control Center.", "error")
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)

    return wrapped


def admin_api_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            return jsonify({"error": "Admin authentication required"}), 401
        return view(*args, **kwargs)

    return wrapped


def create_incident_record(
    incident_type,
    location,
    latitude,
    longitude,
    severity,
    number_of_patients,
    detection_source=None,
    crash_confidence=None,
    event_id=None,
    detection_timestamp=None,
    gps_accuracy=None,
    speed=None,
):
    priority_score, priority = calculate_priority(
        severity,
        number_of_patients,
        incident_type,
    )

    _ensure_schema()
    connection = get_database_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO incidents
        (
            incident_type,
            location,
            latitude,
            longitude,
            severity,
            priority_score,
            number_of_patients,
            detection_source,
            crash_confidence,
            event_id,
            detection_timestamp,
            gps_accuracy,
            speed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            incident_type,
            location,
            latitude,
            longitude,
            severity,
            priority_score,
            number_of_patients,
            detection_source,
            crash_confidence,
            event_id,
            detection_timestamp,
            gps_accuracy,
            speed,
        ),
    )

    connection.commit()
    incident_id = cursor.lastrowid
    connection.close()

    return incident_id, priority_score, priority


def _load_available_unassigned_ambulances(connection):
    active_ambulance_ids = {
        row["ambulance_id"]
        for row in connection.execute(
            """
            SELECT DISTINCT ambulance_id
            FROM assignments
            WHERE ambulance_id IS NOT NULL
              AND (
                    status IS NULL
                    OR lower(trim(status)) NOT IN ('completed', 'cancelled')
              )
            """
        ).fetchall()
    }
    all_ambulances = [
        dict(row)
        for row in connection.execute("SELECT * FROM ambulances").fetchall()
    ]
    candidates = [
        ambulance
        for ambulance in all_ambulances
        if ambulance.get("status") == "Available"
        and ambulance.get("current_incident_id") is None
        and ambulance.get("id") not in active_ambulance_ids
    ]
    return all_ambulances, candidates


def create_assignment_record(incident_id):
    connection = get_database_connection()

    def leave_waiting(reason):
        connection.execute(
            """
            UPDATE incidents
            SET status = 'Waiting',
                assignment_failure_reason = ?,
                assignment_explanation = NULL
            WHERE id = ?
            """,
            (reason, incident_id),
        )
        connection.commit()
        connection.close()
        return None, reason, 503

    incident = connection.execute(
        "SELECT * FROM incidents WHERE id = ?",
        (incident_id,),
    ).fetchone()

    if not incident:
        connection.close()
        return None, "Incident not found", 404

    existing_assignment = connection.execute(
        "SELECT id FROM assignments WHERE incident_id = ? ORDER BY id DESC LIMIT 1",
        (incident_id,),
    ).fetchone()
    if existing_assignment:
        connection.close()
        return (
            None,
            f"Incident already has assignment #{existing_assignment['id']}; "
            "historical assignments are not rewritten.",
            409,
        )

    if str(incident["status"] or "").lower() in ("completed", "cancelled"):
        connection.close()
        return None, "Completed or cancelled incidents cannot be reassigned.", 409

    all_ambulances, ambulances = _load_available_unassigned_ambulances(
        connection
    )
    hospitals = connection.execute("SELECT * FROM hospitals").fetchall()

    hospitals = [dict(hospital) for hospital in hospitals]

    try:
        incident_latitude = float(incident["latitude"])
        incident_longitude = float(incident["longitude"])
    except (TypeError, ValueError):
        return leave_waiting(
            "Waiting for a valid incident location: latitude or longitude was not recorded."
        )
    if not (
        math.isfinite(incident_latitude)
        and math.isfinite(incident_longitude)
        and -90 <= incident_latitude <= 90
        and -180 <= incident_longitude <= 180
    ):
        return leave_waiting(
            "Waiting for a valid incident location: stored coordinates are outside valid latitude/longitude ranges."
        )

    ambulance_ranking = rank_available_ambulances(
        incident_latitude,
        incident_longitude,
        ambulances,
    )
    ambulance = ambulance_ranking["selected"]
    if ambulance is None:
        if any(row.get("status") == "Available" for row in all_ambulances):
            reason = (
                "Waiting for an available ambulance: every ambulance marked "
                "Available is already linked to an active assignment or current "
                "incident."
            )
        else:
            status_counts = {}
            for row in all_ambulances:
                status = row.get("status") or "Status not recorded"
                status_counts[status] = status_counts.get(status, 0) + 1
            counts = ", ".join(
                f"{count} {status}" for status, count in sorted(status_counts.items())
            ) or "no ambulance records"
            reason = (
                "Waiting for an available ambulance: no ambulance is currently "
                f"marked Available ({counts})."
            )
        return leave_waiting(reason)

    hospital_ranking = rank_hospitals(
        incident_latitude,
        incident_longitude,
        hospitals,
        severity=incident["severity"] or 1,
        priority_score=incident["priority_score"] or 0,
        number_of_patients=incident["number_of_patients"] or 1,
    )
    hospital = hospital_ranking["selected"]
    if hospital is None:
        return leave_waiting(hospital_ranking["reason"])

    route = ambulance["route"]
    hospital_route = hospital["route"]
    assignment_explanation = (
        f"{ambulance_ranking['reason']} {hospital_ranking['reason']}"
    )

    connection.execute("BEGIN IMMEDIATE")
    current_assignment = connection.execute(
        "SELECT id FROM assignments WHERE incident_id = ? LIMIT 1",
        (incident_id,),
    ).fetchone()
    if current_assignment:
        connection.rollback()
        connection.close()
        return (
            None,
            f"Incident already has assignment #{current_assignment['id']}; "
            "historical assignments are not rewritten.",
            409,
        )

    ambulance_row = connection.execute(
        "SELECT status, current_incident_id FROM ambulances WHERE id = ?",
        (ambulance["ambulance_id"],),
    ).fetchone()
    ambulance_has_active_assignment = connection.execute(
        """
        SELECT 1 FROM assignments
        WHERE ambulance_id = ?
          AND (
                status IS NULL
                OR lower(trim(status)) NOT IN ('completed', 'cancelled')
          )
        LIMIT 1
        """,
        (ambulance["ambulance_id"],),
    ).fetchone()
    if (
        not ambulance_row
        or ambulance_row["status"] != "Available"
        or ambulance_row["current_incident_id"] is not None
        or ambulance_has_active_assignment
    ):
        return leave_waiting(
            "Waiting for an available ambulance: the selected ambulance became "
            "unavailable before its assignment could be saved; retry dispatch."
        )

    hospital_row = connection.execute(
        """
        SELECT status, available_beds, emergency_capacity, latitude, longitude
        FROM hospitals WHERE id = ?
        """,
        (hospital["hospital_id"],),
    ).fetchone()
    required_patients = max(1, int(incident["number_of_patients"] or 1))
    if (
        not hospital_row
        or hospital_row["status"] != "Available"
        or (hospital_row["available_beds"] or 0) < required_patients
        or (hospital_row["emergency_capacity"] or 0) < required_patients
        or hospital_row["latitude"] != hospital["latitude"]
        or hospital_row["longitude"] != hospital["longitude"]
    ):
        return leave_waiting(
            "Awaiting hospital placement: the selected hospital's availability, "
            "capacity, or coordinates changed before the assignment was saved; "
            "retry dispatch."
        )

    ambulance_update = connection.execute(
        """
        UPDATE ambulances
        SET status = 'Assigned',
            current_incident_id = ?
        WHERE id = ? AND status = 'Available' AND current_incident_id IS NULL
        """,
        (incident_id, ambulance["ambulance_id"]),
    )
    if ambulance_update.rowcount != 1:
        return leave_waiting(
            "Waiting for an available ambulance: the selected ambulance became "
            "unavailable before its assignment could be saved; retry dispatch."
        )

    connection.execute(
        """
        INSERT INTO assignments
        (
            incident_id,
            ambulance_id,
            hospital_id,
            ambulance_distance,
            ambulance_eta,
            hospital_distance,
            hospital_eta,
            status,
            created_at,
            ambulance_route_geometry,
            hospital_route_geometry,
            ambulance_route_status,
            hospital_route_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
        """,
        (
            incident_id,
            ambulance["ambulance_id"],
            hospital["hospital_id"],
            route["distance_km"] if route else None,
            route["duration_minutes"] if route else None,
            hospital_route["distance_km"] if hospital_route else None,
            hospital_route["duration_minutes"] if hospital_route else None,
            "Active",
            json.dumps(route["geometry"]) if route and route.get("geometry") else None,
            (
                json.dumps(hospital_route["geometry"])
                if hospital_route and hospital_route.get("geometry")
                else None
            ),
            "available" if route else "unavailable",
            "available" if hospital_route else "unavailable",
        ),
    )

    connection.execute(
        """
        UPDATE incidents
        SET status = 'Assigned',
            assignment_failure_reason = NULL,
            assignment_explanation = ?
        WHERE id = ?
        """,
        (assignment_explanation, incident_id),
    )

    connection.commit()
    connection.close()

    payload = {
        "message": "Emergency assignment created successfully",
        "incident_id": incident_id,
        "ambulance": ambulance,
        "hospital": hospital,
        "assignment_explanation": assignment_explanation,
        "ambulance_to_incident_distance_km": (
            route["distance_km"] if route else None
        ),
        "ambulance_to_incident_eta_minutes": (
            route["duration_minutes"] if route else None
        ),
        "incident_to_hospital_distance_km": (
            hospital_route["distance_km"] if hospital_route else None
        ),
        "incident_to_hospital_eta_minutes": (
            hospital_route["duration_minutes"] if hospital_route else None
        ),
    }

    return payload, None, 201


# -----------------------------
# DASHBOARD (Jinja2)
# -----------------------------

@app.route("/", methods=["GET"])
def home():
    return render_template("home.html")


@app.route("/phone", methods=["GET"])
def phone_monitor():
    return render_template("phone_monitor.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("role") == "admin":
        return redirect(url_for("admin_overview"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        _ensure_schema()
        connection = get_database_connection()
        admin = connection.execute(
            "SELECT * FROM admins WHERE username = ?",
            (username,),
        ).fetchone()
        connection.close()
        if admin and check_password_hash(admin["password_hash"], password):
            session["role"] = "admin"
            session["admin_username"] = admin["username"]
            return redirect(url_for("admin_overview"))
        flash("Invalid admin login.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("role", None)
    session.pop("admin_username", None)
    flash("Logged out.", "success")
    return redirect(url_for("home"))


@app.route("/admin")
@app.route("/admin/overview")
@admin_login_required
def admin_overview():
    data = fetch_dashboard_data()
    return render_template("admin_overview.html", **data)


@app.route("/admin/live")
@admin_login_required
def admin_live_response():
    data = fetch_dashboard_data()
    return render_template("admin_live_response.html", **data), {
        "Cache-Control": "no-store"
    }


@app.route("/admin/live/data")
@admin_api_login_required
def admin_live_data():
    data = fetch_dashboard_data()
    known_ids = set()
    for value in (request.args.get("known") or "")[:5000].split(",")[:500]:
        if value.isdigit():
            known_ids.add(int(value))
    new_cards = [
        item
        for item in data["live_responses"]
        if item["incident_id"] not in known_ids
    ]
    response = jsonify(
        {
            "live_responses": data["live_responses"],
            "new_cards_html": render_template(
                "_admin_live_response_cards.html",
                live_responses=new_cards,
            ),
            "server_time": datetime.now(_INDIA_TIMEZONE).isoformat(),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/admin/live/retry/<int:incident_id>", methods=["POST"])
@admin_api_login_required
def admin_live_retry(incident_id):
    payload, error, status = create_assignment_record(incident_id)
    if error:
        return jsonify({"error": error, "incident_id": incident_id}), status
    return jsonify(payload), status


@app.route("/admin/hospitals")
@admin_login_required
def admin_hospitals():
    data = fetch_dashboard_data()
    return render_template("admin_hospitals.html", **data)


@app.route("/admin/hospitals/add", methods=["POST"])
@admin_login_required
def admin_add_hospital():
    _ensure_schema()
    name = (request.form.get("name") or "").strip()
    location = (request.form.get("location") or "").strip()
    username = (request.form.get("username") or "").strip().lower()
    password = request.form.get("password") or ""
    try:
        total_beds = int(request.form.get("total_beds"))
        available_beds = int(request.form.get("available_beds"))
        occupied_beds = int(request.form.get("occupied_beds"))
        emergency_capacity = int(request.form.get("emergency_capacity"))
    except (TypeError, ValueError):
        flash("Please enter valid hospital capacity values.", "error")
        return redirect(url_for("admin_hospitals"))

    status = (request.form.get("status") or "Available").strip()
    if (
        min(total_beds, available_beds, occupied_beds, emergency_capacity) < 0
        or available_beds + occupied_beds > total_beds
        or status not in ("Available", "Limited", "Unavailable")
    ):
        flash("Please check the hospital capacity and status values.", "error")
        return redirect(url_for("admin_hospitals"))

    if not name or not location or not username or not password:
        flash("All hospital fields are required.", "error")
        return redirect(url_for("admin_hospitals"))

    try:
        coordinates = geocode_location(location)
    except requests.RequestException:
        coordinates = None
    if coordinates is None:
        flash("SAYA could not locate that address. Please enter a more specific location.", "error")
        return redirect(url_for("admin_hospitals"))
    latitude, longitude = coordinates

    connection = get_database_connection()
    existing = connection.execute(
        "SELECT id FROM hospitals WHERE lower(username) = ?",
        (username,),
    ).fetchone()
    if existing:
        connection.close()
        flash("That hospital username is already in use.", "error")
        return redirect(url_for("admin_hospitals"))

    connection.execute(
        """
        INSERT INTO hospitals
        (
            name, location, latitude, longitude,
            total_beds, available_beds, occupied_beds,
            icu_total, icu_available, emergency_capacity,
            status, username, password_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            location,
            latitude,
            longitude,
            total_beds,
            available_beds,
            occupied_beds,
            0,
            0,
            emergency_capacity,
            status,
            username,
            generate_password_hash(password),
        ),
    )
    connection.commit()
    connection.close()
    flash(f"{name} joined the SAYA network.", "success")
    return redirect(url_for("admin_hospitals"))


@app.route("/admin/ambulances")
@admin_login_required
def admin_ambulances():
    data = fetch_dashboard_data()
    return render_template("admin_ambulances.html", **data)


@app.route("/admin/ambulances/add", methods=["POST"])
@admin_login_required
def admin_add_ambulance():
    number = (request.form.get("ambulance_number") or "").strip()
    ambulance_type = (request.form.get("ambulance_type") or "").strip()
    location = (request.form.get("location") or "").strip()
    status = (request.form.get("status") or "Available").strip()

    if not number or not ambulance_type or not location:
        flash("Ambulance number, type, and location are required.", "error")
        return redirect(url_for("admin_ambulances"))

    try:
        coordinates = geocode_location(location)
    except requests.RequestException:
        coordinates = None
    if coordinates is None:
        flash("SAYA could not locate that address. Please enter a more specific location.", "error")
        return redirect(url_for("admin_ambulances"))
    latitude, longitude = coordinates

    connection = get_database_connection()
    connection.execute(
        """
        INSERT INTO ambulances
        (ambulance_number, latitude, longitude, ambulance_type, status, location)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (number, latitude, longitude, ambulance_type, status, location),
    )
    connection.commit()
    connection.close()
    flash(f"SAYA ambulance {number} added.", "success")
    return redirect(url_for("admin_ambulances"))


def _hospital_for_session():
    data = fetch_dashboard_data()
    hospital_id = session.get("hospital_id")
    hospital = next(
        (row for row in data["hospitals"] if row["id"] == hospital_id),
        None,
    )
    incoming = [
        row for row in data["live_responses"]
        if row.get("hospital_id") == hospital_id and row.get("id")
    ]
    return hospital, incoming


@app.route("/hospital/login", methods=["GET", "POST"])
def hospital_login():
    _ensure_schema()
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        connection = get_database_connection()
        hospital = connection.execute(
            "SELECT * FROM hospitals WHERE lower(username) = ?",
            (username.lower(),),
        ).fetchone()
        connection.close()
        valid = False
        if hospital:
            if hospital["password_hash"]:
                valid = check_password_hash(hospital["password_hash"], password)
            else:
                valid = password == HOSPITAL_PASSWORD
        if hospital and valid:
            session["role"] = "hospital"
            session["hospital_id"] = hospital["id"]
            session["hospital_name"] = hospital["name"]
            return redirect(url_for("hospital_dashboard"))
        flash("Invalid hospital login.", "error")
    return render_template("hospital_login.html")


@app.route("/hospital/logout", methods=["GET", "POST"])
def hospital_logout():
    session.pop("role", None)
    session.pop("hospital_id", None)
    session.pop("hospital_name", None)
    flash("Logged out.", "success")
    return redirect(url_for("home"))


@app.route("/hospital/dashboard")
@hospital_login_required
def hospital_dashboard():
    hospital, incoming = _hospital_for_session()
    if hospital is None:
        flash("Hospital not found.", "error")
        return redirect(url_for("hospital_logout"))
    return render_template(
        "hospital_dashboard.html",
        hospital=hospital,
        incoming=incoming,
    )


@app.route("/hospital/incoming")
@hospital_login_required
def hospital_incoming():
    hospital, incoming = _hospital_for_session()
    if hospital is None:
        flash("Hospital not found.", "error")
        return redirect(url_for("hospital_logout"))
    return render_template(
        "hospital_incoming.html",
        hospital=hospital,
        incoming=incoming,
    )


@app.route("/hospital/resources", methods=["GET", "POST"])
@hospital_login_required
def hospital_resources():
    hospital, incoming = _hospital_for_session()
    if hospital is None:
        flash("Hospital not found.", "error")
        return redirect(url_for("hospital_logout"))
    if request.method == "GET":
        return render_template(
            "hospital_resources.html",
            hospital=hospital,
            incoming=incoming,
        )
    return hospital_update_resources()


@app.route("/hospital/resources/update", methods=["POST"])
@hospital_login_required
def hospital_update_resources():
    try:
        available_beds = int(request.form.get("available_beds"))
        occupied_beds = int(request.form.get("occupied_beds"))
        emergency_capacity = int(request.form.get("emergency_capacity"))
    except (TypeError, ValueError):
        flash("Please enter valid resource numbers.", "error")
        return redirect(url_for("hospital_resources"))

    status = (request.form.get("status") or "").strip()
    if status not in ("Available", "Limited", "Unavailable"):
        flash("Invalid hospital status.", "error")
        return redirect(url_for("hospital_resources"))
    if available_beds < 0 or occupied_beds < 0 or emergency_capacity < 0:
        flash("Resource values cannot be negative.", "error")
        return redirect(url_for("hospital_resources"))

    total_beds = max(available_beds + occupied_beds, available_beds)
    _ensure_schema()
    connection = get_database_connection()
    connection.execute(
        """
        UPDATE hospitals
        SET available_beds = ?,
            occupied_beds = ?,
            emergency_capacity = ?,
            total_beds = ?,
            status = ?
        WHERE id = ?
        """,
        (
            available_beds,
            occupied_beds,
            emergency_capacity,
            total_beds,
            status,
            session.get("hospital_id"),
        ),
    )
    connection.commit()
    connection.close()
    flash("Hospital resources updated. SAYA will use these values for the next selection.", "success")
    return redirect(url_for("hospital_resources"))


@app.route("/hospital/intake/<int:assignment_id>", methods=["POST"])
@hospital_login_required
def hospital_update_intake(assignment_id):
    status = (request.form.get("hospital_intake_status") or "").strip()
    if status not in ("Received", "Preparing", "Patient Arrived"):
        flash("Invalid intake status.", "error")
        return redirect(url_for("hospital_incoming"))

    _ensure_schema()
    connection = get_database_connection()
    assignment = connection.execute(
        "SELECT id, hospital_id FROM assignments WHERE id = ?",
        (assignment_id,),
    ).fetchone()
    if not assignment or assignment["hospital_id"] != session.get("hospital_id"):
        connection.close()
        flash("Assignment not found for this hospital.", "error")
        return redirect(url_for("hospital_incoming"))

    connection.execute(
        "UPDATE assignments SET hospital_intake_status = ? WHERE id = ?",
        (status, assignment_id),
    )
    connection.commit()
    connection.close()
    flash(f"Emergency status updated to {status}.", "success")
    return redirect(url_for("hospital_incoming"))


@app.route("/simulate-accident", methods=["POST"])
@admin_api_login_required
def simulate_accident():
    """Simulate a CCTV-detected accident and run the full emergency pipeline."""
    camera_id = None
    if request.is_json and request.get_json(silent=True):
        camera_id = request.get_json(silent=True).get("camera_id")
    elif request.form:
        camera_id = request.form.get("camera_id") or None

    result = dispatch_simulated_accident(
        camera_id=camera_id,
        create_incident_fn=create_incident_record,
        create_assignment_fn=create_assignment_record,
    )

    if request.is_json:
        status = 201 if result.get("success") else 400
        return jsonify(result), status

    if result.get("success"):
        flash(
            f"Automatic CCTV response: Incident #{result['incident_id']} "
            f"dispatched. Ambulance {result['ambulance']} "
            f"({result['ambulance_eta']} min) → "
            f"Hospital {result['hospital']} ({result['hospital_eta']} min).",
            "success",
        )
    else:
        flash(result.get("message", "Automatic dispatch failed."), "error")

    return redirect(url_for("admin_live_response"))


@app.route("/detect-accident", methods=["POST"])
@admin_api_login_required
def detect_accident():
    """Accept a detected accident event and run the full emergency pipeline."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object body required"}), 400

    if "accident_detected" not in data:
        return jsonify({"error": "accident_detected field is required"}), 400

    if not isinstance(data["accident_detected"], bool):
        return jsonify({"error": "accident_detected must be a boolean"}), 400

    if data.get("accident_detected"):
        required = ("incident_type", "location", "latitude", "longitude", "severity")
        missing = [field for field in required if data.get(field) is None]
        if missing:
            return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
        confidence = data.get("crash_confidence", data.get("confidence"))
        try:
            latitude = float(data["latitude"])
            longitude = float(data["longitude"])
            severity = _parse_integer(data["severity"])
            number_of_patients = _parse_integer(data.get("number_of_patients", 1))
            confidence = float(confidence)
        except (TypeError, ValueError):
            return jsonify({"error": "Coordinates, confidence, severity, and patient count must be numeric"}), 400
        if (
            not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not (-90 <= latitude <= 90 and -180 <= longitude <= 180)
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
            or not 1 <= severity <= 5
            or number_of_patients < 1
        ):
            return jsonify({"error": "Coordinates, confidence, severity, or patient count are outside valid ranges"}), 400
        if (
            not isinstance(data["incident_type"], str)
            or not data["incident_type"].strip()
            or len(data["incident_type"]) > 120
            or not isinstance(data["location"], str)
            or not data["location"].strip()
            or len(data["location"]) > 250
        ):
            return jsonify({"error": "incident_type and location must be non-empty text within length limits"}), 400
        data["latitude"] = latitude
        data["longitude"] = longitude
        data["severity"] = severity
        data["number_of_patients"] = number_of_patients
        data["crash_confidence"] = confidence

    result = dispatch_detected_accident(
        data,
        create_incident_fn=create_incident_record,
        create_assignment_fn=create_assignment_record,
    )

    status = 201 if result.get("success") else 400
    return jsonify(result), status


def _ensure_phone_alert_table():
    _ensure_schema()


def _ensure_hospital_intake_column():
    _ensure_schema()


def _is_duplicate_phone_alert(device_id: str, event_id: str | None) -> bool:
    """Block only a replay of the same event from the same device."""
    _ensure_phone_alert_table()
    connection = get_database_connection()

    if event_id:
        existing = connection.execute(
            "SELECT id FROM phone_crash_alerts WHERE device_id = ? AND event_id = ?",
            (device_id, str(event_id)),
        ).fetchone()
    connection.close()
    return existing is not None if event_id else False


def _record_phone_alert(
    device_id: str,
    event_id: str | None,
    timestamp: str | None,
    incident_id: int | None,
    crash_confidence=None,
    speed=None,
    gps_accuracy=None,
    severity=None,
    detection_source=None,
):
    _ensure_phone_alert_table()
    connection = get_database_connection()
    connection.execute(
        """
        INSERT INTO phone_crash_alerts
            (device_id, event_id, alert_timestamp, incident_id, crash_confidence,
             speed, gps_accuracy, severity, detection_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            device_id,
            event_id,
            timestamp,
            incident_id,
            crash_confidence,
            speed,
            gps_accuracy,
            severity,
            detection_source,
        ),
    )
    connection.commit()
    connection.close()


@app.route("/phone-crash-alert", methods=["POST"])
def phone_crash_alert():
    """
    Receive a confirmed victim-phone crash alert and run the EXISTING pipeline:
    incident → priority → ambulance → hospital → routing → assignment.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    required = [
        "device_id", "event_id", "timestamp", "latitude", "longitude",
        "crash_confidence", "severity",
    ]
    missing = [field for field in required if data.get(field) is None]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
    if any(
        not isinstance(data[field], str) or not data[field].strip()
        for field in ("device_id", "event_id", "timestamp")
    ):
        return jsonify({
            "error": "device_id, event_id, and timestamp must be non-empty text"
        }), 400
    if (
        len(data["device_id"]) > 128
        or len(data["event_id"]) > 128
        or len(data["timestamp"]) > 80
    ):
        return jsonify({"error": "device_id/event_id/timestamp exceeds its maximum length"}), 400
    location = data.get("location")
    if location is not None and (
        not isinstance(location, str) or len(location.strip()) > 250
    ):
        return jsonify({"error": "location must be text no longer than 250 characters"}), 400

    try:
        latitude = float(data["latitude"])
        longitude = float(data["longitude"])
        gps_accuracy = (
            None if data.get("gps_accuracy") is None
            else float(data["gps_accuracy"])
        )
        crash_confidence = float(data["crash_confidence"])
        severity = _parse_integer(data["severity"])
        speed = None if data.get("speed") is None else float(data["speed"])
        number_of_patients = _parse_integer(data.get("number_of_patients", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "GPS, confidence, severity, and speed values must be numeric"}), 400
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not (-90 <= latitude <= 90 and -180 <= longitude <= 180)
    ):
        return jsonify({"error": "Latitude or longitude is outside the valid range"}), 400
    if (
        (gps_accuracy is not None
         and (not math.isfinite(gps_accuracy) or gps_accuracy < 0))
        or (speed is not None and (not math.isfinite(speed) or speed < 0))
    ):
        return jsonify({"error": "GPS accuracy and speed must not be negative"}), 400
    if (
        not math.isfinite(crash_confidence)
        or not 0 <= crash_confidence <= 1
        or not 1 <= severity <= 5
        or number_of_patients < 1
    ):
        return jsonify({
            "error": (
                "Crash confidence must be 0–1, severity must be 1–5, "
                "and number_of_patients must be at least 1"
            )
        }), 400

    data["latitude"] = latitude
    data["longitude"] = longitude
    data["gps_accuracy"] = gps_accuracy
    data["crash_confidence"] = crash_confidence
    data["severity"] = severity
    data["speed"] = speed
    data["location"] = (
        location.strip()
        if isinstance(location, str) and location.strip()
        else f"Victim Smartphone GPS ({latitude:.6f}, {longitude:.6f})"
    )
    data["incident_type"] = "Road Accident"
    data["detection_source"] = "Victim Smartphone"
    data["number_of_patients"] = number_of_patients

    device_id = str(data["device_id"])
    event_id = str(data["event_id"])
    timestamp = data.get("timestamp")

    if _is_duplicate_phone_alert(device_id, event_id):
        connection = get_database_connection()
        existing = connection.execute(
            "SELECT incident_id FROM phone_crash_alerts WHERE device_id = ? AND event_id = ?",
            (device_id, event_id),
        ).fetchone()
        connection.close()
        incident_id = existing["incident_id"] if existing else None
        return jsonify({
            "success": incident_id is not None,
            "emergency_status": "DUPLICATE_BLOCKED",
            "message": "Duplicate phone crash alert blocked (same device and event)",
            "device_id": device_id,
            "event_id": event_id,
            "incident_id": incident_id,
            "already_received": incident_id is not None,
            "detection_source": "Victim Smartphone",
        }), 409

    result = dispatch_phone_crash_alert(
        data,
        create_incident_fn=create_incident_record,
        create_assignment_fn=create_assignment_record,
    )

    if result.get("incident_id"):
        _record_phone_alert(
            device_id, event_id, timestamp, result.get("incident_id"),
            data.get("crash_confidence"), data.get("speed"),
            data.get("gps_accuracy"), data.get("severity"),
            data.get("detection_source"),
        )
        status_code = 201 if result.get("emergency_status") == "AUTO_DISPATCHED" else 202
        return jsonify(result), status_code

    return jsonify(result), result.get("status_code", 400)


@app.route("/phone-crash-demo", methods=["POST"])
@admin_api_login_required
def phone_crash_demo():
    """
    SAFE DEMO MODE (dashboard / server).

    Simulates phone sensors for a selected scenario → SAME crash detector →
    SAME /phone-crash-alert endpoint when a crash is confirmed.
    Does not create an assignment by bypassing the detector.
    """
    import uuid

    from ai.emergency_dispatcher import set_last_auto_response

    if request.is_json and request.get_json(silent=True):
        scenario_name = request.get_json(silent=True).get("scenario")
    else:
        scenario_name = request.form.get("scenario")

    scenario = get_demo_scenario(scenario_name)
    sample = scenario["sample"]
    assessment = assess_crash(sample)
    gps = get_phone_gps(scenario["gps"])

    if not assessment.crash_detected:
        result = {
            "success": False,
            "demo": True,
            "scenario": scenario["name"],
            "scenario_label": scenario["label"],
            "emergency_status": "NO_CRASH",
            "message": (
                f"Safe demo [{scenario['name']}]: no crash confirmed — "
                f"{assessment.reason}"
            ),
            "detection_source": "Victim Smartphone",
            "crash_confidence": round(assessment.crash_confidence, 3),
            "confidence": round(assessment.crash_confidence, 3),
            "location": gps["location"],
            "severity": assessment.severity,
            "ambulance": None,
            "hospital": None,
            "ambulance_eta": None,
            "hospital_eta": None,
            "assessment": assessment.to_dict(),
        }
        set_last_auto_response(result)
        if request.is_json:
            return jsonify(result), 200
        flash(result["message"], "error")
        return redirect(url_for("admin_live_response"))

    # Countdown elapsed without cancel (server-side safe demo).
    # New device_id + event_id each click = separate test event;
    # repeating the SAME event_id is still blocked by /phone-crash-alert.
    demo_device = f"{mobile_config.DEVICE_ID}-DEMO-{uuid.uuid4().hex[:8]}"
    payload = build_alert_payload(
        assessment,
        gps,
        device_id=demo_device,
        speed_mps=sample.speed_mps,
        event_id=str(uuid.uuid4()),
    )

    endpoint_response = app.test_client().post("/phone-crash-alert", json=payload)
    result = endpoint_response.get_json() or {}
    result["assessment"] = assessment.to_dict()
    result["demo"] = True
    result["scenario"] = scenario["name"]
    result["scenario_label"] = scenario["label"]
    set_last_auto_response(result)

    if request.is_json:
        return jsonify(result), endpoint_response.status_code

    if result.get("success"):
        flash(
            f"Safe demo [{scenario['name']}]: Incident #{result['incident_id']} "
            f"auto-dispatched. Confidence {assessment.crash_confidence:.2f}, "
            f"severity {assessment.severity}. "
            f"Ambulance {result.get('ambulance')} ({result.get('ambulance_eta')} min) → "
            f"Hospital {result.get('hospital')} ({result.get('hospital_eta')} min).",
            "success",
        )
    else:
        flash(result.get("message", "Phone crash demo failed."), "error")

    return redirect(url_for("admin_live_response"))


@app.route("/create-emergency", methods=["POST"])
@admin_login_required
def create_emergency():
    incident_type = request.form.get("incident_type", "").strip()
    location = request.form.get("location", "").strip()
    latitude = request.form.get("latitude")
    longitude = request.form.get("longitude")
    severity = request.form.get("severity")
    number_of_patients = request.form.get("number_of_patients")

    if (
        not incident_type
        or len(incident_type) > 120
        or not location
        or len(location) > 250
    ):
        flash("Incident type and location are required.", "error")
        return redirect(url_for("admin_live_response"))

    try:
        latitude = float(latitude)
        longitude = float(longitude)
        severity = _parse_integer(severity)
        number_of_patients = _parse_integer(number_of_patients)
    except (TypeError, ValueError):
        flash("Please enter valid numeric values for coordinates, severity, and patients.", "error")
        return redirect(url_for("admin_live_response"))
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not (-90 <= latitude <= 90 and -180 <= longitude <= 180)
        or not 1 <= severity <= 5
        or number_of_patients < 1
    ):
        flash("Coordinates, severity, or patient count are outside valid ranges.", "error")
        return redirect(url_for("admin_live_response"))

    incident_id, priority_score, priority = create_incident_record(
        incident_type,
        location,
        latitude,
        longitude,
        severity,
        number_of_patients,
    )

    assignment, error, _ = create_assignment_record(incident_id)

    if error:
        flash(
            f"Incident #{incident_id} created (priority {priority}, score {priority_score}), "
            f"but assignment failed: {error}",
            "error",
        )
        return redirect(url_for("admin_live_response"))

    flash(
        f"Emergency assignment created for incident #{incident_id}. "
        f"Priority: {priority} ({priority_score}). "
        f"Ambulance: {assignment['ambulance']['ambulance_number']} "
        f"({assignment['ambulance_to_incident_eta_minutes']} min). "
        f"Hospital: {assignment['hospital']['hospital_name']} "
        f"({assignment['incident_to_hospital_eta_minutes']} min).",
        "success",
    )
    return redirect(url_for("admin_live_response"))


# -----------------------------
# GET ALL AMBULANCES
# -----------------------------

@app.route("/ambulances")
@admin_api_login_required
def get_ambulances():
    connection = get_database_connection()
    ambulances = connection.execute("SELECT * FROM ambulances").fetchall()
    connection.close()
    return jsonify([dict(ambulance) for ambulance in ambulances])


# -----------------------------
# GET ALL HOSPITALS
# -----------------------------

@app.route("/hospitals")
@admin_api_login_required
def get_hospitals():
    connection = get_database_connection()
    hospitals = connection.execute("SELECT * FROM hospitals").fetchall()
    connection.close()
    return jsonify([dict(hospital) for hospital in hospitals])


# -----------------------------
# CREATE NEW INCIDENT
# -----------------------------

@app.route("/incidents", methods=["POST"])
@admin_api_login_required
def create_incident():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object body required"}), 400

    incident_type = data.get("incident_type")
    location = data.get("location")

    if (
        not isinstance(incident_type, str)
        or not incident_type.strip()
        or len(incident_type) > 120
        or not isinstance(location, str)
        or not location.strip()
        or len(location) > 250
    ):
        return jsonify({
            "error": "Incident type and location are required and must be within length limits"
        }), 400
    try:
        latitude = float(data["latitude"])
        longitude = float(data["longitude"])
        severity = _parse_integer(data["severity"])
        number_of_patients = _parse_integer(data["number_of_patients"])
    except (KeyError, TypeError, ValueError):
        return jsonify({
            "error": "Latitude, longitude, severity, and number_of_patients must be numeric"
        }), 400
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
        or not 1 <= severity <= 5
        or number_of_patients < 1
    ):
        return jsonify({
            "error": (
                "Coordinates must be valid; severity must be 1–5; "
                "number_of_patients must be at least 1"
            )
        }), 400
    incident_type = incident_type.strip()
    location = location.strip()

    incident_id, priority_score, priority = create_incident_record(
        incident_type,
        location,
        latitude,
        longitude,
        severity,
        number_of_patients,
    )

    return jsonify({
        "message": "Incident created successfully",
        "incident_id": incident_id,
        "priority_score": priority_score,
        "priority": priority,
    }), 201


# -----------------------------
# GET ALL INCIDENTS
# -----------------------------

@app.route("/incidents", methods=["GET"])
@admin_api_login_required
def get_incidents():
    connection = get_database_connection()
    incidents = connection.execute(
        "SELECT * FROM incidents ORDER BY created_at DESC"
    ).fetchall()
    connection.close()
    return jsonify([dict(incident) for incident in incidents])


@app.route("/select-ambulance/<int:incident_id>", methods=["GET"])
@admin_api_login_required
def select_ambulance(incident_id):
    connection = get_database_connection()

    incident = connection.execute(
        "SELECT * FROM incidents WHERE id = ?",
        (incident_id,),
    ).fetchone()

    if not incident:
        connection.close()
        return jsonify({"error": "Incident not found"}), 404

    _, ambulances = _load_available_unassigned_ambulances(connection)
    connection.close()

    result = select_best_ambulance(
        incident["latitude"],
        incident["longitude"],
        ambulances,
    )

    if result is None:
        return jsonify({"message": "No available ambulance found"}), 404

    return jsonify({
        "incident_id": incident_id,
        "best_ambulance": result,
    })


@app.route("/assign-ambulance/<int:incident_id>", methods=["POST"])
@admin_api_login_required
def assign_ambulance(incident_id):
    payload, error, status = create_assignment_record(incident_id)
    if error:
        return jsonify({"error": error}), status
    payload["message"] = "Ambulance and hospital assigned successfully"
    return jsonify(payload), status


@app.route("/select-hospital/<int:incident_id>", methods=["GET"])
@admin_api_login_required
def select_hospital(incident_id):
    connection = get_database_connection()

    incident = connection.execute(
        "SELECT * FROM incidents WHERE id = ?",
        (incident_id,),
    ).fetchone()

    if not incident:
        connection.close()
        return jsonify({"error": "Incident not found"}), 404

    hospitals = connection.execute("SELECT * FROM hospitals").fetchall()
    connection.close()

    hospitals = [dict(hospital) for hospital in hospitals]

    result = select_best_hospital(
        incident["latitude"],
        incident["longitude"],
        hospitals,
    )

    if result is None:
        return jsonify({"message": "No suitable hospital found"}), 404

    return jsonify({
        "incident_id": incident_id,
        "best_hospital": result,
    })


@app.route("/create-assignment/<int:incident_id>", methods=["POST"])
@admin_api_login_required
def create_assignment(incident_id):
    payload, error, status = create_assignment_record(incident_id)

    if error:
        return jsonify({"error": error}), status

    return jsonify(payload), status


@app.route("/assignments", methods=["GET"])
@admin_api_login_required
def get_assignments():
    connection = get_database_connection()

    assignments = connection.execute(
        """
        SELECT
            assignments.*,
            ambulances.latitude AS ambulance_latitude,
            ambulances.longitude AS ambulance_longitude,
            hospitals.latitude AS hospital_latitude,
            hospitals.longitude AS hospital_longitude,
            incidents.latitude AS incident_latitude,
            incidents.longitude AS incident_longitude
        FROM assignments
        JOIN ambulances
            ON assignments.ambulance_id = ambulances.id
        JOIN hospitals
            ON assignments.hospital_id = hospitals.id
        JOIN incidents
            ON assignments.incident_id = incidents.id
        ORDER BY assignments.id DESC
        """
    ).fetchall()

    connection.close()

    result = []

    for assignment in assignments:
        assignment_data = dict(assignment)

        ambulance_route = get_route(
            assignment["ambulance_latitude"],
            assignment["ambulance_longitude"],
            assignment["incident_latitude"],
            assignment["incident_longitude"],
        )

        hospital_route = get_route(
            assignment["incident_latitude"],
            assignment["incident_longitude"],
            assignment["hospital_latitude"],
            assignment["hospital_longitude"],
        )

        assignment_data["ambulance_route"] = (
            ambulance_route["geometry"] if ambulance_route else []
        )
        assignment_data["hospital_route"] = (
            hospital_route["geometry"] if hospital_route else []
        )

        result.append(assignment_data)

    return jsonify(result)


_ensure_schema()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
    )
