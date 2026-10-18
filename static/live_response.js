(() => {
  "use strict";

  const refreshStatus = document.getElementById("live-refresh-status");
  const refreshButton = document.getElementById("refresh-live");
  const mapStates = new Map();
  const pollIntervalMs = 10000;
  const demoDurationMs = 60000;

  function displayValue(value, fallback) {
    return value === null || value === undefined || value === ""
      ? fallback
      : String(value);
  }

  function finiteNumber(value) {
    if (
      value === null ||
      value === undefined ||
      value === "" ||
      (typeof value === "string" && value.trim() === "") ||
      typeof value === "boolean"
    ) {
      return null;
    }
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function numericDistance(value, unavailable) {
    const number = finiteNumber(value);
    return number === null ? unavailable : `${number.toFixed(2)} km`;
  }

  function numericEta(value, unavailable) {
    const number = finiteNumber(value);
    return number === null ? unavailable : `${number.toFixed(1)} minutes`;
  }

  function valuesFor(item) {
    const hasAssignment = item.id !== null && item.id !== undefined;
    const totalBeds = finiteNumber(item.hospital_total_beds);
    const availableBeds = finiteNumber(item.hospital_available_beds);
    const recordedOccupiedBeds = finiteNumber(item.hospital_occupied_beds);
    const priorityScore = finiteNumber(item.priority_score);
    const occupiedBeds =
      recordedOccupiedBeds !== null
        ? String(recordedOccupiedBeds)
        : totalBeds !== null && availableBeds !== null
          ? `${Math.max(0, totalBeds - availableBeds)} — derived from total minus available beds`
          : "Occupied-bed count not recorded";
    return {
      incident_status: displayValue(item.incident_status, "Incident status not recorded"),
      elapsed_since_received: displayValue(
        item.elapsed_since_received,
        "Elapsed time unavailable — backend receipt time not recorded"
      ),
      assignment_timestamp_display: displayValue(
        item.assignment_timestamp_display,
        "Timestamp unavailable"
      ),
      priority_score:
        priorityScore === null
          ? "Priority score not recorded"
          : `${priorityScore.toFixed(2)} / 10`,
      priority_label: displayValue(item.priority_label, "Priority category not recorded"),
      ambulance_assignment_status: displayValue(
        item.ambulance_assignment_status,
        "No ambulance assigned"
      ),
      ambulance_number_display: displayValue(
        item.ambulance_number_display,
        "No ambulance assigned"
      ),
      ambulance_type: displayValue(
        item.ambulance_type,
        "Ambulance type unavailable — no linked assignment"
      ),
      ambulance_latitude: displayValue(
        item.ambulance_latitude,
        "Ambulance coordinates unavailable — no linked assignment"
      ),
      ambulance_longitude: displayValue(
        item.ambulance_longitude,
        "Ambulance coordinates unavailable — no linked assignment"
      ),
      ambulance_last_gps_update_display: displayValue(
        item.ambulance_last_gps_update_display,
        "Not recorded — no ambulance GPS feed/update timestamp exists"
      ),
      ambulance_coordinate_freshness: hasAssignment
        ? "Stored last-known coordinates; no location-update timestamp is recorded"
        : "Unavailable — no ambulance is assigned",
      ambulance_route_status_display: displayValue(
        item.ambulance_route_status_display,
        "Road route and ETA unavailable"
      ),
      ambulance_distance: numericDistance(
        item.ambulance_distance,
        item.ambulance_distance_status_message || "Road distance unavailable"
      ),
      ambulance_eta: numericEta(
        item.ambulance_eta,
        item.ambulance_eta_status_message || "ETA unavailable"
      ),
      hospital_assignment_status: displayValue(
        item.hospital_assignment_status,
        "No hospital assigned"
      ),
      hospital_name_display: item.hospital_name
        ? `${item.hospital_name} (Hospital #${item.hospital_id})`
        : "No hospital assigned",
      hospital_location: displayValue(
        item.hospital_location,
        "Hospital address/location not recorded"
      ),
      hospital_latitude: displayValue(
        item.hospital_latitude,
        "Hospital latitude unavailable — no linked assignment"
      ),
      hospital_longitude: displayValue(
        item.hospital_longitude,
        "Hospital longitude unavailable — no linked assignment"
      ),
      hospital_available_beds: displayValue(
        item.hospital_available_beds,
        "Available-bed count not recorded"
      ),
      hospital_occupied_beds: occupiedBeds,
      hospital_emergency_capacity: displayValue(
        item.hospital_emergency_capacity,
        "Emergency capacity not recorded"
      ),
      hospital_status: displayValue(item.hospital_status, "Hospital status not recorded"),
      hospital_route_status_display: displayValue(
        item.hospital_route_status_display,
        "Road route and ETA unavailable"
      ),
      hospital_distance: numericDistance(
        item.hospital_distance,
        item.hospital_distance_status_message || "Road distance unavailable"
      ),
      hospital_eta: numericEta(
        item.hospital_eta,
        item.hospital_eta_status_message || "ETA unavailable"
      ),
      assignment_explanation_display: displayValue(
        item.assignment_explanation_display,
        "Assignment explanation unavailable"
      ),
      hospital_selection_explanation: displayValue(
        item.hospital_selection_explanation,
        "Hospital selection explanation unavailable"
      ),
      hospital_map_url: item.map_payload?.hospital?.url || null,
    };
  }

  function updateCard(article, item) {
    const values = valuesFor(item);
    article.querySelectorAll("[data-live-field]").forEach((element) => {
      const key = element.dataset.liveField;
      if (Object.prototype.hasOwnProperty.call(values, key)) {
        element.textContent = values[key];
      }
    });
    article.dataset.assignmentId =
      item.id === null || item.id === undefined ? "" : String(item.id);

    const hospitalLink = article.querySelector("[data-live-hospital-map-link]");
    const noHospitalLink = article.querySelector("[data-live-hospital-map-unavailable]");
    if (hospitalLink && values.hospital_map_url) {
      hospitalLink.href = values.hospital_map_url;
      hospitalLink.textContent = "Open exact hospital coordinates in Google Maps";
      hospitalLink.hidden = false;
      if (noHospitalLink) noHospitalLink.hidden = true;
    } else if (hospitalLink) {
      hospitalLink.hidden = true;
      if (noHospitalLink) noHospitalLink.hidden = false;
    }

    const retryForm = article.querySelector(".retry-dispatch");
    if (retryForm) retryForm.hidden = item.id !== null && item.id !== undefined;

    const payloadScript = article.querySelector(".map-payload");
    if (payloadScript) {
      payloadScript.textContent = JSON.stringify(item.map_payload || {});
    }
    updateMap(article, item.map_payload || {});
    const simulationNote = article.querySelector("[data-simulation-note]");
    if (simulationNote) {
      simulationNote.textContent =
        item.id === null || item.id === undefined
          ? "No ambulance movement is shown because this incident is unassigned."
          : "Demo positions follow the stored OSRM road geometry only. They never update ambulance GPS records.";
    }
  }

  function validPoint(point) {
    return (
      Array.isArray(point) &&
      point.length === 2 &&
      finiteNumber(point[0]) !== null &&
      finiteNumber(point[1]) !== null &&
      finiteNumber(point[0]) >= -90 &&
      finiteNumber(point[0]) <= 90 &&
      finiteNumber(point[1]) >= -180 &&
      finiteNumber(point[1]) <= 180
    );
  }

  function markerIcon(kind, simulated) {
    const classNames = ["map-marker", `${kind}-marker`];
    if (simulated) classNames.push("simulated-marker");
    const letter = kind === "incident" ? "!" : kind === "ambulance" ? "A" : "H";
    return L.divIcon({
      className: "map-marker-wrapper",
      html: `<span class="${classNames.join(" ")}">${letter}</span>`,
      iconSize: [30, 30],
      iconAnchor: [15, 15],
    });
  }

  function popupContent(location, simulated) {
    const wrapper = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = simulated
      ? `SIMULATED — ${location.label}`
      : location.label;
    wrapper.appendChild(title);
    if (validPoint(location.coordinates)) {
      const coordinates = document.createElement("div");
      coordinates.textContent =
        `${Number(location.coordinates[0]).toFixed(6)}, ` +
        `${Number(location.coordinates[1]).toFixed(6)}`;
      wrapper.appendChild(coordinates);
    }
    if (location.url) {
      const link = document.createElement("a");
      link.href = location.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Open exact coordinates";
      wrapper.appendChild(link);
    }
    return wrapper;
  }

  function makeState(mapElement, payload) {
    const state = {
      map: L.map(mapElement, { scrollWheelZoom: false }),
      markers: {},
      lines: {},
      assignmentId: payload.assignment_id ?? null,
      simulation: null,
      initialView: false,
    };
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(state.map);
    return state;
  }

  function setMarker(state, kind, location, simulated) {
    const present = location && validPoint(location.coordinates);
    if (!present) {
      if (state.markers[kind]) {
        state.map.removeLayer(state.markers[kind]);
        delete state.markers[kind];
      }
      return;
    }
    const coordinates = location.coordinates.map(Number);
    if (!state.markers[kind]) {
      state.markers[kind] = L.marker(coordinates, {
        icon: markerIcon(kind, simulated),
      }).addTo(state.map);
    } else {
      state.markers[kind].setLatLng(coordinates);
      state.markers[kind].setIcon(markerIcon(kind, simulated));
    }
    state.markers[kind].bindPopup(popupContent(location, simulated));
  }

  function setRoute(state, key, points, color) {
    if (!Array.isArray(points) || points.length < 2 || !points.every(validPoint)) {
      if (state.lines[key]) {
        state.map.removeLayer(state.lines[key]);
        delete state.lines[key];
      }
      return;
    }
    if (!state.lines[key]) {
      state.lines[key] = L.polyline(points, {
        color,
        weight: 5,
        opacity: 0.82,
      }).addTo(state.map);
    } else {
      state.lines[key].setLatLngs(points);
    }
  }

  function mapStatus(article, payload) {
    const status = article.querySelector("[data-map-status]");
    if (!status) return;
    if (!payload.incident || !validPoint(payload.incident.coordinates)) {
      status.textContent = "Map unavailable — incident coordinates are missing or invalid.";
      return;
    }
    if (payload.assignment_id === null || payload.assignment_id === undefined) {
      status.textContent =
        "Incident location shown from stored coordinates. No ambulance movement is shown because this incident is unassigned.";
      return;
    }
    const missingRoutes = [];
    if (!payload.ambulance_route?.length) missingRoutes.push("ambulance");
    if (!payload.hospital_route?.length) missingRoutes.push("hospital");
    status.textContent = missingRoutes.length
      ? `Stored locations shown; ${missingRoutes.join(" and ")} road route geometry unavailable. Ambulance GPS is not connected.`
      : "Stored locations and OSRM road routes shown. Ambulance GPS is not connected.";
  }

  function updateMap(article, payload) {
    const mapElement = article.querySelector("[data-live-map]");
    if (!mapElement) return;
    if (!payload.incident || !validPoint(payload.incident.coordinates)) {
      mapElement.hidden = true;
      const status = article.querySelector("[data-map-status]");
      if (status) status.textContent = "Map unavailable — incident coordinates are missing or invalid.";
      const button = article.querySelector("[data-simulation-button]");
      if (button) button.disabled = true;
      return;
    }
    mapElement.hidden = false;
    if (!window.L) {
      const status = article.querySelector("[data-map-status]");
      if (status) {
        status.textContent =
          "Interactive map unavailable — Leaflet could not be loaded. Use the exact-coordinate map links.";
      }
      const button = article.querySelector("[data-simulation-button]");
      if (button) button.disabled = true;
      return;
    }
    const incidentId = String(payload.incident_id ?? mapElement.dataset.incidentId);
    let state = mapStates.get(incidentId);
    if (!state) {
      state = makeState(mapElement, payload);
      mapStates.set(incidentId, state);
    } else if (state.assignmentId !== (payload.assignment_id ?? null)) {
      stopSimulation(article, state, true);
      state.assignmentId = payload.assignment_id ?? null;
      Object.keys(state.markers).forEach((key) => {
        state.map.removeLayer(state.markers[key]);
        delete state.markers[key];
      });
    }

    if (!state.simulation) {
      setMarker(state, "ambulance", payload.ambulance, false);
    }
    setMarker(state, "incident", payload.incident, false);
    setMarker(state, "hospital", payload.hospital, false);
    setRoute(state, "ambulance", payload.ambulance_route, "#8b2535");
    setRoute(state, "hospital", payload.hospital_route, "#255d89");
    mapStatus(article, payload);

    if (!state.initialView) {
      const points = [];
      [payload.incident, payload.ambulance, payload.hospital].forEach((place) => {
        if (place && validPoint(place.coordinates)) points.push(place.coordinates);
      });
      if (points.length) {
        state.map.fitBounds(points, { padding: [24, 24], maxZoom: 15 });
      }
      state.initialView = true;
    }
    window.setTimeout(() => state.map.invalidateSize(), 0);

    const button = article.querySelector("[data-simulation-button]");
    if (button) {
      const eta = finiteNumber(payload.ambulance_eta_minutes);
      const distance = finiteNumber(payload.ambulance_distance_km);
      const maySimulate =
        payload.assignment_id !== null &&
        payload.assignment_id !== undefined &&
        validPoint(payload.ambulance?.coordinates) &&
        Array.isArray(payload.ambulance_route) &&
        payload.ambulance_route.length >= 2 &&
        eta !== null &&
        distance !== null;
      button.disabled = !maySimulate && !state.simulation;
      button.hidden = payload.assignment_id === null || payload.assignment_id === undefined;
      button.textContent = state.simulation ? "Stop demo movement" : "Start demo movement";
    }
  }

  function routePosition(points, progress) {
    const segmentLengths = [];
    let total = 0;
    for (let index = 1; index < points.length; index += 1) {
      const [lat1, lon1] = points[index - 1].map((value) => (Number(value) * Math.PI) / 180);
      const [lat2, lon2] = points[index].map((value) => (Number(value) * Math.PI) / 180);
      const deltaLat = lat2 - lat1;
      const deltaLon = lon2 - lon1;
      const a =
        Math.sin(deltaLat / 2) ** 2 +
        Math.cos(lat1) * Math.cos(lat2) * Math.sin(deltaLon / 2) ** 2;
      const segment = 6371000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
      segmentLengths.push(segment);
      total += segment;
    }
    if (total <= 0) {
      const segmentPosition = progress * (points.length - 1);
      const index = Math.min(Math.floor(segmentPosition), points.length - 2);
      const fraction = segmentPosition - index;
      return [
        Number(points[index][0]) +
          (Number(points[index + 1][0]) - Number(points[index][0])) * fraction,
        Number(points[index][1]) +
          (Number(points[index + 1][1]) - Number(points[index][1])) * fraction,
      ];
    }
    let remaining = total * progress;
    for (let index = 0; index < segmentLengths.length; index += 1) {
      const length = segmentLengths[index];
      if (remaining <= length || index === segmentLengths.length - 1) {
        const fraction = length > 0 ? Math.min(1, remaining / length) : 0;
        return [
          Number(points[index][0]) +
            (Number(points[index + 1][0]) - Number(points[index][0])) * fraction,
          Number(points[index][1]) +
            (Number(points[index + 1][1]) - Number(points[index][1])) * fraction,
        ];
      }
      remaining -= length;
    }
    return points[points.length - 1].map(Number);
  }

  function updateSimulationDisplay(article, state) {
    if (!state.simulation) return;
    const progress = Math.min(
      1,
      (Date.now() - state.simulation.startedAt) / state.simulation.durationMs
    );
    const coordinates = routePosition(state.simulation.route, progress);
    const location = {
      ...state.simulation.ambulance,
      coordinates,
    };
    setMarker(state, "ambulance", location, true);
    const remainingDistance =
      state.simulation.distanceKm * Math.max(0, 1 - progress);
    const remainingEta =
      state.simulation.etaMinutes * Math.max(0, 1 - progress);
    const label = article.querySelector("[data-simulation-label]");
    const display = article.querySelector("[data-simulation-progress]");
    if (label) label.hidden = false;
    if (display) {
      display.textContent =
        `Simulated progress: ${(progress * 100).toFixed(0)}% · ` +
        `${remainingDistance.toFixed(2)} km and ${remainingEta.toFixed(1)} min remaining (simulated)`;
    }
    if (progress >= 1 && state.simulation.timer) {
      window.clearInterval(state.simulation.timer);
      state.simulation.timer = null;
    }
  }

  function stopSimulation(article, state, reset) {
    if (state.simulation?.timer) window.clearInterval(state.simulation.timer);
    state.simulation = null;
    const label = article.querySelector("[data-simulation-label]");
    const display = article.querySelector("[data-simulation-progress]");
    const button = article.querySelector("[data-simulation-button]");
    if (label) label.hidden = true;
    if (display) display.textContent = "";
    if (button) button.textContent = "Start demo movement";
    if (reset) {
      const payloadScript = article.querySelector(".map-payload");
      const payload = payloadScript ? JSON.parse(payloadScript.textContent) : {};
      setMarker(state, "ambulance", payload.ambulance, false);
    }
  }

  function bindControls() {
    document.querySelectorAll("[data-simulation-button]").forEach((button) => {
      if (button.dataset.bound) return;
      button.dataset.bound = "true";
      button.addEventListener("click", () => {
        const article = button.closest(".response");
        const mapPayload = article?.querySelector(".map-payload");
        if (!article || !mapPayload) return;
        const payload = JSON.parse(mapPayload.textContent);
        const state = mapStates.get(String(payload.incident_id));
        if (!state) return;
        if (state.simulation) {
          stopSimulation(article, state, true);
          return;
        }
        if (
          payload.assignment_id === null ||
          payload.assignment_id === undefined ||
          !payload.ambulance ||
          !Array.isArray(payload.ambulance_route) ||
          payload.ambulance_route.length < 2 ||
          finiteNumber(payload.ambulance_eta_minutes) === null ||
          finiteNumber(payload.ambulance_distance_km) === null
        ) {
          const status = article.querySelector("[data-map-status]");
          if (status) status.textContent = "Demo movement unavailable — an assigned ambulance and stored OSRM route are required.";
          return;
        }
        state.simulation = {
          startedAt: Date.now(),
          durationMs: demoDurationMs,
          route: payload.ambulance_route,
          ambulance: payload.ambulance,
          distanceKm: finiteNumber(payload.ambulance_distance_km),
          etaMinutes: finiteNumber(payload.ambulance_eta_minutes),
          timer: null,
        };
        const label = article.querySelector("[data-simulation-label]");
        if (label) label.hidden = false;
        updateSimulationDisplay(article, state);
        state.simulation.timer = window.setInterval(
          () => updateSimulationDisplay(article, state),
          100
        );
      });
    });

    document.querySelectorAll(".retry-dispatch").forEach((form) => {
      if (form.dataset.bound) return;
      form.dataset.bound = "true";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const button = form.querySelector("button");
        const message = form.querySelector("[data-retry-message]");
        if (button) button.disabled = true;
        if (message) message.textContent = "Retrying dispatch…";
        try {
          const response = await fetch(form.action, {
            method: "POST",
            headers: { Accept: "application/json" },
            credentials: "same-origin",
          });
          const result = await response.json();
          if (message) {
            message.textContent = response.ok
              ? "Assignment saved. Refreshing live data…"
              : result.error || "Dispatch retry failed.";
          }
          await refreshLiveData();
        } catch (_error) {
          if (message) {
            message.textContent =
              "Dispatch retry could not reach the backend. The incident remains visible; retry when connected.";
          }
        } finally {
          if (button) button.disabled = false;
        }
      });
    });
  }

  async function refreshLiveData() {
    try {
      const known = Array.from(
        document.querySelectorAll("#live-response-cards .response[data-incident-id]")
      )
        .map((article) => article.dataset.incidentId)
        .join(",");
      const response = await fetch(
        `/admin/live/data?known=${encodeURIComponent(known)}`,
        {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
        }
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      const cards = document.getElementById("live-response-cards");
      if (cards && result.new_cards_html) {
        const template = document.createElement("template");
        template.innerHTML = result.new_cards_html;
        cards.appendChild(template.content);
      }
      const emptyMessage = document.querySelector("[data-live-empty]");
      if (emptyMessage) {
        emptyMessage.hidden =
          document.querySelectorAll("#live-response-cards .response").length > 0;
      }
      const byIncident = new Map(
        (result.live_responses || []).map((item) => [String(item.incident_id), item])
      );
      document.querySelectorAll(".response[data-incident-id]").forEach((article) => {
        const item = byIncident.get(article.dataset.incidentId);
        if (item) updateCard(article, item);
      });
      bindControls();
      if (refreshStatus) {
        refreshStatus.textContent = `Updated ${displayValue(result.server_time, "just now")}`;
      }
    } catch (_error) {
      if (refreshStatus) {
        refreshStatus.textContent =
          "Live refresh unavailable — connection failed; showing last retrieved data.";
      }
    }
  }

  function initialize() {
    document.querySelectorAll(".response[data-incident-id]").forEach((article) => {
      const payloadScript = article.querySelector(".map-payload");
      if (!payloadScript) return;
      try {
        updateMap(article, JSON.parse(payloadScript.textContent));
      } catch (_error) {
        const status = article.querySelector("[data-map-status]");
        if (status) status.textContent = "Map data unavailable — stored map payload could not be read.";
      }
    });
    bindControls();
    if (refreshButton) refreshButton.addEventListener("click", refreshLiveData);
    refreshLiveData();
    window.setInterval(refreshLiveData, pollIntervalMs);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
