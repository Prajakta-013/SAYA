import math
from ai.routing import get_route


def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate approximate distance between two locations in kilometers.
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


def select_best_hospital(
    incident_latitude,
    incident_longitude,
    hospitals
):
    """
    Select the most suitable available hospital.
    """

    suitable_hospitals = [
        hospital
        for hospital in hospitals
        if hospital["status"] == "Available"
        and hospital["available_beds"] > 0
        and hospital["emergency_capacity"] > 0
    ]

    if not suitable_hospitals:
        return None

    best_hospital = None
    best_score = float("-inf")
    best_distance = 0
    best_eta = 0

    for hospital in suitable_hospitals:

        route = get_route(
            incident_latitude,
            incident_longitude,
            hospital["latitude"],
            hospital["longitude"]
        )

        if route is None:
            continue

        distance = route["distance_km"]
        eta = route["duration_minutes"]

        score = (
            hospital["available_beds"] * 0.05
            + hospital["emergency_capacity"] * 0.05
            - eta
        )

        if score > best_score:
            best_score = score
            best_hospital = hospital
            best_distance = distance
            best_eta = eta

    if best_hospital is None:
        return None

    return {
    "hospital_id": best_hospital["id"],
    "hospital_name": best_hospital["name"],
    "available_beds": best_hospital["available_beds"],
    "emergency_capacity": best_hospital["emergency_capacity"],
    "latitude": best_hospital["latitude"],
    "longitude": best_hospital["longitude"],
    "distance_km": best_distance,
    "road_eta_minutes": best_eta
}