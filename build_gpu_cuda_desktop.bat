@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_gpu_cuda_desktop.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] GPU CUDA package build failed.
  exit /b 1
)
echo.
echo [OK] GPU CUDA desktop package is ready under the release folder.
endlocal
