# Vent-visible Leak display without external beams

This supersedes the rendered exit-gradient behavior described in the earlier
exit-guide/overlap notes. The user clarified the intended appearance: a soft
internal light visible through vent slots, not light being sprayed into space.

The viewer no longer creates external gradient ribbon meshes, including on
selection. It keeps small audited visible-endpoint annotations with existing
physical CAD/Block masking. The 3px amber core uses .55 opacity; the .08-opacity
halo appears only with explicit selection or angle highlighting. Normal blending
is retained. Selecting an individual Point shows its actual Ray arrows and small
reflection annotations to communicate direction. No duplicate exit glow point or
opaque-wall transparency is introduced.

Optical calculations, candidates, minimum luminance and sampling are unchanged.
These remain diagnostic location/path annotations, not a calibrated photorealistic
render of the internal emitting surface. Existing exit-estimate data/helpers are
retained for analysis but no longer generate visible external beams.

Related panel/picking/physical-occlusion tests and production build passed. A
comparison against the user's actual TV view has not been performed. No commit,
push, GPU run or server restart was performed for this adjustment.
