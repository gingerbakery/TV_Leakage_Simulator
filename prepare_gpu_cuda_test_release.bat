@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [GPU RELEASE] Building a commit-identified GPU CUDA tester ZIP.
echo [GPU RELEASE] Git pull and a previously extracted EXE are different delivery paths.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepare_gpu_cuda_test_release.ps1"
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo [GPU RELEASE FAILED] No tester handoff should be sent from this run.
  pause
  exit /b %EXITCODE%
)

echo.
echo [GPU RELEASE READY] Send the ZIP, its .sha256, and its .handoff.json together.
pause
exit /b 0
