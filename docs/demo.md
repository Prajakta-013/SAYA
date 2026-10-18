# Competition demonstration (about 4 minutes)

## Before presenting

- Follow [README setup](../README.md#setup-and-run-windows) and initialize a
  fresh temporary demo database. This avoids exposing locations from the
  existing `lifeline.db`.
- Confirm the admin login works and open `/admin/live`.
- Have `/phone` ready on a phone or browser. Allow location/motion permission
  only if comfortable; do not deliberately create a real crash-like movement.
- Keep the demonstration on a private LAN. Do not show real personal or patient
  coordinates. The controlled simulation creates a persistent local incident.

## Script

**0:00–0:30 — Problem and scope**

> “SAYA is an academic prototype for a phone-to-dashboard emergency-response
> workflow. It demonstrates sensing, a cancellation step, GPS, incident
> validation, resource selection, and a live response view. It does not call
> emergency services.”

Point out the “prototype” scope and distinguish a local assignment from an
actual ambulance dispatch.

**0:30–1:10 — Phone monitoring**

Open `/phone`. Show the monitor state and available sensor/GPS readings. Explain
that the browser UI uses motion/orientation heuristics, while the Android path
uses sensor-fusion rules. Do not try to provoke a physical event. Explain that
a recent real GPS fix and the countdown are required before a phone alert.

**1:10–2:20 — Controlled end-to-end event**

Switch to the admin Live Response page and use **Controlled phone-sensor
simulation** with `MAJOR_CRASH`. State before clicking:

> “This named sample is controlled test data. It creates a test incident in my
> copied local database; it does not contact a real ambulance or hospital.”

Show the generated incident ID and event ID. If resources and the public route
service are available, point to the assigned ambulance/hospital and route
estimates. If the dashboard shows `Waiting` / `UNASSIGNED`, explain that the
incident remains visible and no assignment was created because a resource or
route was unavailable.

**2:20–3:00 — Location, status, and honest scores**

Show the coordinates and map link (do not open it if it would disclose a
location). Point out that the displayed score is explicitly a **rule-based
estimate**, not a learned crash probability. Explain severity (1–5) and the
priority bands: LOW (<4), MEDIUM (4–<6), HIGH (6–<8), CRITICAL (>=8).
Emphasize that priority is a simple score, not a medical triage standard.

**3:00–3:45 — Evidence from tests and research**

Mention that the backend regression tests run against a disposable database
copy and use mocked route responses for deterministic tests. Show
`research/results/har_experiment.png` and report that Logistic Regression
achieved 0.9555 macro F1 on UCI's held-out **daily activity** labels, versus
0.0514 for the most-frequent baseline; Random Forest achieved 0.9271. State
clearly that UCI HAR has no crash labels and none of those metrics describe
crash detection.

**3:45–4:00 — Close**

> “The next research requirement is ethically sourced, crash-labeled phone
> sensor data with event/subject-aware evaluation. Until that exists, the
> current rule detector stays separate from the activity classifier and
> dispatch remains a prototype.”
