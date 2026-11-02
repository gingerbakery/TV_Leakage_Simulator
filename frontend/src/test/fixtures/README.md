# CAD hole recognition fixture

`hole-plate.scene.json` contains sanitized display mesh and B-rep edge data from
a generated STEP plate imported through `build_scene_payload`. It contains no
user CAD, project paths, or analysis results.

The plate is 40 × 25 × 2 mm, centered at the origin. Through holes are Ø2 mm at
(-10, 0), Ø6 mm at (0, 0), and an ellipse with radii 3 and 1.5 mm at (10, 0).
Its entrance planes are Z = -1 and +1 mm. The small circular edge has only five
sample points, covering the sparse B-rep sampling case. The ellipse is recognized
geometrically but deliberately rejected by the circular point-placement tool.
