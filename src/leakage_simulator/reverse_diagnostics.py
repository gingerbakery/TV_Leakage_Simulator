"""Per-committed-sample stages, disjoint from optical estimators."""
STOP_REASONS = ('unknown', 'escaped', 'depth_limit', 'reflection_disabled', 'zero_throughput', 'roulette', 'camera_roi')


CONNECTION_OUTCOMES = ("too_close", "direction_rejected", "occluded", "visible_emission_zero", "visible_emission_positive")


def stage_record(source_seen, emission_positive, stop, connections=None):
    return dict(source_seen=bool(source_seen), emission_positive=bool(emission_positive),
        stop_reason=STOP_REASONS[int(stop)], connections=connections)


def empty_connections():
    return dict(samples_with_counters=0, no_attempt_samples=0, attempted_samples=0,
        attempts=0, **{reason: 0 for reason in CONNECTION_OUTCOMES})


def empty_stages():
    return dict(contract='reverse_contribution_stages_v1', available=True, sample_count=0,
        counts=dict(positive=0, source_not_encountered=0, source_emission_zero=0, contribution_zero=0, camera_roi=0),
        termination_counts={reason: 0 for reason in STOP_REASONS}, missing_records=0,
        source_connections=empty_connections(), source_not_encountered_connections=empty_connections())


def record_stages(summary, items):
    for item in items:
        if item is None:
            continue
        summary['sample_count'] += 1
        stage = item[7] if len(item) > 7 else None
        if not stage:
            summary['missing_records'] += 1
            summary['available'] = False
            continue
        summary['termination_counts'][stage['stop_reason']] += 1
        category = ('positive' if item[1] > 0 else 'camera_roi' if stage['stop_reason'] == 'camera_roi' else
            'source_not_encountered' if not stage['source_seen'] else 'source_emission_zero' if not stage['emission_positive'] else 'contribution_zero')
        summary['counts'][category] += 1

        connections = stage.get("connections")
        if connections is None:
            continue
        outcomes = [(key, int(value)) for key, value in zip(CONNECTION_OUTCOMES, connections)]
        attempts = sum(value for _, value in outcomes)
        targets = [summary["source_connections"]]
        if category == "source_not_encountered":
            targets.append(summary["source_not_encountered_connections"])
        for target in targets:
            target["samples_with_counters"] += 1
            target["attempted_samples" if attempts else "no_attempt_samples"] += 1
            target["attempts"] += attempts
            for key, value in outcomes:
                target[key] += value
