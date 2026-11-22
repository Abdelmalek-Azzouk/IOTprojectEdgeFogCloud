"""Fog gateway (FastAPI). Receives events from Edge, forwards to Cloud with store-and-forward.

Run: uvicorn fog.gateway:app --port 8100
Env: CLOUD_URL, API_KEY, FOG_ID, FOG_BUFFER_PATH
"""
import os
import time
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI

from cloud.schemas import Envelope, HeartbeatIn
from fog.forwarder import CloudForwarder
from fog.message_buffer import MessageBuffer

CLOUD_URL = os.getenv("CLOUD_URL", "http://localhost:8000").rstrip("/")
FOG_ID = os.getenv("FOG_ID", "fog_001")
HEADERS = {"X-API-Key": os.environ["API_KEY"]} if os.getenv("API_KEY") else {}
TIMEOUT = float(os.getenv("CLOUD_TIMEOUT_S", "3"))


def cloud_send(events: list[dict]) -> dict:
    r = requests.post(f"{CLOUD_URL}/api/v1/events/batch", json={"fog_id": FOG_ID, "events": events},
                      headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


buffer = MessageBuffer(int(os.getenv("FOG_BUFFER_MAX", "10000")), os.getenv("FOG_BUFFER_PATH") or None)
forwarder = CloudForwarder(cloud_send, buffer)
edges: dict[str, dict] = {}  # connected edge devices
started = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    forwarder.start(float(os.getenv("FOG_RETRY_S", "2")))
    yield
    forwarder.stop()


app = FastAPI(title="Smart Driver Safety - Fog Gateway", version="1.0.0", lifespan=lifespan)


def _note_edge(device_id: str):
    e = edges.setdefault(device_id, {"messages": 0})
    e["last_seen"] = time.time()
    e["messages"] += 1


@app.post("/ingest")
def ingest(env: Envelope):
    t0 = time.perf_counter()  # monotonic: Fog processing time
    _note_edge(env.data.get("device_id", "unknown"))
    event = env.model_dump(mode="json")
    status = forwarder.submit(event)
    return {"message_id": env.data["message_id"], "status": status, "fog_id": FOG_ID,
            "fog_processing_ms": round((time.perf_counter() - t0) * 1000, 3)}


@app.post("/heartbeat")
def heartbeat(hb: HeartbeatIn):
    _note_edge(hb.device_id)
    try:  # best-effort; heartbeats are not buffered (stale by the time cloud returns)
        requests.post(f"{CLOUD_URL}/api/v1/devices/heartbeat", json=hb.model_dump(), headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException:
        pass
    return {"ok": True}


@app.get("/status")
def status():
    now = time.time()
    return {
        "fog_id": FOG_ID, "uptime_s": round(now - started, 1),
        "cloud_url": CLOUD_URL, "cloud_up": forwarder.cloud_up, "last_error": forwarder.last_error,
        "buffered": len(buffer), "buffer_dropped": buffer.dropped, "counters": forwarder.counters,
        "edges": {k: {"messages": v["messages"], "idle_s": round(now - v["last_seen"], 1)} for k, v in edges.items()},
    }


@app.post("/flush")
def flush():
    return {"delivered": forwarder.flush(), "remaining": len(buffer)}


@app.get("/health")
def health():
    return {"status": "ok", "layer": "fog"}
