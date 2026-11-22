"""Idempotent persistence of events + query/statistics helpers."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import ValidationError

from cloud import models, schemas

_TYPES = {
    "reading": (schemas.ReadingIn, models.SensorReading),
    "prediction": (schemas.PredictionIn, models.Prediction),
    "alert": (schemas.AlertIn, models.Alert),
}


def touch_device(db: Session, device_id: str, layer: str = "edge", meta: dict | None = None):
    dev = db.get(models.Device, device_id)
    now = datetime.now(timezone.utc)
    if dev is None:
        db.add(models.Device(device_id=device_id, layer=layer, last_seen=now, meta=meta or {}))
    else:
        dev.last_seen = now
        if meta:
            dev.meta = meta


def store_event(db: Session, kind: str, data: dict) -> str:
    """Returns 'stored' or 'duplicate'. Raises ValidationError on bad payload.

    Duplicate handling: message_id is UNIQUE, so Fog retries / at-least-once
    delivery never create double rows.
    """
    schema, table = _TYPES[kind]
    obj = schema(**data)
    if db.scalar(select(table.id).where(table.message_id == obj.message_id)):
        return "duplicate"
    row = table(**obj.model_dump())
    try:
        db.add(row)
        touch_device(db, obj.device_id)
        db.commit()  # one transaction per event: a bad/duplicate event never rolls back good ones
    except IntegrityError as exc:
        db.rollback()
        if "message_id" in str(exc.orig) or "UNIQUE" in str(exc.orig).upper():
            return "duplicate"  # concurrent insert of the same message_id
        raise
    return "stored"


def store_batch(db: Session, batch: schemas.BatchIn) -> schemas.BatchResult:
    stored = dups = 0
    rejected: list[dict] = []
    touch_device(db, batch.fog_id, layer="fog")
    db.commit()
    # predictions before alerts so the alert FK resolves inside one batch
    order = {"reading": 0, "prediction": 1, "alert": 2}
    for ev in sorted(batch.events, key=lambda e: order[e.type]):
        try:
            res = store_event(db, ev.type, ev.data)
        except ValidationError as exc:
            rejected.append({"message_id": ev.data.get("message_id"), "error": exc.errors(include_url=False)[0]["msg"]})
            continue
        except IntegrityError as exc:  # e.g. alert referencing unknown prediction (Postgres FK)
            rejected.append({"message_id": ev.data.get("message_id"), "error": "integrity: " + str(exc.orig)[:80]})
            continue
        stored += res == "stored"
        dups += res == "duplicate"
    return schemas.BatchResult(stored=stored, duplicates=dups, rejected=rejected)


def stats(db: Session) -> dict:
    cnt = lambda t: db.scalar(select(func.count()).select_from(t)) or 0
    by_label = dict(db.execute(select(models.Prediction.prediction, func.count()).group_by(models.Prediction.prediction)).all())
    by_loc = {
        (k or "unknown"): {"count": c, "avg_inference_ms": round(a, 2) if a is not None else None}
        for k, c, a in db.execute(
            select(models.Prediction.model_location, func.count(), func.avg(models.Prediction.inference_time_ms))
            .group_by(models.Prediction.model_location)
        ).all()
    }
    by_level = dict(db.execute(select(models.Alert.level, func.count()).group_by(models.Alert.level)).all())
    return {
        "readings": cnt(models.SensorReading),
        "predictions": cnt(models.Prediction),
        "alerts": cnt(models.Alert),
        "drivers": db.scalar(select(func.count(func.distinct(models.Prediction.driver_id)))) or 0,
        "predictions_by_label": by_label,
        "predictions_by_model_location": by_loc,
        "alerts_by_level": by_level,
    }


def device_status(db: Session, online_within_s: int = 30) -> list[dict]:
    now = datetime.now(timezone.utc)
    out = []
    for d in db.scalars(select(models.Device).order_by(models.Device.device_id)):
        ls = d.last_seen if d.last_seen.tzinfo else d.last_seen.replace(tzinfo=timezone.utc)
        out.append({
            "device_id": d.device_id, "layer": d.layer, "last_seen": ls.isoformat(),
            "online": (now - ls) <= timedelta(seconds=online_within_s), "meta": d.meta,
        })
    return out
