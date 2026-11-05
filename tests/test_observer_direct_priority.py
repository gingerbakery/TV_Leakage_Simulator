"""Mixed direct/reflected diagnostics preserve flux and independent occlusion."""
from types import SimpleNamespace
from unittest import TestCase

import numpy as np
from leakage_simulator.observer_radiance import trace_observer_radiance
from leakage_simulator.observer_probe import _source_geometry, sampling_plan
from leakage_simulator.gpu_cuda_observer import reference_chunk, pack_scene, compact_cell_batch
from test_gpu_cuda_observer import fixture
from test_observer_radiance import source


class ObserverDirectPriorityTests(TestCase):
    def test_faint_direct_survives_stronger_mirror_component_without_changing_total(self):
        trace, _, _, resolved = fixture(True)
        trace.emitters.append(source((0, 0, -4), 40, 1., reverse=False))
        collector = SimpleNamespace(minimum=(-1,-1,-10), maximum=(1,1,10), direction="neg_z", regions=[],
            _allowed=lambda *args: False)
        summary = trace_observer_radiance(trace, collector, resolved)
        self.assertEqual(summary["path_classification_contract"], "direct_presence_priority_v1")
        self.assertEqual(summary["direct_count"], summary["planned_count"])
        self.assertEqual(summary["reflected_count"], 0)
        for cell in summary["cells"]:
            self.assertAlmostEqual(cell["luminance_nit"], 91., delta=1e-9)
            self.assertAlmostEqual(cell["direct_luminance_nit"], 1., delta=1e-9)
            self.assertAlmostEqual(cell["reflected_luminance_nit"], 90., delta=1e-9)
            self.assertEqual(len(cell["representative_path"]), 2)
            self.assertAlmostEqual(cell["representative_path"][0][2], -4.)
        plan = sampling_plan(trace.observer_probe, collector.minimum, collector.maximum, "neg_z", 0)[0]
        values, paths = reference_chunk(trace, pack_scene(trace.mesh, _source_geometry(trace.mesh, trace.emitters), resolved), plan, (0,0,0), 0, 0, 32)
        np.testing.assert_allclose(values[:,0], 91., atol=1e-9, rtol=0.)
        np.testing.assert_array_equal(values[:,2], 0)
        np.testing.assert_array_equal(values[:,3], 2)
        np.testing.assert_allclose(values[:,7], 1., atol=1e-9, rtol=0.)
        np.testing.assert_allclose(paths[:,0,2], -4.)

    def test_compact_display_prefers_direct_path_across_samples_without_discarding_flux(self):
        trace, _, _, _ = fixture()
        trace.observer_probe["samples_per_cell"] = 4
        values = np.array([[100,100,2,4,0,3,0],[1,1,0,2,0,1,0],[200,200,1,3,0,2,0],[0,0,0,0,0,1,0]], dtype=float)
        paths = {0: [(0,0,0)]*4, 1: [(1,0,-4),(1,0,-10)], 2: [(2,0,0)]*3}
        result = compact_cell_batch(trace, values, 0, 0, 1, paths)[0]
        self.assertEqual(result["representative_path"], paths[1])
        self.assertEqual(result["sum"], 301.)
        self.assertEqual((result["direct_count"], result["reflected_count"]), (1,2))
