"""
plc_simulator.py

Simulates a PLC controlling a motor/pump, exposing sensor values over Modbus TCP.
This stands in for a real PLC (e.g. Allen-Bradley, Siemens, Schneider) that would
normally expose these same values as holding registers over Modbus TCP/RTU.

Registers (holding registers, 0-indexed):
  0: Vibration      (mm/s RMS,  x100 scaled to fit as integer, e.g. 245 = 2.45 mm/s)
  1: Temperature    (deg C,     x10 scaled, e.g. 683 = 68.3 C)
  2: Current draw   (Amps,      x10 scaled, e.g. 152 = 15.2 A)
  3: Health status  (0 = Normal, 1 = Warning, 2 = Alarm) -- set by this simulator
                      for reference only; your anomaly detector should compute
                      its own verdict independently from 0/1/2.

Run this first, then run modbus_client.py in a second terminal.
"""

import asyncio
import math
import random
import time
import logging

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusSlaveContext,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("plc_simulator")

HOST = "127.0.0.1"
PORT = 5020  # non-privileged port so you don't need sudo/admin rights

# ---- Simulation parameters -------------------------------------------------
BASE_VIBRATION = 2.0     # mm/s RMS, healthy baseline
BASE_TEMP = 55.0         # deg C, healthy baseline
BASE_CURRENT = 12.0      # A, healthy baseline

ANOMALY_CHANCE_PER_TICK = 0.03   # ~3% chance per update to start an anomaly episode
ANOMALY_DURATION_TICKS = 12      # how many ticks an anomaly episode lasts
UPDATE_INTERVAL_SEC = 2.0        # how often register values are refreshed
BURN_IN_TICKS = 20               # no anomalies allowed until the baseline settles
                                  # (mirrors real commissioning: you let a machine
                                  # run clean before trusting a health baseline)


class MotorState:
    """Holds the evolving 'true' state of the simulated motor."""

    def __init__(self):
        self.t = 0
        self.anomaly_ticks_remaining = 0
        self.anomaly_type = None  # "vibration", "temperature", "current"

    def step(self):
        self.t += 1

        # Randomly trigger a new anomaly episode if none is active (only after burn-in)
        if (
            self.t > BURN_IN_TICKS
            and self.anomaly_ticks_remaining == 0
            and random.random() < ANOMALY_CHANCE_PER_TICK
        ):
            self.anomaly_ticks_remaining = ANOMALY_DURATION_TICKS
            self.anomaly_type = random.choice(["vibration", "temperature", "current", "bearing_wear"])
            log.warning(f"--- Injecting anomaly episode: {self.anomaly_type} ---")

        # Slow natural drift + noise (normal operating variation)
        drift = 0.05 * math.sin(self.t / 40.0)
        vibration = BASE_VIBRATION + drift + random.gauss(0, 0.08)
        temperature = BASE_TEMP + drift * 3 + random.gauss(0, 0.4)
        current = BASE_CURRENT + drift * 0.5 + random.gauss(0, 0.15)

        # Apply anomaly effects on top of the baseline
        if self.anomaly_ticks_remaining > 0:
            progress = 1.0 - (self.anomaly_ticks_remaining / ANOMALY_DURATION_TICKS)
            severity = math.sin(progress * math.pi)  # ramps up then back down

            if self.anomaly_type == "vibration":
                vibration += 3.5 * severity
            elif self.anomaly_type == "temperature":
                temperature += 20 * severity
            elif self.anomaly_type == "current":
                current += 8 * severity
            elif self.anomaly_type == "bearing_wear":
                # bearing wear tends to show up across multiple sensors at once
                vibration += 2.0 * severity
                temperature += 10 * severity
                current += 2.5 * severity

            self.anomaly_ticks_remaining -= 1
            if self.anomaly_ticks_remaining == 0:
                log.info("--- Anomaly episode ended ---")
                self.anomaly_type = None

        # Determine a simple PLC-side health flag (0/1/2) just for realism
        if vibration > BASE_VIBRATION + 2.5 or temperature > BASE_TEMP + 15 or current > BASE_CURRENT + 6:
            health = 2  # Alarm
        elif vibration > BASE_VIBRATION + 1.0 or temperature > BASE_TEMP + 7 or current > BASE_CURRENT + 3:
            health = 1  # Warning
        else:
            health = 0  # Normal

        return vibration, temperature, current, health


async def update_registers(context: ModbusServerContext, state: MotorState):
    """Background loop that keeps pushing fresh sensor values into the datastore."""
    slave_id = 0x00
    while True:
        vibration, temperature, current, health = state.step()

        values = [
            int(round(vibration * 100)),
            int(round(temperature * 10)),
            int(round(current * 10)),
            health,
        ]
        # function code 3 (holding registers), address 0
        context[slave_id].setValues(3, 0, values)

        log.info(
            f"Vib={vibration:5.2f} mm/s  Temp={temperature:5.1f} C  "
            f"Current={current:5.1f} A  Health={['Normal','Warning','Alarm'][health]}"
        )
        await asyncio.sleep(UPDATE_INTERVAL_SEC)


async def main():
    # 4 holding registers, initialized to 0
    store = ModbusSlaveContext(
        hr=ModbusSequentialDataBlock(0, [0] * 10),
    )
    context = ModbusServerContext(slaves=store, single=True)

    state = MotorState()

    log.info(f"Starting simulated PLC (Modbus TCP) on {HOST}:{PORT}")
    server_task = asyncio.create_task(
        StartAsyncTcpServer(context=context, address=(HOST, PORT))
    )
    updater_task = asyncio.create_task(update_registers(context, state))

    await asyncio.gather(server_task, updater_task)


if __name__ == "__main__":
    asyncio.run(main())
