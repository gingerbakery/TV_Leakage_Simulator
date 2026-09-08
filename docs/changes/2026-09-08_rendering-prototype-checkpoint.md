# Rendering prototype checkpoint — 2026-09-08

Branch: `codex/rendering-studio-prototype-20260908`, based on `main` at `ed27db0d`.

This checkpoint includes the completed-result link and source validation, interactive 3D leakage preview, planar aperture reconstruction, directional relative-brightness display, exterior controls, fixed light-only spread, camera-preserving Case comparison, scene reconnect, and automatic horizontal-seam demo entry. Synthetic enclosed, corner-gap and side-seam STEP/BITSAM references and their generation/validation scripts are included.

The display remains an uncalibrated planar-gap prototype. Absolute nit, eye/camera calibration and general curved/recessed openings remain future work. The next planned work is observation-angle behavior, then comparison of gap widths and refinement of visible line shape. Automatic design assistance remains deferred until after V1.

## Reproducing the horizontal-seam demo

With the repository's normal source dependencies prepared, run `node scripts/stage_horizontal_seam_demo.mjs` from the repository root. It creates `outputs` when missing and stages the synthetic settings and STEP files. Start the local application and open `/?demo=horizontal-seam` on its actual port. The demo performs a new normal CPU analysis and opens its completed result in the 3D view; no stored result is injected.

The large `samples/side_seam/*.result.json` and `samples/side_seam_horizontal/*.result.json` files are reproducible local outputs and are excluded from Git. The sample READMEs describe how to regenerate them before running validators that read raw results. Source STEP/BITSAM files, small verification summaries and generation scripts remain included. Existing unrelated attachments, original BITSAM copies and separate backlog/review notes remain outside this checkpoint.

## Checkpoint validation

- Complete frontend suite: 41 test files, 311 tests passed.
- TypeScript build check passed; scoped frontend lint reported zero warnings/errors.
- Production Vite build passed. Existing chunk-size and plugin-timing advisories remain.
- Demo staging smoke check started without an `outputs` directory and successfully produced the manifest plus eight fixture files, with default `16x` and four source-power cases.
- Staged source/document whitespace checks passed. Synthetic STEP files retain the CAD exporter's original trailing spaces; the full whitespace check reports those generated-file formatting warnings.
- Unit tests use jsdom and do not establish WebGL visual correctness; they emit the expected missing-canvas-context messages. Earlier live rendering checks and real CPU/path validations are recorded in the preceding change notes. No new optical or GPU benchmark was performed for this commit.
