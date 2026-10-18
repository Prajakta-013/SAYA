import logging
import math

import requests


logger = logging.getLogger(__name__)


def get_route(start_lat, start_lon, end_lat, end_lon):
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{start_lon},{start_lat};{end_lon},{end_lat}"
        f"?overview=full&geometries=geojson"
    )

    try:
        response = requests.get(url, timeout=8)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        logger.warning("OSRM route request failed: %s", error)
        return None

    if not isinstance(data, dict) or data.get("code") != "Ok":
        logger.warning("OSRM could not calculate a route for the requested coordinates.")
        return None

    routes = data.get("routes")
    if not isinstance(routes, list) or not routes:
        logger.warning("OSRM returned no route for the requested coordinates.")
        return None

    route = routes[0]
    try:
        distance_km = float(route["distance"]) / 1000
        duration_minutes = float(route["duration"]) / 60
        raw_geometry = route["geometry"]["coordinates"]
    except (KeyError, TypeError, ValueError) as error:
        logger.warning("OSRM returned an invalid route payload: %s", error)
        return None
    if (
        not math.isfinite(distance_km)
        or not math.isfinite(duration_minutes)
        or distance_km < 0
        or duration_minutes < 0
        or not isinstance(raw_geometry, list)
        or len(raw_geometry) < 2
    ):
        logger.warning("OSRM returned invalid distance, duration, or geometry data.")
        return None

    geometry = []
    for point in raw_geometry:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            logger.warning("OSRM returned invalid route coordinates.")
            return None
        try:
            longitude, latitude = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            logger.warning("OSRM returned non-numeric route coordinates.")
            return None
        if not (
            math.isfinite(longitude)
            and math.isfinite(latitude)
            and -180 <= longitude <= 180
            and -90 <= latitude <= 90
        ):
            logger.warning("OSRM returned out-of-range route coordinates.")
            return None
        geometry.append([longitude, latitude])

    return {
        "distance_km": round(distance_km, 2),
        "duration_minutes": round(duration_minutes, 2),
        "geometry": geometry,
    }