# Point selection tolerance and redundant work

The user reports small Point targets and a loading delay when selecting them.
The implementation scanned nearby points and performed a full physical CAD
intersection for every candidate before choosing the best. Selection also rebuilt
the point positions, geometry, materials and textures for unchanged results.

Changes:

- Use a 12px screen-space click radius independently of the small visual dot.
  Outside the rendered halo, the centre must actually be visible. Inside the
  halo, use the clicked pixel to match visible edge fragments. No wall masking
  is disabled to make a hidden Point selectable.
- Order nearby candidates by screen distance/depth and stop after the first
  visible candidate. The click pixel's nearest CAD hit is computed lazily once
  and reused for overlapping points. Geometry/camera state is not cached across
  clicks, so changes cannot leave stale occlusion evidence.
- Preserve marker geometry/materials/textures for an unchanged visible-point
  array; selection updates colour buffers and halo visibility. Positions and
  detection data are unchanged. Reuse temporary colour values during updates.

Tests cover one visibility callback for 1,000 overlapping visible points, one
CAD mesh raycast for 100 same-click occlusion tests, blocked pixels and existing
projection/layer/selection cases. These are operation-count tests, not measured
remote-PC latency or hardware performance claims. Optical calculations remain
unchanged. Soft internal-surface rendering is not implemented by this change;
it requires a bounded surface/image representation rather than external beams.
