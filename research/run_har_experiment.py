"""Evaluate smartphone human-activity recognition on the official UCI split."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import platform
import zipfile

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests
import sklearn
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


DATASET_URL = (
    "https://archive.ics.uci.edu/static/public/240/"
    "human+activity+recognition+using+smartphones.zip"
)
EXPECTED_ARCHIVE_SHA256 = (
    "c00b803081a5c797cd5e4b83700a9810b38d53d9d84e01917e090e1fdbc81031"
)
RANDOM_STATE = 42
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
ARCHIVE_PATH = DATA_DIR / "uci_har_240.zip"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_archive(path: Path = ARCHIVE_PATH) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        actual_hash = sha256_file(path)
        if actual_hash != EXPECTED_ARCHIVE_SHA256:
            raise ValueError(
                f"Cached UCI archive checksum mismatch at {path}; "
                "move the file aside and rerun to download a verified copy."
            )
        return actual_hash

    temporary_path = path.with_suffix(path.suffix + ".part")
    try:
        with requests.get(DATASET_URL, stream=True, timeout=(15, 180)) as response:
            response.raise_for_status()
            with temporary_path.open("wb") as destination:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        destination.write(chunk)
        actual_hash = sha256_file(temporary_path)
        if actual_hash != EXPECTED_ARCHIVE_SHA256:
            raise ValueError(
                "Downloaded UCI archive checksum did not match the verified source."
            )
        temporary_path.replace(path)
        return actual_hash
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _read_array(dataset: zipfile.ZipFile, filename: str, dtype: type) -> np.ndarray:
    with dataset.open(filename) as source:
        return np.loadtxt(source, dtype=dtype)


def load_dataset(archive_path: Path) -> dict:
    with zipfile.ZipFile(archive_path) as outer:
        inner_name = "UCI HAR Dataset.zip"
        if inner_name not in outer.namelist():
            raise ValueError("UCI archive is missing its expected nested dataset ZIP.")
        with zipfile.ZipFile(io.BytesIO(outer.read(inner_name))) as dataset:
            prefix = "UCI HAR Dataset/"
            feature_names = [
                line.split(maxsplit=1)[1]
                for line in dataset.read(prefix + "features.txt")
                .decode("utf-8")
                .splitlines()
            ]
            label_map = {
                int(parts[0]): parts[1]
                for line in dataset.read(prefix + "activity_labels.txt")
                .decode("utf-8")
                .splitlines()
                if (parts := line.split(maxsplit=1))
            }
            return {
                "X_train": _read_array(dataset, prefix + "train/X_train.txt", float),
                "y_train": _read_array(dataset, prefix + "train/y_train.txt", int),
                "subject_train": _read_array(
                    dataset, prefix + "train/subject_train.txt", int
                ),
                "X_test": _read_array(dataset, prefix + "test/X_test.txt", float),
                "y_test": _read_array(dataset, prefix + "test/y_test.txt", int),
                "subject_test": _read_array(
                    dataset, prefix + "test/subject_test.txt", int
                ),
                "feature_names": feature_names,
                "label_map": label_map,
            }


def validate_dataset(data: dict) -> dict:
    X_train = data["X_train"]
    y_train = data["y_train"].reshape(-1)
    subject_train = data["subject_train"].reshape(-1)
    X_test = data["X_test"]
    y_test = data["y_test"].reshape(-1)
    subject_test = data["subject_test"].reshape(-1)
    feature_names = data["feature_names"]
    label_map = data["label_map"]

    if X_train.ndim != 2 or X_test.ndim != 2:
        raise ValueError("Feature matrices must be two-dimensional.")
    if X_train.shape[1] != X_test.shape[1] or X_train.shape[1] != len(feature_names):
        raise ValueError("Feature matrix widths do not match feature_names.")
    if len(y_train) != len(X_train) or len(y_test) != len(X_test):
        raise ValueError("Feature and activity-label row counts differ.")
    if len(subject_train) != len(y_train) or len(subject_test) != len(y_test):
        raise ValueError("Subject identifiers and activity-label row counts differ.")
    if not np.isfinite(X_train).all() or not np.isfinite(X_test).all():
        raise ValueError("Feature matrices contain NaN or infinite values.")
    if set(np.unique(y_train)) != set(label_map) or set(np.unique(y_test)) != set(
        label_map
    ):
        raise ValueError("Activity labels do not match the documented class map.")
    overlap = set(np.unique(subject_train)) & set(np.unique(subject_test))
    if overlap:
        raise ValueError(f"Subject leakage between train and test: {sorted(overlap)}")

    return {
        "train_rows": len(y_train),
        "test_rows": len(y_test),
        "feature_count": X_train.shape[1],
        "class_count": len(label_map),
        "train_subject_count": len(np.unique(subject_train)),
        "test_subject_count": len(np.unique(subject_test)),
        "subject_overlap": [],
        "missing_values": 0,
        "class_counts": {
            label_map[int(class_id)]: int(np.count_nonzero(y_train == class_id))
            for class_id in sorted(label_map)
        },
    }


def evaluate_model(model, X_train, y_train, X_test, y_test, class_ids, label_map):
    model.fit(X_train, y_train)
    predictions = model.predict(X_test)
    matrix = confusion_matrix(y_test, predictions, labels=class_ids)
    false_positives = matrix.sum(axis=0) - np.diag(matrix)
    false_negatives = matrix.sum(axis=1) - np.diag(matrix)
    return {
        "accuracy": float(accuracy_score(y_test, predictions)),
        "macro_precision": float(
            precision_score(y_test, predictions, average="macro", zero_division=0)
        ),
        "macro_recall": float(
            recall_score(y_test, predictions, average="macro", zero_division=0)
        ),
        "macro_f1": float(
            f1_score(y_test, predictions, average="macro", zero_division=0)
        ),
        "confusion_matrix": matrix.tolist(),
        "false_positives_by_class": {
            label_map[int(class_id)]: int(count)
            for class_id, count in zip(class_ids, false_positives)
        },
        "false_negatives_by_class": {
            label_map[int(class_id)]: int(count)
            for class_id, count in zip(class_ids, false_negatives)
        },
        "classification_report": classification_report(
            y_test,
            predictions,
            labels=class_ids,
            target_names=[label_map[int(class_id)] for class_id in class_ids],
            output_dict=True,
            zero_division=0,
        ),
        "predictions": predictions,
    }


def save_plot(data: dict, results: dict, class_ids: list[int]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    names = [data["label_map"][class_id] for class_id in class_ids]
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    train_counts = [
        int(np.count_nonzero(data["y_train"] == class_id)) for class_id in class_ids
    ]
    axes[0].barh(names, train_counts, color="#447c74")
    axes[0].set_title("Training windows by activity")
    axes[0].set_xlabel("Windows")

    for axis, model_name in zip(axes[1:], ("Logistic Regression", "Random Forest")):
        matrix = np.asarray(results[model_name]["confusion_matrix"])
        image = axis.imshow(matrix, cmap="Blues")
        axis.set_title(f"{model_name} test confusion matrix")
        axis.set_xticks(range(len(names)), names, rotation=40, ha="right")
        axis.set_yticks(range(len(names)), names)
        axis.set_xlabel("Predicted")
        axis.set_ylabel("Actual")
        for row in range(matrix.shape[0]):
            for column in range(matrix.shape[1]):
                axis.text(column, row, str(matrix[row, column]), ha="center", va="center")
        fig.colorbar(image, ax=axis, fraction=0.046, pad=0.04)

    fig.tight_layout()
    path = RESULTS_DIR / "har_experiment.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


def run_experiment() -> dict:
    archive_hash = download_archive()
    data = load_dataset(ARCHIVE_PATH)
    validation = validate_dataset(data)
    y_train = data["y_train"].reshape(-1)
    y_test = data["y_test"].reshape(-1)
    class_ids = sorted(data["label_map"])

    models = {
        "Most-frequent baseline": DummyClassifier(strategy="most_frequent"),
        "Logistic Regression": make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=3000, random_state=RANDOM_STATE),
        ),
        "Random Forest": RandomForestClassifier(
            n_estimators=300,
            random_state=RANDOM_STATE,
            n_jobs=-1,
        ),
    }
    results = {
        name: evaluate_model(
            model,
            data["X_train"],
            y_train,
            data["X_test"],
            y_test,
            class_ids,
            data["label_map"],
        )
        for name, model in models.items()
    }
    for result in results.values():
        result.pop("predictions")

    plot_path = save_plot(data, results, class_ids)
    report = {
        "experiment": "UCI HAR smartphone activity classification",
        "task_scope": (
            "Six daily activities only. The labels do not include crashes "
            "or accidents; these scores are not crash-detection performance."
        ),
        "source_url": DATASET_URL,
        "archive_sha256": archive_hash,
        "random_state": RANDOM_STATE,
        "split": "Official subject-disjoint train/test files from UCI",
        "model_configuration": {
            "baseline": "DummyClassifier(strategy='most_frequent')",
            "logistic_regression": (
                "StandardScaler + LogisticRegression(max_iter=3000, random_state=42)"
            ),
            "random_forest": (
                "RandomForestClassifier(n_estimators=300, random_state=42, n_jobs=-1)"
            ),
        },
        "validation": validation,
        "class_labels": data["label_map"],
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "models": results,
        "plot": str(plot_path.relative_to(ROOT.parent)),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "metrics.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        name: {
            key: result[key]
            for key in ("accuracy", "macro_precision", "macro_recall", "macro_f1")
        }
        for name, result in results.items()
    }
    print(
        json.dumps(
            {"validation": validation, "metrics": summary, "plot": str(plot_path)},
            indent=2,
        )
    )
    return report


if __name__ == "__main__":
    run_experiment()
