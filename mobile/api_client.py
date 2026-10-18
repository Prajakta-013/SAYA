"""HTTP client that sends confirmed crash alerts to the Flask backend."""

from __future__ import annotations

from typing import Any

import requests

from mobile import config


def send_phone_crash_alert(payload: dict[str, Any], base_url: str | None = None) -> dict[str, Any]:
    """
    POST the alert to /phone-crash-alert.

    The Flask server runs the existing incident → priority → ambulance →
    hospital → routing → assignment pipeline.
    """
    url = (base_url or config.API_BASE_URL).rstrip("/") + config.PHONE_CRASH_ALERT_PATH
    try:
        response = requests.post(url, json=payload, timeout=15)
    except requests.RequestException:
        return {
            "success": False,
            "connection_error": True,
            "message": (
                "Unable to contact SAYA\n"
                "Check that:\n"
                "- Flask server is running\n"
                "- phone and PC are on the same network\n"
                "- configured backend URL is correct"
            ),
        }
    try:
        body = response.json()
    except ValueError:
        body = {"error": response.text}
    body["_http_status"] = response.status_code
    return body
