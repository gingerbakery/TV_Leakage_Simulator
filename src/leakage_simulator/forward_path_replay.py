"""One isolated production forward ray, with only its launch fixed by evidence.

Stochastic reflections are NOT steered towards the saved path. Neither the
diagnostic ray nor its flux is committed to the product/comparison estimators.
"""
from dataclasses import replace
import math

import numpy as np

from .geometry import vec_add, vec_mul, vec_norm, vec_dot, vec_sub
from .observer_probe import _source_geometry, emission_supported

_AMBIGUOUS_SOURCE = object()


def _containing_triangles(triangles, point, tolerance):
    for start in range(0,len(triangles),4096):
        chunk=triangles[start:start+4096]
        vertices=np.asarray([item[0] for item in chunk],dtype=float)
        e1,e2=vertices[:,1]-vertices[:,0],vertices[:,2]-vertices[:,0]
        delta=np.asarray(point)-vertices[:,0]
        cross=np.cross(e1,e2)
        length=np.linalg.norm(cross,axis=1)
        plane=np.abs(np.sum(delta*cross,axis=1))/np.maximum(length,1e-300)
        aa,bb,ab=np.sum(e1*e1,axis=1),np.sum(e2*e2,axis=1),np.sum(e1*e2,axis=1)
        denominator=aa*bb-ab*ab
        d1,d2=np.sum(delta*e1,axis=1),np.sum(delta*e2,axis=1)
        u=np.divide(bb*d1-ab*d2,denominator,out=np.full(len(chunk),-1.),where=denominator>0)
        v=np.divide(aa*d2-ab*d1,denominator,out=np.full(len(chunk),-1.),where=denominator>0)
        for i in np.flatnonzero((length>0)&(plane<=tolerance)&(u>=-1e-9)&(v>=-1e-9)&(u+v<=1+1e-9)):
            yield chunk[int(i)]


def _source_at(trace, point, direction):
    tolerance = max(trace.config.epsilon_mm*4, 1e-7)
    found = None
    for emitter, triangles, _, area in _source_geometry(trace.mesh, trace.emitters):
        for triangle, normal, face in _containing_triangles(triangles,point,tolerance):
            if not emission_supported(emitter, point, normal, direction):
                continue
            launch_normal = vec_norm(emitter.custom_normal if emitter.normal_mode == 'custom'
                                     and emitter.custom_normal is not None else normal)
            if emitter.normal_flip:
                launch_normal = vec_mul(launch_normal,-1)
            if emitter.emission_direction == 'both' and vec_dot(launch_normal,direction) < 0:
                launch_normal = vec_mul(launch_normal,-1)
            offset_axis = direction if emitter.aim is not None and emitter.aim.enabled else launch_normal
            origin = vec_add(point,vec_mul(offset_axis,trace.config.epsilon_mm))
            power = emitter.effective_power_lumen(area)/emitter.ray_count
            if found is not None:
                return _AMBIGUOUS_SOURCE
            found = emitter, origin, -1 if face is None else face, power
            break
    return found


def replay_forward_path(trace, receiver):
    from .raytracer import run_direct_ray_trace, _build_receiver_frame, _find_first_receiver_hit
    from .types import ReceiverGrid
    path = receiver.observer_reference_path
    report = dict(contract='isolated_forward_path_replay_v1', receiver_id=receiver.receiver_id,
                  execution_backend='cpu', included_in_flux=False, injected_hits=0,
                  launch_fixed=True, reflection_directions_fixed=False)
    if not path or len(path) < 2:
        return dict(report,state='unavailable',reason='missing_representative_path')
    if any(math.dist(a,b)<=trace.config.epsilon_mm for a,b in zip(path[1:],path[2:])):
        return dict(report,state='unavailable',reason='degenerate_segment')
    delta = vec_sub(path[1],path[0])
    if vec_dot(delta,delta) <= trace.config.epsilon_mm**2:
        return dict(report,state='unavailable',reason='degenerate_first_segment')
    direction = vec_norm(delta)
    source = _source_at(trace,path[0],direction)
    if source is _AMBIGUOUS_SOURCE:
        return dict(report,state='unavailable',reason='ambiguous_source')
    if source is None:
        return dict(report,state='unavailable',reason='source_position_or_emission_mismatch')
    emitter, origin, source_face, packet = source
    config = replace(trace.config, compute_backend='cpu', intersection_backend='bvh',
                     store_ray_paths=True,max_stored_paths=1,receiver_diagnostics=True,auto_convergence=False)
    replay_seed = emitter.seed if emitter.seed is not None else config.seed
    isolated = replace(trace, emitters=[replace(emitter,ray_count=1,seed=replay_seed)],receivers=[receiver],config=config,
                       observer_probe=None,escape_collection=None)
    try:
        result = run_direct_ray_trace(isolated, diagnostic_primary_ray=(origin,direction,source_face,packet))
    except Exception as exc:
        return dict(report,state='failed',reason='replay_execution_failed',error=str(exc))
    events = result.stored_paths[0] if result.stored_paths else []
    surfaces = [event for event in events if event.face_index >= 0 and event.event_type != 'emitter']
    expected = path[1:-1]
    tolerance = max(trace.config.epsilon_mm*8,1e-6)
    difference = None
    matched = 0
    for i, point in enumerate(expected):
        actual = surfaces[i].point if i < len(surfaces) else None
        if actual is None or math.dist(point,actual) > tolerance:
            difference = dict(segment_index=i,expected_point=list(point),actual_point=list(actual) if actual else None)
            break
        matched += 1
    termination = result.metrics['_termination_summary']
    hit = result.receiver_hit_count > 0
    if difference is None and not hit:
        frame = _build_receiver_frame(receiver)
        frame.roi_boxes = tuple((trace.analysis_roi or {}).get('detection_boxes', []))
        target = _find_first_receiver_hit(path[-2],vec_norm(vec_sub(path[-1],path[-2])),packet,-1,
            [frame],{receiver.receiver_id:ReceiverGrid.empty(receiver)},trace.config)
        difference = dict(segment_index=len(expected),expected_point=list(target.point if target else path[-1]),
                          actual_point=list(events[-1].point) if events else None)
    stochastic = matched > 0 and surfaces[matched-1].scatter_model in ('lambertian','gaussian','mixed','bsdf')
    changed_surface = difference is not None and difference['segment_index'] < len(expected) and difference['actual_point'] is not None
    reason = ('receiver_reached' if hit else
              'different_sampled_branch' if changed_surface and stochastic else
              'first_intersection_differs' if changed_surface else 'energy_cutoff' if termination['energy_cutoff_count'] else
              'depth_limit' if termination['depth_limit_count'] else 'roulette_terminated' if termination['roulette_terminated_count'] else
              'different_sampled_branch' if stochastic else 'first_intersection_differs' if difference and difference['segment_index'] < len(expected) else 'receiver_not_reached')
    report.update(state='receiver_reached' if hit else 'not_reached',reason=reason,
                  path_reproduced=hit and difference is None and len(surfaces)==len(expected),
                  first_difference=difference,matched_surface_count=matched,
                  emitter_id=emitter.emitter_id,seed=replay_seed,seed_policy='emitter_seed_else_config_seed',
                  nominal_packet_lumen=packet, launch_origin=list(origin), launch_direction=list(direction),
                  actual_events=[event.to_dict() for event in events],termination=termination,
                  diagnostics=result.metrics.get('_receiver_diagnostics'))
    return report
