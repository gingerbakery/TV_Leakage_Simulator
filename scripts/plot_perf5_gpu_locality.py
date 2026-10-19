from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('outputs/perf5_transport'))
    args = parser.parse_args()
    grid = json.loads((args.directory / 'uniform-grid-guarded-paired.json').read_text(encoding='utf-8'))
    spatial = json.loads((args.directory / 'spatial-primary-order-probe.json').read_text(encoding='utf-8'))
    early = json.loads((args.directory / 'early-barycentric-probe.json').read_text(encoding='utf-8'))
    if not all(report['passed'] and report['source_unchanged'] for report in (grid, spatial, early)):
        raise RuntimeError('Only validated source-frozen measurements can be plotted')
    plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False, 'font.size': 11})
    figure, axes = plt.subplots(1, 2, figsize=(14, 5.3), layout='constrained')
    labels = ['반사 MIS 65K', '반사 MIS 1M', 'Face 직달 1M', '확률 반사 1M', '갇힌 구조 1M', 'TV CAD 100K']
    ratios = [case['records']['ordered_scan']['median_sec'] / case['records']['ordered']['median_sec'] for case in grid['cases']]
    axes[0].barh(labels[::-1], ratios[::-1], color=['#1b9e77' if ratio >= 1 else '#e89b45' for ratio in ratios[::-1]])
    axes[0].set_title('정밀 히트맵 경로 추가 최적화\n기존 순서 집계 대비 · warm 11회')
    axes[0].set_xlim(0, 1.48)
    for position, ratio in enumerate(ratios[::-1]):
        axes[0].text(ratio + 0.015, position, f'{ratio:.3f}배', va='center')
    records = spatial['cases'][0]['records']
    candidates = [('삼각형 조기 제외', early['cases'][0]['records']['early_u']['speedup']),
        ('방향 우선 재배치', records['direction_origin']['speedup']),
        ('위치 우선 재배치', records['origin_direction']['speedup']),
        ('방향 부호 + 위치', records['octant_origin']['speedup'])]
    axes[1].barh([value[0] for value in candidates][::-1], [value[1] for value in candidates][::-1], color='#e89b45')
    axes[1].set_title('TV CAD GPU 탐색 후보\n각 동일 세션 기본 경로 대비 · warm 5회')
    axes[1].set_xlim(0, 1.15)
    for position, (_, ratio) in enumerate(candidates[::-1]):
        axes[1].text(ratio + 0.015, position, f'{ratio:.3f}배', va='center')
    for axis in axes:
        axis.axvline(1.0, color='#516375', linestyle='--', linewidth=1)
        axis.set_xlabel('속도비 · 1보다 클수록 빠름')
        axis.spines[['top', 'right']].set_visible(False)
    figure.suptitle('PERF 5C 추가 실험 결과 · GPU 전체 2배 목표는 미완료', fontsize=15, fontweight='bold')
    figure.savefig(args.directory / 'uniform-grid-and-locality.png', dpi=170)
    plt.close(figure)


if __name__ == '__main__':
    main()
