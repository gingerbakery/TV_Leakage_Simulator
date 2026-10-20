from __future__ import annotations

import copy
from dataclasses import asdict
import math
from pathlib import Path
import random
import sys
import unittest
import tempfile
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakage_simulator.bsdf_guard import bind_bsdf_profiles, validate_bsdf_profiles
from leakage_simulator.bsdf_import import import_lt_ascii_bsdf
from leakage_simulator.bsdf_transport import compile_asset, incident_frame
from leakage_simulator.api import ApiRuntime, create_app
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.reflection import effective_surface_reflectance, sample_reflection_direction
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import EmitterAimSpec, EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec


def synthetic_asset(lobe=False, reflectance=.3, wavelength=550):
    """Own analytic fixtures, never copied measured/vendor values."""
    aoi = (0, 30, 60, 80)
    files, master = [], ['#UDOP AngGridMultiple 1.0']
    for angle in aoi:
        name = f'scatter-{angle}.txt'
        lines = ['SphereMesh: 73 37 0 0 360 90']
        for theta in np.linspace(90, 0, 37):
            row = []
            for phi in np.linspace(0, 360, 73):
                # Isotropic about the mirror direction, expressed in LT frame.
                ct = math.cos(math.radians(theta))
                st = math.sin(math.radians(theta))
                axis_dot = ct*math.cos(math.radians(angle))-st*math.sin(math.radians(phi))*math.sin(math.radians(angle))
                value = .02 + math.exp(25*(axis_dot-1)) if lobe else 1.
                row.append(f'{value:.12g}')
            lines.append(' '.join(row))
        files.append({'name':name, 'text':'\n'.join(lines)})
        master += [f'FileName = ..\\{name}', 'MeshFormat = Polar', 'MeshDataType = BSDF', f'RAOI = {angle}', f'Lambda = {wavelength}', 'POI = 0', 'Index = 1', 'IndexIncident = 1']
    files += [{'name':'master.txt','text':'\n'.join(master)},
              {'name':'rt.txt','text':f'DFAT Version 1.0\nABSORBING: YES\nINDEX: 1\nDATAITEMS: TAVG RAVG\nwv {wavelength}\n' + '\n'.join(f'AOI {a} 0 {reflectance}' for a in (0,30,60,80,90))}]
    asset = import_lt_ascii_bsdf({'files':files})
    asset['transport'] = {'model':'isotropic_no_shift', 'wavelengthNm':wavelength, 'aoiExtrapolation':'clamp'}
    return asset


def planar_trace_input(asset, rays=1200, depth=1):
    mesh = TriangleMesh()
    vertices = [mesh.add_vertex(v) for v in [(-100,-100,0),(100,-100,0),(100,100,0),(-100,100,0)]]
    mesh.add_face(*vertices[:3], 'measured')
    mesh.add_face(vertices[0],vertices[2],vertices[3], 'measured')
    emitter = EmitterSpec(emitter_id='beam', emitter_type='datum_plane', center=(0,0,1),
        u_axis=(1,0,0), v_axis=(0,-1,0), width_mm=.001,height_mm=.001,power_lumen=1,
        ray_count=rays,seed=712,
        aim=EmitterAimSpec(enabled=True,mode='sphere',distribution='uniform_solid_angle',sphere_upper_deg=0,sphere_lower_deg=0,sphere_beta_deg=180))
    receiver = ReceiverSpec(receiver_id='screen',center=(0,0,2),normal=(0,0,-1),
        u_axis=(1,0,0),v_axis=(0,-1,0),width_mm=2000,height_mm=2000,resolution=(20,20),acceptance_angle_deg=90)
    return DirectRayTraceInput(mesh=mesh,emitters=[emitter],receivers=[receiver],
        optical_profiles=[OpticalProfile(profile_id='measured',reflectance=0.,scatter_model='none',bsdf_asset_id=asset['id'])],
        config=RayTraceConfig(max_depth=depth,min_energy=0.,store_ray_paths=True,max_stored_paths=50,contribution_mode='detailed'),
        bsdf_assets=[asset])


class BsdfTransportTests(unittest.TestCase):
    def test_api_roundtrip_runs_bsdf_and_repeat_seed_reproduces_flux_grid(self):
        trace=planar_trace_input(synthetic_asset(),rays=200)
        payload={'scene_token':'fixture','emitters':[asdict(trace.emitters[0])],
                 'receivers':[asdict(trace.receivers[0])],'config':asdict(trace.config),
                 'optical_profiles':[trace.optical_profiles[0].to_dict()],'bsdf_assets':trace.bsdf_assets}
        with tempfile.TemporaryDirectory() as folder:
            runtime=ApiRuntime(Path(folder))
            runtime._scene_mesh_cache['fixture']={'vertices':trace.mesh.vertices,'faces':[(f.v0,f.v1,f.v2) for f in trace.mesh.faces],
                 'face_material_ids':['measured','measured'],'face_component_ids':[1,1]}
            client=TestClient(create_app(runtime))
            first=client.post('/api/raytrace/direct',json=payload)
            self.assertEqual(first.status_code,200,first.text)
            second=client.post('/api/raytrace/direct',json=payload)
            self.assertEqual(second.status_code,200,second.text)
            self.assertEqual(first.json()['receiver_grids'],second.json()['receiver_grids'])
            self.assertAlmostEqual(first.json()['metrics']['_reflection_summary']['lobes']['bsdf']['emitted_flux_lumen'],.3)
            hits=first.json()['metrics']['_optical_summary']['profile_hits']['measured']
            self.assertEqual(hits['scatter_model'],'bsdf')
            self.assertAlmostEqual(hits['reflectance'],.3)

    def test_wavelength_interpolates_rt_without_using_legacy_reflectance(self):
        asset=synthetic_asset()
        master=next(f for f in asset['files'] if f['name']=='master.txt')
        master['text']+='\n'+master['text'].split('\n',1)[1].replace('Lambda = 550','Lambda = 650')
        rt=next(f for f in asset['files'] if f['name']=='rt.txt')
        rt['text']+='\nwv 650\nAOI 0 0 0.9\nAOI 90 0 0.9\n'
        fresh=import_lt_ascii_bsdf({'files':asset['files']})
        fresh['transport']={**asset['transport'],'wavelengthNm':600}
        self.assertAlmostEqual(compile_asset(fresh).reflectance_at(42),.6)
        fresh['transport']['wavelengthNm']=700
        with self.assertRaisesRegex(ValueError,'outside measured'):compile_asset(fresh)

    def test_rt_zero_absorbs_and_unity_preserves_energy(self):
        for reflectance in (0.,1.):
            result=run_direct_ray_trace(planar_trace_input(synthetic_asset(reflectance=reflectance),rays=300))
            summary=result.metrics['_reflection_summary']
            self.assertAlmostEqual(summary['lobes']['bsdf']['emitted_flux_lumen'],reflectance)
            self.assertLessEqual(summary['reflected_receiver_flux_lumen'],reflectance+1e-12)

    def test_constant_brdf_integrates_to_pi_and_samples_cosine_distribution(self):
        model = compile_asset(synthetic_asset())
        table = model.tables[0][0]
        self.assertAlmostEqual(table.normalization, math.pi, places=12)
        rng = random.Random(491)
        samples = np.array([model.sample_direction(rng,(0,0,-1),(0,0,1)) for _ in range(18000)])
        self.assertLess(abs(float(samples[:,2].mean())-2/3), .007)
        self.assertLess(abs(float((samples[:,2]**2).mean())-.5), .007)
        self.assertLess(abs(float(samples[:,0].mean())), .012)
        self.assertTrue(np.all(samples[:,2]>=0))
        self.assertLess(float(np.max(np.abs(np.linalg.norm(samples,axis=1)-1))), 1e-12)

    def test_oblique_frame_points_mirror_lobe_to_correct_side(self):
        model = compile_asset(synthetic_asset(lobe=True))
        incoming=(math.sin(math.pi/3),0,-.5)
        rng=random.Random(82)
        rays=np.array([model.sample_direction(rng,incoming,(0,0,1)) for _ in range(14000)])
        self.assertGreater(float(rays[:,0].mean()), .4)  # includes diffuse floor
        self.assertLess(abs(float(rays[:,1].mean())), .012)
        # Rotate all geometry 90 deg around Z; same random draws rotate outputs.
        rotate=lambda v:(-v[1],v[0],v[2])
        a,b=random.Random(9),random.Random(9)
        for _ in range(50):
            original=model.sample_direction(a,incoming,(0,0,1))
            rotated=model.sample_direction(b,rotate(incoming),(0,0,1))
            np.testing.assert_allclose(rotated,rotate(original),atol=2e-15)

    def test_rt_energy_replaces_analytic_values_and_grazing_boost(self):
        asset=synthetic_asset()
        profile=OpticalProfile(profile_id='x',reflectance=0.,scatter_model='none',bsdf_asset_id=asset['id'])
        bound,_=bind_bsdf_profiles([profile],[asset],RayTraceConfig())
        for angle in (0,30,60,85,89.99):
            direction=(math.sin(math.radians(angle)),0,-math.cos(math.radians(angle)))
            for boost in (True,False):
                self.assertAlmostEqual(effective_surface_reflectance(direction,(0,0,1),bound[0],boost),.3,places=13)
            self.assertEqual(sample_reflection_direction(random.Random(5),direction,(0,0,1),bound[0]).lobe,'bsdf')
        self.assertFalse(hasattr(profile,'_bsdf_transport'))
        self.assertNotIn('_bsdf_transport',bound[0].to_dict())

    def test_normalized_table_mixture_and_rt_interpolation_are_separate(self):
        asset=synthetic_asset(lobe=True)
        rt=next(f for f in asset['files'] if f['name']=='rt.txt')
        rt['text']=rt['text'].replace('AOI 0 0 0.3','AOI 0 0 0.1').replace('AOI 30 0 0.3','AOI 30 0 0.5')
        fresh=import_lt_ascii_bsdf({'files':asset['files']});fresh['transport']=asset['transport']
        model=compile_asset(fresh)
        self.assertAlmostEqual(model.reflectance_at(15),.3)
        self.assertEqual([w for t,w in model.mixture(15)],[.5,.5])
        self.assertEqual(len(model.mixture(89)),1)
        self.assertAlmostEqual(model.reflectance_at(90),.3)

    def test_cached_data_and_readiness_cannot_override_originals(self):
        asset=synthetic_asset();fake=copy.deepcopy(asset)
        fake['simulationReady']=True
        fake['data']['rt'][0]['reflectance']=1
        self.assertAlmostEqual(compile_asset(fake).reflectance_at(0),.3)
        fake['files'][0]['text'] += '\n# changed'
        with self.assertRaisesRegex(ValueError,'hash'):
            compile_asset(fake)
        del asset['transport']
        with self.assertRaisesRegex(ValueError,'옵션'):
            compile_asset(asset)

    def test_guard_blocks_unsupported_modes(self):
        asset=synthetic_asset();profile={'bsdf_asset_id':asset['id']}
        validate_bsdf_profiles([profile],[asset],{'compute_backend':'gpu_cuda'})
        with self.assertRaises(ValueError):
            validate_bsdf_profiles([profile],[asset],{'bounce_sampling_strategy':'receiver_mis'})
        asset['transport']['model']='shift_with_stretch'
        with self.assertRaisesRegex(ValueError,'isotropic_no_shift'):compile_asset(asset)

    def test_actual_engine_uses_bsdf_with_analytic_profile_disabled(self):
        asset=synthetic_asset()
        result=run_direct_ray_trace(planar_trace_input(asset))
        summary=result.metrics['_reflection_summary']
        self.assertEqual(summary['lobes']['bsdf']['emitted_count'],1200)
        self.assertAlmostEqual(summary['lobes']['bsdf']['emitted_flux_lumen'],.3,places=11)
        self.assertGreater(summary['reflected_receiver_flux_lumen'],.299)
        self.assertEqual(result.metrics['_performance_summary']['bsdf_transport'][0]['contract'],'bsdf_isotropic_no_shift_v1')
        self.assertEqual(result.metrics['_performance_summary']['compute_backend'],'cpu')
        self.assertTrue(any(p[-1].ray_kind=='bsdf' for p in result.stored_paths))

    def test_multibounce_uses_same_bsdf_and_energy_bookkeeping(self):
        trace=planar_trace_input(synthetic_asset(),rays=600,depth=3)
        # Top lid intercepts upward reflection before the (now distant) screen.
        v=[trace.mesh.add_vertex(p) for p in [(-100,-100,1.5),(100,-100,1.5),(100,100,1.5),(-100,100,1.5)]]
        trace.mesh.add_face(v[2],v[1],v[0],'measured');trace.mesh.add_face(v[3],v[2],v[0],'measured')
        result=run_direct_ray_trace(trace)
        depths=result.metrics['_reflection_summary']['depths']
        self.assertAlmostEqual(depths['1']['emitted_flux_lumen'],.3,places=11)
        self.assertGreater(depths['2']['emitted_count'],590)
        self.assertLessEqual(depths['2']['emitted_flux_lumen'],.090000001)
        self.assertLessEqual(depths['3']['emitted_flux_lumen'],.027000001)


if __name__=='__main__':unittest.main()
