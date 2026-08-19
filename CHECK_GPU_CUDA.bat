@echo off
setlocal
cd /d "%~dp0"
"%~dp0_tools\python313\python.exe" "%~dp0scripts\verify_gpu_cuda_runtime.py" --mode device
if errorlevel 1 (
  echo.
  echo [FAIL] NVIDIA CUDA runtime check failed. The app will use CPU fallback.
  echo See docs\desktop-exe-packaging.md for driver and CUDA Toolkit requirements.
  pause
  exit /b 1
)
echo.
echo [OK] NVIDIA CUDA runtime and a real GPU kernel are working.
pause
endlocal
