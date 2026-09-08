# Horizontal seam demo link

- Explicit entry: http://127.0.0.1:8788/?demo=horizontal-seam . Ordinary URLs perform no demo fetch, upload or trace.
- The fixed local /outputs/horizontal-seam-demo.json manifest selects the 0.3 mm horizontal slit / 16 lm fixture. Files remain synthetic development references.
- Startup reads the STEP and BITSAM settings, uses the normal upload and Scene query, requires the stored CAD fingerprint to match, then restores optical/source/receiver settings and submits a normal CPU job with buildRayTraceRequest.
- No archived result is attached. Source context is recorded from the actual mesh, Case and exact submitted request; the standard completed-result path stores the result. The 3D view opens only when the source-bound latest Case/run is ready.
- Initial view uses right +X (YZ), fits the leakage geometry and backs out by a fixed 3.5 distance multiplier so the exterior provides context. This initial framing applies once; subsequent user rotation is retained.
- Startup/loading, actual job progress and errors are visible. StrictMode replay does not duplicate the upload or job. A real unmount cancels pending work and stops a known started job; switching away prevents late writes to another Case.
- A fixed-manifest/path check prevents this convenience link from fetching arbitrary manifest URLs. The fixture must use CPU with automatic convergence disabled; no GPU setup or execution was performed.

## Validation

- 20 targeted tests passed across the demo bootstrap, ViewerWorkspace and SimulatorShell. New coverage checks ordinary-URL inactivity, StrictMode single execution, exact source request, settings restoration without archived result injection, preservation of other Cases, incompatible CAD rejection, cancellation during start, and one-time right-side 3D entry with later user direction retained.
- TypeScript, scoped lint and git diff --check passed. Production build succeeded; only the existing large-chunk advisory remains.
- Independent real API validation used the served fixture, normal upload/Scene/settings/request/start path and a 500,000-ray CPU job (104 receiver hits, full receiver capture). See outputs/horizontal-seam-demo-validation/validation.json.
- The binary Scene Float32 grid-origin representation is now recognized in the aperture grid anchor without widening the metal-intersection epsilon. The actual binary Scene reconstructs one continuous field, all 104 paths, zero fallback paths and 360 open 0.1 mm cells (3.6 mm²). The aperture-mask suite passed 29 tests, including the new binary-origin/off-grid checks.
- Automated component tests do not prove browser WebGL rendering. This change relies on the earlier live PSF rendering verification; the new automatic entry was checked through tests and actual API/asset responses. Do not describe a fresh browser visual inspection unless separately performed.
- Post-build HTTP verification confirmed the demo URL, new index-B-NVZ0jJ.js and viewer-BvsEOgRj.js assets, related assets and default16x manifest return 200. See outputs/local-demo-server/demo-url-ready.json.

## Scope

The display remains a relative-brightness planar-gap prototype with a fixed normalized light-only PSF. It is not a calibrated nit prediction. A new demo tab calculates a new real result; repeated page reloads intentionally create new demo sessions rather than preserving previous in-memory Cases.
