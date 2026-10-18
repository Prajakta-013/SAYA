"""Run backend regression tests against a disposable copy of lifeline.db."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_MODULES = (
    "backend.test_phone_crash",
    "backend.test_emergency_dispatch",
    "backend.test_accident_detection",
    "backend.test_incident",
    "backend.test_assignment",
    "backend.test_routing",
    "backend.test_database_setup",
    "backend.test_live_response",
)


def main() -> int:
    source_database = PROJECT_ROOT / "lifeline.db"
    if not source_database.is_file():
        print(f"Cannot run isolated tests: database copy source not found: {source_database}")
        return 2

    sys.path.insert(0, str(PROJECT_ROOT))
    environment_names = (
        "SAYA_DATABASE_PATH",
        "SAYA_ADMIN_PASSWORD",
        "SAYA_HOSPITAL_BOOTSTRAP_PASSWORD",
    )
    previous_environment = {
        name: os.environ.get(name) for name in environment_names
    }
    try:
        with tempfile.TemporaryDirectory(prefix="saya-test-") as directory:
            test_database = Path(directory) / "lifeline-test.db"
            shutil.copy2(source_database, test_database)
            os.environ["SAYA_DATABASE_PATH"] = str(test_database)
            os.environ["SAYA_ADMIN_PASSWORD"] = "saya"
            os.environ["SAYA_HOSPITAL_BOOTSTRAP_PASSWORD"] = "saya"

            import app as saya_app
            from werkzeug.security import generate_password_hash

            connection = saya_app.get_database_connection()
            admin = connection.execute(
                "SELECT id FROM admins WHERE username = 'admin'"
            ).fetchone()
            if admin is None:
                admin = connection.execute(
                    "SELECT id FROM admins ORDER BY id LIMIT 1"
                ).fetchone()
                connection.execute(
                    "UPDATE admins SET username = 'admin' WHERE id = ?",
                    (admin["id"],),
                )
            connection.execute(
                "UPDATE admins SET password_hash = ? WHERE username = 'admin'",
                (generate_password_hash("saya"),),
            )
            connection.execute(
                "UPDATE hospitals SET password_hash = ?",
                (generate_password_hash("saya"),),
            )
            connection.commit()
            connection.close()

            class IsolatedDatabaseResult(unittest.TextTestResult):
                def startTest(self, test):
                    connection = saya_app.get_database_connection()
                    connection.execute(
                        """
                        UPDATE assignments SET status = 'Completed'
                        WHERE status IS NULL
                           OR lower(trim(status)) NOT IN ('completed', 'cancelled')
                        """
                    )
                    connection.execute(
                        """
                        UPDATE ambulances
                        SET status = 'Busy', current_incident_id = NULL
                        """
                    )
                    connection.commit()
                    connection.close()
                    super().startTest(test)

            suite = unittest.TestSuite()
            loader = unittest.defaultTestLoader
            for module_name in TEST_MODULES:
                suite.addTests(loader.loadTestsFromName(module_name))
            result = unittest.TextTestRunner(
                verbosity=2,
                resultclass=IsolatedDatabaseResult,
            ).run(suite)
            return 0 if result.wasSuccessful() else 1
    finally:
        for name, value in previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(main())
