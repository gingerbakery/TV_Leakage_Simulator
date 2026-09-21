from __future__ import annotations

import argparse
import base64
import collections
import csv
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    data = json.loads(args.report.read_text(encoding="utf-8"))
    output = args.report.parent
    evidence = []
    for index, entry in enumerate(data["runs"]):
        performance = entry["performance"]
        evidence.append({
            "index": index, "case": entry["case"], "backend": entry["backend"],
            "state": performance.get("compute_execution_state"),
            "reason": performance.get("compute_execution_reason"),
            "device": performance.get("gpu_cuda_device_name"),
            "cuda_attempts": performance.get("gpu_cuda_gpu_attempt_count", 0),
            "cuda_successes": performance.get("gpu_cuda_gpu_success_count", 0),
            "cpu_hybrid_successes": performance.get("gpu_cuda_hybrid_cpu_success_count", 0),
            "intersection_fallbacks": performance.get("intersection_fallback_count", 0),
            "resident_fallbacks": performance.get("gpu_resident_wavefront_fallback_count", 0),
            "elapsed_sec": entry["elapsed_sec"], "emitter_types": ",".join(entry["emitter_types"]),
        })
    with (output / "execution_evidence.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(evidence[0]))
        writer.writeheader()
        writer.writerows(evidence)
    gpu = [entry for entry in evidence if entry["backend"] == "gpu_cuda"]
    summary = {
        "passed": data["passed"], "head": data["head"], "preflight": data["preflight"],
        "runs": len(data["runs"]), "device_pairs": len(data["paired_device_checks"]),
        "gpu_states": dict(collections.Counter(entry["state"] for entry in gpu)),
        **{key: sum(entry[key] for entry in gpu) for key in ("cuda_attempts", "cuda_successes", "cpu_hybrid_successes", "intersection_fallbacks", "resident_fallbacks")},
        "roulette_groups": data["roulette_groups"],
        "legacy_accuracy_failures": sum(entry["case"] == "power_ray_sweep" and entry["min_energy_basis"] == "absolute_lumen" and not entry["accuracy_passed"] for entry in data["runs"]),
        "relative_sweep_passes": sum(entry["case"] == "power_ray_sweep" and entry["min_energy_basis"] == "initial_ray_fraction" and entry["accuracy_passed"] for entry in data["runs"]),
        "first_warm_seconds": [{key: entry[key] for key in ("backend", "repetition", "elapsed_sec")} for entry in data["runs"] if entry["case"] == "first_and_warm"],
    }
    root = Path(__file__).resolve().parents[1]
    summary["source_sha256"] = {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in (
            "src/leakage_simulator/termination.py", "src/leakage_simulator/types.py",
            "src/leakage_simulator/raytracer.py", "src/leakage_simulator/native_cpu_counter_wavefront.py",
            "src/leakage_simulator/gpu_cuda_resident_wavefront.py", "scripts/verify_termination_policy.py",
        )
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False, "font.size": 11})
    figure, axes = plt.subplots(2, 2, figsize=(13, 9.3), constrained_layout=True)
    figure.suptitle("종료 정책·절단 손실 검증 | CPU / RTX 3070", fontsize=20, weight="bold")
    sweep = [entry for entry in data["runs"] if entry["backend"] == "gpu_cuda" and entry["case"] == "power_ray_sweep" and entry["source_power_lumen"] == 1e-5]
    for basis, threshold, label, color in (("absolute_lumen", 1e-9, "기존: 1e-9 lm/Ray", "#c74a47"), ("initial_ray_fraction", 1e-9, "개선: 초기 Ray의 1e-9", "#127b97")):
        rows = [entry for entry in sweep if entry["min_energy_basis"] == basis and entry["min_energy"] == threshold]
        axes[0, 0].plot([entry["ray_count"] for entry in rows], [entry["flux_lumen"] * 1e6 for entry in rows], "o-", label=label, color=color, linewidth=2.5)
    axes[0, 0].set(title="같은 광원, Ray 수만 증가", xlabel="Ray 수", ylabel="수광 광속 (µlm)", ylim=(-0.2, 4.2))
    axes[0, 0].axhline(3.6, color="#222222", linestyle=":", label="독립 이론값: 3.6 µlm")
    axes[0, 0].legend(fontsize=9)
    losses = [entry for entry in data["runs"] if entry["backend"] == "gpu_cuda" and entry["case"] == "depth_sweep" and entry["reflectance"] == 0.999 and entry["required_bounces"] == 300]
    positions = list(range(len(losses)))
    axes[0, 1].bar([value - 0.18 for value in positions], [entry["flux_lumen"] for entry in losses], 0.36, label="수광 광속", color="#127b97")
    axes[0, 1].bar([value + 0.18 for value in positions], [entry["termination"]["unpropagated_surface_flux_lumen"] for entry in losses], 0.36, label="종료 후 미전파 광속", color="#e3a83f")
    axes[0, 1].set_xticks(positions, [str(entry["max_depth"]) for entry in losses])
    axes[0, 1].set(title="300회 통로 · 반사율 99.9% · 입력 1 lm", xlabel="반사 횟수 상한", ylabel="광속 (lm)")
    axes[0, 1].legend(fontsize=9)
    for basis, label, color in (("absolute_lumen", "RR: 1e-9 lm/Ray", "#b86e39"), ("initial_ray_fraction", "RR: 초기 Ray의 0.5", "#127b97")):
        rows = [entry for entry in data["runs"] if entry["backend"] == "gpu_cuda" and entry["case"] == "roulette" and entry["min_energy_basis"] == basis]
        axes[1, 0].plot([entry["seed_index"] + 1 for entry in rows], [100 * (entry["flux_lumen"] / entry["expected_untruncated_flux_lumen"] - 1) for entry in rows], "o-", markersize=3, label=label, color=color)
    axes[1, 0].axhline(0, color="black", linestyle=":")
    axes[1, 0].set(title="Russian roulette · 독립 32 seed", xlabel="독립 seed 번호", ylabel="이론 광량 대비 오차 (%)")
    axes[1, 0].legend(fontsize=9)
    axes[1, 1].axis("off")
    text = (f"실행 {summary['runs']}회 / 장치 쌍 {summary['device_pairs']}개\n"
            f"CUDA 성공 / 시도: {summary['cuda_successes']} / {summary['cuda_attempts']}\n"
            f"기존 절대 기준의 광량 절단: {summary['legacy_accuracy_failures']}조건 재현\n"
            f"상대 기준·종료 해제 광량 sweep: {summary['relative_sweep_passes']}조건 통과\n\n"
            "· 반사 상한 손실은 별도: 상대 기준으로 해결되지 않음\n"
            "· RR: 평균 편향 완화, 개별 결과의 노이즈는 남음\n"
            "· MIS/RR 전체 에너지 ledger: 아직 미완료\n"
            "· 실제 TV / LT 정합성: 이번 실험 범위 밖")
    axes[1, 1].text(0.02, 0.94, text, va="top", linespacing=1.8, fontsize=12)
    for axis in axes.flat[:3]:
        axis.grid(alpha=0.15)
        axis.spines[["top", "right"]].set_visible(False)
    figure.savefig(output / "termination_validation.png", dpi=160)
    image = base64.b64encode((output / "termination_validation.png").read_bytes()).decode("ascii")
    (output / "report.html").write_text(f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>종료 정책 검증</title>
<style>body{{font-family:"Malgun Gothic",sans-serif;background:#f1f5f9;color:#172332;margin:24px auto;max-width:1100px;padding:20px}}article{{background:white;border-radius:14px;padding:24px}}img{{width:100%;height:auto}}li{{margin:10px 0;line-height:1.7}}p{{line-height:1.8}}</style>
<article><h1>종료 정책과 절단 손실 검증</h1><p>2026-09-21 · source checkout · NVIDIA RTX 3070 · CPU/GPU 독립 이론 비교</p>
<img src="data:image/png;base64,{image}" alt="종료 정책 검증 그래프">
<h2>실제 수정</h2><ul><li>광원별 초기 Ray 광속 대비 상대 임계값 추가: Ray 수와 광원 밝기가 바뀌어도 동일한 감쇠 비율에서 종료합니다.</li>
<li>새 작업은 상대 기준 1e-9, 이전 파일은 기존 lm/Ray 기준을 유지합니다. 기준 0은 에너지 종료만 해제하고 반사 상한은 유지합니다.</li>
<li>종료 진단을 Multi-bounce의 접힌 메뉴로 제공합니다. MIS 가중치 0 경로의 잘못된 종료 상태도 CPU/GPU에서 수정했습니다.</li></ul>
<h2>주의사항</h2><p>상대 임계값도 미약한 경로를 잘라내므로 무편향 해법은 아닙니다. 절단을 줄이면 계산 시간이 늘 수 있습니다. 미전파 광량은 Receiver에서 실제 잃은 양이 아니며, 자동 수렴의 통계 오차에도 절단 편향은 포함되지 않습니다. RR/MIS의 완전한 손실 ledger와 실제 복잡 TV/LT 정합성은 별도 검증이 필요합니다.</p>
<p>정밀 비교 순서: 같은 반사 상한에서 임계값을 낮추거나 0으로 비교 → 반사 상한 증가 비교 → 독립 seed의 Flux와 Peak 통계 비교.</p></article></html>''', encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
