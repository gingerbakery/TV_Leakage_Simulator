# 2026-07-13 git collaboration setup

## Summary
- Added a project-level `.gitignore` for collaboration-friendly source control.
- Documented how to split the first commits and how to share the project with other developers.

## Added
- `.gitignore`
  - excludes local runtime, cache, generated outputs, and release packages
- `docs/git-collaboration-guide.md`
  - explains first upload order
  - explains why commit splitting is useful
  - explains how to hand off the project for development vs testing

## Intent
- Keep the repository lightweight and reviewable.
- Prevent large local runtime folders from entering history by mistake.
