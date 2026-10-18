import requests


def get_route(start_lat, start_lon, end_lat, end_lon):
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{start_lon},{start_lat};{end_lon},{end_lat}"
        f"?overview=full&geometries=geojson"
    )

    response = requests.get(url)

    if response.status_code != 200:
        return None

    data = response.json()

    if data["code"] != "Ok":
        return None

    route = data["routes"][0]

    distance_km = route["distance"] / 1000
    duration_minutes = route["duration"] / 60

    return {
    "distance_km": round(distance_km, 2),
    "duration_minutes": round(duration_minutes, 2),
    "geometry": route["geometry"]["coordinates"]
}