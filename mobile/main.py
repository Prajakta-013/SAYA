"""
SAYA victim-phone prototype.

AI-Assisted Crash Detection (prototype) — not a guarantee of real-world accuracy.

Flow:
  MONITORING → crash suspected → 10s cancel countdown → GPS → /phone-crash-alert
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mobile import config
from mobile.api_client import send_phone_crash_alert
from mobile.android_device import AndroidSensorSource
from mobile.crash_detector import (
    CrashAssessment,
    SensorSample,
    assess_crash,
    get_demo_scenario,
    simulate_normal_driving_sample,
)
from mobile.location_service import LiveGPSLocation, get_phone_gps


def get_or_create_device_id(storage_directory: str) -> str:
    device_file = Path(storage_directory) / "device_id"
    try:
        device_file.parent.mkdir(parents=True, exist_ok=True)
        existing = device_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    except OSError:
        return str(uuid.uuid4())
    if existing:
        return existing
    device_id = str(uuid.uuid4())
    try:
        device_file.write_text(device_id, encoding="utf-8")
    except OSError:
        pass
    return device_id


def build_alert_payload(
    assessment: CrashAssessment,
    gps: dict[str, Any],
    *,
    device_id: str | None = None,
    speed_mps: float | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    return {
        "device_id": device_id or config.DEVICE_ID,
        "event_id": event_id or str(uuid.uuid4()),
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "latitude": gps["latitude"],
        "longitude": gps["longitude"],
        "gps_accuracy": gps.get("accuracy"),
        "location": gps.get("location"),
        "speed": speed_mps,
        "crash_confidence": assessment.crash_confidence,
        "severity": assessment.severity,
        "incident_type": "Road Accident",
        "detection_source": "Victim Smartphone",
        "number_of_patients": 1,
        "signals": assessment.signals,
    }


def run_countdown(
    seconds: int = config.CANCEL_COUNTDOWN_SECONDS,
    *,
    cancelled_check: Callable[[], bool] | None = None,
    tick_callback: Callable[[int], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    """
    Return True if the victim cancelled, False if countdown completed.
    """
    for remaining in range(seconds, 0, -1):
        if cancelled_check and cancelled_check():
            return True
        if tick_callback:
            tick_callback(remaining)
        sleep_fn(1)
    if cancelled_check and cancelled_check():
        return True
    return False


def process_sensor_sample(
    sample: SensorSample,
    *,
    auto_confirm: bool = False,
    cancelled: bool = False,
    skip_countdown: bool = False,
    send_alert: bool = True,
    base_url: str | None = None,
    gps_override: dict[str, Any] | None = None,
    countdown_seconds: int = config.CANCEL_COUNTDOWN_SECONDS,
    sleep_fn: Callable[[float], None] | None = None,
    device_id: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """
    Full phone-side flow for one sensor sample.

    auto_confirm / skip_countdown: used by SAFE DEMO and automated tests.
    cancelled=True: victim pressed Cancel during countdown.
    """
    assessment = assess_crash(sample)
    result: dict[str, Any] = {
        "phase": "MONITORING",
        "assessment": assessment.to_dict(),
        "alert_sent": False,
        "cancelled": False,
        "server_response": None,
    }

    if not assessment.crash_detected:
        result["phase"] = "MONITORING"
        result["message"] = assessment.reason
        return result

    result["phase"] = "COUNTDOWN"
    result["message"] = (
        "Possible crash detected. Emergency response will start in "
        f"{countdown_seconds} seconds. Cancel if you are safe."
    )

    if cancelled:
        result["phase"] = "CANCELLED"
        result["cancelled"] = True
        result["message"] = "Victim cancelled — no emergency alert sent"
        return result

    if not skip_countdown and not auto_confirm:
        was_cancelled = run_countdown(
            countdown_seconds,
            cancelled_check=lambda: cancelled,
            sleep_fn=sleep_fn or time.sleep,
        )
        if was_cancelled:
            result["phase"] = "CANCELLED"
            result["cancelled"] = True
            result["message"] = "Victim cancelled — no emergency alert sent"
            return result

    gps = get_phone_gps(gps_override)
    payload = build_alert_payload(
        assessment,
        gps,
        device_id=device_id,
        speed_mps=sample.speed_mps,
        event_id=event_id,
    )
    result["payload"] = payload
    result["phase"] = "ALERTING"
    result["gps"] = gps

    if not send_alert:
        result["message"] = "GPS obtained; alert not sent (dry run)"
        return result

    server_response = send_phone_crash_alert(payload, base_url=base_url)
    result["server_response"] = server_response
    result["alert_sent"] = server_response.get("success", False) or server_response.get(
        "_http_status"
    ) in (200, 201, 202) or (
        server_response.get("_http_status") == 409
        and server_response.get("already_received", False)
    )
    result["phase"] = "DISPATCHED" if result["alert_sent"] else "ALERT_FAILED"
    result["message"] = server_response.get(
        "message", "Phone crash alert processed by SAYA"
    )
    return result


def run_safe_demo(
    *,
    scenario: str = "MAJOR_CRASH",
    cancel: bool = False,
    base_url: str | None = None,
    send_alert: bool = True,
    skip_countdown: bool = True,
) -> dict[str, Any]:
    """
    SAFE DEMO MODE:

    Named sensor scenario → same crash detector → countdown (optional) →
    scenario GPS → SAME /phone-crash-alert endpoint.
    """
    import uuid

    demo = get_demo_scenario(scenario)
    result = process_sensor_sample(
        demo["sample"],
        auto_confirm=True,
        cancelled=cancel,
        skip_countdown=skip_countdown,
        send_alert=send_alert and not cancel,
        base_url=base_url,
        gps_override=demo["gps"],
        device_id=f"{config.DEVICE_ID}-DEMO-{uuid.uuid4().hex[:8]}",
        event_id=str(uuid.uuid4()),
    )
    result["scenario"] = demo["name"]
    result["scenario_label"] = demo["label"]
    return result


def run_kivy_app() -> None:
    """Launch the real-device Android sensor UI; safe demo remains CLI-only."""
    try:
        from kivy.app import App
        from kivy.core.window import Window
        from kivy.clock import Clock
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.label import Label
        from kivy.utils import platform
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Kivy is required for the phone UI. Install with: pip install kivy\n"
            f"Or run safe demo without UI: python -m mobile.main --demo\n({exc})"
        ) from exc

    Window.clearcolor = (0.965, 0.94, 0.9, 1)

    class SayaPhoneRoot(BoxLayout):
        def __init__(self, **kwargs):
            super().__init__(orientation="vertical", padding=28, spacing=14, **kwargs)
            self.app_platform = platform
            self.phase = "STARTING"
            self.device_id = None
            self.event_id = None
            self.assessment = None
            self.payload = None
            self.countdown_event = None
            self.remaining = config.CANCEL_COUNTDOWN_SECONDS
            self.gps = LiveGPSLocation()
            self.sensors = AndroidSensorSource(
                self.gps,
                self._on_sensor_sample,
                self._on_device_error,
            )

            self.brand = Label(
                text="[b]SAYA[/b]\nHelp That Stays With You.",
                markup=True,
                color=(0.29, 0.11, 0.16, 1),
                font_size="26sp",
                size_hint_y=None,
                height="100dp",
                halign="center",
                valign="middle",
            )
            self.brand.bind(size=self.brand.setter("text_size"))
            self.add_widget(self.brand)

            self.headline = Label(
                text="Monitoring for accidents...",
                color=(0.16, 0.12, 0.1, 1),
                font_size="23sp",
                size_hint_y=None,
                height="84dp",
                halign="center",
                valign="middle",
            )
            self.headline.bind(size=self.headline.setter("text_size"))
            self.add_widget(self.headline)

            self.message = Label(
                text="REAL DEVICE MODE",
                color=(0.35, 0.29, 0.25, 1),
                font_size="17sp",
                halign="center",
                valign="middle",
            )
            self.message.bind(size=self.message.setter("text_size"))
            self.add_widget(self.message)

            self.countdown = Label(
                text="",
                color=(0.42, 0.13, 0.2, 1),
                font_size="48sp",
                size_hint_y=None,
                height="90dp",
                halign="center",
                valign="middle",
            )
            self.add_widget(self.countdown)

            self.cancel_button = Button(
                text="I'm OK — CANCEL",
                size_hint_y=None,
                height="58dp",
                disabled=True,
                background_normal="",
                background_color=(0.42, 0.13, 0.2, 1),
            )
            self.cancel_button.bind(on_press=self.cancel_alert)
            self.add_widget(self.cancel_button)

            self.action_button = Button(
                text="",
                size_hint_y=None,
                height="54dp",
                disabled=True,
                opacity=0,
            )
            self.action_button.bind(on_press=self.handle_action)
            self.add_widget(self.action_button)

        def start_real_monitoring(self) -> None:
            if self.app_platform != "android":
                self.phase = "UNAVAILABLE"
                self.headline.text = "REAL DEVICE MODE"
                self.message.text = "Install this app on Android to read physical sensors and GPS."
                return
            self.device_id = get_or_create_device_id(App.get_running_app().user_data_dir)
            self.gps.start(status_callback=self._on_gps_status)
            self._start_sensor_monitoring()

        def _start_sensor_monitoring(self) -> None:
            self.phase = "MONITORING"
            self.headline.text = "Monitoring for accidents..."
            self.message.text = "REAL DEVICE MODE · accelerometer · gyroscope · GPS"
            self.countdown.text = ""
            self.cancel_button.disabled = True
            self._hide_action()
            self.sensors.start()

        def _on_sensor_sample(self, _sample: SensorSample, assessment: CrashAssessment) -> None:
            if self.phase != "MONITORING" or not assessment.crash_detected:
                return
            self.phase = "COUNTDOWN"
            self.assessment = assessment
            self.event_id = str(uuid.uuid4())
            self.sensors.stop()
            self.remaining = config.CANCEL_COUNTDOWN_SECONDS
            self.headline.text = "POSSIBLE ACCIDENT DETECTED"
            self.message.text = (
                "Are you okay?\n\n"
                "Emergency alert will be sent in 10 seconds if no response."
            )
            self.countdown.text = "[ 10 ]"
            self.cancel_button.disabled = False
            self.countdown_event = Clock.schedule_interval(self._tick_countdown, 1.0)

        def _tick_countdown(self, _dt) -> bool:
            self.remaining -= 1
            if self.remaining > 0:
                self.countdown.text = f"[ {self.remaining} ]"
                return True
            self.countdown_event = None
            self.cancel_button.disabled = True
            self.phase = "SENDING"
            self.headline.text = "SENDING EMERGENCY ALERT"
            self.message.text = "Contacting SAYA..."
            threading.Thread(target=self._send_alert, daemon=True).start()
            return False

        def cancel_alert(self, *_args) -> None:
            if self.phase != "COUNTDOWN":
                return
            if self.countdown_event is not None:
                self.countdown_event.cancel()
                self.countdown_event = None
            self.assessment = None
            self.event_id = None
            self._start_sensor_monitoring()

        def _send_alert(self) -> None:
            gps_fix = self.gps.latest_fix(max_age_seconds=30.0)
            if gps_fix is None:
                Clock.schedule_once(self._show_missing_gps)
                return
            self.payload = build_alert_payload(
                self.assessment,
                gps_fix,
                device_id=self.device_id,
                speed_mps=gps_fix.get("speed"),
                event_id=self.event_id,
            )
            result = send_phone_crash_alert(self.payload)
            Clock.schedule_once(lambda _dt: self._show_alert_result(result))

        def _show_missing_gps(self, _dt) -> None:
            self.phase = "GPS_MISSING"
            self.headline.text = "CURRENT GPS FIX UNAVAILABLE"
            self.message.text = "No emergency was sent. Move to an open area and try again."
            self._show_action("RETRY GPS")

        def _show_alert_result(self, result: dict[str, Any]) -> None:
            status_code = result.get("_http_status")
            accepted = (
                status_code in (201, 202)
                or (status_code == 409 and result.get("incident_id") is not None)
                or result.get("success") is True
            )
            if accepted:
                self.phase = "SENT"
                self.headline.text = "EMERGENCY ALERT SENT"
                self.message.text = (
                    "SAYA has received your location.\n\n"
                    f"Incident ID: {result.get('incident_id', 'received')}"
                )
                if result.get("emergency_status") == "WAITING_FOR_RESOURCES":
                    self.message.text += "\nWaiting for emergency resources."
                self._show_action("RETURN TO MONITORING")
                return

            self.phase = "FAILED"
            if result.get("connection_error"):
                self.headline.text = "Unable to contact SAYA"
                self.message.text = result["message"]
                self._show_action("RETRY ALERT")
            else:
                self.headline.text = "ALERT NOT CONFIRMED"
                self.message.text = result.get(
                    "message", "SAYA could not confirm receipt. Please contact emergency services."
                )
                self._show_action("RETRY ALERT")

        def _retry_alert(self) -> None:
            if self.payload is None:
                self._retry_gps()
                return
            self.phase = "SENDING"
            self.headline.text = "SENDING EMERGENCY ALERT"
            self.message.text = "Contacting SAYA..."
            self._hide_action()
            threading.Thread(
                target=self._post_existing_payload,
                daemon=True,
            ).start()

        def _post_existing_payload(self) -> None:
            result = send_phone_crash_alert(self.payload)
            Clock.schedule_once(lambda _dt: self._show_alert_result(result))

        def _retry_gps(self) -> None:
            self.phase = "SENDING"
            self.headline.text = "SENDING EMERGENCY ALERT"
            self.message.text = "Waiting for a current GPS fix..."
            threading.Thread(target=self._send_alert, daemon=True).start()

        def handle_action(self, *_args) -> None:
            if self.phase == "FAILED":
                self._retry_alert()
            elif self.phase == "GPS_MISSING":
                self._retry_gps()
            elif self.phase == "SENT":
                self.assessment = None
                self.event_id = None
                self.payload = None
                self._start_sensor_monitoring()

        def _show_action(self, label: str) -> None:
            self.action_button.text = label
            self.action_button.disabled = False
            self.action_button.opacity = 1

        def _hide_action(self) -> None:
            self.action_button.text = ""
            self.action_button.disabled = True
            self.action_button.opacity = 0

        def _on_gps_status(self, message: str) -> None:
            Clock.schedule_once(lambda _dt: self._set_gps_status(message))

        def _set_gps_status(self, message: str) -> None:
            if self.phase == "MONITORING":
                self.message.text = f"REAL DEVICE MODE · {message}"

        def _on_device_error(self, message: str) -> None:
            Clock.schedule_once(lambda _dt: self._show_device_error(message))

        def _show_device_error(self, message: str) -> None:
            if self.phase == "MONITORING":
                self.headline.text = "REAL DEVICE MODE UNAVAILABLE"
                self.message.text = message

        def shutdown(self) -> None:
            if self.countdown_event is not None:
                self.countdown_event.cancel()
                self.countdown_event = None
            self.sensors.stop()
            self.gps.stop()

    class SayaPhoneApp(App):
        def build(self):
            self.title = "SAYA Phone"
            self.root_widget = SayaPhoneRoot()
            return self.root_widget

        def on_start(self):
            self.root_widget.start_real_monitoring()

        def on_pause(self):
            self.root_widget.shutdown()
            return True

        def on_resume(self):
            self.root_widget.start_real_monitoring()

        def on_stop(self):
            self.root_widget.shutdown()

    SayaPhoneApp().run()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--demo" in argv:
        scenario = "MAJOR_CRASH"
        for arg in argv:
            if arg.startswith("--scenario="):
                scenario = arg.split("=", 1)[1]
        print("SAFE DEMO MODE — AI-Assisted Crash Detection (prototype)")
        print(f"Scenario: {scenario}")
        demo = get_demo_scenario(scenario)
        assessment = assess_crash(demo["sample"])
        print("Crash detected:", assessment.crash_detected, assessment.to_dict())
        if "--cancel" in argv:
            result = run_safe_demo(scenario=scenario, cancel=True, send_alert=False)
            print(result["message"])
            return 0
        result = run_safe_demo(
            scenario=scenario,
            cancel=False,
            send_alert=True,
            skip_countdown=True,
        )
        print("Phase:", result["phase"])
        print("Message:", result.get("message"))
        print("Server:", result.get("server_response"))
        if not assessment.crash_detected:
            return 0
        return 0 if result.get("alert_sent") else 1

    if "--check-normal" in argv:
        assessment = assess_crash(simulate_normal_driving_sample())
        print(assessment.to_dict())
        return 0 if not assessment.crash_detected else 1

    run_kivy_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
