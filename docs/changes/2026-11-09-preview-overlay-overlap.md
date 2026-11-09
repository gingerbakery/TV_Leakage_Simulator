# Reduce overlapping Leak display effects

The user reported a burning-looking TV and clarified that yellow/red points and
light effects overlap and smear the image. This is a display complaint, not
evidence that the simulated optical energy or physical TV has increased.

The previous overlay applied a 26px halo at .72 opacity and an 8px red core,
with additive blending at every retained cell/view. It also displayed all-view
exit gradients concurrently, with a .35 minimum relative-strength factor.
Dense sampling could therefore produce a saturated, fire-like annotation.

Changes:

- Normal alpha blending replaces additive brightening for Leak points/guides.
- Halo: 12px / .18 opacity; core: 4px / .85 opacity, muted amber instead of red.
- Short narrow exit guides appear only for explicitly selected Points/groups.
  Length/width ratios and opacity are reduced. All optical candidates and
  per-view evidence remain in the list/result.
- Picking shares the visible halo size; physical masking remains unchanged.
- (?) help distinguishes location annotations from a photometric image.

No production optical calculation, source specification, 0-nit threshold,
candidate detection or sampling setting is changed. This does not calibrate
the preview to human visual perception or prove physical detection accuracy.
Live visual comparison against the user's TV screen remains outstanding.
