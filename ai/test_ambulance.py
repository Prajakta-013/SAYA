from ambulance_selector import select_best_ambulance


ambulances = [
    {
        "id": 1,
        "ambulance_number": "AMB-001",
        "latitude": 19.0760,
        "longitude": 72.8777,
        "ambulance_type": "Basic Life Support",
        "status": "Available"
    },
    {
        "id": 2,
        "ambulance_number": "AMB-002",
        "latitude": 19.0330,
        "longitude": 73.0297,
        "ambulance_type": "Advanced Life Support",
        "status": "Available"
    },
    {
        "id": 3,
        "ambulance_number": "AMB-003",
        "latitude": 19.0178,
        "longitude": 73.1050,
        "ambulance_type": "Basic Life Support",
        "status": "Busy"
    }
]


result = select_best_ambulance(
    19.0760,
    72.8777,
    ambulances
)

print("Best Ambulance:")
print(result)