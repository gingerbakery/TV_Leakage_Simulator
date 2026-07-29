@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0"
set "PORT=%~1"
set "PY="
set "BASE_PY="
set "NPM="

if "%PORT%"=="" set "PORT=8788"
cd /d "%ROOT%"

if exist "%ROOT%.venv\Scripts\python.exe" (
  set "PY=%ROOT%.venv\Scripts\python.exe"
)
if not defined PY if exist "%ROOT%_tools\python313\python.exe" (
  set "PY=%ROOT%_tools\python313\python.exe"
)

if not defined PY (
  echo [SETUP] Python virtual environment was not found.
  echo [SETUP] Creating .venv with Python 3.13...

  where.exe py.exe >nul 2>nul
  if not errorlevel 1 (
    py -3.13 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>nul
    if not errorlevel 1 set "BASE_PY=py -3.13"
  )

  if not defined BASE_PY (
    where.exe python.exe >nul 2>nul
    if not errorlevel 1 (
      python.exe -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>nul
      if not errorlevel 1 set "BASE_PY=python.exe"
    )
  )

  if not defined BASE_PY (
    echo.
    echo [ERROR] Python 3.13 was not found.
    echo Install Python 3.13 once, then double-click run_web.bat again.
    pause
    exit /b 1
  )

  call !BASE_PY! -m venv "%ROOT%.venv"
  if errorlevel 1 goto :setup_failed

  set "PY=%ROOT%.venv\Scripts\python.exe"
  "!PY!" -m pip install --upgrade pip
  if errorlevel 1 goto :setup_failed
  "!PY!" -m pip install -r "%ROOT%requirements-dev.txt"
  if errorlevel 1 goto :setup_failed
)

for /f "delims=" %%I in ('where.exe npm.cmd 2^>nul') do (
  if not defined NPM set "NPM=%%I"
)
if not defined NPM (
  echo.
  echo [ERROR] Node.js npm.cmd was not found.
  echo Install Node.js once, then double-click run_web.bat again.
  pause
  exit /b 1
)

if not exist "%ROOT%frontend\node_modules" (
  echo [SETUP] Installing frontend packages for the first run...
  call "%NPM%" --prefix "%ROOT%frontend" ci --no-audit --no-fund
  if errorlevel 1 goto :setup_failed
)

echo [BUILD] Preparing the latest web UI...
call "%NPM%" --prefix "%ROOT%frontend" run build
if errorlevel 1 goto :setup_failed

set "LEAKAGE_WEB_PORT=%PORT%"
set "PATH=%ROOT%.venv\Scripts;%ROOT%_tools\python313;%PATH%"

echo.
echo [READY] TV Leakage Simulator
echo [READY] Browser: http://127.0.0.1:%PORT%/
echo [READY] Keep this window open while using the simulator.
echo.

start "" /b "%PY%" "%ROOT%scripts\open_web_when_ready.py" "http://127.0.0.1:%PORT%/"
"%PY%" "%ROOT%run_web.py" --port "%PORT%" --strict-port
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo [ERROR] Server stopped with exit code %EXITCODE%.
  pause
  exit /b %EXITCODE%
)

exit /b 0

:setup_failed
echo.
echo [ERROR] Automatic setup or web build failed.
echo Review the message above, then run run_web.bat again.
pause
exit /b 1
