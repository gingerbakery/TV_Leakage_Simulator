from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from unittest import TestCase
import math

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.types import EmitterSpec, OpticalProfile, ReceiverGrid, RayTraceConfig
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.escape_collection import EscapeCollector
from leakage_simulator.observer_probe import (trace_observer_paths, reflection_supported,
    emission_supported, validate_settings, view_direction)
from test_escape_collection import receivers


def quad(mesh, z, material="black", radius=20):
    ids = [mesh.add_vertex(p) for p in ((-radius,-radius,z), (radius,-radius,z), (radius,radius,z), (-radius,radius,z))]
    mesh.add_face(ids[0], ids[1], ids[2], material)
    mesh.add_face(ids[0], ids[2], ids[3], material)


def setup(mirror=False, allowed=None):
    mesh = TriangleMesh()
    # Source is viewed from below. In mirror case it emits away from observer.
    source = EmitterSpec(emitter_id="led", emitter_type="datum_plane", center=(0,0,-5),
        u_axis=(1,0,0), v_axis=(0,1 if mirror else -1,0), width_mm=18, height_mm=18,
        power_lumen=1., ray_count=16)
    if mirror:
        quad(mesh, 0, "mirror")
    else:
        quad(mesh, 9)
    profiles = [OpticalProfile(profile_id="black", reflectance=0, absorption=1, scatter_model="none"),
                OpticalProfile(profile_id="mirror", reflectance=1, absorption=0, scatter_model="specular")]
    detectors = receivers()
    collector = EscapeCollector({"minimum": [-10,-10,-10], "maximum": [10,10,10], "direction": "neg_z", "allowed_regions": allowed or []},
        detectors, {r.receiver_id: ReceiverGrid.empty(r) for r in detectors})
    spec = {"horizontal_deg": 0., "vertical_deg": 0., "range_deg": 0., "step_deg": 1., "resolution": 8, "samples_per_cell": 1}
    trace = SimpleNamespace(mesh=mesh, emitters=[source], config=RayTraceConfig(max_depth=3), observer_probe=spec)
    def run(stop=None):
        resolved = OpticalPropertyResolver(mesh, profiles, []).resolve_all()
        return trace_observer_paths(trace, collector, resolved, stop)
    return trace, run


class ObserverProbeTests(TestCase):
    def test_face_emitter_uses_winding_normal_not_reverse_ray_facing_normal(self):
        trace, _ = setup()
        mesh = TriangleMesh()
        quad(mesh, -5, "black", 9)
        trace.mesh = mesh
        trace.emitters[0].emitter_type = "face"
        trace.emitters[0].face_indices = [0, 1]
        trace.emitters[0].normal_flip = True
        resolved = OpticalPropertyResolver(mesh, [OpticalProfile(profile_id="black", reflectance=0)], []).resolve_all()
        detectors = receivers()
        c = EscapeCollector({"minimum": [-10,-10,-10], "maximum": [10,10,10], "direction": "neg_z"}, detectors,
            {r.receiver_id: ReceiverGrid.empty(r) for r in detectors})
        self.assertGreater(trace_observer_paths(trace, c, resolved)["direct_count"], 0)
        trace.emitters[0].normal_flip = False
        self.assertEqual(trace_observer_paths(trace, c, resolved)["direct_count"], 0)

    def test_validation_gate_rejects_identical_cpu_fallback_as_gpu_success(self):
        from unittest.mock import patch
        from leakage_simulator.gpu_cuda_intersection import GpuCudaUnavailable
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        from verify_observer_gpu_accuracy import verify_case
        with patch.object(TriangleMesh, "intersect_rays_gpu_cuda", side_effect=GpuCudaUnavailable("test_no_device")):
            result = verify_case("rear_0", 16, True)
        self.assertTrue(result["cpu_validation_passed"])
        self.assertFalse(result["gpu_validation_passed"])
        self.assertTrue(all(run["difference_count"] == 0 for run in result["gpu_runs"]))
        self.assertTrue(all(not run["gpu_execution_proven"] for run in result["gpu_runs"]))

    def test_scalar_and_different_batch_groupings_use_identical_samples(self):
        for mirror in (False, True):
            trace, _ = setup(mirror)
            profiles = [OpticalProfile(profile_id="black", reflectance=0, scatter_model="none"),
                OpticalProfile(profile_id="mirror", reflectance=.5, scatter_model="lambertian")]
            resolved = OpticalPropertyResolver(trace.mesh, profiles, []).resolve_all()
            detectors = receivers()
            c = EscapeCollector({"minimum": [-10,-10,-10], "maximum": [10,10,10], "direction": "neg_z"}, detectors,
                {r.receiver_id: ReceiverGrid.empty(r) for r in detectors})
            def physical(result):
                return {k:v for k,v in result.items() if k not in ("runtime_sec", "performance")}
            expected = physical(trace_observer_paths(trace, c, resolved, dispatch="scalar"))
            for size in (1, 7, 8192):
                with self.subTest(mirror=mirror, size=size):
                    self.assertEqual(expected, physical(trace_observer_paths(trace, c, resolved, batch_size=size)))

    def test_gpu_batch_routing_and_fallback_are_accounted_separately(self):
        # Adapter test only: mock output is never real-device GPU evidence.
        from unittest.mock import patch
        from leakage_simulator.gpu_cuda_intersection import GpuCudaExecution, GpuCudaUnavailable, GpuCudaProviderError
        from dataclasses import fields
        trace, _ = setup(True)
        trace.config.compute_backend = "gpu_cuda"
        trace.config.intersection_backend = "bvh"
        profiles = [OpticalProfile(profile_id="mirror", reflectance=1, scatter_model="specular")]
        resolved = OpticalPropertyResolver(trace.mesh, profiles, []).resolve_all()
        detectors = receivers()
        c = EscapeCollector({"minimum": [-10,-10,-10], "maximum": [10,10,10], "direction": "neg_z"}, detectors,
            {r.receiver_id: ReceiverGrid.empty(r) for r in detectors})
        calls = []
        def fake(rays, backend=None):
            calls.append(len(rays))
            hits = trace.mesh.intersect_rays(rays, backend)
            args = {f.name: 0. for f in fields(GpuCudaExecution) if f.name.endswith("_sec")}
            return hits, GpuCudaExecution(distances=hits.t, face_indices=hits.face_indices, **args,
                numba_version="MOCK", device_name="MOCK", compute_capability="MOCK", device_id=0,
                toolkit_layout="MOCK", reused_device_scene=True, reused_workspace=True)
        with patch.object(trace.mesh, "intersect_rays_gpu_cuda", side_effect=fake):
            result = trace_observer_paths(trace, c, resolved)
        self.assertGreater(max(calls), 1)
        self.assertEqual(result["performance"]["gpu_cuda_gpu_success_count"], len(calls))
        self.assertEqual(result["performance"]["compute_execution_state"], "gpu_active")
        self.assertGreater(result["reflected_count"], 0)
        with patch.object(trace.mesh, "intersect_rays_gpu_cuda", side_effect=GpuCudaUnavailable("test_unavailable")):
            fallback = trace_observer_paths(trace, c, resolved)
        self.assertEqual(fallback["execution_backend"], "cpu")
        self.assertEqual(fallback["performance"]["gpu_cuda_gpu_success_count"], 0)
        self.assertEqual(fallback["performance"]["compute_execution_state"], "gpu_requested_cpu_only")
        self.assertEqual(fallback["direct_count"], result["direct_count"])
        self.assertEqual(fallback["reflected_count"], result["reflected_count"])
        calls.clear()
        def partial(rays, backend=None):
            if calls:
                raise GpuCudaProviderError("execute", "test_after_first_success")
            return fake(rays, backend)
        with patch.object(trace.mesh, "intersect_rays_gpu_cuda", side_effect=partial):
            mixed = trace_observer_paths(trace, c, resolved)
        self.assertEqual(mixed["performance"]["compute_execution_state"], "gpu_mixed")
        self.assertEqual(mixed["performance"]["gpu_cuda_gpu_success_count"], 1)
        self.assertGreater(mixed["performance"]["intersection_fallback_count"], 0)
        self.assertEqual(mixed["cells"], result["cells"])

    def test_direct_source_and_opaque_occlusion(self):
        trace, run = setup()
        result = run()
        self.assertGreater(result["direct_count"], 0)
        self.assertEqual(result["reflected_count"], 0)
        self.assertEqual(result["attempted_count"], 128)
        self.assertEqual(result["measure"], "path_support_counts")
        quad(trace.mesh, -8)
        self.assertEqual(run()["direct_count"], 0)

    def test_mirror_only_path_and_absorber_do_not_become_direct_light(self):
        trace, run = setup(mirror=True)
        result = run()
        self.assertEqual(result["direct_count"], 0)
        self.assertGreater(result["reflected_count"], 0)
        self.assertTrue(all(c["min_depth"] == 1 for c in result["cells"]))
        quad(trace.mesh, -8)
        self.assertEqual(run()["reflected_count"], 0)

    def test_allowed_boundary_filters_direct_and_reflected_paths(self):
        region = {"plane": "xy", "xMin": -10, "xMax": 10, "yMin": -10, "yMax": 10, "zMin": -7, "zMax": -7}
        for mirror in (False, True):
            with self.subTest(mirror=mirror):
                _, run = setup(mirror, [region])
                result = run()
                self.assertEqual(result["direct_count"]+result["reflected_count"], 0)
                self.assertGreater(result["excluded_count"], 0)

    def test_extreme_compound_views_and_sweep_include_center_and_endpoints(self):
        direction = view_direction("neg_z", 89.9, 0)
        self.assertGreater(direction[0], .999)
        self.assertLess(direction[2], 0)
        compound = view_direction("neg_z", 85, 80)
        self.assertGreater(compound[0], 0)
        self.assertGreater(compound[1], 0)
        self.assertLess(compound[2], 0)
        trace, _ = setup()
        trace.observer_probe.update(horizontal_deg=88.9, range_deg=1, step_deg=.6)
        views = validate_settings(trace.observer_probe)
        self.assertIn((88.9, 0), views)
        self.assertIn((89.9, 1), views)
        self.assertIn((87.9, -1), views)

    def test_extreme_direction_direct_source_is_found_without_forward_paths(self):
        trace, run = setup()
        trace.observer_probe.update(horizontal_deg=89.9)
        result = run()
        self.assertGreater(result["direct_count"], 0)
        self.assertEqual(result["reflected_count"], 0)

    def test_diffuse_connection_finds_reflections_and_obeys_reflection_support(self):
        trace, _ = setup(mirror=True)
        profiles = [OpticalProfile(profile_id="mirror", reflectance=.5, absorption=.5, scatter_model="lambertian")]
        resolved = OpticalPropertyResolver(trace.mesh, profiles, []).resolve_all()
        detectors = receivers()
        collector = EscapeCollector({"minimum": [-10,-10,-10], "maximum": [10,10,10], "direction": "neg_z"}, detectors,
            {r.receiver_id: ReceiverGrid.empty(r) for r in detectors})
        result = trace_observer_paths(trace, collector, resolved)
        self.assertGreater(result["reflected_count"], 0)
        self.assertFalse(reflection_supported(profiles[0], (0,0,1), (0,0,-1), (0,0,-1)))

    def test_bsdf_support_is_evaluated_forward_not_assumed_reciprocal(self):
        from test_bsdf_transport import synthetic_asset
        from leakage_simulator.bsdf_transport import compile_asset
        profile = OpticalProfile(profile_id="measured", reflectance=.5, bsdf_asset_id="fixture")
        profile._bsdf_transport = compile_asset(synthetic_asset())
        model = profile._bsdf_transport
        import random
        incoming, normal = (0,0,-1), (0,0,1)
        outgoing = model.sample_direction(random.Random(1), incoming, normal)
        self.assertTrue(reflection_supported(profile, normal, incoming, outgoing))
        self.assertFalse(reflection_supported(profile, normal, incoming, (0,0,-1)))
        profile._bsdf_transport = None
        with self.assertRaises(ValueError):
            reflection_supported(profile, normal, incoming, outgoing)

    def test_invalid_and_excessive_settings_fail_closed_and_stop_is_partial(self):
        trace, run = setup()
        for patch in ({"horizontal_deg": 90}, {"step_deg": 0}, {"resolution": 8.5}, {"range_deg": 10, "step_deg": .1}, {"vertical_deg": math.nan}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_settings(dict(trace.observer_probe, **patch))
        result = run(lambda: True)
        self.assertTrue(result["stopped"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["attempted_count"], 0)

    def test_source_side_and_aim_are_respected(self):
        trace, _ = setup()
        source = trace.emitters[0]
        self.assertTrue(emission_supported(source, (0,0,-5), (0,0,-1), (0,0,-1)))
        self.assertFalse(emission_supported(source, (0,0,-5), (0,0,-1), (0,0,1)))
        from leakage_simulator.types import EmitterAimSpec
        source.aim = EmitterAimSpec(enabled=True, mode="sphere", distribution="uniform_solid_angle", sphere_upper_deg=0, sphere_lower_deg=5)
        self.assertTrue(emission_supported(source, (0,0,-5), (0,0,-1), (0,0,1)))
        self.assertFalse(emission_supported(source, (0,0,-5), (0,0,-1), (1,0,0)))

    def test_two_specular_bounces_are_discovered(self):
        trace, run = setup(mirror=True)
        mesh = TriangleMesh()
        def tilted(center, u):
            points = [vec_add(center, vec_add(vec_mul(u, su*2), (0, sv*8, 0))) for su, sv in ((-1,-1),(1,-1),(1,1),(-1,1))]
            ids = [mesh.add_vertex(p) for p in points]
            mesh.add_face(ids[0], ids[1], ids[2], "mirror")
            mesh.add_face(ids[0], ids[2], ids[3], "mirror")
        from leakage_simulator.geometry import vec_add, vec_mul
        d = math.sqrt(.5)
        tilted((0,0,0), (d,0,d))
        tilted((5,0,0), (d,0,-d))
        trace.mesh = mesh
        trace.emitters[0].center = (5,0,-5)
        trace.emitters[0].width_mm = 4
        trace.observer_probe["resolution"] = 16
        profiles = [OpticalProfile(profile_id="mirror", reflectance=1, absorption=0, scatter_model="specular")]
        resolved = OpticalPropertyResolver(mesh, profiles, []).resolve_all()
        detectors = receivers()
        c = EscapeCollector({"minimum": [-10,-10,-10], "maximum": [10,10,10], "direction": "neg_z"}, detectors,
            {r.receiver_id: ReceiverGrid.empty(r) for r in detectors})
        result = trace_observer_paths(trace, c, resolved)
        self.assertGreater(result["reflected_count"], 0)
        self.assertTrue(any(cell["max_depth"] >= 2 for cell in result["cells"]))

    def test_engine_preserves_probe_separate_from_physical_flux(self):
        from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
        trace, _ = setup()
        direct = DirectRayTraceInput(mesh=trace.mesh, emitters=trace.emitters, receivers=receivers(),
            optical_profiles=[OpticalProfile(profile_id="black", reflectance=0, absorption=1, scatter_model="none")],
            config=RayTraceConfig(ray_count=16, max_depth=3, store_ray_paths=False), observer_probe=trace.observer_probe,
            escape_collection={"minimum": [-10,-10,-10], "maximum": [10,10,10], "direction": "neg_z"})
        result = run_direct_ray_trace(direct)
        probe = result.metrics["_observer_probe_summary"]
        self.assertGreater(probe["direct_count"], 0)
        self.assertEqual(result.total_rays, 16)
        self.assertLessEqual(result.metrics["_escape_direction_summary"]["selected_count"], 16)
        self.assertEqual(result.receiver_hit_count + result.terminated_ray_count, 16)

    def test_thin_source_and_backside_emission_do_not_depend_on_grid_resolution(self):
        trace, run = setup()
        trace.emitters[0].width_mm = .001
        trace.emitters[0].height_mm = .001
        trace.observer_probe["horizontal_deg"] = 89.9
        self.assertGreater(run()["direct_count"], 0)
        trace.emitters[0].normal_flip = True
        self.assertEqual(run()["direct_count"], 0)

    def test_compound_extreme_angles_find_source_and_cad_face_occlusion(self):
        trace, run = setup()
        trace.observer_probe.update(horizontal_deg=-85, vertical_deg=80)
        self.assertGreater(run()["direct_count"], 0)
        trace.emitters[0].enabled = False
        self.assertEqual(run()["direct_count"], 0)

    def test_bridge_passes_observer_settings_to_engine(self):
        from leakage_simulator.raytrace_bridge import build_direct_trace_input
        from dataclasses import asdict
        trace, _ = setup()
        mesh = {"vertices": [[0,0,0], [1,0,0], [0,1,0]], "faces": [[0,1,2]], "face_component_ids": [1]}
        request = {"emitters": [{"emitter_id": "face", "face_indices": [0], "ray_count": 16}],
            "receivers": [asdict(receiver) for receiver in receivers()],
            "optical_profiles": [{"profile_id": "default", "reflectance": 0, "absorption": 1}],
            "config": {"ray_count": 16, "max_depth": 3}, "observer_probe": trace.observer_probe,
            "escape_collection": {"minimum": [-10,-10,-10], "maximum": [10,10,10], "direction": "neg_z"}}
        prepared = build_direct_trace_input(mesh, request)
        self.assertEqual(prepared.observer_probe, trace.observer_probe)

    def test_engine_bsdf_reflections_use_measured_rt_even_with_zero_legacy_reflectance(self):
        from test_bsdf_transport import synthetic_asset
        from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
        for reflectance in (.3, 0.):
            with self.subTest(reflectance=reflectance):
                trace, _ = setup(mirror=True)
                asset = synthetic_asset(reflectance=reflectance)
                direct = DirectRayTraceInput(mesh=trace.mesh, emitters=trace.emitters, receivers=receivers(),
                    optical_profiles=[OpticalProfile(profile_id="mirror", reflectance=0, scatter_model="none", bsdf_asset_id=asset["id"])],
                    bsdf_assets=[asset], config=RayTraceConfig(ray_count=16, max_depth=3, store_ray_paths=False),
                    observer_probe=trace.observer_probe,
                    escape_collection={"minimum": [-10,-10,-10], "maximum": [10,10,10], "direction": "neg_z"})
                result = run_direct_ray_trace(direct).metrics["_observer_probe_summary"]
                self.assertEqual(result["direct_count"], 0)
                if reflectance > 0:
                    self.assertGreater(result["reflected_count"], 0)
                else:
                    self.assertEqual(result["reflected_count"], 0)
