"""Pydantic schemas = the message contract between Edge -> Fog -> Cloud.

Sensor reading `values` dict carries all four physical sensors:
  speed_kmh      : float   vehicle speed in km/h
  light_lux      : float   ambient light in lux
  road_surface   : str     "dry" | "wet" | "slippery"
  weather        : str     "clear" | "rain" | "fog"

Prediction `extra` dict carries raw model / sensor details:
  asleep         : bool    raw output of the sleep-detection model
  road_risk      : str     "low" | "medium" | "high"  (derived from sensors)
"""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


class ReadingIn(BaseModel):
    message_id: str
    device_id: str
    driver_id: str
    timestamp: datetime
    # Physical sensor values
    values: dict[str, Any] = Field(default_factory=dict)
    simulated: bool = False


class PredictionIn(BaseModel):
    message_id: str
    device_id: str
    driver_id: str
    timestamp: datetime
    # "awake" | "drowsy" (mapped from model's True/False)
    prediction: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    # 0.0 (fully awake, safe road) … 1.0 (asleep, hazardous road)
    fatigue_score: float | None = Field(default=None, ge=0, le=1)
    model_version: str | None = None
    model_location: Literal["edge", "fog", "cloud"] | None = None
    deployment_config: str | None = None
    inference_time_ms: float | None = Field(default=None, ge=0)
    simulated: bool = False
    # Raw details: asleep bool, road_risk, sensor breakdown
    extra: dict[str, Any] = Field(default_factory=dict)


class AlertIn(BaseModel):
    message_id: str
    prediction_message_id: str | None = None
    device_id: str
    driver_id: str
    timestamp: datetime
    level: Literal["warning", "critical"]
    # human-readable cause: "asleep", "skid_risk", "low_visibility", "speed+fog", …
    reason: str = ""
    simulated: bool = False


class Envelope(BaseModel):
    """What Fog forwards: one typed event."""
    type: Literal["reading", "prediction", "alert"]
    data: dict[str, Any]


class BatchIn(BaseModel):
    fog_id: str = "fog_001"
    events: list[Envelope]


class BatchResult(BaseModel):
    stored: int
    duplicates: int
    rejected: list[dict[str, Any]] = Field(default_factory=list)


class HeartbeatIn(BaseModel):
    device_id: str
    layer: Literal["edge", "fog"] = "edge"
    meta: dict[str, Any] = Field(default_factory=dict)
