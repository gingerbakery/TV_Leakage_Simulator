"""Measured table tests separate from the real-CUDA acceptance benchmark."""
from dataclasses import replace
import math
import unittest

import numpy as np

from test_bsdf_transport import synthetic_asset, planar_trace_input
from leakage_simulator.bsdf_guard import bind_bsdf_profiles
from leakage_simulator.bsdf_import import import_lt_ascii_bsdf
from leakage_simulator.bsdf_packed import pack_bsdf_profiles, validate_bsdf_indices, make_bsdf_functions
from leakage_simulator.native_cpu_counter_wavefront import (
    CounterWavefrontPlanInput, counter_uniform, plan_counter_reference,
    plan_counter_native_cpu, probe_native_cpu_counter_wavefront,
)
from leakage_simulator.native_cpu_wavefront import (
    SCATTER_BSDF, SCATTER_SPECULAR, LOBE_BSDF, LOBE_SPECULAR,
    TERMINATION_RUSSIAN_ROULETTE,
)
from leakage_simulator.types import OpticalProfile, RayTraceConfig


class PackedBsdfTests(unittest.TestCase):
    def pack(self, asset, copies=1):
        profiles=[OpticalProfile(profile_id='measured',reflectance=0,
            scatter_model='none',bsdf_asset_id=asset['id'])] * copies
        bound, _=bind_bsdf_profiles(profiles,[asset],RayTraceConfig())
        return pack_bsdf_profiles(bound)

    def batch(self, asset, n=200):
        packed, _=self.pack(asset)
        a=np.radians(np.linspace(0,89.99,n))
        return CounterWavefrontPlanInput(
            incoming_directions=np.column_stack((np.sin(a),np.zeros(n),-np.cos(a))),
            surface_normals=np.tile((0.,0.,1.),(n,1)),incoming_power_lumen=np.ones(n),
            profile_reflectance=np.zeros(n),profile_roughness=np.zeros(n),
            scatter_models=np.full(n,SCATTER_BSDF),profile_specular_ratio=np.ones(n),
            profile_gaussian_sigma_deg=np.ones(n),rng_keys=np.arange(n,dtype=np.uint64),
            depth=0,max_depth=3,min_energy=0,bsdf=packed,bsdf_indices=np.zeros(n,dtype=np.int64))

    def test_buffers_are_deduplicated_readonly_and_indices_fail_closed(self):
        packed, ids=self.pack(synthetic_asset(),copies=50)
        self.assertEqual(len(packed.materials),1)
        self.assertEqual(len(packed.tables),4)
        self.assertTrue(all(not a.flags.writeable for a in packed.arrays()))
        np.testing.assert_array_equal(ids,np.zeros(50,dtype=np.int64))
        for bad_ids,scatter in (([-1],[SCATTER_BSDF]),([0],[SCATTER_SPECULAR]),([1],[SCATTER_BSDF])):
            with self.assertRaises(ValueError):
                validate_bsdf_indices(packed,np.array(bad_ids),np.array(scatter),SCATTER_BSDF)

    def test_table_scale_does_not_change_rt_or_sample_distribution(self):
        asset=synthetic_asset(lobe=True)
        scaled=synthetic_asset(lobe=True)
        for f in scaled['files']:
            if f['name'].startswith('scatter-'):
                lines=f['text'].splitlines()
                f['text']=lines[0]+'\n'+'\n'.join(' '.join(str(float(x)*4) for x in l.split()) for l in lines[1:])
        fresh=import_lt_ascii_bsdf({'files':scaled['files']});fresh['transport']=asset['transport']
        a,b=plan_counter_reference(self.batch(asset)),plan_counter_reference(self.batch(fresh))
        np.testing.assert_allclose(a.emitted_directions,b.emitted_directions,atol=1e-14,rtol=0)
        np.testing.assert_array_equal(a.reflected_power_lumen,b.reflected_power_lumen)

    def test_wavelength_and_aoi_rt_are_interpolated_independently(self):
        asset=synthetic_asset(lobe=True)
        master=next(f for f in asset['files'] if f['name']=='master.txt')
        master['text']+='\n'+master['text'].split('\n',1)[1].replace('Lambda = 550','Lambda = 650')
        rt=next(f for f in asset['files'] if f['name']=='rt.txt')
        rt['text']=rt['text'].replace('AOI 30 0 0.3','AOI 30 0 0.5')
        rt['text']+='\nwv 650\nAOI 0 0 0.7\nAOI 90 0 0.9\n'
        asset=import_lt_ascii_bsdf({'files':asset['files']})
        asset['transport']={'model':'isotropic_no_shift','wavelengthNm':600,'aoiExtrapolation':'clamp'}
        packed,_=self.pack(asset)
        reflect,_=make_bsdf_functions(lambda f:f,counter_uniform)
        self.assertAlmostEqual(reflect(0,math.cos(math.radians(15)),*packed.arrays()),(.4+( .7+.2/6))/2)
        self.assertAlmostEqual(reflect(0,0.,*packed.arrays()),.6)

    def test_numba_matches_python_with_mixed_lobes_and_roulette(self):
        if not probe_native_cpu_counter_wavefront().available:self.skipTest('Numba unavailable')
        batch=self.batch(synthetic_asset(lobe=True))
        ids=batch.bsdf_indices.copy();ids[::3]=-1
        scatter=batch.scatter_models.copy();scatter[::3]=SCATTER_SPECULAR
        batch=replace(batch,bsdf_indices=ids,scatter_models=scatter,
            profile_reflectance=np.full(len(batch),.3),min_energy=.4,
            termination_mode=TERMINATION_RUSSIAN_ROULETTE)
        reference=plan_counter_reference(batch)
        native=plan_counter_native_cpu(batch).result
        for field in ('lobe_codes','status_flags','rng_draw_counts'):
            np.testing.assert_array_equal(getattr(native,field),getattr(reference,field))
        np.testing.assert_allclose(native.emitted_directions,reference.emitted_directions,atol=1e-13,rtol=1e-13)
        np.testing.assert_array_equal(native.emitted_power_lumen,reference.emitted_power_lumen)
        self.assertIn(LOBE_BSDF,native.lobe_codes)
        self.assertIn(LOBE_SPECULAR,native.lobe_codes)

    def test_lambertian_moments_and_rt_zero_unity(self):
        batch=self.batch(synthetic_asset(),n=20000)
        # Angles vary; a constant BRDF remains cosine distributed about the normal.
        result=plan_counter_reference(batch)
        z=result.emitted_directions[:,2]
        self.assertLess(abs(z.mean()-2/3),.008)
        self.assertLess(abs((z*z).mean()-.5),.008)
        np.testing.assert_allclose(np.linalg.norm(result.emitted_directions,axis=1),1.,atol=1e-14)
        for r in (0.,1.):
            result=plan_counter_reference(self.batch(synthetic_asset(reflectance=r)))
            np.testing.assert_array_equal(result.emitted_power_lumen,np.full(200,r))


if __name__=='__main__':unittest.main()
