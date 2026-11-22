"""Edge alert rules — covers both driver state AND road/environment sensors.

Triggers:
  CRITICAL  - driver is asleep (model says True)
  CRITICAL  - prediction label is "drowsy"
  CRITICAL  - fatigue_score >= critical_score (0.75)
  CRITICAL  - slippery road AND speed > critical_speed_kmh
  WARNING   - drowsy-but-not-asleep (fatigue_score >= warning_score)
  WARNING   - wet/slippery road AND speed > warning_speed_kmh
  WARNING   - fog or rain detected
  WARNING   - very low light AND speed > warning_speed_kmh
"""
from dataclasses import dataclass, field


@dataclass
class AlertRules:
    # Driver state thresholds
    warning_score: float = 0.5         # fatigue_score at which a warning fires
    critical_score: float = 0.75       # fatigue_score at which critical fires
    min_confidence: float = 0.4        # ignore predictions below this confidence
    critical_labels: tuple = ("drowsy",)

    # Speed thresholds (km/h)
    warning_speed_kmh: float = 90.0    # speed at which weather/road warnings trigger
    critical_speed_kmh: float = 110.0  # speed at which road hazard → critical

    # Light threshold (lux)
    low_light_lux: float = 50.0        # below this + speed = warning

    # Road / weather hazard labels
    hazard_road_surfaces: tuple = ("wet", "slippery")
    critical_road_surfaces: tuple = ("slippery",)
    hazard_weather: tuple = ("rain", "fog")


class AlertManager:
    def __init__(self, rules: AlertRules | None = None):
        self.rules = rules or AlertRules()

    # ------------------------------------------------------------------ #
    #  Main entry point                                                     #
    # ------------------------------------------------------------------ #
    def evaluate(self, pred: dict, sensor: dict | None = None) -> list[tuple[str, str]]:
        """Return a list of (level, reason) tuples (may be empty).

        pred   - the prediction dict (same schema as PredictionIn)
        sensor - optional dict with sensor values:
                   speed_kmh, light_lux, road_surface, weather
        """
        alerts: list[tuple[str, str]] = []
        r = self.rules
        sensor = sensor or {}

        conf = pred.get("confidence")
        score = pred.get("fatigue_score") or 0.0
        label = pred.get("prediction", "")
        asleep: bool = pred.get("extra", {}).get("asleep", False)

        speed = float(sensor.get("speed_kmh", 0))
        lux = float(sensor.get("light_lux", 1000))
        road = str(sensor.get("road_surface", "dry")).lower()
        weather = str(sensor.get("weather", "clear")).lower()

        # Skip very low-confidence predictions (model unreliable)
        if conf is not None and conf < r.min_confidence:
            return []

        # ── Driver state alerts ─────────────────────────────────────────
        if asleep:
            alerts.append(("critical", "driver asleep (model output: True)"))
        elif score >= r.critical_score:
            alerts.append(("critical", f"fatigue_score {score:.2f} >= {r.critical_score}"))
        elif label in r.critical_labels:
            alerts.append(("critical", f"label '{label}'"))
        elif score >= r.warning_score:
            alerts.append(("warning", f"fatigue_score {score:.2f} >= {r.warning_score}"))

        # ── Road surface alerts ─────────────────────────────────────────
        if road in r.critical_road_surfaces and speed > r.critical_speed_kmh:
            alerts.append(("critical",
                f"slippery road at {speed:.0f} km/h (skid risk)"))
        elif road in r.hazard_road_surfaces and speed > r.warning_speed_kmh:
            alerts.append(("warning",
                f"{road} road surface at {speed:.0f} km/h"))

        # ── Weather / visibility alerts ─────────────────────────────────
        if weather in r.hazard_weather:
            alerts.append(("warning", f"adverse weather detected: {weather}"))

        # ── Low light alert ─────────────────────────────────────────────
        if lux < r.low_light_lux and speed > r.warning_speed_kmh:
            alerts.append(("warning",
                f"low ambient light ({lux:.0f} lux) at {speed:.0f} km/h"))

        # Escalate the highest existing warning to critical when asleep
        if asleep and len(alerts) > 1:
            alerts = [("critical", reason) for _, reason in alerts]

        # Deduplicate, preserve order
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for lvl, reason in alerts:
            if reason not in seen:
                seen.add(reason)
                unique.append((lvl, reason))
        return unique

    # Convenience: highest-severity single result (backward-compat)
    def evaluate_single(self, pred: dict, sensor: dict | None = None) -> tuple[str, str] | None:
        results = self.evaluate(pred, sensor)
        if not results:
            return None
        order = {"critical": 0, "warning": 1}
        return sorted(results, key=lambda x: order.get(x[0], 99))[0]
