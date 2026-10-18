# SAYA API notes

These contracts describe the current Flask application in `app.py`. API
responses represent a **local academic prototype**, not real emergency
dispatch. The alert API has no device credential/authentication yet; keep the
server on a trusted demonstration network.

## Access control

- `/phone` and `POST /phone-crash-alert` are public in this prototype so the
  phone client can connect without an admin session.
- `/incidents`, `/ambulances`, `/hospitals`, `/assignments`, resource-selection
  routes, and controlled demo/detection routes require an admin Flask session.
  JSON routes return `401 {"error":"Admin authentication required"}` without
  that session.
- `/admin/*` pages/actions are guarded by `admin_login_required`.
- `/hospital/*` operational pages/actions are guarded by the logged-in
  hospital's session. Hospital users only receive their own incoming
  assignments in their page context.
- New admin/hospital bootstrap passwords come from
  `SAYA_ADMIN_PASSWORD` and `SAYA_HOSPITAL_BOOTSTRAP_PASSWORD`; no password
  default is embedded in source. Existing database password hashes are not
  rewritten at startup.

## Phone alert — `POST /phone-crash-alert`

JSON body fields:

| Field | Required | Validation / meaning |
|---|---|---|
| `device_id` | Yes | Non-empty text; maximum 128 characters. |
| `event_id` | Yes | Non-empty text; maximum 128 characters; repeated with the same device ID to identify a replay. |
| `timestamp` | Yes | Non-empty text; maximum 80 characters. |
| `latitude`, `longitude` | Yes | Numeric, finite; latitude [-90, 90], longitude [-180, 180]. |
| `crash_confidence` | Yes | Numeric finite rule-based score in [0, 1]; not a calibrated probability. |
| `severity` | Yes | Integer from 1 to 5. |
| `gps_accuracy`, `speed` | No | If supplied, finite and non-negative. |
| `number_of_patients` | No | Defaults to 1; must be positive. |
| `location` | No | Optional display label; at most 250 characters. Coordinates remain separately stored and validated. |

Responses:

- `201`: incident and local ambulance/hospital assignment recorded.
- `202`: incident recorded but resources or routes are unavailable; it remains
  `Waiting` and unassigned.
- `400`: malformed, missing, or out-of-range input.
- `409`: same device/event was already received. `already_received` and
  `incident_id` identify the original accepted incident when present.

The mobile/browser clients retain the event ID for retry. A duplicate
acknowledgement is treated as delivery confirmation on both clients; it does
not create another incident.

## Incident/resource JSON routes

Admin-session required:

| Method and path | Purpose |
|---|---|
| `GET /incidents` | Return incidents, newest first. Includes precise coordinates; keep access restricted. |
| `POST /incidents` | Validate and record an incident without automatically assigning resources. Requires `incident_type`, `location`, `latitude`, `longitude`, `severity`, and `number_of_patients`. Returns `201` with `incident_id`, `priority_score`, and `priority`. |
| `GET /ambulances` | Return ambulance inventory. |
| `GET /hospitals` | Return hospital inventory. |
| `GET /assignments` | Return assignments with route geometry where the route service is available. |
| `GET /select-ambulance/<incident_id>` | Preview the selected available ambulance. |
| `GET /select-hospital/<incident_id>` | Preview the selected suitable hospital. |
| `POST /assign-ambulance/<incident_id>` | Run the same atomic ambulance-and-hospital assignment pipeline. |
| `POST /create-assignment/<incident_id>` | Run the existing ambulance/hospital/route assignment operation. |
| `GET /admin/live/data` | Return current live-response cards and map data for dashboard polling. |
| `POST /admin/live/retry/<incident_id>` | Explicitly retry an unassigned incident; existing assignment rows are not rewritten. |

Incident creation validates coordinate bounds, severity 1–5, positive patient
count, and bounded non-empty text fields.

## Controlled detection routes

Admin-session required:

- `POST /phone-crash-demo`: choose one of the server-defined sensor scenarios
  using form field `scenario` or JSON `{"scenario":"MAJOR_CRASH"}`. This uses a
  simulated sensor bundle and submits through the same phone-alert pipeline.
- `POST /simulate-accident`: controlled CCTV simulator. An optional
  `camera_id` may be sent as JSON or form data.
- `POST /detect-accident`: accepts a JSON object with boolean
  `accident_detected`. A positive event must include `incident_type`, `location`,
  `latitude`, `longitude`, `severity`, and `confidence` or `crash_confidence`.
  `number_of_patients` defaults to 1. Invalid coordinates/ranges are rejected.
  Missing event fields are no longer filled with demo coordinates.

All these routes can create persistent incidents in the configured database.
Use a copied database and controlled coordinates for demonstrations.

## Web pages

| Path | Access |
|---|---|
| `GET /phone` | Public phone monitor page. |
| `GET /admin/login`, `GET|POST /admin/login` | Admin login. |
| `/admin`, `/admin/overview`, `/admin/live`, `/admin/hospitals`, `/admin/ambulances` | Admin session. |
| `/hospital/login`, `/hospital/dashboard`, `/hospital/incoming`, `/hospital/resources` | Hospital login/session. |

The live page provides a controlled sensor-simulation form, an interactive
Leaflet/OpenStreetMap location map, marker and route legend, and explicit
dispatch retry for eligible unassigned incidents. It displays incident/event
IDs, source, rule-based score, severity, numeric priority and category,
timestamps in `Asia/Kolkata`, coordinates/map links, stored assignments/routes,
and truthful waiting/route-unavailable states. `GET /admin/live/data` returns
the current joined incident, assignment, ambulance, hospital, and map data as
JSON for in-page polling; it requires an admin session and disables caching.
`POST /admin/live/retry/<incident_id>` explicitly retries an unassigned
incident. It never rewrites an incident that already has an assignment row;
repeat attempts return HTTP 409 instead of creating a duplicate.

On application startup, the existing SQLite schema is upgraded with additive
columns for assignment explanations, route status/geometry, and assignment
timestamps. Existing incident and assignment rows are preserved; historical
assignments without route geometry continue to show route/ETA unavailable.

## Live Response resource selection and timestamps

`GET /admin/live` reads the latest assignment row for each incident, joined to
the linked ambulance and hospital. It does not infer an assignment from an
ambulance's `status` field alone. A missing assignment row is presented as
unassigned, with a persisted failure reason when recorded; older waiting rows
without one are explained using current stored resource statuses.

Automatic assignment considers only ambulances whose status is exactly
`Available`, whose current incident is NULL, whose coordinates are valid, and
which have no active assignment row. Successful OSRM routes rank ambulances by
road ETA. If all ambulance route requests fail, dispatch can still assign an
otherwise eligible ambulance using its shortest straight-line distance; the
dashboard explicitly marks road distance and ETA unavailable. Ambulance claim,
assignment insertion, and incident status update are performed in a serialized
SQLite write transaction, with availability rechecked before commit.

Hospitals are considered only after an ambulance candidate is found. A hospital
is eligible only when its status is exactly `Available`, its available beds and
emergency capacity each meet the incident's patient count, and its coordinates
are valid. Successful OSRM routes contribute the road-ETA term to the
deterministic prototype score below. If routing fails for every eligible
hospital, a capacity/headroom-only comparison chooses among otherwise eligible
hospitals and leaves route distance and ETA NULL and explicitly unavailable.
No route estimate is invented. `bed_headroom` is
`available_beds / max(total_beds, available_beds + occupied_beds, 1)`; when
occupied beds are NULL, they are derived as
`max(total_beds - available_beds, 0)`:

```text
(available_beds
 + emergency_capacity * (1 + severity / 5 + priority_score / 10)
 + 10 * bed_headroom)
/ (1 + road_eta_minutes)
```

The highest score wins; ties use lower ETA, then lower hospital ID. This is a
deterministic prototype heuristic, not a medical triage rule. New assignment
rows store route status, available route geometry, route distances/ETAs, a
creation timestamp, and the selection explanation. If no eligible ambulance
or hospital exists, the incident remains `Waiting` with an explanatory reason.
Existing assignment rows are never automatically rewritten. Future assignment
routes are reserved against active assignment rows so the same ambulance cannot
be assigned to multiple concurrent incidents by competing requests.

Original event timestamps are retained. Offset-aware event timestamps,
backend-created timestamps, and newly recorded assignment timestamps display in
`Asia/Kolkata` as `13 Oct 2026, 10:47:28 PM IST`. SQLite backend-created and
assignment timestamps are interpreted as UTC. Missing, invalid, or timezone-
naive device event times display as `Timestamp unavailable`, not as the current
time. Elapsed time is measured from backend receipt/incident creation, not from
the device event time. Existing assignments with no recorded creation time
continue to show timestamp unavailable.

The ambulance schema has stored coordinates but no GPS-update timestamp or
ambulance GPS ingestion endpoint. The page labels them as stored last-known
coordinates and does not imply real movement. For presentation, the admin may
start a clearly labelled `SIMULATED AMBULANCE MOVEMENT` animation. It is only
available for an assignment with stored OSRM road-route geometry, follows that
geometry, calculates simulated remaining distance/ETA from the stored route,
and never writes simulated positions to the database. Leaflet and OSM tiles
require browser network access.
