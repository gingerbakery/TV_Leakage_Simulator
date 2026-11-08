from unittest import TestCase
from unittest.mock import patch

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.observer_exit_estimate import estimate_observer_exits


def rectangle(mesh, x0, x1, y0=-4, y1=4, z=0., curve=0.):
    points = [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
    ids = [mesh.add_vertex((x,y,z-curve*(x*x+y*y))) for x,y in points]
    mesh.add_face(ids[0],ids[1],ids[2],"cover")
    mesh.add_face(ids[0],ids[2],ids[3],"cover")


def evidence():
    return dict(view_index=0, row=0, column=0, path_validation=dict(status="valid"),
                representative_path=[(0,0,-5),(0,0,5)])


class ObserverExitEstimateTests(TestCase):
    def test_open_seam_between_parts_without_a_bounded_hole(self):
        mesh = TriangleMesh()
        rectangle(mesh,-4,-.5); rectangle(mesh,.5,4)
        cell = evidence()
        summary = dict(cells=[cell])
        estimate_observer_exits(mesh, summary)
        self.assertEqual(cell["aperture"]["contract"], "automatic_exit_estimate_v1")
        self.assertAlmostEqual(cell["aperture"]["position"][2], 0., places=6)
        self.assertFalse(cell["aperture"]["exterior_shell_certified"])
        self.assertGreaterEqual(cell["aperture"]["support_pairs"], 4)
        self.assertEqual(cell["representative_path"], [(0,0,-5),(0,0,5)])

    def test_curved_cover_without_a_coplanar_opening(self):
        mesh = TriangleMesh()
        step = .25
        for x in range(-16,16):
            for y in range(-16,16):
                if -2 <= x < 2 and -2 <= y < 2:
                    continue
                rectangle(mesh,x*step,(x+1)*step,y*step,(y+1)*step,curve=.02)
        cell = evidence()
        summary = dict(cells=[cell])
        estimate_observer_exits(mesh, summary)
        self.assertIn("aperture", cell)
        self.assertLess(abs(cell["aperture"]["position"][2]), .1)
        self.assertEqual(cell["aperture"]["method"], "opposed_neighborhood_multiscale")

    def test_one_sided_geometry_and_invalid_paths_stay_unresolved(self):
        mesh = TriangleMesh()
        rectangle(mesh,.5,4)
        cell = evidence()
        summary = dict(cells=[cell])
        estimate_observer_exits(mesh, summary)
        self.assertNotIn("aperture", cell)
        rectangle(mesh,-4,-.5)
        cell["path_validation"]["status"] = "invalid"
        estimate_observer_exits(mesh, summary)
        self.assertNotIn("aperture", cell)

    def test_partial_outer_support_vetoes_a_deeper_internal_opening(self):
        from test_observer_aperture import ring
        mesh = TriangleMesh()
        ring(mesh,-2)
        rectangle(mesh,-4,-1.5); rectangle(mesh,1.5,4)
        cell = evidence()
        summary = dict(cells=[cell])
        estimate_observer_exits(mesh, summary)
        # Either the true outer seam is supported, or uncertainty is retained;
        # the internal ring must never be assigned as the exterior estimate.
        if "aperture" in cell:
            self.assertGreater(cell["aperture"]["position"][2], -.1)

    def test_illuminated_exterior_sheet_is_not_an_exit(self):
        mesh = TriangleMesh()
        rectangle(mesh,-4,4)
        cell = evidence()
        cell["representative_path"] = [(0,0,-2),(0,0,0),(0,0,5)]
        summary = dict(cells=[cell])
        estimate_observer_exits(mesh, summary)
        self.assertNotIn("aperture", cell)

    def test_resource_budget_removes_old_planar_marker_without_losing_path(self):
        mesh = TriangleMesh()
        rectangle(mesh,-4,-.5); rectangle(mesh,.5,4)
        cell = evidence()
        cell["aperture"] = dict(id="old-internal-ring", position=[0,0,-2])
        summary = dict(cells=[cell])
        estimate_observer_exits(mesh, summary, max_queries=0)
        self.assertNotIn("aperture", cell)
        self.assertEqual(len(cell["representative_path"]), 2)
        self.assertEqual(summary["aperture_summary"]["reason"], "exit_estimation_budget_exceeded")

    def test_cuda_failure_never_falls_back_to_cpu_or_claims_success(self):
        mesh = TriangleMesh()
        rectangle(mesh,-4,-.5); rectangle(mesh,.5,4)
        summary = dict(cells=[evidence()])
        with patch.object(mesh,"intersect_rays_gpu_cuda",side_effect=RuntimeError("device unavailable")), \
             patch.object(mesh,"intersect_rays_native_cpu") as cpu:
            estimate_observer_exits(mesh, summary, compute_backend="gpu_cuda")
        cpu.assert_not_called()
        self.assertNotIn("aperture", summary["cells"][0])
        self.assertEqual(summary["aperture_summary"]["gpu_successful_batches"], 0)
        self.assertEqual(summary["aperture_summary"]["reason"], "exit_estimation_cuda_failed")
