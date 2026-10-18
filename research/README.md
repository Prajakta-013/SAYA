# SAYA research: smartphone human-activity recognition

This is a stand-alone research experiment. It is not imported by Flask and its
models do not make live incident or dispatch decisions.

## Dataset audit

- **Source:** UCI Machine Learning Repository, [Human Activity Recognition
  Using Smartphones (dataset 240)](https://archive.ics.uci.edu/dataset/240/human+activity+recognition+using+smartphones),
  original archive:
  <https://archive.ics.uci.edu/static/public/240/human+activity+recognition+using+smartphones.zip>.
- **What was downloaded:** 61,005,872-byte archive; SHA-256
  `c00b803081a5c797cd5e4b83700a9810b38d53d9d84e01917e090e1fdbc81031`.
  The archive contains the dataset ZIP and its `.names` description.
- **Measured archive contents:** 10,299 labeled windows; `X_train` is
  7,352 × 561 and `X_test` is 2,947 × 561. The activity target contains six
  classes: walking, walking upstairs, walking downstairs, sitting, standing,
  and laying. There are no crash or accident labels.
- **Sensors and channels:** waist-mounted Samsung Galaxy S II recordings of
  tri-axial accelerometer and gyroscope at 50 Hz. The archive supplies total
  acceleration, estimated body acceleration, gyroscope axes, and a
  561-column vector of precomputed time- and frequency-domain features.
  Features are normalized and bounded in [-1, 1]. The dataset README describes
  2.56-second windows of 128 samples with 50% overlap.
- **Split/leakage check:** the official train/test files contain 21 and 9
  subjects respectively; the subject-ID intersection is empty. The experiment
  rejects overlapping subject IDs rather than randomly splitting adjacent
  windows. It also checks shapes, labels, finite values, and class coverage.
- **Missing data:** none found in either feature matrix.
- **Class balance (training windows):** WALKING 1,226; WALKING_UPSTAIRS 1,073;
  WALKING_DOWNSTAIRS 986; SITTING 1,286; STANDING 1,374; LAYING 1,407.
- **License/reuse:** the UCI API response did not declare a standard license
  identifier. The downloaded `.names` file says publications must cite the
  listed Anguita et al. ESANN 2013 paper and states that commercial use is
  prohibited. The paper title calls it a “Public Domain Dataset,” but this
  experiment does not treat that title as overriding the archive's explicit
  non-commercial restriction. Review the source terms before any commercial
  use.

UCI's metadata API currently reports `num_features: 0`, while the downloaded
archive's feature list and actual matrices contain 561 columns. Counts above
come from inspecting and loading the archive, not from that metadata field.

## Measured experiment

The held-out metrics below were produced by
[`run_har_experiment.py`](./run_har_experiment.py) from the official
subject-disjoint test set. Values are rounded to four decimal places for this
table. The complete precision, recall, F1, confusion matrices, and per-class
false-positive/false-negative counts are in
[`results/metrics.json`](./results/metrics.json); plots are in
[`results/har_experiment.png`](./results/har_experiment.png).

| Model | Accuracy | Macro precision | Macro recall | Macro F1 |
|---|---:|---:|---:|---:|
| Most-frequent training-class baseline | 0.1822 | 0.0304 | 0.1667 | 0.0514 |
| Logistic Regression (scaled features) | 0.9555 | 0.9585 | 0.9543 | 0.9555 |
| Random Forest (300 trees) | 0.9287 | 0.9301 | 0.9258 | 0.9271 |

One-vs-rest false-positive (FP) and false-negative (FN) counts on the test
windows:

| Activity | Logistic FP | Logistic FN | Random Forest FP | Random Forest FN |
|---|---:|---:|---:|---:|
| WALKING | 29 | 3 | 54 | 12 |
| WALKING_UPSTAIRS | 18 | 24 | 46 | 40 |
| WALKING_DOWNSTAIRS | 4 | 24 | 12 | 60 |
| SITTING | 14 | 61 | 42 | 56 |
| STANDING | 65 | 14 | 56 | 42 |
| LAYING | 1 | 5 | 0 | 0 |

This experiment uses a majority-class baseline because SAYA's current
sensor-fusion rules output a binary crash decision; that decision is not a
valid baseline for six unrelated daily-activity labels. Logistic Regression
has the best macro F1 on this one official test split. That result applies
only to the six UCI activity classes and is not evidence of crash-detection
precision, recall, safety, or dispatch performance.

The recorded run used Python 3.13.3, NumPy 2.5.3, scikit-learn 1.9.1, and
Matplotlib 3.11.2. Those research dependencies are pinned in
`research/requirements.txt`.

## Reproduce

From the project root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r research\requirements.txt
.\.venv\Scripts\python.exe research\run_har_experiment.py
.\.venv\Scripts\python.exe -m unittest research.test_experiment
```

The first run downloads the verified archive to `research\data\`; that cache
is excluded from version control by `research\.gitignore`. The script checks
the exact source checksum and fails rather than silently using a changed or
corrupt archive. It writes regenerated metrics and a plot under
`research\results\`.

## What this does not establish

- No crash/accident event appears in the supplied labels, so this is **not a
  crash detector** and must not be integrated into live dispatch.
- Activities are performed by volunteers under a waist-mounted-phone study
  protocol; the data do not represent arbitrary modern phones, vehicle mounts,
  crash impacts, or real emergency populations.
- The supplied window features are used as-is. No new crash-specific features
  were engineered, and the app's separate rule-based sensor detector was not
  tuned or evaluated with these labels.
- One official hold-out split is reported. No confidence intervals,
  cross-dataset validation, calibration, production deployment, or clinical /
  emergency-service validation is claimed.
