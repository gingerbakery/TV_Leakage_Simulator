from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding='utf-8'))
    labels = {'face_direct': 'CAD 면광원 직접광', 'stochastic_two_bounce': '확률 반사 부하 대조군',
              'trapped_corridor_depth10': '갇힌 구조 · 상한 10', 'tv_corner_cad_probe': 'TV CAD 부하 대조군'}
    cases = payload['cases']
    names = [f'{labels.get(case["name"], "첫 면 흡광 · 상한 " + str(case["max_depth"]))}\n{case["rays"]:,} Ray'
             for case in cases]
    values = [case['backends']['gpu_cuda']['same_session_speedup'] for case in cases]
    plt.rcParams['font.family'] = ['Malgun Gothic', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    figure, axes = plt.subplots(figsize=(10.8, 6.5), layout='constrained')
    positions = np.arange(len(cases))
    bars = axes.barh(positions, values, color=['#16a34a' if value >= 2.0 else '#0284c7' for value in values])
    bar_labels = axes.bar_label(bars, labels=[f'{value:.3f}배' for value in values], padding=6, fontsize=11)
    for label in bar_labels:
        label.set_bbox({'facecolor': 'white', 'edgecolor': 'none', 'alpha': 0.9, 'pad': 2})
    axes.set_yticks(positions, names)
    axes.invert_yaxis()
    axes.axvline(2.0, color='#dc2626', linestyle='--', label='진행 목표 · 2배')
    axes.set_xlim(0, max(2.6, max(values) * 1.2))
    axes.set_xlabel('기존 GPU 시간 / 개선 GPU 시간 · 클수록 빠름')
    axes.set_title('PERF-5C GPU 중간 측정 · 모든 조건의 2배 달성은 아님', pad=15)
    axes.grid(axis='x', alpha=0.2)
    axes.set_axisbelow(True)
    axes.legend(loc='lower right', frameon=False)
    figure.supxlabel('RTX 3070 · 같은 프로세스에서 기존/개선 교대 · warm 3회 중앙값\nCAD import·최초 BVH build·화면 표시 제외 · TV는 수광 0인 부하 대조군\n흡광군은 첫 면에서 종료하며 실제 1,000회 반사를 뜻하지 않음', fontsize=9)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=160, facecolor='white')
    plt.close(figure)


if __name__ == '__main__':
    main()
