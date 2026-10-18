import sqlite3


DATABASE = "lifeline.db"


def add_sample_data():

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    # -----------------------------
    # SAMPLE AMBULANCES
    # -----------------------------

    ambulances = [
        ("AMB-001", 19.0760, 72.8777, "Basic Life Support", "Available"),
        ("AMB-002", 19.0330, 73.0297, "Advanced Life Support", "Available"),
        ("AMB-003", 19.0178, 73.1050, "Basic Life Support", "Busy"),
        ("AMB-004", 19.2183, 72.9781, "Advanced Life Support", "Available"),
        ("AMB-005", 19.1197, 72.8468, "Basic Life Support", "Available")
    ]

    cursor.executemany("""
        INSERT INTO ambulances
        (
            ambulance_number,
            latitude,
            longitude,
            ambulance_type,
            status
        )
        VALUES (?, ?, ?, ?, ?)
    """, ambulances)


    # -----------------------------
    # SAMPLE HOSPITALS
    # -----------------------------

    hospitals = [
        (
            "Lifeline General Hospital",
            "Central Mumbai",
            19.0760,
            72.8777,
            200,
            80,
            20,
            8,
            70,
            "Available"
        ),

        (
            "City Care Hospital",
            "Western Mumbai",
            19.0330,
            72.8350,
            150,
            45,
            15,
            5,
            60,
            "Available"
        ),

        (
            "Metro Emergency Hospital",
            "Eastern Mumbai",
            19.0800,
            72.9000,
            250,
            100,
            30,
            12,
            85,
            "Available"
        ),

        (
            "Sunrise Medical Center",
            "Northern Mumbai",
            19.2183,
            72.9781,
            180,
            20,
            10,
            1,
            30,
            "Limited"
        ),

        (
            "Hope Multispeciality Hospital",
            "Western Suburbs",
            19.1197,
            72.8468,
            220,
            90,
            25,
            10,
            75,
            "Available"
        )
    ]

    cursor.executemany("""
        INSERT INTO hospitals
        (
            name,
            location,
            latitude,
            longitude,
            total_beds,
            available_beds,
            icu_total,
            icu_available,
            emergency_capacity,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, hospitals)


    connection.commit()
    connection.close()

    print("Sample ambulances and hospitals added successfully!")


if __name__ == "__main__":
    add_sample_data()