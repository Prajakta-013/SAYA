"""
AI-Assisted Crash Detection (PROTOTYPE).

Fuses phone sensors so a crash is NOT declared from a single signal:
  - accelerometer / impact
  - gyroscope / rotation
  - GPS speed reduction
  - post-impact inactivity

This does not claim to detect every real-world accident with certainty.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from mobile import config


@dataclass
class SensorSample:
    """One fused reading from the victim's phone."""

    accel_magnitude: float = 9.8  # m/s^2
    gyro_magnitude: float = 0.0  # rad/s
    speed_mps: float = 0.0  # m/s
    previous_speed_mps: float = 0.0  # m/s before this sample
    post_impact_idle: bool = False


@dataclass
class CrashAssessment:
    crash_detected: bool
    crash_confidence: float
    severity: int
    signals: dict[str, bool] = field(default_factory=dict)
    signal_scores: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    incident_type: str = "Road Accident"
    detection_source: str = "Victim Smartphone"

    def to_dict(self) -> dict[str, Any]:
        return {
            "crash_detected": self.crash_detected,
            "crash_confidence": round(self.crash_confidence, 3),
            "severity": self.severity,
            "signals": self.signals,
            "signal_scores": {k: round(v, 3) for k, v in self.signal_scores.items()},
            "reason": self.reason,
            "incident_type": self.incident_type,
            "detection_source": self.detection_source,
        }


def vector_magnitude(values: tuple[float, float, float]) -> float:
    return math.sqrt(sum(component * component for component in values))


class SensorFusionWindow:
    """Aggregate live device readings before evaluating the existing detector."""

    def __init__(self, *, window_seconds: float = 2.0):
        self.window_seconds = window_seconds
        self._acceleration: deque[tuple[float, float]] = deque()
        self._rotation: deque[tuple[float, float]] = deque()
        self._speeds: deque[tuple[float, float]] = deque()
        self._last_speed: float | None = None
        self._impact_time: float | None = None
        self._idle_samples = 0

    def reset(self) -> None:
        self._acceleration.clear()
        self._rotation.clear()
        self._speeds.clear()
        self._last_speed = None
        self._impact_time = None
        self._idle_samples = 0

    def update(
        self,
        acceleration: tuple[float, float, float],
        rotation: tuple[float, float, float],
        *,
        speed_mps: float | None = None,
        timestamp: float | None = None,
    ) -> SensorSample:
        now = time.monotonic() if timestamp is None else timestamp
        acceleration_magnitude = vector_magnitude(acceleration)
        rotation_magnitude = vector_magnitude(rotation)
        self._acceleration.append((now, acceleration_magnitude))
        self._rotation.append((now, rotation_magnitude))

        if speed_mps is not None and math.isfinite(speed_mps) and speed_mps >= 0:
            speed = float(speed_mps)
            self._speeds.append((now, speed))
            self._last_speed = speed

        self._discard_old(self._acceleration, now - self.window_seconds)
        self._discard_old(self._rotation, now - self.window_seconds)
        self._discard_old(self._speeds, now - 10.0)

        if acceleration_magnitude >= config.IMPACT_THRESHOLD_MS2:
            self._impact_time = now
            self._idle_samples = 0
        elif (
            self._impact_time is not None
            and now - self._impact_time <= 10.0
            and abs(acceleration_magnitude - 9.8) <= config.INACTIVITY_ACCEL_MAX_MS2
            and rotation_magnitude <= 1.0
        ):
            self._idle_samples += 1
        else:
            self._idle_samples = 0

        peak_acceleration = max(value for _, value in self._acceleration)
        peak_rotation = max(value for _, value in self._rotation)
        current_speed = self._last_speed or 0.0
        previous_speed = max((value for _, value in self._speeds), default=current_speed)

        return SensorSample(
            accel_magnitude=peak_acceleration,
            gyro_magnitude=peak_rotation,
            speed_mps=current_speed,
            previous_speed_mps=previous_speed,
            post_impact_idle=self._idle_samples >= config.INACTIVITY_WINDOW_SAMPLES,
        )

    @staticmethod
    def _discard_old(samples: deque[tuple[float, float]], cutoff: float) -> None:
        while samples and samples[0][0] < cutoff:
            samples.popleft()


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def evaluate_impact(accel_magnitude: float) -> tuple[bool, float]:
    if accel_magnitude < config.IMPACT_THRESHOLD_MS2:
        return False, 0.0
    # Scale how far above threshold the impact sits
    score = _clamp(
        (accel_magnitude - config.IMPACT_THRESHOLD_MS2)
        / (config.IMPACT_THRESHOLD_MS2 * 1.5)
    )
    return True, max(score, 0.35)


def evaluate_rotation(gyro_magnitude: float) -> tuple[bool, float]:
    if gyro_magnitude < config.ROTATION_THRESHOLD_RAD_S:
        return False, 0.0
    score = _clamp(
        (gyro_magnitude - config.ROTATION_THRESHOLD_RAD_S)
        / (config.ROTATION_THRESHOLD_RAD_S * 1.5)
    )
    return True, max(score, 0.3)


def evaluate_speed_drop(previous_speed: float, current_speed: float) -> tuple[bool, float]:
    drop = previous_speed - current_speed
    if drop < config.SPEED_DROP_THRESHOLD_MS:
        return False, 0.0
    score = _clamp(drop / (config.SPEED_DROP_THRESHOLD_MS * 2.0))
    return True, max(score, 0.3)


def evaluate_inactivity(post_impact_idle: bool, accel_magnitude: float) -> tuple[bool, float]:
    # Idle after a violent event: low motion near gravity or explicit flag
    near_still = abs(accel_magnitude - 9.8) <= config.INACTIVITY_ACCEL_MAX_MS2
    active = bool(post_impact_idle or near_still)
    if not active:
        return False, 0.0
    return True, 0.7 if post_impact_idle else 0.45


def estimate_severity(confidence: float, signal_scores: dict[str, float]) -> int:
    """Estimate 1–5 severity from evidence instead of always using 5."""
    impact = signal_scores.get("impact", 0.0)
    rotation = signal_scores.get("rotation", 0.0)
    speed = signal_scores.get("speed_drop", 0.0)
    evidence = 0.45 * confidence + 0.25 * impact + 0.15 * rotation + 0.15 * speed

    if evidence >= 0.85:
        return 5
    if evidence >= 0.7:
        return 4
    if evidence >= 0.55:
        return 3
    if evidence >= 0.4:
        return 2
    return 1


def assess_crash(sample: SensorSample) -> CrashAssessment:
    """
    Fuse sensors into a crash-confidence score.

    Requires at least MIN_SIGNALS_FOR_CRASH independent positive signals.
    """
    impact_hit, impact_score = evaluate_impact(sample.accel_magnitude)
    rotation_hit, rotation_score = evaluate_rotation(sample.gyro_magnitude)
    speed_hit, speed_score = evaluate_speed_drop(
        sample.previous_speed_mps, sample.speed_mps
    )
    idle_hit, idle_score = evaluate_inactivity(
        sample.post_impact_idle, sample.accel_magnitude
    )

    signals = {
        "impact": impact_hit,
        "rotation": rotation_hit,
        "speed_drop": speed_hit,
        "inactivity": idle_hit,
    }
    signal_scores = {
        "impact": impact_score,
        "rotation": rotation_score,
        "speed_drop": speed_score,
        "inactivity": idle_score,
    }

    confidence = (
        config.WEIGHT_IMPACT * impact_score
        + config.WEIGHT_ROTATION * rotation_score
        + config.WEIGHT_SPEED_DROP * speed_score
        + config.WEIGHT_INACTIVITY * idle_score
    )
    confidence = _clamp(confidence)

    positive = sum(1 for hit in signals.values() if hit)
    crash_detected = (
        positive >= config.MIN_SIGNALS_FOR_CRASH
        and confidence >= config.CRASH_CONFIDENCE_THRESHOLD
    )

    severity = estimate_severity(confidence, signal_scores) if crash_detected else 1

    if crash_detected:
        active = [name for name, hit in signals.items() if hit]
        reason = (
            f"AI-Assisted Crash Detection (prototype): "
            f"{positive} signals ({', '.join(active)}), "
            f"confidence={confidence:.2f}"
        )
    elif positive < config.MIN_SIGNALS_FOR_CRASH:
        reason = (
            f"Insufficient sensor agreement ({positive}/"
            f"{config.MIN_SIGNALS_FOR_CRASH} signals)"
        )
    else:
        reason = (
            f"Confidence {confidence:.2f} below threshold "
            f"{config.CRASH_CONFIDENCE_THRESHOLD}"
        )

    return CrashAssessment(
        crash_detected=crash_detected,
        crash_confidence=confidence,
        severity=severity,
        signals=signals,
        signal_scores=signal_scores,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# SAFE DEMO sensor scenarios (sensor layer only — not final incidents)
# ---------------------------------------------------------------------------

DEMO_SCENARIOS: dict[str, dict[str, Any]] = {
    "NORMAL_DRIVING": {
        "label": "Normal driving",
        "description": "Normal acceleration, rotation, and speed — no crash",
        "sample": SensorSample(
            accel_magnitude=10.2,
            gyro_magnitude=0.4,
            previous_speed_mps=14.0,
            speed_mps=13.5,
            post_impact_idle=False,
        ),
        "gps": {
            "latitude": 19.0760,
            "longitude": 72.8777,
            "location": "Victim phone GPS — Bandra-Worli Sea Link approach, Mumbai",
            "source": "demo_scenario_gps",
        },
    },
    "HARD_BRAKING": {
        "label": "Hard braking",
        "description": "Significant speed reduction without strong impact/rotation",
        "sample": SensorSample(
            # Elevated longitudinal braking accel, but below impact threshold
            accel_magnitude=18.0,
            gyro_magnitude=0.8,
            previous_speed_mps=24.0,
            speed_mps=6.0,
            post_impact_idle=False,
        ),
        "gps": {
            "latitude": 19.0596,
            "longitude": 72.8295,
            "location": "Victim phone GPS — SV Road, Bandra West, Mumbai",
            "source": "demo_scenario_gps",
        },
    },
    "MINOR_IMPACT": {
        "label": "Minor impact",
        "description": "Moderate impact, rotation, and speed drop — lower confidence",
        "sample": SensorSample(
            accel_magnitude=40.0,
            gyro_magnitude=7.0,
            previous_speed_mps=20.0,
            speed_mps=2.0,
            post_impact_idle=True,
        ),
        "gps": {
            "latitude": 19.1176,
            "longitude": 72.9060,
            "location": "Victim phone GPS — Powai Lake Road, Mumbai",
            "source": "demo_scenario_gps",
        },
    },
    "MAJOR_CRASH": {
        "label": "Major crash",
        "description": "Strong impact, rotation, speed collapse, and inactivity",
        "sample": SensorSample(
            accel_magnitude=55.0,
            gyro_magnitude=10.0,
            previous_speed_mps=28.0,
            speed_mps=1.0,
            post_impact_idle=True,
        ),
        "gps": {
            "latitude": 19.0896,
            "longitude": 72.8656,
            "location": "Victim phone GPS — Eastern Express Highway, Mumbai",
            "source": "demo_scenario_gps",
        },
    },
}

DEFAULT_DEMO_SCENARIO = "MAJOR_CRASH"


def list_demo_scenarios() -> list[str]:
    return list(DEMO_SCENARIOS.keys())


def get_demo_scenario(name: str | None = None) -> dict[str, Any]:
    """
    Return a named SAFE DEMO sensor scenario.

    Always returns sensors + optional GPS for the demo — never an assignment.
    """
    key = (name or DEFAULT_DEMO_SCENARIO).strip().upper()
    if key not in DEMO_SCENARIOS:
        key = DEFAULT_DEMO_SCENARIO
    scenario = DEMO_SCENARIOS[key]
    return {
        "name": key,
        "label": scenario["label"],
        "description": scenario["description"],
        "sample": scenario["sample"],
        "gps": dict(scenario["gps"]),
    }


def simulate_crash_sensor_bundle() -> SensorSample:
    """Backward-compatible alias for the MAJOR_CRASH demo sensors."""
    return get_demo_scenario("MAJOR_CRASH")["sample"]


def simulate_normal_driving_sample() -> SensorSample:
    """Backward-compatible alias for NORMAL_DRIVING demo sensors."""
    return get_demo_scenario("NORMAL_DRIVING")["sample"]


def simulate_hard_braking_sample() -> SensorSample:
    return get_demo_scenario("HARD_BRAKING")["sample"]


def simulate_minor_impact_sample() -> SensorSample:
    return get_demo_scenario("MINOR_IMPACT")["sample"]
