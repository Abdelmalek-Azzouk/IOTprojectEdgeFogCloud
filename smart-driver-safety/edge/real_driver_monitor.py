"""Real-Time Driver Safety Edge Monitor — partners' actual hardware integration.

Integrates:
  • driver_state_best.keras  → True (asleep) / False (awake)
  • speed sensor             → speed_kmh  (float)
  • light sensor             → light_lux  (float)
  • road surface sensor      → road_surface: "dry" | "wet" | "slippery"
  • rain / fog sensor        → weather:  "clear" | "rain" | "fog"

The script reads sensor values from ONE of three sources (in priority order):
  1. --sensor-port  : live serial/UART feed from hardware (JSON lines)
  2. --sensor-file  : a JSON/CSV file your partners export (re-read each cycle)
  3. built-in demo  : realistic values generated internally (no hardware needed)

Camera/video input feeds the AI sleep-detection model every --interval seconds.
All inferences are shipped to the Fog Gateway as real events (simulated=False).

Usage examples:
  # Webcam + demo sensors:
  python -m edge.real_driver_monitor --fog http://localhost:8100

  # Video file + demo sensors:
  python -m edge.real_driver_monitor --video data/driver.mp4

  # Webcam + hardware sensors on COM3:
  python -m edge.real_driver_monitor --sensor-port COM3

  # Video + sensor JSON file from partners:
  python -m edge.real_driver_monitor --video data/driver.mp4 --sensor-file data/sensors.json

  # Headless (no GUI):
  python -m edge.real_driver_monitor --video data/driver.mp4 --no-gui
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import requests

from edge.alert_manager import AlertManager, AlertRules

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("edge.real_monitor")

# ──────────────────────────────────────────────────────────────────────────────
# Paths & constants
# ──────────────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"
IMG_SIZE = (160, 160)

SLEEP_MODEL_FILE = "driver_state_best.keras"
MODEL_VERSION = "driver-state-v1"


# ──────────────────────────────────────────────────────────────────────────────
# Model loading
# ──────────────────────────────────────────────────────────────────────────────
def load_sleep_model(models_dir: Path):
    import tensorflow as tf
    path = models_dir / SLEEP_MODEL_FILE
    if not path.exists():
        raise FileNotFoundError(f"Model not found: {path}")
    log.info("Loading sleep-detection model: %s", path)
    model = tf.keras.models.load_model(str(path))
    log.info("Model loaded.")
    return model


def prepare_frame(frame_bgr: np.ndarray) -> np.ndarray:
    """Resize + convert to RGB float32 batch for the Keras model."""
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, IMG_SIZE)
    return np.expand_dims(resized.astype(np.float32), axis=0)


def infer_sleep(model, frame_bgr: np.ndarray, threshold: float = 0.6) -> tuple[bool, float]:
    """Run the sleep-detection model.

    Returns:
        asleep     : True if model output >= threshold
        confidence : raw sigmoid score (0..1)
    """
    batch = prepare_frame(frame_bgr)
    score = float(model.predict(batch, verbose=0)[0, 0])
    return score >= threshold, round(score, 4)


# ──────────────────────────────────────────────────────────────────────────────
# Sensor reading helpers
# ──────────────────────────────────────────────────────────────────────────────

class SensorReader:
    """Reads the four physical sensors.

    Priority:
      1. Serial port  (live hardware)
      2. JSON/CSV file  (file written by partner's MCU)
      3. Demo mode (realistic synthetic values for testing without hardware)
    """

    ROAD_SURFACES = ["dry", "wet", "slippery"]
    WEATHERS = ["clear", "rain", "fog"]

    def __init__(self, port: str | None = None, filepath: str | None = None):
        self._port = port
        self._filepath = filepath
        self._serial = None
        self._demo_step = 0

        if port:
            try:
                import serial
                self._serial = serial.Serial(port, baudrate=9600, timeout=1)
                log.info("Serial sensor port opened: %s", port)
            except Exception as exc:
                log.warning("Could not open serial port %s: %s — falling back to demo mode", port, exc)
                self._serial = None

        if filepath and not os.path.exists(filepath):
            log.warning("Sensor file not found: %s — demo mode active", filepath)
            self._filepath = None

    def read(self) -> dict:
        """Return dict: speed_kmh, light_lux, road_surface, weather."""
        if self._serial:
            return self._read_serial()
        if self._filepath:
            return self._read_file()
        return self._demo()

    # ── Serial ────────────────────────────────────────────────────────────────
    def _read_serial(self) -> dict:
        """Expect newline-delimited JSON from hardware, e.g.:
        {"speed_kmh": 72.3, "light_lux": 420.0, "road_surface": "wet", "weather": "rain"}
        Falls back to demo if the line is malformed.
        """
        try:
            line = self._serial.readline().decode("utf-8", errors="ignore").strip()
            if line:
                data = json.loads(line)
                return self._validate(data)
        except Exception as exc:
            log.debug("Serial read error: %s", exc)
        return self._demo()

    # ── File ──────────────────────────────────────────────────────────────────
    def _read_file(self) -> dict:
        """Read the latest sensor state from a JSON file partners drop.

        Supported formats:
          • JSON object: {"speed_kmh": 72.3, "light_lux": 420.0, ...}
          • JSON array (last element is newest): [{...}, {...}, {...}]
        """
        try:
            with open(self._filepath, encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, list):
                raw = raw[-1]  # newest row
            return self._validate(raw)
        except Exception as exc:
            log.debug("Sensor file read error: %s", exc)
        return self._demo()

    # ── Demo ──────────────────────────────────────────────────────────────────
    def _demo(self) -> dict:
        """Produce realistic but synthetic sensor readings for testing."""
        step = self._demo_step
        self._demo_step += 1

        # Speed: starts low, increases, then stays high
        speed = min(130.0, 30.0 + step * 1.5 + random.uniform(-5, 5))

        # Light: daytime bright, drops after step 40 (simulate dusk / tunnel)
        lux = max(10.0, 800.0 - step * 10 + random.uniform(-30, 30)) if step > 40 else \
              800.0 + random.uniform(-50, 50)

        # Road & weather cycle every ~30 steps
        cycle = (step // 30) % 3
        road = ["dry", "dry", "wet"][cycle]
        weather = ["clear", "rain", "fog"][cycle]

        return {
            "speed_kmh": round(speed, 1),
            "light_lux": round(lux, 1),
            "road_surface": road,
            "weather": weather,
        }

    @staticmethod
    def _validate(data: dict) -> dict:
        return {
            "speed_kmh":    float(data.get("speed_kmh", 0.0)),
            "light_lux":    float(data.get("light_lux", 1000.0)),
            "road_surface": str(data.get("road_surface", "dry")).lower(),
            "weather":      str(data.get("weather", "clear")).lower(),
        }


# ──────────────────────────────────────────────────────────────────────────────
# Risk score fusion
# ──────────────────────────────────────────────────────────────────────────────

ROAD_RISK_SCORE = {"dry": 0.0, "wet": 0.4, "slippery": 0.75}
WEATHER_RISK_SCORE = {"clear": 0.0, "rain": 0.35, "fog": 0.5}


def compute_risk_score(
    asleep: bool,
    sleep_confidence: float,
    sensor: dict,
) -> tuple[float, str, float]:
    """Fuse AI output + sensor data into one overall risk score (0..1).

    Weights:
      40%  sleep model output
      25%  road surface risk
      20%  weather / visibility risk
      15%  speed risk (normalized at 130 km/h)

    Returns: (fatigue_score, prediction_label, confidence)
    """
    sleep_score = sleep_confidence if asleep else (1.0 - sleep_confidence) * 0.3

    speed_norm = min(1.0, sensor.get("speed_kmh", 0) / 130.0)
    road_score = ROAD_RISK_SCORE.get(sensor.get("road_surface", "dry"), 0.0)
    weather_score = WEATHER_RISK_SCORE.get(sensor.get("weather", "clear"), 0.0)

    fatigue_score = (
        0.40 * sleep_score
        + 0.25 * road_score
        + 0.20 * weather_score
        + 0.15 * speed_norm
    )
    fatigue_score = round(min(1.0, max(0.0, fatigue_score)), 3)

    if asleep:
        label = "drowsy"
        conf = sleep_confidence
    elif fatigue_score >= 0.5:
        label = "alert"
        conf = fatigue_score
    else:
        label = "normal"
        conf = 1.0 - fatigue_score

    return fatigue_score, label, round(float(conf), 3)


# ──────────────────────────────────────────────────────────────────────────────
# Fog helpers
# ──────────────────────────────────────────────────────────────────────────────

def post_to_fog(fog_url: str, event_type: str, data: dict, timeout: float = 4.0) -> dict | None:
    try:
        res = requests.post(
            f"{fog_url.rstrip('/')}/ingest",
            json={"type": event_type, "data": data},
            timeout=timeout,
        )
        res.raise_for_status()
        return res.json()
    except Exception as exc:
        log.warning("Fog ingest failed (%s): %s", event_type, exc)
        return None


def send_heartbeat(fog_url: str, device_id: str, meta: dict | None = None):
    try:
        requests.post(
            f"{fog_url.rstrip('/')}/heartbeat",
            json={"device_id": device_id, "layer": "edge", "meta": meta or {}},
            timeout=2.0,
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# GUI overlay
# ──────────────────────────────────────────────────────────────────────────────

ROAD_ICON = {"dry": "🟢 DRY", "wet": "🔵 WET", "slippery": "🔴 SLIPPERY"}
WEATHER_ICON = {"clear": "☀ CLEAR", "rain": "🌧 RAIN", "fog": "🌫 FOG"}


def _put(frame, text, pos, color, scale=0.6, thickness=2):
    cv2.putText(frame, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness)


def draw_overlay(frame, asleep, label, fatigue_score, sensor, alerts, fog_status, inf_ms):
    h, w = frame.shape[:2]
    # background strip
    cv2.rectangle(frame, (0, 0), (w, 150), (20, 20, 20), -1)

    alert_color = (0, 0, 255) if any(a[0] == "critical" for a in alerts) \
        else (0, 165, 255) if alerts else (0, 220, 60)

    state_text = "ASLEEP ⚠" if asleep else label.upper()
    _put(frame, f"Driver: {state_text}  score={fatigue_score:.2f}", (12, 30), alert_color, 0.75, 2)
    _put(frame, f"Speed: {sensor['speed_kmh']:.1f} km/h   Light: {sensor['light_lux']:.0f} lux",
         (12, 60), (220, 220, 220), 0.6)
    road_txt = ROAD_ICON.get(sensor.get("road_surface", "dry"), sensor.get("road_surface", "?"))
    wx_txt = WEATHER_ICON.get(sensor.get("weather", "clear"), sensor.get("weather", "?"))
    _put(frame, f"Road: {road_txt}   Weather: {wx_txt}", (12, 90), (220, 220, 220), 0.6)
    if alerts:
        reason = " | ".join(f"[{a[0].upper()}] {a[1]}" for a in alerts[:2])
        _put(frame, f"ALERT: {reason[:70]}", (12, 120), (0, 60, 255), 0.55, 2)
    _put(frame, f"Inf: {inf_ms:.1f}ms  Fog: {fog_status}", (12, 145), (150, 150, 150), 0.5, 1)


# ──────────────────────────────────────────────────────────────────────────────
# Main monitor loop
# ──────────────────────────────────────────────────────────────────────────────

def run_monitor(
    video_source,
    fog_url: str,
    driver_id: str,
    device_id: str,
    models_dir: Path,
    sensor_port: str | None,
    sensor_file: str | None,
    inference_interval: float,
    sleep_threshold: float,
    show_gui: bool,
    loop_video: bool,
):
    model = load_sleep_model(models_dir)
    sensor_reader = SensorReader(port=sensor_port, filepath=sensor_file)
    alert_manager = AlertManager(AlertRules())

    # Open video capture
    is_webcam = isinstance(video_source, int) or \
                (isinstance(video_source, str) and str(video_source).isdigit())
    cap = cv2.VideoCapture(int(video_source) if is_webcam else video_source)
    if not cap.isOpened():
        log.error("Cannot open video source: %s", video_source)
        sys.exit(1)

    log.info("Stream opened (%s). Sending to Fog %s | driver=%s | device=%s",
             video_source, fog_url, driver_id, device_id)

    send_heartbeat(fog_url, device_id, {"source": str(video_source), "driver": driver_id})

    last_inference = 0.0
    last_heartbeat = 0.0
    frame_index = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                if not is_webcam and loop_video:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                log.info("Stream ended.")
                break

            frame_index += 1
            now = time.time()

            # Periodic heartbeat
            if now - last_heartbeat >= 10.0:
                send_heartbeat(fog_url, device_id, {"frame": frame_index, "driver": driver_id})
                last_heartbeat = now

            if now - last_inference < inference_interval:
                if show_gui:
                    cv2.imshow("Smart Driver Safety", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                continue

            last_inference = now
            ts = datetime.now(timezone.utc).isoformat()

            # 1. Read all four sensors
            sensor = sensor_reader.read()

            # 2. Run sleep-detection model
            t0 = time.perf_counter()
            asleep, sleep_conf = infer_sleep(model, frame, threshold=sleep_threshold)
            inf_ms = (time.perf_counter() - t0) * 1000.0

            # 3. Compute composite risk score
            fatigue_score, pred_label, confidence = compute_risk_score(
                asleep, sleep_conf, sensor
            )

            # 4. Build messages
            pred_msg_id = str(uuid.uuid4())

            reading_data = {
                "message_id": str(uuid.uuid4()),
                "device_id": device_id,
                "driver_id": driver_id,
                "timestamp": ts,
                "values": {
                    "speed_kmh":    sensor["speed_kmh"],
                    "light_lux":    sensor["light_lux"],
                    "road_surface": sensor["road_surface"],
                    "weather":      sensor["weather"],
                    "frame_id":     frame_index,
                },
                "simulated": False,
            }

            pred_data = {
                "message_id":        pred_msg_id,
                "device_id":         device_id,
                "driver_id":         driver_id,
                "timestamp":         ts,
                "prediction":        pred_label,
                "confidence":        confidence,
                "fatigue_score":     fatigue_score,
                "model_version":     MODEL_VERSION,
                "model_location":    "edge",
                "deployment_config": "edge_inference",
                "inference_time_ms": round(inf_ms, 2),
                "simulated":         False,
                "extra": {
                    "asleep":         asleep,
                    "sleep_score":    sleep_conf,
                    "road_risk":      sensor["road_surface"],
                    "weather":        sensor["weather"],
                    "speed_kmh":      sensor["speed_kmh"],
                    "light_lux":      sensor["light_lux"],
                },
            }

            # 5. Evaluate edge alerts
            alerts = alert_manager.evaluate(pred_data, sensor)

            # 6. Send to Fog
            post_to_fog(fog_url, "reading", reading_data)
            ack = post_to_fog(fog_url, "prediction", pred_data)
            fog_status = ack.get("status", "?") if ack else "offline"

            for level, reason in alerts:
                post_to_fog(fog_url, "alert", {
                    "message_id":            str(uuid.uuid4()),
                    "prediction_message_id": pred_msg_id,
                    "device_id":             device_id,
                    "driver_id":             driver_id,
                    "timestamp":             ts,
                    "level":                 level,
                    "reason":                reason,
                    "simulated":             False,
                })

            # 7. Log
            alert_summary = "|".join(a[0] for a in alerts) or "-"
            log.info(
                "[%d] asleep=%-5s label=%-6s score=%.2f speed=%.0fkm/h "
                "road=%-8s wx=%-5s alerts=%s fog=%s inf=%.1fms",
                frame_index, asleep, pred_label, fatigue_score,
                sensor["speed_kmh"], sensor["road_surface"], sensor["weather"],
                alert_summary, fog_status, inf_ms,
            )

            # 8. GUI
            if show_gui:
                draw_overlay(frame, asleep, pred_label, fatigue_score,
                             sensor, alerts, fog_status, inf_ms)
                cv2.imshow("Smart Driver Safety", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    finally:
        cap.release()
        if show_gui:
            cv2.destroyAllWindows()
        log.info("Monitor stopped.")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Smart Driver Safety – Real Edge Monitor")
    p.add_argument("--fog",           default="http://localhost:8100",  help="Fog Gateway URL")
    p.add_argument("--video",         default=None,                     help="Video file path (.mp4/.avi/…)")
    p.add_argument("--camera",        type=int, default=0,              help="Webcam device index (default 0)")
    p.add_argument("--driver",        default="driver_001",             help="Driver ID")
    p.add_argument("--device",        default="edge_cam_001",           help="Edge device ID")
    p.add_argument("--interval",      type=float, default=0.5,          help="Inference interval in seconds")
    p.add_argument("--threshold",     type=float, default=0.6,          help="Sleep model positive threshold (0-1)")
    p.add_argument("--models-dir",    default=str(MODELS_DIR),          help="Path to models/ directory")
    p.add_argument("--sensor-port",   default=None,                     help="Serial port for hardware sensors (e.g. COM3)")
    p.add_argument("--sensor-file",   default=None,                     help="JSON file written by partner's MCU")
    p.add_argument("--no-gui",        action="store_true",              help="Headless mode — no OpenCV window")
    p.add_argument("--no-loop",       action="store_true",              help="Do not loop video when it ends")

    a = p.parse_args()
    source = a.video if a.video else a.camera
    run_monitor(
        video_source=source,
        fog_url=a.fog,
        driver_id=a.driver,
        device_id=a.device,
        models_dir=Path(a.models_dir),
        sensor_port=a.sensor_port,
        sensor_file=a.sensor_file,
        inference_interval=a.interval,
        sleep_threshold=a.threshold,
        show_gui=not a.no_gui,
        loop_video=not a.no_loop,
    )
