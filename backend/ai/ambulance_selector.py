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
    """
    Select the nearest available ambulance.
    """

    available_ambulances = [
        ambulance
        for ambulance in ambulances
        if ambulance["status"] == "Available"
    ]

    if not available_ambulances:
        return None

    best_ambulance = None
    shortest_distance = float("inf")
    best_distance = 0
    best_eta = 0

    for ambulance in available_ambulances:

        route = get_route(
            ambulance["latitude"],
            ambulance["longitude"],
            incident_latitude,
            incident_longitude
        )

        if route is None:
            continue

        distance = route["distance_km"]
        eta = route["duration_minutes"]

        if eta < shortest_distance:
            shortest_distance = eta
            best_ambulance = ambulance
            best_distance = distance
            best_eta = eta

    if best_ambulance is None:
        return None
    
    return {
        "ambulance_id": best_ambulance["id"],
        "ambulance_number": best_ambulance["ambulance_number"],
        "ambulance_type": best_ambulance["ambulance_type"],
        "distance_km": best_distance,
        "road_eta_minutes": best_eta
    }