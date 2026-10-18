import importlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest


class TestConfigurableDatabaseSetup(unittest.TestCase):
    def test_schema_and_sample_data_use_configured_database_path(self):
        original_path = os.environ.get("SAYA_DATABASE_PATH")
        with tempfile.TemporaryDirectory(prefix="saya-setup-test-") as directory:
            database_path = Path(directory) / "fresh.db"
            os.environ["SAYA_DATABASE_PATH"] = str(database_path)
            try:
                import database
                import seed_data

                importlib.reload(database)
                importlib.reload(seed_data)
                database.create_database()
                seed_data.add_sample_data()

                connection = sqlite3.connect(database_path)
                try:
                    counts = {
                        table: connection.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                        for table in ("ambulances", "hospitals")
                    }
                finally:
                    connection.close()

                self.assertEqual(counts, {"ambulances": 5, "hospitals": 5})
            finally:
                if original_path is None:
                    os.environ.pop("SAYA_DATABASE_PATH", None)
                else:
                    os.environ["SAYA_DATABASE_PATH"] = original_path
                if "database" in locals():
                    importlib.reload(database)
                if "seed_data" in locals():
                    importlib.reload(seed_data)


if __name__ == "__main__":
    unittest.main()
