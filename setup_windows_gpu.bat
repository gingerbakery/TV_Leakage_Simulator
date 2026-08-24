@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "MODE=%~1"
set "DRIVER_INSTALLER=%~2"

if "%MODE%"=="" set "MODE=check"
if /I "%MODE%"=="-Check" set "MODE=check"
if /I "%MODE%"=="-Install" set "MODE=install"
if /I "%MODE%"=="-RuntimeOnly" set "MODE=runtime-check"

if /I "%MODE%"=="check" (
  if not "%~2"=="" goto :usage
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup_windows_gpu.ps1"
  exit /b %ERRORLEVEL%
)

if /I "%MODE%"=="install" (
  if not "%~3"=="" goto :usage
  if "%DRIVER_INSTALLER%"=="" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup_windows_gpu.ps1" -Install
  ) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup_windows_gpu.ps1" -Install -ApprovedDriverInstallerPath "%DRIVER_INSTALLER%"
  )
  exit /b %ERRORLEVEL%
)

if /I "%MODE%"=="runtime-check" (
  if not "%~2"=="" goto :usage
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup_windows_gpu.ps1" -RuntimeOnly
  exit /b %ERRORLEVEL%
)

if /I "%MODE%"=="runtime-install" (
  if not "%~3"=="" goto :usage
  if "%DRIVER_INSTALLER%"=="" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup_windows_gpu.ps1" -RuntimeOnly -Install
  ) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup_windows_gpu.ps1" -RuntimeOnly -Install -ApprovedDriverInstallerPath "%DRIVER_INSTALLER%"
  )
  exit /b %ERRORLEVEL%
)

:usage
echo Usage:
echo   setup_windows_gpu.bat
echo   setup_windows_gpu.bat check
echo   setup_windows_gpu.bat -Install
echo   setup_windows_gpu.bat -Install "C:\IT-approved\NVIDIA\setup.exe"
echo   setup_windows_gpu.bat runtime-check
echo   setup_windows_gpu.bat runtime-install ["C:\IT-approved\NVIDIA\setup.exe"]
echo.
echo The default mode is read-only and auto-detects source versus GPU ZIP.
echo Install mode requires explicit company approval and an elevated terminal.
echo This launcher never downloads a driver or reboots.
echo Guide: docs\WINDOWS_GPU_SETUP.md
exit /b 64
