"""Cloud REST API (FastAPI). Run: uvicorn cloud.api:app --port 8000"""
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from cloud import event_manager as em
from cloud import models, schemas
from cloud.database import get_db, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Smart Driver Safety - Cloud API", version="1.0.0", lifespan=lifespan)


def require_key(x_api_key: Optional[str] = Header(default=None)):
    """Optional API key. Enabled only when API_KEY env var is set (prod); open for local demo."""
    expected = os.getenv("API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(401, "invalid or missing X-API-Key")


auth = [Depends(require_key)]


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "layer": "cloud", "time": datetime.utcnow().isoformat() + "Z"}


# ---------- ingestion (called by Fog) ----------
@app.post("/api/v1/events/batch", response_model=schemas.BatchResult, dependencies=auth)
def ingest_batch(batch: schemas.BatchIn, db: Session = Depends(get_db)):
    return em.store_batch(db, batch)


def _single(kind: str, payload: dict, db: Session):
    try:
        res = em.store_event(db, kind, payload)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False))
    return {"result": res}


@app.post("/api/v1/readings", status_code=201, dependencies=auth)
def post_reading(body: schemas.ReadingIn, db: Session = Depends(get_db)):
    return _single("reading", body.model_dump(mode="json"), db)


@app.post("/api/v1/predictions", status_code=201, dependencies=auth)
def post_prediction(body: schemas.PredictionIn, db: Session = Depends(get_db)):
    return _single("prediction", body.model_dump(mode="json"), db)


@app.post("/api/v1/alerts", status_code=201, dependencies=auth)
def post_alert(body: schemas.AlertIn, db: Session = Depends(get_db)):
    return _single("alert", body.model_dump(mode="json"), db)


@app.post("/api/v1/devices/heartbeat", dependencies=auth)
def heartbeat(body: schemas.HeartbeatIn, db: Session = Depends(get_db)):
    em.touch_device(db, body.device_id, body.layer, body.meta)
    db.commit()  # explicit: heartbeat is not routed through store_event
    return {"ok": True}


# ---------- queries (used by dashboard) ----------
def _row(r) -> dict:
    d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def _filters(q, table, driver_id, since, until):
    if driver_id:
        q = q.where(table.driver_id == driver_id)
    if since:
        q = q.where(table.timestamp >= since)
    if until:
        q = q.where(table.timestamp <= until)
    return q


@app.get("/api/v1/predictions", dependencies=auth)
def list_predictions(
    driver_id: Optional[str] = None, prediction: Optional[str] = None,
    model_version: Optional[str] = None, deployment_config: Optional[str] = None,
    model_location: Optional[str] = None,
    since: Optional[datetime] = None, until: Optional[datetime] = None,
    has_alert: Optional[bool] = None,
    limit: int = Query(200, le=5000), offset: int = 0, db: Session = Depends(get_db),
):
    T = models.Prediction
    q = _filters(select(T), T, driver_id, since, until)
    for col, val in ((T.prediction, prediction), (T.model_version, model_version),
                     (T.deployment_config, deployment_config), (T.model_location, model_location)):
        if val:
            q = q.where(col == val)
    if has_alert is not None:
        sub = select(models.Alert.prediction_message_id).where(models.Alert.prediction_message_id.is_not(None))
        q = q.where(T.message_id.in_(sub) if has_alert else T.message_id.not_in(sub))
    rows = db.scalars(q.order_by(T.timestamp.desc()).limit(limit).offset(offset)).all()
    return [_row(r) for r in rows]


@app.get("/api/v1/readings", dependencies=auth)
def list_readings(driver_id: Optional[str] = None, since: Optional[datetime] = None,
                  until: Optional[datetime] = None, limit: int = Query(200, le=5000),
                  db: Session = Depends(get_db)):
    T = models.SensorReading
    q = _filters(select(T), T, driver_id, since, until).order_by(T.timestamp.desc()).limit(limit)
    return [_row(r) for r in db.scalars(q)]


@app.get("/api/v1/alerts", dependencies=auth)
def list_alerts(driver_id: Optional[str] = None, level: Optional[str] = None,
                since: Optional[datetime] = None, until: Optional[datetime] = None,
                limit: int = Query(200, le=5000), db: Session = Depends(get_db)):
    T = models.Alert
    q = _filters(select(T), T, driver_id, since, until)
    if level:
        q = q.where(T.level == level)
    return [_row(r) for r in db.scalars(q.order_by(T.timestamp.desc()).limit(limit))]


@app.get("/api/v1/stats", dependencies=auth)
def get_stats(db: Session = Depends(get_db)):
    return em.stats(db)


@app.get("/api/v1/devices", dependencies=auth)
def get_devices(online_within_s: int = 30, db: Session = Depends(get_db)):
    return em.device_status(db, online_within_s)
