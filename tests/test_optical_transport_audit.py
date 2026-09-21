from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.raytrace_bridge import build_direct_trace_input, build_prepared_trace_geometry
from leakage_simulator.raytracer import run_direct_ray_trace


def override_scene():
    return {
        "vertices": [[-20, -20, 0], [-10, -20, 0], [-20, -10, 0],
                     [-10, -10, 10], [10, -10, 10], [0, 10, 10]],
        "faces": [[0, 1, 2], [3, 4, 5]],
        "face_component_ids": [7, 8],
        "face_material_ids": ["default", "default"],
    }


def override_payload(ray_count=64):
    return {
        "emitters": [{"emitter_id": "source", "emitter_type": "datum_plane",
                      "center": [0, 0, 0], "u_axis": [1, 0, 0], "v_axis": [0, 1, 0],
                      "width_mm": 0.1, "height_mm": 0.1,
                      "direction_distribution": "gaussian", "gaussian_sigma_deg": 0.01,
                      "ray_count": ray_count, "power_lumen": 1.0, "seed": 123}],
        "receivers": [{"receiver_id": "receiver", "center": [0, 0, -2],
                       "normal": [0, 0, 1], "width_mm": 10, "height_mm": 10,
                       "resolution": [2, 2]}],
        "optical_profiles": [{"profile_id": "default", "reflectance": 0.1,
                              "scatter_model": "specular"},
                             {"profile_id": "face_mirror", "reflectance": 0.9,
                              "scatter_model": "specular"}],
        "optical_assignments": [{"assignment_id": "override", "target_type": "faces",
                                 "component_id": 8, "profile_id": "face_mirror",
                                 "face_indices": [1]}],
        "config": {"compute_backend": "cpu", "max_depth": 1, "min_energy": 0,
                   "angle_dependent_reflectance": False, "store_ray_paths": False},
    }


def clipped_override_case(ray_count=64):
    scene = override_scene()
    scene["faces"] = [scene["faces"][0]] * 5 + [scene["faces"][1]]
    scene["face_component_ids"] = [7] * 5 + [8]
    scene["face_material_ids"] = ["default"] * 6
    payload = override_payload(ray_count)
    payload["roi_faces"] = [5]
    payload["optical_assignments"][0]["face_indices"] = [5]
    payload["roi_clip_boxes"] = [{
        "x_min": -2, "x_max": 2, "y_min": -1, "y_max": 1,
        "z_min": 9, "z_max": 11,
    }]
    return scene, payload


def capture_payload(reflectance, scatter_model, ray_count=128):
    payload = override_payload(ray_count)
    payload["roi_faces"] = [1]
    payload["optical_profiles"][1].update({
        "reflectance": reflectance, "scatter_model": scatter_model,
        "specular_ratio": 0.35, "diffuse_ratio": 0.65, "gaussian_sigma_deg": 12,
    })
    payload["receivers"] = [
        {"receiver_id": "bottom", "center": [0, 0, -10], "normal": [0, 0, 1],
         "u_axis": [1, 0, 0], "v_axis": [0, 1, 0], "width_mm": 100, "height_mm": 100},
        {"receiver_id": "left", "center": [-50, 0, 0], "normal": [1, 0, 0],
         "u_axis": [0, 1, 0], "v_axis": [0, 0, 1], "width_mm": 100, "height_mm": 20},
        {"receiver_id": "right", "center": [50, 0, 0], "normal": [-1, 0, 0],
         "u_axis": [0, 1, 0], "v_axis": [0, 0, 1], "width_mm": 100, "height_mm": 20},
        {"receiver_id": "front", "center": [0, -50, 0], "normal": [0, 1, 0],
         "u_axis": [1, 0, 0], "v_axis": [0, 0, 1], "width_mm": 100, "height_mm": 20},
        {"receiver_id": "back", "center": [0, 50, 0], "normal": [0, -1, 0],
         "u_axis": [1, 0, 0], "v_axis": [0, 0, 1], "width_mm": 100, "height_mm": 20},
    ]
    for receiver in payload["receivers"]:
        receiver["resolution"] = [2, 2]
    return payload


def captured_flux(result):
    return sum(result.metrics[grid.receiver_id]["total_flux_lumen"] for grid in result.receiver_grids)


class OpticalTransportAuditTests(unittest.TestCase):
    def test_override_flux_survives_roi_exclusion_transform_and_prepared_reuse(self):
        for mode in ("full", "roi", "exclude", "roi_exclude", "transformed"):
            payload = override_payload()
            if mode in ("roi", "roi_exclude", "transformed"):
                payload["roi_faces"] = [1]
            if mode in ("exclude", "roi_exclude"):
                payload["excluded_component_ids"] = [7]
            if mode == "transformed":
                payload["transform_rules"] = [{
                    "target_type": "component", "object_id": 8, "enabled": True,
                    "move": {"x": 0, "y": 0, "z": 3},
                    "tilt": {"x": 0, "y": 0, "z": 30},
                }]
            restored = json.loads(json.dumps(payload))
            prepared = build_prepared_trace_geometry(override_scene(), restored)
            for reuse in (False, True):
                for dispatch in ("scalar", "batch"):
                    with self.subTest(mode=mode, reuse=reuse, dispatch=dispatch):
                        trace_input = build_direct_trace_input(
                            override_scene(), restored,
                            prepared_geometry=prepared if reuse else None,
                            geometry_cache_hit=reuse,
                        )
                        self.assertEqual(trace_input.optical_assignments[0].face_indices, [1])
                        result = run_direct_ray_trace(
                            trace_input, intersection_dispatch=dispatch,
                            intersection_provider="python_cpu", wavefront_planner="python_cpu",
                        )
                        self.assertEqual(result.receiver_hit_count, 64)
                        self.assertAlmostEqual(result.metrics["receiver"]["total_flux_lumen"], 0.9, places=12)
                        self.assertEqual(restored, payload)

    def test_source_indices_do_not_alias_adjacent_surviving_faces(self):
        scene = override_scene()
        scene["vertices"] += [[20, -10, 10], [40, -10, 10], [30, 10, 10]]
        scene["faces"].append([6, 7, 8])
        scene["face_component_ids"] = [8, 8, 8]
        scene["face_material_ids"].append("default")
        payload = override_payload()
        payload["roi_faces"] = [1, 2]
        payload["optical_profiles"].append({"profile_id": "neighbor", "reflectance": 0.3})
        payload["optical_assignments"].append({
            "assignment_id": "neighbor", "target_type": "faces", "component_id": 8,
            "profile_id": "neighbor", "face_indices": [2],
        })
        trace_input = build_direct_trace_input(scene, payload)
        resolver = OpticalPropertyResolver(trace_input.mesh, trace_input.optical_profiles,
                                           trace_input.optical_assignments)
        self.assertEqual(resolver.resolve(0).profile.profile_id, "face_mirror")
        self.assertEqual(resolver.resolve(1).profile.profile_id, "neighbor")

    def test_roi_clip_children_keep_optical_source_ids_and_emitter_geometry_ids(self):
        scene, payload = clipped_override_case()
        prepared = build_prepared_trace_geometry(scene, payload)
        self.assertEqual(len(prepared.mesh.faces), 2)
        for reuse in (False, True):
            with self.subTest(reuse=reuse):
                trace_input = build_direct_trace_input(
                    scene, payload, prepared if reuse else None, reuse,
                )
                self.assertEqual(trace_input.optical_assignments[0].face_indices, [5])
                resolver = OpticalPropertyResolver(
                    trace_input.mesh, trace_input.optical_profiles, trace_input.optical_assignments,
                )
                for face_index in range(len(trace_input.mesh.faces)):
                    self.assertEqual(trace_input.mesh.metadata(face_index)["source_face_index"], 5)
                    self.assertEqual(resolver.resolve(face_index).profile.reflectance, 0.9)
                result = run_direct_ray_trace(trace_input, intersection_provider="python_cpu")
                self.assertAlmostEqual(result.metrics["receiver"]["total_flux_lumen"], 0.9, places=12)
        emitter_payload = copy.deepcopy(payload)
        emitter_payload["emitters"] = [{"emitter_id": "face", "emitter_type": "face", "face_indices": [5]}]
        face_input = build_direct_trace_input(scene, emitter_payload, prepared, True)
        self.assertEqual(face_input.emitters[0].face_indices, list(range(len(prepared.mesh.faces))))
        self.assertEqual(face_input.optical_assignments[0].face_indices, [5])

    def test_reediting_profile_does_not_reuse_stale_optical_values(self):
        payload = override_payload()
        payload["roi_faces"] = [1]
        prepared = build_prepared_trace_geometry(override_scene(), payload)
        for reflectance in (0.9, 0.6, 0.14):
            edited = copy.deepcopy(payload)
            edited["optical_profiles"][1]["reflectance"] = reflectance
            trace_input = build_direct_trace_input(override_scene(), edited, prepared, True)
            result = run_direct_ray_trace(trace_input, intersection_dispatch="batch",
                                          intersection_provider="python_cpu")
            self.assertAlmostEqual(result.metrics["receiver"]["total_flux_lumen"], reflectance, places=12)

    def test_all_reflected_flux_reaches_capture_box_for_every_scatter_model(self):
        for scatter_model in ("specular", "lambertian", "gaussian", "mixed"):
            for reflectance in (0, 0.03, 0.14, 0.6, 0.95, 0.999, 1):
                for dispatch in ("scalar", "batch"):
                    with self.subTest(model=scatter_model, reflectance=reflectance, dispatch=dispatch):
                        trace_input = build_direct_trace_input(
                            override_scene(), capture_payload(reflectance, scatter_model),
                        )
                        result = run_direct_ray_trace(trace_input, intersection_dispatch=dispatch,
                                                      intersection_provider="python_cpu")
                        self.assertAlmostEqual(captured_flux(result), reflectance, places=12)
                        self.assertEqual(result.receiver_hit_count, 128 if reflectance else 0)

    def test_disabled_missing_and_deleted_overrides_fall_back_without_leaking(self):
        for mode in ("enabled", "disabled", "missing", "deleted"):
            payload = override_payload()
            payload["roi_faces"] = [1]
            payload["optical_profiles"].append({"profile_id": "part", "reflectance": 0.3,
                                                "scatter_model": "specular"})
            payload["optical_assignments"].append({
                "assignment_id": "part", "target_type": "part", "component_id": 8,
                "profile_id": "part",
            })
            if mode == "disabled":
                payload["optical_assignments"][0]["enabled"] = False
            elif mode == "missing":
                payload["optical_assignments"][0]["profile_id"] = "absent"
            elif mode == "deleted":
                payload["optical_assignments"].pop(0)
            with self.subTest(mode=mode):
                result = run_direct_ray_trace(build_direct_trace_input(override_scene(), payload))
                self.assertAlmostEqual(captured_flux(result), 0.9 if mode == "enabled" else 0.3, places=12)

    def test_zero_reflectance_has_no_phantom_reflected_flux_at_grazing_incidence(self):
        payload = capture_payload(0, "specular")
        angle = math.radians(89)
        payload["emitters"][0].update({
            "center": [-math.sin(angle), 0, 10-math.cos(angle)], "width_mm": 0.001, "height_mm": 0.001,
            "aim": {"enabled": True, "mode": "sphere", "distribution": "uniform_solid_angle",
                    "sphere_upper_deg": 0, "sphere_lower_deg": 0, "sphere_beta_deg": 89},
        })
        payload["config"]["angle_dependent_reflectance"] = True
        for dispatch in ("scalar", "batch"):
            with self.subTest(dispatch=dispatch):
                result = run_direct_ray_trace(build_direct_trace_input(override_scene(), payload),
                                              intersection_dispatch=dispatch)
                self.assertEqual(captured_flux(result), 0)
                profile = result.metrics["_optical_summary"]["profile_hits"]["face_mirror"]
                self.assertEqual(profile["potential_reflected_flux_lumen"], 0)


if __name__ == "__main__":
    unittest.main()
