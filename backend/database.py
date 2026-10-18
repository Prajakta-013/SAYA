import sqlite3


DATABASE = "lifeline.db"


def create_database():
    connection = sqlite3.connect(DATABASE)

    cursor = connection.cursor()

    # Incidents table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_type TEXT NOT NULL,
            location TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            severity INTEGER,
            priority_score REAL,
            number_of_patients INTEGER,
            status TEXT DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Ambulances table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ambulances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ambulance_number TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            ambulance_type TEXT,
            status TEXT DEFAULT 'Available',
            current_incident_id INTEGER
        )
    """)

    # Hospitals table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hospitals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT,
            latitude REAL,
            longitude REAL,
            total_beds INTEGER,
            available_beds INTEGER,
            icu_total INTEGER,
            icu_available INTEGER,
            emergency_capacity INTEGER,
            status TEXT DEFAULT 'Available'
        )
    """)

    # Assignments table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id INTEGER,
            ambulance_id INTEGER,
            hospital_id INTEGER,
            ambulance_distance REAL,
            ambulance_eta REAL,
            hospital_distance REAL,
            hospital_eta REAL,
            status TEXT DEFAULT 'Active'
        )
    """)

    connection.commit()
    connection.close()

    print("LIFELINE database created successfully!")


if __name__ == "__main__":
    create_database()