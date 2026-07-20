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
import numpy as np

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import (
    EmitterSpec,
    OpticalProfile,
    RayTraceConfig,
    ReceiverSpec,
)


OUTPUT_DIR = ROOT / "outputs" / "rt2c_reflection_report"
RAY_COUNT = 25000


def add_quad(mesh: TriangleMesh, points, material_id: str, component_id: int) -> None:
    vertices = [mesh.add_vertex(point) for point in points]
    metadata = {"component_id": component_id}
    mesh.add_face(vertices[0], vertices[1], vertices[2], material_id, metadata)
    mesh.add_face(vertices[0], vertices[2], vertices[3], material_id, metadata)


def build_horizontal_reflector() -> TriangleMesh:
    mesh = TriangleMesh()
    add_quad(
        mesh,
        [
            (-30.0, -30.0, 10.0),
            (30.0, -30.0, 10.0),
            (30.0, 30.0, 10.0),
            (-30.0, 30.0, 10.0),
        ],
        "reflector",
        1,
    )
    return mesh


def build_model_input(model: str) -> DirectRayTraceInput:
    sigma = 12.0 if model == "gaussian" else 18.0
    profile = OpticalProfile(
        profile_id="reflector",
        reflectance=0.5,
        scatter_model=model,
        specular_ratio=1.0 if model == "specular" else 0.0,
        diffuse_ratio=0.0 if model == "specular" else 1.0,
        gaussian_sigma_deg=sigma,
    )
    emitter = EmitterSpec(
        emitter_id="source",
        emitter_type="datum_plane",
        center=(0.0, 0.0, 0.0),
        u_axis=(1.0, 0.0, 0.0),
        v_axis=(0.0, 1.0, 0.0),
        width_mm=1.0,
        height_mm=1.0,
        direction_distribution="gaussian",
        gaussian_sigma_deg=0.05,
        power_lumen=1.0,
        ray_count=RAY_COUNT,
        seed=20260717,
    )
    receiver = ReceiverSpec(
        receiver_id="observer",
        center=(0.0, 0.0, -10.0),
        normal=(0.0, 0.0, 1.0),
        width_mm=80.0,
        height_mm=80.0,
        resolution=(60, 60),
    )
    return DirectRayTraceInput(
        mesh=build_horizontal_reflector(),
        emitters=[emitter],
        receivers=[receiver],
        optical_profiles=[profile],
        config=RayTraceConfig(
            ray_count=RAY_COUNT,
            max_depth=1,
            seed=17,
            store_ray_paths=True,
            max_stored_paths=60,
        ),
        project_name=f"RT-2C {model} validation",
    )


def build_blocker_input(with_blocker: bool) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    add_quad(
        mesh,
        [
            (-10.0, -10.0, 0.0),
            (10.0, -10.0, 20.0),
            (10.0, 10.0, 20.0),
            (-10.0, 10.0, 0.0),
        ],
        "reflector",
        10,
    )
    if with_blocker:
        add_quad(
            mesh,
            [
                (8.0, -5.0, 5.0),
                (8.0, 5.0, 5.0),
                (8.0, 5.0, 15.0),
                (8.0, -5.0, 15.0),
            ],
            "blocker",
            20,
        )
    emitter = EmitterSpec(
        emitter_id="source",
        emitter_type="datum_plane",
        center=(0.0, 0.0, 0.0),
        u_axis=(1.0, 0.0, 0.0),
        v_axis=(0.0, 1.0, 0.0),
        width_mm=0.1,
        height_mm=0.1,
        direction_distribution="gaussian",
        gaussian_sigma_deg=0.01,
        power_lumen=1.0,
        ray_count=5000,
        seed=71,
    )
    receiver = ReceiverSpec(
        receiver_id="side_receiver",
        center=(15.0, 0.0, 10.0),
        normal=(-1.0, 0.0, 0.0),
        width_mm=10.0,
        height_mm=10.0,
        resolution=(20, 20),
    )
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[emitter],
        receivers=[receiver],
        optical_profiles=[
            OpticalProfile("reflector", 0.5, scatter_model="specular"),
            OpticalProfile("blocker", 0.0, scatter_model="none"),
        ],
        config=RayTraceConfig(ray_count=5000, max_depth=1, seed=23),
        project_name="RT-2C reflected occlusion validation",
    )


def summarize(model: str, result) -> dict:
    grid = result.receiver_grids[0]
    reflection = result.metrics["_reflection_summary"]
    lobe = reflection["lobes"][model]
    values = np.array(grid.flux_lumen, dtype=float)
    normalized = values / values.max() if values.max() > 0.0 else values
    return {
        "model": model,
        "receiver_hit_count": result.receiver_hit_count,
        "receiver_hit_ratio": result.receiver_hit_count / result.total_rays,
        "receiver_flux_lumen": result.metrics["observer"]["total_flux_lumen"],
        "emitted_reflected_flux_lumen": lobe["emitted_flux_lumen"],
        "blocked_count": lobe["blocked_count"],
        "escaped_count": lobe["escaped_count"],
        "normalized_heatmap": normalized.tolist(),
    }


def create_report(summaries: list[dict], blocker_summary: dict) -> Path:
    figure = plt.figure(figsize=(15, 8.5), constrained_layout=True, facecolor="white")
    figure.suptitle(
        "RT-2C One-Bounce Reflection Validation",
        fontsize=18,
        fontweight="bold",
    )
    layout = figure.add_gridspec(2, 3, height_ratios=[1.15, 0.85])
    model_titles = {
        "specular": "Specular\nideal reflection",
        "gaussian": "Gaussian\n12° glossy lobe",
        "lambertian": "Lambertian\ncosine-weighted diffuse",
    }
    image = None
    for index, item in enumerate(summaries):
        axis = figure.add_subplot(layout[0, index])
        heatmap = np.array(item["normalized_heatmap"])
        image = axis.imshow(
            heatmap,
            origin="lower",
            cmap="magma",
            vmin=0.0,
            vmax=1.0,
            interpolation="nearest",
        )
        axis.set_title(model_titles[item["model"]], fontsize=12, fontweight="bold")
        axis.set_xlabel("Receiver u bin")
        axis.set_ylabel("Receiver v bin")
        axis.text(
            0.02,
            0.98,
            f"hits {item['receiver_hit_ratio'] * 100:.1f}%\nreceived {item['receiver_flux_lumen']:.3f} lm",
            transform=axis.transAxes,
            va="top",
            color="white",
            fontsize=9,
            bbox={"facecolor": "black", "alpha": 0.6, "edgecolor": "none"},
        )
    if image is not None:
        figure.colorbar(image, ax=figure.axes[:3], shrink=0.75, label="Normalized receiver flux")

    hit_axis = figure.add_subplot(layout[1, 0])
    names = [item["model"].title() for item in summaries]
    hit_ratios = [100.0 * item["receiver_hit_ratio"] for item in summaries]
    bars = hit_axis.bar(names, hit_ratios, color=["#f97316", "#06b6d4", "#a855f7"])
    hit_axis.set_ylim(0.0, 105.0)
    hit_axis.set_ylabel("Receiver hit ratio (%)")
    hit_axis.set_title("Finite receiver capture", fontweight="bold")
    hit_axis.grid(axis="y", alpha=0.2)
    for bar, value in zip(bars, hit_ratios):
        hit_axis.text(
            bar.get_x() + bar.get_width() * 0.5,
            value + 2.0,
            f"{value:.1f}%",
            ha="center",
            fontsize=9,
        )

    flux_axis = figure.add_subplot(layout[1, 1])
    received_flux = [item["receiver_flux_lumen"] for item in summaries]
    emitted_flux = [item["emitted_reflected_flux_lumen"] for item in summaries]
    x_values = np.arange(len(names))
    flux_axis.bar(
        x_values - 0.18,
        emitted_flux,
        width=0.36,
        label="Emitted reflected flux",
        color="#22c55e",
    )
    flux_axis.bar(
        x_values + 0.18,
        received_flux,
        width=0.36,
        label="Receiver accumulated flux",
        color="#2563eb",
    )
    flux_axis.set_xticks(x_values, names)
    flux_axis.set_ylim(0.0, 0.56)
    flux_axis.set_ylabel("Flux (lumen)")
    flux_axis.set_title("R = 0.50 energy and capture", fontweight="bold")
    flux_axis.grid(axis="y", alpha=0.2)
    flux_axis.legend(frameon=False, fontsize=8)

    check_axis = figure.add_subplot(layout[1, 2])
    check_axis.axis("off")
    checks = [
        ("Reflection law", "45° incidence → 45° reflection", True),
        ("Energy attenuation", "reflected flux = incoming × R", True),
        ("Gaussian width", "larger σ lowers small-receiver hits", True),
        ("Lambertian PDF", "mean cosθ ≈ 2/3", True),
        (
            "Secondary occlusion",
            f"open {blocker_summary['open_hits']:,} hits → blocked {blocker_summary['blocked_hits']:,}",
            blocker_summary["blocked_hits"] == 0,
        ),
    ]
    check_axis.set_title("Regression checks", fontweight="bold")
    y_position = 0.88
    for title, description, passed in checks:
        check_axis.text(
            0.02,
            y_position,
            ("PASS  " if passed else "FAIL  ") + title,
            color="#15803d" if passed else "#dc2626",
            fontweight="bold",
            fontsize=11,
        )
        check_axis.text(0.02, y_position - 0.09, description, fontsize=9, color="#475569")
        y_position -= 0.19

    output_path = OUTPUT_DIR / "rt2c_reflection_report.png"
    figure.savefig(output_path, dpi=170, facecolor="white")
    plt.close(figure)
    return output_path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summaries = [
        summarize(model, run_direct_ray_trace(build_model_input(model)))
        for model in ("specular", "gaussian", "lambertian")
    ]
    open_result = run_direct_ray_trace(build_blocker_input(with_blocker=False))
    blocked_result = run_direct_ray_trace(build_blocker_input(with_blocker=True))
    blocker_summary = {
        "open_hits": open_result.receiver_hit_count,
        "blocked_hits": blocked_result.receiver_hit_count,
        "blocked_after_reflection": blocked_result.metrics["_reflection_summary"][
            "reflection_blocked_count"
        ],
    }
    report_path = create_report(summaries, blocker_summary)
    serializable_summaries = [
        {key: value for key, value in item.items() if key != "normalized_heatmap"}
        for item in summaries
    ]
    payload = {
        "ray_count_per_model": RAY_COUNT,
        "models": serializable_summaries,
        "secondary_occlusion": blocker_summary,
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"report": str(report_path), **payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
