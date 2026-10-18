"""Configuration for the SAYA victim-phone crash prototype."""

import os

# Flask server that receives confirmed crash alerts
API_BASE_URL = os.environ.get(
	"SAYA_API_BASE_URL",
	"http://172.29.178.111:5000",
).rstrip("/")
PHONE_CRASH_ALERT_PATH = "/phone-crash-alert"

DEVICE_ID = "VICTIM-PHONE-01"

# Seconds the victim has to cancel before an alert is sent
CANCEL_COUNTDOWN_SECONDS = 10

# --- Sensor-fusion thresholds (prototype, tunable) ---
# Accelerometer magnitude (m/s^2). ~9.8 is gravity; impact >> gravity.
IMPACT_THRESHOLD_MS2 = 25.0

# Gyroscope rotation magnitude (rad/s)
ROTATION_THRESHOLD_RAD_S = 4.0

# Sudden GPS speed drop (m/s). e.g. 15 m/s ≈ 54 km/h drop
SPEED_DROP_THRESHOLD_MS = 12.0

# Post-impact near-stillness (m/s^2 deviation from gravity / low motion)
INACTIVITY_ACCEL_MAX_MS2 = 2.5
INACTIVITY_WINDOW_SAMPLES = 5

# Crash confidence: require at least this many independent signals
MIN_SIGNALS_FOR_CRASH = 2

# Weighted contribution of each signal to confidence [0, 1]
WEIGHT_IMPACT = 0.35
WEIGHT_ROTATION = 0.25
WEIGHT_SPEED_DROP = 0.25
WEIGHT_INACTIVITY = 0.15

# Minimum confidence to enter the cancellation countdown
CRASH_CONFIDENCE_THRESHOLD = 0.55

# Default GPS used when live GPS is unavailable (Mumbai demo corridor)
DEFAULT_LATITUDE = 19.0896
DEFAULT_LONGITUDE = 72.8656
DEFAULT_LOCATION_LABEL = "Victim phone GPS — Eastern Express Highway, Mumbai"
