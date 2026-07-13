# Feature module boundaries

## Summary
- Strengthened feature ownership boundaries for multi-developer work.
- Moved ROI backend logic into a dedicated module.

## Code changes
- Added `src/leakage_simulator/roi.py`
  - `build_default_receivers`
  - `resolve_receiver_faces`
  - `build_face_groups`
  - `build_scene_payload`
- Updated `src/leakage_simulator/engine.py`
  - now consumes ROI receiver helpers from `roi.py`
- Updated `run_web.py`
  - now consumes ROI scene payload helper from `roi.py`

## Collaboration impact
- ROI/CAD selection logic is now more isolated from gap and ray trace logic.
- Gap remains centered in `src/leakage_simulator/gap.py`
- Ray trace remains centered in `src/leakage_simulator/raytracer.py`
- Ownership guide added: `docs/developer-ownership.md`
