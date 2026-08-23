@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "%~dp0_tools\python313\python.exe" (
  echo [GPU FAILED] The bundled GPU Python runtime was not found.
  echo [ACTION] Source checkout: run run_web_gpu.bat instead.
  echo [ACTION] Packaged app: extract the complete GPU CUDA ZIP to a new folder.
  pause
  exit /b 1
)
if not exist "%~dp0scripts\verify_gpu_cuda_runtime.py" (
  echo [GPU FAILED] The GPU verification script was not found.
  echo [ACTION] Extract the complete GPU CUDA ZIP to a new folder. Do not copy only the EXE.
  pause
  exit /b 1
)

"%~dp0_tools\python313\python.exe" "%~dp0scripts\verify_gpu_cuda_runtime.py" --mode device --human
if errorlevel 1 (
  echo.
  echo [FAIL] NVIDIA CUDA runtime check failed. GPU use is NOT verified.
  echo [ACTION] Follow the reason above or docs\gpu-cuda-user-guide.md.
  pause
  exit /b 1
)
echo.
echo [OK] NVIDIA CUDA runtime and the production Ray/BVH kernel are working on THIS PC.
pause
endlocal
