from __future__ import annotations

import os
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import semantic_hash
from benchmark_perf5_transport import semantic_payload
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.native_cpu_wavefront import scatter_codes_from_names
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.raytrace_bridge import build_direct_trace_input
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.types import OpticalAssignment, OpticalProfile
from test_optical_transport_audit import clipped_override_case, override_payload, override_scene
from perf4_accuracy import compare_semantic_payloads


def build_mesh(count):
    mesh = TriangleMesh()
    for vertex in ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
        mesh.add_vertex(vertex)
    for index in range(count):
        component = index % 7
        metadata = {'component_id': str(component), 'source_face_index': str(index // 4)}
        if index % 9 == 0:
            metadata.pop('component_id')
        mesh.add_face(0, 1, 2, ('default', 'black', 'missing')[index % 3], metadata)
    return mesh


class OpticalPaletteTests(unittest.TestCase):
    def assertResolverParity(self, resolver):
        expected = [resolver.resolve(index) for index in range(len(resolver.mesh.faces))]
        actual = resolver.resolve_all()
        self.assertEqual(list(actual), expected)
        self.assertEqual(actual[-3:], tuple(expected[-3:]))
        for original, resolved in zip(expected, actual):
            self.assertIs(resolved.profile, original.profile)
            self.assertEqual(resolved.source, original.source)
            self.assertEqual(resolved.assignment_id, original.assignment_id)
        self.assertFalse(actual.palette_slots.flags.writeable)
        self.assertTrue(actual.palette_slots.flags.owndata)
        tables = actual.numeric_tables(scatter_codes_from_names)
        reference = (
            np.asarray([entry.profile.reflectance for entry in expected], dtype=np.float64),
            np.asarray([entry.profile.roughness for entry in expected], dtype=np.float64),
            scatter_codes_from_names(entry.profile.scatter_model for entry in expected),
            np.asarray([entry.profile.specular_ratio for entry in expected], dtype=np.float64),
            np.asarray([entry.profile.gaussian_sigma_deg for entry in expected], dtype=np.float64),
        )
        for original, table in zip(reference, tables):
            np.testing.assert_array_equal(table, original)
            if table.dtype == np.float64:
                np.testing.assert_array_equal(table.view(np.uint64), original.view(np.uint64))
            self.assertFalse(table.flags.writeable)
            self.assertTrue(table.flags.owndata)
        return actual

    def test_priorities_missing_profiles_clipped_parent_ids_and_ties(self):
        generator = random.Random(19019)
        mesh = build_mesh(700)
        profiles = [OpticalProfile('default', 0.14), OpticalProfile('black', 0.03),
                    OpticalProfile('mirror', 0.999, scatter_model='specular', roughness=-0.0)]
        for repeat in range(20):
            assignments = [OpticalAssignment(
                f'assignment-{index}', 'faces' if index % 3 else 'part',
                generator.randrange(7), generator.choice(('default', 'black', 'mirror', 'missing')),
                generator.sample(range(175), 20), generator.randrange(3), index % 11 != 0,
            ) for index in range(45)]
            with self.subTest(repeat=repeat):
                self.assertResolverParity(OpticalPropertyResolver(mesh, profiles, assignments))

    def test_empty_unassigned_and_unused_invalid_profile(self):
        self.assertResolverParity(OpticalPropertyResolver(build_mesh(0), [], []))
        self.assertResolverParity(OpticalPropertyResolver(build_mesh(20), [], []))
        unused = OpticalProfile('unused', 0.9)
        unused.scatter_model = 'unsupported_but_unused'
        resolver = OpticalPropertyResolver(build_mesh(20), [OpticalProfile('default', 0.6), unused], [])
        actual = self.assertResolverParity(resolver)
        self.assertEqual(len(actual.palette), 2)

    def test_profile_assignment_material_and_metadata_reedits_do_not_stay_cached(self):
        mesh = build_mesh(600)
        profiles = [OpticalProfile('default', 0.14), OpticalProfile('black', 0.03),
                    OpticalProfile('mirror', 0.99, scatter_model='specular')]
        assignment = OpticalAssignment('override', 'faces', 1, 'mirror', [0])
        resolver = OpticalPropertyResolver(mesh, profiles, [assignment])
        previous = self.assertResolverParity(resolver).numeric_tables(scatter_codes_from_names)
        snapshot = tuple(table.copy() for table in previous)
        profiles[2].reflectance = 0.6
        profiles[2].scatter_model = 'gaussian'
        self.assertResolverParity(resolver)
        assignment.profile_id = 'black'
        mesh.face_metadata[1]['source_face_index'] = 17
        mesh.face_material[1] = 'mirror'
        self.assertResolverParity(resolver)
        resolver.profiles.pop('default')
        self.assertResolverParity(resolver)
        for original, table in zip(snapshot, previous):
            np.testing.assert_array_equal(table, original)

    def test_public_roi_custom_property_results_are_bit_exact(self):
        for roi in (False, True):
            scene, payload = clipped_override_case(1000) if roi else (override_scene(), override_payload(1000))
            trace_input = build_direct_trace_input(scene, payload)
            for reflectance in (0.03, 0.6, 0.99):
                trace_input.optical_profiles[1].reflectance = reflectance
                results = []
                for mode in ('off', 'on'):
                    with patch.dict(os.environ, {'LEAKAGE_OPTICAL_TABLES': mode}):
                        results.append(run_direct_ray_trace(trace_input))
                self.assertEqual(semantic_hash(results[1]), semantic_hash(results[0]))
                self.assertEqual(results[1].metrics['_performance_summary']['optical_resolution_contract'], 'run_local_optical_palette_v1')

    def test_invalid_source_metadata_is_not_silently_ignored(self):
        mesh = build_mesh(20)
        mesh.face_metadata[2]['source_face_index'] = 'invalid-source'
        resolver = OpticalPropertyResolver(mesh, [OpticalProfile('default', 0.6)], [])
        with self.assertRaises(ValueError):
            resolver.resolve(2)
        with self.assertRaises(ValueError):
            resolver.resolve_all()


class GpuOpticalPaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, key) is True for key in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('production CUDA contract mismatch')

    def test_roi_face_overrides_scatter_and_reedits_match_cpu_and_old_gpu_tables(self):
        for roi in (False, True):
            scene, payload = clipped_override_case(20000) if roi else (override_scene(), override_payload(20000))
            trace_input = build_direct_trace_input(scene, payload)
            trace_input.optical_assignments.append(OpticalAssignment('part_base', 'part', 8, 'default', priority=10))
            for index, scatter in enumerate(('specular', 'lambertian', 'gaussian', 'mixed')):
                with self.subTest(roi=roi, scatter=scatter):
                    profile = trace_input.optical_profiles[1]
                    profile.reflectance = (0.03, 0.6, 0.99, 0.14)[index]
                    profile.scatter_model = scatter
                    profile.specular_ratio = 0.35
                    profile.diffuse_ratio = 0.65
                    trace_input.config.compute_backend = 'cpu'
                    with patch.dict(os.environ, {'LEAKAGE_OPTICAL_TABLES': 'off'}):
                        cpu = run_direct_ray_trace(trace_input)
                    trace_input.config.compute_backend = 'gpu_cuda'
                    results = []
                    for mode in ('off', 'on'):
                        with patch.dict(os.environ, {'LEAKAGE_OPTICAL_TABLES': mode}):
                            results.append(run_direct_ray_trace(trace_input))
                    for expected in (cpu, results[0]):
                        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(results[1]),
                            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
                        self.assertTrue(parity.passed, parity.to_dict())
                    performance = results[1].metrics['_performance_summary']
                    self.assertEqual(performance['optical_resolution_contract'], 'run_local_optical_palette_v1')
                    self.assertEqual(performance['compute_execution_state'], 'gpu_active')
                    self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
                    self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)


if __name__ == '__main__':
    unittest.main()
