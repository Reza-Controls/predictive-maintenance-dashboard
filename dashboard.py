"""
dashboard.py

Live predictive-maintenance dashboard. Reads from sensor_data.db (populated by
modbus_client.py) and displays trends, current status, and anomaly history.

Run:
    1) python plc_simulator.py     (terminal 1)
    2) python modbus_client.py     (terminal 2)
    3) streamlit run dashboard.py  (terminal 3)
"""

import sqlite3
import time

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

DB_PATH = "sensor_data.db"
REFRESH_SEC = 3
ROWS_TO_SHOW = 200

st.set_page_config(page_title="Predictive Maintenance Dashboard", layout="wide")


def load_data():
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(
            f"SELECT * FROM readings ORDER BY id DESC LIMIT {ROWS_TO_SHOW}", conn
        )
        conn.close()
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")
        return df
    except Exception:
        return pd.DataFrame()


def status_badge(latest_row):
    if latest_row is None:
        return "NO DATA", "gray"
    if latest_row["overall_anomaly"] == 1:
        return "ANOMALY DETECTED", "red"
    health = latest_row["plc_health"]
    if health == 2:
        return "ALARM (PLC)", "red"
    if health == 1:
        return "WARNING (PLC)", "orange"
    return "NORMAL", "green"


def render():
    df = load_data()

    st.title("🛠️ Motor/Pump Predictive Maintenance Dashboard")
    st.caption(
        "Live Modbus TCP feed from a simulated motor/pump PLC · "
        "Rolling z-score anomaly detection · Portfolio demo project"
    )

    if df.empty:
        st.warning(
            "No data yet. Make sure `plc_simulator.py` and `modbus_client.py` "
            "are both running, then this page will populate automatically."
        )
        return

    latest = df.iloc[-1]
    label, color = status_badge(latest)

    col1, col2, col3, col4 = st.columns(4)
    col1.markdown(
        f"### Status: :{color}[{label}]"
    )
    col2.metric("Vibration (mm/s)", f"{latest['vibration']:.2f}",
                delta=f"z={latest['vibration_z']}" if latest['vibration_z'] is not None else None)
    col3.metric("Temperature (°C)", f"{latest['temperature']:.1f}",
                delta=f"z={latest['temperature_z']}" if latest['temperature_z'] is not None else None)
    col4.metric("Current (A)", f"{latest['current']:.1f}",
                delta=f"z={latest['current_z']}" if latest['current_z'] is not None else None)

    st.divider()

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        subplot_titles=("Vibration (mm/s)", "Temperature (°C)", "Current (A)"),
        vertical_spacing=0.08,
    )

    sensors = [
        ("vibration", "vibration_anomaly", 1),
        ("temperature", "temperature_anomaly", 2),
        ("current", "current_anomaly", 3),
    ]

    for col, anom_col, row in sensors:
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"], y=df[col], mode="lines",
                name=col, line=dict(width=2),
            ),
            row=row, col=1,
        )
        anomalies = df[df[anom_col] == 1]
        if not anomalies.empty:
            fig.add_trace(
                go.Scatter(
                    x=anomalies["timestamp"], y=anomalies[col], mode="markers",
                    name=f"{col} anomaly", marker=dict(color="red", size=9, symbol="x"),
                ),
                row=row, col=1,
            )

    fig.update_layout(height=700, showlegend=False, margin=dict(t=40, b=20))
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("Recent anomaly events")
    anomaly_df = df[df["overall_anomaly"] == 1][
        ["timestamp", "vibration", "temperature", "current",
         "vibration_z", "temperature_z", "current_z"]
    ].sort_values("timestamp", ascending=False)

    if anomaly_df.empty:
        st.info("No anomalies detected in the current window — system running within normal bounds.")
    else:
        st.dataframe(anomaly_df, use_container_width=True, hide_index=True)


render()
time.sleep(REFRESH_SEC)
st.rerun()
