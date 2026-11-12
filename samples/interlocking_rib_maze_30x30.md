# Interlocking rib maze (30 × 30 mm)

`interlocking_rib_maze_30x30.stp` is a compact, three-component STEP sample
for visual and multiple-reflection tests. The outer envelope is 30 × 30 ×
12 mm (X × Y × Z). Its geometry is a diagnostic fixture, not a model of a
production material or a calibrated brightness standard.

| Component | Geometry |
| --- | --- |
| `Rib_Tray_A` | Floor, perimeter walls, inlet/outlet holes and low support ribs |
| `Fork_Insert_B` | One connected U-shaped insert with two tall parallel ribs |
| `Tongue_Cover_C` | Roof and a tall tongue inserted between the fork ribs, plus two return ribs |

The inlet is on the -X end (Y 12–18, Z 4–8 mm); the outlet is on the +X end
with the same aperture. The fork back wall at X 6.5–7.7 mm prevents a
straight line of sight. The cover tongue and fork do not have overlapping
solid volume. With the roof assembled, the light route can pass around the
fork and through the narrow interlocking rib region only after reflection.

Suggested initial simulator setup:

1. Import the STEP and confirm **three** separate Components. Keep all
   visible and TRACE ON. Start with ROI off.
2. Place a virtual Datum Plane Emitter near `(0.5, 15, 6)` mm, 4 × 4 mm,
   facing +X into the inlet. Use one-sided Lambertian emission.
3. Give all three Components the same diagnostic optical properties;
   `ABS · White / Normal` is a simple existing preset. Keep properties
   identical when comparing structures. For a pure diffuse high-reflectance
   test use custom Reflectance 0.85, Loss 0.15, Specular 0, Diffuse 1; this
   *does not* test Gaussian width.
4. Place a virtual Datum Plane Receiver at `(31, 15, 6)` mm, 4 × 4 mm,
   facing -X toward the outlet. Set Acceptance Angle 90°.
5. Start with 100,000 rays for geometry checks and increase the count for
   rare-path analysis. Keep the seed and all optical settings unchanged in
   case comparisons. This geometry can have low hit rates.

The top cover is an actual opaque solid; the CAD viewer's transparency or
Section mode can be used to inspect the inserted tongue and the air gaps.
The assembly colors are for distinguishing Components in the viewer, not
the ray-tracing optical properties.
