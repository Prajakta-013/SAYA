(() => {
  "use strict";

  const COUNTDOWN_SECONDS = 10;
  const ACCELERATION_THRESHOLD = 22;
  const GRAVITY_ACCELERATION_THRESHOLD = 30;
  const ROTATION_THRESHOLD = 220;
  const ORIENTATION_DELTA_THRESHOLD = 75;
  const ORIENTATION_WINDOW_MS = 900;
  const GPS_MAX_AGE_MS = 30_000;

  const stateLabel = document.getElementById("monitor-state");
  const stateMessage = document.getElementById("monitor-message");
  const stateDot = document.getElementById("monitor-dot");
  const accelValue = document.getElementById("accel-value");
  const rotationValue = document.getElementById("rotation-value");
  const orientationValue = document.getElementById("orientation-value");
  const gpsValue = document.getElementById("gps-value");
  const countdownPanel = document.getElementById("countdown");
  const countdownValue = document.getElementById("countdown-value");
  const startButton = document.getElementById("start-monitoring");
  const cancelButton = document.getElementById("cancel-alert");
  const resumeButton = document.getElementById("resume-monitoring");
  const retryButton = document.getElementById("retry-alert");

  let phase = "idle";
  let countdownTimer = null;
  let remaining = COUNTDOWN_SECONDS;
  let orientationSample = null;
  let lastOrientationDelta = 0;
  let latestGpsFix = null;
  let gpsWatchId = null;
  let pendingPayload = null;
  let deviceId = getDeviceId();
  let event = null;
  let listenersAttached = false;
  let motionSignalActive = false;
  let orientationSignalActive = false;
  let triggerLatched = false;
  let quietSince = null;
  let latestMotionIntensity = 0;
  let latestSensorValues = {
    acceleration: null,
    rotation: null,
    orientationDelta: 0,
    intensity: 0,
    impactTriggered: false,
    rotationTriggered: false,
  };

  function randomId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function getDeviceId() {
    try {
      const existing = window.localStorage.getItem("saya-phone-device-id");
      if (existing) return existing;
      const created = `SAYA-WEB-${randomId()}`;
      window.localStorage.setItem("saya-phone-device-id", created);
      return created;
    } catch (error) {
      return `SAYA-WEB-${randomId()}`;
    }
  }

  function setState(name, message, statusClass = "") {
    stateLabel.textContent = name === "monitoring" ? "Phone Monitoring Active" : name;
    stateMessage.textContent = message;
    stateDot.className = `phone-status-dot ${statusClass}`.trim();
  }

  function vectorMagnitude(x, y, z) {
    if (![x, y, z].every(Number.isFinite)) return null;
    return Math.sqrt(x * x + y * y + z * z);
  }

  function angleDifference(current, previous) {
    return Math.abs(((current - previous + 540) % 360) - 180);
  }

  function onMotion(motion) {
    if (phase !== "monitoring") return;
    const acceleration = motion.acceleration;
    const gravityAcceleration = motion.accelerationIncludingGravity;
    const dynamicMagnitude = acceleration
      ? vectorMagnitude(acceleration.x, acceleration.y, acceleration.z)
      : null;
    const gravityMagnitude = gravityAcceleration
      ? vectorMagnitude(gravityAcceleration.x, gravityAcceleration.y, gravityAcceleration.z)
      : null;
    const displayMagnitude = dynamicMagnitude ?? gravityMagnitude;
    if (displayMagnitude !== null) {
      accelValue.textContent = `${displayMagnitude.toFixed(1)} m/s²`;
    }

    const rate = motion.rotationRate;
    const rotationMagnitude = rate
      ? vectorMagnitude(rate.alpha, rate.beta, rate.gamma)
      : null;
    if (rotationMagnitude !== null) {
      rotationValue.textContent = `${rotationMagnitude.toFixed(0)} °/s`;
    }

    const accelerationThreshold = dynamicMagnitude !== null
      ? ACCELERATION_THRESHOLD
      : GRAVITY_ACCELERATION_THRESHOLD;
    const rotationTriggered = rotationMagnitude !== null
      && rotationMagnitude >= ROTATION_THRESHOLD;
    const accelerationTriggered = displayMagnitude !== null
      && displayMagnitude >= accelerationThreshold;
    motionSignalActive = accelerationTriggered || rotationTriggered;
    latestMotionIntensity = Math.max(
      accelerationTriggered ? displayMagnitude / accelerationThreshold : 0,
      rotationTriggered ? rotationMagnitude / ROTATION_THRESHOLD : 0,
    );
    latestSensorValues = {
      acceleration: displayMagnitude,
      rotation: rotationMagnitude,
      orientationDelta: lastOrientationDelta,
      intensity: Math.max(
        latestMotionIntensity,
        orientationSignalActive ? lastOrientationDelta / ORIENTATION_DELTA_THRESHOLD : 0,
      ),
      impactTriggered: accelerationTriggered,
      rotationTriggered,
    };
    updateTriggerState();
  }

  function onOrientation(orientation) {
    if (phase !== "monitoring") return;
    const beta = orientation.beta;
    const gamma = orientation.gamma;
    const now = Date.now();
    if (Number.isFinite(beta) && Number.isFinite(gamma)) {
      if (orientationSample && now - orientationSample.time <= ORIENTATION_WINDOW_MS) {
        lastOrientationDelta = Math.max(
          angleDifference(beta, orientationSample.beta),
          angleDifference(gamma, orientationSample.gamma),
        );
      } else {
        lastOrientationDelta = 0;
        orientationSample = { beta, gamma, time: now };
      }
      orientationValue.textContent = `${lastOrientationDelta.toFixed(0)} °`;
      orientationSignalActive = lastOrientationDelta >= ORIENTATION_DELTA_THRESHOLD;
      latestSensorValues.orientationDelta = lastOrientationDelta;
      latestSensorValues.intensity = Math.max(
        latestMotionIntensity,
        orientationSignalActive ? lastOrientationDelta / ORIENTATION_DELTA_THRESHOLD : 0,
      );
      updateTriggerState();
    }
  }

  function updateTriggerState() {
    const signalActive = motionSignalActive || orientationSignalActive;
    if (triggerLatched) {
      if (signalActive) {
        quietSince = null;
      } else {
        quietSince ??= Date.now();
        if (Date.now() - quietSince >= 1500) {
          triggerLatched = false;
          quietSince = null;
        }
      }
      return;
    }
    if (signalActive) {
      triggerLatched = true;
      beginCountdown(latestSensorValues);
    }
  }

  function startGpsWatch() {
    if (!navigator.geolocation) {
      gpsValue.textContent = "Unavailable";
      return;
    }
    gpsValue.textContent = "Requesting";
    gpsWatchId = navigator.geolocation.watchPosition(
      (position) => {
        latestGpsFix = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
          speed: Number.isFinite(position.coords.speed) ? position.coords.speed : null,
          timestamp: position.timestamp,
        };
        const speed = latestGpsFix.speed === null
          ? "speed unavailable"
          : `${(latestGpsFix.speed * 3.6).toFixed(1)} km/h`;
        gpsValue.textContent = `${latestGpsFix.latitude.toFixed(6)}, ${latestGpsFix.longitude.toFixed(6)} ±${latestGpsFix.accuracy.toFixed(0)} m · ${speed}`;
      },
      (error) => {
        gpsValue.textContent = error.code === error.PERMISSION_DENIED ? "Permission denied" : "No fix yet";
      },
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 15_000 },
    );
  }

  async function requestSensorPermission(sensorType) {
    const sensor = window[sensorType];
    if (sensor && typeof sensor.requestPermission === "function") {
      const permission = await sensor.requestPermission();
      if (permission !== "granted") {
        throw new Error("Motion sensor permission was not granted.");
      }
    }
  }

  async function startMonitoring() {
    if (phase === "starting" || phase === "monitoring" || phase === "countdown" || phase === "sending") return;
    if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
      setState("Secure connection required", "Browser motion sensors need HTTPS (or localhost). Open this page using a secure connection.", "error");
      return;
    }
    if (!("DeviceMotionEvent" in window) && !("DeviceOrientationEvent" in window)) {
      setState("Sensors unavailable", "This browser does not expose motion or orientation sensors. Try a supported mobile browser.", "error");
      return;
    }
    try {
      phase = "starting";
      startButton.disabled = true;
      const permissionRequests = [
        requestSensorPermission("DeviceMotionEvent"),
        requestSensorPermission("DeviceOrientationEvent"),
      ];
      await Promise.all(permissionRequests);
      if (!listenersAttached) {
        window.addEventListener("devicemotion", onMotion);
        window.addEventListener("deviceorientation", onOrientation);
        listenersAttached = true;
      }
      orientationSample = null;
      lastOrientationDelta = 0;
      motionSignalActive = false;
      orientationSignalActive = false;
      startButton.hidden = true;
      startButton.disabled = false;
      cancelButton.hidden = true;
      resumeButton.hidden = true;
      retryButton.hidden = true;
      countdownPanel.hidden = true;
      startGpsWatch();
      phase = "monitoring";
      setState("monitoring", "AI-assisted demo detection is active. Strong motion or rotation will start the cancellation countdown.", "active");
    } catch (error) {
      phase = "error";
      startButton.disabled = false;
      setState("Sensor access unavailable", error.message || "Allow motion and orientation access, then try again.", "error");
      startButton.hidden = false;
    }
  }

  function beginCountdown(sensorValues) {
    if (phase !== "monitoring") return;
    event = {
      id: randomId(),
      timestamp: new Date().toISOString(),
      sensorValues,
      gpsFix: latestGpsFix ? { ...latestGpsFix } : null,
    };
    remaining = COUNTDOWN_SECONDS;
    countdownValue.textContent = String(remaining);
    countdownPanel.hidden = false;
    startButton.hidden = true;
    cancelButton.hidden = false;
    resumeButton.hidden = true;
    retryButton.hidden = true;
    setState("Possible Accident Detected", "Strong phone motion or rotation crossed the demo trigger. Cancel within 10 seconds if you are safe.", "alert");
    phase = "countdown";
    countdownTimer = window.setInterval(() => {
      remaining -= 1;
      countdownValue.textContent = String(remaining);
      if (remaining <= 0) {
        window.clearInterval(countdownTimer);
        countdownTimer = null;
        sendAlert();
      }
    }, 1000);
  }

  function cancelAlert() {
    if (phase !== "countdown") return;
    if (countdownTimer !== null) window.clearInterval(countdownTimer);
    countdownTimer = null;
    event = null;
    countdownPanel.hidden = true;
    cancelButton.hidden = true;
    orientationSample = null;
    lastOrientationDelta = 0;
    orientationSignalActive = false;
    phase = "monitoring";
    setState("monitoring", "Alert cancelled. Phone monitoring remains active for a separate event.", "active");
  }

  function confidenceAndSeverity(sensorValues) {
    const intensity = Math.max(1, sensorValues.intensity);
    const confidence = Math.min(0.98, 0.55 + Math.min(0.43, (intensity - 1) * 0.35));
    const severity = Math.max(2, Math.min(5, 2 + Math.round((confidence - 0.55) * (3 / 0.43))));
    return { confidence: Number(confidence.toFixed(3)), severity };
  }

  async function sendAlert() {
    if (!event || phase !== "countdown") return;
    phase = "sending";
    cancelButton.hidden = true;
    countdownPanel.hidden = true;
    setState("Sending emergency alert", "Countdown completed. Using the latest real GPS fix before contacting SAYA.", "alert");
    try {
      const gpsFix = [latestGpsFix, event.gpsFix]
        .filter((fix) => fix && Date.now() - fix.timestamp <= GPS_MAX_AGE_MS)
        .sort((first, second) => second.timestamp - first.timestamp)[0];
      if (!gpsFix
        || !Number.isFinite(gpsFix.latitude)
        || !Number.isFinite(gpsFix.longitude)
        || !Number.isFinite(gpsFix.accuracy)) {
        throw new Error("The phone did not return valid GPS coordinates.");
      }
      const assessment = confidenceAndSeverity(event.sensorValues);
      pendingPayload = {
        device_id: deviceId,
        event_id: event.id,
        timestamp: event.timestamp,
        latitude: gpsFix.latitude,
        longitude: gpsFix.longitude,
        gps_accuracy: gpsFix.accuracy,
        speed: gpsFix.speed,
        crash_confidence: assessment.confidence,
        severity: assessment.severity,
        incident_type: "Road Accident",
        detection_source: "Victim Smartphone",
        number_of_patients: 1,
        signals: {
          impact: event.sensorValues.impactTriggered,
          rotation: event.sensorValues.rotationTriggered || event.sensorValues.orientationDelta >= ORIENTATION_DELTA_THRESHOLD,
          speed_drop: false,
          inactivity: false,
        },
      };
      await postAlert();
    } catch (error) {
      phase = "error";
      setState("Alert not sent", `${error.message} No emergency was posted because the required real GPS fix was unavailable.`, "error");
      resumeButton.hidden = false;
    }
  }

  async function postAlert() {
    if (!pendingPayload) return;
    phase = "sending";
    retryButton.disabled = true;
    setState("Sending emergency alert", "Posting this phone-detected event to SAYA for automatic dispatch.", "alert");
    try {
      const response = await fetch("/phone-crash-alert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pendingPayload),
      });
      const result = await response.json();
      const alreadyReceived = response.status === 409 && result.already_received;
      if (response.ok || alreadyReceived) {
        phase = "sent";
        setState("Emergency alert received", `SAYA received event ${pendingPayload.event_id}. Incident ${result.incident_id ?? "accepted"}; automatic dispatch is handled by SAYA.`, "active");
        retryButton.hidden = true;
        retryButton.disabled = false;
        resumeButton.hidden = false;
        return;
      }
      throw new Error(result.message || result.error || `SAYA returned HTTP ${response.status}.`);
    } catch (error) {
      phase = "error";
      setState("Alert delivery not confirmed", `${error.message} The event is retained so you can retry without creating a new event ID.`, "error");
      retryButton.disabled = false;
      retryButton.hidden = false;
      resumeButton.hidden = true;
    }
  }

  function resumeMonitoring() {
    pendingPayload = null;
    event = null;
    countdownPanel.hidden = true;
    cancelButton.hidden = true;
    resumeButton.hidden = true;
    retryButton.hidden = true;
    startButton.hidden = true;
    orientationSample = null;
    lastOrientationDelta = 0;
    motionSignalActive = false;
    orientationSignalActive = false;
    phase = "monitoring";
    setState("monitoring", "AI-assisted demo detection is active. Strong motion or rotation will start the cancellation countdown.", "active");
  }

  startButton.addEventListener("click", startMonitoring);
  cancelButton.addEventListener("click", cancelAlert);
  resumeButton.addEventListener("click", resumeMonitoring);
  retryButton.addEventListener("click", postAlert);

  window.addEventListener("pagehide", () => {
    if (gpsWatchId !== null && navigator.geolocation) {
      navigator.geolocation.clearWatch(gpsWatchId);
    }
    if (countdownTimer !== null) window.clearInterval(countdownTimer);
  });
})();
