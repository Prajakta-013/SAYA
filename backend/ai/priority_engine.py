def calculate_priority(severity, number_of_patients, incident_type):
    """
    Calculate an emergency priority score from 0 to 10.
    """

    # Base score from severity
    score = severity * 1.5

    # Additional patients increase urgency
    if number_of_patients >= 2:
        score += 1

    if number_of_patients >= 4:
        score += 1

    # Certain incident types receive additional priority
    critical_incidents = [
        "Cardiac Arrest",
        "Severe Accident",
        "Fire Injury",
        "Unconscious Person"
    ]

    if incident_type in critical_incidents:
        score += 1.5

    # Keep score between 0 and 10
    score = min(score, 10)

    # Determine priority category
    if score >= 8:
        priority = "CRITICAL"

    elif score >= 6:
        priority = "HIGH"

    elif score >= 4:
        priority = "MEDIUM"

    else:
        priority = "LOW"

    return round(score, 2), priority