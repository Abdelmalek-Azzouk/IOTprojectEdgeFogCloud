# Smart Driver Safety — Cloud, Fog gateway, API, DB, Dashboard

Infrastructure around an **external** fatigue-detection model. No AI is implemented here;
everything the simulator emits is marked `simulated=True`.

```
Edge (simulator) --HTTP--> Fog gateway :8100 --HTTP batch--> Cloud API :8000 --SQLAlchemy--> PostgreSQL / SQLite
                           (buffer + retry)                      ^
                                                   Streamlit dashboard :8501
```

| Piece | Where | Notes |
|---|---|---|
| REST API | `cloud/api.py` | FastAPI; optional `X-API-Key` (set `API_KEY`) |
| Database | `cloud/models.py`, `database.py` | tables: `devices`, `sensor_readings`, `predictions`, `alerts`; SQLite default, Postgres via `DATABASE_URL` |
| Idempotent ingest | `cloud/event_manager.py` | unique `message_id` ⇒ retries never duplicate |
| Fog | `fog/gateway.py`, `forwarder.py`, `message_buffer.py` | store-and-forward when Cloud is down, ordered flush, optional on-disk buffer |
| Dashboard | `dashboard/app.py` | overview, live charts, alerts, filterable history + CSV export, devices/Fog status |
| Compose | `docker-compose.yml` | db, cloud-api, fog, dashboard (+ `simulator` profile) |

## Run with Docker (Windows 11: Docker Desktop)
```
copy .env.example .env
docker compose up --build
docker compose --profile sim up simulator      # synthetic traffic
```
API docs http://localhost:8000/docs · Dashboard http://localhost:8501 · Fog status http://localhost:8100/status

Cloud outage demo: `docker compose stop cloud-api` → run simulator → Fog `buffered` grows →
`docker compose start cloud-api` → buffer drains automatically, dashboard catches up.

## Run without Docker (SQLite)
```
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
uvicorn cloud.api:app --port 8000
uvicorn fog.gateway:app --port 8100            # new terminal
streamlit run dashboard/app.py                 # new terminal
python -m edge.simulator --drivers 3 --count 30
pytest
```

## Main endpoints
`POST /api/v1/events/batch` (Fog) · `POST /api/v1/{readings,predictions,alerts}` ·
`GET /api/v1/{predictions,readings,alerts,stats,devices}` (filters: driver_id, prediction, model_version,
deployment_config, model_location, has_alert, since, until) · `GET /health`

## Status
- Implemented: all of the above.
- Simulated: Edge devices, model outputs, readings (`simulated=True`).
- Awaiting AI team: real input/output schema — readings store free-form JSON `values`; prediction fields
  (`prediction`, `confidence`, `fatigue_score`) in `cloud/schemas.py` should be aligned with their model.
- Not yet in this slice: MQTT, model adapter, benchmarking module, scenario runner (rest of the full spec).
