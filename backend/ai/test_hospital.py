from hospital_selector import select_best_hospital


hospitals = [
    {
        "id": 1,
        "name": "Lifeline General Hospital",
        "latitude": 19.0760,
        "longitude": 72.8777,
        "available_beds": 80,
        "emergency_capacity": 70,
        "status": "Available"
    },
    {
        "id": 2,
        "name": "City Care Hospital",
        "latitude": 19.0330,
        "longitude": 72.8350,
        "available_beds": 45,
        "emergency_capacity": 60,
        "status": "Available"
    },
    {
        "id": 3,
        "name": "Sunrise Medical Center",
        "latitude": 19.2183,
        "longitude": 72.9781,
        "available_beds": 20,
        "emergency_capacity": 30,
        "status": "Limited"
    }
]


result = select_best_hospital(
    19.0760,
    72.8777,
    hospitals
)

print("Best Hospital:")
print(result)
