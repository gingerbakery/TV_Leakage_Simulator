from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from perf4_accuracy import _ordered_float64_bits, _ulp_distance, compare_semantic_payloads


class Float64ParityTests(unittest.TestCase):
    def test_signed_zero_has_zero_distance(self):
        self.assertEqual(_ulp_distance(-0.0, 0.0), 0)
        self.assertTrue(compare_semantic_payloads(-0.0, 0.0).semantic_exact)

    def test_subnormal_neighbors_across_zero(self):
        smallest = math.ulp(0.0)
        for zero in (-0.0, 0.0):
            self.assertEqual(_ulp_distance(-smallest, zero), 1)
            self.assertEqual(_ulp_distance(zero, smallest), 1)
        self.assertEqual(_ulp_distance(-smallest, smallest), 2)

    def test_finite_order_is_monotonic(self):
        largest = sys.float_info.max
        values = [-largest, -2.0, -1.0, -sys.float_info.min, -math.ulp(0.0),
                  0.0, math.ulp(0.0), sys.float_info.min, 1.0, 2.0, largest]
        keys = [_ordered_float64_bits(value) for value in values]
        self.assertEqual(keys, sorted(set(keys)))

    def test_nextafter_is_one_ulp_on_both_sides(self):
        for value in (-sys.float_info.max, -2.0, -1.0, -sys.float_info.min,
                      -math.ulp(0.0), 0.0, math.ulp(0.0), sys.float_info.min, 1.0, 2.0):
            neighbor = math.nextafter(value, math.inf)
            with self.subTest(value=value):
                self.assertEqual(_ulp_distance(value, neighbor), 1)
                self.assertEqual(_ulp_distance(neighbor, value), 1)

    def test_cross_zero_comparison_obeys_ulp_limit(self):
        smallest = math.ulp(0.0)
        passed = compare_semantic_payloads(-smallest, smallest, max_ulp_distance=2)
        failed = compare_semantic_payloads(-smallest, smallest, max_ulp_distance=1)
        self.assertTrue(passed.passed)
        self.assertEqual(passed.max_ulp_distance, 2)
        self.assertFalse(failed.passed)

    def test_absolute_or_relative_tolerance_still_requires_ulp_gate(self):
        smallest = math.ulp(0.0)
        self.assertTrue(compare_semantic_payloads(0.0, smallest,
            absolute_tolerance=smallest, relative_tolerance=0.0, max_ulp_distance=1).passed)
        neighbor = math.nextafter(1.0, math.inf)
        self.assertTrue(compare_semantic_payloads(1.0, neighbor,
            absolute_tolerance=0.0, relative_tolerance=1e-12, max_ulp_distance=1).passed)
        self.assertFalse(compare_semantic_payloads(1.0, neighbor,
            absolute_tolerance=0.0, relative_tolerance=0.0, max_ulp_distance=1).passed)
        self.assertFalse(compare_semantic_payloads(1.0, neighbor,
            absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=0).passed)

    def test_nonfinite_mismatches_fail(self):
        for reference, candidate in ((math.nan, math.nan), (math.inf, -math.inf),
                                     (math.inf, 1.0), (-math.inf, 0.0)):
            with self.subTest(reference=reference, candidate=candidate):
                self.assertFalse(compare_semantic_payloads(reference, candidate).passed)

    def test_discrete_types_counts_and_missing_fields_are_not_tolerated(self):
        reference = {'hits': 1, 'flux': 1.0, 'enabled': True}
        for candidate in ({**reference, 'hits': 2}, {**reference, 'hits': 1.0},
                          {**reference, 'enabled': 1}, {'hits': 1, 'flux': 1.0}):
            self.assertFalse(compare_semantic_payloads(reference, candidate).discrete_exact)


if __name__ == '__main__':
    unittest.main()
