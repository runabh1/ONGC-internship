@echo off
title ONGC AI Cluster Monitor — REBUILD
color 0B

echo.
echo  ============================================================
echo    ONGC AI Cluster Monitor — Rebuild and Start
echo  ============================================================
echo  Use this after pulling code updates or making code changes.
echo  ============================================================
echo.

:: ── Check Docker Desktop is running ──────────────────────────
echo  [1/3] Checking Docker Desktop...
docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    color 0C
    echo.
    echo  [ERROR] Docker Desktop is NOT running!
    echo  Open Docker Desktop, wait for the whale icon, then try again.
    echo.
    pause
    exit /b 1
)
echo  [OK] Docker Desktop is running.
echo.

:: ── Rebuild and start ─────────────────────────────────────────
echo  [2/3] Rebuilding images and starting containers...
echo  (This may take a few minutes if dependencies changed)
echo.
docker compose up --build -d

if %ERRORLEVEL% NEQ 0 (
    color 0C
    echo.
    echo  [ERROR] Build or start failed. Check output above.
    echo.
    pause
    exit /b 1
)

:: ── Open dashboard ────────────────────────────────────────────
echo.
echo  [3/3] Opening dashboard...
timeout /t 5 /nobreak >nul
start "" http://localhost:3001

echo.
echo  ============================================================
echo    Rebuild complete!  Dashboard: http://localhost:3001
echo  ============================================================
echo.
pause
