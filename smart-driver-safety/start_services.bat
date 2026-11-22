@echo off
title Smart Driver Safety - Services Daemon
cd /d "C:\Users\AmaTek\Documents\M2\IOT\project\smart-driver-safety"

echo ========================================================
echo   Starting Smart Driver Safety Backend Services
echo ========================================================

start "Cloud-API" /min "C:\Users\AmaTek\anaconda3\python.exe" -m uvicorn cloud.api:app --host 0.0.0.0 --port 8000
timeout /t 2 /nobreak >nul

start "Fog-Gateway" /min "C:\Users\AmaTek\anaconda3\python.exe" -m uvicorn fog.gateway:app --host 0.0.0.0 --port 8100
timeout /t 2 /nobreak >nul

start "Streamlit-Dashboard" /min "C:\Users\AmaTek\anaconda3\python.exe" -m streamlit run dashboard/app.py --server.port 8080 --server.address 127.0.0.1 --server.headless true

echo All 3 services launched in independent Windows processes.
echo   Cloud API  : http://localhost:8000/docs
echo   Fog Gateway: http://localhost:8100/status
echo   Dashboard  : http://localhost:8080
