from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding='utf-8'))
    if not report['passed'] or not report['source_unchanged']:
        raise RuntimeError('A source-frozen passing benchmark is required')
    plt.rcParams['font.family'] = 'Malgun Gothic'
    plt.rcParams['axes.unicode_minus'] = False
    names = {
        'face_direct': '면광원 직달 · 100만 Ray',
        'stochastic_two_bounce': '확률 반사 부하 대조군 · 100만 Ray',
        'trapped_corridor_depth10': '갇힌 구조 / 상한 10 · 100만 Ray',
        'tv_corner_cad_probe': 'TV CAD 부하 대조군 · 10만 Ray',
    }
    labels = [names.get(case['scene'], case['scene'].replace('absorber_cap_', '첫 면 흡광 / 상한 ')) for case in report['cases']]
    ratios = [case['speedup'] for case in report['cases']]
    figure, axis = plt.subplots(figsize=(12, 6.6))
    positions = list(range(len(ratios)))
    bars = axis.barh(positions, ratios, color=['#15816c' if ratio >= 2 else '#3678a4' for ratio in ratios], height=0.58)
    axis.set_yticks(positions, labels)
    axis.invert_yaxis()
    axis.axvline(1, color='#8f9eaa', linewidth=1)
    axis.axvline(2, color='#cf6d38', linestyle='--', linewidth=1.4, label='최소 목표 2배')
    for bar, case in zip(bars, report['cases']):
        before = case['measurements']['wavefront']['warm_median_sec']
        after = case['measurements']['fused']['warm_median_sec']
        axis.text(bar.get_width() + 0.04, bar.get_y() + bar.get_height() / 2,
                  f'{case["speedup"]:.3f}배   {before:.3f} → {after:.3f}초', va='center', fontsize=10,
                  bbox={'facecolor': 'white', 'edgecolor': 'none', 'alpha': 0.9, 'pad': 2})
    axis.set_xlim(0, max(2.9, max(ratios) + 1.0))
    axis.set_xlabel('기존 CPU 대비 속도비 · 첫 실행 제외 / warm 3회 중앙값')
    axis.set_title('PERF-5B CPU 자동 경로 개선 · 대표 7조건 비교', loc='left', fontsize=15, pad=20)
    axis.spines[['top', 'right', 'left']].set_visible(False)
    axis.tick_params(axis='y', length=0)
    axis.grid(axis='x', alpha=0.15)
    axis.set_axisbelow(True)
    axis.legend(loc='lower right', frameon=False)
    figure.text(0.02, 0.025, '같은 i7-10700 / 입력·광량·히트맵 hash exact. GPU 대비 수치가 아님.\nTV는 수광 0인 부하 대조군. 흡광군은 첫 면에서 종료하며 실제 1,000회 반사를 뜻하지 않음.', fontsize=9, color='#526472')
    figure.tight_layout(rect=(0, 0.08, 1, 1))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=160, bbox_inches='tight')
    plt.close(figure)


if __name__ == '__main__':
    main()
