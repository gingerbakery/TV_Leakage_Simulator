# Aperture-only Leak marker presentation

## Implemented scope

The independent observer geometry audit now attaches bounded planar-opening provenance to valid paths. The mesh is grouped into checked coplanar triangle patches; manifold boundary loops distinguish filled sheets from bounded empty interiors. A path must cross a bounded interior strictly between its last visible surface/source and observer, away from the rim. Among recognized crossings on that segment the one nearest the observer is selected. Arbitrary plane orientations and non-round holes are supported; an AABB projection is never a marker. Active Blocks never define holes, and existing path occlusion audit must pass before attribution. All raw paths, radiance, sampling budgets and material settings are preserved.

A CAD opening identity partitions observation clusters before grouping. Paths with different internal reflections but the same identity group together regardless of their spatial bins; neighboring distinct openings do not merge. Each group displays one actual crossing from its representative path, not a centroid. Internal surface markers and missing-provenance legacy markers are suppressed. A new result starts without selection; internal arrows appear after explicit selection. Removing the selected group clears selection. Saved explicit selection still restores normally. Confirmed opening coordinates are used as the Receiver lateral target, while the existing outside-CAD Receiver placement/acceptance model remains unchanged.

The UI shows 개구부 경유 versus 개구부 미확정 and a visible 최외곽 개구부 여부 미검증 status whenever recognized openings are present. Missing historical aperture data requires a new Preview run. Unresolved candidates retain path inspection and sensor diagnostics but cannot create a Leak Receiver or precision ROI from an internal point. Per-result metadata and opening positions survive project save/load and are structurally validated. Explanations remain in (?) help.

## Limits and unresolved work

This is **not** a general exterior-shell or gap topology certification. The recorded contract is bounded_planar_opening_v1 with exterior_shell_certified=false. A last recognized opening can still be an interior opening if a later exterior gap has no recognizable bounded planar outline. Curved openings, open part-to-part gaps, non-manifold/overlapping coplanar meshes and incomplete outlines can stay unresolved. Neither point suppression nor unresolved attribution means no physical leak. Full exterior-shell attribution needs additional local shell semantics/geometry evidence and has not been claimed complete.

Extraction and attribution have a five-second postprocessing budget; exceeding it leaves unresolved evidence and a visible diagnostic, without inventing markers or changing optical energy. Marker masks continue to use physical CAD/Block depth independent of CAD Hide, transparency and section display (see hidden-CAD occlusion change).

## Validation

Synthetic geometry tests exercise nested openings, arbitrary orientation, points outside the bounded outline, rim ambiguity, solid sheets, missing/invalid audits, budget failure and an actual independent path audit that rejects a covering plate. Frontend checks cover distinct opening identities, one representative marker for multiple internal routes, unknown-marker suppression, explicit selection, Receiver gating and saved metadata corruption. Existing regression expectations that treated internal/legacy coordinates as marker provenance were updated to the new display contract, without changing optical equivalence gates. Actual user CAD, browser pixels and NVIDIA execution remain unverified; no actual GPU trace was performed. Commit/push and user-server restart were not performed.

Final checks: related frontend suite 115 tests passed; related CPU backend suite 76 tests passed; production frontend build passed. The final additional visible shell-certification warning is copy-only.
