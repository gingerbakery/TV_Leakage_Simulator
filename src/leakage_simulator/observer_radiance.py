"""Parallel-camera photometric reference estimator, separate from path counts.

The finite-depth, reflection-only model is an estimate, not a visibility proof.
NEE and continuation use balance-heuristic MIS; delta hits are kept separately.
"""
from __future__ import annotations

from functools import lru_cache
import copy
import math
import random
import time

import numpy as np

from .aim_sampling import aim_sphere_rotation
from .bsdf_transport import incident_frame
from .geometry import vec_add, vec_sub, vec_mul, vec_dot, vec_norm, vec_cross
from .reflection import (effective_surface_reflectance, ideal_specular_direction,
                         sample_cosine_weighted_hemisphere, orthonormal_basis)

CONTRACT = "observer_radiance_parallel_v1"


def prefer_observer_path(depth, contribution, previous, previous_contribution):
    """Preserve any physically sampled direct path before ranking by strength.

    This changes diagnostic path choice only, never the total radiance sum.
    Within either class retain the largest single contribution.
    """
    if contribution <= 0:
        return False
    if not previous:
        return True
    direct, previous_direct = depth == 0, len(previous) == 2
    return direct if direct != previous_direct else contribution > previous_contribution


def _angle(a, b):
    cross = vec_cross(a, b)
    return math.atan2(math.sqrt(vec_dot(cross, cross)), vec_dot(a, b))


@lru_cache(maxsize=1)
def _quadrature():
    return np.polynomial.legendre.leggauss(96)


@lru_cache(maxsize=8192)
def gaussian_acceptance(sigma_deg, cosine_axis):
    """Probability that one legacy Rayleigh proposal survives its two tests.

    Integrate in Rayleigh CDF space to resolve narrow lobes. Split where the
    surface clips azimuths; this avoids integrating across a square-root kink.
    """
    sigma = math.radians(max(1e-6, sigma_deg))
    cosine_axis = max(0., min(1., cosine_axis))
    gamma = math.acos(cosine_axis)
    limit = min(math.pi/2, sigma*math.sqrt(-2*math.log(1e-12)))
    split = max(0., min(limit, math.asin(cosine_axis)-1e-9))
    edges = [0., -math.expm1(-split*split/(2*sigma*sigma)),
             -math.expm1(-limit*limit/(2*sigma*sigma))]
    nodes, weights = _quadrature()
    total = 0.
    for low, high in zip(edges, edges[1:]):
        if high <= low:
            continue
        cdf = low + (nodes+1)*(high-low)/2
        theta = sigma*np.sqrt(-2*np.log1p(-cdf))
        denominator = math.sin(gamma)*np.sin(theta)
        numerator = 1e-9-cosine_axis*np.cos(theta)
        ratio = np.divide(numerator, denominator, out=np.full_like(theta, -1.), where=denominator > 1e-30)
        fraction = np.arccos(np.clip(ratio, -1., 1.))/math.pi
        if gamma == 0:
            fraction = (np.cos(theta) > 1e-9).astype(float)
        total += float(weights @ fraction)*(high-low)/2
    return max(0., min(1., total))


def _lobe_weights(profile):
    if profile.scatter_model == "lambertian":
        return 1., 0., 0.
    if profile.scatter_model == "specular":
        return 0., 0., 1.
    if profile.scatter_model == "gaussian":
        return 0., 1., 0.
    if profile.scatter_model == "mixed":
        if profile.gaussian_sigma_deg <= .01:
            return 1-profile.specular_ratio, 0., profile.specular_ratio
        return 1-profile.specular_ratio, profile.specular_ratio, 0.
    return 0., 0., 0.


def brdf(profile, normal, wi, wo, angle_dependent=True):
    """Forward BRDF with wi/wo both pointing away from the surface."""
    ci, co = vec_dot(normal, wi), vec_dot(normal, wo)
    if ci <= 0 or co <= 0:
        return 0.
    incoming = vec_mul(wi, -1)
    reflectance = effective_surface_reflectance(incoming, normal, profile, angle_dependent)
    if profile.bsdf_asset_id:
        model = getattr(profile, "_bsdf_transport", None)
        if model is None:
            raise ValueError("Observer radiance requires compiled BSDF; no fallback")
        angle, x, y, n = incident_frame(incoming, normal)
        theta = math.acos(max(-1., min(1., vec_dot(wo, n))))
        phi = math.atan2(vec_dot(wo, y), vec_dot(wo, x))
        return reflectance*sum(w*t.relative_value(theta, phi)/t.normalization for t, w in model.mixture(angle))
    diffuse, gaussian, _ = _lobe_weights(profile)
    value = diffuse/math.pi
    if gaussian:
        axis = ideal_specular_direction(incoming, normal)
        theta = _angle(axis, wo)
        if theta < math.pi/2 and co > 1e-9:
            sigma = math.radians(max(1e-6, profile.gaussian_sigma_deg))
            # The sampler truncates the radial U at 1e-12.
            if theta < sigma*math.sqrt(-2*math.log(1e-12)):
                acceptance = gaussian_acceptance(profile.gaussian_sigma_deg, ci)
                if acceptance > 0:
                    raw = math.exp(-theta*theta/(2*sigma*sigma))/(2*math.pi*sigma*sigma)
                    raw *= theta/math.sin(theta) if theta > 1e-8 else 1.
                    value += gaussian*raw*(-math.expm1(32*math.log1p(-acceptance)) if acceptance < 1 else 1.)/acceptance/co
    return reflectance*value


def delta_weight(profile, normal, wi, angle_dependent=True):
    if profile.bsdf_asset_id:
        return 0.
    _, gaussian, delta = _lobe_weights(profile)
    if gaussian:
        acceptance = gaussian_acceptance(profile.gaussian_sigma_deg, vec_dot(normal, wi))
        delta += gaussian*(1-acceptance)**32
    return effective_surface_reflectance(vec_mul(wi, -1), normal, profile, angle_dependent)*delta


def source_flux(emitter, area_mm2):
    return emitter.effective_power_lumen(area_mm2)


def balance(a, b):
    if a <= 0:
        return 0.
    scale = max(a, b)
    return (a/scale)/(a/scale+b/scale)


def continuation_pdf(profile, normal, wo, wi, angle_dependent):
    """Unconditional continuous proposal PDF in sr^-1 (delta is separate)."""
    cosine = vec_dot(normal, wi)
    if cosine <= 0:
        return 0.
    reflectance = effective_surface_reflectance(vec_mul(wo, -1), normal, profile, angle_dependent)
    mirror = ideal_specular_direction(vec_mul(wo, -1), normal)
    delta = delta_weight(profile, normal, mirror, angle_dependent)
    delta_probability = min(1., delta/reflectance) if reflectance > 0 else 0.
    q = cosine/math.pi
    _, gaussian, _ = _lobe_weights(profile)
    if profile.bsdf_asset_id and reflectance > 0:
        q = .5*q + .5*cosine*brdf(profile, normal, wo, wi, angle_dependent)/reflectance
    elif gaussian:
        sigma = math.radians(max(1e-6, profile.gaussian_sigma_deg))
        limit = min(math.pi/2, sigma*math.sqrt(-2*math.log(1e-12)))
        theta = _angle(mirror, wi)
        acceptance = gaussian_acceptance(profile.gaussian_sigma_deg, vec_dot(normal, wo))
        qg = 0.
        if theta < limit and cosine > 1e-9 and acceptance > 0:
            qg = math.exp(-theta*theta/(2*sigma*sigma))/(2*math.pi*sigma*sigma*acceptance)
            qg *= theta/math.sin(theta) if theta > 1e-8 else 1.
        q = .5*q+.5*qg
    return (1-delta_probability)*q


def continuation(rng, profile, normal, wo, angle_dependent):
    """Sample a known reverse proposal; evaluate its weight forward.

    A broad component preserves support for nonreciprocal measured data.
    Gaussian importance proposals resolve narrow lobes without promoting a
    finite-width lobe to an ideal specular reflection.
    """
    mirror = ideal_specular_direction(vec_mul(wo, -1), normal)
    reflectance = effective_surface_reflectance(vec_mul(wo, -1), normal, profile, angle_dependent)
    delta = delta_weight(profile, normal, mirror, angle_dependent)
    delta_probability = min(1., delta/reflectance) if reflectance > 0 else 0.
    if rng.random() < delta_probability:
        return mirror, delta/delta_probability, True, 0.
    if delta_probability == 1:
        return mirror, 0., True, 0.
    _, gaussian, _ = _lobe_weights(profile)
    if profile.bsdf_asset_id and reflectance > 0:
        if rng.random() < .5:
            wi = profile._bsdf_transport.sample_direction(rng, vec_mul(wo, -1), normal)
        else:
            wi = sample_cosine_weighted_hemisphere(rng, normal)
        wi = vec_norm(wi)
        cosine = max(0., vec_dot(normal, wi))
        q = .5*cosine/math.pi + .5*cosine*brdf(profile, normal, wo, wi, angle_dependent)/reflectance
    elif gaussian:
        sigma = math.radians(max(1e-6, profile.gaussian_sigma_deg))
        limit = min(math.pi/2, sigma*math.sqrt(-2*math.log(1e-12)))
        mass = -math.expm1(-limit*limit/(2*sigma*sigma))
        acceptance = gaussian_acceptance(profile.gaussian_sigma_deg, vec_dot(normal, wo))
        if rng.random() < .5 and acceptance > 0:
            u, v, _ = orthonormal_basis(mirror)
            while True:
                theta = sigma*math.sqrt(-2*math.log1p(-rng.random()*mass))
                phi = 2*math.pi*rng.random()
                wi = vec_norm(tuple(mirror[i]*math.cos(theta) + math.sin(theta)*(
                    u[i]*math.cos(phi)+v[i]*math.sin(phi)) for i in range(3)))
                if vec_dot(normal, wi) > 1e-9:
                    break
        else:
            wi = vec_norm(sample_cosine_weighted_hemisphere(rng, normal))
        theta = _angle(mirror, wi)
        qg = 0.
        if theta < limit and vec_dot(normal, wi) > 1e-9 and acceptance > 0:
            qg = math.exp(-theta*theta/(2*sigma*sigma))/(2*math.pi*sigma*sigma*acceptance)
            qg *= theta/math.sin(theta) if theta > 1e-8 else 1.
        cosine = max(0., vec_dot(normal, wi))
        q = .5*cosine/math.pi + .5*qg
    else:
        wi = vec_norm(sample_cosine_weighted_hemisphere(rng, normal))
        cosine = max(0., vec_dot(normal, wi))
        q = cosine/math.pi
    f = brdf(profile, normal, wi, wo, angle_dependent)
    pdf = (1-delta_probability)*q
    return wi, f*cosine/pdf if pdf > 0 else 0., False, pdf


def validate_sources(sources):
    for emitter, _, _, _ in sources:
        aim = emitter.aim
        if aim is not None and aim.enabled:
            if aim.mode != "sphere" or aim.distribution == "gaussian" or aim.sphere_upper_deg == aim.sphere_lower_deg:
                raise ValueError(f"{emitter.emitter_id}: 휘도 역추적은 유한 범위 Sphere Uniform/Lambertian을 지원합니다. 이 Aim은 경로 탐색을 사용하세요.")
        elif emitter.direction_distribution not in ("lambertian", "isotropic"):
            raise ValueError(f"{emitter.emitter_id}: 휘도 역추적은 기본 Lambertian/Isotropic을 지원합니다. 이 배광은 경로 탐색을 사용하세요.")


def emission_radiance(source, point, normal, outgoing):
    """Photometric radiance of the SAME area/angle flux law as the emitter.

    set_luminance retains the existing pi*L*A flux contract, including for
    non-Lambertian sources; it is not a promise of constant directional L.
    """
    emitter, _, _, area = source
    original_normal = normal
    if emitter.normal_mode == "custom" and emitter.custom_normal is not None:
        normal = vec_norm(emitter.custom_normal)
    if emitter.normal_flip:
        normal = vec_mul(normal, -1)
    cosine = vec_dot(normal, outgoing)
    aim = emitter.aim
    if aim is not None and aim.enabled:
        axis = tuple(row[2] for row in aim_sphere_rotation(aim))
        c = max(-1., min(1., vec_dot(axis, outgoing)))
        low, high = map(math.radians, (aim.sphere_upper_deg, aim.sphere_lower_deg))
        if not low-1e-12 <= _angle(axis, outgoing) <= high+1e-12:
            return 0.
        if aim.distribution == "lambertian":
            high = min(high, math.pi/2)
            mass = math.pi*math.sin(high+low)*math.sin(high-low)
            density = max(0., c)/mass if mass > 0 and c > 0 else 0.
        else:
            mass = 4*math.pi*math.sin((high+low)/2)*math.sin((high-low)/2)
            if mass <= 0:
                raise ValueError("Aim Sphere interval has no resolvable angular mass")
            density = 1/mass
    elif emitter.direction_distribution == "isotropic":
        density = 1/(4*math.pi)
    else:
        if emitter.emission_direction == "both":
            density = abs(cosine)/(2*math.pi)
        else:
            density = max(0., cosine)/math.pi
    # Area is geometric, even when a custom angular axis is selected.
    projected = abs(vec_dot(original_normal, outgoing))
    if projected <= 1e-14 or density <= 0:
        return 0.
    return source_flux(emitter, area)/(area*1e-6)*density/projected


def unstarted_summary(spec, plans, requested):
    """Valid partial contract while a resumable Preview is still tracing sources."""
    return dict(contract=CONTRACT,measure="luminance_cd_m2",sample_contract="observer_per_ray_seed_batch_v1",
        search_contract="observer_full_spacing_v1" if spec.get("search_mode") == "full_detail" else "observer_grid_v1",
        progress_contract="committed_samples_v1",resume_contract="live_observer_wave_v1",
        execution_backend="cpu",requested_backend=requested,
        performance=dict(compute_execution_state="gpu_requested_cpu_only" if requested == "gpu_cuda" else "cpu",
            compute_execution_reason="observer_not_started"),settings=spec,
        attempted_count=0,planned_count=sum(p["grid_samples"] for p in plans),direct_count=0,reflected_count=0,
        excluded_count=0,filtered_count=0,depth_limited_count=0,stopped=True,complete=False,runtime_sec=0.,views=[],cells=[],
        calibration_state="uncalibrated",observation_state="not_observed",display_truncated=False,
        limitations=["parallel_camera","finite_sampling","reflection_only","uncalibrated_source_material_threshold","forward_incomplete","partial_result"])


def trace_observer_radiance(trace_input, collector, resolved, should_stop=None,
                            *, dispatch="batch", batch_size=8192, progress_callback=None, pause_callback=None, sensor=None):
    from .observer_probe import (_source_geometry, _sample_source, _triangle_intersection,
                                 sampling_plan, _execute_probes, FULL_DETAIL_CELL_LIMIT)
    from .raytracer import _IntersectionDispatchStats
    started = time.perf_counter()
    spec, mesh, config = trace_input.observer_probe, trace_input.mesh, trace_input.config
    epsilon = max(config.epsilon_mm, 1e-8)
    sources = _source_geometry(mesh, trace_input.emitters)
    validate_sources(sources)
    plans = sensor.plans() if sensor is not None else sampling_plan(spec, collector.minimum, collector.maximum, collector.direction, 0, epsilon)
    planned = sum(p["grid_samples"] for p in plans)
    requested = getattr(config, "compute_backend", "cpu")
    resident = None
    resident_reason = None
    resident_batches = 0
    resident_cpu_batches = 0
    if requested == "gpu_cuda":
        from .gpu_cuda_observer import ResidentObserver, ObserverGpuUnsupported
        try:
            resident = ResidentObserver(trace_input, sources, resolved, **({"sensor": sensor} if sensor is not None else {}))
            # Allowed predicates are per sample/segment; preserve that path
            # whenever regions exist. Otherwise reduce ordered cells in NumPy
            # and download one representative path per partial cell.
            resident.compact_cells = sensor is None and not collector.regions
        except Exception as exc:
            # Measured BSDF retains its exact host optical evaluator while
            # every primary/continuation/shadow intersection uses CUDA BVH.
            # Only this declared unsupported optical law may choose hybrid;
            # CUDA initialization/kernel failures still fail closed here.
            bsdf_hybrid = isinstance(exc, ObserverGpuUnsupported) and str(exc) == "observer_resident_bsdf_requires_host"
            if sensor is not None and not bsdf_hybrid:
                raise ValueError(f"GPU 센서 역추적 준비 실패: {exc}") from exc
            # Preserve the exact reference optical model on unsupported scenes.
            resident_reason = str(exc) or type(exc).__name__
            if bsdf_hybrid:
                print("[OBSERVER] BSDF hybrid | exact CPU optical evaluation | CUDA BVH wavefront requested", flush=True)
            else:
                print(f"[OBSERVER] GPU resident unavailable | {resident_reason} | using host transport", flush=True)
    stats = _IntersectionDispatchStats(intersection_backend=config.intersection_backend,
        requested_provider="gpu_cuda" if requested == "gpu_cuda" else "python_cpu")
    faces, virtual = {}, []
    for source in sources:
        for triangle, normal, face in source[1]:
            if face is None or mesh.metadata(face).get("trace_excluded", False):
                virtual.append((source, triangle, normal))
            else:
                faces.setdefault(face, []).append(source)
    center = tuple((collector.minimum[i]+collector.maximum[i])/2 for i in range(3))
    samples = int(spec["samples_per_cell"])
    threshold = spec.get("min_luminance_nit", 0.)
    attempted = depth_limited = excluded = filtered = 0
    committed = committed_depth_limited = pause_count = 0
    sensor_sum = sensor_square_sum = 0.
    sensor_positive = 0
    stopped = display_truncated = False
    cells, views = [], []
    cell_limit = min(16_384, max(1, FULL_DETAIL_CELL_LIMIT//len(plans)))
    if progress_callback:
        progress_callback(0, planned)

    def view_summary(plan, view_cells):
        columns, rows = plan["columns"], plan["rows"]
        outward = plan["outward"]
        return dict(horizontal_deg=plan["horizontal"], vertical_deg=plan["vertical"],
            direction=outward, u_axis=plan["u"], v_axis=plan["v"], observer_center=vec_add(center, vec_mul(outward, plan["distance"])),
            spacing_mm=[plan["width"]/columns, plan["height"]/rows],
            sample_spacing_mm=[plan["width"]/columns, plan["height"]/rows],
            sampling_resolution=[columns, rows], grid_resolution=[columns, rows], display_stride=1,
            direct_count=sum(c["direct_count"] for c in view_cells), reflected_count=sum(c["reflected_count"] for c in view_cells),
            peak_luminance_nit=max((c["luminance_nit"] for c in view_cells), default=0.))

    def result(out_cells, out_views, is_stopped):
        performance = stats.to_summary()
        state, reason = stats.compute_execution_diagnosis(gpu_compute_requested=requested == "gpu_cuda")
        if state == "gpu_active" and resident is None:
            state, reason = "gpu_mixed", "observer_transport_cpu_host_roundtrip"
        performance.update(compute_execution_state=state, compute_execution_reason=reason)
        performance.update(observer_transport_contract="strict_float64_observer_resident_v1" if resident_batches else "observer_host_wavefront_v1",
            observer_bsdf_evaluation_backend="cpu" if resident_reason == "observer_resident_bsdf_requires_host" else None,
            observer_resident_success_count=resident_batches, observer_resident_cpu_batch_count=resident_cpu_batches,
            observer_resident_fallback_reason=resident_reason,
            observer_kernel_timing_scope="launch_sync_including_first_jit" if resident_batches else None,
            observer_resident_prepare_sec=getattr(resident, "prepare_sec", 0.),
            observer_resident_runtime_guard_sec=getattr(resident, "validation_sec", 0.),
            observer_sample_contract="observer_counter_latin_v1" if resident is not None else "observer_per_ray_seed_batch_v1")
        return dict(contract=CONTRACT, measure="luminance_cd_m2", sample_contract="observer_counter_latin_v1" if resident is not None else "observer_per_ray_seed_batch_v1",
            path_classification_contract="direct_presence_priority_v1",
            search_contract="observer_full_spacing_v1" if spec.get("search_mode") == "full_detail" else "observer_grid_v1",
            progress_contract="committed_samples_v1", resume_contract="live_observer_wave_v1" if pause_callback else None,
            execution_backend="gpu_cuda" if state in ("gpu_active", "gpu_mixed") else "cpu", requested_backend=requested,
            performance=performance, settings=spec, attempted_count=committed, planned_count=planned,
            in_flight_sample_count=attempted-committed, pause_count=pause_count,
            direct_count=sum(c["direct_count"] for c in out_cells), reflected_count=sum(c["reflected_count"] for c in out_cells),
            excluded_count=excluded, filtered_count=filtered, depth_limited_count=committed_depth_limited,
            stopped=is_stopped, complete=not is_stopped, runtime_sec=time.perf_counter()-started, views=out_views, cells=out_cells,
            display_truncated=display_truncated, calibration_state="uncalibrated", observation_state="estimated" if out_cells else "not_observed",
            limitations=["parallel_camera", "finite_sampling", "reflection_only", "uncalibrated_source_material_threshold"]
                + (["depth_truncated"] if committed_depth_limited else []) + (["partial_result"] if is_stopped else []))

    @lru_cache(maxsize=8192)
    def cell_permutation(seed):
        # All repetitions of a cell share this layout. Keep its EXACT legacy
        # RNG stream while avoiding samples_per_cell identical MT/shuffle work.
        permutation = list(range(samples))
        random.Random(seed).shuffle(permutation)
        return tuple(permutation)

    for view_index, plan in enumerate(plans):
        columns, rows = plan["columns"], plan["rows"]
        outward, u, v = plan["outward"], plan["u"], plan["v"]
        view_cells = []

        def probe(index):
            nonlocal attempted, depth_limited
            attempted += 1
            pixel, pass_index = divmod(index, samples)
            row, column = divmod(pixel, columns)
            # Per-cell random Latin hypercube, including zero-hit cells. No
            # fixed centre plus random samples (that mixture biases a pixel).
            seed = (config.seed*0x9E3779B1 + view_index*1_000_003 + pixel*9176) & ((1 << 64)-1)
            rng = random.Random(seed + (pass_index+1)*0x85EBCA77)
            camera_weight = 1.
            if sensor is None:
                permutation = cell_permutation(seed)
                jx = (pass_index+rng.random())/samples
                jy = (permutation[pass_index]+rng.random())/samples
                launch = vec_add(vec_add(vec_add(center, vec_mul(outward, plan["distance"])),
                    vec_mul(u, plan["min_u"]+(column+jx)*plan["width"]/columns)),
                    vec_mul(v, plan["min_v"]+(row+jy)*plan["height"]/rows))
                direction = vec_mul(outward, -1)
            else:
                launch, direction, camera_weight = sensor.launch(index, rng)
                if camera_weight == 0:
                    return index, 0., None, 0, launch, 0.
            origin = launch
            throughput, value, direct_value = 1., 0., 0.
            previous_delta = True
            previous_pdf = 0.
            vertices = []
            representative = None
            best = 0.
            max_depth = 0

            def record(contribution, point, depth):
                nonlocal value, direct_value, representative, best, max_depth
                if not math.isfinite(contribution) or contribution < 0:
                    raise ValueError("Observer radiance has non-finite/negative contribution")
                value += contribution
                if depth == 0:
                    direct_value += contribution
                if prefer_observer_path(depth, contribution, representative, best):
                    best = contribution
                    max_depth = depth
                    representative = [point] + list(reversed(vertices)) + [launch]

            def source_hit_weight(source, point, source_normal):
                if previous_delta or not vertices:
                    return 1.
                delta = vec_sub(point, vertices[-1])
                cosine = abs(vec_dot(source_normal, vec_mul(direction, -1)))
                pdf = vec_dot(delta, delta)/(source[3]*cosine*len(sources)) if cosine > 1e-14 else 0.
                return balance(previous_pdf, pdf)

            for depth in range(config.max_depth+1):
                hit = yield origin, direction, epsilon, math.inf
                closest = hit.t if hit else math.inf
                virtual_seen = set()
                # Virtual planes add radiance; they are not opaque CAD.
                # In particular their non-emitting back must not hide a leak.
                for source, triangle, normal in virtual:
                    t = _triangle_intersection(origin, direction, triangle, epsilon)
                    if t is not None and t < closest:
                        point = vec_add(origin, vec_mul(direction, t))
                        # Shared triangle edges must not emit twice.
                        key = (id(source), round(t, 10))
                        if key not in virtual_seen:
                            virtual_seen.add(key)
                            record(throughput*emission_radiance(source, point, normal, vec_mul(direction, -1))
                                *source_hit_weight(source, point, normal), point, depth)
                if hit is None:
                    break
                for source in faces.get(hit.face_index, []):
                    source_normal = mesh.normal(hit.face_index)
                    record(throughput*emission_radiance(source, hit.point, source_normal, vec_mul(direction, -1))
                        *source_hit_weight(source, hit.point, source_normal), hit.point, depth)
                if depth >= config.max_depth:
                    profile = resolved[hit.face_index].profile
                    if config.k_brdf > 0 and (profile.bsdf_asset_id or
                        (profile.reflectance > 0 and profile.scatter_model != "none")):
                        depth_limited += 1
                    break
                profile = resolved[hit.face_index].profile
                if profile.scatter_model == "none" and not profile.bsdf_asset_id or config.k_brdf <= 0:
                    break
                normal = hit.normal if vec_dot(direction, hit.normal) < 0 else vec_mul(hit.normal, -1)
                wo = vec_mul(direction, -1)
                if sources:
                    source_index = rng.randrange(len(sources))
                    emitter, point, source_normal, face = _sample_source(sources, source_index, rng)
                    delta = vec_sub(point, hit.point)
                    distance2 = vec_dot(delta, delta)
                    if distance2 > (4*epsilon)**2:
                        distance = math.sqrt(distance2)
                        wi = vec_mul(delta, 1/distance)
                        f = brdf(profile, normal, wi, wo, config.angle_dependent_reflectance)
                        cosine_light = abs(vec_dot(source_normal, vec_mul(wi, -1)))
                        if f > 0 and cosine_light > 1e-14:
                            # Includes every mesh face; finite endpoints keep
                            # the sampled source face out of its own shadow.
                            block = yield vec_add(hit.point, vec_mul(wi, epsilon)), wi, epsilon, distance-2*epsilon
                            if block is None:
                                pdf = distance2/(sources[source_index][3]*cosine_light*len(sources))
                                le = emission_radiance(sources[source_index], point, source_normal, vec_mul(wi, -1))
                                # All lengths in the solid-angle ratio are mm;
                                # mm²/mm² cancels. Le itself uses physical m².
                                q = continuation_pdf(profile, normal, wo, wi, config.angle_dependent_reflectance)
                                contribution = throughput*f*max(0., vec_dot(normal, wi))*le/pdf*balance(pdf, q)
                                vertices.append(hit.point)
                                record(contribution, point, depth+1)
                                vertices.pop()
                # Proposal has both continuous and delta support. Measured
                # tables are evaluated light->camera, never transposed.
                new_direction, weight, previous_delta, previous_pdf = continuation(rng, profile, normal, wo, config.angle_dependent_reflectance)
                throughput *= weight
                if throughput <= 0:
                    break
                if depth >= 3:
                    survival = min(.95, max(.05, throughput))
                    if rng.random() >= survival:
                        break
                    throughput /= survival
                vertices.append(hit.point)
                # No deterministic energy cutoff. Roulette survivors retain
                # the reciprocal probability weight; finite depth is explicit.
                origin, direction = vec_add(hit.point, vec_mul(new_direction, epsilon)), new_direction
            return index, value*camera_weight, representative, max_depth, launch, best*camera_weight, direct_value*camera_weight

        pending = None

        def format_cell(cell):
            if cell is None or cell["direct_count"]+cell["reflected_count"] == 0:
                return None
            cell = dict(cell)
            cell["luminance_nit"] = cell.pop("sum")/cell["sample_count"]
            cell["direct_luminance_nit"] = cell.pop("direct_sum", 0.)/cell["sample_count"]
            cell["reflected_luminance_nit"] = max(0., cell["luminance_nit"]-cell["direct_luminance_nit"])
            # Standard error is intentionally absent: stratified low-sample
            # zero variance is not evidence of absence or calibrated confidence.
            cell["observation_state"] = "estimated"
            if cell["luminance_nit"] < threshold:
                return None
            return cell

        def finish(cell):
            nonlocal filtered, display_truncated
            formatted = format_cell(cell)
            if formatted is None:
                if cell:
                    filtered += cell["direct_count"]+cell["reflected_count"]
                return
            view_cells.append(formatted)
            if len(view_cells) > 2*cell_limit:
                view_cells.sort(key=lambda c: c["luminance_nit"], reverse=True)
                del view_cells[cell_limit:]
                display_truncated = True

        def pause_current():
            nonlocal started, pause_count
            pause_count += 1
            visible_cells = list(view_cells)
            current_cell = format_cell(pending)
            if current_cell is not None:
                visible_cells.append(current_cell)
            visible_cells.sort(key=lambda c: c["luminance_nit"], reverse=True)
            visible_cells = visible_cells[:cell_limit]
            # Snapshot the committed prefix only. Uncommitted primary slots,
            # active reverse generators and their queries stay in _execute_probes.
            snapshot = copy.deepcopy(result(cells+visible_cells, views+[view_summary(plan, visible_cells)], True))
            snapshot["display_truncated"] = display_truncated or len(view_cells)+(current_cell is not None) > cell_limit
            wait_started = time.perf_counter()
            keep_running = pause_callback(snapshot)
            started += time.perf_counter()-wait_started
            return keep_running

        effective_batch_size = resident.batch_size(batch_size) if resident is not None else batch_size
        for start in range(0, plan["grid_samples"], effective_batch_size):
            if should_stop and should_stop():
                if pause_callback is None or not pause_current():
                    stopped = True
                    break
            if resident is not None:
                # Commit only after the entire chunk has validated. Failure does
                # not publish an incomplete or duplicated radiance accumulator.
                completed, execution, limited = resident.run(plan, center, view_index, start,
                    min(effective_batch_size, plan["grid_samples"]-start))
                if getattr(execution, "cpu_fallback", False):
                    resident_cpu_batches += 1
                    resident_reason = execution.reason
                    stats.reference_scalar_query_count += execution.logical_intersection_rows
                    stats.fallback_count += 1
                    stats.fallback_ray_count += getattr(completed, "sample_count", len(completed))
                    stats.fallback_phase = "observer_resident"
                    stats.fallback_reason = execution.reason
                else:
                    stats.record_gpu_resident_execution(execution)
                    resident_batches += 1
                attempted += getattr(completed, "sample_count", len(completed))
                depth_limited += limited
                interrupted = False
            else:
                completed, interrupted = _execute_probes(mesh,
                    [probe(i) for i in range(start, min(plan["grid_samples"], start+effective_batch_size))],
                    stats, dispatch, should_stop, pause_current if pause_callback is not None else None)
            if sensor is not None:
                values = [item[1] for item in completed if item is not None]
                sensor_sum += math.fsum(values)
                sensor_square_sum += math.fsum(value*value for value in values)
                sensor_positive += sum(value > 0 for value in values)
                committed += len(values)
                committed_depth_limited = depth_limited
                if progress_callback:
                    progress_callback(committed, planned)
                if interrupted:
                    stopped = True
                    break
                continue
            if hasattr(completed, "sample_count"):
                for cell in completed:
                    if pending is None or (pending["row"], pending["column"]) != (cell["row"], cell["column"]):
                        finish(pending)
                        pending = cell
                    else:
                        for key in ("sample_count", "sum", "direct_sum", "direct_count", "reflected_count"):
                            pending[key] += cell[key]
                        pending["min_depth"] = min(pending["min_depth"], cell["min_depth"])
                        pending["max_depth"] = max(pending["max_depth"], cell["max_depth"])
                        if prefer_observer_path(len(cell["representative_path"])-2 if cell["representative_path"] else -1,
                            cell["representative_contribution_nit"], pending["representative_path"], pending["representative_contribution_nit"]):
                            for key in ("position", "representative_path", "representative_contribution_nit"):
                                pending[key] = cell[key]
                committed += completed.sample_count
                committed_depth_limited = depth_limited
                if progress_callback:
                    progress_callback(committed, planned)
                continue
            for item in completed:
                if item is None:
                    continue
                index, value, path, depth, launch, contribution = item[:6]
                direct_value = item[6] if len(item) > 6 else (value if depth == 0 else 0.)
                row, column = divmod(index//samples, columns)
                if pending is None or (pending["row"], pending["column"]) != (row, column):
                    finish(pending)
                    pending = dict(view_index=view_index, row=row, column=column, sample_count=0, sum=0., direct_sum=0.,
                        direct_count=0, reflected_count=0, min_depth=config.max_depth+1, max_depth=0,
                        representative_path=None, position=None, representative_contribution_nit=0.)
                pending["sample_count"] += 1
                if path is not None and value > 0:
                    if collector._allowed(path[-2], outward, launch):
                        excluded += 1
                        continue
                    pending["sum"] += value
                    pending["direct_sum"] += direct_value
                    pending["direct_count" if depth == 0 else "reflected_count"] += 1
                    pending["min_depth"] = min(pending["min_depth"], depth)
                    pending["max_depth"] = max(pending["max_depth"], depth)
                    if prefer_observer_path(depth, contribution, pending["representative_path"], pending["representative_contribution_nit"]):
                        pending.update(position=path[-2], representative_path=path, representative_contribution_nit=contribution)
            committed += sum(item is not None for item in completed)
            committed_depth_limited = depth_limited
            if progress_callback:
                progress_callback(committed, planned)
            if interrupted:
                stopped = True
                break
        finish(pending)
        if len(view_cells) > cell_limit:
            view_cells.sort(key=lambda c: c["luminance_nit"], reverse=True)
            del view_cells[cell_limit:]
            display_truncated = True
        cells.extend(view_cells)
        views.append(view_summary(plan, view_cells))
        if stopped:
            break
    if sensor is not None:
        return dict(contract="finite_receiver_reverse_flux_v1", flux_lumen=sensor_sum/committed if committed else None,
            sample_second_moment=sensor_square_sum/committed if committed else None, sample_count=committed,
            positive_samples=sensor_positive, planned_count=planned, complete=not stopped and committed == planned,
            runtime_sec=time.perf_counter()-started, compute_backend=requested, performance=result([], [], stopped)["performance"])
    return result(cells, views, stopped)
