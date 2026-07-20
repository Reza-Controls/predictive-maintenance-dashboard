"""
ml_anomaly_detector.py  (v2 — stretch goal)

Trains an Isolation Forest on historical sensor readings collected in
sensor_data.db (via modbus_client.py) and compares its anomaly calls against
the rolling z-score detector already running in production.

This is meant to be run AFTER you've collected a reasonable amount of data
(a few minutes of readings, ideally including at least one anomaly episode).

Why compare instead of replace?
  The z-score detector is simple and fully explainable — good for a first
  production pass. Isolation Forest can catch multivariate patterns the
  per-sensor z-score misses (e.g. three sensors each moving only slightly,
  but together forming an unusual combination — like the simulator's
  "bearing wear" pattern). Comparing the two, rather than blindly swapping,
  is the honest way to evaluate whether the added complexity is worth it.

Run:
    python ml_anomaly_detector.py
(after you've let plc_simulator.py + modbus_client.py run for a few minutes)
"""

import sqlite3
import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest

DB_PATH = "sensor_data.db"
FEATURES = ["vibration", "temperature", "current"]
CONTAMINATION = 0.1  # rough prior: assume ~10% of readings might be anomalous


def load_readings():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM readings ORDER BY id ASC", conn)
    conn.close()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def train_isolation_forest(df):
    X = df[FEATURES].values
    model = IsolationForest(
        n_estimators=200,
        contamination=CONTAMINATION,
        random_state=42,
    )
    model.fit(X)
    # decision_function: higher = more normal, lower/negative = more anomalous
    scores = model.decision_function(X)
    predictions = model.predict(X)  # 1 = normal, -1 = anomaly
    return model, scores, predictions


def summarize_agreement(df):
    z_flags = df["overall_anomaly"].values
    ml_flags = (df["ml_anomaly"] == 1).values

    both = int(np.sum((z_flags == 1) & ml_flags))
    only_z = int(np.sum((z_flags == 1) & ~ml_flags))
    only_ml = int(np.sum((z_flags == 0) & ml_flags))
    neither = int(np.sum((z_flags == 0) & ~ml_flags))

    print("\n=== Agreement between z-score detector and Isolation Forest ===")
    print(f"Flagged by BOTH methods:        {both}")
    print(f"Flagged by z-score ONLY:        {only_z}")
    print(f"Flagged by Isolation Forest ONLY: {only_ml}")
    print(f"Flagged by neither (normal):     {neither}")
    print(f"Total readings analyzed:        {len(df)}")


def main():
    df = load_readings()
    if len(df) < 30:
        print(
            f"Only {len(df)} readings found — collect more data first "
            "(let plc_simulator.py + modbus_client.py run for a few minutes)."
        )
        return

    print(f"Loaded {len(df)} readings from {DB_PATH}")
    model, scores, predictions = train_isolation_forest(df)

    df["ml_anomaly_score"] = scores
    df["ml_anomaly"] = np.where(predictions == -1, 1, 0)

    summarize_agreement(df)

    print("\n=== Sample of flagged readings (either method) ===")
    flagged = df[(df["overall_anomaly"] == 1) | (df["ml_anomaly"] == 1)]
    cols = ["timestamp", "vibration", "temperature", "current",
            "overall_anomaly", "ml_anomaly", "ml_anomaly_score"]
    if flagged.empty:
        print("No anomalies flagged by either method in this dataset.")
    else:
        print(flagged[cols].to_string(index=False))

    # Save enriched results for reference / for plotting in the dashboard later
    df.to_csv("ml_comparison_results.csv", index=False)
    print("\nSaved full comparison to ml_comparison_results.csv")


if __name__ == "__main__":
    main()
