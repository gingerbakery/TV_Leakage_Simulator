@echo off
setlocal

set "TARGET_PORT=%~1"
if "%TARGET_PORT%"=="" set "TARGET_PORT=8788"

echo [INFO] scanning LISTENING sockets on 127.0.0.1:%TARGET_PORT%
set "KILLED=0"

for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":%TARGET_PORT% .*LISTENING"') do (
  call :kill_pid %%p
)

if "%KILLED%"=="0" (
  echo [INFO] no listener found on port %TARGET_PORT%.
)

endlocal
goto :EOF

:kill_pid
  echo [INFO] stopping PID %1
  taskkill /F /PID %1 >nul 2>&1
  if %errorlevel% equ 0 (
    echo [OK] stopped pid %1
    set "KILLED=1"
  ) else (
    echo [WARN] failed to kill %1 - permission or ownership issue
  )
  goto :EOF
