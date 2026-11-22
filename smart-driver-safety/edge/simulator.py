"""Synthetic Edge simulator — matches the NEW 4-sensor + sleep-model schema.

Simulates:
  - driver_state_best.keras output  → asleep (bool) + sleep_confidence
  - speed sensor                    → speed_kmh
  - light sensor                    → light_lux
  - road surface sensor             → "dry" | "wet" | "slippery"
  - rain / fog sensor               → "clear" | "rain" | "fog"

All events are tagged simulated=True so they are distinguishable in the DB.

Usage:
  python -m edge.simulator --fog http://localhost:8100 --drivers 3 --count 60 \\
         --interval 0.5 --scenario increasing
"""
import argparse
import random
import time
import uuid
from datetime import datetime, timezone

import requests

from edge.alert_manager import AlertManager, AlertRules

# ── Scenario definitions ──────────────────────────────────────────────────────
# Returns (sleep_score, speed_kmh, road_surface, weather, light_lux)
SCENARIOS = {
    "normal": lambda i, n: (
        random.uniform(0.05, 0.25),                                   # awake
        random.uniform(50, 90),                                        # safe speed
        "dry",
        "clear",
        random.uniform(500, 1200),
    ),
    "increasing": lambda i, n: (
        min(1.0, 0.1 + 0.9 * i / max(1, n - 1) + random.uniform(-0.07, 0.07)),
        min(140.0, 60 + 0.8 * i + random.uniform(-8, 8)),             # speed rises too
        random.choice(["dry", "dry", "wet"]),
        random.choice(["clear", "clear", "rain"]),
        max(20.0, 900.0 - 8.0 * i + random.uniform(-30, 30)),         # light drops
    ),
    "high": lambda i, n: (
        random.uniform(0.82, 0.99),                                   # almost always asleep
        random.uniform(95, 135),
        random.choice(["wet", "slippery"]),
        random.choice(["rain", "fog"]),
        random.uniform(10, 80),
    ),
    "road_hazard": lambda i, n: (
        random.uniform(0.1, 0.4),                                     # awake, but bad road
        random.uniform(90, 130),
        random.choice(["wet", "slippery", "slippery"]),
        random.choice(["rain", "fog", "fog"]),
        random.uniform(15, 100),
    ),
}

SLEEP_THRESHOLD = 0.6
ROAD_RISK_SCORE = {"dry": 0.0, "wet": 0.4, "slippery": 0.75}
WEATHER_RISK_SCORE = {"clear": 0.0, "rain": 0.35, "fog": 0.5}


def compute_risk(sleep_score, asleep, speed, road, weather):
    """Same fusion formula as real_driver_monitor."""
    ss = sleep_score if asleep else (1.0 - sleep_score) * 0.3
    speed_norm = min(1.0, speed / 130.0)
    score = (0.40 * ss + 0.25 * ROAD_RISK_SCORE.get(road, 0)
             + 0.20 * WEATHER_RISK_SCORE.get(weather, 0)
             + 0.15 * speed_norm)
    score = round(min(1.0, max(0.0, score)), 3)

    if asleep:
        label, conf = "drowsy", sleep_score
    elif score >= 0.5:
        label, conf = "alert", score
    else:
        label, conf = "normal", 1.0 - score
    return score, label, round(conf, 3)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def post(fog: str, kind: str, data: dict):
    r = requests.post(f"{fog}/ingest", json={"type": kind, "data": data}, timeout=5)
    r.raise_for_status()
    return r.json()


def run(fog: str, drivers: int, count: int, interval: float, scenario: str, seed: int | None):
    if seed is not None:
        random.seed(seed)

    am = AlertManager(AlertRules())
    gen = SCENARIOS[scenario]

    for i in range(count):
        for d in range(1, drivers + 1):
            driver = f"driver_{d:03d}"
            device = f"edge_{d:03d}"

            sleep_score, speed, road, weather, lux = gen(i, count)
            sleep_score = round(float(sleep_score), 4)
            asleep = sleep_score >= SLEEP_THRESHOLD
            ts = now_iso()

            risk_score, label, conf = compute_risk(sleep_score, asleep, speed, road, weather)
            pred_msg_id = str(uuid.uuid4())

            # Sensor reading
            post(fog, "reading", {
                "message_id": str(uuid.uuid4()),
                "device_id":  device,
                "driver_id":  driver,
                "timestamp":  ts,
                "values": {
                    "speed_kmh":    round(speed, 1),
                    "light_lux":    round(lux, 1),
                    "road_surface": road,
                    "weather":      weather,
                    "frame_id":     i,
                },
                "simulated": True,
            })

            # Prediction
            pred = {
                "message_id":        pred_msg_id,
                "device_id":         device,
                "driver_id":         driver,
                "timestamp":         ts,
                "prediction":        label,
                "confidence":        conf,
                "fatigue_score":     risk_score,
                "model_version":     "sim-driver-state-v1",
                "model_location":    "edge",
                "deployment_config": "simulator",
                "inference_time_ms": round(random.uniform(10, 35), 1),
                "simulated":         True,
                "extra": {
                    "asleep":      asleep,
                    "sleep_score": sleep_score,
                    "road_risk":   road,
                    "weather":     weather,
                    "speed_kmh":   round(speed, 1),
                    "light_lux":   round(lux, 1),
                },
            }
            sensor = {"speed_kmh": speed, "light_lux": lux, "road_surface": road, "weather": weather}
            ack = post(fog, "prediction", pred)

            # Alerts
            alerts = am.evaluate(pred, sensor)
            for level, reason in alerts:
                post(fog, "alert", {
                    "message_id":            str(uuid.uuid4()),
                    "prediction_message_id": pred_msg_id,
                    "device_id":             device,
                    "driver_id":             driver,
                    "timestamp":             ts,
                    "level":                 level,
                    "reason":                reason,
                    "simulated":             True,
                })

            alert_str = "|".join(a[0] for a in alerts) or "-"
            print(
                f"[{i+1}/{count}] {driver} {label:7s} score={risk_score:.2f} "
                f"asleep={asleep!s:<5} speed={speed:.0f}km/h road={road:9s} "
                f"wx={weather:5s} alert={alert_str:8s} fog={ack['status']}"
            )

            # Heartbeat
            requests.post(f"{fog}/heartbeat",
                          json={"device_id": device, "layer": "edge"}, timeout=5)

        time.sleep(interval)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Smart Driver Safety – Synthetic Simulator")
    p.add_argument("--fog",      default="http://localhost:8100")
    p.add_argument("--drivers",  type=int, default=3)
    p.add_argument("--count",    type=int, default=30)
    p.add_argument("--interval", type=float, default=0.5)
    p.add_argument("--scenario", choices=SCENARIOS, default="increasing")
    p.add_argument("--seed",     type=int, default=42)
    a = p.parse_args()
    run(a.fog, a.drivers, a.count, a.interval, a.scenario, a.seed)
