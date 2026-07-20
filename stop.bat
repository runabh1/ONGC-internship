@echo off
title ONGC AI Cluster Monitor — STOP
color 0E

echo.
echo  ============================================================
echo    ONGC AI Cluster Monitor — Stopping...
echo  ============================================================
echo.

:: ── Check Docker Desktop is running ──────────────────────────
docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [INFO] Docker Desktop is not running — nothing to stop.
    echo.
    pause
    exit /b 0
)

:: ── Stop all containers ───────────────────────────────────────
echo  Stopping all containers (data is preserved)...
echo.
docker compose down

if %ERRORLEVEL% NEQ 0 (
    color 0C
    echo.
    echo  [ERROR] Something went wrong while stopping. See output above.
    echo.
    pause
    exit /b 1
)

echo.
echo  ============================================================
echo    All containers stopped. Your data is safe.
echo  ============================================================
echo.
echo  To start again:  Run  start.bat
echo.
pause
