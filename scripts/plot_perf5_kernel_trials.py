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
    specialization = json.loads((args.directory / 'kernel-specialization-cad-probe.json').read_text())
    math = json.loads((args.directory / 'fp64-math-cad-probe.json').read_text())
    bvh = json.loads((args.directory / 'guarded-bvh-padded-final.json').read_text())
    if not all(report['passed'] and report['source_unchanged'] for report in (specialization, math, bvh)):
        raise RuntimeError('Source-frozen comparison evidence is required')
    first = specialization['cases'][0]['records']
    second = math['cases'][0]['records']
    left = [('설정별 분기 고정', first['config']['speedup']),
        ('레지스터 64개 제한', first['regs64']['speedup']),
        ('광학 값 고정', first['optics']['speedup']),
        ('단위 벡터 계산 생략', second['unit_norm']['speedup']),
        ('FP64 sin/cos 통합', second['sincos']['speedup'])]
    right = [('빠른 BVH만 사용\n면 속성 오류 재현', bvh['records']['unsafe_leaf_one']['speedup']),
        ('GPU 원래 탐색 재검산', bvh['records']['guarded']['speedup']),
        ('외향 경계 여유 + 재검산', bvh['records']['padded_guarded']['speedup']),
        ('여유 경계 + 함수 통합', bvh['records']['padded_inlined']['speedup'])]
    plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False, 'font.size': 11})
    figure, axes = plt.subplots(1, 2, figsize=(14, 5.5), layout='constrained')
    for axis, values, title in zip(axes, (left, right), ('커널과 FP64 연산 후보', 'BVH와 정확도 보호 후보')):
        labels = [value[0] for value in values][::-1]
        ratios = [value[1] for value in values][::-1]
        colors = ['#bc4a4a' if '면 속성 오류' in label else '#347aa0' for label in labels]
        axis.barh(labels, ratios, color=colors)
        axis.axvline(1.0, color='#526878', linestyle='--', linewidth=1)
        for index, ratio in enumerate(ratios):
            axis.text(ratio + 0.015, index, f'{ratio:.3f}배', va='center', bbox={'facecolor': 'white', 'edgecolor': 'none', 'pad': 0.1})
        axis.set_title(title)
        axis.set_xlabel('동일 세션 현재 GPU 경로 대비 속도비')
        axis.set_xlim(0, 1.7)
        axis.spines[['top', 'right']].set_visible(False)
    figure.suptitle('GPU 추가 가속 실험 · 정확도 오류 후보는 기본 적용 제외', fontsize=15, fontweight='bold')
    figure.savefig(args.directory / 'kernel-trials-comparison.png', dpi=170)
    plt.close(figure)


if __name__ == '__main__':
    main()
