import math
from ai.routing import get_route


def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate approximate straight-line distance in kilometers.
    """

    R = 6371

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return round(R * c, 2)


def select_best_ambulance(
    incident_latitude,
    incident_longitude,
    ambulances
):
    """Return the available ambulance with the shortest successful road ETA."""
    return rank_available_ambulances(
        incident_latitude, incident_longitude, ambulances
    )["selected"]


def rank_available_ambulances(incident_latitude, incident_longitude, ambulances):
    available = [
        ambulance
        for ambulance in ambulances
        if ambulance.get("status") == "Available"
    ]
    if not available:
        return {
            "selected": None,
            "reason": "Waiting for an available ambulance: no ambulance is currently marked Available.",
        }

    routed = []
    invalid_locations = 0
    for ambulance in available:
        try:
            latitude = float(ambulance["latitude"])
            longitude = float(ambulance["longitude"])
            if not (
                math.isfinite(latitude)
                and math.isfinite(longitude)
                and -90 <= latitude <= 90
                and -180 <= longitude <= 180
            ):
                raise ValueError("coordinates out of range")
        except (KeyError, TypeError, ValueError):
            invalid_locations += 1
            continue

        route = get_route(
            latitude,
            longitude,
            incident_latitude,
            incident_longitude,
        )
        if route is None:
            continue
        routed.append(
            (
                float(route["duration_minutes"]),
                float(route["distance_km"]),
                int(ambulance["id"]),
                ambulance,
                route,
            )
        )

    if not routed:
        if invalid_locations == len(available):
            reason = (
                "Waiting for an available ambulance: all available ambulances "
                "are missing valid stored coordinates."
            )
            return {"selected": None, "reason": reason}

        located = []
        for ambulance in available:
            try:
                latitude = float(ambulance["latitude"])
                longitude = float(ambulance["longitude"])
                if not (
                    math.isfinite(latitude)
                    and math.isfinite(longitude)
                    and -90 <= latitude <= 90
                    and -180 <= longitude <= 180
                ):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            located.append(
                (
                    calculate_distance(
                        incident_latitude,
                        incident_longitude,
                        latitude,
                        longitude,
                    ),
                    int(ambulance["id"]),
                    ambulance,
                )
            )

        if not located:
            return {
                "selected": None,
                "reason": (
                    "Waiting for an available ambulance: no available ambulance "
                    "has valid stored coordinates."
                ),
            }

        straight_line_distance, _, ambulance = min(located)
        return {
            "selected": {
                "ambulance_id": ambulance["id"],
                "ambulance_number": ambulance["ambulance_number"],
                "ambulance_type": ambulance.get("ambulance_type"),
                "latitude": ambulance["latitude"],
                "longitude": ambulance["longitude"],
                "status": ambulance["status"],
                "distance_km": None,
                "road_eta_minutes": None,
                "route": None,
                "straight_line_distance_km": straight_line_distance,
            },
            "reason": (
                "Selected "
                f"{ambulance['ambulance_number']} by shortest straight-line "
                f"distance ({straight_line_distance:.2f} km) because OSRM routing "
                "failed for all available ambulances. Road distance and ETA "
                "remain unavailable."
            ),
        }

    eta, distance, _, ambulance, route = min(routed)
    return {
        "selected": {
            "ambulance_id": ambulance["id"],
            "ambulance_number": ambulance["ambulance_number"],
            "ambulance_type": ambulance.get("ambulance_type"),
            "latitude": ambulance["latitude"],
            "longitude": ambulance["longitude"],
            "status": ambulance["status"],
            "distance_km": distance,
            "road_eta_minutes": eta,
            "route": route,
        },
        "reason": (
            f"Selected {ambulance['ambulance_number']} by the shortest "
            f"successful road ETA ({eta:.1f} min; {distance:.2f} km) among "
            f"{len(routed)} available ambulance(s)."
        ),
    }