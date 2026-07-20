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

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import (
    EmitterSpec,
    OpticalAssignment,
    OpticalProfile,
    RayTraceConfig,
    ReceiverSpec,
)


OUTPUT_DIR = ROOT / "outputs" / "rt2b_optical_lookup_report"
RAY_COUNT = 10000


def build_mesh() -> TriangleMesh:
    mesh = TriangleMesh()
    vertices = [
        mesh.add_vertex((-12.0, -12.0, 10.0)),
        mesh.add_vertex((12.0, -12.0, 10.0)),
        mesh.add_vertex((12.0, 12.0, 10.0)),
        mesh.add_vertex((-12.0, 12.0, 10.0)),
    ]
    mesh.add_face(
        vertices[0], vertices[1], vertices[2], "mesh_black", {"component_id": 7, "source_face_index": 0}
    )
    mesh.add_face(
        vertices[0], vertices[2], vertices[3], "mesh_black", {"component_id": 7, "source_face_index": 1}
    )
    return mesh


def build_input(assignments: list[OpticalAssignment]) -> DirectRayTraceInput:
    emitter = EmitterSpec(
        emitter_id="source",
        emitter_type="datum_plane",
        center=(0.0, 0.0, 0.0),
        u_axis=(1.0, 0.0, 0.0),
        v_axis=(0.0, 1.0, 0.0),
        width_mm=2.0,
        height_mm=2.0,
        direction_distribution="gaussian",
        gaussian_sigma_deg=3.0,
        power_lumen=1.0,
        ray_count=RAY_COUNT,
        seed=20260716,
    )
    receiver = ReceiverSpec(
        receiver_id="observer",
        center=(0.0, 0.0, 20.0),
        normal=(0.0, 0.0, -1.0),
        width_mm=40.0,
        height_mm=40.0,
    )
    profiles = [
        OpticalProfile(
            "mesh_black",
            0.05,
            specular_ratio=0.0,
            diffuse_ratio=1.0,
            scatter_model="lambertian",
            notes="Mesh material fallback",
        ),
        OpticalProfile(
            "part_black",
            0.20,
            specular_ratio=0.25,
            diffuse_ratio=0.75,
            scatter_model="mixed",
            notes="Part assignment",
        ),
        OpticalProfile(
            "face_gloss",
            0.80,
            specular_ratio=0.70,
            diffuse_ratio=0.30,
            scatter_model="gaussian",
            gaussian_sigma_deg=8.0,
            notes="Face override validation profile",
        ),
    ]
    return DirectRayTraceInput(
        mesh=build_mesh(),
        emitters=[emitter],
        receivers=[receiver],
        optical_profiles=profiles,
        optical_assignments=assignments,
        config=RayTraceConfig(
            ray_count=RAY_COUNT,
            max_depth=0,
            store_ray_paths=True,
            max_stored_paths=20,
        ),
        project_name="RT-2B optical lookup validation",
    )


def extract_summary(name: str, result) -> dict:
    summary = result.metrics["_optical_summary"]
    profile_entry = max(summary["profile_hits"].values(), key=lambda item: item["hit_count"])
    return {
        "scenario": name,
        "profile_id": profile_entry["profile_id"],
        "source": profile_entry["source"],
        "hit_count": profile_entry["hit_count"],
        "reflectance": profile_entry["reflectance"],
        "specular_ratio": profile_entry["specular_ratio"],
        "diffuse_ratio": profile_entry["diffuse_ratio"],
        "scatter_model": profile_entry["scatter_model"],
        "incoming_flux_lumen": profile_entry["incoming_flux_lumen"],
        "potential_reflected_flux_lumen": profile_entry["potential_reflected_flux_lumen"],
        "unassigned_surface_hit_count": summary["unassigned_surface_hit_count"],
    }


def create_report(summaries: list[dict]) -> Path:
    figure = plt.figure(figsize=(14, 8), constrained_layout=True, facecolor="white")
    figure.suptitle("RT-2B Optical Property Lookup and Energy Budget", fontsize=17, fontweight="bold")
    layout = figure.add_gridspec(2, 2, height_ratios=[1.05, 0.95])

    precedence_axis = figure.add_subplot(layout[0, 0])
    precedence_axis.axis("off")
    boxes = [
        (0.08, 0.70, "1. Face override", "component + source face match", "#dc2626"),
        (0.08, 0.43, "2. Part assignment", "component match", "#2563eb"),
        (0.08, 0.16, "3. Mesh material", "face material ID fallback", "#475569"),
    ]
    for x, y, title, description, color in boxes:
        precedence_axis.text(
            x,
            y,
            title + "\n" + description,
            transform=precedence_axis.transAxes,
            fontsize=12,
            fontweight="bold",
            va="center",
            bbox={"boxstyle": "round,pad=0.7", "facecolor": "white", "edgecolor": color, "linewidth": 2},
        )
    precedence_axis.annotate("", xy=(0.38, 0.57), xytext=(0.38, 0.66), xycoords="axes fraction", arrowprops={"arrowstyle": "->", "color": "#64748b"})
    precedence_axis.annotate("", xy=(0.38, 0.30), xytext=(0.38, 0.39), xycoords="axes fraction", arrowprops={"arrowstyle": "->", "color": "#64748b"})
    precedence_axis.set_title("Deterministic lookup precedence", fontsize=13, fontweight="bold")

    profile_axis = figure.add_subplot(layout[0, 1])
    names = [item["scenario"] for item in summaries]
    reflectances = [100.0 * item["reflectance"] for item in summaries]
    bars = profile_axis.bar(names, reflectances, color=["#475569", "#2563eb", "#dc2626"])
    for bar, item in zip(bars, summaries):
        profile_axis.text(
            bar.get_x() + bar.get_width() * 0.5,
            bar.get_height() + 2.0,
            f"R={item['reflectance']:.2f}\n{item['profile_id']}",
            ha="center",
            fontsize=9,
            fontweight="bold",
        )
    profile_axis.set_ylim(0.0, 100.0)
    profile_axis.set_ylabel("Total reflectance (%)")
    profile_axis.set_title("Resolved profile", fontsize=13, fontweight="bold")
    profile_axis.grid(axis="y", alpha=0.2)

    energy_axis = figure.add_subplot(layout[1, :])
    incoming = [item["incoming_flux_lumen"] for item in summaries]
    reflected = [item["potential_reflected_flux_lumen"] for item in summaries]
    lost = [incoming[index] - reflected[index] for index in range(len(summaries))]
    energy_axis.bar(names, reflected, label="Potential reflected flux", color="#22c55e")
    energy_axis.bar(names, lost, bottom=reflected, label="Terminated / non-reflected", color="#cbd5e1")
    for index, item in enumerate(summaries):
        energy_axis.text(index, reflected[index] * 0.5, f"{reflected[index]:.3f} lm", ha="center", va="center", fontweight="bold")
        energy_axis.text(
            index,
            incoming[index] + 0.025,
            f"source={item['source']} · hits={item['hit_count']:,}\n{item['scatter_model']} · spec {item['specular_ratio']:.2f} / diffuse {item['diffuse_ratio']:.2f}",
            ha="center",
            fontsize=9,
        )
    energy_axis.set_ylim(0.0, 1.18)
    energy_axis.set_ylabel("Flux budget (lumen)")
    energy_axis.set_title("Energy conservation check: reflected = incoming × R", fontsize=13, fontweight="bold")
    energy_axis.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False)
    energy_axis.grid(axis="y", alpha=0.2)

    output_path = OUTPUT_DIR / "rt2b_optical_lookup_report.png"
    figure.savefig(output_path, dpi=170, facecolor="white")
    plt.close(figure)
    return output_path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scenarios = {
        "Mesh material": [],
        "Part assignment": [OpticalAssignment("part", "part", 7, "part_black")],
        "Face override": [
            OpticalAssignment("part", "part", 7, "part_black"),
            OpticalAssignment("faces", "faces", 7, "face_gloss", [0, 1], priority=10),
        ],
    }
    summaries = [
        extract_summary(name, run_direct_ray_trace(build_input(assignments)))
        for name, assignments in scenarios.items()
    ]
    report_path = create_report(summaries)
    payload = {"ray_count_per_scenario": RAY_COUNT, "scenarios": summaries}
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"report": str(report_path), **payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
