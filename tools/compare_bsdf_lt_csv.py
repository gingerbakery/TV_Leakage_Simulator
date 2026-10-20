"""Compare existing calibration CSVs inside the LT PC; Python standard library only.

No ray tracing, network, or proprietary raw-data export. The summary contains
only aggregate metrics and the supplied run provenance, never the LT mesh.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

CASES = ('lambertian-aoi0', 'directional-aoi0', 'directional-aoi30',
         'directional-aoi60', 'directional-aoi80', 'directional-aoi45')
CRITERIA = {'flux_error_percent_max': 1., 'distribution_overlap_percent_min': 95., 'correlation_min': .99}


def read_map(path):
    bins = {}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            u, v, flux = (float(row[key]) for key in ('u_mm', 'v_mm', 'flux_lm'))
            if not all(math.isfinite(x) for x in (u, v, flux)) or flux < 0:
                raise ValueError(f'{path.name}: invalid coordinate/flux')
            col, line = round(u + 9.5), round(v + 9.5)
            if not (0 <= col < 20 and 0 <= line < 20) or abs(u-col+9.5) > 1e-8 or abs(v-line+9.5) > 1e-8:
                raise ValueError(f'{path.name}: expected 20x20 bins, centres -9.5..9.5 mm')
            key = (line, col)
            if key in bins:
                raise ValueError(f'{path.name}: duplicate bin')
            bins[key] = flux
    if len(bins) != 400 or math.fsum(bins.values()) <= 0:
        raise ValueError(f'{path.name}: requires 400 bins and positive total flux')
    return [bins[(row, col)] for row in range(20) for col in range(20)]


def scores(sim, lt):
    sim_total, lt_total = math.fsum(sim), math.fsum(lt)
    p, q = [x/sim_total for x in sim], [x/lt_total for x in lt]
    mean = 1/len(p)
    covariance = math.fsum((a-mean)*(b-mean) for a, b in zip(p, q))
    variance = math.sqrt(math.fsum((a-mean)**2 for a in p)*math.fsum((b-mean)**2 for b in q))
    correlation = covariance/variance if variance > 0 else None
    error = (sim_total/lt_total-1)*100
    overlap = (1-math.fsum(abs(a-b) for a, b in zip(p, q))/2)*100
    return {'sim_received_lm': sim_total, 'lt_received_lm': lt_total,
            'flux_error_percent': error, 'distribution_overlap_percent': overlap,
            'correlation': correlation,
            'pass': abs(error) <= 1 and overlap >= 95 and correlation is not None and correlation >= .99}


def compare(sim_dir, lt_dir):
    provenance = json.loads((lt_dir/'provenance.json').read_text(encoding='utf-8-sig'))
    if (provenance.get('engine') != 'LightTools' or not provenance.get('version')
            or provenance.get('shift_type') != 'No Shift' or provenance.get('anisotropic') is not False
            or provenance.get('smoothing') is not False or provenance.get('source_power_lm') != 1
            or isinstance(provenance.get('rays'), bool) or not isinstance(provenance.get('rays'), int)
            or provenance['rays'] <= 0):
        raise ValueError('Document LT version, No Shift, anisotropic=false, smoothing=false, source_power_lm=1 and positive integer rays')
    rows = []
    for case in CASES:
        if not (lt_dir/f'{case}.csv').is_file():
            rows.append({'case': case, 'status': 'missing'})
            continue
        rows.append({'case': case, 'status': 'compared',
                     **scores(read_map(sim_dir/f'{case}-sim.csv'), read_map(lt_dir/f'{case}.csv'))})
    complete = all(row['status'] == 'compared' for row in rows)
    failed = any(row.get('pass') is False for row in rows)
    return {'scope': 'User-provided LightTools CSV comparison; provenance supplied by operator',
            'status': 'failed' if failed else 'passed' if complete else 'partial',
            'criteria': CRITERIA, 'provenance': provenance, 'cases': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sim-directory', type=Path, default=Path('.'))
    parser.add_argument('--lt-directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('lt-comparison-summary.json'))
    args = parser.parse_args()
    report = compare(args.sim_directory, args.lt_directory)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report['status'] == 'passed' else 1 if report['status'] == 'failed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
