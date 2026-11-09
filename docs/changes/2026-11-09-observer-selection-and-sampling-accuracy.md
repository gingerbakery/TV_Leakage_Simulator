# Individual observer evidence and denser full-detail radiance sampling

## Individual Point evidence

Viewer picking now carries the exact stored endpoint and observation H/V along
with its grouped candidate ID. A transient selected observation resolves only
against audited cells actually associated with that candidate's retained points.
The selected Ray and generated Receiver use that observation's path, angle,
opening provenance and cell footprint. The candidate list and grouped/raw
photometry remain unchanged. Individual highlighting distinguishes the selected
observation from other points in the same group.

`최고 휘도 표본` selects the highest retained valid cell-mean luminance for the
candidate. It does not invent untested view angles or claim a global maximum.
The cell mean includes multiple optical contributions; its representative path
is not necessarily the sole/dominant brightness contribution. `대표 경로` restores
the candidate's default representative. Missing/invalid observations cannot be
used to manufacture a new path. Unresolved selected exits keep Receiver actions
gated as before. Selection does not rotate the camera.

Observation indexes are built when result/point arrays change and reused across
selection changes. This avoids rebuilding large endpoint maps on every click.
Exact observation selection is current-view state; new results, group/list
selection, deletion, clearing and project restoration reset it. No saved-project
schema or raw result is rewritten.

## Numerical coverage option

Full-detail radiance search now exposes 8 or 16 samples per cell, both supported
by the existing backend sampling contract. Existing default behavior remains 8;
path-support search remains at 2. The frontend planner and actual request both
carry 16 when chosen. Spatial/angular spacing is preserved and over-budget plans
fail instead of silently coarsening. No production backend emission, BRDF/BSDF,
MIS, roulette, radiance averaging or minimum-luminance formula is modified.

All cells, including zero-contribution cells, receive the requested sample
budget. Mean luminance still includes zero samples. More strata change the
sample positions and are not a superset of the old 8 positions: finding every
previously found rare path or every narrow gap is not guaranteed.

## Evidence and limits

CPU tests verify 16 repeats double attempted/planned samples, preserve uniform
source analytic mean and scalar/batch determinism. A fixed adversarial 0.02mm
offset-slit fixture at identical geometry/seed/spacing is missed at 8 samples and
found at 16. It is a coverage regression fixture chosen against finite sample
locations, not a statistical detection-rate or physical-TV accuracy claim.

Frontend tests cover exact picked evidence within a shared candidate, immutable
group photometry, angle/path transfer into Receiver, rejection of invalid or
unassociated observations, highest-retained-cell choice, state resets, repeat
request/planner agreement, budget rejection and the UI repeat control. Related
project round-trip, grouping and viewer tests pass; production build passes
with the existing large-bundle warning. CPU radiance/full-detail/path-audit/
direct-priority/coplanar suites pass (41 tests).

No actual TV/CAD reproduction, hardware CUDA verification, physical calibration,
browser visual QA, server restart, commit or push was performed. The new repeat
option has not been tested on physical GPU hardware. Independent confirmation
runs/confidence classification, adaptive image refinement, BTDF/recirculation,
true soft internal-surface rendering and orthographic camera switching are not
implemented by this change.
