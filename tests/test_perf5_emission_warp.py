from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

import test_perf5_uniform_emissions as baseline
from perf5_emission_warp import make_factory
from leakage_simulator import gpu_cuda_emitted_flux as flux
from leakage_simulator import gpu_cuda_intersection as gpu


class WarpEmissionIntegrationTests(baseline.UniformEmissionIntegrationTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kernel = make_factory()(gpu._CUDA)

    def setUp(self):
        super().setUp()
        self.kernel_patch = patch.object(flux, '_KERNEL', type(self).kernel)
        self.kernel_patch.start()
        self.addCleanup(self.kernel_patch.stop)


if __name__ == '__main__':
    unittest.main()
