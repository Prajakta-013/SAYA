import math

from ai.routing import get_route


def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate approximate straight-line distance in kilometers."""
    radius_km = 6371
    latitude_delta = math.radians(lat2 - lat1)
    longitude_delta = math.radians(lon2 - lon1)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(longitude_delta / 2) ** 2
    )
    return round(
        radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value)),
        2,
    )


def select_best_hospital(
    incident_latitude,
    incident_longitude,
    hospitals,
    severity=1,
    priority_score=0,
    number_of_patients=1,
):
    """Select an eligible hospital using route, capacity, and priority data."""
    return rank_hospitals(
        incident_latitude,
        incident_longitude,
        hospitals,
        severity,
        priority_score,
        number_of_patients,
    )["selected"]


def rank_hospitals(
    incident_latitude,
    incident_longitude,
    hospitals,
    severity=1,
    priority_score=0,
    number_of_patients=1,
):
    patients = max(1, int(number_of_patients or 1))
    severity = min(5, max(1, int(severity or 1)))
    priority_score = max(0.0, float(priority_score or 0))
    eligible = []
    rejected = {"status": 0, "beds": 0, "capacity": 0, "coordinates": 0}

    for hospital in hospitals:
        if hospital.get("status") != "Available":
            rejected["status"] += 1
            continue
        try:
            available_beds = int(hospital["available_beds"])
        except (KeyError, TypeError, ValueError):
            available_beds = 0
        try:
            emergency_capacity = int(hospital["emergency_capacity"])
        except (KeyError, TypeError, ValueError):
            emergency_capacity = 0
        if available_beds < patients:
            rejected["beds"] += 1
            continue
        if emergency_capacity < patients:
            rejected["capacity"] += 1
            continue
        try:
            latitude = float(hospital["latitude"])
            longitude = float(hospital["longitude"])
            if not (
                math.isfinite(latitude)
                and math.isfinite(longitude)
                and -90 <= latitude <= 90
                and -180 <= longitude <= 180
            ):
                raise ValueError("coordinates out of range")
        except (KeyError, TypeError, ValueError):
            rejected["coordinates"] += 1
            continue
        try:
            occupied_beds = (
                int(hospital["occupied_beds"])
                if hospital.get("occupied_beds") is not None
                else max(
                    0,
                    int(hospital.get("total_beds") or 0) - available_beds,
                )
            )
        except (TypeError, ValueError):
            occupied_beds = max(
                0,
                int(hospital.get("total_beds") or 0) - available_beds,
            )
        total_beds = max(
            int(hospital.get("total_beds") or 0),
            available_beds + occupied_beds,
            1,
        )
        bed_headroom = min(1.0, max(0.0, available_beds / total_beds))
        eligible.append(
            (
                hospital,
                available_beds,
                emergency_capacity,
                occupied_beds,
                bed_headroom,
            )
        )

    if not eligible:
        reasons = []
        if rejected["status"]:
            reasons.append(f"{rejected['status']} not operationally Available")
        if rejected["beds"]:
            reasons.append(
                f"{rejected['beds']} lack {patients} required available bed(s)"
            )
        if rejected["capacity"]:
            reasons.append(
                f"{rejected['capacity']} lack emergency capacity for {patients} patient(s)"
            )
        if rejected["coordinates"]:
            reasons.append(f"{rejected['coordinates']} lack valid coordinates")
        detail = "; ".join(reasons) or "no hospitals are configured"
        return {
            "selected": None,
            "reason": f"Awaiting hospital placement: no eligible hospital ({detail}).",
        }

    routed = []
    for hospital, beds, capacity, occupied_beds, bed_headroom in eligible:
        route = get_route(
            incident_latitude,
            incident_longitude,
            hospital["latitude"],
            hospital["longitude"],
        )
        if route is None:
            continue
        eta = float(route["duration_minutes"])
        distance = float(route["distance_km"])
        score = (
            beds
            + capacity * (1 + severity / 5 + priority_score / 10)
            + 10 * bed_headroom
        ) / (1 + eta)
        routed.append(
            (
                -score,
                eta,
                int(hospital["id"]),
                hospital,
                beds,
                capacity,
                occupied_beds,
                bed_headroom,
                distance,
                route,
                score,
            )
        )

    if not routed:
        def capacity_score(candidate):
            hospital, beds, capacity, occupied_beds, bed_headroom = candidate
            score = (
                beds
                + capacity * (1 + severity / 5 + priority_score / 10)
                + 10 * bed_headroom
            )
            return (-score, int(hospital["id"]), candidate, score)

        _, _, selected, score = min(
            capacity_score(candidate) for candidate in eligible
        )
        hospital, beds, capacity, occupied, bed_headroom = selected
        reason = (
            f"Selected {hospital['name']} from {len(eligible)} eligible hospital(s) "
            "using available-bed headroom and emergency capacity because road "
            "routing failed for every eligible option. No road distance or ETA "
            "is available. "
            f"Available beds: {beds}; occupied beds: {occupied}; emergency "
            f"capacity: {capacity}; deterministic capacity score: {score:.3f}."
        )
        return {
            "selected": {
                "hospital_id": hospital["id"],
                "hospital_name": hospital["name"],
                "location": hospital.get("location"),
                "status": hospital.get("status"),
                "available_beds": beds,
                "occupied_beds": occupied,
                "total_beds": hospital.get("total_beds"),
                "emergency_capacity": capacity,
                "latitude": hospital["latitude"],
                "longitude": hospital["longitude"],
                "distance_km": None,
                "road_eta_minutes": None,
                "selection_score": score,
                "selection_reason": reason,
                "route": None,
            },
            "reason": reason,
        }

    (
        _,
        eta,
        _,
        hospital,
        beds,
        capacity,
        occupied,
        bed_headroom,
        distance,
        route,
        score,
    ) = min(routed)
    reason = (
        f"Selected {hospital['name']} from {len(routed)} routeable eligible "
        f"hospital(s): status Available, {beds} available beds, "
        f"{capacity} emergency capacity, {eta:.1f} min / {distance:.2f} km. "
        f"Occupied beds: {occupied}; bed headroom: {bed_headroom:.3f}. "
        f"Deterministic score = (available beds + emergency capacity × "
        f"(1 + severity/5 + priority score/10) + 10 × bed headroom) / "
        f"(1 + road ETA minutes) "
        f"= {score:.3f}; ties use lower ETA then hospital ID."
    )
    return {
        "selected": {
            "hospital_id": hospital["id"],
            "hospital_name": hospital["name"],
            "location": hospital.get("location"),
            "status": hospital.get("status"),
            "available_beds": beds,
            "occupied_beds": occupied,
            "total_beds": hospital.get("total_beds"),
            "emergency_capacity": capacity,
            "latitude": hospital["latitude"],
            "longitude": hospital["longitude"],
            "distance_km": distance,
            "road_eta_minutes": eta,
            "selection_score": score,
            "selection_reason": reason,
            "route": route,
        },
        "reason": reason,
    }
