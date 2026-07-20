"""
modbus_client.py

Connects to the simulated PLC (plc_simulator.py) over Modbus TCP, polls the
sensor registers, runs anomaly detection on each reading, and writes results
to a local SQLite database that the dashboard reads from.

Run plc_simulator.py first (in its own terminal), then run this.
"""

import time
import sqlite3
import logging
from collections import deque
from datetime import datetime, timezone

from pymodbus.client import ModbusTcpClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("modbus_client")

HOST = "127.0.0.1"
PORT = 5020
POLL_INTERVAL_SEC = 2.0
DB_PATH = "sensor_data.db"

# --- Anomaly detection parameters -------------------------------------------
# Simple, explainable rolling z-score detector: flags a reading as anomalous if
# it's more than Z_THRESHOLD standard deviations from the recent rolling mean.
# This is intentionally transparent (vs. a black-box model) so you can explain
# exactly how it works in an interview.
WINDOW_SIZE = 30          # number of recent readings kept per sensor
Z_THRESHOLD = 3.0         # std-devs from rolling mean to flag as anomaly
MIN_SAMPLES_BEFORE_FLAGGING = 10  # warm-up period before trusting the stats


class RollingZScoreDetector:
    """Maintains a rolling window per sensor and flags statistical outliers."""

    def __init__(self, window_size=WINDOW_SIZE, z_threshold=Z_THRESHOLD):
        self.window_size = window_size
        self.z_threshold = z_threshold
        self.history = {
            "vibration": deque(maxlen=window_size),
            "temperature": deque(maxlen=window_size),
            "current": deque(maxlen=window_size),
        }

    def _mean_std(self, values):
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        std = variance ** 0.5
        return mean, std

    def update_and_check(self, sensor_name, value):
        """Returns (is_anomaly: bool, z_score: float or None)."""
        hist = self.history[sensor_name]

        if len(hist) < MIN_SAMPLES_BEFORE_FLAGGING:
            hist.append(value)
            return False, None

        mean, std = self._mean_std(hist)
        if std == 0:
            z = 0.0
        else:
            z = (value - mean) / std

        is_anomaly = abs(z) > self.z_threshold
        hist.append(value)  # include current reading in future baseline
        return is_anomaly, round(z, 2)


def init_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            vibration REAL,
            temperature REAL,
            current REAL,
            plc_health INTEGER,
            vibration_anomaly INTEGER,
            temperature_anomaly INTEGER,
            current_anomaly INTEGER,
            vibration_z REAL,
            temperature_z REAL,
            current_z REAL,
            overall_anomaly INTEGER
        )
        """
    )
    conn.commit()
    return conn


def read_registers(client):
    """Reads the 4 holding registers and converts them back to real units."""
    result = client.read_holding_registers(address=0, count=4, slave=0)
    if result.isError():
        raise IOError(f"Modbus read error: {result}")
    raw_vibration, raw_temp, raw_current, plc_health = result.registers
    vibration = raw_vibration / 100.0
    temperature = raw_temp / 10.0
    current = raw_current / 10.0
    return vibration, temperature, current, plc_health


def main():
    conn = init_db()
    detector = RollingZScoreDetector()
    client = ModbusTcpClient(HOST, port=PORT)

    log.info(f"Connecting to simulated PLC at {HOST}:{PORT} ...")
    if not client.connect():
        log.error("Could not connect. Is plc_simulator.py running?")
        return

    log.info("Connected. Polling sensor registers...")
    try:
        while True:
            try:
                vibration, temperature, current, plc_health = read_registers(client)
            except IOError as e:
                log.error(str(e))
                time.sleep(POLL_INTERVAL_SEC)
                continue

            vib_anom, vib_z = detector.update_and_check("vibration", vibration)
            temp_anom, temp_z = detector.update_and_check("temperature", temperature)
            cur_anom, cur_z = detector.update_and_check("current", current)
            overall_anomaly = int(vib_anom or temp_anom or cur_anom)

            timestamp = datetime.now(timezone.utc).isoformat()

            conn.execute(
                """
                INSERT INTO readings (
                    timestamp, vibration, temperature, current, plc_health,
                    vibration_anomaly, temperature_anomaly, current_anomaly,
                    vibration_z, temperature_z, current_z, overall_anomaly
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp, vibration, temperature, current, plc_health,
                    int(vib_anom), int(temp_anom), int(cur_anom),
                    vib_z, temp_z, cur_z, overall_anomaly,
                ),
            )
            conn.commit()

            flag = "  <-- ANOMALY" if overall_anomaly else ""
            log.info(
                f"Vib={vibration:5.2f} (z={vib_z})  Temp={temperature:5.1f} (z={temp_z})  "
                f"Current={current:5.1f} (z={cur_z}){flag}"
            )

            time.sleep(POLL_INTERVAL_SEC)

    except KeyboardInterrupt:
        log.info("Stopping client...")
    finally:
        client.close()
        conn.close()


if __name__ == "__main__":
    main()
