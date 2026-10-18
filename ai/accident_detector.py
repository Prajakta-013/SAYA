"""
PROTOTYPE accident detector — NOT production-grade AI.

Uses OpenCV to measure sudden significant scene / motion change between
video frames. A large frame-difference spike is treated as a possible
accident for demo purposes only. Do not rely on this for real emergencies.
"""

from __future__ import annotations

from typing import Any

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - exercised when OpenCV missing
    cv2 = None
    np = None


# Default CCTV camera metadata used when an accident is simulated / detected.
CAMERA_REGISTRY = {
    "CAM-MUM-01": {
        "location": "Eastern Express Highway, Mumbai",
        "latitude": 19.0896,
        "longitude": 72.8656,
    },
    "CAM-MUM-02": {
        "location": "Sion Circle Junction, Mumbai",
        "latitude": 19.0433,
        "longitude": 72.8631,
    },
    "CAM-MUM-03": {
        "location": "Andheri Kurla Road, Mumbai",
        "latitude": 19.1176,
        "longitude": 72.9060,
    },
}

DEFAULT_CAMERA_ID = "CAM-MUM-01"
MOTION_THRESHOLD = 28.0
CONFIDENCE_CAP = 0.95


def _camera_meta(camera_id: str) -> dict[str, Any]:
    return CAMERA_REGISTRY.get(camera_id, CAMERA_REGISTRY[DEFAULT_CAMERA_ID])


def build_detection_event(
    *,
    accident_detected: bool,
    confidence: float,
    camera_id: str = DEFAULT_CAMERA_ID,
    incident_type: str = "Road Accident",
    severity: int = 5,
    number_of_patients: int = 1,
    location: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, Any]:
    """Return a normalized accident-detection payload."""
    meta = _camera_meta(camera_id)
    return {
        "accident_detected": bool(accident_detected),
        "confidence": round(float(confidence), 3),
        "camera_id": camera_id,
        "location": location or meta["location"],
        "latitude": float(latitude if latitude is not None else meta["latitude"]),
        "longitude": float(longitude if longitude is not None else meta["longitude"]),
        "incident_type": incident_type,
        "severity": int(severity),
        "number_of_patients": int(number_of_patients),
        "detector": "prototype_opencv_scene_change",
        "note": "PROTOTYPE only — not production-grade accident AI",
    }


def simulate_cctv_accident(
    camera_id: str = DEFAULT_CAMERA_ID,
    confidence: float = 0.91,
) -> dict[str, Any]:
    """
    Simulate a CCTV camera reporting an unconscious-victim road accident.

    Used by the dashboard "SIMULATE CCTV ACCIDENT" control and by
    POST /simulate-accident. Does not require a live video feed.
    """
    return build_detection_event(
        accident_detected=True,
        confidence=confidence,
        camera_id=camera_id,
        incident_type="Road Accident",
        severity=5,
        number_of_patients=1,
    )


def _mean_frame_diff(prev_gray, gray) -> float:
    diff = cv2.absdiff(prev_gray, gray)
    return float(np.mean(diff))


def detect_accident_in_video(
    video_source,
    camera_id: str = DEFAULT_CAMERA_ID,
    motion_threshold: float = MOTION_THRESHOLD,
    max_frames: int = 90,
) -> dict[str, Any]:
    """
    Process a video file path or camera index with a prototype motion check.

    Returns a detection event. If OpenCV is unavailable or the source cannot
    be opened, returns accident_detected=False with an explanatory note.
    """
    if cv2 is None:
        event = build_detection_event(
            accident_detected=False,
            confidence=0.0,
            camera_id=camera_id,
        )
        event["error"] = "OpenCV (cv2) is not installed"
        return event

    capture = cv2.VideoCapture(video_source)
    if not capture.isOpened():
        event = build_detection_event(
            accident_detected=False,
            confidence=0.0,
            camera_id=camera_id,
        )
        event["error"] = f"Unable to open video source: {video_source}"
        return event

    prev_gray = None
    peak_motion = 0.0
    frames_read = 0

    try:
        while frames_read < max_frames:
            ok, frame = capture.read()
            if not ok:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21, 21), 0)

            if prev_gray is not None:
                motion = _mean_frame_diff(prev_gray, gray)
                if motion > peak_motion:
                    peak_motion = motion

            prev_gray = gray
            frames_read += 1
    finally:
        capture.release()

    accident_detected = peak_motion >= motion_threshold
    confidence = 0.0
    if accident_detected:
        confidence = min(CONFIDENCE_CAP, peak_motion / (motion_threshold * 2.5))

    event = build_detection_event(
        accident_detected=accident_detected,
        confidence=confidence,
        camera_id=camera_id,
        severity=5 if accident_detected else 1,
        number_of_patients=1 if accident_detected else 0,
    )
    event["peak_motion_score"] = round(peak_motion, 2)
    event["frames_analyzed"] = frames_read
    return event


def detect_accident_from_frame_pair(
    frame_a,
    frame_b,
    camera_id: str = DEFAULT_CAMERA_ID,
    motion_threshold: float = MOTION_THRESHOLD,
) -> dict[str, Any]:
    """
    Compare two BGR frames (numpy arrays) for sudden scene change.

    Useful for unit tests without a video file.
    """
    if cv2 is None or np is None:
        event = build_detection_event(
            accident_detected=False,
            confidence=0.0,
            camera_id=camera_id,
        )
        event["error"] = "OpenCV (cv2) is not installed"
        return event

    gray_a = cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)
    gray_a = cv2.GaussianBlur(gray_a, (21, 21), 0)
    gray_b = cv2.GaussianBlur(gray_b, (21, 21), 0)

    motion = _mean_frame_diff(gray_a, gray_b)
    accident_detected = motion >= motion_threshold
    confidence = 0.0
    if accident_detected:
        confidence = min(CONFIDENCE_CAP, motion / (motion_threshold * 2.5))

    event = build_detection_event(
        accident_detected=accident_detected,
        confidence=confidence,
        camera_id=camera_id,
        severity=5 if accident_detected else 1,
        number_of_patients=1 if accident_detected else 0,
    )
    event["peak_motion_score"] = round(motion, 2)
    return event
