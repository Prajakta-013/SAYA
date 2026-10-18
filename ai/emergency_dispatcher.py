"""
Connect accident / phone-crash detection events to the EXISTING LIFELINE pipeline.

Does NOT reimplement priority, ambulance, hospital, routing, or assignment logic.
Calls create_incident_record / create_assignment_record from the Flask app.
"""

from __future__ import annotations

from typing import Any, Callable

LAST_AUTO_RESPONSE: dict[str, Any] | None = None


def get_last_auto_response() -> dict[str, Any] | None:
    return LAST_AUTO_RESPONSE


def set_last_auto_response(payload: dict[str, Any] | None) -> None:
    global LAST_AUTO_RESPONSE
    LAST_AUTO_RESPONSE = payload


def _confidence_from_detection(detection: dict[str, Any]) -> Any:
    if detection.get("crash_confidence") is not None:
        return detection.get("crash_confidence")
    return detection.get("confidence")


def dispatch_detected_accident(
    detection: dict[str, Any],
    create_incident_fn: Callable | None = None,
    create_assignment_fn: Callable | None = None,
) -> dict[str, Any]:
    """
    accident detected → create incident → priority → ambulance → hospital →
    routes → assignment.
    """
    if not detection.get("accident_detected"):
        result = {
            "success": False,
            "emergency_status": "NO_ACCIDENT",
            "message": "No accident detected — emergency pipeline not started",
            "detection": detection,
            "detection_source": detection.get("detection_source"),
            "crash_confidence": _confidence_from_detection(detection),
            "confidence": _confidence_from_detection(detection),
            "location": detection.get("location"),
            "severity": detection.get("severity"),
            "ambulance": None,
            "hospital": None,
            "ambulance_eta": None,
            "hospital_eta": None,
        }
        set_last_auto_response(result)
        return result

    if create_incident_fn is None or create_assignment_fn is None:
        from app import create_assignment_record, create_incident_record

        create_incident_fn = create_incident_fn or create_incident_record
        create_assignment_fn = create_assignment_fn or create_assignment_record

    patients = detection.get("number_of_patients", 1)
    try:
        incident_id, priority_score, priority = create_incident_fn(
            detection["incident_type"],
            detection["location"],
            detection["latitude"],
            detection["longitude"],
            detection["severity"],
            patients,
            detection_source=detection.get("detection_source"),
            crash_confidence=_confidence_from_detection(detection),
            event_id=detection.get("event_id"),
            detection_timestamp=detection.get("timestamp"),
            gps_accuracy=detection.get("gps_accuracy"),
            speed=detection.get("speed"),
        )
    except TypeError:
        incident_id, priority_score, priority = create_incident_fn(
            detection["incident_type"],
            detection["location"],
            detection["latitude"],
            detection["longitude"],
            detection["severity"],
            patients,
        )

    assignment, error, status_code = create_assignment_fn(incident_id)

    confidence = _confidence_from_detection(detection)
    detection_source = detection.get("detection_source") or detection.get("camera_id")

    if error:
        waiting = status_code == 503
        result = {
            "success": False,
            "emergency_status": "WAITING_FOR_RESOURCES" if waiting else "INCIDENT_CREATED_ASSIGNMENT_FAILED",
            "message": (
                f"{error}. Incident remains active and unassigned."
                if waiting else error
            ),
            "status_code": 202 if waiting else status_code,
            "detection": detection,
            "incident_id": incident_id,
            "priority": priority,
            "priority_score": priority_score,
            "camera_id": detection.get("camera_id"),
            "device_id": detection.get("device_id"),
            "detection_source": detection_source,
            "confidence": confidence,
            "crash_confidence": confidence,
            "location": detection.get("location"),
            "severity": detection.get("severity"),
            "ambulance": None,
            "hospital": None,
            "ambulance_eta": None,
            "hospital_eta": None,
        }
        set_last_auto_response(result)
        return result

    ambulance = assignment["ambulance"]
    hospital = assignment["hospital"]

    result = {
        "success": True,
        "emergency_status": "AUTO_DISPATCHED",
        "message": "Automatic emergency response initiated from accident detection",
        "status_code": status_code,
        "detection": detection,
        "incident_id": incident_id,
        "priority": priority,
        "priority_score": priority_score,
        "camera_id": detection.get("camera_id"),
        "device_id": detection.get("device_id"),
        "detection_source": detection_source,
        "confidence": confidence,
        "crash_confidence": confidence,
        "location": detection.get("location"),
        "severity": detection.get("severity"),
        "ambulance": ambulance.get("ambulance_number"),
        "ambulance_details": ambulance,
        "hospital": hospital.get("hospital_name"),
        "hospital_details": hospital,
        "ambulance_eta": assignment["ambulance_to_incident_eta_minutes"],
        "hospital_eta": assignment["incident_to_hospital_eta_minutes"],
        "ambulance_distance_km": assignment["ambulance_to_incident_distance_km"],
        "hospital_distance_km": assignment["incident_to_hospital_distance_km"],
        "assignment": assignment,
    }
    set_last_auto_response(result)
    return result


def dispatch_simulated_accident(
    camera_id: str | None = None,
    create_incident_fn: Callable | None = None,
    create_assignment_fn: Callable | None = None,
) -> dict[str, Any]:
    """Legacy CCTV simulator — kept for existing API tests."""
    from ai.accident_detector import DEFAULT_CAMERA_ID, simulate_cctv_accident

    detection = simulate_cctv_accident(camera_id=camera_id or DEFAULT_CAMERA_ID)
    return dispatch_detected_accident(
        detection,
        create_incident_fn=create_incident_fn,
        create_assignment_fn=create_assignment_fn,
    )


def dispatch_phone_crash_alert(
    alert: dict[str, Any],
    create_incident_fn: Callable | None = None,
    create_assignment_fn: Callable | None = None,
) -> dict[str, Any]:
    """
    Convert a victim-phone crash alert into the shared detection payload and
    run the existing emergency pipeline.
    """
    location = alert.get("location") or "Victim phone GPS location"
    detection = {
        "accident_detected": True,
        "crash_confidence": float(alert.get("crash_confidence", 0)),
        "confidence": float(alert.get("crash_confidence", 0)),
        "device_id": alert.get("device_id"),
        "event_id": alert.get("event_id"),
        "timestamp": alert.get("timestamp"),
        "location": location,
        "latitude": float(alert["latitude"]),
        "longitude": float(alert["longitude"]),
        "gps_accuracy": (
            float(alert["gps_accuracy"])
            if alert.get("gps_accuracy") is not None
            else None
        ),
        "incident_type": alert.get("incident_type") or "Road Accident",
        "severity": int(alert.get("severity", 3)),
        "number_of_patients": int(alert.get("number_of_patients", 1)),
        "detection_source": alert.get("detection_source") or "Victim Smartphone",
        "speed": alert.get("speed"),
    }
    return dispatch_detected_accident(
        detection,
        create_incident_fn=create_incident_fn,
        create_assignment_fn=create_assignment_fn,
    )
