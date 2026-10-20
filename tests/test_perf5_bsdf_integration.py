from __future__ import annotations

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf4b_resident_wavefront import _semantic_payload
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import native_cpu_transport
from leakage_simulator.api import ApiRuntime
from leakage_simulator.bsdf_guard import bind_bsdf_profiles
from leakage_simulator.native_cpu_counter_wavefront import plan_counter_native_cpu, plan_counter_reference
from leakage_simulator.native_cpu_wavefront import SCATTER_BSDF, SCATTER_NONE, SCATTER_SPECULAR, scatter_codes_from_names
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.raytracer import run_direct_ray_trace
from perf5d_evidence import transport_accuracy_gates
import test_bsdf_packed as packed_fixture
from test_bsdf_transport import planar_trace_input, synthetic_asset
from test_bitsam_package import project_fixture, scene_fixture, trace_request
from test_perf5_gpu_pair_production import add_distant_faces


class BsdfAccelerationIntegrationTests(unittest.TestCase):
    def test_palette_cache_preserves_bsdf_and_analytic_reedits(self):
        trace = add_distant_faces(planar_trace_input(synthetic_asset()), count=300)
        bound, _ = bind_bsdf_profiles(trace.optical_profiles, trace.bsdf_assets, trace.config)
        for _ in range(2):
            resolved = OpticalPropertyResolver(trace.mesh, bound, []).resolve_all()
            np.testing.assert_array_equal(resolved.numeric_tables(scatter_codes_from_names)[2], SCATTER_BSDF)
        analytic = [replace(bound[0], bsdf_asset_id='')]
        resolved = OpticalPropertyResolver(trace.mesh, analytic, []).resolve_all()
        np.testing.assert_array_equal(resolved.numeric_tables(scatter_codes_from_names)[2], SCATTER_NONE)

    def test_parallel_counter_keeps_bsdf_indices_aligned_with_ray_slices(self):
        batch = packed_fixture.PackedBsdfTests().batch(synthetic_asset(lobe=True), n=20000)
        identifiers = batch.bsdf_indices.copy()
        scatter = batch.scatter_models.copy()
        identifiers[5000:15000] = -1
        scatter[5000:15000] = SCATTER_SPECULAR
        batch = replace(batch, bsdf_indices=identifiers, scatter_models=scatter,
            profile_reflectance=np.full(len(batch), 0.6))
        expected = plan_counter_reference(batch)
        with patch.dict(os.environ, {'LEAKAGE_CPU_WORKERS': '2'}):
            execution = plan_counter_native_cpu(batch)
        self.assertEqual(execution.parallel_workers, 2)
        for name in ('lobe_codes', 'status_flags', 'rng_draw_counts', 'emitted_power_lumen'):
            np.testing.assert_array_equal(getattr(execution.result, name), getattr(expected, name))
        np.testing.assert_allclose(execution.result.emitted_directions, expected.emitted_directions,
            atol=1e-13, rtol=1e-13)

    def test_bsdf_uses_table_aware_cpu_path_not_analytic_fused_transport(self):
        trace = planar_trace_input(synthetic_asset(), rays=8193)
        trace.config.contribution_mode = 'summary'
        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'fused'}), patch.object(
            native_cpu_transport, 'trace_native_cpu_transport',
            side_effect=AssertionError('Analytic-only fused transport must not receive BSDF'),
        ) as analytic_transport:
            result = run_direct_ray_trace(trace)
        analytic_transport.assert_not_called()
        self.assertGreater(result.receiver_hit_count, 0)
        self.assertAlmostEqual(result.metrics['_reflection_summary']['lobes']['bsdf']['emitted_flux_lumen'], 0.3)
        performance = result.metrics['_performance_summary']
        for name in ('wavefront_cpu_transport_fallback_count', 'wavefront_planner_fallback_count',
            'wavefront_reducer_fallback_count'):
            self.assertEqual(performance.get(name, 0), 0, performance)

    def test_portable_bsdf_roundtrip_preserves_result_and_allows_reanalysis(self):
        self.assert_portable_bsdf_roundtrip('cpu')

    def assert_portable_bsdf_roundtrip(self, backend):
        asset = synthetic_asset()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'mirror.step'
            source.write_bytes(b'ISO-10303-21; synthetic portable regression')
            runtime = ApiRuntime(root / 'save', scene_loader=scene_fixture)
            scene = runtime.load_scene(str(source))
            request = trace_request(scene['metadata']['scene_token'])
            request['config']['compute_backend'] = backend
            if backend == 'gpu_cuda':
                request['config']['ray_count'] = 8193
                request['emitters'][0]['ray_count'] = 8193
            request['optical_profiles'][0].update(reflectance=0, scatter_model='none', bsdf_asset_id=asset['id'])
            request['bsdf_assets'] = [asset]
            result = runtime.run_raytrace_direct(request)
            self.assertGreater(result['receiver_hit_count'], 0)
            project = project_fixture()
            project['schema_version'] = 'bitsam-project.v1-bsdf1'
            project['workspace'].update(bsdfAssets=[asset], emitters=result['emitters'],
                receivers=result['receivers'], rayTraceConfig=request['config'])
            project['workspace']['materialAssignments'][0]['bsdfAssetId'] = asset['id']
            project['analysis_result'] = result
            saved = runtime.export_project({'scene_token': request['scene_token'], 'project': project})
            package, _ = runtime.project_export_file(saved['download_url'].split('/')[-1])
            loader = Mock(side_effect=AssertionError('Embedded CAD must be reused'))
            restored = ApiRuntime(root / 'load', scene_loader=loader)
            imported = restored.import_project(package)
            self.assertEqual(imported['project']['analysis_result'], json.loads(json.dumps(result)))
            self.assertEqual(imported['project']['workspace']['bsdfAssets'], [asset])
            loaded_scene = restored.load_scene(imported['cad']['path'])
            request['scene_token'] = loaded_scene['metadata']['scene_token']
            repeated = restored.run_raytrace_direct(request)
            self.assertEqual(repeated['receiver_grids'], result['receiver_grids'])
            request['receivers'][0]['width_mm'] = 10
            changed = restored.run_raytrace_direct(request)
            self.assertNotEqual(changed['run_id'], result['run_id'])
            self.assertEqual(changed['receivers'][0]['width_mm'], 10)
            self.assertEqual(changed['metrics']['_optical_summary']['profile_hits']['mirror']['scatter_model'], 'bsdf')
            if backend == 'gpu_cuda':
                for traced in (result, repeated, changed):
                    performance = traced['metrics']['_performance_summary']
                    self.assertEqual(performance['compute_execution_state'], 'gpu_active', performance)
                    self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
                    self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
            loader.assert_not_called()


class BsdfPairGpuIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capability = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(capability, name) is True for name in
            ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise RuntimeError(f'Production CUDA required: {asdict(capability)}')
        if capability.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def test_portable_gpu_result_and_reanalysis_survive_restore(self):
        BsdfAccelerationIntegrationTests.assert_portable_bsdf_roundtrip(self, 'gpu_cuda')

    def test_pair_bvh_summary_retrace_and_repeat_preserve_bsdf_results(self):
        trace = add_distant_faces(planar_trace_input(synthetic_asset(lobe=True), rays=8193), count=4096)
        trace.config.contribution_mode = 'summary'
        trace.config.max_stored_paths = 12
        reference = _semantic_payload(run_direct_ray_trace(trace))
        trace.config.compute_backend = 'gpu_cuda'
        first = None
        for mode in ('off', 'auto', 'auto'):
            with self.subTest(mode=mode), patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': mode}):
                result = run_direct_ray_trace(trace)
                payload = _semantic_payload(result)
                first = payload if first is None else first
                gates = transport_accuracy_gates(reference, first, payload)
                for name in ('parity', 'physical_repeatability', 'receiver_parity', 'receiver_repeatability'):
                    self.assertTrue(gates[name]['passed'], (name, gates[name]))
                performance = result.metrics['_performance_summary']
                self.assertEqual(performance['compute_execution_state'], 'gpu_active', performance)
                self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
                self.assertGreater(performance['gpu_resident_wavefront_success_count'], 0)
                self.assertGreater(performance['gpu_summary_accumulator_success_count'], 0)
                for name in ('gpu_resident_wavefront_fallback_count', 'wavefront_planner_fallback_count',
                    'wavefront_reducer_fallback_count', 'gpu_cuda_hybrid_cpu_success_count'):
                    self.assertEqual(performance[name], 0, performance)
                if mode == 'auto':
                    self.assertGreater(performance['gpu_pair_bvh_selected_batches'], 0)
                self.assertGreater(result.receiver_hit_count, 0)
                self.assertTrue(result.stored_paths)
                self.assertAlmostEqual(result.metrics['_reflection_summary']['lobes']['bsdf']['emitted_flux_lumen'], 0.3)


if __name__ == '__main__':
    unittest.main()
