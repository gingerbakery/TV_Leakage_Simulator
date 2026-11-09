# Preview picking and grouped direct-path regression audit

## Scope and evidence

Reported symptoms: an earlier oblique direct leak is absent in radiance mode at
0 nit, and clicking yellow Leak display does not highlight it. The actual remote
TV result is not present in this checkout. The user has not yet established
whether the direct count/list or only the 3D display is missing. Synthetic
reproductions below must not be treated as confirmation of that TV's root cause.

## Fixes

- Picking previously rejected the whole fixed-size halo when its centre was
  covered, although pixels beside the centre can remain visible through a real
  slot. Pick occlusion now tests the clicked pixel at the sprite's camera depth.
  Fully covered pixels remain rejected; physical CAD/Block masking is retained.
- Exit gradient ribbons now select their owning Point. Transparent tails/edges
  and physically covered intersections are rejected. They do not create new
  optical evidence or independent Leak candidates.
- Grouping by opening identity could retain a brighter reflected representative
  even when a faint direct path was detected at another view. Grouping now
  preserves a direct representative and its view/Receiver frame, while retaining
  aggregate counts, peak luminance and both views' point evidence.

## Calculation audit

The deployed ef9a588 change contains no backend transport changes relative to
2cb395c. It changes display endpoints, guides, grouping and list presentation.
The current fix likewise does not change production source/flux conversion,
BRDF/BSDF, MIS, sampling, radiance accumulation or threshold equations.

Older changes from 71f197d to the deployed version did include visibility-predicate
corrections for Datum/CAD coincidence and source-connection endpoint occlusion,
plus pre-threshold diagnostics. Those are detection-affecting changes, distinct
from changing the radiance integration formula; an unspecified older build
cannot be assumed equivalent. The current threshold rejects only cell mean
luminance below the setting. At 0 nit it does not reject a sampled positive
contribution, but does not recover unsampled paths or override path/ROI/occlusion
validation. Radiance and path-support modes still use different sample proposals.

## Verification

- Frontend: 8 related files / 121 tests passed, including picking, point grouping,
  store, panel, saved-project round trip, exit guides and representative paths.
- CPU photometric tests: 18 passed, including a new -45/0/+45-degree direct-source
  case at 1e-12 nit with threshold 0, analytic direct/specular scaling, MIS,
  BSDF, blocking, seed/dispatch and unchanged forward-flux checks.
- CPU path audit/direct-priority/coplanar-source tests: 13 passed. Some exercise
  resident equations on CPU; these are not hardware execution evidence.
- Production build passed with the existing large-bundle warning.
- An initial audit command used a nonexistent test module name; it was corrected
  to test_observer_path_validation and the relevant suite passed.

No GPU hardware run, real TV calculation, actual browser visual confirmation,
server restart or user-file mutation was performed. Confirming the original
missing direct leak requires the remote result/project and matching settings.
