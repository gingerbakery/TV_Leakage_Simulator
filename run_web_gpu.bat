@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8788"

echo [GPU SOURCE] Preparing a verified NVIDIA CUDA development runtime.
echo [GPU SOURCE] This can take several minutes after the first pull or a requirements change.
echo [GUIDE] Prerequisite setup: docs\WINDOWS_GPU_SETUP.md
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%run_web_gpu.ps1" -Port "%PORT%"
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo [GPU SOURCE FAILED] The GPU server was not started.
  echo Review the first [ACTION] message above and docs\WINDOWS_GPU_SETUP.md.
  echo CPU-only users can run run_web.bat only when CPU fallback is intentional.
  pause
  exit /b %EXITCODE%
)

exit /b 0
