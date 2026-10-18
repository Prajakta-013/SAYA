import unittest

from app import app, get_database_connection


class TestIncidentEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.client.post(
            "/admin/login",
            data={"username": "admin", "password": "saya"},
        )

    def test_incident_creation_requires_valid_coordinates(self):
        response = self.client.post(
            "/incidents",
            json={
                "incident_type": "Road Accident",
                "location": "Controlled endpoint test",
                "latitude": 95,
                "longitude": 72,
                "severity": 3,
                "number_of_patients": 1,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_valid_incident_is_persisted(self):
        response = self.client.post(
            "/incidents",
            json={
                "incident_type": "Road Accident",
                "location": "Controlled endpoint test",
                "latitude": 19.1,
                "longitude": 72.9,
                "severity": 3,
                "number_of_patients": 1,
            },
        )
        self.assertEqual(response.status_code, 201)
        incident_id = response.get_json()["incident_id"]
        connection = get_database_connection()
        incident = connection.execute(
            "SELECT latitude, longitude, status FROM incidents WHERE id = ?",
            (incident_id,),
        ).fetchone()
        connection.close()
        self.assertEqual((incident["latitude"], incident["longitude"]), (19.1, 72.9))
        self.assertEqual(incident["status"], "Pending")


if __name__ == "__main__":
    unittest.main()
