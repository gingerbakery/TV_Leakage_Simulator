from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

import test_perf5_uniform_emissions as baseline
from perf5_uniform_warp_metadata import kernel_variant, make_factory


class WarpMetadataIntegrationTests(baseline.UniformEmissionIntegrationTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.factory = staticmethod(make_factory())
        cls.caches = {}

    def setUp(self):
        super().setUp()
        self.variant = kernel_variant('warp', type(self).factory, type(self).caches)
        self.variant.__enter__()
        self.addCleanup(self.variant.__exit__, None, None, None)


class WarpMetadataCountIntegrationTests(WarpMetadataIntegrationTests):
    @classmethod
    def setUpClass(cls):
        baseline.UniformEmissionIntegrationTests.setUpClass()
        cls.factory = staticmethod(make_factory('count'))
        cls.caches = {}


if __name__ == '__main__':
    unittest.main()
