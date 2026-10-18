"""Obtain GPS coordinates for a confirmed crash alert."""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from mobile import config


class LiveGPSLocation:
    """Plyer Android GPS stream. This provider never substitutes demo coordinates."""

    def __init__(self):
        self._lock = threading.Lock()
        self._fix: dict[str, Any] | None = None
        self._gps = None
        self._status_callback = None

    def start(self, status_callback=None) -> None:
        if self._gps is not None:
            return
        self._status_callback = status_callback
        try:
            from kivy.utils import platform

            if platform != "android":
                self._report("Real GPS is available only in the Android app.")
                return
            from android.permissions import Permission, request_permissions

            request_permissions(
                [Permission.ACCESS_FINE_LOCATION, Permission.ACCESS_COARSE_LOCATION],
                self._on_permissions,
            )
        except (ImportError, RuntimeError) as error:
            self._report(f"Unable to start Android GPS: {error}")

    def stop(self) -> None:
        if self._gps is not None:
            try:
                self._gps.stop()
            except Exception:
                pass
            self._gps = None

    def latest_fix(self, *, max_age_seconds: float = 30.0) -> dict[str, Any] | None:
        with self._lock:
            fix = dict(self._fix) if self._fix else None
        if fix is None or time.monotonic() - fix.pop("_received_at") > max_age_seconds:
            return None
        return fix

    def _on_permissions(self, permissions, grants) -> None:
        if not grants or not all(grants):
            self._report("Location permission is required for real-device alerts.")
            return
        try:
            from plyer import gps

            self._gps = gps
            gps.configure(on_location=self._on_location, on_status=self._on_gps_status)
            gps.start(minTime=1000, minDistance=1)
            self._report("Waiting for a current GPS fix...")
        except Exception as error:
            self._report(f"Unable to start GPS provider: {error}")

    def _on_location(self, **kwargs) -> None:
        try:
            latitude = float(kwargs["lat"])
            longitude = float(kwargs["lon"])
        except (KeyError, TypeError, ValueError):
            self._report("GPS returned an invalid location fix.")
            return
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            self._report("GPS returned an invalid location fix.")
            return

        speed = kwargs.get("speed")
        try:
            speed = float(speed) if speed is not None else None
        except (TypeError, ValueError):
            speed = None
        if speed is not None and (not math.isfinite(speed) or speed < 0):
            speed = None
        accuracy = kwargs.get("accuracy")
        try:
            accuracy = float(accuracy) if accuracy is not None else None
        except (TypeError, ValueError):
            accuracy = None
        if accuracy is not None and (not math.isfinite(accuracy) or accuracy < 0):
            accuracy = None

        with self._lock:
            self._fix = {
                "latitude": latitude,
                "longitude": longitude,
                "accuracy": accuracy,
                "location": "Phone GPS location",
                "speed": speed,
                "_received_at": time.monotonic(),
            }
        self._report("GPS fix received.")

    def _on_gps_status(self, status_type, status) -> None:
        self._report(f"GPS {status_type}: {status}")

    def _report(self, message: str) -> None:
        if self._status_callback:
            self._status_callback(message)


def get_phone_gps(override: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Automatically obtain phone GPS.

    In the prototype, live OS GPS is optional. Override values or demo
    defaults are used so the emergency pipeline can always receive a fix.
    """
    if override:
        return {
            "latitude": float(override["latitude"]),
            "longitude": float(override["longitude"]),
            "location": override.get(
                "location",
                f"Victim phone GPS ({override['latitude']}, {override['longitude']})",
            ),
            "accuracy": override.get("accuracy"),
            "source": override.get("source", "override"),
        }

    # Attempt plyer if available (Android/iOS via Kivy ecosystem)
    try:
        from plyer import gps  # type: ignore

        # plyer GPS is async on device; prototype falls through to defaults
        _ = gps
    except Exception:
        pass

    return {
        "latitude": float(config.DEFAULT_LATITUDE),
        "longitude": float(config.DEFAULT_LONGITUDE),
        "accuracy": None,
        "location": config.DEFAULT_LOCATION_LABEL,
        "source": "demo_default_gps",
    }
