# Review: visible TV leakage versus observer path support

## Intended behavior

The target is to inspect a powered TV while rotating an external observer view,
find the leakage that appears bright in that view, and report its observation
angles. The user must not have to know the correct angle before discovering it.
The existing path-support detector is not an implementation of this target.

## Confirmed findings

1. `observer_probe.reflection_supported` reduces BSDF transport to a boolean:
   positive reflectance and positive table support. Small tails and strong
   reflection can both contribute one detected path. Analytic diffuse support
   similarly does not weight the result by its reflected radiance.
2. `_source_geometry` checks source power is positive. The observer result does
   not accumulate source power, emission angular intensity, reflectance products,
   BRDF magnitude or proposal-PDF weights into a radiometric estimate.
3. CPU reproduction using `tests.test_observer_probe.setup`: setting the source
   to 1 lm versus 1000 lm gives direct_count=128, reflected_count=0 in both cases,
   under `observer_path_support_v1`. This demonstrates the count is not brightness.
4. Source-projection proposals and reflection proposals are discovery tools.
   Their raw counts are not an unbiased angular luminance ranking.
5. Observer sampling uses parallel rays on a per-angle model projection plane.
   Preview request options do not include the actual viewer camera pose, lens,
   distance or image dimensions. `ViewerCameraFrame` currently carries target
   and basis directions, not a complete camera model.
6. Fifteen-degree hemisphere steps can miss narrow angular features. Increasing
   forward emitter samples alone does not refine observer angles or image cells.
7. A finite Receiver's irradiance / accepted-cone averaged radiance is not a
   camera image of which TV surface appears luminous. That existing precision
   solve must not silently be relabeled as a camera visibility renderer.

## Required observation pipeline

- Capture the observer camera position, orientation, projection/FOV and image
  dimensions in CAD coordinates; report angles relative to a saved TV datum.
- Render surface/source radiance through complete main CAD + enabled ACC +
  opaque reflective Blocks. Display Hide must not alter transport occlusion.
- Integrate emitted and reflected radiance with forward BSDF directional
  evaluation, correct sampling PDFs and energy factors. Do not assume measured
  BSDF reciprocity or promote nonzero support counts to luminance.
- Produce a linear luminance/radiance image before display tone mapping.
  Compare angles using fixed exposure and explicit calibrated brightness or
  contrast thresholds; no guessed universal human-visibility threshold.
- Sweep/coarsely discover angles and refine suspicious regions with declared
  sample budgets and uncertainty. Zero sampled detections is not proof of zero
  light. Group visible image features while retaining each angle's brightness.
- Keep raw path support as diagnostic evidence alongside this renderer rather
  than treating it as visible-leak detection.

## Acceptance evidence required before claiming visible-leak accuracy

- Fixed geometry/material: scaling source power scales the linear image signal.
- A closed opaque wall blocks direct transmission, including at grazing angles;
  a real gap exposes only the geometrically visible source/reflection paths.
- Specular and narrow measured lobes peak at the correct physical angles;
  diffuse/weak BSDF tails remain weighted by their actual radiance.
- A reflective hole plug suppresses transmission while permitting valid
  reflected paths; rendered and physical solids match, including Reverse.
- Enabled ACC changes image occlusion and reflection as expected.
- A recorded camera pose reproduces the same image and detected angle after
  reload. Rendering convergence and uncertainty are recorded.
- Compare the reported left-edge leakage case against the actual saved TV
  geometry, emitter settings and materials (approximately 60 degrees horizontal,
  within about 20 degrees vertical per user observation). An actual result-
  inclusive BITSAM case and physical brightness/exposure conditions are still
  needed for that comparison.

This review is not a completed camera renderer, a validated customer-case fix,
or a claim that the current detector finds visually observable leakage.
