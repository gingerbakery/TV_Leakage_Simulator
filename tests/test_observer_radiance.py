"""Analytic CPU transport tests; synthetic fixtures, not a TV calibration."""
import math
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from validate_observer_detection import plate, quad, enclosure, source, settings
from leakage_simulator.bsdf_transport import BsdfTransport, PolarTable
from leakage_simulator.escape_collection import EscapeCollector
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.observer_probe import angles, trace_observer_paths
from leakage_simulator.observer_radiance import brdf, gaussian_acceptance, delta_weight, continuation, continuation_pdf
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.raytrace_bridge import _append_preview_blockers
from leakage_simulator.types import OpticalProfile, RayTraceConfig, ReceiverSpec, ReceiverGrid, EmitterAimSpec


def render(mesh, lamp, profiles, *, depth=0, threshold=0., seed=42, spacing=2.,
           bounds=((-10,-10,-10),(10,10,10)), dispatch="batch", batch_size=8192,
           spec_extra=None, should_stop=None):
    spec = dict(settings(spacing=spacing), measurement="radiance", samples_per_cell=8,
                min_luminance_nit=threshold)
    spec.update(spec_extra or {})
    trace = SimpleNamespace(mesh=mesh, emitters=[lamp], observer_probe=spec,
        config=RayTraceConfig(max_depth=depth, seed=seed, angle_dependent_reflectance=False,
                              compute_backend="cpu", intersection_backend="bvh"))
    receivers=[]
    for axis in range(3):
        for sign, prefix in ((1,"pos_"),(-1,"neg_")):
            center=[(bounds[0][i]+bounds[1][i])/2 for i in range(3)]
            center[axis]=bounds[1][axis] if sign>0 else bounds[0][axis]
            normal=[0.,0.,0.]; normal[axis]=-sign
            u=[0.,0.,0.]; v=[0.,0.,0.]
            u[(axis+1)%3]=1.; v[(axis+2)%3]=1.
            receivers.append(ReceiverSpec(receiver_id="__leak_preview_"+prefix+"xyz"[axis],center=tuple(center),
                normal=tuple(normal),u_axis=tuple(u),v_axis=tuple(v),width_mm=20,height_mm=20,resolution=(1,1)))
    collector = EscapeCollector(dict(minimum=bounds[0],maximum=bounds[1],direction="neg_z"),
        receivers,{r.receiver_id:ReceiverGrid.empty(r) for r in receivers})
    return trace_observer_paths(trace,collector,OpticalPropertyResolver(mesh,profiles,[]).resolve_all(),
        should_stop,dispatch=dispatch,batch_size=batch_size)


def black():
    return OpticalProfile(profile_id="black",reflectance=0,scatter_model="none")


class ObserverRadianceTests(TestCase):
    def test_direct_luminance_and_threshold_are_not_path_counts(self):
        mesh=TriangleMesh(); plate(mesh,9,20)
        for luminance in (1e-6,100.):
            with self.subTest(luminance=luminance):
                result=render(mesh,source((0,0,-5),40,luminance),[black()])
                self.assertEqual(result["measure"],"luminance_cd_m2")
                self.assertTrue(result["cells"])
                for cell in result["cells"]:
                    self.assertAlmostEqual(cell["luminance_nit"],luminance,delta=1e-10)
                filtered=render(mesh,source((0,0,-5),40,luminance),[black()],threshold=1.)
                self.assertEqual(bool(filtered["cells"]),luminance>=1.)
                self.assertAlmostEqual(filtered["peak_luminance_before_threshold_nit"],luminance,delta=1e-10)
                self.assertGreater(filtered["positive_before_threshold_count"],0)
                self.assertEqual(filtered["filtered_count"] > 0,luminance<1.)
                self.assertTrue(filtered["complete"])
                self.assertIn("finite_sampling",filtered["limitations"])

    def test_mirror_preserves_analytic_r_times_l(self):
        mesh=TriangleMesh(); plate(mesh,0,20)
        for reflectance in (1e-6,.9):
            profile=OpticalProfile(profile_id="black",reflectance=reflectance,scatter_model="specular")
            result=render(mesh,source((0,0,-5),40,reverse=True),[profile],depth=1)
            self.assertTrue(result["cells"])
            for cell in result["cells"]:
                self.assertAlmostEqual(cell["luminance_nit"],100*reflectance,delta=1e-10)
                self.assertEqual(cell["min_depth"],1)
            filtered=render(mesh,source((0,0,-5),40,reverse=True),[profile],depth=1,threshold=1.)
            self.assertEqual(bool(filtered["cells"]),reflectance==.9)

    def test_measured_bsdf_uses_normalization_and_forward_directions(self):
        table=PolarTable.build(dict(latitudesDeg=[90.,45.,0.],longitudesDeg=[0.,90.,180.,270.,360.],
                                    values=[[1.]*5]*3))
        profile=OpticalProfile(profile_id="measured",reflectance=0,bsdf_asset_id="synthetic")
        profile._bsdf_transport=BsdfTransport("synthetic",550.,(550.,),((0.,),),((table,),),
                                            (((0.,90.),(.9,.9)),))
        self.assertAlmostEqual(brdf(profile,(0,0,1),(0,0,1),(0,0,1)),.9/math.pi,delta=1e-10)
        # Flat weak RT cannot produce the same radiance as strong RT.
        mesh=TriangleMesh(); quad(mesh,[(-10,-10,0),(10,-10,0),(10,10,0),(-10,10,0)],"measured")
        strong=render(mesh,source((0,0,-5),18,reverse=True),[profile],depth=1,threshold=1.)
        self.assertTrue(strong["cells"])
        profile._bsdf_transport=BsdfTransport("synthetic",550.,(550.,),((0.,),),((table,),),
                                            (((0.,90.),(1e-6,1e-6)),))
        weak=render(mesh,source((0,0,-5),18,reverse=True),[profile],depth=1,threshold=1.)
        self.assertFalse(weak["cells"])

    def test_closed_wall_and_reflective_hole_plug_block_direct_light(self):
        for blocked in (False,True):
            mesh=TriangleMesh(); enclosure(mesh,1,-1,1)
            for z in (0.,-1.):
                plate(mesh,z,1,None if not blocked else (.137,.173,.08))
            if blocked:
                _append_preview_blockers(mesh,[dict(blocker_id="plug",shape="box",center=[.137,.173,-1],
                    u_axis=[1,0,0],v_axis=[0,1,0],normal=[0,0,1],width_mm=.24,height_mm=.24,
                    depth_mm=.2,enabled=True)])
            result=render(mesh,source((0,0,.5),.6),[black(),
                OpticalProfile(profile_id="default",reflectance=.9,scatter_model="lambertian")],
                depth=2,spacing=.1,bounds=((-1,-1,-2),(1,1,1)))
            self.assertEqual(result["direct_count"],0)
            self.assertEqual(result["cells"],[])

    def test_global_spatial_strata_find_offset_gap_at_requested_resolution(self):
        mesh=TriangleMesh(); enclosure(mesh,1,-1,1)
        for z in (0.,-1.): plate(mesh,z,1,(.137,.173,.08))
        result=render(mesh,source((0,0,.5),.6),[black()],depth=2,spacing=.1,
            bounds=((-1,-1,-2),(1,1,1)))
        self.assertGreater(result["direct_count"],0)
        self.assertEqual(result["planned_count"],20*20*8)

    def test_midpoint_angles_exist_even_without_coarse_hits(self):
        spec=dict(settings(hemisphere=True),refine_angles=True)
        self.assertIn((52.5,7.5),angles(spec))
        self.assertEqual(len(angles(spec)),577)

    def test_sampling_seed_and_dispatch_are_reproducible(self):
        mesh=TriangleMesh(); plate(mesh,9,20)
        lamp=source((0,0,-5),8)
        a=render(mesh,lamp,[black()],dispatch="scalar",batch_size=7)
        b=render(mesh,lamp,[black()],dispatch="batch",batch_size=37)
        self.assertEqual(a["cells"],b["cells"])
        self.assertNotEqual(a["cells"],render(mesh,lamp,[black()],seed=43)["cells"])

    def test_stop_and_depth_limit_remain_explicit(self):
        mesh=TriangleMesh(); plate(mesh,0,20)
        profile=OpticalProfile(profile_id="black",reflectance=.9,scatter_model="specular")
        result=render(mesh,source((0,0,-5),40,reverse=True),[profile],depth=0)
        self.assertFalse(result["cells"])
        self.assertGreater(result["depth_limited_count"],0)
        self.assertIn("depth_truncated",result["limitations"])
        stopped=render(mesh,source((0,0,-5),40),[profile],should_stop=lambda:True)
        self.assertTrue(stopped["stopped"])
        self.assertFalse(stopped["complete"])
        self.assertIn("partial_result",stopped["limitations"])

    def test_legacy_gaussian_acceptance_normal_incidence(self):
        for sigma_deg in (1.,18.,90.):
            sigma=math.radians(sigma_deg)
            expected=1-math.exp(-math.acos(1e-9)**2/(2*sigma*sigma))
            self.assertAlmostEqual(gaussian_acceptance(sigma_deg,1.),expected,delta=2e-10)

    def test_two_specular_bounces_preserve_radiance_and_depth_limit(self):
        mesh=TriangleMesh(); d=math.sqrt(.5)
        for center,u in (((0,0,0),(d,0,d)),((5,0,0),(d,0,-d))):
            quad(mesh,[tuple(center[i]+u[i]*su*2+(sv*8 if i==1 else 0) for i in range(3))
                for su,sv in ((-1,-1),(1,-1),(1,1),(-1,1))],"mirror")
        profile=OpticalProfile(profile_id="mirror",reflectance=.9,scatter_model="specular")
        lamp=source((5,0,-5),4,reverse=True)
        shallow=render(mesh,lamp,[profile],depth=1,spacing=1.)
        result=render(mesh,lamp,[profile],depth=2,spacing=1.)
        self.assertFalse(shallow["cells"])
        self.assertTrue(result["cells"])
        for cell in result["cells"]:
            # Edge pixels are partially occupied; interior pixels preserve L*R^2.
            self.assertLessEqual(cell["luminance_nit"],81.+1e-10)
        self.assertAlmostEqual(max(c["luminance_nit"] for c in result["cells"]),81.,delta=1e-10)

    def test_public_engine_reports_radiance_without_changing_forward_flux(self):
        from test_observer_full_detail import FullDetailObserverTests
        from test_escape_collection import receivers
        from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
        trace, _, _, profiles, bounds = FullDetailObserverTests().context()
        direct = DirectRayTraceInput(mesh=trace.mesh,emitters=trace.emitters,receivers=receivers(),
            optical_profiles=profiles,config=trace.config,escape_collection=bounds)
        original=run_direct_ray_trace(direct)
        direct.observer_probe=dict(trace.observer_probe,measurement="radiance",samples_per_cell=8)
        progress=[]
        result=run_direct_ray_trace(direct,progress_callback=lambda n,total:progress.append((n,total)))
        summary=result.metrics["_observer_probe_summary"]
        self.assertEqual(summary["contract"],"observer_radiance_parallel_v1")
        self.assertEqual(result.metrics["_escape_direction_summary"],original.metrics["_escape_direction_summary"])
        self.assertEqual(progress[-1],(16+summary["planned_count"],16+summary["planned_count"]))

    def test_gaussian_continuous_and_delta_forward_energy(self):
        import numpy as np
        nodes,weights=np.polynomial.legendre.leggauss(192)
        for sigma in (1.,18.,90.):
            profile=OpticalProfile(profile_id="g",reflectance=.7,scatter_model="gaussian",gaussian_sigma_deg=sigma)
            limit=min(math.pi/2,math.radians(sigma)*8)
            theta=(nodes+1)*limit/2
            integral=sum(float(weight)*brdf(profile,(0,0,1),(0,0,1),
                (math.sin(float(t)),0,math.cos(float(t))),False)*math.cos(float(t))*math.sin(float(t))
                for t,weight in zip(theta,weights))*limit/2*2*math.pi
            integral+=delta_weight(profile,(0,0,1),(0,0,1),False)
            self.assertAlmostEqual(integral,.7,delta=2e-7)
            rng=random.Random(19)
            for _ in range(30):
                wi,weight,is_delta,pdf=continuation(rng,profile,(0,0,1),(0,0,1),False)
                self.assertGreaterEqual(weight,0.)
                if not is_delta:
                    self.assertAlmostEqual(pdf,continuation_pdf(profile,(0,0,1),(0,0,1),wi,False),delta=1e-9)

    def test_weak_bsdf_tail_is_filtered_instead_of_promoted_to_a_bright_path(self):
        table=PolarTable.build(dict(latitudesDeg=[90.,80.,60.,30.,0.],
            longitudesDeg=[0.,90.,180.,270.,360.],values=[[1e-12]*5]*4+[[1.]*5]))
        profile=OpticalProfile(profile_id="measured",reflectance=0,bsdf_asset_id="tail")
        profile._bsdf_transport=BsdfTransport("tail",550.,(550.,),((0.,),),((table,),),(((0.,90.),(.9,.9)),))
        mesh=TriangleMesh(); quad(mesh,[(-10,-10,0),(10,-10,0),(10,10,0),(-10,10,0)],"measured")
        result=render(mesh,source((0,0,-5),18,reverse=True),[profile],depth=1,threshold=1.,spec_extra={"horizontal_deg":80.})
        self.assertFalse(result["cells"])

    def test_unsupported_source_law_fails_closed_and_invalid_threshold_is_rejected(self):
        mesh=TriangleMesh(); plate(mesh,9,20)
        lamp=source((0,0,-5),40); lamp.direction_distribution="gaussian"
        with self.assertRaisesRegex(ValueError,"Lambertian/Isotropic"):
            render(mesh,lamp,[black()])
        for value in (-1,float("nan"),float("inf"),True):
            with self.subTest(value=value),self.assertRaises(ValueError):
                render(mesh,source((0,0,-5),40),[black()],threshold=value)

    def test_diffuse_nee_and_continuation_do_not_double_count(self):
        # Independent closed-form rectangle view factor, averaged over [-1,1]^2.
        # Fixed seeds 42..44, 3,200 paths/seed, predeclared 3% MC tolerance.
        half, height = 9.,5.
        import numpy as np
        nodes,weights=np.polynomial.legendre.leggauss(16)
        def primitive(x,y):
            sx=math.sqrt(x*x+height*height); sy=math.sqrt(y*y+height*height)
            return (x/sx*math.atan(y/sx)+y/sy*math.atan(x/sy))/(2*math.pi)
        def factor(x,y):
            return sum(signx*signy*primitive(cx-x,cy-y)
                for cx,signx in ((-half,-1),(half,1)) for cy,signy in ((-half,-1),(half,1)))
        expected=100*.9*sum(float(wx*wy)*factor(float(x),float(y))
            for x,wx in zip(nodes,weights) for y,wy in zip(nodes,weights))/4
        mesh=TriangleMesh(); plate(mesh,0,20)
        profile=OpticalProfile(profile_id="black",reflectance=.9,scatter_model="lambertian")
        estimates=[]
        for seed in (42,43,44):
            result=render(mesh,source((0,0,-5),18,reverse=True),[profile],depth=1,spacing=.1,
                seed=seed,bounds=((-1,-1,-10),(1,1,10)))
            self.assertEqual(result["planned_count"],3200)
            estimates.append(sum(c["luminance_nit"] for c in result["cells"])/400)
        self.assertLess(abs(sum(estimates)/3/expected-1),.03)

    def test_bsdf_incident_axis_is_not_transposed(self):
        table=PolarTable.build(dict(latitudesDeg=[90.,45.,0.],longitudesDeg=[0.,90.,180.,270.,360.],values=[[1.]*5]*3))
        profile=OpticalProfile(profile_id="nonreciprocal",reflectance=0,bsdf_asset_id="aoi")
        profile._bsdf_transport=BsdfTransport("aoi",550.,(550.,),((0.,),),((table,),),(((0.,60.),(.2,.8)),))
        n=(0,0,1); tilted=(math.sin(math.pi/3),0,math.cos(math.pi/3))
        self.assertAlmostEqual(brdf(profile,n,tilted,n,False),.8/math.pi,delta=1e-10)
        self.assertAlmostEqual(brdf(profile,n,n,tilted,False),.2/math.pi,delta=1e-10)

    def test_sphere_uniform_and_lambertian_keep_existing_world_axis_and_flux(self):
        mesh=TriangleMesh(); plate(mesh,9,20)
        lamp=source((0,0,-5),40)
        for distribution,expected in (("uniform_solid_angle",50.),("lambertian",100.)):
            lamp.aim=EmitterAimSpec(enabled=True,mode="sphere",distribution=distribution,
                sphere_upper_deg=0,sphere_lower_deg=90,sphere_beta_deg=180)
            for flip in (False,True):
                lamp.normal_flip=flip
                result=render(mesh,lamp,[black()])
                self.assertTrue(result["cells"])
                for cell in result["cells"]:
                    self.assertAlmostEqual(cell["luminance_nit"],expected,delta=1e-10)
