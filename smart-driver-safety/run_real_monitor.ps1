# Run the Real Driver Safety Monitor on the Edge
# Examples:
#   .\run_real_monitor.ps1 -Video "data/test_driver.mp4"
#   .\run_real_monitor.ps1 -Camera 0
#   .\run_real_monitor.ps1 -Video "data/test_driver.mp4" -SensorFile "data/sensors.json"
#   .\run_real_monitor.ps1 -Camera 0 -SensorPort "COM3"
#   .\run_real_monitor.ps1 -Video "data/test_driver.mp4" -NoGui

param(
    [string]$Video = "",
    [int]$Camera = 0,
    [string]$Driver = "driver_001",
    [string]$Device = "edge_cam_001",
    [string]$Fog = "http://localhost:8100",
    [string]$SensorFile = "",
    [string]$SensorPort = "",
    [float]$Interval = 0.5,
    [float]$Threshold = 0.6,
    [switch]$NoGui,
    [switch]$NoLoop
)

$PythonExe = "C:\Users\AmaTek\anaconda3\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Anaconda Python not found at $PythonExe"
    exit 1
}

$ArgsList = @(
    "-m", "edge.real_driver_monitor",
    "--fog", $Fog,
    "--driver", $Driver,
    "--device", $Device,
    "--interval", "$Interval",
    "--threshold", "$Threshold"
)

if ($Video -ne "") {
    if (-not (Test-Path $Video)) {
        Write-Error "Video file not found: $Video"
        exit 1
    }
    $ArgsList += @("--video", (Resolve-Path $Video).Path)
} else {
    $ArgsList += @("--camera", "$Camera")
}

if ($SensorFile -ne "") {
    if (-not (Test-Path $SensorFile)) {
        Write-Error "Sensor file not found: $SensorFile"
        exit 1
    }
    $ArgsList += @("--sensor-file", (Resolve-Path $SensorFile).Path)
}

if ($SensorPort -ne "") {
    $ArgsList += @("--sensor-port", $SensorPort)
}

if ($NoGui) {
    $ArgsList += "--no-gui"
}

if ($NoLoop) {
    $ArgsList += "--no-loop"
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Smart Driver Safety — Edge Real Monitor                " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Target Fog Gateway : $Fog" -ForegroundColor Gray
Write-Host "Driver ID          : $Driver" -ForegroundColor Gray
Write-Host "Device ID          : $Device" -ForegroundColor Gray

if ($Video -ne "") {
    Write-Host "Camera Feed        : Video file ($Video)" -ForegroundColor Yellow
} else {
    Write-Host "Camera Feed        : Live Webcam (Index $Camera)" -ForegroundColor Yellow
}

if ($SensorPort -ne "") {
    Write-Host "Sensors Source     : Serial Port ($SensorPort)" -ForegroundColor Yellow
} elseif ($SensorFile -ne "") {
    Write-Host "Sensors Source     : JSON File ($SensorFile)" -ForegroundColor Yellow
} else {
    Write-Host "Sensors Source     : Built-in Demo Generator (Realistic test values)" -ForegroundColor DarkGray
}

Write-Host "Press 'Q' inside the preview window to stop." -ForegroundColor Green
Write-Host "----------------------------------------------------------" -ForegroundColor Gray

& $PythonExe $ArgsList
