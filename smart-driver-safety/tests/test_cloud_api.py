"""Cloud API + DB + Fog integration. Needs fastapi, sqlalchemy, httpx (skipped if missing)."""
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

import cloud.database as database  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    eng = database.make_engine("sqlite://")
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "SessionLocal", database.sessionmaker(bind=eng, expire_on_commit=False))
    database.init_db(eng)
    from cloud.api import app

    def _session():
        s = database.SessionLocal()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[database.get_db] = _session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def pred(driver="driver_001", label="drowsy", score=0.9, **kw):
    d = {"message_id": str(uuid.uuid4()), "device_id": "edge_001", "driver_id": driver,
         "timestamp": datetime.now(timezone.utc).isoformat(), "prediction": label, "confidence": 0.9,
         "fatigue_score": score, "model_version": "dummy-v1", "model_location": "edge",
         "deployment_config": "edge_inference", "inference_time_ms": 18.5, "simulated": True}
    d.update(kw)
    return d


def alert(pm, **kw):
    d = {"message_id": str(uuid.uuid4()), "prediction_message_id": pm, "device_id": "edge_001",
         "driver_id": "driver_001", "timestamp": datetime.now(timezone.utc).isoformat(),
         "level": "critical", "reason": "test", "simulated": True}
    d.update(kw)
    return d


def test_store_and_query_prediction(client):
    p = pred()
    assert client.post("/api/v1/predictions", json=p).status_code == 201
    rows = client.get("/api/v1/predictions", params={"driver_id": "driver_001"}).json()
    assert len(rows) == 1 and rows[0]["prediction"] == "drowsy" and rows[0]["simulated"] is True


def test_duplicate_is_idempotent(client):
    p = pred()
    assert client.post("/api/v1/predictions", json=p).json()["result"] == "stored"
    assert client.post("/api/v1/predictions", json=p).json()["result"] == "duplicate"
    assert client.get("/api/v1/stats").json()["predictions"] == 1


def test_batch_with_alert_and_duplicates(client):
    p = pred()
    a = alert(p["message_id"])
    batch = {"fog_id": "fog_001", "events": [
        {"type": "alert", "data": a},               # alert listed first: must still resolve FK
        {"type": "prediction", "data": p},
        {"type": "prediction", "data": p},          # duplicate inside batch
        {"type": "prediction", "data": {"bad": 1}},  # invalid -> rejected, not fatal
    ]}
    res = client.post("/api/v1/events/batch", json=batch).json()
    assert res["stored"] == 2 and res["duplicates"] == 1 and len(res["rejected"]) == 1
    assert client.get("/api/v1/alerts").json()[0]["prediction_message_id"] == p["message_id"]
    assert client.get("/api/v1/predictions", params={"has_alert": True}).json()[0]["message_id"] == p["message_id"]


def test_validation_rejects_out_of_range(client):
    assert client.post("/api/v1/predictions", json=pred(confidence=1.7)).status_code == 422


def test_filters_and_stats(client):
    client.post("/api/v1/predictions", json=pred(driver="driver_001", label="drowsy"))
    client.post("/api/v1/predictions", json=pred(driver="driver_002", label="normal", score=0.1, model_location="fog"))
    assert len(client.get("/api/v1/predictions", params={"prediction": "normal"}).json()) == 1
    assert len(client.get("/api/v1/predictions", params={"model_location": "fog"}).json()) == 1
    s = client.get("/api/v1/stats").json()
    assert s["drivers"] == 2 and s["predictions_by_label"] == {"drowsy": 1, "normal": 1}


def test_heartbeat_and_device_status(client):
    client.post("/api/v1/devices/heartbeat", json={"device_id": "edge_009", "layer": "edge"})
    dev = {d["device_id"]: d for d in client.get("/api/v1/devices").json()}
    assert dev["edge_009"]["online"] is True


def test_api_key_enforced(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    assert client.get("/api/v1/stats").status_code == 401
    assert client.get("/api/v1/stats", headers={"X-API-Key": "secret"}).status_code == 200
    assert client.get("/health").status_code == 200   # health stays open for orchestrators


def test_fog_to_cloud_end_to_end(client):
    """Real Fog forwarder -> real Cloud API (in-process) -> DB, incl. outage + recovery."""
    from fog.forwarder import CloudForwarder
    from fog.message_buffer import MessageBuffer

    state = {"up": True}

    def send(events):
        if not state["up"]:
            raise ConnectionError("down")
        r = client.post("/api/v1/events/batch", json={"fog_id": "fog_001", "events": events})
        r.raise_for_status()
        return r.json()

    fw = CloudForwarder(send, MessageBuffer(100))
    p1 = pred()
    assert fw.submit({"type": "prediction", "data": p1}) == "forwarded"
    state["up"] = False
    p2, p3 = pred(), pred()
    a = alert(p3["message_id"])
    for e in ({"type": "prediction", "data": p2}, {"type": "prediction", "data": p3}, {"type": "alert", "data": a}):
        assert fw.submit(e) == "buffered"
    assert client.get("/api/v1/stats").json()["predictions"] == 1   # cloud only has the first
    state["up"] = True
    assert fw.flush() == 3
    s = client.get("/api/v1/stats").json()
    assert s["predictions"] == 3 and s["alerts"] == 1
