"""Streamlit dashboard — Smart Driver Safety v2.
Env: CLOUD_URL, FOG_URL, API_KEY

Tabs:
  Live        – real-time risk score chart + sensor breakdown
  Alerts      – alert feed with reason column
  Historical  – filterable predictions table + CSV export
  Sensors     – latest sensor values per driver
  Devices     – device registry + fog status
"""
import os
import time

import pandas as pd
import requests
import streamlit as st

CLOUD = os.getenv("CLOUD_URL", "http://localhost:8000").rstrip("/")
FOG   = os.getenv("FOG_URL",   "http://localhost:8100").rstrip("/")
HEAD  = {"X-API-Key": os.environ["API_KEY"]} if os.getenv("API_KEY") else {}

st.set_page_config(page_title="Smart Driver Safety", layout="wide", page_icon="🚗")
st.title("🚗 Smart Driver Safety — Edge / Fog / Cloud Monitor")
st.caption("Sensors: speed · light · road surface · rain/fog  |  AI: sleep-detection model (True/False)")


def get(base, path, **params):
    try:
        r = requests.get(
            base + path,
            params={k: v for k, v in params.items() if v not in (None, "")},
            headers=HEAD, timeout=4,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Controls")
    auto = st.checkbox("Auto-refresh (3 s)", value=False)
    st.button("🔄 Refresh now")
    st.markdown("---")
    st.markdown("**Sensor inputs**")
    st.markdown("🚀 Speed (km/h)")
    st.markdown("💡 Light (lux)")
    st.markdown("🛣️ Road surface")
    st.markdown("🌧️ Rain / Fog detector")
    st.markdown("---")
    st.markdown("**AI model**")
    st.markdown("`driver_state_best.keras`")
    st.markdown("Output: `True` (asleep) / `False` (awake)")

# ── System overview bar ───────────────────────────────────────────────────────
health_cloud = get(CLOUD, "/health")
fog_status   = get(FOG,   "/status")
stats        = get(CLOUD, "/api/v1/stats") or {}

st.subheader("System Overview")
cols = st.columns(7)
cols[0].metric("☁️ Cloud API",      "🟢 UP" if health_cloud else "🔴 DOWN")
cols[1].metric("🌫️ Fog Gateway",    "🟢 UP" if fog_status else "🔴 DOWN")
cols[2].metric("🔗 Fog→Cloud",
               ("🟢 UP" if fog_status["cloud_up"] else "🔴 DOWN") if fog_status else "n/a")
cols[3].metric("📦 Fog buffer",     fog_status["buffered"] if fog_status else "n/a")
cols[4].metric("🔮 Predictions",    stats.get("predictions", 0))
cols[5].metric("🚨 Alerts",         stats.get("alerts", 0))
cols[6].metric("🧑‍💼 Drivers",        stats.get("drivers", 0))

cols2 = st.columns(4)
cols2[0].metric("📡 Readings",      stats.get("readings", 0))
cols2[1].metric("💤 Drowsy pred.",  stats.get("predictions_by_label", {}).get("drowsy", 0))
cols2[2].metric("⚠️ Warnings",
                stats.get("alerts_by_level", {}).get("warning", 0))
cols2[3].metric("🆘 Critical",
                stats.get("alerts_by_level", {}).get("critical", 0))

if fog_status and fog_status.get("last_error"):
    st.warning(f"⚠️ Fog cannot reach Cloud: {fog_status['last_error']}")

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_live, tab_alerts, tab_hist, tab_sensors, tab_dev = st.tabs(
    ["📈 Live", "🚨 Alerts", "📋 Historical", "🔬 Sensors", "🖥️ Devices & Fog"]
)

# ── Live ──────────────────────────────────────────────────────────────────────
with tab_live:
    preds = get(CLOUD, "/api/v1/predictions", limit=400)
    if preds:
        df = pd.DataFrame(preds)
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        # Expand extra JSON column if present
        if "extra" in df.columns:
            extra_df = df["extra"].apply(lambda x: x if isinstance(x, dict) else {})
            for col in ["asleep", "sleep_score", "speed_kmh", "road_risk", "weather"]:
                if any(col in e for e in extra_df):
                    df[col] = extra_df.apply(lambda e: e.get(col))

        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown("**Risk Score over time (by driver)**")
            piv = df.pivot_table(index="timestamp", columns="driver_id",
                                 values="fatigue_score", aggfunc="mean")
            st.line_chart(piv)

        with col_r:
            st.markdown("**Prediction labels distribution**")
            st.bar_chart(pd.Series(stats.get("predictions_by_label", {})))

        st.markdown("**Inference location breakdown**")
        loc_data = stats.get("predictions_by_model_location", {})
        if loc_data:
            st.dataframe(pd.DataFrame(loc_data).T, use_container_width=True)

        # Asleep events highlight
        if "asleep" in df.columns:
            asleep_df = df[df["asleep"] == True][
                ["timestamp", "driver_id", "fatigue_score", "prediction"]
            ].sort_values("timestamp", ascending=False).head(10)
            if not asleep_df.empty:
                st.markdown("**⚠️ Most recent ASLEEP detections**")
                st.dataframe(asleep_df, use_container_width=True)
    else:
        st.info("No predictions yet. Run the simulator or start the real monitor.")

# ── Alerts ────────────────────────────────────────────────────────────────────
with tab_alerts:
    al = get(CLOUD, "/api/v1/alerts", limit=300)
    if al:
        adf = pd.DataFrame(al)
        adf["timestamp"] = pd.to_datetime(adf["timestamp"])

        # Colour-code by level
        def row_colour(row):
            if row.get("level") == "critical":
                return ["background-color: #3d0000"] * len(row)
            elif row.get("level") == "warning":
                return ["background-color: #2d2000"] * len(row)
            return [""] * len(row)

        st.dataframe(
            adf.sort_values("timestamp", ascending=False),
            use_container_width=True,
        )
    else:
        st.info("No alerts yet.")

# ── Historical ────────────────────────────────────────────────────────────────
with tab_hist:
    f = st.columns(5)
    driver  = f[0].text_input("Driver ID")
    label   = f[1].selectbox("Prediction", ["", "normal", "alert", "drowsy"])
    mver    = f[2].text_input("Model version")
    cfg     = f[3].text_input("Deployment config")
    has_al  = f[4].selectbox("Alert status", ["any", "with alert", "no alert"])
    ha = {"any": None, "with alert": "true", "no alert": "false"}[has_al]

    rows = get(CLOUD, "/api/v1/predictions",
               driver_id=driver, prediction=label, model_version=mver,
               deployment_config=cfg, has_alert=ha, limit=2000)
    hdf = pd.DataFrame(rows) if rows else pd.DataFrame()
    st.dataframe(hdf, use_container_width=True)
    if not hdf.empty:
        st.download_button("⬇️ Export CSV", hdf.to_csv(index=False), "predictions.csv", "text/csv")

# ── Sensors ───────────────────────────────────────────────────────────────────
with tab_sensors:
    readings = get(CLOUD, "/api/v1/readings", limit=500)
    if readings:
        rdf = pd.DataFrame(readings)
        rdf["timestamp"] = pd.to_datetime(rdf["timestamp"])

        # Flatten the values JSON column
        vals = rdf["values"].apply(lambda x: x if isinstance(x, dict) else {})
        for col in ["speed_kmh", "light_lux", "road_surface", "weather"]:
            rdf[col] = vals.apply(lambda v: v.get(col))

        drivers = rdf["driver_id"].unique().tolist()
        sel_driver = st.selectbox("Select driver", ["All"] + sorted(drivers))
        view = rdf if sel_driver == "All" else rdf[rdf["driver_id"] == sel_driver]

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**🚀 Speed over time (km/h)**")
            if "speed_kmh" in view.columns:
                spiv = view.pivot_table(index="timestamp", columns="driver_id",
                                        values="speed_kmh", aggfunc="mean")
                st.line_chart(spiv)
        with c2:
            st.markdown("**💡 Light level over time (lux)**")
            if "light_lux" in view.columns:
                lpiv = view.pivot_table(index="timestamp", columns="driver_id",
                                        values="light_lux", aggfunc="mean")
                st.line_chart(lpiv)

        c3, c4 = st.columns(2)
        with c3:
            st.markdown("**🛣️ Road surface distribution**")
            if "road_surface" in view.columns:
                st.bar_chart(view["road_surface"].value_counts())
        with c4:
            st.markdown("**🌧️ Weather distribution**")
            if "weather" in view.columns:
                st.bar_chart(view["weather"].value_counts())

        st.markdown("**Latest sensor readings**")
        display_cols = [c for c in
            ["timestamp","driver_id","speed_kmh","light_lux","road_surface","weather","simulated"]
            if c in view.columns]
        st.dataframe(view[display_cols].sort_values("timestamp", ascending=False).head(100),
                     use_container_width=True)
    else:
        st.info("No sensor readings yet.")

# ── Devices & Fog ─────────────────────────────────────────────────────────────
with tab_dev:
    st.markdown("**Cloud device registry**")
    devices = get(CLOUD, "/api/v1/devices")
    st.dataframe(pd.DataFrame(devices or []), use_container_width=True)

    st.markdown("**Fog gateway status**")
    st.json(fog_status or {"error": "Fog unreachable"})

# ── Auto-refresh ──────────────────────────────────────────────────────────────
if auto:
    time.sleep(3)
    st.rerun()
