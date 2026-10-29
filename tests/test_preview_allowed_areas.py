from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from leakage_simulator.raytrace_bridge import build_direct_trace_input
from leakage_simulator.raytracer import run_direct_ray_trace


class PreviewAllowedAreaTests(unittest.TestCase):
    def test_allowed_boundary_excludes_normal_and_45_degree_flux_before_receiver_binning(self):
        scene = {"vertices": [[1000, 1000, 1000], [1001, 1000, 1000], [1000, 1001, 1000]],
                 "faces": [[0, 1, 2]], "face_component_ids": [1], "face_material_ids": ["default"]}
        for sign in (-1, 1):
            for angle in (0, 45):
                with self.subTest(direction=sign, angle=angle):
                    a = math.radians(angle)
                    payload = {"emitters": [{"emitter_id": "source", "emitter_type": "datum_plane",
                        "center": [0, 0, 0], "u_axis": [math.cos(a), 0, -sign * math.sin(a)],
                        "v_axis": [0, sign, 0], "width_mm": 0.1, "height_mm": 0.1,
                        "direction_distribution": "gaussian", "gaussian_sigma_deg": 0.1,
                        "power_lumen": 1, "ray_count": 128, "seed": 7, "enabled": True}],
                        "receivers": [{"receiver_id": "detector", "center": [0, 0, sign * 20],
                            "normal": [0, 0, -sign], "u_axis": [1, 0, 0], "v_axis": [0, sign, 0],
                            "width_mm": 80, "height_mm": 80, "resolution": [8, 8],
                            "acceptance_angle_deg": 90, "enabled": True}],
                        "config": {"compute_backend": "cpu", "ray_count": 128, "max_depth": 3,
                            "primary_sampling_strategy": "source", "bounce_sampling_strategy": "source", "seed": 11}}
                    baseline = run_direct_ray_trace(build_direct_trace_input(scene, payload))
                    self.assertGreater(baseline.receiver_hit_count, 100)
                    blocker = {"blocker_id": "precision-allowed-area:hole:0:max", "center": [5 * math.tan(a), 0, sign * 5],
                        "u_axis": [1, 0, 0], "v_axis": [0, 1, 0], "normal": [0, 0, 1],
                        "width_mm": 2, "height_mm": 2, "depth_mm": 0.1, "enabled": True}
                    blocked_payload = {**payload, "preview_blockers": [blocker]}
                    blocked = run_direct_ray_trace(build_direct_trace_input(scene, blocked_payload))
                    self.assertEqual(blocked.receiver_hit_count, 0)
                    outside = copy.deepcopy(blocked_payload)
                    outside["preview_blockers"][0]["center"][0] = 100
                    self.assertGreater(run_direct_ray_trace(build_direct_trace_input(scene, outside)).receiver_hit_count, 100)


if __name__ == "__main__":
    unittest.main()
