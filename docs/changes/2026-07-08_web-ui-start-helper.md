# Web UI start helper

## Summary
- Added `start_web_v3.bat` to launch the embedded Python web UI on port `8788`.
- Purpose is to reduce confusion around PowerShell syntax and environment variable setup.

## Intended usage
- Double-click `start_web_v3.bat`, or run it from the project folder terminal.
- While the window stays open, browse to `http://127.0.0.1:8788/`.
- Version check endpoint: `http://127.0.0.1:8788/health`

## Notes
- If the terminal shows `v0.2.0`, that is still the old server and not the new web UI.
- The correct new script should report `v0.3.1`.
