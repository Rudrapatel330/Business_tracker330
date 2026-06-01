@echo off
title Business Tracker — Launcher
color 0A
echo.
echo  ======================================
echo    Business Tracker - One-Click Launch
echo  ======================================
echo.

:: Navigate to project directory
cd /d "%~dp0"

:: Check Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo         Download from https://python.org
    pause
    exit /b 1
)

:: Install dependencies if needed
echo [1/3] Checking Python dependencies...
pip install -r requirements.txt --quiet 2>nul
if %errorlevel% neq 0 (
    echo [WARN] pip install had issues — trying to continue anyway.
)

:: Install Playwright browsers if needed
echo [2/3] Ensuring Playwright browsers are installed...
python -m playwright install chromium --with-deps 2>nul
if %errorlevel% neq 0 (
    echo [WARN] Playwright browser install had issues — trying to continue.
)

:: Start the backend server
echo [3/3] Starting FastAPI server on http://localhost:8000 ...
echo.
echo  -----------------------------------------------
echo   Open your browser to:  http://localhost:8000
echo   Press Ctrl+C in this window to stop the server
echo  -----------------------------------------------
echo.

:: Open browser automatically after a short delay
start "" "http://localhost:8000"

:: Run the server (blocking — keeps the window open)
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload

echo.
echo Server stopped.
pause
