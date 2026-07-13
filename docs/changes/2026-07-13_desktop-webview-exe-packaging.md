# 2026-07-13 desktop webview exe packaging

## Summary
- Added a desktop EXE launcher so the simulator can be opened by double-click instead of typing batch commands.
- Kept the existing Python-based CAD import path so `STEP/STP/X_T` support remains available.

## Added
- `desktop_launcher/LeakageSimulatorDesktop.cs`
  - WinForms desktop shell
  - embedded `WebView2` viewer
  - local server startup/health wait
  - in-window error reporting
- `build_desktop_webview_exe.bat`
  - compiles the launcher with system `csc.exe`
  - assembles a folder-based desktop package
- `docs/desktop-exe-packaging.md`
  - explains architecture, package contents, and limits

## Runtime adjustment
- `run_web.py` now accepts `--port <number>` in addition to `LEAKAGE_WEB_PORT`.
- The desktop launcher uses the command-line port path to avoid Windows `Path/PATH` environment collisions.

## Packaging direction
- Output target: `release/leakage_simulator_desktop_v0.1`
- Intended usage: user double-clicks `LeakageSimulator.exe`

## Notes
- This is a launcher package, not a rewrite of the simulator core.
- `run_web.py` remains the source of truth for import, ROI, transform, and rendering behavior.
