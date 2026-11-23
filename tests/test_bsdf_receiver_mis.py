"""CPU evidence for measured-distribution MIS, independent of CUDA hardware."""
from dataclasses import replace
import math

import numpy as np

from test_bsdf_packed import PackedBsdfTests
from test_bsdf_transport import synthetic_asset
from test_perf4e_bounce_mis import _build_reflected_case
from leakage_simulator.bsdf_transport import compile_asset, incident_frame
from leakage_simulator.bsdf_import import import_lt_ascii_bsdf
from leakage_simulator.bsdf_packed import make_bsdf_functions
from leakage_simulator.native_cpu_counter_wavefront import (
    counter_uniform, plan_counter_reference, plan_counter_native_cpu,
    _receiver_direction_pdf_reference,
)
from leakage_simulator.raytracer import run_direct_ray_trace


def batch(n=256, asset=None, fraction=.5):
    source = PackedBsdfTests().batch(asset or synthetic_asset(), n)
    return replace(source, incoming_directions=np.tile((0., 0., -1.), (n, 1)),
        surface_points=np.zeros((n, 3)), receiver_centers=np.array([(0., 0., 20.)]),
        receiver_normals=np.array([(0., 0., -1.)]),
        receiver_u_axes=np.array([(1., 0., 0.)]), receiver_v_axes=np.array([(0., 1., 0.)]),
        receiver_half_widths=np.array([.5]), receiver_half_heights=np.array([.5]),
        receiver_minimum_cosines=np.array([0.]), receiver_importance_fraction=fraction)


def test_pdf_matches_independent_bilinear_tables_and_normalizes():
    asset = synthetic_asset(lobe=True)
    model = compile_asset(asset)
    packed, _ = PackedBsdfTests().pack(asset)
    _, _, pdf = make_bsdf_functions(lambda f: f, counter_uniform, include_pdf=True)
    for aoi in (0., 17., 30., 71.84, 89.99):
        incoming = (math.sin(math.radians(aoi)), 0., -math.cos(math.radians(aoi)))
        normal = (0., 0., 1.)
        angle, x, y, normal = incident_frame(incoming, normal)
        nodes, weights = np.polynomial.legendre.leggauss(48)
        integral = 0.
        # Midpoints avoid poles, with a separate seam/boundary check below.
        for cosine, weight in zip((nodes+1)/2, weights/2):
            theta = math.acos(cosine)
            for phi in np.linspace(0, 2*math.pi, 96, endpoint=False):
                outgoing = tuple(math.sin(theta)*math.cos(phi)*x[k]
                    + math.sin(theta)*math.sin(phi)*y[k]+cosine*normal[k] for k in range(3))
                expected = cosine * sum(w*t.relative_value(theta, phi)/t.normalization
                    for t, w in model.mixture(angle))
                actual = pdf(0, *incoming, *normal, *outgoing, *packed.arrays())
                np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-13)
                integral += actual * weight * 2*math.pi/96
        assert abs(integral-1) < .002
    assert pdf(0, 0., 0., -1., 0., 0., 1., 0., 0., -1., *packed.arrays()) == 0


def test_measured_mis_native_matches_reference_and_source_is_unchanged():
    scene = batch(96, synthetic_asset(lobe=True))
    ref = plan_counter_reference(scene)
    native = plan_counter_native_cpu(scene).result
    for name in ('status_flags', 'lobe_codes', 'importance_eligible_mask',
                 'importance_directed_mask', 'rng_draw_counts'):
        np.testing.assert_array_equal(getattr(ref, name), getattr(native, name))
    for name in ('emitted_power_lumen', 'emitted_directions', 'importance_weights'):
        np.testing.assert_allclose(getattr(ref, name), getattr(native, name), rtol=2e-12, atol=1e-14)
    assert ref.importance_eligible_mask.all()
    assert 20 < ref.importance_directed_mask.sum() < 76
    assert ref.importance_weights.max() <= 2.
    assert native.sampling_pdfs is None
    capture = replace(scene, capture_sampling_pdfs=True)
    captured_native = plan_counter_native_cpu(capture).result
    captured_reference = plan_counter_reference(capture)
    np.testing.assert_array_equal(captured_native.emitted_power_lumen, native.emitted_power_lumen)
    np.testing.assert_array_equal(captured_native.emitted_directions, native.emitted_directions)
    np.testing.assert_allclose(captured_native.sampling_pdfs, captured_reference.sampling_pdfs, rtol=2e-12, atol=1e-14)
    base, mixture = captured_native.sampling_pdfs.T
    np.testing.assert_allclose(np.divide(base, mixture, out=np.zeros_like(base), where=mixture > 0),
                               captured_native.importance_weights, rtol=2e-12, atol=1e-14)
    zero = replace(scene, receiver_importance_fraction=0.)
    plain = replace(zero, receiver_centers=None, receiver_normals=None, receiver_u_axes=None,
        receiver_v_axes=None, receiver_half_widths=None, receiver_half_heights=None,
        receiver_minimum_cosines=None, surface_points=None)
    np.testing.assert_array_equal(plan_counter_reference(zero).emitted_directions,
                                  plan_counter_reference(plain).emitted_directions)


def test_pdf_uses_full_wavelength_aoi_mixture_in_rotated_frame():
    files = synthetic_asset()['files']
    other = synthetic_asset(lobe=True, wavelength=650)['files']
    for item in other:
        if item['name'].startswith('scatter-'):
            files.append(dict(name='wave650-'+item['name'], text=item['text']))
    master = next(f for f in files if f['name'] == 'master.txt')
    master['text'] += '\n' + next(f['text'] for f in other if f['name'] == 'master.txt').split('\n', 1)[1].replace('scatter-', 'wave650-scatter-')
    rt = next(f for f in files if f['name'] == 'rt.txt')
    other_rt = next(f['text'] for f in other if f['name'] == 'rt.txt')
    rt['text'] += '\nwv 650\n' + other_rt.split('wv 650\n', 1)[1]
    asset = import_lt_ascii_bsdf({'files': files})
    asset['transport'] = dict(model='isotropic_no_shift', wavelengthNm=600, aoiExtrapolation='clamp')
    model = compile_asset(asset)
    packed, _ = PackedBsdfTests().pack(asset)
    _, _, pdf = make_bsdf_functions(lambda f: f, counter_uniform, include_pdf=True)
    normal = np.array((.2, .3, 1.)); normal /= np.linalg.norm(normal)
    tangent = np.cross(normal, (1., 0., 0.)); tangent /= np.linalg.norm(tangent)
    incoming = math.sin(.4)*tangent-math.cos(.4)*normal
    angle, x, y, normal = incident_frame(incoming, normal)
    for theta in (0., .01, .37, .8, 1.5):
        for phi in (0., .7, 2., 6.28):
            d = [math.sin(theta)*math.cos(phi)*x[k]+math.sin(theta)*math.sin(phi)*y[k]+math.cos(theta)*normal[k] for k in range(3)]
            expected = math.cos(theta)*sum(w*t.relative_value(theta, phi)/t.normalization for t, w in model.mixture(angle))
            assert math.isclose(pdf(0, *incoming, *normal, *d, *packed.arrays()), expected, rel_tol=2e-12, abs_tol=1e-14)


def test_sensor_proposal_cannot_create_light_outside_bsdf_support():
    files = synthetic_asset()['files']
    for item in files:
        if item['name'].startswith('scatter-'):
            lines = item['text'].splitlines()
            for row in range(26, len(lines)):
                lines[row] = ' '.join('0' for _ in lines[row].split())
            item['text'] = '\n'.join(lines)
    asset = import_lt_ascii_bsdf({'files': files})
    asset['transport'] = dict(model='isotropic_no_shift', wavelengthNm=550, aoiExtrapolation='clamp')
    scene = batch(128, asset)
    result = plan_counter_native_cpu(scene).result
    directed = result.importance_directed_mask
    assert directed.any()
    assert np.all(result.emitted_power_lumen[directed] == 0)
    assert result.importance_zero_weight_mask[directed].all()


def test_area_proposal_pdf_ignores_detection_filters_and_adds_overlap():
    scene = batch(1)
    density = _receiver_direction_pdf_reference(scene, (0., 0., 0.), (0., 0., 1.))
    assert density == 400.
    # Sampling still generates these directions even when detection rejects them.
    reversed_normal = replace(scene, receiver_normals=-scene.receiver_normals,
        receiver_minimum_cosines=np.ones(1))
    assert _receiver_direction_pdf_reference(reversed_normal, (0., 0., 0.), (0., 0., 1.)) == density
    overlap = replace(scene, **{key: np.repeat(getattr(scene, key), 2, axis=0) for key in (
        'receiver_centers', 'receiver_normals', 'receiver_u_axes', 'receiver_v_axes',
        'receiver_half_widths', 'receiver_half_heights', 'receiver_minimum_cosines')})
    assert _receiver_direction_pdf_reference(overlap, (0., 0., 0.), (0., 0., 1.)) == density


def test_lobed_bsdf_sensor_flux_matches_independent_table_integration():
    asset = synthetic_asset(lobe=True)
    model = compile_asset(asset)
    incoming = (math.sin(math.radians(17)), 0., -math.cos(math.radians(17)))
    angle, x, y, normal = incident_frame(incoming, (0., 0., 1.))
    nodes, weights = np.polynomial.legendre.leggauss(16)
    expected = 0.
    for u, wu in zip(nodes/2, weights/2):
        for v, wv in zip(nodes/2, weights/2):
            distance = math.sqrt(u*u+v*v+400)
            direction = np.array((u, v, 20.))/distance
            theta = math.acos(direction[2])
            phi = math.atan2(np.dot(direction, y), np.dot(direction, x))
            density = direction[2]*sum(w*t.relative_value(theta, phi)/t.normalization
                for t, w in model.mixture(angle))
            expected += wu*wv*.3*density*20/distance**3
    scene = batch(16384, asset)
    scene = replace(scene, incoming_directions=np.tile(incoming, (len(scene), 1)))
    result = plan_counter_native_cpu(scene).result
    d = result.emitted_directions
    xy = d[:, :2]*(20/d[:, 2])[:, None]
    hit = (d[:, 2] > 0) & (abs(xy[:, 0]) <= .5) & (abs(xy[:, 1]) <= .5)
    assert abs((result.emitted_power_lumen*hit).mean()/expected-1) < .025


def test_mis_preserves_analytic_flux_and_reduces_variance_and_concentration():
    # Uniform measured BRDF: independent finite-area quadrature gives exact flux.
    nodes, weights = np.polynomial.legendre.leggauss(24)
    expected = .3/math.pi * sum(wx*wy/4 * 400/(400+(x/2)**2+(y/2)**2)**2
        for x, wx in zip(nodes, weights) for y, wy in zip(nodes, weights))
    means, variances, ess = {}, {}, {}
    for strategy, n, fraction in (('source', 262144, 0.), ('mis', 32768, .5)):
        scene = batch(n, fraction=fraction)
        result = plan_counter_native_cpu(scene).result
        d = result.emitted_directions
        xy = d[:, :2] * (20/d[:, 2])[:, None]
        hit = (d[:, 2] > 0) & (abs(xy[:, 0]) <= .5) & (abs(xy[:, 1]) <= .5)
        contribution = result.emitted_power_lumen * hit
        means[strategy] = contribution.mean()
        variances[strategy] = contribution.var()
        ess[strategy] = contribution.sum()**2/np.square(contribution).sum()
    assert abs(means['mis']/expected-1) < .02
    assert abs(means['source']/expected-1) < .3
    assert variances['mis'] < variances['source']/100
    assert ess['mis'] > 1000 and ess['mis'] > ess['source']*10


def test_production_bsdf_mis_respects_occlusion_and_replays_detected_paths():
    from leakage_simulator.comparison_path_evidence import detected_paths, pair_detected_paths
    asset = synthetic_asset()
    for blocked in (False, True):
        scene = _build_reflected_case(512, 123, 'receiver_mis', with_blocker=blocked)
        scene = replace(scene, bsdf_assets=[asset], optical_profiles=[
            replace(scene.optical_profiles[0], bsdf_asset_id=asset['id']), scene.optical_profiles[1]],
            config=replace(scene.config, receiver_diagnostics=True, min_energy=0.,
                store_ray_paths=True, max_stored_paths=16))
        result = run_direct_ray_trace(scene)
        if blocked:
            assert result.receiver_hit_count == 0
        else:
            assert result.receiver_hit_count > 100
            capture = detected_paths(result, 'observer')
            pair = pair_detected_paths(scene, scene, capture, 1)
            assert pair['records'][0]['state'] == 'verified', pair
            assert pair['records'][0]['reason'] == 'original_also_detected'


def test_preview_guidance_uses_bsdf_reflectance_on_actual_surface_hits():
    scene = _build_reflected_case(8192, 123, "source")
    receiver = replace(scene.receivers[0],
        observer_reference_path=[(0., 0., 0.), (0., 0., 10.), (0., 0., -10.)],
        path_guidance_percent=50)
    scene = replace(scene, receivers=[receiver],
        emitters=[replace(scene.emitters[0], direction_distribution="lambertian")],
        config=replace(scene.config, min_energy=0.))

    fluxes = []
    for reflectance in (.3, .6):
        asset = synthetic_asset(reflectance=reflectance)
        reflector = replace(scene.optical_profiles[0], reflectance=0., bsdf_asset_id=asset["id"])
        result = run_direct_ray_trace(replace(scene, bsdf_assets=[asset],
            optical_profiles=[reflector, scene.optical_profiles[1]]))
        performance = result.metrics["_performance_summary"]
        assert performance["primary_sampling_strategy"] == "preview_path_mis"
        assert 3800 < performance["primary_sampling_receiver_directed_ray_count"] < 4400
        assert result.receiver_hit_count > 0
        fluxes.append(result.metrics["observer"]["total_flux_lumen"])

    assert fluxes[0] > 0
    assert math.isclose(fluxes[1], 2 * fluxes[0], rel_tol=1e-12)
