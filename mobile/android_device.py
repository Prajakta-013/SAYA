"""Android-only polling adapter for live phone motion sensors."""

from __future__ import annotations

import time
import threading
from typing import Callable

from mobile.crash_detector import CrashAssessment, SensorFusionWindow, SensorSample, assess_crash
from mobile.location_service import LiveGPSLocation


class AndroidSensorSource:
    """Read real accelerometer and gyroscope values while the app is foregrounded."""

    def __init__(
        self,
        location: LiveGPSLocation,
        on_sample: Callable[[SensorSample, CrashAssessment], None],
        on_error: Callable[[str], None],
    ):
        self.location = location
        self.on_sample = on_sample
        self.on_error = on_error
        self.fusion = SensorFusionWindow()
        self._sensor_manager = None
        self._sensor_listener = None
        self._sensor_types = None
        self._sensor_values = {}
        self._sensor_versions = {}
        self._last_polled_versions = None
        self._lock = threading.Lock()
        self._event = None

    def start(self) -> None:
        try:
            from kivy.clock import Clock
            from kivy.utils import platform

            if platform != "android":
                raise RuntimeError("REAL DEVICE MODE requires an Android phone.")

            from jnius import PythonJavaClass, autoclass, cast, java_method
            from plyer.platforms.android import activity

            Context = autoclass("android.content.Context")
            Sensor = autoclass("android.hardware.Sensor")
            SensorManager = autoclass("android.hardware.SensorManager")
            sensor_manager = cast(
                "android.hardware.SensorManager",
                activity.getSystemService(Context.SENSOR_SERVICE),
            )
            sensor_types = {
                int(Sensor.TYPE_ACCELEROMETER): "accelerometer",
                int(Sensor.TYPE_GYROSCOPE): "gyroscope",
            }
            sensors = {
                sensor_type: sensor_manager.getDefaultSensor(sensor_type)
                for sensor_type in sensor_types
            }
            missing = [
                name for sensor_type, name in sensor_types.items()
                if sensors[sensor_type] is None
            ]
            if missing:
                raise RuntimeError("This phone is missing: " + ", ".join(missing))

            source = self

            class MotionSensorListener(PythonJavaClass):
                __javainterfaces__ = ["android/hardware/SensorEventListener"]
                __javacontext__ = "app"

                @java_method("(Landroid/hardware/SensorEvent;)V")
                def onSensorChanged(self, event):
                    sensor_type = int(event.sensor.getType())
                    values = tuple(float(event.values[index]) for index in range(3))
                    source._store_sensor_reading(sensor_type, values)

                @java_method("(Landroid/hardware/Sensor;I)V")
                def onAccuracyChanged(self, _sensor, _accuracy):
                    return

            listener = MotionSensorListener()
            for sensor_type, sensor in sensors.items():
                registered = sensor_manager.registerListener(
                    listener,
                    sensor,
                    SensorManager.SENSOR_DELAY_GAME,
                )
                if not registered:
                    raise RuntimeError(f"Unable to register {sensor_types[sensor_type]} sensor.")

            self._sensor_manager = sensor_manager
            self._sensor_listener = listener
            self._sensor_types = sensor_types
            with self._lock:
                self._sensor_values.clear()
                self._sensor_versions.clear()
                self._last_polled_versions = None
            self.fusion.reset()
            self._event = Clock.schedule_interval(self._poll, 0.05)
        except Exception as error:
            self.stop()
            self.on_error(f"Unable to start phone motion sensors: {error}")

    def stop(self) -> None:
        if self._event is not None:
            self._event.cancel()
            self._event = None
        if self._sensor_manager is not None and self._sensor_listener is not None:
            try:
                self._sensor_manager.unregisterListener(self._sensor_listener)
            except Exception:
                pass
        self._sensor_manager = None
        self._sensor_listener = None
        self._sensor_types = None

    def reset(self) -> None:
        self.fusion.reset()

    def _poll(self, _dt: float) -> None:
        try:
            accelerometer_type = next(
                sensor_type for sensor_type, name in self._sensor_types.items()
                if name == "accelerometer"
            )
            gyroscope_type = next(
                sensor_type for sensor_type, name in self._sensor_types.items()
                if name == "gyroscope"
            )
            with self._lock:
                acceleration, acceleration_time = self._sensor_values.get(
                    accelerometer_type, (None, None)
                )
                rotation, rotation_time = self._sensor_values.get(
                    gyroscope_type, (None, None)
                )
                versions = (
                    self._sensor_versions.get(accelerometer_type, 0),
                    self._sensor_versions.get(gyroscope_type, 0),
                )
                if versions == self._last_polled_versions:
                    return
                self._last_polled_versions = versions
            if not self._valid_vector(acceleration) or not self._valid_vector(rotation):
                return
            now = time.monotonic()
            if now - acceleration_time > 0.5 or now - rotation_time > 0.5:
                return

            fix = self.location.latest_fix(max_age_seconds=15.0)
            speed = fix.get("speed") if fix else None
            sample = self.fusion.update(
                tuple(float(value) for value in acceleration),
                tuple(float(value) for value in rotation),
                speed_mps=speed,
                timestamp=now,
            )
            self.on_sample(sample, assess_crash(sample))
        except Exception as error:
            self.on_error(f"Phone sensor read failed: {error}")

    def _store_sensor_reading(
        self,
        sensor_type: int,
        values: tuple[float, float, float],
    ) -> None:
        with self._lock:
            self._sensor_values[sensor_type] = (values, time.monotonic())
            self._sensor_versions[sensor_type] = (
                self._sensor_versions.get(sensor_type, 0) + 1
            )

    @staticmethod
    def _valid_vector(values) -> bool:
        if values is None or len(values) != 3:
            return False
        try:
            return all(value is not None for value in values)
        except TypeError:
            return False
