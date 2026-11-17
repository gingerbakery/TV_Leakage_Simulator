"""Bounded, non-estimator evidence linked to actual detected primary rays."""
from dataclasses import replace
import math


def detected_paths(result, receiver_id):
    diagnostic = result.metrics.get('_receiver_diagnostics', {})
    row = next((row for row in diagnostic.get('receivers', []) if row['receiver_id'] == receiver_id), {})
    return row.get('detected_paths', dict(samples=[], recorded_count=0, actual_hit_count=result.receiver_hit_count,
        complete=result.receiver_hit_count == 0, truncated=result.receiver_hit_count > 0))


def pair_detected_paths(trace, comparison_input, capture, budget, should_stop=None):
    from .raytracer import run_direct_ray_trace
    reports = []
    receiver = comparison_input.receivers[0]
    tolerance = max(trace.config.epsilon_mm*8, 1e-6)
    # Inspect largest recorded contributors first; the recording prefix is bounded, not representative.
    for sample in sorted(capture['samples'], key=lambda item: item['flux_lumen'], reverse=True)[:budget]:
        if should_stop and should_stop():
            break
        record = dict(ray_id=sample['ray_id'], included_in_flux=False, execution_backend='cpu',
                      state='unavailable', causal_scope='this_recorded_ray_only')
        reports.append(record)
        emitter = next((e for e in comparison_input.emitters if e.emitter_id == sample['emitter_id']), None)
        if emitter is None or sample['rng_contract'] != 'counter_rng_v2':
            record['reason'] = 'primary_identity_or_rng_unavailable'
            continue
        def execute(config):
            isolated = replace(comparison_input, emitters=[replace(emitter, ray_count=1, seed=sample['emitter_seed'])],
                config=replace(config, compute_backend='cpu', intersection_backend='bvh', receiver_diagnostics=True,
                    store_ray_paths=True, max_stored_paths=1, auto_convergence=False,
                    primary_sampling_strategy='source', bounce_sampling_strategy='source'))
            return run_direct_ray_trace(isolated, should_stop=should_stop,
                diagnostic_primary_ray=(sample['launch_origin'], sample['launch_direction'], sample['source_face'], sample['initial_packet_lumen']),
                diagnostic_primary_index=sample['primary_index'])
        try:
            baseline = execute(comparison_input.config)
            base_capture = detected_paths(baseline, receiver.receiver_id)
            repeated = base_capture.get('samples', [])
            verified = (len(repeated) == 1 and repeated[0]['reflection_seed'] == sample['reflection_seed']
                and repeated[0]['surface_faces'] == sample['surface_faces']
                and len(repeated[0]['points']) == len(sample['points'])
                and all(math.dist(a, b) <= tolerance for a, b in zip(repeated[0]['points'], sample['points']))
                and math.isclose(repeated[0]['flux_lumen'], sample['flux_lumen'], rel_tol=1e-9, abs_tol=0.))
            if not verified or baseline.metrics['_termination_summary']['stopped_early']:
                record.update(reason='comparison_ray_reproduction_unverified', state='unverified')
                continue
            original = execute(trace.config)
            events = original.stored_paths[0] if original.stored_paths else []
            points = [event.point for event in events if event.event_type == 'surface']
            expected = sample['points'][1:-1]
            matched = 0
            for a, b in zip(points, expected):
                if math.dist(a, b) > tolerance:
                    break
                matched += 1
            termination = original.metrics['_termination_summary']
            surface_faces = [event.face_index for event in events if event.event_type == 'surface']
            prefix_matches = matched == len(points) and len(points) <= len(expected) and surface_faces == sample['surface_faces'][:len(points)]
            if termination['stopped_early']:
                state, reason = 'incomplete', 'stopped'
            elif not prefix_matches:
                state, reason = 'unverified', 'path_prefix_differs'
            elif original.receiver_hit_count:
                original_points = [event.point for event in events]
                same_path = len(original_points) == len(sample['points']) and all(math.dist(a,b) <= tolerance for a,b in zip(original_points,sample['points']))
                state, reason = ('verified', 'original_also_detected') if same_path else ('unverified', 'original_receiver_path_differs')
            elif termination['energy_cutoff_count']:
                state, reason = 'verified', 'energy_cutoff'
            elif termination['roulette_terminated_count']:
                state, reason = 'verified', 'roulette_terminated'
            elif termination['depth_limit_count']:
                state, reason = 'verified', 'depth_limit'
            else:
                state, reason = 'unverified', 'original_not_detected_without_linked_stop'
            record.update(state=state, reason=reason, comparison_reproduced=True,
                matched_surface_count=matched, original_hits=original.receiver_hit_count,
                original_termination=termination, original_events=[event.to_dict() for event in events],
                comparison_flux_lumen=sample['flux_lumen'],
                original_flux_lumen=sum(sum(row) for row in original.receiver_grids[0].flux_lumen))
        except Exception as exc:
            record.update(state='failed', reason='paired_replay_failed', error=str(exc))
    return dict(contract='detected_primary_policy_pair_v1', included_in_flux=False, execution_backend='cpu',
        selection='largest_recorded_contributors_first', requested_limit=budget, checked_count=len(reports),
        records=reports, scope='bounded_recorded_rays_not_population_causality')
