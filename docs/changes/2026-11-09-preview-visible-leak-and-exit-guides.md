# Preview visible Leak points and exit guides

## Display contract

Audited observer paths now mark their visible endpoint (the vertex immediately
before the observer), including valid virtual emitters. Intermediate reflections
are not luminous Leak markers. CAD/Block physical depth masking and picking remain
in effect. Opening attribution is no longer required to retain these points.
Invalid and unverified paths remain excluded. Optical energies, raw paths,
sampling and sensor-comparison gates are unchanged.

Existing stored opening markers are resolved to their audited visible endpoints
for display without rewriting raw results. Groups preserve distinct observed
patches behind the same opening. Old results that never stored points for
unresolved gaps need Preview rerun to populate those points.

An attributed opening may have a short, soft orange exit guide along the audited
last segment toward the observer. Guides require on-segment provenance and stop
no later than the stored observer endpoint. There is no invented exit for an
unresolved gap, no duplicate glowing exit marker, and no claim of volumetric
scattering or calibrated brightness. The guide is purely directional display.
Intermediate reflection annotations use small orange points matching the Ray.

## List

The heading is `Leak point List`. Each row is a single truncated line:
`Point1 · 45°/0° · 광량 2.30e-3 nit` or a path count. The requested short
`광량` label retains the actual nit unit for observer luminance; it is not a lumen
conversion. Position, exit estimates, grouping and component statistics stay in
closed details. Full row text is also available as the button title.

## Validation scope

Related frontend regression tests and production build were run. New checks cover
unresolved gap endpoint retention, rejection of intermediate/invalid points,
legacy marker migration, off-segment exit rejection, gradient bounds/fade and
single-line list details. No physical TV or GPU tracing was performed.

Live visual QA was attempted using an isolated synthetic page. Computer Use
refused browser access because the current Windows browser URL could not be
verified; no screenshot/visual pass is claimed. Temporary QA source and its
isolated server are removed/stopped after the attempt. User servers and original
CAD files were not changed. Existing large production-bundle warning remains.
