"""Per-committed-sample stages, disjoint from optical estimators."""
STOP_REASONS = ('unknown', 'escaped', 'depth_limit', 'reflection_disabled', 'zero_throughput', 'roulette', 'camera_roi')


def stage_record(source_seen, emission_positive, stop):
    return dict(source_seen=bool(source_seen), emission_positive=bool(emission_positive), stop_reason=STOP_REASONS[int(stop)])


def empty_stages():
    return dict(contract='reverse_contribution_stages_v1', available=True, sample_count=0,
        counts=dict(positive=0, source_not_encountered=0, source_emission_zero=0, contribution_zero=0, camera_roi=0),
        termination_counts={reason: 0 for reason in STOP_REASONS}, missing_records=0)


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
