import unittest

from app import app


class TestAssignmentEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.client.post(
            "/admin/login",
            data={"username": "admin", "password": "saya"},
        )

    def test_unknown_incident_returns_not_found(self):
        response = self.client.post("/create-assignment/999999999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "Incident not found"})


if __name__ == "__main__":
    unittest.main()
