from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'src')]

import benchmark_perf5d_bvh_preparation as benchmark
from leakage_simulator.geometry import TriangleMesh


class BenchmarkEvidenceTests(unittest.TestCase):
    def test_actual_mesh_metadata_preserves_trace_exclusions(self):
        @dataclass
        class Config:
            ray_count: int = 1

        mesh = TriangleMesh()
        vertices = [mesh.add_vertex(vertex) for vertex in ((0, 0, 0), (1, 0, 0), (0, 1, 0))]
        mesh.add_face(*vertices, 'black', {'trace_excluded': True, 'component_id': 'part'})
        trace_input = SimpleNamespace(project_name='fixture', config=Config(), mesh=mesh,
            emitters=[], receivers=[], optical_profiles=[], optical_assignments=[])
        payload = benchmark.trace_input_payload(trace_input)
        restored = json.loads(json.dumps(payload, allow_nan=False))
        self.assertTrue(restored['mesh']['face_metadata'][0]['trace_excluded'])
        self.assertEqual(restored['mesh']['face_metadata'][0]['component_id'], 'part')
        self.assertEqual(len(restored['mesh']['faces']), 1)

    def test_mismatched_warm_tree_is_preserved_before_failure(self):
        scene = SimpleNamespace(**{name: np.asarray([1.0, 2.0]) for name in benchmark.ARRAYS}, stack_width=4)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            first = benchmark.capture_tree_build(output, 'after', 0, scene, None, 0.1)
            scene.node_bounds_min[0] = -2.0
            mismatch = benchmark.capture_tree_build(output, 'after', 1, scene, first['tree_sha256'], 0.1)
            self.assertFalse(mismatch['exact'])
            self.assertEqual(mismatch['expected_sha256'], first['tree_sha256'])
            with np.load(mismatch['tree_artifact']['path'], allow_pickle=False) as saved:
                np.testing.assert_array_equal(saved['node_bounds_min'], scene.node_bounds_min)
            journal = json.loads((output / 'after-build-1.evidence.json').read_text(encoding='utf-8'))
            self.assertEqual(journal, mismatch)

    def setup_command(self, root):
        samples = root / 'samples'
        samples.mkdir()
        (samples / 'tv_leakage_roi_right_bottom_no_gap.stp').write_text('fixture', encoding='utf-8')
        reference = root / 'reference'
        reference.mkdir()
        for name in ('gpu_cuda_pair_scene.py', 'gpu_cuda_pair_sah.py'):
            (reference / name).write_text('not approved', encoding='utf-8')
        scripts = root / 'scripts'
        scripts.mkdir()
        (scripts / 'identity.py').write_text('identity', encoding='utf-8')
        output = root / 'result.json'
        command = ['benchmark', '--reference-dir', str(reference), '--output', str(output)]
        return command, output

    def test_wrong_reference_is_rejected_before_benchmark_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command, output = self.setup_command(root)
            with patch.object(benchmark, 'ROOT', root), patch.object(sys, 'argv', command), \
                    patch.object(benchmark, 'version', return_value='test'), \
                    patch.object(benchmark, 'run_benchmark') as run:
                self.assertEqual(benchmark.main(), 1)
            run.assert_not_called()
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertFalse(report['reference_authentication']['passed'])
            self.assertFalse(report['transport_verified'])
            self.assertTrue(report['identity_gates']['reference']['passed'])

    def test_later_exception_preserves_completed_records_and_stage(self):
        def fail_after_run(args, artifacts, progress):
            progress['records'] = {'before': {'runs': [{'performance': {'gpu_pair_bvh_selected_batches': 16},
                'execution_gate': {'passed': True}, 'payload_artifact': {'path': 'retained.json'}}]}}
            progress['stage'] = {'kind': 'transport', 'mode': 'after', 'repeat': 1}
            raise RuntimeError('injected transport failure')

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command, output = self.setup_command(root)
            with patch.object(benchmark, 'ROOT', root), patch.object(sys, 'argv', command), \
                    patch.object(benchmark, 'version', return_value='test'), \
                    patch.object(benchmark, 'reference_identity_gate', return_value={'passed': True}), \
                    patch.object(benchmark, 'run_benchmark', side_effect=fail_after_run):
                self.assertEqual(benchmark.main(), 1)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertFalse(report['passed'])
            self.assertFalse(report['transport_verified'])
            self.assertIn('injected transport failure', report['traceback'])
            self.assertEqual(report['partial_evidence']['stage']['repeat'], 1)
            run = report['partial_evidence']['records']['before']['runs'][0]
            self.assertEqual(run['performance']['gpu_pair_bvh_selected_batches'], 16)
            self.assertTrue(run['execution_gate']['passed'])


if __name__ == '__main__':
    unittest.main()
