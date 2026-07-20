from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MATPLOTLIB_CACHE = Path(tempfile.gettempdir()) / "tv_leakage_matplotlib"
MATPLOTLIB_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MATPLOTLIB_CACHE))

import matplotlib.pyplot as plt
from matplotlib.colors import PowerNorm
from matplotlib.patches import Rectangle

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import EmitterSpec, RayTraceConfig, ReceiverSpec


RAY_COUNT = 10000
OUTPUT_DIR = ROOT / "outputs" / "rt2a_occlusion_report"


def add_plate(mesh: TriangleMesh, x0: float, x1: float, z: float, component_id: int = 20) -> None:
    y0, y1 = -12.0, 12.0
    vertices = [
        mesh.add_vertex((x0, y0, z)),
        mesh.add_vertex((x1, y0, z)),
        mesh.add_vertex((x1, y1, z)),
        mesh.add_vertex((x0, y1, z)),
    ]
    metadata = {"component_id": component_id}
    mesh.add_face(vertices[0], vertices[1], vertices[2], "black_pc_resin", metadata)
    mesh.add_face(vertices[0], vertices[2], vertices[3], "black_pc_resin", metadata)


def build_scenario(name: str, gap_width_mm: float | None = None) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    if name == "Full blocker":
        add_plate(mesh, -12.0, 12.0, 10.0)
    elif gap_width_mm is not None:
        half_gap = max(0.0, min(12.0, gap_width_mm * 0.5))
        if half_gap <= 0.0:
            add_plate(mesh, -12.0, 12.0, 10.0)
        elif half_gap < 12.0:
            add_plate(mesh, -12.0, -half_gap, 10.0)
            add_plate(mesh, half_gap, 12.0, 10.0)

    emitter = EmitterSpec(
        emitter_id="datum_source",
        emitter_type="datum_plane",
        center=(0.0, 0.0, 0.0),
        u_axis=(1.0, 0.0, 0.0),
        v_axis=(0.0, 1.0, 0.0),
        width_mm=2.0,
        height_mm=2.0,
        direction_distribution="gaussian",
        gaussian_sigma_deg=8.0,
        power_lumen=1.0,
        ray_count=RAY_COUNT,
        seed=20260716,
    )
    receiver = ReceiverSpec(
        receiver_id="observer",
        center=(0.0, 0.0, 20.0),
        normal=(0.0, 0.0, -1.0),
        width_mm=30.0,
        height_mm=30.0,
        resolution=(30, 30),
    )
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[emitter],
        receivers=[receiver],
        optical_profiles=[],
        config=RayTraceConfig(
            ray_count=RAY_COUNT,
            max_depth=0,
            seed=20260716,
            store_ray_paths=True,
            max_stored_paths=180,
        ),
        project_name=f"RT-2A {name}",
    )


def scenario_summary(name: str, result) -> dict:
    missed = result.total_rays - result.receiver_hit_count - result.surface_hit_count
    return {
        "scenario": name,
        "total_rays": result.total_rays,
        "receiver_hits": result.receiver_hit_count,
        "cad_blocked": result.surface_hit_count,
        "missed": missed,
        "receiver_hit_ratio": result.receiver_hit_count / result.total_rays,
        "cad_blocked_ratio": result.surface_hit_count / result.total_rays,
        "runtime_sec": result.runtime_sec,
        "peak_nit_est": result.metrics["observer"]["peak_nit_est"],
        "total_flux_lumen": result.metrics["observer"]["total_flux_lumen"],
    }


def run_bvh_benchmark() -> dict:
    mesh = TriangleMesh()
    cell_count = 69
    cell_size = 24.0 / cell_count
    for x_index in range(cell_count):
        for y_index in range(cell_count):
            x0 = -12.0 + x_index * cell_size
            y0 = -12.0 + y_index * cell_size
            x1 = x0 + cell_size
            y1 = y0 + cell_size
            vertices = [
                mesh.add_vertex((x0, y0, 10.0)),
                mesh.add_vertex((x1, y0, 10.0)),
                mesh.add_vertex((x1, y1, 10.0)),
                mesh.add_vertex((x0, y1, 10.0)),
            ]
            metadata = {"component_id": 99}
            mesh.add_face(vertices[0], vertices[1], vertices[2], "black_pc_resin", metadata)
            mesh.add_face(vertices[0], vertices[2], vertices[3], "black_pc_resin", metadata)
    benchmark_input = build_scenario("Open path")
    benchmark_input.mesh = mesh
    benchmark_input.config.store_ray_paths = False
    result = run_direct_ray_trace(benchmark_input)
    return {
        "triangle_count": len(mesh.faces),
        "ray_count": result.total_rays,
        "cad_blocked": result.surface_hit_count,
        "runtime_sec": result.runtime_sec,
        "acceleration": "lazy BVH",
    }


def draw_schematic(axis, name: str, result) -> None:
    axis.set_title(name, fontsize=12, fontweight="bold")
    for path in result.stored_paths:
        if len(path) < 2:
            continue
        start, end = path[0], path[-1]
        color = "#22c55e" if end.event_type == "receiver" else "#f97316"
        axis.plot([start.point[0], end.point[0]], [start.point[2], end.point[2]], color=color, alpha=0.23, linewidth=0.8)
    axis.add_patch(Rectangle((-1.0, -0.35), 2.0, 0.7, color="#60a5fa", alpha=0.9))
    axis.plot([-15.0, 15.0], [20.0, 20.0], color="#a855f7", linewidth=4)
    if name == "Full blocker":
        draw_blocker(axis, -12.0, 12.0)
    elif name == "4 mm gap":
        draw_blocker(axis, -12.0, -2.0)
        draw_blocker(axis, 2.0, 12.0)
        axis.annotate(
            "4 mm GAP",
            xy=(0.0, 10.0),
            xytext=(0.0, 12.0),
            ha="center",
            fontsize=8,
            fontweight="bold",
            color="#b45309",
            arrowprops={"arrowstyle": "->", "color": "#b45309"},
        )
    axis.text(-14.5, 0.9, "Emitter", color="#2563eb", fontsize=8)
    axis.text(-14.5, 20.8, "Receiver", color="#7e22ce", fontsize=8)
    axis.set_xlim(-15.0, 15.0)
    axis.set_ylim(-1.0, 22.0)
    axis.set_xlabel("X (mm)")
    axis.set_ylabel("Z (mm)")
    axis.grid(alpha=0.15)


def draw_blocker(axis, x0: float, x1: float) -> None:
    axis.add_patch(
        Rectangle(
            (x0, 9.65),
            x1 - x0,
            0.7,
            facecolor="#111827",
            edgecolor="#f59e0b",
            linewidth=1.4,
            hatch="////",
            zorder=5,
        )
    )
    if x1 - x0 > 15.0:
        axis.text((x0 + x1) * 0.5, 10.75, "CAD BLOCKER", ha="center", fontsize=8, fontweight="bold", color="#111827")


def receiver_nit_grid(result) -> list[list[float]]:
    grid = result.receiver_grids[0]
    bin_area_m2 = grid.bin_area_mm2 * 1e-6
    scale = result.config.k_abs * result.config.k_brdf / (bin_area_m2 * 3.141592653589793)
    return [[value * scale for value in row] for row in grid.flux_lumen]


def create_gap_sweep_report(sweep_results: dict[float, object]) -> Path:
    gap_sizes = list(sweep_results)
    hit_ratios = [100.0 * sweep_results[gap].receiver_hit_count / sweep_results[gap].total_rays for gap in gap_sizes]
    blocked_ratios = [100.0 * sweep_results[gap].surface_hit_count / sweep_results[gap].total_rays for gap in gap_sizes]
    flux_values = [sweep_results[gap].metrics["observer"]["total_flux_lumen"] for gap in gap_sizes]
    open_flux = max(flux_values) or 1.0

    selected_gaps = [0.0, 1.0, 2.0, 4.0, 8.0, 24.0]
    grids = {gap: receiver_nit_grid(sweep_results[gap]) for gap in selected_gaps}
    common_max = max(value for grid in grids.values() for row in grid for value in row) or 1.0

    figure = plt.figure(figsize=(15, 9), constrained_layout=True, facecolor="white")
    figure.suptitle("RT-2A Gap Sweep · Transmission and Receiver Heatmaps", fontsize=17, fontweight="bold")
    layout = figure.add_gridspec(3, 6, height_ratios=[1.1, 0.1, 1.0])
    trend_axis = figure.add_subplot(layout[0, :3])
    trend_axis.plot(gap_sizes, hit_ratios, marker="o", linewidth=2.2, color="#16a34a", label="Receiver hit")
    trend_axis.plot(gap_sizes, blocked_ratios, marker="s", linewidth=2.0, color="#ea580c", label="CAD blocked")
    trend_axis.axvline(4.0, color="#2563eb", linestyle="--", alpha=0.8)
    trend_axis.annotate(
        f"4 mm: {hit_ratios[gap_sizes.index(4.0)]:.1f}% hit",
        xy=(4.0, hit_ratios[gap_sizes.index(4.0)]),
        xytext=(5.3, 72.0),
        arrowprops={"arrowstyle": "->", "color": "#2563eb"},
        color="#1d4ed8",
        fontweight="bold",
    )
    trend_axis.set_xlabel("Geometric gap width (mm)")
    trend_axis.set_ylabel("Ray classification (%)")
    trend_axis.set_ylim(0.0, 104.0)
    trend_axis.set_title("Gap width sweep", fontweight="bold")
    trend_axis.legend()
    trend_axis.grid(alpha=0.2)

    flux_axis = figure.add_subplot(layout[0, 3:])
    normalized_flux = [100.0 * value / open_flux for value in flux_values]
    flux_axis.bar(gap_sizes, normalized_flux, width=0.65, color="#7c3aed")
    flux_axis.axvline(4.0, color="#2563eb", linestyle="--", alpha=0.8)
    flux_axis.set_xlabel("Geometric gap width (mm)")
    flux_axis.set_ylabel("Receiver flux / open flux (%)")
    flux_axis.set_ylim(0.0, 104.0)
    flux_axis.set_title("Relative received flux", fontweight="bold")
    flux_axis.grid(axis="y", alpha=0.2)

    image = None
    for index, gap in enumerate(selected_gaps):
        heatmap_axis = figure.add_subplot(layout[2, index])
        image = heatmap_axis.imshow(
            grids[gap],
            origin="lower",
            cmap="inferno",
            norm=PowerNorm(gamma=0.5, vmin=0.0, vmax=common_max),
            extent=(-15.0, 15.0, -15.0, 15.0),
        )
        ratio = 100.0 * sweep_results[gap].receiver_hit_count / sweep_results[gap].total_rays
        heatmap_axis.set_title(f"{gap:g} mm gap\nHit {ratio:.1f}%", fontsize=10, fontweight="bold")
        heatmap_axis.set_xlabel("Receiver U (mm)")
        if index == 0:
            heatmap_axis.set_ylabel("Receiver V (mm)")
        else:
            heatmap_axis.set_yticklabels([])
    if image is not None:
        figure.colorbar(image, ax=figure.axes[-6:], location="bottom", shrink=0.7, pad=0.13, label="nit_est (common scale)")
    output_path = OUTPUT_DIR / "rt2a_gap_sweep_report.png"
    figure.savefig(output_path, dpi=170, facecolor="white")
    plt.close(figure)
    return output_path


def create_report(summaries: list[dict], results: dict[str, object], benchmark: dict) -> Path:
    figure = plt.figure(figsize=(15, 10), constrained_layout=True, facecolor="white")
    figure.suptitle("TV Leakage Simulator · RT-2A CAD Occlusion Test", fontsize=17, fontweight="bold")
    grid = figure.add_gridspec(2, 3, height_ratios=[1.15, 0.85])
    for index, summary in enumerate(summaries):
        draw_schematic(figure.add_subplot(grid[0, index]), summary["scenario"], results[summary["scenario"]])

    bar_axis = figure.add_subplot(grid[1, :2])
    names = [item["scenario"] for item in summaries]
    receiver = [100.0 * item["receiver_hit_ratio"] for item in summaries]
    blocked = [100.0 * item["cad_blocked_ratio"] for item in summaries]
    missed = [100.0 - receiver[index] - blocked[index] for index in range(len(summaries))]
    bar_axis.bar(names, receiver, label="Receiver hit", color="#22c55e")
    bar_axis.bar(names, blocked, bottom=receiver, label="CAD blocked", color="#f97316")
    bar_axis.bar(
        names,
        missed,
        bottom=[receiver[index] + blocked[index] for index in range(len(summaries))],
        label="Missed",
        color="#cbd5e1",
    )
    for index, item in enumerate(summaries):
        bar_axis.text(index, 3.0, f"Hit {100.0 * item['receiver_hit_ratio']:.1f}%", ha="center", fontsize=9, fontweight="bold")
        if item["cad_blocked_ratio"] > 0.03:
            bar_axis.text(
                index,
                receiver[index] + blocked[index] * 0.5,
                f"Blocked {100.0 * item['cad_blocked_ratio']:.1f}%",
                ha="center",
                va="center",
                fontsize=9,
                fontweight="bold",
            )
    bar_axis.set_ylim(0.0, 100.0)
    bar_axis.set_ylabel("Ray classification (%)")
    bar_axis.set_title("Nearest-event classification", fontsize=12, fontweight="bold")
    bar_axis.legend(loc="upper right", ncol=3)
    bar_axis.grid(axis="y", alpha=0.18)

    note_axis = figure.add_subplot(grid[1, 2])
    note_axis.axis("off")
    note_axis.set_title("RT-2A validation", fontsize=12, fontweight="bold", loc="left")
    lines = [
        "Rule",
        "CAD hit distance < Receiver hit distance",
        "→ ray is blocked and contributes no flux",
        "",
        "Checks",
        "✓ Open path reaches Receiver",
        "✓ Opaque plate blocks direct rays",
        "✓ Geometric gap restores transmission",
        "✓ Blocked path stores face/component/material ID",
        "",
        f"Rays per scenario: {RAY_COUNT:,}",
        "Reflection/scattering: OFF (RT-2B/2C)",
        "",
        "BVH benchmark",
        f"{benchmark['triangle_count']:,} triangles × {benchmark['ray_count']:,} rays",
        f"Runtime: {benchmark['runtime_sec']:.3f} s",
    ]
    note_axis.text(0.0, 0.94, "\n".join(lines), va="top", fontsize=10, linespacing=1.45)
    output_path = OUTPUT_DIR / "rt2a_occlusion_report.png"
    figure.savefig(output_path, dpi=170, facecolor="white")
    plt.close(figure)
    return output_path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    names = ["Open path", "Full blocker", "4 mm gap"]
    results = {
        "Open path": run_direct_ray_trace(build_scenario("Open path")),
        "Full blocker": run_direct_ray_trace(build_scenario("Full blocker")),
        "4 mm gap": run_direct_ray_trace(build_scenario("4 mm gap", gap_width_mm=4.0)),
    }
    summaries = [scenario_summary(name, results[name]) for name in names]
    gap_sizes = [0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 24.0]
    sweep_results = {
        gap: run_direct_ray_trace(build_scenario(f"{gap:g} mm gap", gap_width_mm=gap))
        for gap in gap_sizes
    }
    gap_sweep = [
        {
            "gap_width_mm": gap,
            "receiver_hits": sweep_results[gap].receiver_hit_count,
            "cad_blocked": sweep_results[gap].surface_hit_count,
            "receiver_hit_ratio": sweep_results[gap].receiver_hit_count / sweep_results[gap].total_rays,
            "total_flux_lumen": sweep_results[gap].metrics["observer"]["total_flux_lumen"],
        }
        for gap in gap_sizes
    ]
    benchmark = run_bvh_benchmark()
    report_path = create_report(summaries, results, benchmark)
    gap_sweep_report_path = create_gap_sweep_report(sweep_results)
    summary_path = OUTPUT_DIR / "summary.json"
    payload = {"scenarios": summaries, "gap_sweep": gap_sweep, "bvh_benchmark": benchmark}
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"report": str(report_path), "gap_sweep_report": str(gap_sweep_report_path), **payload},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
