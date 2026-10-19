from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding='utf-8'))
    if not report['passed'] or not report['source_unchanged']:
        raise RuntimeError('A source-frozen accuracy-passing report is required')
    labels = {'reflected_receiver_mis': '반사 Receiver MIS', 'face_direct': 'CAD 면광원 직달',
        'stochastic_two_bounce': '확률 반사 부하', 'trapped_corridor_depth10': '갇힌 구조 상한 10',
        'tv_corner_cad_probe': 'TV CAD 부하'}
    plt.rcParams['font.family'] = 'Malgun Gothic'
    plt.rcParams['axes.unicode_minus'] = False
    figure, axis = plt.subplots(figsize=(10.8, 6.4), layout='constrained')
    positions = list(range(len(report['cases'])))
    ratios = [case['speedup'] for case in report['cases']]
    bars = axis.barh(positions, ratios,
        color=['#b45353' if not case['no_regression_below_three_percent'] else '#267d95' for case in report['cases']])
    axis.set_yticks(positions, [f'{labels[case["scene"]]}\n{case["rays"]:,} Ray' for case in report['cases']])
    axis.invert_yaxis()
    axis.axvline(1.0, color='#475569', linestyle='--', label='현행과 같은 속도')
    changes = [(1 / value - 1) * 100 for value in ratios]
    annotations = axis.bar_label(bars, labels=[f'{value:.3f}배 / ' + (f'시간 {change:+.1f}%' if abs(change) >= 0.05 else '변화 없음')
        for value, change in zip(ratios, changes)], padding=7, fontsize=10)
    for annotation in annotations:
        annotation.set_bbox({'facecolor': 'white', 'edgecolor': 'none', 'alpha': 0.95, 'pad': 2})
    axis.set_xlim(0, max(1.65, max(ratios) + 0.65))
    axis.set_xlabel('기본 atomic 시간 / 순서 집계 시간 · 1 미만이면 느려짐')
    axis.set_title('Receiver 히트맵 순서 집계 후보 · 기본 적용은 보류', pad=16)
    axis.grid(axis='x', alpha=0.18)
    axis.set_axisbelow(True)
    axis.legend(loc='lower right', frameon=False)
    figure.supxlabel('RTX 3070 · 동일 소스 교대 실행 · warm 11회 중앙값\n방출광은 두 모드 모두 atomic · 입력 광선과 FP64 정밀도 유지', fontsize=9)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=160, facecolor='white')
    plt.close(figure)


if __name__ == '__main__':
    main()
