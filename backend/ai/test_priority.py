from priority_engine import calculate_priority


score, priority = calculate_priority(
    severity=5,
    number_of_patients=2,
    incident_type="Severe Accident"
)

print("Priority Score:", score)
print("Priority:", priority)