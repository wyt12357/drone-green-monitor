@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Tello Green Monitor

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\tello-green\Scripts\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\tello-green\Scripts\python.exe"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)

if not defined PY (
  echo [ERROR] Python not found.
  echo         Install Python 3.9+ then run:  pip install -r requirements.txt
  pause
  exit /b 1
)

echo Using Python: %PY%
"%PY%" -c "import flask, numpy, cv2" >nul 2>nul
if errorlevel 1 (
  echo [WARN] Missing packages, installing from requirements.txt ...
  "%PY%" -m pip install -r requirements.txt
  "%PY%" -c "import flask, numpy, cv2" >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Install failed. Please install manually:
    echo         "%PY%" -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

if not defined OPEN_BROWSER set "OPEN_BROWSER=1"
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"
echo.
echo   Starting UAV green-coverage monitor ...
echo.
"%PY%" app.py

echo.
echo   Server stopped. Press any key to exit.
pause >nul
