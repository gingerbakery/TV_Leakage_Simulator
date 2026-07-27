@echo off
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%_tools\python313\python.exe"
set "PORT=%~1"

if not exist "%PY%" (
  echo [ERR] Embedded python not found: %PY%
  echo Use: C:\Users\Administrator\Documents\TV leakage simulator\_tools\python313\python.exe
  pause
  exit /b 1
)

if not exist "%ROOT%frontend\dist\index.html" (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [ERR] React production UI is missing and npm was not found.
    echo Install Node.js, run npm install in frontend, then retry.
    pause
    exit /b 1
  )
  if not exist "%ROOT%frontend\node_modules" (
    echo [INFO] Installing React dependencies...
    call npm --prefix "%ROOT%frontend" install
    if errorlevel 1 exit /b 1
  )
  echo [INFO] Building React production UI...
  call npm --prefix "%ROOT%frontend" run build
  if errorlevel 1 exit /b 1
)

set "PATH=%ROOT%_tools\python313;%ROOT%_tools\python313\Scripts;%PATH%"
if "%PORT%"=="" set "PORT=8787"
set "LEAKAGE_WEB_PORT=%PORT%"

where.exe python
echo.
echo [INFO] Starting integrated React + FastAPI UI (keep this window open)
echo [INFO] Open in browser: http://127.0.0.1:%LEAKAGE_WEB_PORT%
echo [INFO] If port is busy, fallback port will be auto-selected by run_web.py and shown in logs.

"%PY%" run_web.py
set "EXITCODE=%ERRORLEVEL%"
if %EXITCODE% neq 0 (
  echo.
  echo [ERR] Failed. Exit code: %EXITCODE%
  pause
  exit /b %EXITCODE%
)

endlocal
