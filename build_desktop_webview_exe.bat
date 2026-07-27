@echo off
setlocal

set "ROOT=%~dp0"
set "OUTDIR=%ROOT%release\leakage_simulator_desktop_v0.10.0"
set "CS=%ROOT%desktop_launcher\LeakageSimulatorDesktop.cs"
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
set "WV2_DIR=C:\Program Files\Microsoft Office\root\Office16\ADDINS\Microsoft Power Query for Excel Integrated\bin"
set "WV2_CORE=%WV2_DIR%\Microsoft.Web.WebView2.Core.dll"
set "WV2_WINFORMS=%WV2_DIR%\Microsoft.Web.WebView2.WinForms.dll"
set "WV2_LOADER=%WV2_DIR%\WebView2Loader.dll"

if not exist "%CSC%" (
  echo [ERR] csc.exe not found: %CSC%
  exit /b 1
)

if not exist "%WV2_CORE%" (
  echo [ERR] WebView2 Core dll not found: %WV2_CORE%
  exit /b 1
)

if not exist "%WV2_WINFORMS%" (
  echo [ERR] WebView2 WinForms dll not found: %WV2_WINFORMS%
  exit /b 1
)

if not exist "%WV2_LOADER%" (
  echo [ERR] WebView2 Loader dll not found: %WV2_LOADER%
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERR] npm was not found. Install Node.js before packaging.
  exit /b 1
)

echo [INFO] Building React production UI...
call npm --prefix "%ROOT%frontend" run build
if errorlevel 1 exit /b 1
if not exist "%ROOT%frontend\dist\index.html" (
  echo [ERR] React production index was not generated.
  exit /b 1
)

if exist "%OUTDIR%" rmdir /s /q "%OUTDIR%"
mkdir "%OUTDIR%"
mkdir "%OUTDIR%\desktop_runtime"

echo [INFO] Building desktop launcher exe...
"%CSC%" /nologo /target:winexe /platform:x64 /optimize+ /out:"%OUTDIR%\LeakageSimulator.exe" ^
  /r:System.dll ^
  /r:System.Core.dll ^
  /r:System.Drawing.dll ^
  /r:System.Windows.Forms.dll ^
  /r:"%WV2_CORE%" ^
  /r:"%WV2_WINFORMS%" ^
  "%CS%"
if errorlevel 1 exit /b 1

echo [INFO] Copying runtime files...
copy "%WV2_CORE%" "%OUTDIR%\" >nul
copy "%WV2_WINFORMS%" "%OUTDIR%\" >nul
copy "%WV2_LOADER%" "%OUTDIR%\" >nul
copy "%ROOT%run_web.py" "%OUTDIR%\" >nul
copy "%ROOT%run_api.py" "%OUTDIR%\" >nul
copy "%ROOT%README.md" "%OUTDIR%\" >nul
copy "%ROOT%COMPANY_PC_QUICK_START.md" "%OUTDIR%\" >nul
if exist "%ROOT%check_cad_import.py" copy "%ROOT%check_cad_import.py" "%OUTDIR%\" >nul

xcopy "%ROOT%src" "%OUTDIR%\src\" /E /I /Y >nul
xcopy "%ROOT%frontend\dist" "%OUTDIR%\frontend\dist\" /E /I /Y >nul
xcopy "%ROOT%_tools\python313" "%OUTDIR%\_tools\python313\" /E /I /Y >nul

if exist "%ROOT%samples" xcopy "%ROOT%samples" "%OUTDIR%\samples\" /E /I /Y >nul
if not exist "%OUTDIR%\_uploads" mkdir "%OUTDIR%\_uploads"
if not exist "%OUTDIR%\outputs" mkdir "%OUTDIR%\outputs"
if not exist "%OUTDIR%\docs" mkdir "%OUTDIR%\docs"
copy "%ROOT%docs\desktop-exe-packaging.md" "%OUTDIR%\docs\" >nul

echo [INFO] Writing launch note...
(
  echo Leakage Simulator Desktop Package
  echo.
  echo 1. Double-click LeakageSimulator.exe
  echo 2. Wait until the window says "Leakage simulator ready"
  echo 3. Import STEP/STP/X_T CAD directly inside the app
  echo.
  echo If startup fails, keep the window open and review the error message.
) > "%OUTDIR%\START_HERE.txt"

echo [OK] Desktop package created:
echo %OUTDIR%
endlocal
