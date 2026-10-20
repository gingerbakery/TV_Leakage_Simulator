from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from perf5d_evidence import (APPROVED_REFERENCE_HASHES, PAIR_CONTRACT, POSITIVE_COUNTERS, ZERO_COUNTERS,
    file_sha256, identity_gate, pair_execution_gate, reference_hashes, reference_identity_gate,
    source_hashes, transport_acceptance_gate, transport_accuracy_gates, write_json_artifact)


def valid_performance():
    return {
        'compute_execution_state': 'gpu_active', 'compute_execution_reason': None,
        'gpu_pair_bvh_contract': PAIR_CONTRACT, 'gpu_cuda_contract': 'strict_float64_bvh_v1',
        'gpu_resident_wavefront_contract': 'strict_float64_resident_wavefront_v1',
        'gpu_cuda_strict_float64': True, 'gpu_resident_wavefront_strict_float64': True,
        **dict.fromkeys(POSITIVE_COUNTERS, 16), **dict.fromkeys(ZERO_COUNTERS, 0),
    }


class PairExecutionGateTests(unittest.TestCase):
    def test_full_candidate_execution_passes(self):
        self.assertTrue(pair_execution_gate(valid_performance())['passed'])

    def test_generic_cuda_success_without_candidate_fails(self):
        performance = valid_performance()
        performance['gpu_pair_bvh_selected_batches'] = 0
        performance['gpu_pair_bvh_contract'] = None
        self.assertFalse(pair_execution_gate(performance)['passed'])

    def test_every_missing_required_field_fails(self):
        performance = valid_performance()
        for name in performance:
            with self.subTest(name=name):
                self.assertFalse(pair_execution_gate({key: value for key, value in performance.items()
                    if key != name})['passed'])

    def test_mixed_fallback_zero_and_inconsistent_counters_fail(self):
        changes = [('compute_execution_state', 'gpu_mixed'), ('compute_execution_reason', 'fallback'),
            ('gpu_pair_bvh_selected_batches', 15), ('gpu_cuda_gpu_attempt_count', 17),
            ('gpu_resident_wavefront_attempt_count', 17), ('gpu_pair_bvh_contract', 'unknown')]
        changes.extend((name, 0) for name in POSITIVE_COUNTERS)
        changes.extend((name, 1) for name in ZERO_COUNTERS)
        for name, value in changes:
            with self.subTest(name=name, value=value):
                self.assertFalse(pair_execution_gate({**valid_performance(), name: value})['passed'])

    def test_null_boolean_and_noninteger_counts_fail_closed(self):
        for name in (*POSITIVE_COUNTERS, *ZERO_COUNTERS):
            for value in (None, True, False, 0.0, '16', -1):
                with self.subTest(name=name, value=value):
                    self.assertFalse(pair_execution_gate({**valid_performance(), name: value})['passed'])


class EvidenceIdentityTests(unittest.TestCase):
    def test_only_approved_baseline_hashes_are_accepted(self):
        self.assertTrue(reference_identity_gate(APPROVED_REFERENCE_HASHES)['passed'])
        for name in APPROVED_REFERENCE_HASHES:
            self.assertFalse(reference_identity_gate({**APPROVED_REFERENCE_HASHES, name: 'candidate'})['passed'])
            self.assertFalse(reference_identity_gate({key: value for key, value in APPROVED_REFERENCE_HASHES.items()
                if key != name})['passed'])

    def test_hashes_cover_api_comparator_tests_and_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ('src/leakage_simulator/api/runtime.py', 'scripts/perf4_accuracy.py',
                'scripts/benchmark_perf5d_bvh_preparation.py', 'tests/test_perf5d_evidence.py',
                'requirements-gpu-cuda.txt')
            for name in paths:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name, encoding='utf-8')
            self.assertEqual(set(source_hashes(root)), set(paths))
            before = source_hashes(root)
            (root / paths[0]).write_text('changed', encoding='utf-8')
            self.assertEqual(identity_gate(before, source_hashes(root))['changed'], [paths[0]])

    def test_addition_removal_and_empty_identity_fail_closed(self):
        self.assertTrue(identity_gate({'file': 'one'}, {'file': 'one'})['passed'])
        for before, after in (({}, {}), ({'file': 'one'}, {}),
                              ({'file': 'one'}, {'file': 'one', 'added': 'two'})):
            self.assertFalse(identity_gate(before, after)['passed'])

    def test_reference_sources_are_required_and_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                reference_hashes(root)
            for name in ('gpu_cuda_pair_scene.py', 'gpu_cuda_pair_sah.py'):
                (root / name).write_text('reference', encoding='utf-8')
            before = reference_hashes(root)
            (root / 'gpu_cuda_pair_scene.py').write_text('modified', encoding='utf-8')
            self.assertFalse(identity_gate(before, reference_hashes(root))['passed'])

    def test_artifact_roundtrips_float_values_and_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'payload.json'
            payload = {'flux': [5e-324, -5e-324, 1.0000000000000002], 'hits': 219}
            artifact = write_json_artifact(path, payload)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), payload)
            self.assertEqual(artifact['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(file_sha256(path), artifact['sha256'])
            with self.assertRaises(FileExistsError):
                write_json_artifact(path, payload)


class TransportRepeatabilityTests(unittest.TestCase):
    def payload(self, receiver=1.0, auxiliary=0.1):
        return {'receiver_grids': {'flux': receiver}, 'metrics': {},
            'contribution_summary': {'flux': auxiliary}, 'receiver_hits': 219}

    def test_receiver_repeat_failure_cannot_pass_against_shared_reference(self):
        reference = self.payload()
        first = self.payload(receiver=1.0 + 20 * math.ulp(1.0))
        candidate = self.payload(receiver=1.0 - 10 * math.ulp(1.0))
        accuracy = transport_accuracy_gates(reference, first, candidate)
        self.assertTrue(accuracy['receiver_parity']['passed'])
        self.assertEqual(accuracy['receiver_repeatability']['max_ulp_distance'], 40)
        self.assertFalse(transport_acceptance_gate({**accuracy, 'execution_gate': {'passed': True}})['passed'])

    def test_physical_repeat_failure_cannot_pass_against_shared_reference(self):
        reference = self.payload()
        first = self.payload(auxiliary=0.1 + 9e-10)
        candidate = self.payload(auxiliary=0.1 - 9e-10)
        accuracy = transport_accuracy_gates(reference, first, candidate)
        self.assertTrue(accuracy['parity']['passed'])
        self.assertTrue(accuracy['receiver_repeatability']['passed'])
        self.assertFalse(accuracy['physical_repeatability']['passed'])
        self.assertFalse(transport_acceptance_gate({**accuracy, 'execution_gate': {'passed': True}})['passed'])

    def test_every_required_gate_must_exist_and_pass(self):
        accuracy = transport_accuracy_gates(self.payload(), self.payload(), self.payload())
        run = {**accuracy, 'execution_gate': {'passed': True}}
        self.assertTrue(transport_acceptance_gate(run)['passed'])
        for name in transport_acceptance_gate(run)['checks']:
            self.assertFalse(transport_acceptance_gate({key: value for key, value in run.items()
                if key != name})['passed'])
            self.assertFalse(transport_acceptance_gate({**run, name: {'passed': False}})['passed'])

    def test_strict_auxiliary_failure_remains_separately_visible(self):
        accuracy = transport_accuracy_gates(self.payload(), self.payload(), self.payload(auxiliary=0.1 + 1e-11))
        self.assertFalse(accuracy['strict_all_fields']['passed'])
        self.assertFalse(accuracy['repeatability']['passed'])
        self.assertTrue(transport_acceptance_gate({**accuracy, 'execution_gate': {'passed': True}})['passed'])


if __name__ == '__main__':
    unittest.main()
