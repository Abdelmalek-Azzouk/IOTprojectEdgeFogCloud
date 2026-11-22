# Local Services Runner (No Docker required)
# Runs the Cloud REST API, Fog Gateway, and Dashboard locally using Anaconda Python.
# Uses SQLite for zero-config persistence.
#
# Usage:
#   .\run_local_services.ps1

$PythonExe = "C:\Users\AmaTek\anaconda3\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Anaconda Python not found at $PythonExe"
    exit 1
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Starting Smart Driver Safety Local Services             " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# Ensure data directory exists
if (-not (Test-Path "data")) {
    New-Item -ItemType Directory -Path "data" | Out-Null
}

# 1. Start Cloud REST API in its own window
Write-Host "1. Launching Cloud REST API on port 8000 ..." -ForegroundColor Green
$cloudJob = Start-Process -FilePath $PythonExe -ArgumentList "-m", "uvicorn", "cloud.api:app", "--host", "0.0.0.0", "--port", "8000" -PassThru

Start-Sleep -Seconds 2

# 2. Start Fog Gateway in its own window
Write-Host "2. Launching Fog Gateway on port 8100 ..." -ForegroundColor Green
$fogJob = Start-Process -FilePath $PythonExe -ArgumentList "-m", "uvicorn", "fog.gateway:app", "--host", "0.0.0.0", "--port", "8100" -PassThru

Start-Sleep -Seconds 2

# 3. Start Streamlit Dashboard on port 8080
Write-Host "3. Launching Streamlit Dashboard on port 8080 ..." -ForegroundColor Green
$dashJob = Start-Process -FilePath $PythonExe -ArgumentList "-m", "streamlit", "run", "dashboard/app.py", "--server.port", "8080", "--server.address", "127.0.0.1" -PassThru

Write-Host "----------------------------------------------------------" -ForegroundColor Gray
Write-Host "All 3 services are active:" -ForegroundColor Cyan
Write-Host "  • Cloud REST API   : http://localhost:8000/docs" -ForegroundColor Yellow
Write-Host "  • Fog Gateway      : http://localhost:8100/status" -ForegroundColor Yellow
Write-Host "  • Dashboard        : http://localhost:8080" -ForegroundColor Yellow
Write-Host "----------------------------------------------------------" -ForegroundColor Gray
Write-Host "Keep this terminal open to keep services alive." -ForegroundColor White
Write-Host "Press Ctrl+C or close this window when you want to stop all services." -ForegroundColor DarkGray

try {
    Wait-Process -Id $cloudJob.Id, $fogJob.Id, $dashJob.Id
} finally {
    Write-Host "Stopping services..." -ForegroundColor Red
    Stop-Process -Id $cloudJob.Id, $fogJob.Id, $dashJob.Id -ErrorAction SilentlyContinue
}
