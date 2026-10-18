import unittest

import numpy as np

from research.run_har_experiment import validate_dataset


def _small_dataset(subject_test=(3, 3)):
    labels = np.array([1, 2])
    return {
        "X_train": np.array([[0.1, 0.2], [0.3, 0.4]]),
        "y_train": labels,
        "subject_train": np.array([1, 2]),
        "X_test": np.array([[0.5, 0.6], [0.7, 0.8]]),
        "y_test": labels.copy(),
        "subject_test": np.array(subject_test),
        "feature_names": ["feature_a", "feature_b"],
        "label_map": {1: "WALKING", 2: "SITTING"},
    }


class TestDatasetValidation(unittest.TestCase):
    def test_accepts_finite_subject_disjoint_data(self):
        summary = validate_dataset(_small_dataset())
        self.assertEqual(summary["train_rows"], 2)
        self.assertEqual(summary["test_rows"], 2)
        self.assertEqual(summary["subject_overlap"], [])

    def test_rejects_subject_overlap(self):
        with self.assertRaisesRegex(ValueError, "Subject leakage"):
            validate_dataset(_small_dataset(subject_test=(2, 3)))

    def test_rejects_non_finite_features(self):
        data = _small_dataset()
        data["X_test"][0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, "NaN or infinite"):
            validate_dataset(data)


if __name__ == "__main__":
    unittest.main()
