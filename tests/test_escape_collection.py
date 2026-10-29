from __future__ import annotations
import math
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from leakage_simulator.escape_collection import EscapeCollector
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.types import ReceiverSpec, ReceiverGrid, EmitterSpec, RayTraceConfig, OpticalProfile


def receivers():
    result = []
    for axis, name in enumerate("xyz"):
        for sign, prefix in ((1, "pos_"), (-1, "neg_")):
            center, normal = [0., 0., 0.], [0., 0., 0.]
            center[axis], normal[axis] = sign * 10., -sign
            u, v = [0., 0., 0.], [0., 0., 0.]
            u[(axis + 1) % 3], v[(axis + 2) % 3] = 1., 1.
            result.append(ReceiverSpec(receiver_id="__leak_preview_" + prefix + name, center=tuple(center),
                normal=tuple(normal), u_axis=tuple(u), v_axis=tuple(v), width_mm=20., height_mm=20., resolution=(1, 1)))
    return result


def collector(regions=None):
    detectors = receivers()
    return EscapeCollector({"minimum": [-10., -10., -10.], "maximum": [10., 10., 10.],
        "direction": "neg_z", "allowed_regions": regions or []}, detectors,
        {r.receiver_id: ReceiverGrid.empty(r) for r in detectors})


class EscapeCollectionTests(unittest.TestCase):
    def test_boundary_normal_offset_preserves_grazing_escapes_on_every_axis(self):
        for axis in range(3):
            for sign in (-1, 1):
                for angle in (0, 60, 80, 89.9, 90):
                    with self.subTest(axis=axis, sign=sign, angle=angle):
                        detectors = receivers()
                        direction_name = ("pos_" if sign > 0 else "neg_") + "xyz"[axis]
                        subject = EscapeCollector({"minimum": [-10.] * 3, "maximum": [10.] * 3,
                            "direction": direction_name}, detectors,
                            {detector.receiver_id: ReceiverGrid.empty(detector) for detector in detectors},
                            epsilon_mm=.001)
                        origin, direction = [0.] * 3, [0.] * 3
                        origin[axis] = sign * 10.001
                        direction[axis] = sign * math.cos(math.radians(angle))
                        direction[(axis + 1) % 3] = math.sin(math.radians(angle))
                        self.assertTrue(subject.record(origin, direction, .25, 0))
                        self.assertEqual(subject.summary()["selected_count"], 1)
                        self.assertAlmostEqual(subject.summary()["escaped_flux_lumen"], .25)
                        origin[axis] = sign * 10.01
                        self.assertFalse(subject.record(origin, direction, .25, 0))
                        self.assertEqual(subject.summary()["escaped_count"], 1)

    def test_all_extreme_angles_including_exact_90_reach_direction_bins(self):
        c = collector()
        for angle in (0, 29, 30, 59, 60, 79, 80, 89.9, 90):
            rad = math.radians(angle)
            self.assertTrue(c.record((0, 0, 0), (math.sin(rad), 0, -math.cos(rad)), 1., 0))
        summary = c.summary()
        self.assertEqual(summary["selected_count"], 9)
        self.assertEqual([b["count"] for b in summary["bins"]], [2, 2, 2, 3])
        self.assertAlmostEqual(sum(b["flux_lumen"] for b in summary["bins"]), 9.)
        self.assertFalse(c.record((0, 0, 0), (0, 0, 1), 1., 0))
        self.assertEqual(c.summary()["selected_count"], 9)

    def test_allowed_filter_is_applied_per_ray_before_averaging_shared_cells(self):
        c = collector([{"plane": "xy", "xMin": -7, "xMax": -5, "yMin": -1, "yMax": 1}])
        self.assertFalse(c.record((-6, 0, 0), (0, 0, -1), 1., 0))
        self.assertTrue(c.record((6, 0, 0), (0, 0, -1), 2., 0))
        s = c.summary()
        self.assertEqual(s["excluded_count"], 1)
        self.assertEqual(s["selected_count"], 1)
        self.assertEqual(s["cells"][0]["position"], [6., 0., -10.])
        self.assertEqual(s["bins"][0]["flux_lumen"], 2.)

    def test_oblique_allowed_hole_is_checked_at_its_plane_not_enclosure(self):
        region = {"plane": "xy", "xMin": -.5, "xMax": .5, "yMin": -.5, "yMax": .5, "zMin": -2, "zMax": -2}
        c = collector([region])
        d = math.sqrt(.5)
        # Crosses the allowed hole at (0,0,-2), exits enclosure at (8,0,-10).
        self.assertFalse(c.record((-2, 0, 0), (d, 0, -d), 1., 0))
        # A neighbouring real leak must not disappear in the old 5 mm padding.
        self.assertTrue(c.record((1, 0, 0), (0, 0, -1), 1., 0))
        # Exit projects inside the hole, but the crossing at z=-2 is outside it.
        self.assertTrue(c.record((-10, 0, 0), (d, 0, -d), 1., 0))
        self.assertEqual(c.summary()["excluded_count"], 1)
        self.assertEqual(c.summary()["selected_count"], 2)

    def test_allowed_boundaries_do_not_exclude_ray_behind_or_parallel_to_patch(self):
        region = {"plane": "xy", "xMin": -1, "xMax": 1, "yMin": -1, "yMax": 1, "zMin": 2, "zMax": 2}
        self.assertTrue(collector([region]).record((0, 0, 0), (0, 0, -1), 1., 0))
        self.assertTrue(collector([region]).record((0, 0, 0), (1, 0, 0), 1., 0))

    def test_allowed_side_planes_and_volumes_follow_final_segment(self):
        for plane, origin, direction, region in (
            ("yz", (0, 0, 0), (1, 0, 0), {"xMin": 2, "xMax": 2, "yMin": -1, "yMax": 1, "zMin": -1, "zMax": 1}),
            ("zx", (0, 0, 0), (0, 1, 0), {"xMin": -1, "xMax": 1, "yMin": 2, "yMax": 2, "zMin": -1, "zMax": 1}),
            ("xyz", (0, 0, 0), (0, 0, -1), {"xMin": -1, "xMax": 1, "yMin": -1, "yMax": 1, "zMin": -3, "zMax": -2}),
        ):
            with self.subTest(plane=plane):
                self.assertFalse(collector([dict(region, plane=plane)]).record(origin, direction, 1., 0))
                self.assertTrue(collector([dict(region, plane=plane)]).record((5, 5, 5), direction, 1., 0))

    def test_bsdf_directional_batch_transport_keeps_tables_and_collects_final_reflections(self):
        from test_bsdf_transport import synthetic_asset, planar_trace_input
        trace = planar_trace_input(synthetic_asset(), rays=128, depth=3)
        trace.receivers = receivers()
        trace.config.store_ray_paths = False
        trace.config.contribution_mode = "summary"
        trace.escape_collection = {"minimum": [-100., -100., -1.], "maximum": [100., 100., 2.], "direction": "pos_z", "allowed_regions": []}
        result = run_direct_ray_trace(trace)
        summary = result.metrics["_escape_direction_summary"]
        self.assertGreater(summary["selected_count"], 30)
        self.assertGreater(sum(b["flux_lumen"] for b in summary["bins"]), 0.)
        self.assertLessEqual(sum(b["flux_lumen"] for b in summary["bins"]), 1.)

    def test_remote_validation_cases_run_on_cpu_and_do_not_depend_on_path_quota(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from verify_escape_direction_accuracy import build_case
        for angle, reflected, face in ((0, False, False), (45, False, False), (89.9, False, False), (0, True, False), (0, False, True)):
            with self.subTest(angle=angle, reflected=reflected, face=face):
                result = run_direct_ray_trace(build_case(angle, 128, "cpu", reflected, face))
                summary = result.metrics["_escape_direction_summary"]
                self.assertEqual(result.receiver_hit_count + result.terminated_ray_count, result.total_rays)
                self.assertGreater(summary["selected_count"], 30)
                self.assertEqual(summary["sample_contract"], "escape_shared_counter_rng_batch_v1")
                self.assertEqual(sum(b["count"] for b in summary["bins"]), summary["selected_count"])

    def test_remote_bsdf_fixture_is_executable_with_the_same_batch_contract(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from verify_escape_direction_accuracy import build_case
        result = run_direct_ray_trace(build_case(0, 128, "cpu", reflected=True, bsdf=True))
        summary = result.metrics["_escape_direction_summary"]
        self.assertGreater(summary["selected_count"], 30)
        self.assertEqual(summary["sample_contract"], "escape_shared_counter_rng_batch_v1")

    def test_accuracy_gate_never_calls_identical_cpu_fallback_results_gpu_success(self):
        from unittest.mock import patch
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import verify_escape_direction_accuracy as gate
        real = run_direct_ray_trace
        def cpu_only(trace):
            trace.config.compute_backend = "cpu"
            return real(trace)
        with patch.object(gate, "run_direct_ray_trace", side_effect=cpu_only):
            report = gate.verify_case("cpu_fallback", 0, 32)
        self.assertFalse(report["passed"])
        self.assertTrue(all(run["difference_count"] == 0 for run in report["runs"]))
        self.assertTrue(all(not run["gpu_execution_proven"] for run in report["runs"]))

    def test_external_sources_pointing_away_are_not_model_escapes(self):
        self.assertFalse(collector().record((0, 0, 20), (0, 0, 1), 1., 0))
        c = collector()
        self.assertTrue(c.record((0, 0, -10.0001), (0, 0, -1), 1., 0))
        self.assertEqual(c.summary()["cells"][0]["position"][2], -10.)

    def test_engine_collects_every_escape_without_storing_paths_and_respects_occlusion(self):
        mesh = TriangleMesh()
        ids = [mesh.add_vertex(p) for p in ((100, 100, 100), (101, 100, 100), (100, 101, 100))]
        mesh.add_face(*ids, "default")
        source = EmitterSpec(emitter_id="source", emitter_type="datum_plane", center=(0, 0, 0),
            u_axis=(1, 0, 0), v_axis=(0, -1, 0), width_mm=.1, height_mm=.1,
            direction_distribution="gaussian", gaussian_sigma_deg=.1, power_lumen=1., ray_count=32, seed=7)
        spec = {"minimum": [-10., -10., -10.], "maximum": [10., 10., 10.], "direction": "neg_z", "allowed_regions": []}
        trace = DirectRayTraceInput(mesh=mesh, emitters=[source], receivers=receivers(),
            optical_profiles=[OpticalProfile(profile_id="default", reflectance=0., absorption=1.)],
            config=RayTraceConfig(ray_count=32, max_depth=3, store_ray_paths=False, compute_backend="cpu"), escape_collection=spec)
        reference = run_direct_ray_trace(trace, intersection_dispatch="scalar")
        self.assertEqual(reference.metrics["_escape_direction_summary"]["selected_count"], 32)
        result = run_direct_ray_trace(trace)
        s = result.metrics["_escape_direction_summary"]
        self.assertEqual(s["selected_count"], 32)
        self.assertEqual(s["bins"], reference.metrics["_escape_direction_summary"]["bins"])
        self.assertAlmostEqual(sum(b["flux_lumen"] for b in s["bins"]), 1.)
        self.assertAlmostEqual(sum(sum(sum(row) for row in grid.flux_lumen) for grid in result.receiver_grids), 1.)
        self.assertEqual(result.stored_paths, [])
        self.assertEqual(result.receiver_hit_count + result.terminated_ray_count, result.total_rays)
        # Geometry beyond a virtual boundary still occludes the final escape.
        vertices = [mesh.add_vertex(p) for p in ((-50,-50,-15), (50,-50,-15), (50,50,-15), (-50,50,-15))]
        mesh.add_face(vertices[0], vertices[1], vertices[2], "default")
        mesh.add_face(vertices[0], vertices[2], vertices[3], "default")
        blocked = run_direct_ray_trace(trace)
        self.assertEqual(blocked.metrics["_escape_direction_summary"]["selected_count"], 0)


if __name__ == "__main__":
    unittest.main()
