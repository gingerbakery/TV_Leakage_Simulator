"""CPU-only analytic audits of path-support versus visible-leak detection.

Exit 2 means the CURRENT detector fails the synthetic visibility acceptance
criteria. A reproduction of a known false positive/negative is not a pass.
The 1 nit threshold is a synthetic test oracle, not a human-visibility standard.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import platform
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leakage_simulator.bsdf_transport import BsdfTransport, PolarTable
from leakage_simulator.escape_collection import EscapeCollector
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.observer_probe import trace_observer_paths
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.raytrace_bridge import _append_preview_blockers
from leakage_simulator.types import EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec, ReceiverGrid


def quad(mesh, points, material='black'):
    ids = [mesh.add_vertex(tuple(p)) for p in points]
    mesh.add_face(ids[0], ids[1], ids[2], material)
    mesh.add_face(ids[0], ids[2], ids[3], material)


def plate(mesh, z, radius, hole=None):
    def rect(x0,y0,x1,y1):
        quad(mesh, [(x0,y0,z),(x1,y0,z),(x1,y1,z),(x0,y1,z)])
    if hole is None:
        rect(-radius,-radius,radius,radius)
    else:
        x,y,w = hole
        a,b,c,d = x-w/2,x+w/2,y-w/2,y+w/2
        rect(-radius,-radius,a,radius); rect(b,-radius,radius,radius)
        rect(a,-radius,b,c); rect(a,d,b,radius)


def enclosure(mesh, radius, front, back):
    plate(mesh,back,radius)
    for x in (-radius,radius):
        quad(mesh,[(x,-radius,front),(x,radius,front),(x,radius,back),(x,-radius,back)])
    for y in (-radius,radius):
        quad(mesh,[(-radius,y,front),(radius,y,front),(radius,y,back),(-radius,y,back)])


def source(center, width, luminance=100., reverse=False):
    return EmitterSpec(emitter_id='source', emitter_type='datum_plane', center=center,
        u_axis=(1,0,0),v_axis=(0,1 if reverse else -1,0),width_mm=width,height_mm=width,
        power_mode='set_luminance',luminance_nit=luminance,ray_count=16,seed=42)


def settings(h=0.,v=0.,spacing=10.,hemisphere=False):
    return dict(horizontal_deg=h,vertical_deg=v,range_deg=0.,step_deg=15. if hemisphere else 1.,
        resolution=8,samples_per_cell=2,search_mode='full_detail',spacing_mm=spacing,
        coverage='hemisphere' if hemisphere else 'local')


def evaluate(name, mesh, lamp, profiles, bounds, spec, expected_visible, oracle, depth=0):
    minimum, maximum = bounds
    receivers=[]
    for axis in range(3):
        for sign, prefix in ((1,'pos_'),(-1,'neg_')):
            center=[(minimum[i]+maximum[i])/2 for i in range(3)]
            center[axis]=maximum[axis] if sign>0 else minimum[axis]
            normal=[0.,0.,0.]; normal[axis]=-sign
            u=[0.,0.,0.]; v=[0.,0.,0.]
            u[(axis+1)%3]=1.; v[(axis+2)%3]=1.
            receivers.append(ReceiverSpec(receiver_id='__leak_preview_'+prefix+'xyz'[axis],center=tuple(center),
                normal=tuple(normal),u_axis=tuple(u),v_axis=tuple(v),width_mm=maximum[(axis+1)%3]-minimum[(axis+1)%3],
                height_mm=maximum[(axis+2)%3]-minimum[(axis+2)%3],resolution=(1,1)))
    collector=EscapeCollector(dict(minimum=minimum,maximum=maximum,direction='neg_z'), receivers,
        {r.receiver_id:ReceiverGrid.empty(r) for r in receivers})
    config=RayTraceConfig(max_depth=depth,compute_backend='cpu',intersection_backend='bvh')
    trace=SimpleNamespace(mesh=mesh,emitters=[lamp],config=config,observer_probe=spec)
    result=trace_observer_paths(trace,collector,OpticalPropertyResolver(mesh,profiles,[]).resolve_all())
    detected=result['direct_count']+result['reflected_count']>0
    classification=('TP' if expected_visible else 'FP') if detected else ('FN' if expected_visible else 'TN')
    inputs=dict(vertices=mesh.vertices,faces=[(f.v0,f.v1,f.v2) for f in mesh.faces],
        materials=[mesh.material_id(i) for i in range(len(mesh.faces))],
        metadata=[mesh.metadata(i) for i in range(len(mesh.faces))],emitter=asdict(lamp),
        profiles=[asdict(p) for p in profiles],bounds=bounds,observer=spec,config=asdict(config))
    inputs['synthetic_compiled_bsdf'] = {
        p.profile_id: dict(asset_id=p.bsdf_asset_id,angles=p._bsdf_transport.angles,rt=p._bsdf_transport.rt,
            tables=[[dict(theta=t.theta.tolist(),phi=t.phi.tolist(),values=t.values.tolist(),normalization=t.normalization)
                for t in wave] for wave in p._bsdf_transport.tables])
        for p in profiles if p.bsdf_asset_id}
    encoded=json.dumps(inputs,sort_keys=True,ensure_ascii=False).encode()
    return dict(name=name,expected_visible=expected_visible,detected=detected,classification=classification,
        analytic_oracle=oracle,input_sha256=hashlib.sha256(encoded).hexdigest(),inputs=inputs,
        direct_count=result['direct_count'],reflected_count=result['reflected_count'],
        attempted_count=result['attempted_count'],planned_count=result['planned_count'],
        depth_limited_count=result['depth_limited_count'],complete=result['complete'],
        execution_backend=result['execution_backend'],runtime_sec=result['runtime_sec'],view_count=len(result['views']),
        positive_views=[dict(horizontal_deg=v['horizontal_deg'],vertical_deg=v['vertical_deg'],
            direct_count=v['direct_count'],reflected_count=v['reflected_count']) for v in result['views']
            if v['direct_count']+v['reflected_count']>0])


def run_validation():
    black=OpticalProfile(profile_id='black',reflectance=0,scatter_model='none')
    cases=[]
    # Exact direct Lambertian radiance is L, independent of positive lamp magnitude.
    for luminance in (1e-6,100.):
        mesh=TriangleMesh(); plate(mesh,9,10)
        cases.append(evaluate('direct_'+str(luminance)+'_nit',mesh,source((0,0,-5),18,luminance),[black],
            ([-10,-10,-10],[10,10,10]),settings(),luminance>=1.,
            dict(kind='unoccluded_lambertian',radiance_nit=luminance,synthetic_threshold_nit=1.)))
    # Ideal mirror radiance is R*L. Both low and high R are nonzero supports.
    for reflectance in (1e-6,.9):
        mesh=TriangleMesh(); quad(mesh,[(-10,-10,0),(10,-10,0),(10,10,0),(-10,10,0)],'mirror')
        mirror=OpticalProfile(profile_id='mirror',reflectance=reflectance,scatter_model='specular')
        cases.append(evaluate('mirror_R_'+str(reflectance),mesh,source((0,0,-5),18,reverse=True),[black,mirror],
            ([-10,-10,-10],[10,10,10]),settings(),100*reflectance>=1.,
            dict(kind='ideal_specular_mirror',radiance_nit=100*reflectance,synthetic_threshold_nit=1.),depth=1))
    # Synthetic compiled BSDF consumer checks, not vendor-file/parser validation.
    # A constant BRDF has L_out <= R*L_source; the source's inscribed disk at
    # the central surface point gives L_out >= R*L_source*81/(81+25).
    for label, reflectance, tail, horizontal in (
        ('weak_rt',1e-6,False,0.),('bright_flat',.9,False,0.),('weak_tail',.9,True,80.)):
        mesh=TriangleMesh(); quad(mesh,[(-10,-10,0),(10,-10,0),(10,10,0),(-10,10,0)],'measured')
        latitudes=[90.,80.,60.,30.,0.]
        values=[[1. if not tail or latitude==0 else 1e-12]*5 for latitude in latitudes]
        table=PolarTable.build(dict(latitudesDeg=latitudes,longitudesDeg=[0.,90.,180.,270.,360.],values=values))
        profile=OpticalProfile(profile_id='measured',reflectance=0,bsdf_asset_id='synthetic_'+label)
        profile._bsdf_transport=BsdfTransport(profile.bsdf_asset_id,550.,(550.,),((0.,),),((table,),),
            (((0.,90.),(reflectance,reflectance)),))
        upper=reflectance*100. if not tail else reflectance*100.*math.pi*1e-12/table.normalization
        lower=reflectance*100.*81/106 if not tail else 0.
        cases.append(evaluate('bsdf_'+label,mesh,source((0,0,-5),18,reverse=True),[black,profile],
            ([-10,-10,-10],[10,10,10]),settings(h=horizontal,spacing=1.),lower>=1.,
            dict(kind='synthetic_compiled_bsdf',radiance_lower_bound_nit=lower,radiance_upper_bound_nit=upper,
                synthetic_threshold_nit=1.,outgoing_angle_deg=horizontal,
                proof='nonnegative BRDF integral bounds; constant-radiance square source contains radius-9 disk at distance 5'),depth=1))
    # Two aligned narrow apertures: a known central ray at H=52.5,V=7.5 passes both.
    h,v=52.5,7.5
    r=20.; holes=[]
    for distance in (4.,8.):
        holes.append((distance*math.tan(math.radians(h)),
            distance*math.tan(math.radians(v))/math.cos(math.radians(h)),.1))
    def angular_mesh():
        mesh=TriangleMesh(); enclosure(mesh,r,-8,2)
        for z,hole in zip((-4.,-8.),holes): plate(mesh,z,r,hole)
        return mesh
    oracle=dict(kind='two_aperture_centerline',known_visible_angle_deg=[h,v],aperture_width_mm=.1,
        aperture_separation_mm=4.,radiance_nit=100.,synthetic_threshold_nit=1.,
        proof='ray from origin crosses both aperture centers; nonzero aperture and source areas give nonzero visible area')
    cases.append(evaluate('angular_coarse_15deg',angular_mesh(),source((0,0,0),.02),[black],
        ([-r,-r,-10],[r,r,2]),settings(hemisphere=True),True,oracle,depth=3))
    cases.append(evaluate('angular_known_direction',angular_mesh(),source((0,0,0),.02),[black],
        ([-r,-r,-10],[r,r,2]),settings(h,v),True,oracle,depth=3))
    # Offset small gap: coarse spatial grid can miss it even at the correct angle.
    def gap_mesh(closed=False,accessory=False,block=False):
        mesh=TriangleMesh(); enclosure(mesh,1,-1,1)
        for z in (0.,-1.): plate(mesh,z,1,None if closed else (.137,.173,.08))
        if accessory: plate(mesh,-.5,1)
        if block:
            _append_preview_blockers(mesh,[dict(blocker_id='reflective-plug',shape='ellipse',
                center=[.137,.173,-1],u_axis=[1,0,0],v_axis=[0,1,0],normal=[0,0,1],
                width_mm=.24,height_mm=.24,depth_mm=.2,enabled=True)])
        return mesh
    gap_oracle=dict(kind='aligned_offset_apertures',known_visible_angle_deg=[0,0],aperture_center_mm=[.137,.173],
        aperture_width_mm=.08,radiance_nit=100.,synthetic_threshold_nit=1.,
        proof='normal ray at x=.137,y=.173 crosses both holes and the source')
    for spacing in (2.,.1):
        cases.append(evaluate('spatial_spacing_'+str(spacing)+'mm',gap_mesh(),source((0,0,.5),.6),[black],
            ([-1,-1,-2],[1,1,1]),settings(spacing=spacing),True,gap_oracle,depth=3))
    for name, kwargs in (('opaque_no_gap',dict(closed=True)),('accessory_cover',dict(accessory=True)),
                         ('reflective_hole_plug',dict(block=True))):
        profiles=[black,OpticalProfile(profile_id='default',reflectance=.9,scatter_model='lambertian')]
        cases.append(evaluate(name,gap_mesh(**kwargs),source((0,0,.5),.6),profiles,
            ([-1,-1,-2],[1,1,1]),settings(spacing=.1),False,
            dict(kind='opaque_intersection_control',radiance_nit=0.,synthetic_threshold_nit=1.,
                proof='every normal source-to-observer line intersects a closed opaque surface'),depth=3))
    # Exact two-mirror path source(5,0,-5)->(5,0,0)->(0,0,0)->observer along -Z.
    def mirror_mesh():
        mesh=TriangleMesh(); d=math.sqrt(.5)
        for center,u in (((0,0,0),(d,0,d)),((5,0,0),(d,0,-d))):
            points=[tuple(center[i]+u[i]*su*2+(sv*8 if i==1 else 0) for i in range(3))
                for su,sv in ((-1,-1),(1,-1),(1,1),(-1,1))]
            quad(mesh,points,'mirror')
        return mesh
    for depth in (1,2):
        cases.append(evaluate('two_mirrors_depth_'+str(depth),mirror_mesh(),source((5,0,-5),4,reverse=True),
            [OpticalProfile(profile_id='mirror',reflectance=1,scatter_model='specular')],
            ([-10,-10,-10],[10,10,10]),settings(spacing=1.),True,
            dict(kind='two_exact_specular_bounces',radiance_nit=100.,synthetic_threshold_nit=1.,
                center_path_mm=[[5,0,-5],[5,0,0],[0,0,0],[0,0,-11]]),depth=depth))
    names=['src/leakage_simulator/'+name+'.py' for name in
        ('observer_probe','geometry','reflection','bsdf_transport','optics','escape_collection')]
    names.append('scripts/validate_observer_detection.py')
    return dict(validation_scope='synthetic CPU analytic audit; not a customer TV validation',
        date='2026-11-05',python_version=platform.python_version(),commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names},
        detector_interpretation='positive path count treated as visible leak ONLY for auditing the user goal',
        synthetic_threshold_nit=1.,threshold_is_human_visibility_standard=False,
        visibility_acceptance_passed=all(c['classification'] in ('TP','TN') for c in cases),
        controls_passed=all(c['classification']=='TN' for c in cases if c['name'] in
            ('opaque_no_gap','accessory_cover','reflective_hole_plug')),
        totals={label:sum(c['classification']==label for c in cases) for label in ('TP','FP','TN','FN')},
        cases=cases)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=run_validation()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for case in report['cases']:
        print(f"{case['classification']:2} {case['name']}: direct={case['direct_count']} reflected={case['reflected_count']} views={case['view_count']}",flush=True)
    print(json.dumps({k:report[k] for k in ('visibility_acceptance_passed','controls_passed','totals')}),flush=True)
    return 0 if report['visibility_acceptance_passed'] else 2

if __name__=='__main__': raise SystemExit(main())
