# Web UI dev auto reload

## Summary
- Added a development watch runner for the web UI.
- During development, the user no longer needs to re-run the start command after every code change.
- Browser reload is also automated after server restart.

## Files
- `run_web_dev.py`
- `start_web_dev.bat`

## Behavior
- Watches:
  - `run_web.py`
  - `run_web_dev.py`
  - `src/**/*.py`
  - `docs/**/*.md`
- On change:
  - restarts the local web server
  - browser page reloads automatically via `/dev-status`

## Usage
- Development mode:
  - `.\start_web_dev.bat`
- Normal mode:
  - `.\start_web_v3.bat`
