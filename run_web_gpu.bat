@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8788"

echo [GPU SOURCE] Preparing a verified NVIDIA CUDA development runtime.
echo [GPU SOURCE] This can take several minutes after the first pull or a requirements change.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%run_web_gpu.ps1" -Port "%PORT%"
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo [GPU SOURCE FAILED] The GPU server was not started.
  echo Review the first [ACTION] message above. CPU-only users can run run_web.bat.
  pause
  exit /b %EXITCODE%
)

exit /b 0
