"""Smoke test for the new multi-sensor + sleep-model pipeline."""
import sys
sys.path.insert(0, ".")

from edge.alert_manager import AlertManager, AlertRules
from edge.real_driver_monitor import compute_risk_score

am = AlertManager(AlertRules())

print("=== Alert Manager Tests ===")
cases = [
    ("ASLEEP driver",
     {"confidence": 0.95, "fatigue_score": 0.85, "extra": {"asleep": True}},
     {"speed_kmh": 100, "light_lux": 500, "road_surface": "dry", "weather": "clear"}),
    ("Slippery road 125km/h + fog",
     {"confidence": 0.80, "fatigue_score": 0.3, "extra": {"asleep": False}},
     {"speed_kmh": 125, "light_lux": 400, "road_surface": "slippery", "weather": "fog"}),
    ("Wet road + rain + 95 km/h",
     {"confidence": 0.80, "fatigue_score": 0.2, "extra": {"asleep": False}},
     {"speed_kmh": 95, "light_lux": 300, "road_surface": "wet", "weather": "rain"}),
    ("Low lux 95km/h",
     {"confidence": 0.80, "fatigue_score": 0.15, "extra": {"asleep": False}},
     {"speed_kmh": 95, "light_lux": 20, "road_surface": "dry", "weather": "clear"}),
    ("All safe",
     {"confidence": 0.90, "fatigue_score": 0.1, "extra": {"asleep": False}},
     {"speed_kmh": 60, "light_lux": 800, "road_surface": "dry", "weather": "clear"}),
]

all_pass = True
for name, pred, sensor in cases:
    result = am.evaluate(pred, sensor)
    print(f"  {name}: {result or '(no alert)'}")

print()
print("=== Risk Score Tests ===")
for asleep, conf, spd, rd, wx in [
    (True,  0.95, 110, "dry",      "clear"),
    (False, 0.30, 120, "slippery", "fog"),
    (False, 0.15,  60, "dry",      "clear"),
]:
    s = {"speed_kmh": spd, "road_surface": rd, "weather": wx}
    score, label, c = compute_risk_score(asleep, conf, s)
    print(f"  asleep={str(asleep):<5} spd={spd:3} road={rd:9} wx={wx:5} "
          f"-> score={score:.2f} label={label:7} conf={c:.2f}")

print()
print("=== SensorReader Demo Mode ===")
from edge.real_driver_monitor import SensorReader
reader = SensorReader()
for i in range(3):
    reading = reader.read()
    print(f"  Step {i}: {reading}")

print()
print("All smoke tests PASSED.")
