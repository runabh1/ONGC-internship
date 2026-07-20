@echo off
title ONGC AI Cluster Monitor — START
color 0A

echo.
echo  ============================================================
echo    ONGC AI Cluster Monitor — Starting...
echo  ============================================================
echo.

:: ── Check Docker Desktop is running ──────────────────────────
echo  [1/4] Checking Docker Desktop...
docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    color 0C
    echo.
    echo  [ERROR] Docker Desktop is NOT running!
    echo.
    echo  Please:
    echo    1. Open Docker Desktop from the Start Menu
    echo    2. Wait for the whale icon in the system tray
    echo    3. Run this script again
    echo.
    pause
    exit /b 1
)
echo  [OK] Docker Desktop is running.
echo.

:: ── Start all containers ──────────────────────────────────────
echo  [2/4] Starting all containers...
docker compose up -d
if %ERRORLEVEL% NEQ 0 (
    color 0C
    echo.
    echo  [ERROR] Failed to start containers. Check the output above.
    echo  Tip: Run "docker compose logs" to see what went wrong.
    echo.
    pause
    exit /b 1
)
echo.

:: ── Wait for backend to be healthy ───────────────────────────
echo  [3/4] Waiting for backend to be ready (up to 30s)...
set RETRIES=0
:WAIT_LOOP
    timeout /t 3 /nobreak >nul
    curl -s -o nul -w "%%{http_code}" http://localhost:8000/health | findstr "200" >nul 2>&1
    if %ERRORLEVEL% EQU 0 goto READY
    set /a RETRIES+=1
    if %RETRIES% GEQ 10 (
        echo  [WARN] Backend is taking longer than usual — it may still be starting.
        goto OPEN_BROWSER
    )
    echo         Still waiting... (%RETRIES%/10)
goto WAIT_LOOP

:READY
echo  [OK] Backend is ready!
echo.

:: ── Open the dashboard in the default browser ─────────────────
:OPEN_BROWSER
echo  [4/4] Opening dashboard in your browser...
start "" http://localhost:3001
echo.

:: ── Print status ──────────────────────────────────────────────
echo  ============================================================
echo    All services started!
echo  ============================================================
echo.
echo    Dashboard   :  http://localhost:3001
echo    API         :  http://localhost:8000
echo    API Docs    :  http://localhost:8000/docs
echo    Prometheus  :  http://localhost:9090
echo.
echo  ============================================================
echo.
echo  To check container status:   docker compose ps
echo  To view live logs:           docker compose logs -f
echo  To stop everything:          Run  stop.bat
echo.
pause
