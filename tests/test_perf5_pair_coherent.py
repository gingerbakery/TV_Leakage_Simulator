from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

import test_perf5_gpu_pair_production as baseline
from perf5_pair_coherent import make_factory
from leakage_simulator import gpu_cuda_resident_wavefront as resident


class PairCoherentGpuTests(baseline.ProductionPairGpuTests):
    bucket_count = 8
    spatial = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        factory, cls.fingerprint = make_factory(cls.bucket_count, spatial=cls.spatial)
        cls.kernel = factory()
        cls.compiled = False

    def setUp(self):
        self.enterContext(patch.object(resident, '_PAIR_KERNEL', type(self).kernel))
        self.enterContext(patch.object(resident, '_PAIR_KERNEL_COMPILED', type(self).compiled))

    def tearDown(self):
        type(self).compiled = resident._PAIR_KERNEL_COMPILED


class PairSpatial4GpuTests(PairCoherentGpuTests):
    bucket_count = 4
    spatial = True


class PairSpatial8GpuTests(PairCoherentGpuTests):
    bucket_count = 8
    spatial = True


if __name__ == '__main__':
    unittest.main()
