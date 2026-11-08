from unittest import TestCase
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.observer_aperture import attribute_observer_apertures


def ring(mesh, z, transform=lambda p: p):
    coordinates = [(-4,-4), (4,-4), (4,4), (-4,4), (-1,-1), (1,-1), (1,1), (-1,1)]
    ids = [mesh.add_vertex(transform((x,y,z))) for x,y in coordinates]
    for i in range(4):
        j = (i+1) % 4
        mesh.add_face(ids[i], ids[j], ids[j+4], "cover")
        mesh.add_face(ids[i], ids[j+4], ids[i+4], "cover")


def cell(start=(0,0,0), end=(0,0,-10)):
    return dict(path_validation=dict(status="valid"), representative_path=[(0,0,2), start, end])


class ObserverApertureTests(TestCase):
    def test_uses_last_opening_not_internal_reflection_or_envelope(self):
        mesh = TriangleMesh()
        ring(mesh, -3); ring(mesh, -7)
        evidence = cell()
        summary = dict(cells=[evidence])
        original = list(evidence["representative_path"])
        attribute_observer_apertures(mesh, summary)
        self.assertEqual(evidence["aperture"]["position"], [0,0,-7])
        self.assertFalse(evidence["aperture"]["exterior_shell_certified"])
        self.assertEqual(evidence["representative_path"], original)

    def test_arbitrary_orientation_and_no_infinite_plane_projection(self):
        mesh = TriangleMesh()
        ring(mesh, -3, lambda p: (p[2], p[0], p[1]))
        hit, outside, rim = cell((0,0,0),(-10,0,0)), cell((0,8,0),(-10,8,0)), cell((0,1,0),(-10,1,0))
        attribute_observer_apertures(mesh, dict(cells=[hit,outside,rim]))
        self.assertEqual(hit["aperture"]["position"], [-3,0,0])
        self.assertNotIn("aperture", outside)
        self.assertNotIn("aperture", rim)

    def test_solid_sheet_missing_audit_and_budget_do_not_create_exit_points(self):
        mesh = TriangleMesh()
        ids = [mesh.add_vertex(p) for p in [(-4,-4,-3),(4,-4,-3),(4,4,-3),(-4,4,-3)]]
        mesh.add_face(ids[0],ids[1],ids[2],"cover"); mesh.add_face(ids[0],ids[2],ids[3],"cover")
        evidence = cell()
        attribute_observer_apertures(mesh, dict(cells=[evidence]))
        self.assertNotIn("aperture", evidence)
        ring(mesh,-7)
        evidence["path_validation"]["status"] = "invalid"
        attribute_observer_apertures(mesh, dict(cells=[evidence]))
        self.assertNotIn("aperture", evidence)
        summary = dict(cells=[cell()])
        attribute_observer_apertures(mesh, summary, budget_sec=-1.)
        self.assertNotIn("aperture", summary["cells"][0])
        self.assertEqual(summary["aperture_summary"]["reason"], "geometry_budget_exceeded")

    def test_real_path_audit_checks_a_cover_before_attribution(self):
        from test_gpu_cuda_observer import fixture
        from test_observer_radiance import plate
        from leakage_simulator.observer_path_validation import validate_observer_paths
        trace, _, _, resolved = fixture()
        ring(trace.mesh, -7)
        evidence = dict(representative_path=[(0,0,-5),(0,0,-11)])
        summary = dict(cells=[evidence], views=[{}])
        validate_observer_paths(trace, summary, resolved)
        self.assertEqual(evidence["path_validation"]["status"], "valid")
        self.assertEqual(evidence["aperture"]["position"], [0,0,-7])
        plate(trace.mesh,-8,20)
        validate_observer_paths(trace, summary, resolved)
        self.assertEqual(evidence["path_validation"]["status"], "invalid")
        self.assertNotIn("aperture", evidence)
