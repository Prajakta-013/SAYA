def calculate_eta(distance_km, average_speed_kmh=30):
    """
    Calculate estimated travel time in minutes.
    """

    if average_speed_kmh <= 0:
        return None

    eta_minutes = (distance_km / average_speed_kmh) * 60

    return round(eta_minutes, 2)