"""A virtual emitter coincident with CAD emits; CAD in front still blocks it.

The resident CUDA equations are exercised on CPU, not as GPU execution proof.
"""
from types import SimpleNamespace
from unittest import TestCase

import numpy as np
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.observer_probe import _source_geometry, sampling_plan
from leakage_simulator.gpu_cuda_observer import pack_scene, reference_chunk
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.types import OpticalProfile, RayTraceConfig, EmitterAimSpec
from test_observer_radiance import plate, quad, source, render


class ObserverCoplanarEmitterTests(TestCase):
    def test_light_connection_at_a_block_endpoint_is_not_counted_as_emission(self):
        mesh=TriangleMesh(); plate(mesh,0,20)
        first=len(mesh.faces)
        quad(mesh,[(-12,-2,-5),(-8,-2,-5),(-8,2,-5),(-12,2,-5)],"black")
        for face in range(first,len(mesh.faces)):
            mesh.metadata(face)["preview_blocker_id"]="covered-source"
        lamp=source((-10,0,-5),4,100.,reverse=True)
        profiles=[OpticalProfile(profile_id="black",reflectance=.9,scatter_model="lambertian")]
        from leakage_simulator.observer_path_validation import validate_observer_paths
        audit_trace=SimpleNamespace(mesh=mesh,emitters=[lamp],config=RayTraceConfig(max_depth=1,angle_dependent_reflectance=False))
        resolved=OpticalPropertyResolver(mesh,profiles,[]).resolve_all()
        audit=dict(measure="luminance_cd_m2",cells=[dict(representative_path=[(-10,0,-5),(0,0,0),(0,0,-11)])])
        validate_observer_paths(audit_trace,audit,resolved)
        self.assertEqual(audit["cells"][0]["path_validation"]["reason"],"source_covered_by_blocker")
        result=render(mesh,lamp,profiles,depth=1,bounds=((-1,-1,-10),(1,1,10)))
        self.assertFalse(result["cells"],"A virtual emitter covered at the connection endpoint must not light the reflector")
        trace=SimpleNamespace(mesh=mesh,emitters=[lamp],config=RayTraceConfig(max_depth=1,angle_dependent_reflectance=False),
            observer_probe=dict(resolution=8,samples_per_cell=4,horizontal_deg=0.,vertical_deg=0.,range_deg=0.,step_deg=1.))
        resolved=OpticalPropertyResolver(mesh,profiles,[]).resolve_all()
        plan=sampling_plan(trace.observer_probe,(-1,-1,-10),(1,1,10),"neg_z",0)[0]
        values,_=reference_chunk(trace,pack_scene(mesh,_source_geometry(mesh,[lamp]),resolved),plan,(0,0,0),0,0,32)
        np.testing.assert_array_equal(values[:,0],0.)

    def test_a_block_coincident_with_a_datum_is_not_promoted_to_a_light_source(self):
        mesh=TriangleMesh(); plate(mesh,-5,20)
        for face in range(len(mesh.faces)):
            mesh.metadata(face)["preview_blocker_id"]="hole-cover"
        lamp=source((0,0,-5),40,100.)
        profiles=[OpticalProfile(profile_id="black",reflectance=0,scatter_model="none")]
        self.assertFalse(render(mesh,lamp,profiles)["cells"])
        self.assertFalse(render(mesh,lamp,profiles,spec_extra=dict(measurement="paths"))["cells"])
        trace=SimpleNamespace(mesh=mesh,emitters=[lamp],config=RayTraceConfig(max_depth=0),
            observer_probe=dict(resolution=8,samples_per_cell=4,horizontal_deg=0.,vertical_deg=0.,range_deg=0.,step_deg=1.))
        resolved=OpticalPropertyResolver(mesh,profiles,[]).resolve_all()
        plan=sampling_plan(trace.observer_probe,(-10,-10,-10),(10,10,10),"neg_z",0)[0]
        values,_=reference_chunk(trace,pack_scene(mesh,_source_geometry(mesh,[lamp]),resolved),plan,(0,0,0),0,0,32)
        np.testing.assert_array_equal(values[:,0],0.)

    def test_path_support_and_luminance_both_see_a_datum_on_the_actual_cad_surface(self):
        for uniform in (False, True):
            with self.subTest(uniform=uniform):
                mesh = TriangleMesh(); plate(mesh, -5, 20)
                lamp = source((0,0,-5),40,100.)
                if uniform:
                    lamp.aim = EmitterAimSpec(enabled=True, mode="sphere", distribution="uniform_solid_angle",
                        sphere_upper_deg=0.,sphere_lower_deg=90.,sphere_beta_deg=180.)
                profiles = [OpticalProfile(profile_id="black",reflectance=0,scatter_model="none")]
                paths = render(mesh,lamp,profiles,spec_extra=dict(measurement="paths"))
                radiance = render(mesh,lamp,profiles)
                self.assertTrue(paths["cells"])
                self.assertTrue(radiance["cells"], "CAD-coincident Datum emission was lost")
                expected = 50. if uniform else 100.
                for cell in radiance["cells"]:
                    self.assertAlmostEqual(cell["luminance_nit"],expected,delta=1e-9)
                    self.assertEqual(cell["min_depth"],0)
                trace = SimpleNamespace(mesh=mesh, emitters=[lamp], config=RayTraceConfig(max_depth=0,angle_dependent_reflectance=False),
                    observer_probe=dict(resolution=8,samples_per_cell=4,horizontal_deg=0.,vertical_deg=0.,range_deg=0.,step_deg=1.))
                resolved = OpticalPropertyResolver(mesh, profiles, []).resolve_all()
                plan = sampling_plan(trace.observer_probe,(-10,-10,-10),(10,10,10),"neg_z",0)[0]
                values,_ = reference_chunk(trace,pack_scene(mesh,_source_geometry(mesh,[lamp]),resolved),plan,(0,0,0),0,0,32)
                np.testing.assert_allclose(values[:,0],expected,atol=1e-9,rtol=0.)
                np.testing.assert_array_equal(values[:,2],0)

    def test_a_real_wall_in_front_of_a_coplanar_source_is_not_turned_into_transmission(self):
        for gap in (1.,1e-5):
            with self.subTest(gap=gap):
                mesh=TriangleMesh(); plate(mesh,-5,20); plate(mesh,-5-gap,20)
                lamp=source((0,0,-5),40,100.)
                profiles=[OpticalProfile(profile_id="black",reflectance=0,scatter_model="none")]
                self.assertFalse(render(mesh,lamp,profiles)["cells"])
                trace=SimpleNamespace(mesh=mesh,emitters=[lamp],config=RayTraceConfig(max_depth=0),
                    observer_probe=dict(resolution=8,samples_per_cell=4,horizontal_deg=0.,vertical_deg=0.,range_deg=0.,step_deg=1.))
                resolved=OpticalPropertyResolver(mesh,profiles,[]).resolve_all()
                plan=sampling_plan(trace.observer_probe,(-10,-10,-10),(10,10,10),"neg_z",0)[0]
                values,_=reference_chunk(trace,pack_scene(mesh,_source_geometry(mesh,[lamp]),resolved),plan,(0,0,0),0,0,32)
                np.testing.assert_array_equal(values[:,0],0.)
