from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
from pathlib import Path
import shutil
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "samples"))
from generate_rt_validation_models import DEFAULT_OUTPUT, definitions, write_setup_guide


def model_overview(output, manifest):
    models, _ = definitions()
    figure = plt.figure(figsize=(15, 9), constrained_layout=True)
    selected = [("01_single_mirror", "01 · 1회 반사"), ("02_two_mirrors", "02 · 2회 반사"),
                ("03_corridor_0010", "03~05 · 10 / 100 / 1,000회 통로"),
                ("06_scatter_plate", "06 · 산란 모델"), ("08_cavity_gap_1p0", "07~09 · 좁은 틈 / 복잡 형상"),
                ("10_source_chamber", "10 · 광원 공통 6면 수광 챔버")]
    for index, (model_id, title) in enumerate(selected, start=1):
        axes = figure.add_subplot(2, 3, index, projection="3d", computed_zorder=False)
        points = []
        for name, body in models[model_id]:
            if name in ("Top", "Side_negative_Y", "Wall_negative_Y", "positive_X", "negative_Y", "positive_Z"):
                continue
            vertices, faces = body.val().tessellate(0.1)
            coordinates = np.asarray([vertex.toTuple() for vertex in vertices])
            triangles = coordinates[np.asarray(faces)]
            points.extend(coordinates)
            color = "#d3ad73" if name.startswith("Baffle") else "#70a894" if name == "Round_post" else "#9fbbce"
            axes.add_collection3d(Poly3DCollection(triangles, facecolor=color, edgecolor="none", alpha=1, zorder=1))
        case = next(item for item in manifest["cases"] if item["model"] == model_id + ".stp")
        for item, color in [(case["emitters"][0], "#ed9f2d"), *[(receiver, "#785cc4") for receiver in case["receivers"]]]:
            center = np.asarray(item["center"])
            horizontal = np.asarray(item["u_axis"]) * item["width_mm"] / 2
            vertical = np.asarray(item["v_axis"]) * item["height_mm"] / 2
            polygon = [center - horizontal - vertical, center + horizontal - vertical,
                       center + horizontal + vertical, center - horizontal + vertical]
            points.extend(polygon)
            axes.add_collection3d(Poly3DCollection([polygon], facecolor=color, edgecolor=color, alpha=0.18, zorder=3))
            axes.scatter(*center, color=color, s=16, zorder=6)
        result_path = output / "results" / (case["id"] + "_cpu_result.json")
        if result_path.exists() and model_id not in ("10_source_chamber", "08_cavity_gap_1p0"):
            result = json.loads(result_path.read_text(encoding="utf-8"))
            for path in result.get("stored_paths", [])[:3]:
                coordinates = np.asarray([event["point"] for event in path])
                axes.plot(*coordinates.T, color="#dc7f16", linewidth=0.9, zorder=5)
        coordinates = np.asarray(points)
        minimum, maximum = coordinates.min(axis=0), coordinates.max(axis=0)
        spans = np.maximum(maximum - minimum, 1)
        axes.set_xlim(minimum[0] - 1, maximum[0] + 1)
        axes.set_ylim(minimum[1] - 1, maximum[1] + 1)
        axes.set_zlim(minimum[2] - 1, maximum[2] + 1)
        axes.set_box_aspect(spans)
        axes.set_proj_type("ortho")
        axes.view_init(elev=23, azim=-57)
        axes.set_title(title, fontsize=12)
        axes.set_axis_off()
    figure.suptitle("BitSam ↔ LT 공통 검증 모델\n주황: 광원 / 보라: 수광면 · 내부 확인을 위해 일부 벽은 그림에서만 생략", fontsize=16)
    figure.savefig(output / "models_overview.png", dpi=150)
    plt.close(figure)


def source_maps(output, manifest, ray_count):
    cases = [item for item in manifest["cases"] if item["id"].startswith("10_source_")]
    figure, plots = plt.subplots(3, 5, figsize=(16, 10), constrained_layout=True)
    for axes, case in zip(plots.ravel(), cases):
        path = output / "results" / (case["id"] + "_cpu_result.json")
        if not path.exists():
            axes.set_axis_off()
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        receiver_id = "negative_Z" if case["id"].endswith("backward") else "positive_Z"
        grid = next(item for item in result["receiver_grids"] if item["receiver_id"] == receiver_id)
        values = np.asarray(grid["flux_lumen"])
        image = axes.imshow(values, origin="lower", extent=(-19, 19, -19, 19), cmap="turbo", interpolation="nearest")
        axes.set_title(case["id"].replace("10_source_", "") + f"\n{receiver_id} · {values.sum():.5f} lm", fontsize=10)
        axes.set_xlabel("u (mm)", fontsize=8)
        axes.set_ylabel("v (mm)", fontsize=8)
        axes.tick_params(labelsize=7)
        figure.colorbar(image, ax=axes, shrink=0.7, format="%.1e")
    for axes in plots.ravel()[len(cases):]:
        axes.set_axis_off()
    figure.suptitle(f"공통 챔버의 광원 유형별 수광 패턴 · CPU {ray_count:,} Rays\n각 그림 색 범위는 독립적 · 원본 셀 flux(lm) / 후방광은 -Z 수광면 표시", fontsize=14)
    figure.savefig(output / "source_comparison.png", dpi=150)
    plt.close(figure)


def write_comparison_csv(output, report):
    fields = ["case_id", "receiver_id", "step_file", "step_sha256", "bitsam_rays", "bitsam_flux_lumen",
              "bitsam_mean_lux", "bitsam_peak_cell_lux", "bitsam_hits", "lt_version", "lt_rays", "lt_seed",
              "lt_max_depth", "lt_termination", "lt_flux_lumen", "lt_mean_lux", "lt_peak_cell_lux", "lt_hits", "notes"]
    with (output / "lt_results_template.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for case in report["cases"]:
            baseline = case["runs"][0]
            for receiver_id, values in baseline["receivers"].items():
                writer.writerow({"case_id": case["id"], "receiver_id": receiver_id, "step_file": case["model"],
                                 "step_sha256": case["step_sha256"], "bitsam_rays": baseline["total_rays"],
                                 "bitsam_flux_lumen": values["flux_lumen"], "bitsam_mean_lux": values["mean_lux"],
                                 "bitsam_peak_cell_lux": values["peak_cell_lux"], "bitsam_hits": values["hits"]})

    heatmap_dir = output / "results" / "heatmaps"
    heatmap_dir.mkdir(exist_ok=True)
    for case in report["cases"]:
        result = json.loads((output / "results" / (case["id"] + "_cpu_result.json")).read_text(encoding="utf-8"))
        receivers = {item["receiver_id"]: item for item in result["receivers"]}
        for grid in result["receiver_grids"]:
            receiver = receivers[grid["receiver_id"]]
            columns, rows = grid["resolution"]
            path = heatmap_dir / (case["id"] + "_" + grid["receiver_id"] + ".csv")
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["row", "column", "u_mm", "v_mm", "flux_lumen", "illuminance_lux"])
                for row in range(rows):
                    for column in range(columns):
                        horizontal = ((column + 0.5) / columns - 0.5) * receiver["width_mm"]
                        vertical = ((row + 0.5) / rows - 0.5) * receiver["height_mm"]
                        flux = grid["flux_lumen"][row][column]
                        writer.writerow([row, column, horizontal, vertical, flux, flux * 1e6 / grid["bin_area_mm2"]])


def convergence_table(output, report):
    rows = []
    schedules = sorted({(item["ray_count"], item["max_depth"]) for item in report["convergence"]})
    for rays, depth in schedules:
        group = [item for item in report["convergence"] if item["ray_count"] == rays and item["max_depth"] == depth]
        values = np.asarray([item["total_receiver_flux_lumen"] for item in group])
        peak = np.asarray([item["receivers"]["observer"]["peak_cell_lux"] for item in group])
        rows.append({"rays": rays, "depth": depth, "repeats": len(group), "mean_flux_lumen": float(values.mean()),
                     "seed_std_lumen": float(values.std(ddof=1)), "relative_seed_std_percent": float(values.std(ddof=1) / values.mean() * 100) if values.mean() else None,
                     "mean_peak_cell_lux": float(peak.mean()), "peak_seed_std_lux": float(peak.std(ddof=1)),
                     "mean_receiver_hits": float(np.mean([item["receiver_hits"] for item in group])),
                     "mean_depth_limit_count": float(np.mean([item["reflection"].get("depth_limit_count", 0) for item in group]))})
    if rows:
        with (output / "results" / "convergence_summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        figure, axes = plt.subplots(figsize=(9, 5), constrained_layout=True)
        for rays in sorted({item["rays"] for item in rows}):
            group = [item for item in rows if item["rays"] == rays]
            axes.errorbar([item["depth"] for item in group], [item["mean_flux_lumen"] for item in group],
                          yerr=[item["seed_std_lumen"] for item in group], marker="o", capsize=4, label=f"{rays:,} rays · seed 5회")
        axes.set_xscale("log")
        axes.set_xticks([20, 50, 100, 300, 1000], ["20", "50", "100", "300", "1000"])
        axes.set_xlabel("최대 반사 횟수")
        axes.set_ylabel("Receiver total flux (lm)")
        axes.set_title("1 mm 출구 합성 cavity · 오차 막대는 seed 간 표준편차")
        axes.grid(alpha=0.25)
        axes.legend()
        figure.savefig(output / "convergence.png", dpi=150)
        plt.close(figure)
    return rows


def render_lines(lines):
    fragments = []
    in_table = False
    for line in lines:
        if line.startswith("|"):
            if not in_table:
                fragments.append("<table>")
                in_table = True
            if set(line.replace("|", "").replace(":", "").replace(" ", "")) == {"-"}:
                continue
            fragments.append("<tr>" + "".join("<td>" + html.escape(cell.strip()) + "</td>" for cell in line.strip("|").split("|")) + "</tr>")
            continue
        if in_table:
            fragments.append("</table>")
            in_table = False
        if line.startswith("## "):
            fragments.append("<h2>" + html.escape(line[3:]) + "</h2>")
        elif line.startswith("# "):
            fragments.append("<h2>" + html.escape(line[2:]) + "</h2>")
        elif line:
            fragments.append("<p>" + html.escape(line) + "</p>")
    if in_table:
        fragments.append("</table>")
    return "\n".join(fragments)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    output = arguments.kit
    manifest = json.loads((output / "models_manifest.json").read_text(encoding="utf-8"))
    report = json.loads((output / "results" / "verification.json").read_text(encoding="utf-8"))
    plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False
    write_setup_guide(output, manifest["cases"])
    model_overview(output, manifest)
    source_maps(output, manifest, report["ray_count"])
    write_comparison_csv(output, report)
    convergence = convergence_table(output, report)
    passed = sum(case["passed"] for case in report["cases"])
    preflight = report["preflight"] or {}
    lines = ["# RT 공통 STEP 검증 결과", "", f"- 검증 기준 commit: `{report['base_commit']}`",
             f"- STEP {len(manifest['models'])}개 / 광학 조건 {len(manifest['cases'])}개 / 검사 통과 {passed}/{len(report['cases'])}",
             f"- 각 조건 {report['ray_count']:,} Ray. CPU/GPU 각각 첫 실행 + 2회 warm 반복. 추가 CPU batch=257 비교.",
             f"- GPU: {preflight.get('device_name', '미검증')}; 실제 실행 여부는 각 run의 원본 performance 항목에 기록.",
             "- 실제 STEP를 다시 import한 정밀 trace 메쉬로 검사했습니다. 손으로 만든 대체 삼각형으로 검사한 결과가 아닙니다.",
             "- GPU source launcher 사전 점검은 이 도구 세션의 Python 탐색 문제로 완료되지 않았습니다. 기존 고정 버전 런타임의 요구 패키지 일치 검사와 공식 production CUDA 검사기를 직접 실행한 후 실제 CUDA run 증거를 별도로 기록했습니다. 런처 정상 동작까지 검증했다고 해석하지 마세요.",
             "- LT 정합·실제 TV 정합·실측 nit 보정은 아직 미검증입니다. 동일 코드 CPU/GPU 일치는 물리 정확도의 충분조건이 아닙니다.", "",
             "## 결과 요약", "", "| 조건 | 통과 | 총 수광 flux(lm) | CPU warm(s) | GPU warm(s) |", "|---|---|---:|---:|---:|"]
    for case in report["cases"]:
        cpu_runs = [item for item in case["runs"] if item["backend"] == "cpu"]
        gpu_runs = [item for item in case["runs"] if item["backend"] == "gpu_cuda"]
        lines.append(f"| {case['id']} | {'O' if case['passed'] else 'X'} | {cpu_runs[0]['total_receiver_flux_lumen']:.10g} | {np.mean([item['runtime_sec'] for item in cpu_runs[1:]]):.4f} | {np.mean([item['runtime_sec'] for item in gpu_runs[1:]]) if gpu_runs else float('nan'):.4f} |")
    lines.extend(["", "## 1~4단계의 판정 범위", "",
                  "1. 이론 검증: 1/2/10/100/1,000회 specular flux 곱셈, Lambertian 면적 적분, 광원 수광 합계와 방향별 분포 검사를 수행했습니다.",
                  "2. 구현 일관성: 실제 GPU 실행 근거, CPU/GPU 격자·hit 수, 동일 seed 반복 및 CPU batch 크기 변경을 검사했습니다.",
                  "3. 복합 형상: 평판·곡면 기둥·엇갈린 차폐벽·0.5/1/2 mm 출구를 검사했습니다. 이 전달판은 ROI OFF 기준이며 UI의 ROI 경계 절단 자체는 별도 회귀검증이 필요합니다.",
                  f"4. 수렴: 1 mm cavity에서 반사 20/50/100/300/1,000, seed 5개, Ray {report['ray_count']:,}/{report['ray_count'] * 4:,}의 초기 실험입니다. 전체 TV·모든 물성·5% 목표 달성을 보증하지 않습니다.", "",
                  "검사 허용오차: 확정적 이론 flux 절대 1e-10 lm; CPU/GPU 격자 rtol=1e-9, atol=1e-13 lm; 방향별 확률 검사는 이항 표준오차 6배 + 1e-6 lm입니다. 통계 검사의 통과는 상대오차 5%를 인증한다는 의미가 아닙니다.", "",
                  "## 초기 수렴 관측", "", "| Ray | 반사 상한 | 평균 flux(lm) | seed 표준편차(lm) | 평균 hit | 평균 depth-limit |", "|---:|---:|---:|---:|---:|---:|"])
    for row in convergence:
        lines.append(f"| {row['rays']} | {row['depth']} | {row['mean_flux_lumen']:.8g} | {row['seed_std_lumen']:.4g} | {row['mean_receiver_hits']:.1f} | {row['mean_depth_limit_count']:.1f} |")
    lines.extend(["", "같은 seed에서 상한 증가 후 flux가 같아지는지와, 다른 seed의 통계 변동을 따로 봐야 합니다. 셀별 표본이 적은 heatmap/Peak는 flux보다 많은 Ray가 필요합니다.",
                  "이 표는 관측값이며 수렴 통과 인증서가 아닙니다. seed 5개만으로 매우 작은 bias를 배제할 수 없습니다.", "",
                  "## 사내 전달", "", "1. 이 폴더 전체 또는 같은 이름 ZIP을 사내로 옮깁니다.",
                  "2. `LT_SETUP_KO.md` 순서대로 STEP를 열고 광학 조건을 적용합니다. 원본 도면은 포함되어 있지만 광원/Receiver/물성은 STEP에 들어 있지 않습니다.",
                  "3. `lt_results_template.csv`의 빈 LT 열과 heatmap 원본 CSV를 채웁니다. 광원 공통 챔버는 6개 수광면별로 기록합니다.",
                  "4. Gaussian은 LT와 분포 정의가 같은지 우선 확인합니다. nit는 광속/조도 정합 이후 별도 정의·관측 각도·측정 조건을 맞춰 비교합니다.",
                  "", "원본 실행 로그와 수치 결과: `results/verification.json`, `results/*_cpu_result.json`."])
    (output / "VALIDATION_REPORT_KO.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    mismatches = [case for case in report["cases"] if not case["passed"]]
    mismatch_lines = []
    if mismatches:
        mismatch_lines = ["", "## 미통과 항목 — 별도 조사 필요", "",
                          "07~09 복합 cavity의 strict CPU/GPU 정합 검사는 미통과입니다. 허용오차를 완화해 통과 처리하지 않았습니다.",
                          "| 조건 | CPU hit | GPU hit | 총 flux 상대 차이 | 최대 셀 flux 차이(lm) |", "|---|---:|---:|---:|---:|"]
        for case in mismatches:
            cpu_run = next(item for item in case["runs"] if item["backend"] == "cpu")
            gpu_run = next(item for item in case["runs"] if item["backend"] == "gpu_cuda")
            relative = abs(gpu_run["total_receiver_flux_lumen"] - cpu_run["total_receiver_flux_lumen"]) / max(cpu_run["total_receiver_flux_lumen"], 1e-300) * 100
            mismatch_lines.append(f"| {case['id']} | {cpu_run['receiver_hits']} | {gpu_run['receiver_hits']} | {relative:.7g}% | {gpu_run['parity']['maximum_grid_error_lumen']:.7g} |")
        mismatch_lines.extend(["", "총 flux 차이가 작다는 이유로 Ray 경로/hit 차이를 무시하지 않습니다. 수치 오차 누적·경계 판정·난수 경로 등을 분리 조사해야 하며, 현재 자료만으로 특정 원인을 확정하지 않습니다.",
                               "깊은 반사에서 광속이 매우 작아지면 hit 개수 차이와 flux 차이는 크게 다를 수 있습니다. 실사용 편향이나 LT 일치 여부는 독립 비교가 필요합니다."])
        diagnostic_path = output / "results" / "cavity_depth_parity.json"
        if diagnostic_path.exists():
            diagnostics = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            mismatch_lines.extend(["", "1 mm cavity 반사 상한별 추가 진단:", "", "| 상한 | 경로 수 일치 | 최대 셀 flux 차이(lm) |", "|---:|---|---:|"])
            for item in diagnostics:
                mismatch_lines.append(f"| {item['depth']} | {item['parity']['discrete_exact']} | {item['parity']['maximum_grid_error_lumen']:.7g} |")
        lines.extend(mismatch_lines)
        (output / "VALIDATION_REPORT_KO.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    body = render_lines(lines)
    image_tags = "".join(f'<h2>{title}</h2><img src="{filename}">' for title, filename in (
        ("공통 모델", "models_overview.png"), ("광원별 패턴", "source_comparison.png"), ("초기 수렴", "convergence.png")) if (output / filename).exists())
    warning = f'<p style="padding:16px;background:#fff0d4;border:1px solid #d5a344;border-radius:8px"><strong>{passed}/{len(report["cases"])} 조건 통과</strong> · 복합 cavity {len(mismatches)}개 strict CPU/GPU 차이 조사 필요 · LT 정합 미검증</p>'
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>RT 검증 모델</title><style>body{font:16px/1.65 "Malgun Gothic",sans-serif;background:#eef2f6;color:#152434;max-width:1280px;margin:40px auto;padding:24px}img{width:100%;background:white;border-radius:12px}p{margin:8px 0}h2{margin-top:40px}table{border-collapse:collapse;background:white;width:100%;font-size:14px}td{border:1px solid #ccd5df;padding:8px}tr:first-child{background:#dbe7f1;font-weight:bold}</style><h1>BitSam · LT 공통 검증 모델</h1><p>자세한 설정: LT_SETUP_KO.md · 수치 표: VALIDATION_REPORT_KO.md · LT 기록: lt_results_template.csv</p>' + warning + image_tags + body + '</html>'
    (output / "REPORT.html").write_text(page, encoding="utf-8")
    (output / "README_KO.md").write_text("# 사내 LT 비교용 검증 패키지\n\n1. REPORT.html: 그림과 검증 요약\n2. LT_SETUP_KO.md: 광원·수광면·재질 입력표\n3. 01~10 .stp: 원본 STEP 모델\n4. lt_results_template.csv: LT 결과 기입표\n5. models_manifest.json / results: 정확한 입력·출력·실행 근거\n\n실행파일/회사 도면/개인 작업 파일은 포함되지 않습니다. STEP 파일만 열어서는 광학 설정이 자동 적용되지 않습니다. 프로그램의 자동 불러오기 형식인 .bitsam과는 다른 검증용 자료 묶음입니다.\n", encoding="utf-8")
    checksums = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            checksums.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.relative_to(output).as_posix())
    (output / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    archive = shutil.make_archive(str(output), "zip", root_dir=output.parent, base_dir=output.name)
    print(archive)


if __name__ == "__main__":
    main()
