from __future__ import annotations

import gc
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import weakref

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import cad_case
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_optical_transport_audit import clipped_override_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import optics
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.raytrace_bridge import build_direct_trace_input
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.types import OpticalAssignment, OpticalProfile
from test_perf5_optical_palette import build_mesh


class OpticalRoutingCacheTests(unittest.TestCase):
    def resolver(self, count=600):
        profiles = [OpticalProfile('default', 0.14), OpticalProfile('black', 0.03),
            OpticalProfile('mirror', 0.99, scatter_model='specular')]
        assignments = [OpticalAssignment('part', 'part', 1, 'mirror', priority=1),
            OpticalAssignment('face', 'faces', 1, 'black', [0], priority=1)]
        return OpticalPropertyResolver(build_mesh(count), profiles, assignments)

    def assert_resolver_parity(self, resolver):
        with patch.dict(os.environ, {'LEAKAGE_OPTICAL_ROUTING_CACHE': 'off'}):
            expected = resolver.resolve_all()
        with patch.dict(os.environ, {'LEAKAGE_OPTICAL_ROUTING_CACHE': 'force'}):
            actual = resolver.resolve_all()
        self.assertEqual(list(actual), list(expected))
        self.assertEqual(actual.palette, expected.palette)
        np.testing.assert_array_equal(actual.palette_slots, expected.palette_slots)
        self.assertFalse(actual.palette_slots.flags.writeable)
        self.assertTrue(actual.palette_slots.flags.owndata)
        for reference, resolved in zip(expected, actual):
            self.assertIs(resolved.profile, reference.profile)
        return actual

    def test_warm_routes_do_not_walk_every_face_or_retain_old_profile_objects(self):
        resolver = self.resolver()
        self.assert_resolver_parity(resolver)
        cached = resolver.mesh._optical_routing_cache
        with patch.dict(os.environ, {'LEAKAGE_OPTICAL_ROUTING_CACHE': 'force'}), patch.object(
            optics, '_build_optical_routing_cache', side_effect=AssertionError('warm cache rebuilt')
        ):
            resolver.resolve_all()
        previous = resolver.profiles['mirror']
        previous_ref = weakref.ref(previous)
        resolver.profiles['mirror'] = OpticalProfile('mirror', 0.6, scatter_model='gaussian')
        del previous
        gc.collect()
        self.assertIsNone(previous_ref())
        self.assert_resolver_parity(resolver)
        self.assertIs(resolver.mesh._optical_routing_cache, cached)

    def test_profile_edits_assignment_targets_ties_and_missing_profiles_remain_current(self):
        resolver = self.resolver()
        self.assert_resolver_parity(resolver)
        for profile in resolver.profiles.values():
            profile.reflectance = 0.6
            profile.scatter_model = 'mixed'
        self.assert_resolver_parity(resolver)
        assignment = resolver.face_assignments[(1, 0)]
        assignment.profile_id = 'mirror'
        assignment.assignment_id = 'renamed'
        self.assert_resolver_parity(resolver)
        resolver.face_assignments[(1, 0)] = OpticalAssignment('later', 'faces', 1, 'default', [0])
        self.assert_resolver_parity(resolver)
        resolver.profiles.pop('default')
        self.assert_resolver_parity(resolver)
        resolver.profiles.pop('mirror')
        self.assert_resolver_parity(resolver)

    def test_in_place_metadata_and_equal_dictionary_replacements_invalidate_routes(self):
        resolver = self.resolver()
        self.assert_resolver_parity(resolver)
        cached = resolver.mesh._optical_routing_cache
        resolver.mesh.face_metadata[1] = dict(resolver.mesh.face_metadata[1])
        self.assert_resolver_parity(resolver)
        self.assertIsNot(resolver.mesh._optical_routing_cache, cached)
        resolver.mesh.face_metadata[1]['source_face_index'] = 17
        resolver.mesh.face_metadata[1]['component_id'] = 2
        self.assert_resolver_parity(resolver)
        resolver.mesh.face_metadata[1].pop('component_id')
        self.assert_resolver_parity(resolver)

    def test_equal_custom_scalar_cannot_hide_a_changed_integer_conversion(self):
        class EqualButDifferentInteger:
            def __eq__(self, other):
                return True

            def __int__(self):
                return 91

        resolver = self.resolver()
        self.assert_resolver_parity(resolver)
        resolver.mesh.face_metadata[1]['source_face_index'] = EqualButDifferentInteger()
        self.assert_resolver_parity(resolver)
        self.assertIsNone(resolver.mesh._optical_routing_cache)

    def test_invalid_metadata_after_cache_hit_still_raises(self):
        resolver = self.resolver()
        self.assert_resolver_parity(resolver)
        resolver.mesh.face_metadata[2]['source_face_index'] = 'invalid-source'
        with patch.dict(os.environ, {'LEAKAGE_OPTICAL_ROUTING_CACHE': 'force'}), self.assertRaises(ValueError):
            resolver.resolve_all()

    def test_material_default_growth_and_short_metadata_are_not_stale(self):
        resolver = self.resolver()
        self.assert_resolver_parity(resolver)
        resolver.mesh.face_material[2] = 'mirror'
        resolver.mesh.default_material_id = 'black'
        self.assert_resolver_parity(resolver)
        resolver.mesh.face_metadata = resolver.mesh.face_metadata[:10]
        self.assert_resolver_parity(resolver)
        resolver.mesh.add_face(0, 1, 2, 'mirror', {'component_id': 1, 'source_face_index': 0})
        self.assert_resolver_parity(resolver)

    def test_returned_slot_mutations_cannot_poison_future_routes(self):
        resolver = self.resolver()
        actual = self.assert_resolver_parity(resolver)
        actual.palette_slots.setflags(write=True)
        actual.palette_slots[:] = 0
        self.assert_resolver_parity(resolver)
        actual = self.assert_resolver_parity(resolver)
        actual.palette_slots.setflags(write=True)
        actual.palette_slots[:] = 0
        self.assert_resolver_parity(resolver)

    def test_method_overrides_and_nonstandard_scalars_use_uncached_resolution(self):
        resolver = self.resolver()
        resolver.mesh.metadata = lambda index: {'component_id': 1, 'source_face_index': 0}
        self.assert_resolver_parity(resolver)
        self.assertFalse(hasattr(resolver.mesh, '_optical_routing_cache'))
        resolver = self.resolver()
        resolver.mesh.face_metadata[1]['component_id'] = np.int64(1)
        self.assert_resolver_parity(resolver)
        self.assertIsNone(resolver.mesh._optical_routing_cache)

    def test_shared_cad_metadata_keeps_one_snapshot_per_authored_face(self):
        resolver = self.resolver(600)
        shared = {'component_id': 1, 'source_face_index': 0}
        resolver.mesh.face_metadata = [shared] * 600
        self.assert_resolver_parity(resolver)
        cached = resolver.mesh._optical_routing_cache
        self.assertEqual(len(cached.metadata_refs), 1)
        self.assertEqual(len(cached.metadata_ids), 600)
        shared['source_face_index'] = 17
        self.assert_resolver_parity(resolver)


class OpticalRoutingTransportTests(unittest.TestCase):
    def test_cpu_roi_reediting_and_cad_paths_preserve_all_public_results(self):
        scene, payload = clipped_override_case(8193)
        trace_input = build_direct_trace_input(scene, payload)
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 50
        for reflectance in (0.03, 0.6, 0.99):
            trace_input.optical_profiles[1].reflectance = reflectance
            results = []
            for mode in ('off', 'force', 'force'):
                with patch.dict(os.environ, {'LEAKAGE_OPTICAL_ROUTING_CACHE': mode, 'LEAKAGE_OPTICAL_TABLES': 'on'}):
                    results.append(run_direct_ray_trace(trace_input))
            for actual in results[1:]:
                parity = compare_semantic_payloads(semantic_payload(results[0]), semantic_payload(actual))
                self.assertTrue(parity.semantic_exact, parity.to_dict())

    def test_gpu_roi_reediting_and_cad_paths_preserve_all_public_results(self):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')
        cases = [cad_case(8193)[0]]
        scene, payload = clipped_override_case(8193)
        cases.append(build_direct_trace_input(scene, payload))
        for trace_input in cases:
            trace_input.config.compute_backend = 'gpu_cuda'
            trace_input.config.store_ray_paths = True
            trace_input.config.max_stored_paths = 50
            for reflectance in (0.03, 0.6, 0.99):
                trace_input.optical_profiles[-1].reflectance = reflectance
                results = []
                for mode in ('off', 'force', 'force'):
                    with patch.dict(os.environ, {'LEAKAGE_OPTICAL_ROUTING_CACHE': mode, 'LEAKAGE_OPTICAL_TABLES': 'on'}):
                        results.append(run_direct_ray_trace(trace_input, gpu_accumulator='host'))
                for actual in results[1:]:
                    parity = compare_semantic_payloads(semantic_payload(results[0]), semantic_payload(actual))
                    self.assertTrue(parity.semantic_exact, parity.to_dict())
                    performance = actual.metrics['_performance_summary']
                    self.assertEqual(performance['compute_execution_state'], 'gpu_active')
                    self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
                    self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)


if __name__ == '__main__':
    unittest.main()
