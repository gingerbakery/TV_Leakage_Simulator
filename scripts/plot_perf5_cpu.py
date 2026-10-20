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
    data = json.loads(args.input.read_text(encoding='utf-8'))
    names = ['face_direct', 'stochastic_two_bounce', 'trapped_corridor_depth10', 'tv_corner_cad_probe']
    scene_labels = ['CAD 면광원 직접광', '확률 반사 2회', '갇힌 구조 10회', '샘플 TV 코너 CAD']
    labels = [f'{label}\n{next(case["rays"] for case in data["cases"] if case["name"] == name):,} Ray'
              for name, label in zip(names, scene_labels)]
    settings = [(1024, 1), (8192, 1), (8192, 4)]
    legend = ['이전 설정: 1,024 · 직렬', '8,192 · 직렬', 'CPU 가속: 8,192 · 최대 4 worker']
    colors = ['#64748b', '#0ea5e9', '#16a34a']
    preparation_comparison = data['contract'] == 'perf5_cpu_preparation_v1'
    if preparation_comparison:
        settings = ['reference', 'optimized']
        legend = ['직전 CPU 가속 설정', '입력 검증 · 집계 준비 최적화']
        colors = ['#64748b', '#16a34a']
    plt.rcParams['font.family'] = ['Malgun Gothic', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    figure, axes = plt.subplots(figsize=(11, 5.1), layout='constrained')
    positions = np.arange(len(names))
    width = 0.25 if not preparation_comparison else 0.32
    for setting_index, (setting, label, color) in enumerate(zip(settings, legend, colors)):
        if preparation_comparison:
            values = [next(case['variants'][setting]['warm_p50_sec'] for case in data['cases']
                           if case['name'] == name) for name in names]
        else:
            batch, workers = setting
            values = [next(case['warm_p50_sec'] for case in data['cases']
                           if case['name'] == name and case['requested_batch_size'] == batch
                           and case['requested_workers'] == workers) for name in names]
        bars = axes.bar(positions + (setting_index - (len(settings) - 1) / 2) * width, values, width,
                        color=color, label=label)
        axes.bar_label(bars, labels=[f'{value:.3f}s' for value in values], padding=3, fontsize=9)
    axes.set_xticks(positions, labels)
    axes.set_ylabel('실행 시간 (초) · 낮을수록 빠름')
    first_case = data['cases'][0]
    repeats = len(first_case['variants']['optimized']['warm_sec'] if preparation_comparison else first_case['warm_sec'])
    title = 'PERF-5B CPU 추가 개선' if preparation_comparison else 'PERF-5 CPU 성능 비교'
    title += f' · 같은 장면과 표본의 warm {repeats}회 중앙값'
    axes.set_title(title, pad=16)
    axes.set_ylim(0, axes.get_ylim()[1] * 1.18)
    axes.grid(axis='y', alpha=0.2)
    axes.set_axisbelow(True)
    axes.legend(loc='upper left', frameon=False, fontsize=9)
    figure.supxlabel('i7-10700 · 동일 프로세스 내 측정 순서 교대 · CAD 준비 및 결과 전송/화면 표시는 제외', fontsize=9)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=160, facecolor='white')
    plt.close(figure)


if __name__ == '__main__':
    main()
