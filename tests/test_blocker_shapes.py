from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from leakage_simulator.raytrace_bridge import build_direct_trace_input
from leakage_simulator.raytracer import run_direct_ray_trace

class BlockerShapeTests(unittest.TestCase):
    def trace(self, shape, source_x, source_y, reverse=False):
        scene = {"vertices": [[100,100,100], [101,100,100], [100,101,100]], "faces": [[0,1,2]], "face_component_ids": [1]}
        payload = {"emitters": [{"emitter_id": "source", "emitter_type": "datum_plane", "center": [source_x,source_y,0],
            "u_axis": [1,0,0], "v_axis": [0,1,0], "width_mm": .01, "height_mm": .01, "ray_count": 32, "seed": 17,
            "aim": {"enabled": True, "mode": "sphere", "distribution": "uniform_solid_angle", "sphere_upper_deg": 0, "sphere_lower_deg": 0}}],
            "receivers": [{"receiver_id": "r", "center": [0,0,10], "normal": [0,0,-1], "width_mm": 20, "height_mm": 20}],
            "config": {"compute_backend": "cpu", "max_depth": 2, "ray_count": 32},
            "preview_blockers": [{"blocker_id": "plug", "shape": shape, "center": [0,0,5], "u_axis": [1,0,0], "v_axis": [0,1,0],
                "normal": [0,0,-1 if reverse else 1], "width_mm": 2, "height_mm": 2, "depth_mm": .1, "enabled": True}]}
        return run_direct_ray_trace(build_direct_trace_input(scene,payload))

    def test_hole_plug_physically_absorbs_both_sides_but_keeps_rectangle_corners_open(self):
        for reverse in (False, True):
            self.assertEqual(self.trace("ellipse",0,0,reverse).receiver_hit_count, 0)
        self.assertGreater(self.trace("ellipse",.8,.8).receiver_hit_count, 25)
        self.assertEqual(self.trace("box",.8,.8).receiver_hit_count, 0)

if __name__ == "__main__":
    unittest.main()
