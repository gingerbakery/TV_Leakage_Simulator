# Receiver internal cell-boundary contract

`receiver_internal_boundary_fp64_v1` changes only the cell assigned to an
already accepted Receiver hit. It does not change a ray's origin, direction,
power, intersection distance, acceptance angle, reflection or termination.
The outer finite-plane bounds remain inclusive and unchanged.

Cells are half-open towards increasing local coordinates. An internal grid
line belongs to the cell on its positive side. Tiny CPU/CUDA arithmetic
differences around that line previously split an exactly targeted spot
between adjacent cells, changing Peak without meaningfully changing flux.

For each axis, let `s = (local + half_extent) / extent * cell_count`, using
the existing reciprocal-extent arithmetic. A value sufficiently close to an
internal integer is assigned to that integer. The band, in cell units, is:

`min(1e-7, 32 * FP64_epsilon * cell_count * max(1, coordinate_scale / extent))`.

`coordinate_scale` is the sum of absolute ray-origin coordinates, Receiver
center coordinates and intersection distance, in mm. This accounts for
coordinate cancellation without using the mesh intersection epsilon. The
hard cap is one ten-millionth of a cell; for a 0.2 mm cell it is 0.00000002 mm.
Typical local scenes have much smaller bands. Outside the band the existing
integer bin calculation is unchanged. No light is redistributed, discarded,
smoothed, renormalized or additionally admitted to the Receiver.

This is an explicit numerical classification convention, not a proof of
identical CPU/GPU arithmetic or a rigorous bound on arbitrary accumulated
transport error. Extremely ill-conditioned geometry can exceed the cap.
Truly distinct hits inside the band intentionally share its grid-line rule.
Moving a Receiver grid can still change the maximum cell of a sharply
focused spot; that is distinct from this roundoff issue.

The same scalar function is compiled for Numba CPU and CUDA resident
transport. Python scalar and NumPy batch use the same rule. New results
record the contract in `metrics._performance_summary`. Existing saved
results are not rewritten; rerunning a boundary-focused case can intentionally
change its Peak and per-cell moments while preserving total light.

Regression gates cover internal lines, adjacent representable values,
independent high-precision classification outside the band, no expansion of
outer acceptance, multiple Receivers, rotation/translation/grazing, and the
70-degree reflected-center reproducer. The latter injects only RNG values
to stress a boundary and is not a production speed benchmark. Its known
total flux is 0.0017502570909109678 lm, from the prior unchanged transport;
the geometric reference for its cell is the positive-side center cell.
