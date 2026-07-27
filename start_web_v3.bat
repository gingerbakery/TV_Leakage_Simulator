@echo off
setlocal

set "ROOT=%~dp0"
set "PY=%ROOT%_tools\python313\python.exe"
set "PORT=8788"

if not exist "%PY%" (
  echo [ERR] Python runtime not found:
  echo %PY%
  pause
  exit /b 1
)

if not exist "%ROOT%frontend\dist\index.html" (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [ERR] React production UI is missing and npm was not found.
    exit /b 1
  )
  if not exist "%ROOT%frontend\node_modules" (
    call npm --prefix "%ROOT%frontend" install
    if errorlevel 1 exit /b 1
  )
  call npm --prefix "%ROOT%frontend" run build
  if errorlevel 1 exit /b 1
)

cd /d "%ROOT%"
set "LEAKAGE_WEB_PORT=%PORT%"

echo [INFO] Starting integrated React + FastAPI UI
echo [INFO] Expected URL: http://127.0.0.1:%PORT%
echo [INFO] Health check: http://127.0.0.1:%PORT%/health
echo [INFO] Keep this window open while using the web UI
echo.

"%PY%" run_web.py

endlocal
